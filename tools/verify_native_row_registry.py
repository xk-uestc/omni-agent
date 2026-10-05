"""Synthetic PDFs exercise geometry, missing/censored values and replay."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import fitz
from prototype_native_row_registry import extract, replay, ROOT
from probe_native_row_comparison import bind_comparison


def pdf(*, right_header='Result', shift=0, rotation=0, right_value='35.000 UG/L',
        identity='SUBJECT-17'):
    document = fitz.open()
    for page_index in range(2):
        page = document.new_page(width=700, height=400)
        page.insert_text((30, 35), 'Record ID: ' + (identity if page_index else 'SUBJECT-17'), fontsize=9)
        coords = [30, 230, 350, 470, 590]
        if page_index:
            coords = [x + shift for x in coords]
        labels = ['Description', right_header if page_index else 'Result', 'When', 'Operator', 'Method']
        for x, label in zip(coords, labels):
            page.insert_text((x, 70), label, fontsize=9)
        for row in range(3):
            result = right_value if page_index and row == 0 else '211.000 UG/L' if row == 0 else str(10 + row) + ' MG/L'
            cells = ['Entity ' + chr(65 + page_index * 3 + row), result, '2025-01-01', 'PERSON_A', 'ISO_A']
            for x, cell in zip(coords, cells):
                page.insert_text((x, 86 + row * 16), cell, fontsize=9)
        page.set_rotation(rotation)
    raw = document.tobytes(); document.close()
    return raw


def main():
    checks = []
    def check(name, function):
        try:
            function(); checks.append({'name': name, 'passed': True})
        except Exception as exc:
            checks.append({'name': name, 'passed': False, 'error_type': type(exc).__name__})
    def verify_normal(**kwargs):
        raw = pdf(**kwargs); registry = extract(raw)
        assert len(registry['records']) == 6
        assert replay(raw, registry)
        assert all(registry[k] is False for k in ('calculator_input_eligible',
            'semantic_sample_identity_verified', 'exhaustive_table_closure_verified', 'production_answer_authority'))
        return registry
    check('six complete physical rows and all fields', lambda: verify_normal())
    check('rotated source coordinates replay', lambda: verify_normal(rotation=90))
    check('different sample remains semantically unverified', lambda: verify_normal(identity='SUBJECT-99'))
    def censored():
        registry = verify_normal(right_value='<200 UG/L')
        assert registry['records'][3]['fields'][1]['numeric_annotation']['qualifier'] == '<'
    check('censored value qualifier preserved', censored)
    def no_result():
        registry = verify_normal(right_value='NO_RESULT')
        assert registry['records'][3]['fields'][1]['numeric_annotation'] is None
    check('missing value not converted to zero', no_result)
    def wrong_unit():
        registry = verify_normal(right_value='35.000 MG/L')
        assert registry['records'][0]['fields'][1]['numeric_annotation']['unit'] != registry['records'][3]['fields'][1]['numeric_annotation']['unit']
    check('different literal units are not normalized together', wrong_unit)
    def changed_header():
        assert not extract(pdf(right_header='Forecast'))['records']
    check('changed column meaning does not form a chain', changed_header)
    def shifted():
        assert not extract(pdf(shift=20))['records']
    check('misaligned page columns do not form a chain', shifted)
    def tampered():
        raw = pdf(); registry = extract(raw); altered = deepcopy(registry)
        altered['records'][0]['fields'][1]['text'] = '999.000 UG/L'
        assert not replay(raw, altered)
        altered = deepcopy(registry); altered['records'][0]['fields'][1]['bbox_pt'][0] += 1
        assert not replay(raw, altered)
        assert not replay(raw + b'\n', registry)
    check('forged value coordinate and changed original rejected', tampered)
    def rejects_comparison(raw, edit):
        registry = extract(raw)
        plan = {'left_row_id': registry['records'][0]['row_id'],
            'right_row_id': registry['records'][3]['row_id'], 'column_index': 1,
            'scope_label': 'Record ID', 'scope_value': 'SUBJECT-17', **edit}
        try:
            bind_comparison(plan, registry)
        except ValueError:
            return
        raise AssertionError('unsafe comparison accepted')
    for name, kwargs, edit in [
        ('different sample cannot be compared', {'identity': 'SUBJECT-99'}, {}),
        ('incompatible units cannot be compared', {'right_value': '35.000 MG/L'}, {}),
        ('censored interval cannot become exact value', {'right_value': '<200 UG/L'}, {}),
        ('missing result cannot become an operand', {'right_value': 'NO_RESULT'}, {}),
        ('ordinary description cannot identify the sample', {}, {'scope_label': 'Description', 'scope_value': 'Entity A'}),
        ('boolean column index rejected', {}, {'column_index': True}),
    ]:
        check(name, lambda kwargs=kwargs, edit=edit: rejects_comparison(pdf(**kwargs), edit))
    report = {'scope': 'synthetic_source_extraction_not_model_or_answer_accuracy', 'checks': checks,
              'passed': sum(c['passed'] for c in checks), 'total': len(checks)}
    output = Path(sys.argv[1])
    if output.resolve().parent != ROOT / 'docs' or output.exists():
        raise ValueError('new docs output required')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report))
    return 0 if all(c['passed'] for c in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
