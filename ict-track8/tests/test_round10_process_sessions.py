"""Evaluation contract tests only; no real model requests."""
from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/evaluate_round10_process_sessions.py'
spec = importlib.util.spec_from_file_location('round10_process_probe', TOOL)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def audit(status=200):
    return {'model': 'gpt-6-luna', 'reasoning': 'medium', 'status': 'completed',
            'model_verified': True, 'http_status': status, 'response_model': 'gpt-6-luna'}


def test_questions_database_and_reference_sql_match_round8(tmp_path):
    # Evaluate only the fixture declarations from the old evaluator; do not run it.
    tree = ast.parse((ROOT / 'tools/evaluate_round8_topic_sessions.py').read_text(encoding='utf-8'))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    loop = next(node for node in main.body if isinstance(node, ast.For))
    names = {'scope', 'topic', 'recovery', 'cases'}
    assignments = [node for node in loop.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)]
    for domain, table, metric in [('advertising', 'campaigns', 'spend'), ('observatory', 'observations', 'exposure')]:
        namespace = {'table': table, 'metric': metric}
        exec(compile(ast.Module(body=assignments, type_ignores=[]), '<fixture-declarations>', 'exec'), namespace)
        cases = probe.prepare_domain(tmp_path / domain, table, metric)
        assert cases == namespace['cases']
        with sqlite3.connect(tmp_path / domain / 'source.sqlite') as connection:
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone() == (4,)


@pytest.mark.parametrize('payload', [None, {}, {'question': 'x', 'store': 'x', 'session_id': 'x', 'reference': 'x'},
                                   {'question': 'x', 'store': 'x', 'session_id': 1}])
def test_child_contract_rejects_future_or_reference_fields_before_model(payload):
    with pytest.raises(ValueError, match='child_input_contract_invalid'):
        probe.production_turn(payload)


def test_child_rejects_nonruntime_storage_before_model(monkeypatch):
    def unexpected_model_configuration(*args, **kwargs):
        raise AssertionError('Rejected storage must never load the real model configuration')
    monkeypatch.setattr('model_runtime.enable_local_model', unexpected_model_configuration)
    outside = probe.ROOT.parent / 'round10-outside-runtime'
    with pytest.raises(ValueError, match='child_store_outside_runtime'):
        probe.production_turn({'question': 'x', 'store': str(outside), 'session_id': 'x'})


def test_real_worker_cli_invalid_input_has_no_exception_text_or_auth():
    run = subprocess.run([sys.executable, str(TOOL), '--worker'], input='{"future_question":"secret"}',
                         text=True, encoding='utf-8', capture_output=True, timeout=20)
    output = json.loads(run.stdout)
    assert run.returncode == 0
    assert output['pid'] != probe.os.getpid()
    assert output['error_code'] == 'child_initialization_failed'
    assert 'secret' not in run.stdout and not run.stderr


def test_invoke_child_sends_only_current_question_store_session(monkeypatch, tmp_path):
    captured = {}
    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, '{"pid":12345}', '')
    monkeypatch.setattr(probe.subprocess, 'run', fake_run)
    assert probe.invoke_child('那日本呢', tmp_path, 'advertising') == {'pid': 12345}
    assert set(json.loads(captured['input'])) == {'question', 'store', 'session_id'}
    assert json.loads(captured['input'])['question'] == '那日本呢'
    assert 'reference' not in captured['input']


def test_score_requires_verified_actual_authorized_model_even_clarification(tmp_path):
    path = tmp_path / 'source.sqlite'
    sqlite3.connect(path).close()
    observation = {'response': {'status': 'clarification'}, 'api_audits': []}
    scored = probe.score(observation, None, path)
    assert scored['semantic_pass'] and not scored['verified_api_pass']
    observation['api_audits'] = [audit()]
    assert probe.score(observation, None, path)['verified_api_pass']
    for key, value in [('reasoning', 'high'), ('model', 'gpt-6-sol'), ('model_verified', False)]:
        observation['api_audits'] = [{**audit(), key: value}]
        assert not probe.score(observation, None, path)['verified_api_pass']


def test_parent_sql_replay_is_readonly(tmp_path):
    cases = probe.prepare_domain(tmp_path / 'advertising', 'campaigns', 'spend')
    path = tmp_path / 'advertising/source.sqlite'
    before = probe.digest(path)
    observation = {'response': {'status': 'ok', 'result': {'sql': 'DELETE FROM countries', 'rows': [], 'columns': []}},
                   'api_audits': [audit()]}
    with pytest.raises(sqlite3.OperationalError, match='readonly'):
        probe.score(observation, cases[0][1], path)
    assert probe.digest(path) == before


def test_domain_keeps_five_denominator_on_auth_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, 'snapshot', lambda directory: {})
    monkeypatch.setattr(probe, 'tool_snapshot', lambda: {})
    calls = []
    def fake_child(question, work, session_id):
        calls.append(question)
        return {'pid': 9876, 'before_context': [], 'after_context': [], 'response': None,
                'api_audits': [{**audit(401), 'status': 'failed', 'model_verified': False}]}
    monkeypatch.setattr(probe, 'invoke_child', fake_child)
    stopped = Event()
    group = probe.run_domain(tmp_path, 'advertising', 'campaigns', 'spend', stopped, {}, {}, {})
    assert stopped.is_set() and len(calls) == 1 and len(group['cases']) == 5
    assert sum(case['executed'] for case in group['cases']) == 1
    assert not any(case['pass'] for case in group['cases'])
    assert all(case['error_code'] == 'not_executed_after_auth_failure' for case in group['cases'][1:])


def test_domain_exact_context_restore_and_pid_reuse_checked(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, 'snapshot', lambda directory: {})
    monkeypatch.setattr(probe, 'tool_snapshot', lambda: {})
    previous, count = [], 0
    def fake_child(question, work, session_id):
        nonlocal previous, count
        count += 1
        before = list(previous)
        after = [*previous, {'question': question}]
        previous = after
        if count == 3:
            before[0] = {'question': 'corrupted'}
        return {'pid': 9876 if count in (1, 2) else 9876 + count,
                'before_context': before, 'after_context': after, 'response': {'status': 'ok'}, 'api_audits': [audit()]}
    monkeypatch.setattr(probe, 'invoke_child', fake_child)
    monkeypatch.setattr(probe, 'score', lambda *args: {'verified_api_pass': True, 'semantic_pass': True})
    group = probe.run_domain(tmp_path, 'advertising', 'campaigns', 'spend', Event(), {}, {}, {})
    assert group['cases'][0]['pass']
    assert not group['cases'][1]['fresh_process_verified'] and not group['cases'][1]['pass']
    assert not group['cases'][2]['context_restored_exactly'] and not group['cases'][2]['pass']
    assert group['cases'][3]['pass'] and group['cases'][4]['pass']
    assert group['database_stable']
