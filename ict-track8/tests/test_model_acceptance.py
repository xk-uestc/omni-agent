"""Validate evaluation gates; fake audits are contracts, never model scores."""
import importlib.util
import json
from pathlib import Path

import pytest

from backend.nl2sql.seed import initialize_database


@pytest.fixture
def evaluator(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'tools'))
    spec = importlib.util.spec_from_file_location('test_model_evaluation_tools', root/'tools/evaluate_model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_suite_has_both_initial_tasks_five_fusion_types_and_five_turns(evaluator):
    cases = evaluator.suite()
    assert len({row['id'] for row in cases}) == len(cases) == 44
    assert len([r for r in cases if r['kind'] == 'sql']) >= 10
    assert len([r for r in cases if r['kind'] == 'document' and r.get('expected_status', 'ok') == 'ok']) >= 10
    assert {'formula', 'forecast', 'document_to_sql', 'sql_to_document', 'threshold'} <= {r['kind'] for r in cases}
    cross = [r for r in cases if r['id'].startswith('cross-turn-')]
    assert len(cross) == 5 and len({r['session_id'] for r in cross}) == 1
    assert [r['context_turns'] for r in cross] == list(range(5))
    assert next(r for r in cases if r['id'] == 'cross-explicit-reset')['reset_context'] is True


def test_rule_sql_correct_value_is_still_not_real_model_pass(evaluator, tmp_path):
    database = initialize_database(tmp_path/'db.sqlite')
    row = evaluator.case('sql', '2025年华东销售额', 'sql', region='华东', year=2025, metric='销售额')
    from backend.nl2sql.engine import Nl2SqlEngine
    data = Nl2SqlEngine(database).answer(row['question']).to_dict()
    result = {'planner_source': 'model_validated', 'route': 'sql', 'status': 'ok', 'result': data}
    checks = evaluator.evaluate_result(row, result, database)
    assert checks['value'] is True
    assert checks['real_sql_planner'] is False
    result['planner_source'] = 'rules_fallback'
    assert evaluator.evaluate_result(row, result, database)['model_router'] is False


def test_correct_threshold_without_actual_tool_chain_is_rejected(evaluator):
    result = {'planner_source': 'model_validated', 'route': 'fusion', 'status': 'ok', 'result': {'results': {
        'final': {'left': {'value': 2}, 'right': {'value': 2}, 'operator': 'le', 'matched': True}}, 'trace': [], 'edges': []}}
    checks = evaluator.evaluate_result({'kind': 'threshold'}, result, None)
    assert checks['actual_comparison'] and checks['two_values']
    assert not checks['actual_dependencies']


def test_usage_totals_preserve_unknown_failed_calls_and_dropped_history(evaluator):
    audits = [{'status': 'completed', 'input_tokens': 11, 'output_tokens': 5, 'total_tokens': 16, 'latency_ms': 10},
              {'status': 'failed', 'http_status': 503, 'latency_ms': 20},
              {'status': 'completed', 'input_tokens': 13, 'output_tokens': 7, 'total_tokens': 20, 'latency_ms': 30}]
    result = evaluator.summarise_audits(audits)
    assert result['calls'] == 3 and result['failed_calls'] == 1
    assert result['tokens']['total_tokens'] == {'reported_sum': 36, 'unknown_calls': 1, 'complete': False}
    assert result['tokens']['cached_input_tokens']['unknown_calls'] == 3
    assert result['api_latency_ms'] == 60 and result['usd_cost'] is None
    assert evaluator.summarise_audits(audits, 2)['calls'] == 5


def test_audits_include_omni_generator_and_sql_component(evaluator):
    class AuditClient:
        audit_history = [{'operation': 'omni_plan'}, {'operation': 'grounded_answer'}]
        audit_dropped_count = 0
    class SqlClient:
        audit_history = [{'operation': 'nl2sql_plan'}]
        audit_dropped_count = 0
    audits, dropped = evaluator.collect_audits({'omni_and_generation': AuditClient(), 'nl2sql': SqlClient()})
    assert [a['operation'] for a in audits] == ['omni_plan', 'grounded_answer', 'nl2sql_plan']
    assert audits[-1]['component'] == 'nl2sql' and dropped == 0


def test_401_preflight_stops_all_cases_and_report_contains_no_secret(evaluator, monkeypatch, tmp_path):
    from backend.responses_client import GenerationError
    import backend.responses_client as responses
    import model_runtime
    class Denied:
        reasoning = 'medium'
        audit = {'http_status': 401}
        audit_history = [{'model': 'gpt-6-luna', 'status': 'failed', 'http_status': 401, 'model_verified': False}]
        def generate(self, *args, **kwargs):
            raise GenerationError('denied', status=401)
    monkeypatch.setattr(responses, 'StructuredResponses', lambda *args, **kwargs: Denied())
    monkeypatch.setattr(model_runtime, 'enable_local_model', lambda model: None)
    monkeypatch.setenv('ICT8_OPENAI_BASE_URL', 'https://example.com/v1')
    monkeypatch.setenv('ICT8_OPENAI_API_KEY', 'private-test-only')
    monkeypatch.setenv('ICT8_OPENAI_REASONING', 'medium')
    (tmp_path/'docs').mkdir()
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setattr(evaluator, 'suite', lambda: [{'id': 'not-executed', 'kind': 'document'}])
    monkeypatch.setattr('sys.argv', ['evaluate_model.py', '--full'])
    assert evaluator.main() == 1
    text = (tmp_path/'docs/REAL_MODEL_REPORT.json').read_text()
    report = json.loads(text)
    assert report['status'] == 'authentication_failed'
    assert report['total'] == report['passed'] == 0 and report['not_run'] == ['not-executed']
    assert 'private-test-only' not in text
