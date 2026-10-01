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


def test_targeted_selection_includes_all_prior_session_turns_in_suite_order(evaluator):
    rows = evaluator.select_cases(evaluator.suite(), ['sql-turn-5', 'qa-02', 'cross-turn-2'])
    assert [row['id'] for row in rows] == ['qa-02', 'cross-turn-1', 'cross-turn-2',
        'sql-turn-1', 'sql-turn-2', 'sql-turn-3', 'sql-turn-4', 'sql-turn-5']
    assert [row['context_turns'] for row in rows if row['id'].startswith('sql-turn')] == list(range(5))
    assert evaluator.select_cases(evaluator.suite(), ['qa-02', 'qa-02']) == [
        row for row in evaluator.suite() if row['id'] == 'qa-02']


def test_targeted_selection_rejects_unknown_id_before_api(evaluator, monkeypatch):
    with pytest.raises(ValueError, match='未知测试ID'):
        evaluator.select_cases(evaluator.suite(), ['not-a-case'])
    import model_runtime
    monkeypatch.setattr(model_runtime, 'enable_local_model', lambda model: pytest.fail('must not load credentials'))
    monkeypatch.setattr('sys.argv', ['evaluate_model.py', '--case-ids', 'not-a-case'])
    with pytest.raises(SystemExit) as exc:
        evaluator.main()
    assert exc.value.code == 2


def test_report_output_is_bounded_and_never_overwrites_history(evaluator, monkeypatch, tmp_path):
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    (tmp_path/'docs').mkdir()
    prior = tmp_path/'docs/first.json'
    prior.write_text('{"passed":11}')
    assert evaluator.report_output('docs/new.json') == tmp_path/'docs/new.json'
    with pytest.raises(ValueError, match='覆盖历史'):
        evaluator.report_output('docs/first.json')
    with pytest.raises(ValueError, match='docs'):
        evaluator.report_output('runtime/model_config.json')
    with pytest.raises(ValueError, match='JSON'):
        evaluator.report_output('docs/output.txt')


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


def test_wall_throughput_is_not_parallel_api_latency_sum(evaluator):
    records = [{'pass':True, 'latency_ms':1000, 'api_audits':[{'latency_ms':1000,'operation':'grounded_answer'}],
        'result':{'result':{'generation_attempts':[{'validation_status':'validated'}]}}},
        {'pass':False, 'latency_ms':2000, 'api_audits':[{'latency_ms':2000,'operation':'grounded_answer_correction'}],
        'result':{'result':{'generation_attempts':[{'validation_status':'rejected'},{'validation_status':'validated'}]}}}]
    metrics = evaluator.execution_metrics(records, 2000)
    assert metrics['wall_ms']==2000
    assert metrics['attempted_queries_per_second']==1 and metrics['passed_queries_per_second']==.5
    assert metrics['query_latency_p50_ms']==1500 and metrics['query_latency_p95_ms']==1950
    assert metrics['repair_calls']==1 and metrics['repair_operations']=={'grounded_answer_correction':1}
    assert metrics['grounded_first_attempt_rejections']==1
    assert metrics['grounded_correction_validated_cases']==1
    assert metrics['transport_retry_count'] is None


def test_throughput_handles_empty_and_failed_results(evaluator):
    metrics = evaluator.execution_metrics([], 0)
    assert metrics['attempted_queries_per_second'] is None
    assert metrics['query_latency_p95_ms'] is None and metrics['repair_calls']==0
    records = [{'pass':False,'latency_ms':2,'api_audits':[], 'result':None},
        {'pass':False,'latency_ms':3,'api_audits':[], 'result':{'result':None}}]
    assert evaluator.execution_metrics(records, 5)['grounded_first_attempt_rejections']==0


def test_parallel_groups_preserve_session_order_and_original_report_order(evaluator, monkeypatch):
    import threading
    barrier = threading.Barrier(2)
    seen = {}
    def execute(agent, clients, cases, database, **kwargs):
        barrier.wait(timeout=3)
        rows = []
        for row in cases:
            seen.setdefault(row['session_id'], []).append(row['id'])
            record = {**row, 'pass': True}
            rows.append(record)
            if kwargs['on_record']:
                kwargs['on_record'](record)
        return rows, None
    monkeypatch.setattr(evaluator, 'execute_cases', execute)
    cases = [{'id': 'a1', 'session_id': 'a'}, {'id': 'b1', 'session_id': 'b'},
             {'id': 'a2', 'session_id': 'a'}, {'id': 'b2', 'session_id': 'b'}]
    callbacks = []
    records, stopped = evaluator.execute_grouped(None, {}, cases, None, workers=2, on_record=callbacks.append)
    assert stopped is None and [row['id'] for row in records] == ['a1', 'b1', 'a2', 'b2']
    assert seen == {'a': ['a1', 'a2'], 'b': ['b1', 'b2']}
    assert len(callbacks) == 4


def test_auth_stop_event_prevents_sending_next_case(evaluator):
    import threading
    stop = threading.Event()
    stop.set()
    class ForbiddenAgent:
        def query(self, *args, **kwargs):
            raise AssertionError('must not submit a new API request after authentication failure')
    records, reason = evaluator.execute_cases(ForbiddenAgent(), {}, [{'id': 'not-sent'}], None, stop_event=stop)
    assert not records and reason == 'authentication_or_access_rejected'


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


def test_targeted_401_writes_only_requested_new_report_with_history(evaluator, monkeypatch, tmp_path):
    from backend.responses_client import GenerationError
    import backend.responses_client as responses
    import model_runtime
    class Denied:
        reasoning = 'medium'
        audit = {'http_status': 401}
        audit_history = [{'model': 'gpt-6-luna', 'status': 'failed', 'http_status': 401}]
        def generate(self, *args, **kwargs):
            raise GenerationError('denied', status=401)
    monkeypatch.setattr(responses, 'StructuredResponses', lambda *args, **kwargs: Denied())
    monkeypatch.setattr(model_runtime, 'enable_local_model', lambda model: None)
    monkeypatch.setenv('ICT8_OPENAI_BASE_URL', 'https://example.com/v1')
    monkeypatch.setenv('ICT8_OPENAI_API_KEY', 'private-test-only')
    monkeypatch.setenv('ICT8_OPENAI_REASONING', 'medium')
    monkeypatch.setattr(evaluator, 'ROOT', tmp_path)
    monkeypatch.setattr(evaluator, 'suite', lambda: [{'id':'turn1','kind':'sql','session_id':'s'},
        {'id':'turn2','kind':'sql','session_id':'s'}, {'id':'other','kind':'sql'}])
    monkeypatch.setattr('sys.argv', ['evaluate_model.py', '--case-ids', 'turn2', '--output', 'docs/targeted.json'])
    assert evaluator.main() == 1
    assert not (tmp_path/'docs/REAL_MODEL_REPORT.json').exists()
    report = json.loads((tmp_path/'docs/targeted.json').read_text())
    assert report['planned_cases'] == 2
    assert report['requested_case_ids'] == ['turn2']
    assert report['history_prerequisite_case_ids'] == ['turn1']
    assert report['not_run'] == ['turn1', 'turn2']
    assert report['scope'].endswith('not_full_suite')
