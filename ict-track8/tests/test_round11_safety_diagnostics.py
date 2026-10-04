"""Failure diagnostics must explain a refusal without weakening or leaking it."""
import json
import sqlite3
from contextlib import closing

import pytest

from backend.nl2sql import engine as engine_module
from backend.nl2sql.engine import Nl2SqlEngine, _safe_failure_diagnostics, _safety_stage
from backend.nl2sql.models import QueryPlan
from backend.nl2sql.result_artifact import ResultBudgets, ResultRevisionError
from backend.nl2sql.security import SqlSafetyError


@pytest.fixture
def engine(tmp_path, monkeypatch):
    source = tmp_path / 'facts.sqlite'
    with closing(sqlite3.connect(source)) as db, db:
        db.execute('CREATE TABLE facts(amount REAL)')
        db.execute('INSERT INTO facts VALUES (2)')
    result = Nl2SqlEngine(source, result_artifact_dir=tmp_path / 'artifacts')
    monkeypatch.setattr(result, '_rules_plan', lambda *a, **kw: QueryPlan(
        table='facts', metric_table='facts', metric_column='amount',
        metric_function='SUM', metric_label='amount', rewritten_question='sum amount'))
    monkeypatch.setattr(result, '_intent_audit', lambda *a: {})
    monkeypatch.setattr(result.planner, 'build_sql', lambda plan: ('SELECT SUM(amount) FROM facts', ()))
    return result


@pytest.mark.parametrize('message,code', [
    ('查询结果超过安全行数上限', 'sql_row_budget_exceeded'),
    ('complete_result_operation_time_budget_exceeded', 'complete_result_operation_time_budget_exceeded'),
    ('complex_query_join_not_actual_fk_or_shared_key', 'complex_query_join_not_actual_fk_or_shared_key'),
    ('complex_query_function_not_allowed:SECRET_FUNCTION', 'complex_query_function_not_allowed'),
    ('secret SQL or bearer token', 'sql_safety_rejected'),
    ('complete_result_operation_time_budget_exceeded:SECRET', 'sql_safety_rejected'),
])
def test_diagnostic_allowlist_never_copies_arbitrary_error_text(message, code):
    diagnostics = _safe_failure_diagnostics(SqlSafetyError(message), 'execution')
    assert diagnostics == {'stage': 'execution', 'code': code}
    assert 'SECRET' not in json.dumps(diagnostics)
    assert 'bearer' not in json.dumps(diagnostics)


def test_context_keeps_same_exception_and_message():
    refusal = ResultRevisionError('complete_result_source_or_producer_changed')
    with pytest.raises(ResultRevisionError) as caught:
        with _safety_stage('complete_result_execution_and_delivery'):
            raise refusal
    assert caught.value is refusal
    assert str(caught.value) == 'complete_result_source_or_producer_changed'
    assert refusal.safety_code == str(refusal)
    assert refusal.safety_stage == 'complete_result_execution_and_delivery'


@pytest.mark.parametrize('sqlite_code,expected', [
    (sqlite3.SQLITE_INTERRUPT, 'sql_execution_interrupted_budget_or_cancel'),
    (sqlite3.SQLITE_AUTH, 'sql_execution_authorization_denied'),
    (sqlite3.SQLITE_TOOBIG, 'sql_execution_cell_too_large'),
    (sqlite3.SQLITE_ERROR, 'sql_execution_database_error'),
])
def test_sqlite_cause_classification_does_not_invent_specific_timeout(sqlite_code, expected):
    cause = sqlite3.OperationalError('secret column or SQL')
    cause.sqlite_errorcode = sqlite_code
    refusal = SqlSafetyError('只读 SQL 执行失败: secret column or SQL')
    refusal.__cause__ = cause
    assert _safe_failure_diagnostics(refusal, 'execution')['code'] == expected


def test_preview_execution_failure_has_stage_and_remains_refusal(engine, monkeypatch):
    refusal = SqlSafetyError('查询结果超过安全行数上限')
    def reject(*a, **kw):
        raise refusal
    monkeypatch.setattr(engine_module, 'execute_read_only', reject)
    with pytest.raises(SqlSafetyError) as caught:
        engine.answer('sum amount')
    assert caught.value is refusal
    assert refusal.safety_diagnostics == {'stage': 'execution', 'code': 'sql_row_budget_exceeded'}
    assert not engine._result_bindings


def test_complete_producer_failure_does_not_guess_execution_vs_publication(engine, monkeypatch):
    refusal = SqlSafetyError('unknown secret SQL')
    def reject(*a, **kw):
        raise refusal
    monkeypatch.setattr(engine_module, 'execute_complete_read_only', reject)
    with pytest.raises(SqlSafetyError) as caught:
        engine.answer('sum amount', complete_results=True)
    assert caught.value is refusal
    assert refusal.safety_diagnostics == {
        'stage': 'complete_result_execution_and_delivery', 'code': 'sql_safety_rejected'}
    assert not engine._result_bindings


def test_real_source_budget_refusal_is_diagnosable_before_execution(engine, monkeypatch):
    engine.complete_result_budgets = ResultBudgets(max_source_bytes=1024)
    called = []
    monkeypatch.setattr(engine_module, 'execute_complete_read_only', lambda *a, **kw: called.append(True))
    with pytest.raises(SqlSafetyError) as caught:
        engine.answer('sum amount', complete_results=True)
    assert caught.value.safety_diagnostics == {
        'stage': 'artifact_source_validation', 'code': 'complete_result_source_byte_budget_exceeded'}
    assert not called


def test_non_safety_errors_are_not_relabelled():
    original = RuntimeError('unrelated')
    with pytest.raises(RuntimeError) as caught:
        with _safety_stage('execution'):
            raise original
    assert caught.value is original
    assert not hasattr(original, 'safety_diagnostics')
