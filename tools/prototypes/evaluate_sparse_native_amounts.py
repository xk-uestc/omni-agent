"""Read-only source extraction comparison; never asks or answers a question."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ict-track8'), str(Path(__file__).resolve().parent)]
from backend.native_text_tables import extract_native_text_tables
from sparse_native_amounts import extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--document-id', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.baseline.resolve().parent != (ROOT/'docs').resolve()
            or args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists()):
        parser.error('Retained baseline and new docs report required')
    baseline = json.loads(args.baseline.read_bytes())
    corpus = Path(baseline['store_path']).resolve()
    if not corpus.is_relative_to(Path('D:/ICT8-OfficialDatasets/ohr-bench/evaluations').resolve()):
        parser.error('Retained source corpus required')
    pins = {row['document_id']: row['pdf_sha256'] for row in baseline['documents']}
    records = []
    with sqlite3.connect((corpus/'knowledge.sqlite').as_uri()+'?mode=ro', uri=True) as connection:
        for identifier in args.document_id:
            if identifier not in pins:
                raise ValueError('document_not_in_retained_corpus')
            row = connection.execute('SELECT payload FROM documents WHERE document_id=?', (identifier,)).fetchone()
            metadata = json.loads(row[0])
            path = (corpus/'assets'/metadata['asset']).resolve()
            if not path.is_relative_to(corpus/'assets'):
                raise ValueError('original_asset_path_invalid')
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if sha != metadata['sha256'] or sha != pins[identifier]:
                raise ValueError('original_sha_mismatch')
            previous = extract_native_text_tables(raw, page_no=1, expected_source_sha256=sha)
            candidate = extract(raw, page_no=1, expected_source_sha256=sha)
            records.append({'document_id': identifier, 'source_sha256': sha,
                'production_fact_labels': [fact['row_header'] for fact in previous['facts']],
                'candidate': candidate})
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'original_page_extraction_diagnosis_not_question_accuracy',
        'question_or_gold_used': False, 'model_calls': 0, 'production_modified': False,
        'prototype_sha256': hashlib.sha256(Path(__file__).with_name('sparse_native_amounts.py').read_bytes()).hexdigest(),
        'records': records}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'documents': len(records), 'annotations': sum(len(r['candidate']['annotations']) for r in records),
        'model_calls': 0, 'production_modified': False}))


if __name__ == '__main__':
    main()
