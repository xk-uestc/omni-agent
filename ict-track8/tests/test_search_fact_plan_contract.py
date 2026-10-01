"""Text retrieval cannot masquerade as a physical numeric fact; no API calls."""
import json

import pytest

from backend.omni_agent import OmniAgent, _search_fact_plan_errors
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


QUESTION = '先从数据库查2025年销售额排名第一的地区，再根据该地区检索冠军团队的方法，保留来源。'


def graph(unit=None):
    tasks = [
        {'id': 'values', 'tool': 'sql', 'args': {'question': '查询2025年销售额排名第一的地区及销售额'}},
        {'id': 'method_search', 'tool': 'search', 'args': {
            'query': [{'ref': 'values', 'path': ['dimension_values', 'sales_orders', 'region', 0]}, '冠军团队的方法'],
            'document_id': 'champion-method'}}]
    if unit is not None:
        tasks.append({'id': 'method_fact', 'tool': 'search_fact', 'args': {
            'evidence': {'ref': 'method_search', 'path': []},
            'scope': '排名第一的销售地区冠军团队', 'label': '冠军团队的方法', 'unit': unit}})
    return tasks


class Planner:
    def __init__(self, graphs, status=200):
        self.graphs, self.calls = graphs, []
        self.audit = {'status': 'completed', 'http_status': status}

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append({'context': context, 'operation': kwargs['name']})
        tasks = self.graphs[min(len(self.calls)-1, len(self.graphs)-1)]
        return {'route': 'fusion', 'effective_question': QUESTION, 'clarification': '',
                'tasks_json': json.dumps(tasks, ensure_ascii=False)}


def agent(tmp_path, planner):
    knowledge = KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('华东团队采用重点客户分层、每周回访和订单交付跟踪。'.encode(),
        document_id='champion-method', title='销售冠军经验分享', modality='txt', filename='methods.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),
                     knowledge, ConversationStore(), planner)


@pytest.mark.parametrize('unit', ['原文', 'text', '文本', 'unknown', 'ratio', '小时 ', '', None, []])
def test_invalid_numeric_unit_is_planning_error(unit):
    tasks = graph('原文')
    tasks[-1]['args']['unit'] = unit
    assert _search_fact_plan_errors(tasks) == ['search_fact_requires_supported_explicit_numeric_unit']


@pytest.mark.parametrize('unit', ['小时', '分钟', '天', '日', '个月', '元', 'CNY', '%'])
def test_supported_numeric_units_keep_existing_execution_guards(unit):
    assert _search_fact_plan_errors(graph(unit)) == []


def test_text_fact_mistake_requests_one_repair_then_executes_ranked_search(tmp_path):
    planner = Planner([graph('原文'), graph()])
    result = agent(tmp_path, planner).query(QUESTION)
    assert len(planner.calls) == 2
    assert planner.calls[1]['operation'] == 'omni_plan_completion_repair'
    assert planner.calls[1]['context']['plan_completion_feedback']['errors'] == [
        'search_fact_requires_supported_explicit_numeric_unit']
    assert result['status'] == 'ok' and result['planner_source'] == 'model_validated'
    execution = result['result']
    assert set(execution['results']) == {'values', 'method_search'}
    search = execution['results']['method_search']
    assert search['dependency_reference_validation']['status'] == 'verified'
    assert search['dependency_reference_validation']['value'] == '华东'
    assert '重点客户分层' in search['hits'][0]['snippet']
    assert search['hits'][0]['metadata']['document_id'] == 'champion-method'
    # A model actually replanned; the server did not silently drop a task.
    assert [a['validation'] for a in result['trace'][0]['attempts']] == [
        'requested_operation_missing', 'complete']


def test_repeated_invalid_plan_is_not_silently_salvaged(tmp_path):
    planner = Planner([graph('原文')])
    result = agent(tmp_path, planner).query(QUESTION)
    assert len(planner.calls) == 2
    assert result['planner_source'] == 'rules_fallback'
    assert result['status'] != 'ok'


@pytest.mark.parametrize('status', [401, 403, 429, 503])
def test_failed_provider_audit_never_authorizes_repair(tmp_path, status):
    planner = Planner([graph('原文'), graph()], status=status)
    result = agent(tmp_path, planner).query(QUESTION)
    assert len(planner.calls) == 1
    assert result['status'] != 'ok'


def test_correct_text_search_plan_needs_no_repair(tmp_path):
    planner = Planner([graph()])
    result = agent(tmp_path, planner).query(QUESTION)
    assert result['status'] == 'ok' and len(planner.calls) == 1
