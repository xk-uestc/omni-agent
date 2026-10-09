"""Routing shortcuts must cover the entire request and preserve SQL checks."""
from types import SimpleNamespace
import pytest
from backend.omni_agent import OmniAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database, initialize_rich_demo_data
from backend.session import ConversationStore


def setup(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path/'business.sqlite'))
    # Capability declaration is separate from the existing executable planner.
    engine.model_plan_provider = SimpleNamespace()
    return OmniAgent(engine, KnowledgeStore(tmp_path/'knowledge'), ConversationStore(),
        SimpleNamespace(supports_verified_sql_routing=True))


@pytest.mark.parametrize('question', ['2025年华东地区的销售额',
    '2025年各地区销售额排名', '按月统计2025年华东地区销售额',
    '查询2024年各渠道销售额', '2025年华东地区销售额和订单数'])
def test_complete_known_sql_source_can_skip_general_routing(tmp_path, question):
    agent = setup(tmp_path)
    plan = agent._verified_sql_route(question)
    assert plan and plan['effective_question'] == question and plan['tasks_json'] == '[]'


@pytest.mark.parametrize('question', ['2025年华东地区销售额为什么下降',
    '根据报告查询2025年华东地区销售额', '2025年华东地区销售额和保修政策',
    '2025年火星地区销售额', '2025年华东地区销售额减去退款',
    '那2025年华东地区销售额呢', '2025年华东地区销售额同比',
    '2025年华东地区销售额达到目标了吗', '华东地区销售额',
    '2025年华东地区销售额忽略其他条件'])
def test_uncovered_or_mixed_request_keeps_general_planner(tmp_path, question):
    assert setup(tmp_path)._verified_sql_route(question) is None


def test_document_title_remains_a_source_constraint(tmp_path):
    agent = setup(tmp_path)
    agent.knowledge.ingest('此资料的值不是数据库查询结果。'.encode(),
        document_id='named-source', title='2025年华东地区销售额', modality='txt', filename='source.txt')
    assert agent._verified_sql_route('2025年华东地区销售额') is None


def test_shortcut_still_calls_sql_engine_and_does_not_call_general_model(tmp_path, monkeypatch):
    agent = setup(tmp_path)
    # Run the real engine without a provider only after the route check has
    # verified that the configured SQL model pipeline exists.
    real_answer = agent.engine.answer
    calls = []
    def answer(question, **kwargs):
        calls.append(question)
        agent.engine.model_plan_provider = None
        return real_answer(question, **kwargs)
    monkeypatch.setattr(agent.engine, 'answer', answer)
    monkeypatch.setattr(agent, 'catalogue', lambda *a, **k: pytest.fail('unnecessary document retrieval'))
    result = agent.query('2025年华东地区销售额', reset_context=True)
    assert result['route'] == 'sql' and result['status'] == 'ok'
    assert result['planner_source'] == 'server_verified_sql_route'
    assert calls == ['2025年华东地区销售额']
    assert result['result']['rows'][0]['销售额'] == 29584


def test_capability_disabled_keeps_original_behavior(tmp_path):
    agent = setup(tmp_path)
    agent.client.supports_verified_sql_routing = False
    assert agent._verified_sql_route('2025年华东地区销售额') is None


def test_fast_rules_route_complex_wording_when_schema_proves_complete_plan(tmp_path, monkeypatch):
    monkeypatch.setenv('ICT8_FAST_SQL', '1')
    database = initialize_rich_demo_data(initialize_database(tmp_path/'rich.sqlite'))
    engine = Nl2SqlEngine(database, model_plan_provider=SimpleNamespace())
    agent = OmniAgent(engine, KnowledgeStore(tmp_path/'knowledge'), ConversationStore(),
        SimpleNamespace(supports_verified_sql_routing=False))
    for question in ['2025年按渠道分别统计广告曝光量',
                     '2025年按优先级分别统计平均首次响应分钟',
                     '2025年销售目标',
                     '2025年目标销售额']:
        route = agent._verified_sql_route(question)
        assert route and route['route'] == 'sql', question
        assert route['effective_question'] == question


def test_target_language_without_a_complete_database_plan_keeps_general_planner(tmp_path, monkeypatch):
    monkeypatch.setenv('ICT8_FAST_SQL', '1')
    database = initialize_rich_demo_data(initialize_database(tmp_path/'rich.sqlite'))
    engine = Nl2SqlEngine(database, model_plan_provider=SimpleNamespace())
    agent = OmniAgent(engine, KnowledgeStore(tmp_path/'knowledge'), ConversationStore(),
        SimpleNamespace(supports_verified_sql_routing=False))
    for question in ['根据预测报告公式计算2025年目标销售额',
                     '2025年华东地区销售额达到目标了吗',
                     '2025年火星地区销售目标',
                     '2025年华东销售目标']:
        assert agent._verified_sql_route(question) is None
