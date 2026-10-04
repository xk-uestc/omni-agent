"""Audit the two document patches without overwriting baseline measurements."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_round6_release import api_summary, verify_source

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    path = ROOT / 'docs' / name
    raw = path.read_bytes()
    return json.loads(raw), {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    baseline, baseline_pin = read('ROUND9_FINAL_SOURCE_AUDIT_20261004.json')
    if baseline['source_audit'] != 'PASS':
        raise ValueError('baseline_audit_failed')
    for pin in baseline['reports'].values():
        _, actual = read(pin['path'])
        if actual['sha256'] != pin['sha256']:
            raise ValueError('baseline_report_changed')
    current = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT / 'ict-track8/backend').rglob('*.py'))}
    changed = sorted(path for path in current if current[path] != baseline['backend_file_sha256'].get(path))
    if set(current) != set(baseline['backend_file_sha256']) or changed != [
            'ict-track8/backend/native_table_question.py', 'ict-track8/backend/visual_source_answer.py']:
        raise ValueError('unexpected_incremental_backend_scope')
    regression, regression_pin = read('ROUND9_INCREMENTAL_REGRESSION_20261004.json')
    if verify_source(regression) != current or not regression['ok'] or regression['failed'] != 0:
        raise ValueError('incremental_regression_failed')
    paired = {}
    pins = {'baseline_audit': baseline_pin, 'related_regression': regression_pin}
    for key, suffix, fields, total in [
            ('arithmetic', 'DOCUMENT_ARITHMETIC', ('question', 'original_sha256', 'scoring_only'), 5),
            ('raster', 'RASTER_DOCUMENTS', ('question', 'source_sha256', 'reference_scoring_only'), 4)]:
        old, old_pin = read(f'ROUND9_{suffix}_FINAL_20261004.json')
        new, new_pin = read(f'ROUND9_{suffix}_INCREMENTAL_20261004.json')
        if verify_source(new) != current or new.get('reference_used_as_model_input') is not False:
            raise ValueError('incremental_source_or_reference_' + key)
        replay = new.get('replay_source') or {}
        if replay.get('sha256') != old_pin['sha256']:
            raise ValueError('replay_report_pin_' + key)
        a, b = ({c['id']: c for c in run['cases']} for run in (old, new))
        if len(old['cases']) != total or len(new['cases']) != total or len(a) != total or a.keys() != b.keys():
            raise ValueError('replay_denominator_' + key)
        for identifier in a:
            if any(a[identifier].get(field) != b[identifier].get(field) for field in fields):
                raise ValueError('replay_input_changed_' + key + '_' + identifier)
            case = b[identifier]
            if type(case['pass']) is not bool or (case['pass'] and (not case['api_audits'] or any(
                    row.get('status') != 'completed' or row.get('model_verified') is not True
                    for row in case['api_audits']))):
                raise ValueError('passing_case_without_verified_api_' + key)
        if new['passed'] != sum(c['pass'] for c in b.values()):
            raise ValueError('score_count_' + key)
        paired[key] = {'before_passed': old['passed'], 'after_passed': new['passed'], 'total': total,
            'identical_originals_questions_and_scoring_references': True,
            'improved_ids': [i for i in a if not a[i]['pass'] and b[i]['pass']],
            'regressed_ids': [i for i in a if a[i]['pass'] and not b[i]['pass']],
            'failed_ids': [i for i in b if not b[i]['pass']],
            'api_summary': api_summary([row for c in b.values() for row in c['api_audits']])}
        pins[key] = new_pin
    complex_run, complex_pin = read('ROUND9_COMPLEX_PROBES_INCREMENTAL_20261004.json')
    if verify_source(complex_run) != current or len(complex_run['cases']) != 12 or complex_run['total'] != 12:
        raise ValueError('complex_source_or_denominator')
    if complex_run['reference_used_as_model_input'] is not False:
        raise ValueError('complex_reference_input')
    complex_api = api_summary([a for c in complex_run['cases'] for a in c.get('api_audits', [])])
    http, http_pin = read('ROUND9_HTTP_INCREMENTAL_20261004.json')
    if (not http['ok'] or http['implementation_file_sha256'] != current
            or http['implementation_file_sha256_end'] != current):
        raise ValueError('live_http_contract_or_source')
    pins.update({'complex': complex_pin, 'http': http_pin})
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'source_audit': 'PASS',
        'scope': 'two_document_contract_patches_after_retained_round9_full_validation',
        'changed_backend_files': changed, 'current_backend_file_sha256': current,
        'current_backend_lf_normalized_sha256': {path: hashlib.sha256(
            (ROOT / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for path in current},
        'reports': pins, 'paired_original_replays': paired,
        'related_regression': {k: regression[k] for k in ('passed', 'failed', 'skipped', 'duration_seconds')},
        'complex': {'passed': complex_run['passed'], 'total': 12, 'api_summary': complex_api},
        'http_checks_passed': sum(http['checks'].values()),
        'baseline_full_regression_passed': baseline['local_regression_passed'],
        'full_local_suite_rerun_after_patch': False, 'full_core_or_ohr_rerun_after_patch': False,
        'limitations': ['Original baseline failures are retained, not rescored.',
            'Focused regressions and small same-original replays do not establish generalization.',
            'A successful replay does not identify the hidden cause of the prior visual selection rejection.',
            'The baseline OHR F1 regression remains an unresolved limitation.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'source_audit': 'PASS', 'paired': paired, 'regression': report['related_regression']}))


if __name__ == '__main__':
    main()
