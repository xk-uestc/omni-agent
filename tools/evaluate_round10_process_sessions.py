"""Five-turn topic recovery with a fresh OS process for every production turn.

Authored advertising/observatory probes reuse ROUND8 questions and databases.
Reference SQL is executed only by the parent, never passed to a child/model.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(directory):
    return {p.relative_to(ROOT).as_posix(): digest(p)
            for p in sorted(directory.rglob('*.py'))}


def tool_snapshot():
    return {f'tools/{name}': digest(ROOT / 'tools' / name) for name in (
        'evaluate_round10_process_sessions.py', 'evaluate_round8_topic_sessions.py',
        'model_runtime.py')}


def contexts(store, session_id):
    return [asdict(turn) for turn in store.context(session_id)]


def safe_audits(client, provider):
    fields = ('provider', 'model', 'reasoning', 'reasoning_effort', 'operation',
              'http_status', 'status', 'model_verified', 'response_model',
              'call_index', 'latency_ms', 'input_tokens', 'output_tokens',
              'reasoning_tokens')
    return [{**{key: audit[key] for key in fields if key in audit}, 'component': name}
            for name, api in (('router', client), ('sql', provider))
            for audit in api.audit_history]


def production_turn(payload):
    # No reference SQL, expected rows, future questions or parent score accepted.
    if not isinstance(payload, dict) or set(payload) != {'question', 'store', 'session_id'}:
        raise ValueError('child_input_contract_invalid')
    if not all(isinstance(value, str) and value for value in payload.values()):
        raise ValueError('child_input_contract_invalid')
    work = Path(payload['store']).resolve()
    if not work.is_relative_to((ROOT / 'runtime').resolve()):
        raise ValueError('child_store_outside_runtime')
    from model_runtime import enable_local_model
    from evaluate_round8_topic_sessions import build_agent
    configured = enable_local_model()
    if configured['model'] != 'gpt-6-luna' or configured['reasoning'] != 'medium':
        raise ValueError('model_contract_invalid')
    agent, engine, provider, client = build_agent(work, work / 'source.sqlite')
    before = contexts(agent.conversations, payload['session_id'])
    client.reset_audit()
    provider.reset_audit()
    engine.execution_rejections.clear()
    response, error = None, None
    try:
        response = agent.query(payload['question'], session_id=payload['session_id'])
    except Exception:
        # Exception text can contain endpoint/auth/provider output. Keep fixed code.
        error = 'production_query_exception'
    return {'pid': os.getpid(), 'before_context': before,
            'after_context': contexts(agent.conversations, payload['session_id']),
            'response': response, 'error_code': error,
            'api_audits': safe_audits(client, provider),
            'execution_rejections': list(engine.execution_rejections)}


def child_main():
    try:
        payload = json.loads(sys.stdin.read())
        # Never mix uncontrolled production prints into the IPC stream.
        with redirect_stdout(io.StringIO()):
            result = production_turn(payload)
    except Exception:
        result = {'pid': os.getpid(), 'error_code': 'child_initialization_failed',
                  'api_audits': [], 'response': None}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


def prepare_domain(work, table, metric):
    work.mkdir()
    database = work / 'source.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(f'''CREATE TABLE countries(id INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE {table}(id INTEGER PRIMARY KEY,country_id INTEGER REFERENCES countries(id),
                event_date TEXT,{metric} REAL);
            CREATE TABLE contracts(id INTEGER PRIMARY KEY,country_id INTEGER REFERENCES countries(id),
                contract_date TEXT,amount REAL);
            INSERT INTO countries VALUES(1,'中国'),(2,'日本');
            INSERT INTO {table} VALUES(1,1,'2025-02-03',12),(2,1,'2025-03-03',7),
                (3,2,'2025-02-03',31),(4,1,'2024-02-03',99);
            INSERT INTO contracts VALUES(1,1,'2024-02-03',80),(2,2,'2024-03-03',1000),
                (3,1,'2025-02-03',130);''')
    scope = f'以{table}.event_date为准，统计2025年{table}.{metric}合计，只筛选countries.name为中国，关联{table}.country_id=countries.id。'
    topic = '换个主题，以contracts.contract_date为准，统计2024年contracts.amount合计，只筛选countries.name为中国，关联contracts.country_id=countries.id。'
    recovery = '以contracts.contract_date为准，统计2025年contracts.amount合计，只筛选countries.name为日本，关联contracts.country_id=countries.id。'
    return [(scope, f"SELECT SUM(x.{metric}) FROM {table} x JOIN countries c ON x.country_id=c.id WHERE c.name='中国' AND x.event_date>='2025-01-01' AND x.event_date<'2026-01-01'"),
            ('那日本呢', f"SELECT SUM(x.{metric}) FROM {table} x JOIN countries c ON x.country_id=c.id WHERE c.name='日本' AND x.event_date>='2025-01-01' AND x.event_date<'2026-01-01'"),
            (topic, "SELECT SUM(x.amount) FROM contracts x JOIN countries c ON x.country_id=c.id WHERE c.name='中国' AND x.contract_date>='2024-01-01' AND x.contract_date<'2025-01-01'"),
            ('那排除未说明的特殊合同呢', None),
            (recovery, "SELECT SUM(x.amount) FROM contracts x JOIN countries c ON x.country_id=c.id WHERE c.name='日本' AND x.contract_date>='2025-01-01' AND x.contract_date<'2026-01-01'")]


def invoke_child(question, work, session_id):
    payload = {'question': question, 'store': str(work.resolve()), 'session_id': session_id}
    run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
        encoding='utf-8', errors='replace', cwd=ROOT, timeout=600)
    if run.returncode != 0:
        return {'error_code': 'child_exit_failed', 'exit_code': run.returncode,
                'api_audits': [], 'response': None}
    try:
        result = json.loads(run.stdout)
        if not isinstance(result, dict):
            raise ValueError('invalid_child_output')
        return result
    except (ValueError, TypeError):
        return {'error_code': 'child_output_invalid', 'api_audits': [], 'response': None}


def score(observation, reference, database):
    response = observation.get('response') or {}
    result = response.get('result') or {}
    audits = observation.get('api_audits', [])
    api_ok = bool(audits) and all(a.get('status') == 'completed'
        and a.get('model_verified') is True and a.get('model') == 'gpt-6-luna'
        and a.get('reasoning') == 'medium'
        and type(a.get('http_status')) is int and 200 <= a['http_status'] < 300
        and re.fullmatch(r'gpt-6-luna(?:-\d{4}-\d{2}-\d{2})?', str(a.get('response_model'))) is not None
        for a in audits)
    actual = [[row[column] for column in result.get('columns', [])]
              for row in result.get('rows', [])]
    with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True) as db:
        expected = [list(row) for row in db.execute(reference)] if reference else []
        replay = [list(row) for row in db.execute(result['sql'], result.get('parameters', []))] if result.get('sql') else []
    semantics_ok = (response.get('status') == 'clarification' if reference is None else
        response.get('status') == 'ok' and actual == expected == replay)
    return {'reference_rows_scoring_only': expected, 'production_rows': actual,
            'production_sql_replay_rows': replay, 'verified_api_pass': api_ok,
            'semantic_pass': semantics_ok}


def run_domain(directory, domain, table, metric, stopped, backend_before, tests_before, tools_before):
    group_backend_start = snapshot(ROOT / 'ict-track8/backend')
    group_tests_start = snapshot(ROOT / 'ict-track8/tests')
    group_tools_start = tool_snapshot()
    work = directory / domain
    cases = prepare_domain(work, table, metric)
    database = work / 'source.sqlite'
    database_before = digest(database)
    observations, previous_after, pids = [], [], set()
    for turn, (question, reference) in enumerate(cases, 1):
        base = {'turn': turn, 'question': question, 'pass': False, 'executed': False}
        if stopped.is_set():
            observations.append({**base, 'error_code': 'not_executed_after_auth_failure'})
            continue
        if (snapshot(ROOT / 'ict-track8/backend') != backend_before
                or snapshot(ROOT / 'ict-track8/tests') != tests_before or tool_snapshot() != tools_before):
            observations.append({**base, 'error_code': 'source_changed'})
            continue
        try:
            child = invoke_child(question, work, domain)
        except subprocess.TimeoutExpired:
            child = {'error_code': 'child_timeout', 'api_audits': [], 'response': None}
        except Exception:
            child = {'error_code': 'child_launch_failed', 'api_audits': [], 'response': None}
        audits = child.get('api_audits', [])
        if any(a.get('http_status') in (401, 403) for a in audits):
            stopped.set()
        pid = child.get('pid')
        identity_ok = type(pid) is int and pid != os.getpid() and pid not in pids
        before = child.get('before_context')
        after = child.get('after_context')
        recovery_ok = before == previous_after and isinstance(before, list) and len(before) == turn - 1
        persisted_ok = isinstance(after, list) and len(after) == turn
        if isinstance(pid, int):
            pids.add(pid)
        previous_after = after
        try:
            scored = score(child, reference, database)
        except Exception:
            scored = {'verified_api_pass': False, 'semantic_pass': False,
                      'error_code': 'parent_sql_scoring_failed'}
        row = {**base, 'executed': True, **child, **scored,
               'fresh_process_verified': identity_ok, 'context_restored_exactly': recovery_ok,
               'persistent_turn_count_verified': persisted_ok}
        row['pass'] = (identity_ok and recovery_ok and persisted_ok
            and scored['verified_api_pass'] and scored['semantic_pass'] and not child.get('error_code'))
        observations.append(row)
        print(json.dumps({'domain': domain, 'turn': turn, 'pass': row['pass'],
                          'status': (child.get('response') or {}).get('status')}), flush=True)
    group_backend_end = snapshot(ROOT / 'ict-track8/backend')
    group_tests_end = snapshot(ROOT / 'ict-track8/tests')
    group_tools_end = tool_snapshot()
    return {'domain': domain, 'planned_turns': 5, 'cases': observations,
            'implementation_file_sha256_start': group_backend_start,
            'implementation_file_sha256_end': group_backend_end,
            'test_file_sha256_start': group_tests_start,
            'test_file_sha256_end': group_tests_end,
            'evaluation_tool_sha256_start': group_tools_start,
            'evaluation_tool_sha256_end': group_tools_end,
            'implementation_stable': (group_backend_start == group_backend_end == backend_before
                and group_tests_start == group_tests_end == tests_before
                and group_tools_start == group_tools_end == tools_before),
            'database_sha256_start': database_before, 'database_sha256_end': digest(database),
            'database_stable': digest(database) == database_before,
            'process_count': len(pids), 'process_ids': sorted(pids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return child_main()
    if not args.output or args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    backend_before = snapshot(ROOT / 'ict-track8/backend')
    tests_before = snapshot(ROOT / 'ict-track8/tests')
    tools_before = tool_snapshot()
    directory = ROOT / 'runtime' / ('round10-process-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True)
    stopped = Event()
    domains = [('advertising', 'campaigns', 'spend'), ('observatory', 'observations', 'exposure')]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_domain, directory, *domain, stopped,
                               backend_before, tests_before, tools_before) for domain in domains]
        sessions = []
        for domain, future in zip(domains, futures):
            try:
                sessions.append(future.result())
            except Exception:
                # A broken group is still five failures, never a smaller denominator.
                sessions.append({'domain': domain[0], 'planned_turns': 5,
                    'cases': [{'turn': turn, 'pass': False, 'executed': False,
                               'error_code': 'domain_execution_failed'} for turn in range(1, 6)],
                    'database_stable': False, 'process_count': 0, 'process_ids': []})
    backend_after = snapshot(ROOT / 'ict-track8/backend')
    tests_after = snapshot(ROOT / 'ict-track8/tests')
    tools_after = tool_snapshot()
    stable = backend_before == backend_after and tests_before == tests_after and tools_before == tools_after
    all_cases = [case for session in sessions for case in session['cases']]
    pids = [case['pid'] for case in all_cases if case.get('pid')]
    distinct = len(pids) == len(set(pids)) == 10
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'authored_two_domain_five_turn_fresh_os_process_recovery_not_official_accuracy',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'reference_used_as_model_input': False,
        'child_input_fields': ['question', 'store', 'session_id'], 'parent_pid': os.getpid(),
        'planned_turns': 10, 'executed_turns': sum(c['executed'] for c in all_cases),
        'passed_turns': sum(c['pass'] for c in all_cases),
        'complete_five_turn_sessions': sum(all(c['pass'] for c in s['cases']) for s in sessions),
        'all_ten_distinct_child_processes': distinct, 'auth_failure_stopped_subsequent_turns': stopped.is_set(),
        'auth_stop_policy': 'shared_stop_prevents_new_turns_existing_inflight_request_can_finish',
        'implementation_file_sha256_start': backend_before, 'implementation_file_sha256_end': backend_after,
        'test_file_sha256_start': tests_before, 'test_file_sha256_end': tests_after,
        'evaluation_tool_sha256_start': tools_before, 'evaluation_tool_sha256_end': tools_after,
        'implementation_stable': stable, 'sessions': sessions,
        'ok': stable and distinct and all(c['pass'] for c in all_cases)
            and all(s['database_stable'] and s.get('implementation_stable') for s in sessions)}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in ('ok', 'passed_turns', 'executed_turns', 'implementation_stable')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
