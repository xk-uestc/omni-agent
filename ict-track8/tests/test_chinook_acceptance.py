"""Real-model score gates on the recommended DB; no network calls here."""
import importlib.util
from pathlib import Path


def evaluator():
    path = Path(__file__).resolve().parents[2] / 'tools/evaluate_chinook.py'
    spec = importlib.util.spec_from_file_location('chinook_acceptance', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rule_answer_with_correct_execution_is_not_model_success():
    case = {'columns': ['Total']}
    result = {'status': 'ok', 'columns': ['Total'], 'rows': [{'Total': 12}],
              'plan': {'planner_source': 'rules'}}
    checks = evaluator().assess(case, result, [(12,)], [], model=True)
    assert checks['execution_result']
    assert not checks['actual_model_plan']
    assert not checks['completed_verified_api']
    assert not all(checks.values())


def test_verified_model_with_wrong_sql_result_still_fails():
    case = {'columns': ['Total']}
    result = {'status': 'ok', 'columns': ['Total'], 'rows': [{'Total': 13}],
              'plan': {'planner_source': 'model_validated'}}
    audits = [{'status': 'completed', 'model_verified': True, 'model': 'gpt-6-luna'}]
    checks = evaluator().assess(case, result, [(12,)], audits, model=True)
    assert checks['actual_model_plan'] and checks['completed_verified_api']
    assert not checks['execution_result']


def test_model_gate_includes_history_and_strict_projection_labels():
    result = {'status': 'ok', 'columns': ['other'], 'rows': [{'other': 12}],
              'plan': {'planner_source': 'model_validated'}}
    audits = [{'status': 'completed', 'model_verified': True, 'model': 'gpt-6-luna'}]
    checks = evaluator().assess({'columns': ['Total']}, result, [(12,)], audits, model=True, dropped=1)
    assert not checks['projection_labels'] and not checks['complete_call_history']


def test_duplicate_rows_are_not_collapsed_in_execution_comparison():
    result = {'status': 'ok', 'columns': ['Total'], 'rows': [{'Total': 12}]}
    assert not evaluator().assess({'columns': ['Total']}, result, [(12,), (12,)], [])['execution_result']
