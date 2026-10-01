"""Artifact-only replay of the already-exposed six AdventureWorks dev queries.

This does not change any old evaluator/scorer or the frozen new 56 questions.
Reference SQL is executed in a separate readonly connection and never passed
to the planner. Every returned artifact cell is compared, not just COUNT.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from evaluate_adventureworks import verify_assets, projection, same_rows


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def all_cells(engine, receipt):
    result, offset, pages = [], 0, 0
    while True:
        page = engine.complete_result_page(receipt['artifact_id'], offset=offset,
            expected_query_sha256=receipt['query_sha256'],
            expected_binding_sha256=receipt['binding_sha256'])
        result.extend(page['rows'])
        pages += 1
        if page['next_offset'] is None:
            return result, pages
        offset = page['next_offset']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path('D:/ICT8-OfficialDatasets/adventureworks'))
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/COMPLETE_RESULTS_ADVENTUREWORKS_CANDIDATE_20261002.json')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Never overwrite evidence')
    manifest = verify_assets(args.directory)
    database = (args.directory / manifest['database']['file']).resolve()
    questions_path = ROOT / 'benchmarks/adventureworks/questions.json'
    frozen = json.loads((ROOT / 'benchmarks/adventureworks/MANIFEST.json').read_bytes())
    frozen_question_sha = hashlib.sha256(questions_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if frozen_question_sha != frozen['questions_sha256']:
        raise ValueError('Exposed development question SHA changed')
    definitions = json.loads(questions_path.read_bytes())
    source_before = digest(database)
    engine = Nl2SqlEngine(database, result_artifact_dir=ROOT / 'runtime' / 'complete-result-candidate',
                         metric_catalog_path=args.directory / 'no_domain_catalog.json')
    assert engine.metric_catalog is None and engine.model_plan_provider is None
    records = []
    for case in definitions['cases']:
        started = time.perf_counter()
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as reference:
            expected = reference.execute(case['gold_sql']).fetchall()
        legacy = engine.answer(case['question']).to_dict()
        complete = engine.answer(case['question'], complete_results=True).to_dict()
        receipt = complete.get('provenance', {}).get('complete_result')
        cells, pages = all_cells(engine, receipt) if receipt and receipt['status'] == 'complete' else ([], 0)
        if len(set(complete.get('columns', []))) != len(complete.get('columns', [])):
            raise ValueError('Typed projected columns unexpectedly duplicate')
        full = {**complete, 'rows': [dict(zip(complete['columns'], row)) for row in cells]}
        observed = projection(case, full)
        legacy_projected = projection(case, legacy)
        checks = {'status_ok': complete['status'] == 'ok',
            'artifact_cursor_eof': bool(receipt and receipt['cursor_eof_verified']),
            'all_actual_cells_equal_reference': same_rows(observed, expected),
            'preview_at_most_100': len(complete['rows']) <= 100,
            'artifact_row_count_matches_actual': bool(receipt and receipt['row_count'] == len(cells)),
            'physical_column_roles_verified': observed is not None,
            'rules_no_api_no_alias_catalog': complete['plan']['planner_source'] == 'rules'}
        record = {'id': case['id'], 'checks': checks, 'artifact_validation_pass': all(checks.values()),
            'reference_rows': len(expected), 'legacy_rows': len(legacy['rows']),
            'legacy_complete_rows_match': same_rows(legacy_projected, expected),
            'preview_rows': len(complete['rows']), 'actual_artifact_rows': len(cells), 'pages_read': pages,
            'actual_rows_sha256': hashlib.sha256(json.dumps(cells, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'receipt': receipt, 'sql': complete['sql'], 'parameters': complete['parameters'],
            'result_status': complete['status'], 'latency_ms': round((time.perf_counter()-started)*1000, 3)}
        # The saved receipt omits the duplicated presentation cells; their
        # actual values were verified through all product pagination calls.
        if record['receipt']:
            record['receipt'] = {key: value for key, value in record['receipt'].items() if key != 'preview_cells'}
        records.append(record)
        print(json.dumps({key: record[key] for key in ('id', 'artifact_validation_pass',
            'reference_rows', 'legacy_rows', 'actual_artifact_rows', 'pages_read')}, ensure_ascii=False), flush=True)
    if digest(database) != source_before:
        raise ValueError('Official source changed')
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'already_exposed_adventureworks_sqlite_development_complete_artifact_candidate_not_official_score',
        'new_frozen_56_questions_or_oracles_read': False, 'old_evaluator_or_scorer_changed': False,
        'model_api_calls': 0, 'domain_alias_rules': 0, 'source_sha256_before_after': source_before,
        'exposed_questions_sha256': digest(questions_path),
        'exposed_questions_frozen_lf_sha256': frozen_question_sha,
        'isolated_checkout_crlf_normalized_only_for_frozen_byte_check': digest(questions_path) != frozen_question_sha,
        'passed_complete_artifact_checks': sum(row['artifact_validation_pass'] for row in records),
        'total_exposed_queries': len(records), 'cases': records}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in ('passed_complete_artifact_checks', 'total_exposed_queries', 'model_api_calls')}))


if __name__ == '__main__':
    main()
