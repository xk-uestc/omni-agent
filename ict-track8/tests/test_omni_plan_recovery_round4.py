"""Recorded rejection classes and pending-user recovery, with no model calls.

The release report retains rejection codes, not raw rejected model responses.
Malformed plans here reproduce those protocol classes; the successful three-
step aggregate-evidence plan follows the recorded cross-turn-2 structure.
These are local regression fixtures, not real-model accuracy measurements.
"""
from copy import deepcopy
import json

import pytest

from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.responses_client import GenerationError
from backend.session import ConversationStore


FIRST = '按指标公式文档计算2025年华东地区客单价，销售额和订单数从数据库取。'
FOLLOWUP = '那华南呢，仍按同一公式？'


def plan(route, question, tasks=None):
    return {'route': route, 'effective_question': question, 'clarification': '',
            'tasks_json': json.dumps(tasks or [], ensure_ascii=False)}


def formula_tasks(question, *, label='客单价'):
    region = '华南' if '华南' in question else '华东'
    return [
        {'id': 'formula', 'tool': 'document_formula',
         'args': {'document_id': 'formula-library', 'label': label}},
        {'id': 'sales_data', 'tool': 'sql',
         'args': {'question': f'2025年{region}地区销售额和订单数'}},
        {'id': 'result', 'tool': 'calculate', 'args': {
            'formula': {'ref': 'formula', 'path': []}, 'parameters': {
                '销售额': {'ref': 'sales_data', 'path': [
                    'aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0]},
                '订单数': {'ref': 'sales_data', 'path': [
                    'aggregate_cells', 'sales_orders', 'order_id', 'COUNT', 0]}}}}]


def invalid_fusion(question=FIRST):
    tasks = formula_tasks(question)
    tasks[-1]['args']['formula']['ref'] = 'missing_formula'
    return plan('fusion', question, tasks)


class ReplayPlanner:
    def __init__(self, responses, *, http_status=200):
        self.responses, self.calls = responses, []
        self.audit = {'status': 'completed', 'http_status': http_status}

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append({'context': deepcopy(context), 'operation': kwargs['name']})
        response = self.responses[min(len(self.calls)-1, len(self.responses)-1)]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response(context) if callable(response) else response)


def make_agent(tmp_path, planner):
    knowledge = KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('客单价 = 销售额 / 订单数\n效率评分 = 销售额 / 订单数'.encode(),
                     document_id='formula-library', title='指标公式', modality='txt', filename='formula.txt')
    knowledge.ingest('紧急工单应在2小时内首次响应。'.encode(),
                     document_id='service-notice', title='响应通知', modality='txt', filename='notice.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'data.sqlite')), knowledge,
                     ConversationStore(storage_path=tmp_path/'sessions.sqlite'), planner)


def test_single_source_dag_rejection_gets_one_protocol_repair_without_discarding_tasks(tmp_path, monkeypatch):
    question = '紧急工单首次响应几小时'
    wrong = plan('document', question, [
        {'id': 'search', 'tool': 'search', 'args': {'query': question}},
        {'id': 'fact', 'tool': 'search_fact', 'args': {
            'evidence': {'ref': 'search', 'path': []}, 'scope': '紧急工单', 'label': '首次响应', 'unit': '小时'}}])
    planner = ReplayPlanner([wrong, plan('document', question)])
    agent = make_agent(tmp_path, planner)
    monkeypatch.setattr(DependencyAgent, 'execute', lambda *a, **k: pytest.fail('Rejected read DAG executed'))
    result = agent.query(question)
    assert result['status'] == 'ok' and '2小时' in result['result']['answer']
    assert result['planner_source'] == 'model_validated' and len(planner.calls) == 2
    assert planner.calls[1]['context']['plan_completion_feedback']['errors'] == ['single_source_task_not_discardable']
    assert [attempt['validation'] for attempt in result['trace'][0]['attempts']] == ['plan_protocol_rejected', 'complete']
    assert result['trace'][0]['normalizations'] == []


def test_fusion_normalization_rejection_is_replanned_before_any_dependency_executes(tmp_path, monkeypatch):
    planner = ReplayPlanner([invalid_fusion(), lambda c: plan('fusion', c['question'], formula_tasks(c['question']))])
    executed = []
    original = DependencyAgent.execute
    def track(self, tool, args, *rest, **kwargs):
        assert len(planner.calls) == 2
        executed.append(tool)
        return original(self, tool, args, *rest, **kwargs)
    monkeypatch.setattr(DependencyAgent, 'execute', track)
    result = make_agent(tmp_path, planner).query(FIRST)
    assert result['status'] == 'ok' and result['planner_source'] == 'model_validated'
    assert result['result']['results']['result']['value'] == pytest.approx(29584/3)
    assert planner.calls[1]['context']['plan_completion_feedback']['errors'] == ['fusion_task_normalization_rejected']
    assert executed == ['document_formula', 'document_formula', 'sql', 'calculate']
    assert result['result']['user_constraint_validation']['status'] == 'verified'


@pytest.mark.parametrize('label', ['客单价', '效率评分'])
def test_failed_planning_recovers_only_literal_user_scope_and_rebuilds_evidence(tmp_path, label):
    first = FIRST.replace('客单价', label)
    planner = ReplayPlanner([invalid_fusion(first), invalid_fusion(first),
        lambda c: plan('fusion', 'model guessed 2024年华北', formula_tasks(c['question'], label=label))])
    agent = make_agent(tmp_path, planner)
    failed = agent.query(first, session_id='recover')
    assert failed['status'] == 'clarification'
    assert failed['state']['pending_fusion_scope'] == {'status': 'user_text_only', 'scope_question': first}
    assert 'fusion_context' not in failed['state'] and 'execution_plan' not in failed['state']
    result = agent.query(FOLLOWUP, session_id='recover')
    assert result['status'] == 'ok' and result['context_turns'] == 1
    assert result['effective_question'] == first.replace('华东', '华南')
    assert result['context_resolution']['mode'] == 'server_resolved_pending_fusion_scope'
    assert result['context_resolution']['document_versions'] == {}
    assert result['result']['user_constraint_validation']['status'] == 'verified'
    assert result['result']['results']['result']['value'] == pytest.approx(22992/3)
    assert result['state']['fusion_context']['documents']
    assert 'pending_fusion_scope' not in result['state']
    context = planner.calls[-1]['context']
    assert context['actual_question'] == FOLLOWUP and '2025年华南' in context['question']


def test_recovered_pending_context_survives_persistent_store_restart(tmp_path):
    planner = ReplayPlanner([invalid_fusion()])
    agent = make_agent(tmp_path, planner)
    agent.query(FIRST, session_id='restart')
    agent = OmniAgent(agent.engine, agent.knowledge,
                      ConversationStore(storage_path=agent.conversations.storage_path),
                      ReplayPlanner([lambda c: plan('fusion', c['question'], formula_tasks(c['question']))]))
    result = agent.query(FOLLOWUP, session_id='restart')
    assert result['status'] == 'ok'
    assert result['result']['results']['result']['value'] == pytest.approx(22992/3)


@pytest.mark.parametrize('question', [
    '那华南呢，不含线上', '那华南和华北呢', '那华南呢，按另一公式',
    '那华南2024年呢', '那华南呢，订单数要去重', '那华南呢，新增未知条件',
])
def test_pending_scope_unknown_changes_stop_before_model_and_execution(tmp_path, question):
    planner = ReplayPlanner([invalid_fusion()])
    agent = make_agent(tmp_path, planner)
    agent.query(FIRST, session_id='unsafe')
    calls = len(planner.calls)
    result = agent.query(question, session_id='unsafe')
    assert result['status'] == 'clarification' and len(planner.calls) == calls
    assert 'pending_fusion_scope' not in result['state']
    assert not result['result'].get('results')


def test_unknown_original_constraint_is_preserved_and_cannot_authorize_sql(tmp_path, monkeypatch):
    first = FIRST + '未批准记录不要算。'
    planner = ReplayPlanner([invalid_fusion(first), invalid_fusion(first),
                            lambda c: plan('fusion', c['question'], formula_tasks(c['question']))])
    agent = make_agent(tmp_path, planner)
    agent.query(first, session_id='unknown-original')
    monkeypatch.setattr(agent.engine, 'answer', lambda *a, **k: pytest.fail('Unknown original constraint executed SQL'))
    result = agent.query(FOLLOWUP, session_id='unknown-original')
    assert '未批准记录不要算' in result['effective_question']
    assert result['status'] == 'clarification' and result['result']['results'] == {}
    assert result['result']['user_constraint_validation']['status'] == 'unverified'


def test_pending_scope_has_no_source_or_formula_proof_to_reuse(tmp_path):
    wrong_formula = formula_tasks(FIRST)
    wrong_formula[0]['args']['label'] = '效率评分'
    planner = ReplayPlanner([invalid_fusion(), invalid_fusion(), plan('fusion', FOLLOWUP, wrong_formula)])
    agent = make_agent(tmp_path, planner)
    agent.query(FIRST, session_id='formula-change')
    result = agent.query(FOLLOWUP, session_id='formula-change')
    assert result['status'] == 'clarification' and result['result']['results'] == {}
    assert result['result']['user_constraint_validation']['status'] == 'unverified'


@pytest.mark.parametrize('change', [{'status': 'verified'}, {'scope_question': '2025年销售额'}, {'documents': {}}])
def test_tampered_pending_record_is_not_a_verified_history_carrier(tmp_path, change):
    agent = make_agent(tmp_path, ReplayPlanner([invalid_fusion()]))
    agent.query(FIRST, session_id='tamper')
    turn = agent.conversations.context('tamper')[-1]
    state = deepcopy(turn.state)
    state['pending_fusion_scope'].update(change)
    agent.conversations.remember('tamper', question=turn.question, effective_question=turn.effective_question, state=state)
    calls = len(agent.client.calls)
    result = agent.query(FOLLOWUP, session_id='tamper')
    assert result['status'] == 'clarification' and len(agent.client.calls) == calls


def test_complete_new_topic_and_context_reset_do_not_recover_pending_workflow(tmp_path):
    agent = make_agent(tmp_path, ReplayPlanner([invalid_fusion()]))
    agent.query(FIRST, session_id='new-topic')
    agent.client = ReplayPlanner([lambda c: plan('sql', c['question'])])
    fresh = agent.query('那2024年华北地区销售额呢', session_id='new-topic')
    assert fresh['status'] == 'ok' and fresh['route'] == 'sql'
    assert agent.client.calls[0]['context']['history'] == []
    agent.query(FIRST, session_id='reset')
    reset = agent.query(FOLLOWUP, session_id='reset', reset_context=True)
    assert reset['context_turns'] == 0 and reset['context_resolution']['mode'] == 'independent'


@pytest.mark.parametrize('status', [401, 403, 429, 503])
def test_unsuccessful_provider_audit_never_authorizes_protocol_repair(tmp_path, status):
    planner = ReplayPlanner([invalid_fusion(), plan('fusion', FIRST, formula_tasks(FIRST))], http_status=status)
    result = make_agent(tmp_path, planner).query(FIRST)
    assert len(planner.calls) == 1 and result['status'] == 'clarification'
    assert result['planner_source'] == 'rules_fallback'


@pytest.mark.parametrize('bad', [None, [], {'route': 'sql'},
    {**plan('fusion', FIRST), 'tasks_json': '{'},
    {**plan('fusion', FIRST), 'tasks_json': '{}'},
    {**plan('fusion', FIRST), 'tasks_json': '['*1100 + ']'*1100},
    {**plan('fusion', FIRST), 'tasks_json': json.dumps([{'id': 'bad', 'tool': 'sql', 'args': {'question': float('nan')}}])},
])
def test_envelope_json_and_payload_errors_share_bounded_repair(tmp_path, bad):
    planner = ReplayPlanner([bad, plan('fusion', FIRST, formula_tasks(FIRST))])
    result = make_agent(tmp_path, planner).query(FIRST)
    assert result['status'] == 'ok' and len(planner.calls) == 2
    assert result['trace'][0]['attempts'][0]['validation'] == 'plan_protocol_rejected'


def test_provider_failure_on_repair_stops_and_preserves_user_scope(tmp_path):
    planner = ReplayPlanner([invalid_fusion(), GenerationError('service unavailable')])
    result = make_agent(tmp_path, planner).query(FIRST)
    assert result['status'] == 'clarification' and len(planner.calls) == 2
    assert result['trace'][0]['attempts'][-1]['validation'] == 'provider_failed'
    assert result['state']['pending_fusion_scope']['scope_question'] == FIRST


@pytest.mark.parametrize('route', ['sql', 'document'])
def test_explicit_workflow_cannot_become_empty_single_source_plan(tmp_path, route):
    planner = ReplayPlanner([plan(route, FIRST)])
    result = make_agent(tmp_path, planner).query(FIRST)
    assert result['status'] == 'clarification' and result['planner_source'] == 'rules_fallback'
    assert result['trace'][0]['rejection_code'] == 'single_source_cross_source_request'


def test_pending_recovery_cannot_drop_the_workflow_route(tmp_path):
    planner = ReplayPlanner([invalid_fusion(), invalid_fusion(), lambda c: plan('sql', c['question'])])
    agent = make_agent(tmp_path, planner)
    agent.query(FIRST, session_id='route')
    result = agent.query(FOLLOWUP, session_id='route')
    assert result['status'] == 'clarification' and result['route'] == 'clarify'
    assert result['trace'][0]['rejection_code'] == 'requested_operations_incomplete'
    assert '2025年华南' in result['state']['pending_fusion_scope']['scope_question']


@pytest.mark.parametrize('condition', [
    '未批准记录不要算', '核验后的记录才参与', '订单数要去重',
    '目标记录只取最新', '金额必须大于1', 'unknownCustomConstraint', 'approved records only',
])
def test_unknown_unassigned_conditions_never_execute_even_on_first_full_request(tmp_path, monkeypatch, condition):
    question = FIRST + condition + '。'
    agent = make_agent(tmp_path, ReplayPlanner([plan('fusion', question, formula_tasks(question))]))
    monkeypatch.setattr(agent.engine, 'answer', lambda *a, **k: pytest.fail('Unassigned condition executed SQL'))
    result = agent.query(question)
    assert result['status'] == 'clarification' and result['result']['results'] == {}
    assert result['result']['user_constraint_validation']['status'] == 'unverified'


@pytest.mark.parametrize('output_request', ['保留来源', '请给出结果', '展示答案', '多少', '是多少'])
def test_complete_output_or_question_clauses_keep_legitimate_scope(tmp_path, output_request):
    question = FIRST + output_request + '。'
    result = make_agent(tmp_path, ReplayPlanner([plan('fusion', question, formula_tasks(question))])).query(question)
    assert result['status'] == 'ok'
    assert result['result']['results']['result']['value'] == pytest.approx(29584/3)


def test_failed_execution_recovery_reloads_current_sources_and_revalidates_sql(tmp_path):
    wrong_tasks = formula_tasks(FIRST)
    wrong_tasks[1]['args']['question'] = '2024年华东地区销售额和订单数'
    planner = ReplayPlanner([plan('fusion', FIRST, wrong_tasks),
                            lambda c: plan('fusion', c['question'], formula_tasks(c['question']))])
    agent = make_agent(tmp_path, planner)
    failed = agent.query(FIRST, session_id='failed-execution')
    assert failed['status'] == 'incomplete'
    assert failed['state']['pending_fusion_scope']['status'] == 'user_text_only'
    assert 'fusion_context' not in failed['state']
    agent.knowledge.ingest('客单价 = 销售额 / 订单数 * 2'.encode(),
                          document_id='formula-library', title='指标公式新版', modality='txt', filename='new.txt')
    result = agent.query(FOLLOWUP, session_id='failed-execution')
    assert result['status'] == 'ok'
    assert result['result']['results']['result']['value'] == pytest.approx(22992/3*2)
    digest = agent.knowledge.document('formula-library')['sha256']
    assert result['state']['fusion_context']['documents'] == {'formula-library': digest}
    assert result['context_resolution']['document_versions'] == {}


def test_pending_multi_year_assignment_is_not_guessed_from_unexecuted_tasks(tmp_path):
    first = '根据经营预测PDF公式、区域目标Excel中华东2026增长率和数据库2025年华东销售额，算2026年目标销售额。'
    planner = ReplayPlanner([invalid_fusion(first)])
    agent = make_agent(tmp_path, planner)
    agent.query(first, session_id='pending-years')
    calls = len(planner.calls)
    result = agent.query('那华南2026年的目标呢，基准还是2025年？', session_id='pending-years')
    assert result['status'] == 'clarification' and len(planner.calls) == calls
    assert result['trace'][0]['rejection_code'] == 'fusion_followup_time_ambiguous'
