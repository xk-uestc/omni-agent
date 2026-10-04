"""Separate full-artifact semantic score, alongside the unchanged inline score.

Only this scoring process opens the private frozen roles/answers. The model
runner remains reference-isolated. Complete results are authenticated through
the production pagination reader before expected rows are compared. This new
delivery-contract score cannot be substituted for historical inline scores.
"""
import argparse
import json
from pathlib import Path
import sys

from cross_schema_round5_common import (ROOT, DEFAULT, digest, projection, read_sql, rows_equal,
    value_digest, verify_public, write_new, now)
from score_cross_schema_round5 import load_oracles, score_case, run_integrity_supported

sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.nl2sql.result_artifact import read_result_page, query_hash


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
    if digest(observed_path) != manifest['observed_sha256']:
        raise ValueError('observations_changed')
    stable = run_integrity_supported(manifest, public)
    by_id = {row['case_id']:row for row in
        (json.loads(line) for line in observed_path.read_text(encoding='utf-8').splitlines())}
    results = []
    for oracle in load_oracles(DEFAULT, public):
        row = by_id.get(oracle['case_id'])
        original = score_case(oracle, row, database)
        score = {**original, 'inline_contract_pass':original['pass'],
                 'checks':dict(original['checks']), 'artifact_used':False}
        outer = (row or {}).get('response') or {}
        result = outer.get('result', {}) if oracle['kind']=='session' else outer
        metadata = result.get('provenance', {}).get('complete_result')
        if metadata:
            try:
                cells = []; offset = 0
                while True:
                    page = read_result_page(ROOT/'runtime/query-results', metadata['artifact_id'],
                        database_path=database, offset=offset, page_size=100,
                        expected_binding_sha256=metadata['binding_sha256'],
                        expected_query_sha256=query_hash(result['sql'], result['parameters']))
                    cells.extend(page['rows'])
                    if page['next_offset'] is None:
                        break
                    if page['next_offset']<=offset:
                        raise ValueError('pagination_not_advancing')
                    offset = page['next_offset']
                columns, replay, _ = read_sql(database, result['sql'], result['parameters'])
                preview = [[r[name] for name in result['columns']] for r in result['rows']]
                authenticated = (metadata['status']=='complete' and metadata['cursor_eof_verified']
                    and len(cells)==metadata['row_count'] and columns==page['columns']==result['columns']
                    and value_digest(cells)==value_digest(replay) and preview==cells[:len(preview)])
                delivered = {**result,'rows':[dict(zip(columns,values)) for values in cells]}
                projected = projection(delivered, oracle['expected_output_roles'])
                score['checks'].update(physical_projection=projected is not None,
                    unchanged_sql_replay_matches_returned_rows=authenticated,
                    complete_result=authenticated and projected is not None and
                        rows_equal(projected, oracle['full_expected_rows'], oracle['comparison_contract']))
                score.update(artifact_used=True, artifact_rows=len(cells),
                    artifact_authentication=authenticated, result_completeness=metadata['status'])
            except Exception as exc:
                score['checks']['complete_result']=False
                score['artifact_error_type']=type(exc).__name__
        score['pass']=stable and all(score['checks'].values())
        score['failure_reason']=None if score['pass'] else ('run_not_stable' if not stable else
            next((key for key,value in score['checks'].items() if not value), original['failure_reason']))
        results.append(score)
    sessions={}
    for row in results:
        if row['kind']=='session':sessions.setdefault(row['session_id'],[]).append(row['pass'])
    report={'created_at':now(), 'scope':'artifact_aware_frozen_reference_delivery_score_not_legacy_inline_score',
        'official_benchmark_score':False, 'model_runner_reference_opened':False,
        'scoring_process_reference_opened':True, 'fixed_inline_scorer_unchanged':True,
        'stable':stable, 'planned_total':56, 'executed_total':len(by_id),
        'inline_passed':sum(r['inline_contract_pass'] for r in results),
        'passed':sum(r['pass'] for r in results),
        'session_turns_passed':sum(r['pass'] for r in results if r['kind']=='session'),
        'sessions_all_passed':sum(len(v)==5 and all(v) for v in sessions.values()),
        'sessions':sessions,'artifact_cases':sum(r['artifact_used'] for r in results), 'cases':results,
        'run_manifest_sha256':digest(manifest_path),'observed_sha256':digest(observed_path),
        'oracle_sha256':public['oracle_sha256'],
        'limitations':['Question set is exposed development replay, not an official or blind score.',
            'Artifact-authenticated complete cells are scored separately; historical inline results remain unchanged.',
            'A single read-only database replay does not formally prove general SQL equivalence.']}
    write_new(args.output,report)
    print(json.dumps({key:report[key] for key in ('scope','inline_passed','passed','artifact_cases',
        'session_turns_passed','sessions_all_passed','stable')}))


if __name__=='__main__':main()
