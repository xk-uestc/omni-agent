"""Recorded development failure, typed navigation, and bounded offline repair."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import requests

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.fusion_normalization import normalize_fusion_tasks
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent, _normalize_model_tasks
from backend.responses_client import GenerationError
from backend.session import ConversationStore
from backend.text_reference_contract import text_reference_errors


RECORDED = json.loads((Path(__file__).parent / 'fixtures' /
                       'round5_cross_turn1_invalid_plan.json').read_text(encoding='utf-8'))
QUESTION = RECORDED['question']


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline reference-contract tests must not call an API')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


def task(identifier, tool, **args):
    return {'id': identifier, 'tool': tool, 'args': args}


def ref(identifier, *path):
    return {'ref': identifier, 'path': list(path)}


def plan(tasks):
    return {'route': 'fusion', 'effective_question': QUESTION, 'clarification': '',
            'tasks_json': json.dumps(tasks, ensure_ascii=False)}


def repaired_tasks():
    tasks = deepcopy(RECORDED['execution_plan'][1:])
    tasks[1]['args']['question'] = '2025年华东地区销售额和订单数'
    return tasks


class ReplayPlanner:
    def __init__(self, responses, *, http_status=200, status='completed'):
        self.responses, self.calls = responses, []
        self.audit = {'status': status, 'http_status': http_status}

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append({'operation': kwargs['name'], 'context': deepcopy(context)})
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            self.audit = {'status': 'failed', 'http_status': None}
            raise response
        return deepcopy(response)


def agent(tmp_path, planner):
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest('客单价 = 销售额 / 订单数'.encode(), document_id='metric-definitions',
                     title='指标公式', modality='txt', filename='formula.txt')
    knowledge.ingest('销售额统计口径以数据库中的明细为准。'.encode(), document_id='revenue-policy',
                     title='销售额统计口径', modality='txt', filename='policy.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite')), knowledge,
                     ConversationStore(storage_path=tmp_path / 'sessions.sqlite'), planner)


def test_exact_recorded_invalid_plan_rejects_statically_without_rewriting_or_resolving(monkeypatch):
    tasks = deepcopy(RECORDED['execution_plan'])
    original = deepcopy(tasks)
    monkeypatch.setattr(DependencyAgent, 'resolve', lambda *a: pytest.fail('Static check resolved data'))
    with pytest.raises(GenerationError) as caught:
        _normalize_model_tasks(plan(tasks), tasks, None, None, QUESTION)
    assert tasks == original
    assert caught.value.plan_rejection_code == 'text_reference_type_invalid'
    detail = caught.value.plan_rejection_details[0]
    assert detail['argument_path'] == ['question', 0]
    assert detail['source_task_id'] == 'policy_scope' and detail['resolved_type'] == 'object'
    assert {'path': ['hits', 0, 'snippet'], 'type': 'string',
            'use': 'text_navigation_only_not_verified_fact',
            'availability': 'must_be_rechecked_at_execution'} in detail['available_paths']


@pytest.mark.parametrize('path', [
    ['hits', 0, 'snippet'], ['hits', 0, 'title'],
    ['hits', 0, 'metadata', 'document_id'], ['hits', 0, 'metadata', 'source_locator'],
])
def test_deep_string_projections_keep_original_paths_and_dependency_semantics(path):
    tasks = [task('source', 'search', query='地区说明'),
             task('query', 'sql', question=['2025年', ref('source', *path), '销售额'])]
    original = deepcopy(tasks)
    normalized, notes = normalize_fusion_tasks(tasks)
    assert text_reference_errors(normalized) == []
    assert normalized == original and notes == []
    assert DependencyAgent.references(normalized[1]['args']) == {'source'}
    source = {'hits': [{'snippet': '华东', 'title': '华东',
                       'metadata': {'document_id': '华东', 'source_locator': '华东'}}]}
    resolved = DependencyAgent.resolve(normalized[1]['args'], {'source': source})
    assert DependencyAgent.text(resolved['question']) == '2025年 华东 销售额'
    assert tasks == original


@pytest.mark.parametrize('tool,path', [
    ('search', []), ('search', ['hits']), ('search', ['hits', 0]),
    ('search', ['hits', 0, 'metadata']), ('document_formula', []),
    ('sql', ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0]),
    ('compare', ['matched']),
])
def test_known_objects_arrays_and_booleans_cannot_enter_text_parts(tool, path):
    tasks = [task('source', tool),
             task('query', 'sql', question=[ref('source', *path), '查询'])]
    error = text_reference_errors(tasks)[0]
    assert error['code'] == 'text_reference_non_scalar'
    assert error['argument_path'] == ['question', 0]


@pytest.mark.parametrize('path', [
    ['hits', 0, 'metadata', 'unknown_field'], ['missing'],
    ['hits', 0, 'snippet', 'pretend_field'], ['search_scope', 'unknown'],
])
def test_unknown_projection_type_fails_closed_instead_of_guessing(path):
    tasks = [task('source', 'search', query='检索'),
             task('query', 'search', query=ref('source', *path))]
    error = text_reference_errors(tasks)[0]
    assert error['code'] == 'text_reference_unknown_type' and error['resolved_type'] == 'unknown'


@pytest.mark.parametrize('value', [[['nested']], ['plain', ['nested']],
                                  [False, 'plain'], [None], [{'arbitrary': 'object'}]])
def test_nested_literal_arrays_and_non_scalar_parts_are_rejected(value):
    errors = text_reference_errors([task('query', 'sql', question=value)])
    assert errors and errors[0]['code'] == 'text_argument_non_scalar'


def test_resolved_scalar_array_allowed_only_as_whole_text_argument():
    source = task('formula', 'document_formula', document_id='formulas', label='ratio')
    whole = [source, task('query', 'search', query=ref('formula', 'parameters'))]
    nested = [source, task('query', 'search', query=[ref('formula', 'parameters'), 'text'])]
    assert text_reference_errors(whole) == []
    assert text_reference_errors(nested)[0]['resolved_type'] == 'array<string>'
    values = {'formula': {'parameters': ['销售额', '订单数']}}
    assert DependencyAgent.text(DependencyAgent.resolve(whole[1]['args']['query'], values)) == '销售额 订单数'
    with pytest.raises(DependencyPlanError):
        DependencyAgent.text(DependencyAgent.resolve(nested[1]['args']['query'], values))


@pytest.mark.parametrize('path', [
    ['dimension_values', 'sales_orders', 'region', 0], ['rows', 0, '地区'],
    ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0, 'value'],
])
def test_physical_sql_navigation_and_scalar_evidence_fields_remain_valid(path):
    tasks = [task('source', 'sql', question='2025年地区销售额'),
             task('query', 'search', query=[ref('source', *path), '团队方法'])]
    assert text_reference_errors(tasks) == []
    assert DependencyAgent.references(tasks[1]['args']) == {'source'}


def test_cell_projection_still_normalizes_only_the_text_slot():
    tasks = [task('cell', 'document_cell', document_id='targets', where={'年份': 2025}, column='地区'),
             task('query', 'sql', question=['2025年', ref('cell'), '地区销售额'])]
    normalized, notes = normalize_fusion_tasks(tasks)
    assert normalized[1]['args']['question'][1] == ref('cell', 'value')
    assert text_reference_errors(normalized) == [] and notes
    assert tasks[1]['args']['question'][1] == ref('cell')


def test_recorded_failure_gets_one_fresh_repair_before_any_tool_executes(tmp_path, monkeypatch):
    planner = ReplayPlanner([plan(RECORDED['execution_plan']), plan(repaired_tasks())])
    executed = []
    original = DependencyAgent.execute
    def track(self, tool, args, *rest, **kwargs):
        assert len(planner.calls) == 2
        executed.append(tool)
        return original(self, tool, args, *rest, **kwargs)
    monkeypatch.setattr(DependencyAgent, 'execute', track)
    result = agent(tmp_path, planner).query(QUESTION)
    assert result['status'] == 'ok' and result['planner_source'] == 'model_validated'
    assert result['result']['results']['customer_price']['value'] == pytest.approx(29584 / 3)
    assert [call['operation'] for call in planner.calls] == ['omni_plan', 'omni_plan_completion_repair']
    feedback = planner.calls[1]['context']['plan_completion_feedback']
    assert feedback['errors'] == ['text_reference_type_invalid']
    assert feedback['reference_type_errors'][0]['source_task_id'] == 'policy_scope'
    assert feedback['reference_type_errors'][0]['available_paths']
    assert '不是已核验事实' in feedback['instruction']
    assert [attempt['validation'] for attempt in result['trace'][0]['attempts']] == ['plan_protocol_rejected', 'complete']
    assert 'search' not in executed and set(executed) == {'document_formula', 'sql', 'calculate'}
    assert result['result']['user_constraint_validation']['status'] == 'verified'


def test_repeated_invalid_plan_stops_after_existing_repair_budget(tmp_path, monkeypatch):
    planner = ReplayPlanner([plan(RECORDED['execution_plan'])])
    monkeypatch.setattr(DependencyAgent, 'execute', lambda *a, **k: pytest.fail('Rejected graph executed'))
    result = agent(tmp_path, planner).query(QUESTION)
    assert len(planner.calls) == 2 and result['planner_source'] == 'rules_fallback'
    assert result['status'] == 'clarification'
    assert all(attempt['errors'] == ['text_reference_type_invalid'] for attempt in result['trace'][0]['attempts'])


@pytest.mark.parametrize('http_status,status', [(401, 'failed'), (503, 'failed'), (None, 'failed')])
def test_unsuccessful_provider_does_not_authorize_type_repair(tmp_path, http_status, status):
    planner = ReplayPlanner([plan(RECORDED['execution_plan']), plan(repaired_tasks())],
                            http_status=http_status, status=status)
    result = agent(tmp_path, planner).query(QUESTION)
    assert len(planner.calls) == 1 and result['planner_source'] == 'rules_fallback'


def test_provider_exception_is_not_retried(tmp_path):
    planner = ReplayPlanner([GenerationError('unavailable'), plan(repaired_tasks())])
    result = agent(tmp_path, planner).query(QUESTION)
    assert len(planner.calls) == 1 and result['status'] == 'clarification'
    assert result['trace'][0]['attempts'][0]['validation'] == 'provider_failed'
