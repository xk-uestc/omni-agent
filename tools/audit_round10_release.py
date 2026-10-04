"""Seal round-ten evidence without rescoring previous failures."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_round6_release import api_summary, verify_source

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    raw = (ROOT / 'docs' / name).read_bytes()
    return json.loads(raw), {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    current = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((ROOT / 'ict-track8/backend').rglob('*.py'))}
    baseline, baseline_pin = read('ROUND9_INCREMENTAL_SOURCE_AUDIT_20261004.json')
    previous = baseline['current_backend_file_sha256']
    changed = sorted(p for p in previous if current.get(p) != previous[p])
    added = sorted(set(current) - set(previous))
    if (baseline['source_audit'] != 'PASS' or set(previous) - set(current) or changed != [
            'ict-track8/backend/evidence_recovery.py', 'ict-track8/backend/grounded_span_answer.py',
            'ict-track8/backend/knowledge_store.py', 'ict-track8/backend/source_span_answer.py']
            or added != ['ict-track8/backend/source_multi_span_answer.py']):
        raise ValueError('unexpected_backend_change_scope')
    names = {'local': 'LOCAL_REGRESSION_ROUND10_FINAL_20261004.json',
        'focused': 'ROUND10_SOURCE_REGRESSION_FINAL_20261004.json',
        'authored': 'ROUND10_AUTHORED_DOCUMENTS_CANDIDATE2_20261004.json',
        'process': 'ROUND10_PROCESS_SESSIONS_FINAL_20261004.json',
        'ohr_subset': 'ROUND10_MULTI_DOCUMENTS_CANDIDATE2_20261004.json',
        'core_targeted': 'ROUND10_CORE_TARGETED_FINAL_20261004.json'}
    runs, pins = {}, {'baseline_audit': baseline_pin}
    for key, name in names.items():
        runs[key], pins[key] = read(name)
        if verify_source(runs[key]) != current:
            raise ValueError('source_version_' + key)
    for key in ('local', 'focused'):
        if not runs[key]['ok'] or runs[key]['failed'] != 0:
            raise ValueError('regression_not_passed_' + key)
    api = {}
    authored = runs['authored']
    if (len(authored['cases']) != 6 or authored['total'] != 6
            or authored['reference_used_as_model_input'] is not False):
        raise ValueError('authored_denominator_or_reference')
    api['authored'] = api_summary([a for c in authored['cases'] for a in c['api_audits']])
    process = runs['process']
    if (process['planned_turns'] != 10 or process['executed_turns'] != 10
            or process['reference_used_as_model_input'] is not False
            or process['child_input_fields'] != ['question', 'store', 'session_id']
            or not process['all_ten_distinct_child_processes']):
        raise ValueError('process_recovery_protocol')
    pids = []
    for session in process['sessions']:
        if len(session['cases']) != 5 or not session['database_stable']:
            raise ValueError('process_session_denominator_or_database')
        previous_after = []
        for turn, case in enumerate(session['cases'], 1):
            if (case['turn'] != turn or case['before_context'] != previous_after
                    or len(case['before_context']) != turn - 1 or len(case['after_context']) != turn
                    or not case['context_restored_exactly'] or not case['fresh_process_verified']):
                raise ValueError('process_context_or_identity')
            previous_after = case['after_context']
            pids.append(case['pid'])
    if len(set(pids)) != 10 or process['parent_pid'] in pids:
        raise ValueError('processes_not_independent')
    api['process'] = api_summary([a for s in process['sessions'] for c in s['cases'] for a in c['api_audits']])
    # The process run predates a test-only fixture fix. Its stable backend and
    # tool pins still match; do not pretend its saved tests describe the fix.
    process_test_changes = sorted(path for path, sha in process['test_file_sha256_start'].items()
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != sha)
    if process_test_changes != ['ict-track8/tests/test_round10_process_sessions.py']:
        raise ValueError('unexpected_post_process_test_changes')
    subset = runs['ohr_subset']
    old, old_pin = read('OHR_ROUND9_FINAL_20261004.json')
    if (subset['baseline']['sha256'] != old_pin['sha256'] or subset['gold_used_as_corpus_or_model_input'] is not False
            or len(subset['cases']) != 6 or subset['summary']['executed'] != 6):
        raise ValueError('ohr_subset_protocol')
    old_cases = {c['ID']: c for c in old['cases']}
    for case in subset['cases']:
        baseline_case = old_cases[case['ID']]
        if (case['question'] != baseline_case['question'] or case['doc_name'] != baseline_case['doc_name']
                or case['official_answer_scoring_only'] != baseline_case['official_answer']):
            raise ValueError('ohr_subset_input_changed')
    api['ohr_subset'] = api_summary([a for c in subset['cases'] for a in c['api_audits']])
    core = runs['core_targeted']
    if len(core['cases']) != 7 or core['not_run']:
        raise ValueError('core_targeted_denominator')
    api['core_targeted'] = api_summary([*core['preflight'], *(a for c in core['cases'] for a in c['api_audits'])],
        dropped=core['api_summary']['dropped_calls'], saved=core['api_summary'])
    http, pins['http'] = read('ROUND10_HTTP_FINAL_20261004.json')
    if (not http['ok'] or http['implementation_file_sha256'] != current
            or http['implementation_file_sha256_end'] != current):
        raise ValueError('http_contract')
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'source_audit': 'PASS',
        'scope': 'source_contract_and_process_recovery_release_not_full_goal_completion',
        'backend_file_sha256': current,
        'backend_lf_normalized_sha256': {p: hashlib.sha256((ROOT / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in current}, 'changed_backend_files': changed, 'added_backend_files': added,
        'reports': pins, 'api_summary': api,
        'local_regression': {k: runs['local'][k] for k in ('passed', 'failed', 'skipped', 'warnings', 'subtests_passed')},
        'focused_regression': {k: runs['focused'][k] for k in ('passed', 'failed', 'skipped')},
        'authored': {'passed': authored['passed'], 'total': 6},
        'process_recovery': {'passed': process['passed_turns'], 'total': 10,
            'complete_sessions': process['complete_five_turn_sessions'], 'distinct_pids': len(set(pids)),
            'later_test_only_changes': process_test_changes},
        'ohr_subset': subset['summary'], 'core_targeted': {'passed': core['passed'], 'total': 7},
        'http_checks_passed': sum(http['checks'].values()),
        'limitations': ['Original 304/1 regression and its offline-audit erratum are retained.',
            'OHR subset lexical accuracy did not improve; its failures remain in the denominator.',
            'Matching inventories cover supplied evidence, not unseen pages or sources.',
            'Self-authored six cases and two five-turn domains do not prove arbitrary domain generalization.',
            'This audit does not mark the three requested capabilities fully completed.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'source_audit': 'PASS', 'local': report['local_regression'],
        'process': report['process_recovery'], 'core': report['core_targeted']}))


if __name__ == '__main__':
    main()
