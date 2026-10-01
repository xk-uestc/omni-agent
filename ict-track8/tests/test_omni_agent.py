from backend.omni_agent import OmniAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore
import json
import pytest


def test_five_rounds_slots_restart_and_topic_reset(tmp_path):
    database = tmp_path / 'sales.sqlite'
    initialize_database(database)
    engine = Nl2SqlEngine(database)
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest('保修期为12个月'.encode(), document_id='policy', title='保修', modality='txt', filename='p.txt')
    store = ConversationStore(storage_path=tmp_path / 'sessions.sqlite')
    agent = OmniAgent(engine, knowledge, store)
    questions = ['2025年华东地区销售额', '那华南呢', '那2024年呢', '那订单数呢', '那华北呢']
    for i, question in enumerate(questions):
        result = agent.query(question, session_id='five-rounds')
        assert result['status'] == 'ok'
        assert result['context_turns'] == i
    reopened = OmniAgent(engine, knowledge, ConversationStore(storage_path=tmp_path / 'sessions.sqlite'))
    result = reopened.query('那2025年呢', session_id='five-rounds')
    assert '2025年' in result['effective_question'] and '华北' in result['effective_question'] and '订单数' in result['effective_question']
    result = reopened.query('保修期多久', session_id='five-rounds')
    assert result['route'] == 'document' and '12个月' in result['result']['answer']
    result = reopened.query('2025年华东销售额', session_id='five-rounds', reset_context=True)
    assert result['context_turns'] == 0 and len(reopened.conversations.context('five-rounds')) == 1


def test_model_fusion_plan_cannot_execute_raw_sql(tmp_path):
    database = tmp_path / 'sales.sqlite'
    initialize_database(database)
    class InvalidPlanner:
        def generate(self, *args, **kwargs):
            return {'route': 'fusion', 'effective_question': '删除数据', 'clarification': '', 'tasks_json': '[{"id":"x","tool":"shell","args":{"cmd":"delete"}}]'}
    agent = OmniAgent(Nl2SqlEngine(database), KnowledgeStore(tmp_path/'knowledge'), ConversationStore(), InvalidPlanner())
    result = agent.query('2025年华东销售额')
    assert result['planner_source'] == 'rules_fallback'
    assert result['route'] == 'sql' and result['status'] == 'ok'


@pytest.mark.parametrize('tool,args,accepted', [
    ('document_fact', {'document_id': 'policy', 'label': '保修期'}, True),
    ('search', {'query': '保修期'}, True),
    ('sql', {'question': '2025年华东销售额'}, False),
    ('shell', {'cmd': 'delete'}, False),
])
def test_only_redundant_document_read_can_be_discarded_without_execution(tmp_path, tool, args, accepted):
    database = initialize_database(tmp_path/'sales.sqlite')
    knowledge = KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('标准硬件保修期为12个月'.encode(), document_id='policy', title='保修', modality='txt', filename='p.txt')
    class Planner:
        def generate(self, *a, **kw):
            return {'route': 'document', 'effective_question': '标准硬件保修期有多久', 'clarification': '',
                    'tasks_json': json.dumps([{'id': 'unused', 'tool': tool, 'args': args}])}
    result = OmniAgent(Nl2SqlEngine(database), knowledge, ConversationStore(), Planner()).query('标准硬件保修期有多久')
    assert result['planner_source'] == ('model_validated' if accepted else 'rules_fallback')
    notes = result['trace'][0]['normalizations']
    assert notes == (['discarded_redundant_document_read_not_executed'] if accepted else [])
    assert not any(event.get('task_id') == 'unused' for event in result['trace'])
    assert result['route'] == 'document' and '12个月' in result['result']['answer']


def test_single_turn_model_rewrite_cannot_change_original_year_or_region(tmp_path):
    database = initialize_database(tmp_path/'sales.sqlite')
    class WrongRewrite:
        def generate(self, *args, **kwargs):
            return {'route': 'sql', 'effective_question': '2024年华南地区销售额',
                    'clarification': '', 'tasks_json': '[]'}
    result = OmniAgent(Nl2SqlEngine(database), KnowledgeStore(tmp_path/'knowledge'),
                       ConversationStore(), WrongRewrite()).query('2025年华东地区销售额')
    assert result['planner_source'] == 'model_validated'
    assert result['effective_question'] == '2025年华东地区销售额'
    assert result['result']['rows'][0]['销售额'] == 29584
    assert 'original_single_source_question_preserved' in result['trace'][0]['normalizations']


