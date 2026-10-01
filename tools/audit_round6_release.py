"""Seal fixed-source round-six evidence without model requests or rescoring.

Reads saved reports and audit fields, never the cross-schema input or oracle.
All regressions and provider failures remain in their original denominators.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from compare_ohr_runs import compare

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    path = Path(path)
    return json.loads(path.read_bytes()), digest(path)


def verify_source(run):
    start = run.get('implementation_sha256_start', run.get('implementation_file_sha256_start',
                    run.get('implementation_file_sha256')))
    end = run.get('implementation_sha256_end', run.get('implementation_file_sha256_end'))
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
    if any(row.get('model_verified') is not True for row in audits if row.get('status') == 'completed'):
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
    regression_name = 'LOCAL_REGRESSION_ROUND6_FINAL_20261002.json'
    sakila_name = 'SAKILA_ROUND6_OPTIMIZED_ACCEPTANCE_20261002.json'
    core, core_sha = load(DOCS / core_name)
    rag, rag_sha = load(DOCS / rag_name)
    regression, regression_sha = load(DOCS / regression_name)
    sakila, sakila_sha = load(DOCS / sakila_name)
    manifest_path = args.sakila_run_directory / 'RUN_MANIFEST.json'
    manifest, manifest_sha = load(manifest_path)
    sources = [verify_source(run) for run in (core, rag, regression, manifest)]
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
    observed_path = args.sakila_run_directory / 'observed.jsonl'
    if (digest(observed_path) != manifest['observed_sha256'] or
            sakila['run_manifest_sha256'] != manifest_sha or manifest['reference_opened'] is not False
            or manifest['requested_model'] != 'gpt-6-luna' or manifest['reasoning'] != 'medium'):
        raise ValueError('sakila_provenance_mismatch')
    observations = [json.loads(line) for line in observed_path.read_text(encoding='utf-8').splitlines()]
    sakila_api = api_summary([audit for row in observations for audit in row['api_audits']],
                            dropped=sum(row['api_audit_dropped'] for row in observations))
    core_api = api_summary([*core['preflight'], *(audit for row in core['cases'] for audit in row['api_audits'])],
                           dropped=core['api_summary']['dropped_calls'], saved=core['api_summary'])
    rag_api = api_summary(rag['api_audits'], dropped=rag['api_summary']['dropped_calls'], saved=rag['api_summary'])
    previous_core, _ = load(DOCS / 'REAL_MODEL_CORE_ROUND5_ACCEPTANCE_20261002.json')
    previous_sakila, _ = load(DOCS / 'SAKILA_ROUND6_ISOLATED_BASELINE_20261002.json')
    rag_paired = compare(DOCS / 'OHR_ROUND5_UNSEEN_ACCEPTANCE_20261002.json', DOCS / rag_name)
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'fixed_source_round6_evidence_not_four_core_goal_completion',
        'source_audit': 'PASS', 'same_final_backend': True, 'backend_files_verified': len(sources[0]),
        'backend_file_sha256': sources[0], 'goal_status': 'active',
        'core': {'report': core_name, 'sha256': core_sha, 'api_summary': core_api,
                 'paired_with_round5': paired_core(previous_core, core)},
        'rag': {'report': rag_name, 'sha256': rag_sha, 'api_summary': rag_api,
                'metrics': rag['summary']['model'],
                'paired_with_round5': {key: value for key, value in rag_paired.items() if key != 'cases'}},
        'sakila': {'report': sakila_name, 'sha256': sakila_sha, 'run_manifest_sha256': manifest_sha,
                   'api_summary': sakila_api, 'paired_with_isolated_baseline': paired_sakila(previous_sakila, sakila)},
        'regression': {'report': regression_name, 'sha256': regression_sha,
                       **{key: regression[key] for key in ('passed', 'failed', 'skipped', 'warnings', 'subtests_passed')}},
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
