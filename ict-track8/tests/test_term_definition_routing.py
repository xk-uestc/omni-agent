"""Terminology definitions retrieve documents; numerical requests retain SQL."""
import sqlite3

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.unified_routing import source_hint, term_definition_request


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    return Nl2SqlEngine(initialize_database(tmp_path_factory.mktemp('term-routing')/'db.sqlite'))


@pytest.fixture
def agent(engine,tmp_path):
    knowledge = KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest(
        ('成本是为取得商品或服务付出的金额。销售成本的计算方法是销售额减去毛利。\n\n'
         '本金是金融业务最初投入或借入的资金，不包含利息。本钱是本金或投入资金的口语称呼。\n\n'
         '毛利的计算方法是销售额减去销售成本。销售额是已经成交订单的收入金额。\n\n'
         '订单数量是满足指定条件的订单记录数量。华东采用定期客户回访的销售方法。').encode(),
        document_id='term-manual',title='业务术语与指标口径手册',modality='txt',filename='terms.txt')
    return OmniAgent(engine,knowledge,ConversationStore())


@pytest.mark.parametrize('question',[
    '成本是什么意思', '成本是什么', '成本是啥', '成本啥意思',
    '成本怎么计算', '成本怎么算', '成本如何计算', '成本计算方法是什么',
    '本金是什么意思', '本金怎么计算', '本金怎么算',
    '本钱是什么意思', '本钱怎么计算', '本钱怎么算',
    '毛利是什么意思', '销售额是什么意思', '订单数量是什么意思',
    '什么叫成本', '啥叫本金', '请解释一下成本是什么意思',
])
def test_pure_term_definition_is_document_in_hint_and_basic_plan(agent,question):
    assert term_definition_request(question)
    assert source_hint(question,agent.engine) == 'document'
    plan = agent.basic_plan(question,())
    assert plan['route'] == 'document'
    assert plan['effective_question'] == question


@pytest.mark.parametrize('question',[
    '成本怎么计算', '本金怎么计算', '本钱怎么计算',
    '成本是什么意思', '销售额是什么意思', '订单数量是什么意思',
])
@pytest.mark.parametrize('with_history',[False,True])
def test_real_knowledge_retrieval_never_executes_sql_for_definition(agent,monkeypatch,question,with_history):
    if with_history:
        assert agent.query('2025年华东销售额',session_id='definitions')['status'] == 'ok'
    def forbidden(*args,**kwargs):
        raise AssertionError('a pure definition must not execute SQL or call route model')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    agent.client = type('NoApi',(),{'generate':forbidden})()
    result = agent.query(question,session_id='definitions')
    assert result['route'] == 'document', result
    assert result['effective_question'] == question
    assert not result['result'].get('sql')
    assert result['status'] == 'ok', result['result']
    assert result['result'].get('citations')
    assert {citation['metadata']['document_id'] for citation in result['result']['citations']} == {'term-manual'}
    assert 'executed_sql_context' not in result['state']


@pytest.mark.parametrize('question',[
    '2025年销售额最高的地区是什么', '2025年销售额排名第一的地区是什么',
    '2025年销售额排名前3的地区是什么', '订单数量是多少',
    '2025年订单数量是多少', '查询订单数量是多少',
    'SQL查询2025年各地区销售额', '数据库2025年华东销售额是多少',
    '2025年各地区销售额排名', '2025年销售额按地区分组是什么',
])
def test_numerical_and_rank_questions_remain_sql_in_basic_plan(agent,question):
    assert not term_definition_request(question)
    assert source_hint(question,agent.engine) == 'sql', question
    assert agent.basic_plan(question,())['route'] == 'sql'


@pytest.mark.parametrize('question,sql,params',[
    ('订单数量是多少','SELECT COUNT(order_id) FROM sales_orders',()),
    ('2025年订单数量是多少','SELECT COUNT(order_id) FROM sales_orders WHERE order_date>=? AND order_date<?',
     ('2025-01-01','2026-01-01')),
    ('2025年华东销售额是多少','SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?',
     ('2025-01-01','2026-01-01','华东')),
])
def test_actual_numeric_queries_still_match_independent_sql(agent,question,sql,params):
    result = agent.query(question)
    assert result['route'] == 'sql' and result['status'] == 'ok', result
    with sqlite3.connect(agent.engine.database_path) as connection:
        gold = connection.execute(sql,params).fetchone()[0]
    assert list(result['result']['rows'][0].values()) == [gold]


def test_winner_question_is_not_misrouted_as_term_definition(agent):
    result = agent.query('2025年销售额最高的地区是什么',session_id='winner')
    assert result['route'] == 'sql' and result['status'] == 'ok', result
    with sqlite3.connect(agent.engine.database_path) as connection:
        gold = connection.execute('SELECT region,SUM(sales_amount) FROM sales_orders '
            'WHERE order_date>=? AND order_date<? GROUP BY region ORDER BY SUM(sales_amount) DESC LIMIT 1',
            ('2025-01-01','2026-01-01')).fetchone()
    assert result['result']['rows'][0]['region'] == gold[0]
    assert result['result']['rows'][0]['销售额'] == gold[1]


def test_sql_entity_bridge_definition_remains_document_retrieval(agent):
    first = agent.query('2025年各地区销售额排名',session_id='entity-bridge')
    assert first['status'] == 'ok'
    selected = agent.query('华东排名多少',session_id='entity-bridge')
    assert selected['status'] == 'ok'
    answer = agent.query('它的销售方法是什么',session_id='entity-bridge')
    assert answer['route'] == 'document' and answer['status'] == 'ok', answer
    assert answer['effective_question'] == '华东的销售方法是什么'
    assert answer['result']['sql_entity_evidence']['entity'] == '华东'
    assert '定期客户回访' in answer['result']['answer']
    assert answer['result'].get('citations')