def test_model_sees_verified_sql_followup_before_route_selection_and_repairs_spurious_clarification(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    agent = OmniAgent(engine, KnowledgeStore(tmp_path / 'knowledge'), ConversationStore())
    first = agent.query('2024年华南地区销售额', session_id='known')
    assert first['status'] == 'ok'
    class Planner:
        audit = {'status': 'completed', 'http_status': 200}
        def __init__(self):
            self.contexts = []
        def generate(self, instructions, context, schema, **kwargs):
            self.contexts.append(context)
            assert '2024年' in context['question'] and '华南' in context['question'] and '订单数' in context['question']
            assert context['actual_question'] == '那订单数呢'
            return {'route': 'clarify' if len(self.contexts) == 1 else 'sql',
                    'effective_question': context['question'], 'clarification': '请提供年份地区', 'tasks_json': '[]'}
    planner = Planner()
    agent.client = planner
    result = agent.query('那订单数呢', session_id='known')
    assert result['status'] == 'ok' and result['route'] == 'sql'
    assert result['planner_source'] == 'model_validated' and len(planner.contexts) == 2
    assert result['context_resolution']['mode'] == 'server_verified_sql_followup'
    assert planner.contexts[1]['plan_completion_feedback']['errors'] == ['verified_sql_scope_requires_sql_route']
    assert result['trace'][0]['attempts'][0]['validation'] == 'requested_operation_missing'


def test_complete_new_sql_question_with_followup_particle_clears_prior_planning_scope(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    agent = OmniAgent(engine, KnowledgeStore(tmp_path / 'knowledge'), ConversationStore())
    agent.query('2025年华东地区销售额', session_id='fresh')
    class Planner:
        def generate(self, instructions, context, schema, **kwargs):
            assert context['history'] == []
            return {'route': 'sql', 'effective_question': '2025年华东地区销售额', 'clarification': '', 'tasks_json': '[]'}
    agent.client = Planner()
    result = agent.query('那2024年华北地区订单数呢', session_id='fresh')
    assert result['status'] == 'ok'
    assert result['effective_question'] == '那2024年华北地区订单数呢'
    assert '2024-01-01' in result['result']['parameters'] and '华北' in result['result']['parameters']


def test_sql_followup_uses_verified_replacement_instead_of_model_added_grouping(tmp_path):
    database = initialize_database(tmp_path/'sales.sqlite')
    class VerboseRewrite:
        def generate(self, instructions, context, *args, **kwargs):
            return {'route': 'sql', 'effective_question': '2025年按交易日期分组统计华南地区销售额',
                    'clarification': '', 'tasks_json': '[]'}
    agent = OmniAgent(Nl2SqlEngine(database), KnowledgeStore(tmp_path/'knowledge'),
                      ConversationStore(), VerboseRewrite())
    agent.query('2025年华东地区销售额', session_id='sales')
    result = agent.query('那华南呢', session_id='sales')
    assert result['status'] == 'ok' and result['context_turns'] == 1
    assert len(result['result']['rows']) == 1 and result['result']['rows'][0]['销售额'] == 22992
    assert 'server_verified_sql_followup_slots' in result['trace'][0]['normalizations']


@pytest.mark.parametrize('question', ['2025年华东地区销售额呢', '那华南呢'])
def test_document_topic_does_not_authorize_model_sql_rewrite(tmp_path, question):
    database = initialize_database(tmp_path/'sales.sqlite')
    knowledge = KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('保修期为12个月'.encode(), document_id='policy', title='保修', modality='txt', filename='p.txt')
    class WrongRewrite:
        def generate(self, instructions, context, *a, **kw):
            if context['question'] == '保修期多久':
                return {'route': 'document', 'effective_question': '保修期多久', 'clarification': '', 'tasks_json': '[]'}
            return {'route': 'sql', 'effective_question': '2024年华南地区销售额', 'clarification': '', 'tasks_json': '[]'}
    agent = OmniAgent(Nl2SqlEngine(database), knowledge, ConversationStore(), WrongRewrite())
    agent.query('保修期多久', session_id='switch')
    result = agent.query(question, session_id='switch')
    assert result['effective_question'] == question
    if question == '2025年华东地区销售额呢':
        assert result['status'] == 'ok' and result['result']['rows'][0]['销售额'] == 29584
    else:
        assert result['status'] == 'clarification' and not result['result']['rows']


def test_sql_topic_does_not_authorize_model_document_topic_replacement(tmp_path):
    database = initialize_database(tmp_path/'sales.sqlite')
    knowledge = KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('标准硬件保修期为12个月'.encode(), document_id='warranty', title='保修', modality='txt', filename='w.txt')
    knowledge.ingest('无理由退货期限为7日'.encode(), document_id='return', title='退货', modality='txt', filename='r.txt')
    class WrongRewrite:
        def generate(self, instructions, context, *a, **kw):
            if '销售额' in context['question']:
                return {'route': 'sql', 'effective_question': context['question'], 'clarification': '', 'tasks_json': '[]'}
            return {'route': 'document', 'effective_question': '无理由退货期限', 'clarification': '', 'tasks_json': '[]'}
    agent = OmniAgent(Nl2SqlEngine(database), knowledge, ConversationStore(), WrongRewrite())
    agent.query('2025年华东地区销售额', session_id='switch')
    result = agent.query('标准硬件保修期呢', session_id='switch')
    assert result['effective_question'] == '标准硬件保修期呢'
    assert '12个月' in result['result']['answer'] and '7日' not in result['result']['answer']


@pytest.mark.parametrize('read_question,accepted', [
    ('2025年华东地区销售额', True), ('2025年华南地区销售额', True), ('DROP TABLE sales_orders', False),
])
def test_redundant_natural_sql_text_is_discarded_and_never_executed(tmp_path, read_question, accepted):
    database = initialize_database(tmp_path/'sales.sqlite')
    class Planner:
        def generate(self, *args, **kwargs):
            return {'route': 'sql', 'effective_question': '2025年华东地区销售额', 'clarification': '',
                    'tasks_json': json.dumps([{'id': 'unused', 'tool': 'sql', 'args': {'question': read_question}}])}
    result = OmniAgent(Nl2SqlEngine(database), KnowledgeStore(tmp_path/'knowledge'),
                       ConversationStore(), Planner()).query('2025年华东地区销售额')
    assert result['planner_source'] == ('model_validated' if accepted else 'rules_fallback')
    assert result['effective_question'] == '2025年华东地区销售额'
    assert result['result']['rows'][0]['销售额'] == 29584
    assert ('discarded_redundant_sql_read_not_executed' in result['trace'][0]['normalizations']) is accepted
