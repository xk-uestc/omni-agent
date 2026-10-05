"""Retain original PDF fixtures and audit printed amount scope without model calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
import fitz
from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic


CASES = [
    ('symbol-billion', 'Amount ($ bn)', '$', ' bn'),
    ('symbol-million', 'Amount ($ million)', '$', ' million'),
    ('symbol-thousand', 'Amount ($ thousand)', '$', ' thousand'),
    ('code-million', 'Amount USD million', '', ' USD million'),
    ('code-billion', 'Amount EUR billion', '', ' EUR billion'),
    ('symbol-unscaled', 'Amount ($)', '$', ''),
]


def fixtures(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, header, _, _ in CASES:
        path = directory / (name + '.pdf')
        if path.exists():
            continue
        with fitz.open() as document:
            page = document.new_page(width=600, height=400)
            page.insert_text((40, 40), 'Independent native amount display fixture', fontsize=10)
            page.insert_text((40, 70), 'Region', fontsize=10)
            page.insert_text((260, 70), header, fontsize=10)
            for i, (label, value) in enumerate([('North', '4.1'), ('South', '2.6'), ('Total', '6.7')]):
                page.insert_text((40, 100+i*25), label, fontsize=10)
                page.insert_text((350-fitz.get_text_length(value, fontsize=10), 100+i*25), value, fontsize=10)
            # Unrelated narrative cannot supply a scale to the amount column.
            page.insert_text((40, 260), 'Other narrative: billions of historical transactions', fontsize=10)
            path.write_bytes(document.tobytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (not args.fixtures.resolve().is_relative_to((ROOT/'runtime').resolve())
            or args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists()):
        parser.error('Retained runtime fixtures and new docs report required')
    fixtures(args.fixtures)
    source = ROOT/'ict-track8/backend/native_table_question.py'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    records = []
    for name, header, symbol, suffix in CASES:
        raw = (args.fixtures/(name+'.pdf')).read_bytes()
        extracted = extract_native_text_tables(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
        named = {fact['row_header']: fact for fact in extracted['facts']}
        for operation, labels, number in [
                ('lookup', ['North'], '4.1'), ('sum', ['North', 'South'], '6.7'),
                ('difference', ['North', 'South'], '1.5'),
                ('difference', ['South', 'North'], '-1.5'),
                ('ratio', ['Total', 'South'], None)]:
            expected = symbol+number+suffix if number is not None else None
            record = {'fixture': name, 'pdf_sha256': hashlib.sha256(raw).hexdigest(),
                'original_header': header, 'operation': operation, 'labels': labels,
                'expected_display': expected}
            try:
                result = annotation_arithmetic([named[label] for label in labels], operation)
                record.update(result=result, passed=(expected is not None and result['answer'] == expected))
            except ValueError as error:
                # The chosen quotient is nonterminating; rejecting it is the
                # existing exact-arithmetic contract, never a currency answer.
                record.update(error=str(error), passed=(operation == 'ratio'
                    and str(error) == 'native_annotation_ratio_nonterminating_decimal'))
            records.append(record)
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'synthetic_original_PDF_amount_display_not_model_accuracy', 'model_calls': 0,
        'fixtures': str(args.fixtures.resolve()), 'implementation_sha_start': before,
        'implementation_sha_end': after, 'implementation_stable': before == after,
        'passed': sum(r['passed'] for r in records), 'total': len(records), 'records': records}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k:v for k,v in report.items() if k != 'records'}))
    return 0 if before == after else 2


if __name__ == '__main__':
    raise SystemExit(main())
