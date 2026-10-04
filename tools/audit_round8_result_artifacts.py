"""Supplementary actual-artifact replay; never changes the fixed scorer.

The legacy cross-schema scorer requires all cells inline. This audit checks
the new complete-result path separately, using no oracle/reference answer.
It does not establish that generated SQL answers the user's intended question.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.nl2sql.result_artifact import read_result_page, query_hash
from cross_schema_round5_common import DEFAULT, digest, read_sql, value_digest, verify_public, write_new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists():
        parser.error('New report directly in docs required')
    _, database, public, _ = verify_public(DEFAULT)
    manifest_path = args.run_directory/'RUN_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    observed_path = args.run_directory/'observed.jsonl'
    if (digest(observed_path) != manifest['observed_sha256'] or manifest['reference_opened'] is not False
            or manifest['database_sha256'] != public['database_sha256'] or not manifest['source_stable']):
        raise ValueError('run_provenance_invalid')
    results = []
    for line in observed_path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        outer = row.get('response') or {}
        result = outer.get('result', {}) if row['kind'] == 'session' else outer
        metadata = result.get('provenance', {}).get('complete_result')
        if not metadata:
            continue
        if metadata['status']!='complete':
            # A budget-exhausted result correctly has no published artifact.
            # Record it in the denominator as incomplete, not a reader error
            # and not a successful complete delivery.
            results.append({'case_id':row['case_id'],'pass':False,'checks':{},
                'row_count':None,'error_type':None,'result_status':metadata['status'],
                'partial_contract_correct':result.get('status')=='incomplete'
                    and result.get('result_state')=='partial_rows'
                    and metadata.get('artifact_id') is None
                    and metadata.get('cursor_eof_verified') is False})
            continue
        checks = {'receipt_and_current_source':False, 'all_pages_replayed':False,
                  'sql_and_parameters_unchanged':False, 'columns_match':False,
                  'preview_matches_full_prefix':False, 'full_rows_equal_sql_replay':False}
        error = None
        actual_count = None
        try:
            cells = []; offset = 0
            while True:
                page = read_result_page(ROOT/'runtime/query-results', metadata['artifact_id'],
                    database_path=database, expected_binding_sha256=metadata['binding_sha256'],
                    offset=offset, page_size=100)
                cells.extend(page['rows'])
                if page['next_offset'] is None:
                    break
                if page['next_offset'] <= offset:
                    raise ValueError('pagination_not_advancing')
                offset = page['next_offset']
            columns, replay, _ = read_sql(database, result['sql'], result['parameters'])
            actual_count = len(cells)
            query_sha = query_hash(result['sql'], result['parameters'])
            preview = [[record[column] for column in result['columns']] for record in result['rows']]
            checks = {'receipt_and_current_source':True,
                'all_pages_replayed':len(cells) == metadata['row_count'],
                'sql_and_parameters_unchanged':page['query_sha256'] == query_sha,
                'columns_match':page['columns'] == columns == result['columns'],
                'preview_matches_full_prefix':preview == cells[:len(preview)],
                'full_rows_equal_sql_replay':value_digest(cells) == value_digest(replay)}
        except Exception as exc:
            error = type(exc).__name__
        results.append({'case_id':row['case_id'], 'pass':all(checks.values()), 'checks':checks,
                        'row_count':actual_count, 'error_type':error})
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'complete_artifact_integrity_and_actual_sql_replay_not_question_accuracy_or_legacy_score',
        'reference_opened':False, 'model_called':False, 'fixed_scorer_unchanged':True,
        'run_manifest_sha256':digest(manifest_path), 'observed_sha256':digest(observed_path),
        'artifact_cases':len(results), 'passed':sum(r['pass'] for r in results), 'cases':results}
    report['correctly_reported_partial']=sum(r.get('partial_contract_correct') is True for r in results)
    write_new(args.output, report)
    print(json.dumps({k:report[k] for k in ('scope','artifact_cases','passed')}))
    return 0 if results and all(r['pass'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
