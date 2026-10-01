"""Seal fixed-source round-six evidence without model requests or rescoring.

Reads saved reports and audit fields, never the cross-schema input or oracle.
All regressions and provider failures remain in their original denominators.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from compare_ohr_runs import compare

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    path = Path(path)
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def verify_source(run):
    start = run.get('implementation_sha256_start', run.get('implementation_file_sha256_start',
                    run.get('implementation_file_sha256', run.get('implementation_before', {}).get('files'))))
    end = run.get('implementation_sha256_end', run.get('implementation_file_sha256_end',
                  run.get('implementation_after', {}).get('files')))
    if not run.get('implementation_stable') or not start or start != end:
        raise ValueError('run_source_not_stable')
    if any(digest(ROOT / path) != value for path, value in start.items()):
        raise ValueError('run_source_not_current')
    backend = {path: value for path, value in start.items() if path.startswith('ict-track8/backend/')}
    actual = {path.relative_to(ROOT).as_posix(): digest(path)
              for path in (ROOT / 'ict-track8/backend').rglob('*.py')}
    if backend != actual:
        raise ValueError('backend_pin_incomplete')
    return backend


def api_summary(audits, *, dropped=0, saved=None):
    if dropped != 0 or not audits or any(
            row.get('model') != 'gpt-6-luna' or row.get('reasoning') != 'medium'
            for row in audits):
        raise ValueError('model_or_audit_contract_mismatch')
    completed = sum(row.get('status') == 'completed' for row in audits)
    failed = sum(row.get('status') == 'failed' for row in audits)
    if completed + failed != len(audits):
        raise ValueError('unknown_api_status')
    if any(row.get('model_verified') is not True
           or type(row.get('http_status')) is not int or not 200 <= row['http_status'] < 300
           or not isinstance(row.get('response_model'), str)
           or not re.fullmatch(r'gpt-6-luna(?:-\d{4}-\d{2}-\d{2})?', row['response_model'])
           for row in audits if row.get('status') == 'completed'):
        raise ValueError('completed_api_model_not_verified')
    summary = {'retained_calls': len(audits), 'completed_calls': completed,
               'failed_calls': failed, 'dropped_calls': dropped}
    for field in ('input_tokens', 'output_tokens', 'total_tokens'):
        known = [row[field] for row in audits if type(row.get(field)) is int and row[field] >= 0]
        summary[field + '_visible_lower_bound'] = sum(known)
        summary[field + '_unknown_calls'] = len(audits) - len(known)
    if saved and any(saved.get(key) != summary[key] for key in
                     ('retained_calls', 'completed_calls', 'failed_calls', 'dropped_calls')):
        raise ValueError('saved_api_counts_mismatch')
    return summary


def paired_core(before, after):
    if (not before.get('implementation_stable') or
            any(before.get(key) != after.get(key) or after.get(key) is None
                for key in ('cases_sha256', 'database_sha256'))):
        raise ValueError('core_paired_input_mismatch')
    for run in (before, after):
        if (len(run['cases']) != 44 or len({row['id'] for row in run['cases']}) != 44
                or any(type(row['pass']) is not bool for row in run['cases'])
                or run['passed'] != sum(row['pass'] for row in run['cases'])):
            raise ValueError('core_saved_score_counts_mismatch')
    old, new = ({row['id']: row for row in run['cases']} for run in (before, after))
    if len(old) != 44 or len(new) != 44 or old.keys() != new.keys():
        raise ValueError('core_paired_denominator_mismatch')
    changes = []
    for identifier, previous in old.items():
        current = new[identifier]
        if any(previous.get(key) != current.get(key) for key in ('question', 'kind', 'sources', 'contains')):
            raise ValueError('core_paired_case_changed')
        if previous['pass'] != current['pass'] or not current['pass']:
            changes.append({'id': identifier, 'before_pass': previous['pass'],
                            'after_pass': current['pass'], 'after_checks': current['checks']})
    return {'before_passed': before['passed'], 'after_passed': after['passed'],
            'total': 44, 'delta': after['passed'] - before['passed'], 'changes': changes}


def paired_sakila(before, after):
    for run in (before, after):
        if (run.get('stable') is not True or run.get('planned_total') != 56
                or run.get('executed_total') != 56 or run.get('not_run')):
            raise ValueError('sakila_run_incomplete_or_unstable')
        cases = run['cases']
        if (len(cases) != 56 or len({row['case_id'] for row in cases}) != 56
                or any(type(row['pass']) is not bool for row in cases)
                or run['passed'] != sum(row['pass'] for row in cases)
                or run['failure_counts'] != dict(Counter(row['failure_reason'] for row in cases if not row['pass']))):
            raise ValueError('sakila_saved_score_counts_mismatch')
        for kind, passed, total, required in (
                ('single', 'standalone_passed', 'standalone_total', 36),
                ('session', 'session_turns_passed', 'session_turns_total', 20)):
            selected = [row for row in cases if row['kind'] == kind]
            if len(selected) != required or run[total] != required or run[passed] != sum(row['pass'] for row in selected):
                raise ValueError('sakila_saved_kind_counts_mismatch')
        sessions = {}
        for row in cases:
            if row['kind'] == 'session':
                sessions.setdefault(row['session_id'], []).append(row)
        if len(sessions) != 4 or run['sessions_total'] != 4 or set(sessions) != set(run['sessions']):
            raise ValueError('sakila_saved_session_counts_mismatch')
        whole_passed = 0
        for key, turns in sessions.items():
            if sorted(row['turn_index'] for row in turns) != list(range(1, 6)):
                raise ValueError('sakila_saved_turn_indices_mismatch')
            passes = [row['pass'] for row in sorted(turns, key=lambda row: row['turn_index'])]
            if run['sessions'][key] != {'all_pass': all(passes), 'turn_passes': passes}:
                raise ValueError('sakila_saved_session_passes_mismatch')
            whole_passed += all(passes)
        if run['sessions_all_passed'] != whole_passed:
            raise ValueError('sakila_saved_whole_session_count_mismatch')
    for key in ('system_input_sha256', 'oracle_sha256', 'database_sha256', 'schema_sha256',
                'source_manifest_sha256', 'scorer_sha256'):
        if before.get(key) is None or before.get(key) != after.get(key):
            raise ValueError('sakila_paired_contract_mismatch_' + key)
    old, new = ({row['case_id']: row for row in run['cases']} for run in (before, after))
    if len(old) != 56 or len(new) != 56 or old.keys() != new.keys():
        raise ValueError('sakila_paired_denominator_mismatch')
    changes = [{'case_id': key, 'before_pass': old[key]['pass'], 'after_pass': new[key]['pass'],
                'after_failure_reason': new[key]['failure_reason']}
               for key in old if old[key]['pass'] != new[key]['pass'] or not new[key]['pass']]
    return {'before_passed': before['passed'], 'after_passed': after['passed'], 'total': 56,
            'delta': after['passed'] - before['passed'], 'changes': changes,
            'standalone': [before['standalone_passed'], after['standalone_passed'], 36],
            'session_turns': [before['session_turns_passed'], after['session_turns_passed'], 20],
            'whole_sessions': [before['sessions_all_passed'], after['sessions_all_passed'], 4],
            'failure_counts': after['failure_counts']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sakila-run-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=DOCS / 'ROUND6_FINAL_SOURCE_AUDIT_20261002.json')
    args = parser.parse_args()
    if args.output.resolve().parent != DOCS.resolve() or args.output.exists():
        parser.error('Output must be a new report directly in docs')
    core_name = 'REAL_MODEL_CORE_ROUND6_ACCEPTANCE_20261002.json'
    rag_name = 'OHR_ROUND6_FIXED_SUBSET_ACCEPTANCE_20261002.json'
    regression_name = 'LOCAL_REGRESSION_ROUND6_FINAL_REVIEWED_20261002.json'
    artifact_name = 'COMPLETE_RESULTS_ADVENTUREWORKS_ROUND6_FINAL_REVIEWED_20261002.json'
    sakila_name = 'SAKILA_ROUND6_OPTIMIZED_ACCEPTANCE_20261002.json'
    core, core_sha = load(DOCS / core_name)
    rag, rag_sha = load(DOCS / rag_name)
    regression, regression_sha = load(DOCS / regression_name)
    artifact, artifact_sha = load(DOCS / artifact_name)
    sakila, sakila_sha = load(DOCS / sakila_name)
    manifest_path = args.sakila_run_directory / 'RUN_MANIFEST.json'
    manifest, manifest_sha = load(manifest_path)
    sources = [verify_source(run) for run in (core, rag, regression, manifest, artifact)]
    if any(source != sources[0] for source in sources[1:]):
        raise ValueError('different_final_backend_versions')
    if (core['planned_cases'] != 44 or core['total'] != 44 or len(core['cases']) != 44
            or core['not_run'] or core['passed'] != sum(row['pass'] for row in core['cases'])):
        raise ValueError('core_denominator_mismatch')
    if (len(rag['cases']) != 36 or len({row['ID'] for row in rag['cases']}) != 36
            or rag['summary']['model']['attempted'] != 36
            or rag.get('gold_used_as_corpus_or_model_input') is not False
            or len(rag['documents']) != 15
            or any(row['ingestion_status'] != 'ok' for row in rag['documents'])):
        raise ValueError('rag_contract_mismatch')
    if (not regression['ok'] or regression['failed'] != 0 or regression['exit_code'] != 0
            or regression['passed'] < 2793):
        raise ValueError('local_regression_failed')
    if (artifact['model_api_calls'] != 0 or artifact['total_exposed_queries'] != 6
            or artifact['passed_complete_artifact_checks'] != 6 or len(artifact['cases']) != 6
            or len({row['id'] for row in artifact['cases']}) != 6
            or any(row['artifact_validation_pass'] is not True
                   or not all(row['checks'].values()) or row['actual_artifact_rows'] != row['reference_rows']
                   or not 0 <= row['preview_rows'] <= 100 for row in artifact['cases'])):
        raise ValueError('complete_artifact_development_verification_failed')
    observed_path = args.sakila_run_directory / 'observed.jsonl'
    observed_raw = observed_path.read_bytes()
    if (hashlib.sha256(observed_raw).hexdigest() != manifest['observed_sha256'] or
            sakila['run_manifest_sha256'] != manifest_sha or manifest['reference_opened'] is not False
            or manifest['requested_model'] != 'gpt-6-luna' or manifest['reasoning'] != 'medium'):
        raise ValueError('sakila_provenance_mismatch')
    observations = [json.loads(line) for line in observed_raw.decode('utf-8').splitlines()]
    if (len(observations) != 56 or len({row['case_id'] for row in observations}) != 56
            or {row['case_id'] for row in observations} != {row['case_id'] for row in sakila['cases']}
            or sakila['scorer_sha256'] != digest(ROOT / 'tools/score_cross_schema_round5.py')
            or manifest['implementation_file_sha256']['tools/score_cross_schema_round5.py'] != sakila['scorer_sha256']):
        raise ValueError('sakila_observation_or_scorer_pin_mismatch')
    sakila_api = api_summary([audit for row in observations for audit in row['api_audits']],
                            dropped=sum(row['api_audit_dropped'] for row in observations))
    core_api = api_summary([*core['preflight'], *(audit for row in core['cases'] for audit in row['api_audits'])],
                           dropped=core['api_summary']['dropped_calls'], saved=core['api_summary'])
    rag_api = api_summary(rag['api_audits'], dropped=rag['api_summary']['dropped_calls'], saved=rag['api_summary'])
    core_baseline_name = 'REAL_MODEL_CORE_ROUND5_ACCEPTANCE_20261002.json'
    sakila_baseline_name = 'SAKILA_ROUND6_ISOLATED_BASELINE_20261002.json'
    previous_core, core_baseline_sha = load(DOCS / core_baseline_name)
    previous_sakila, sakila_baseline_sha = load(DOCS / sakila_baseline_name)
    rag_paired = compare(DOCS / 'OHR_ROUND5_UNSEEN_ACCEPTANCE_20261002.json', DOCS / rag_name)
    if rag_paired['optimized']['sha256'] != rag_sha:
        raise ValueError('rag_report_changed_during_sealing')
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'fixed_source_round6_evidence_not_four_core_goal_completion',
        'source_audit': 'PASS', 'same_final_backend': True, 'backend_files_verified': len(sources[0]),
        'backend_file_sha256': sources[0], 'goal_status': 'active',
        'core': {'report': core_name, 'sha256': core_sha, 'api_summary': core_api,
                 'baseline_report': core_baseline_name, 'baseline_sha256': core_baseline_sha,
                 'paired_with_round5': paired_core(previous_core, core)},
        'rag': {'report': rag_name, 'sha256': rag_sha, 'api_summary': rag_api,
                'metrics': rag['summary']['model'],
                'paired_with_round5': {key: value for key, value in rag_paired.items() if key != 'cases'}},
        'sakila': {'report': sakila_name, 'sha256': sakila_sha, 'run_manifest_sha256': manifest_sha,
                   'baseline_report': sakila_baseline_name, 'baseline_sha256': sakila_baseline_sha,
                   'api_summary': sakila_api, 'paired_with_isolated_baseline': paired_sakila(previous_sakila, sakila)},
        'regression': {'report': regression_name, 'sha256': regression_sha,
                       **{key: regression[key] for key in ('passed', 'failed', 'skipped', 'warnings', 'subtests_passed')}},
        'complete_artifact': {'report': artifact_name, 'sha256': artifact_sha, 'model_api_calls': 0,
            'passed': artifact['passed_complete_artifact_checks'], 'total': artifact['total_exposed_queries'],
            'legacy_complete_rows_passed': sum(row['legacy_complete_rows_match'] for row in artifact['cases']),
            'full_cells_from_product_pagination_verified': True},
        'limitations': ['One run per version is not statistical significance or a contest score.',
            'Core questions are exposed development fixtures.',
            'OHR is a fixed resource-biased subset repeated after its first run, not a new blind test or full benchmark.',
            'OHR EM and lexical F1 do not prove semantic correctness or entailment.',
            'Sakila is an authored audit of public fictional sample data, not official benchmark questions.',
            'Complete artifact opt-in is measured separately and does not change the frozen Sakila scorer.',
            'Provider failures, missing usage, regressions and fixed denominators are preserved.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'source_audit': 'PASS', 'same_final_backend': True,
        'core': core['passed'], 'core_total': 44,
        'rag_em': rag['summary']['model']['normalized_exact_matches'],
        'rag_f1': rag['summary']['model']['mean_english_token_f1'],
        'sakila': sakila['passed'], 'sakila_total': 56, 'regression': regression['passed']}))


if __name__ == '__main__':
    main()
