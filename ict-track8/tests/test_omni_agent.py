from backend.omni_agent import OmniAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


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
