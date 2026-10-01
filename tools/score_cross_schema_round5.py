"""Separate scoring process; references never enter the model runner."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from cross_schema_round5_common import (DEFAULT, EVALUATION_SUBDIR, MODEL, digest, implementation_hashes, inside, load, now,
    projection, read_sql, rows_equal, value_digest, verify_public, write_new)


def load_oracles(directory, public):
    private = Path(directory) / EVALUATION_SUBDIR / 'private'
    manifest = load(private / 'PRIVATE_MANIFEST.json')
    path = inside(private, manifest['reference_file'])
    if (digest(path) != manifest['reference_sha256'] or digest(path) != public['oracle_sha256']
            or manifest['system_input_sha256'] != public['system_input_sha256']
            or manifest['source_manifest_sha256'] != public['source_manifest_sha256']):
        raise ValueError('private_reference_freeze_changed')
    records = load(path)['cases']
    if len(records) != 56 or len({r['case_id'] for r in records}) != 56:
        raise ValueError('reference_denominator_changed')
    return records


def source_reads_supported(required, actual):
    actual = {tuple(item) for item in actual}
    tables = {table for table, _ in actual}
    return all((table in tables if column == '' else (table, column) in actual)
               for table, column in required)


def run_integrity_supported(manifest, public):
    hashes = implementation_hashes()
    return (manifest.get('mode') == 'real_model'
        and manifest.get('implementation_stable') is True and manifest.get('source_stable') is True
        and manifest.get('implementation_file_sha256') == hashes
        and manifest.get('implementation_file_sha256_end') == hashes
        and all(manifest.get(key) == public[key] for key in (
            'source_manifest_sha256', 'database_sha256', 'schema_sha256', 'system_input_sha256'))
        and manifest.get('planned_total') == 56 and manifest.get('reference_opened') is False
        and manifest.get('few_shot_examples') == 0 and manifest.get('domain_alias_rules') == 0
        and manifest.get('metric_catalog') is None
        and manifest.get('limits', {}).get('max_rows') == 100
        and manifest.get('limits', {}).get('max_seconds') == 5.0)


def score_case(oracle, record, database, *, require_model=True):
    checks = {'executed': bool(record), 'status_ok': False, 'sql_route': False,
              'physical_projection': False, 'unchanged_sql_replay_matches_returned_rows': False,
              'required_source_reads': False, 'complete_result': False, 'history': False,
              'no_previous_topic_filters': False, 'real_model_planner': not require_model,
              'verified_api_calls': not require_model, 'gold_payload_isolation': not require_model}
    reason = 'not_run'
    if not record:
        return {'case_id': oracle['case_id'], 'pass': False, 'checks': checks, 'failure_reason': reason,
            'domain': oracle['domain'], 'family': oracle['family'], 'kind': oracle['kind'],
            'session_id': oracle['session_id'], 'turn_index': oracle['turn_index'], 'tags': oracle['tags'],
            'expected_row_count': oracle['expected_row_count'], 'observed_row_count': None}
    outer = record.get('response') or {}
    result = outer.get('result', {}) if oracle['kind'] == 'session' else outer
    checks['status_ok'] = outer.get('status') == 'ok' and result.get('status') == 'ok'
    checks['sql_route'] = (outer.get('route') == 'sql') if oracle['kind'] == 'session' else True
    checks['history'] = (record.get('history_before_count') == oracle['expected_history_turns'])
    filters = [*result.get('plan', {}).get('filters', []),
               *(f for m in result.get('plan', {}).get('metrics', []) for f in m.get('filters', []))]
    forbidden = {tuple(item) for item in oracle.get('forbidden_filter_columns', [])}
    checks['no_previous_topic_filters'] = not any(
        (f.get('table') or result.get('plan', {}).get('table'), f.get('column')) in forbidden for f in filters)
    if require_model:
        checks['real_model_planner'] = result.get('plan', {}).get('planner_source') == 'model_validated' and (
            oracle['kind'] == 'single' or outer.get('planner_source') == 'model_validated')
        audits = record.get('api_audits', [])
        checks['verified_api_calls'] = bool(audits) and record.get('api_audit_dropped', 0) == 0 and all(
            a.get('status') == 'completed' and a.get('model_verified') is True
            and a.get('model') == MODEL for a in audits)
        ledger = record.get('payload_audit', [])
        checks['gold_payload_isolation'] = bool(ledger) and all(
            a.get('private_fields_absent') is True and a.get('future_question_absent') is True
            and a.get('case_id') == oracle['case_id'] for a in ledger)
    actual = projection(result, oracle['expected_output_roles'])
    checks['physical_projection'] = actual is not None
    provenance = result.get('provenance', {})
    completeness = provenance.get('result_completeness')
    if checks['status_ok'] and checks['sql_route'] and result.get('sql'):
        try:
            names, replay, reads = read_sql(database, result['sql'], result.get('parameters', []))
            returned = [[row[name] for name in result.get('columns', [])] for row in result.get('rows', [])]
            checks['unchanged_sql_replay_matches_returned_rows'] = (
                names == result.get('columns', []) and value_digest(replay) == value_digest(returned))
            checks['required_source_reads'] = source_reads_supported(oracle['reference_read_sources'], reads)
        except (ValueError, TypeError, KeyError, __import__('sqlite3').Error) as exc:
            reason = 'generated_sql_replay_' + type(exc).__name__
    checks['complete_result'] = (actual is not None
        and rows_equal(actual, oracle['full_expected_rows'], oracle['comparison_contract'])
        and (not oracle['requires_complete_unbounded_result'] or completeness == 'within_return_limit'))
    if not checks['status_ok']:
        reason = result.get('status') or outer.get('status') or record.get('error_type') or 'no_result'
    elif not all(checks.values()) and reason == 'not_run':
        reason = next(key for key, value in checks.items() if not value)
    return {'case_id': oracle['case_id'], 'pass': all(checks.values()), 'checks': checks,
        'failure_reason': None if all(checks.values()) else reason,
        'observed_status': result.get('status') or outer.get('status'),
        'clarification_code': result.get('clarification_code'), 'result_state': result.get('result_state'),
        'observed_row_count': len(actual) if actual is not None else None,
        'expected_row_count': oracle['expected_row_count'], 'result_completeness': completeness,
        'domain': oracle['domain'], 'family': oracle['family'], 'kind': oracle['kind'],
        'session_id': oracle['session_id'], 'turn_index': oracle['turn_index'], 'tags': oracle['tags']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--run-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source, database, public, bundle = verify_public(args.directory)
    oracles = load_oracles(args.directory, public)
    run = args.run_directory.resolve()
    manifest = load(run / 'RUN_MANIFEST.json')
    observations = run / 'observed.jsonl'
    if digest(observations) != manifest['observed_sha256']:
        raise ValueError('observed_report_changed')
    records = [json.loads(line) for line in observations.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len({r['case_id'] for r in records}) != len(records):
        raise ValueError('duplicate_observation')
    ids = {c['case_id'] for c in bundle['cases']}
    if any(r['case_id'] not in ids for r in records):
        raise ValueError('unexpected_observation')
    by_id = {r['case_id']: r for r in records}
    stable = run_integrity_supported(manifest, public)
    scored = [score_case(o, by_id.get(o['case_id']), database) for o in oracles]
    if not stable:
        for row in scored:
            row['pass'] = False
            row['failure_reason'] = 'run_integrity_or_implementation_unstable'
    sessions = {}
    for oracle, row in zip(oracles, scored):
        if oracle['kind'] == 'session':
            sessions.setdefault(oracle['session_id'], []).append(row['pass'])
    strata = {}
    for key in ('domain', 'family'):
        strata[key] = {label: {'passed': sum(r['pass'] for r in scored if r.get(key) == label),
                             'planned': sum(o[key] == label for o in oracles)}
                      for label in sorted({o[key] for o in oracles})}
    report = {'created_at': now(), 'scope': source['scope'], 'official_benchmark_score': False,
        'source_commit': source['source_commit'], 'source_manifest_sha256': public['source_manifest_sha256'],
        'database_sha256': public['database_sha256'], 'schema_sha256': public['schema_sha256'],
        'system_input_sha256': public['system_input_sha256'], 'oracle_sha256': public['oracle_sha256'],
        'mode': 'strict_real_model_full_result', 'planned_total': 56, 'executed_total': len(records),
        'not_run': [o['case_id'] for o in oracles if o['case_id'] not in by_id],
        'passed': sum(r['pass'] for r in scored), 'standalone_passed': sum(r['pass'] for r in scored if r.get('kind') == 'single'),
        'standalone_total': 36, 'session_turns_passed': sum(r['pass'] for r in scored if r.get('kind') == 'session'),
        'session_turns_total': 20, 'sessions_all_passed': sum(len(v) == 5 and all(v) for v in sessions.values()),
        'sessions_total': 4, 'sessions': {s: {'all_pass': len(v) == 5 and all(v), 'turn_passes': v} for s, v in sessions.items()},
        'strata': strata, 'stable': stable, 'cases': scored,
        'failure_counts': dict(Counter(r['failure_reason'] for r in scored if not r['pass'])),
        'run_manifest_sha256': digest(run / 'RUN_MANIFEST.json'), 'scorer_sha256': digest(Path(__file__)),
        'limitations': ['New authored questions on upstream fictional sample; not official question score.',
                       'Projection uses frozen physical roles or aliases explicitly requested in complex-output questions.',
                       'Source-correct read-only execution and full rows are checked; a single database snapshot is not formal SQL semantic equivalence.']}
    output = args.output.resolve() if args.output else run / 'SCORE.json'
    write_new(output, report)
    print(json.dumps({k: report[k] for k in ('planned_total', 'executed_total', 'passed', 'standalone_passed',
        'standalone_total', 'session_turns_passed', 'session_turns_total', 'sessions_all_passed', 'sessions_total', 'stable', 'failure_counts')}))
    return 0 if stable and report['passed'] == 56 else 1


if __name__ == '__main__':
    raise SystemExit(main())
