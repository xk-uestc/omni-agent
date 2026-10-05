"""Atomic predicate mutations, including independent SQL result checks."""
import sqlite3
import pytest
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


def independent(agent, *, year=2025, region='华东', channel=None, exclude_region=None):
    clauses=['order_date>=?','order_date<?'];parameters=[f'{year}-01-01',f'{year+1}-01-01']
    for column,value in [('region',region),('channel',channel)]:
        if value is not None:clauses.append(f'{column}=?');parameters.append(value)
    if exclude_region is not None:
        clauses.append('region!=?');parameters.append(exclude_region)
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.row_factory=sqlite3.Row
        sql="SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 FROM sales_orders WHERE "
        return [dict(row) for row in connection.execute(sql+' AND '.join(clauses)+' GROUP BY 1 ORDER BY 1',parameters)]


@pytest.mark.parametrize('command',[
    '增加渠道筛选为线上','添加渠道=线上','新增sales_orders.channel:线上','加上线上','增加线上筛选'])
def test_add_keeps_scope_and_restores_from_sqlite(agent,command):
    agent.query('2025年华东销售额趋势',session_id='filters')
    pending=agent.query(command,session_id='filters')
    assert pending['status']=='clarification'
    assert pending['context_resolution']['mode']=='server_verified_pending_sql_edit'
    assert pending['context_resolution']['replacements']==[
        {'slot':'sales_orders.channel','from':'','to':'线上','operation':'add'}]
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    result=agent.query('按月统计',session_id='filters')
    assert result['result']['rows']==independent(agent,channel='线上')


@pytest.mark.parametrize('command',['删除地区筛选','移除华东条件','取消sales_orders.region筛选'])
def test_remove_only_named_predicate(agent,command):
    agent.query('2025年华东线上销售额趋势',session_id='remove')
    result=agent.query(f'{command}，按月统计',session_id='remove')
    assert result['status']=='ok', result
    assert result['result']['rows']==independent(agent,region=None,channel='线上')
    assert result['context_resolution']['replacements'][0]['operation']=='remove'


def test_remove_negative_predicate_removes_operator_as_well(agent):
    original=agent.query('2025年排除华东销售额趋势',session_id='negative')
    assert original['status']=='clarification'
    from backend.pending_filter_edit import PendingFilterEditAgent
    mutation=PendingFilterEditAgent(agent.engine).run('删除地区筛选',original['effective_question'])
    assert '排除' not in mutation.scope, (mutation,agent.engine.analyze_slots(original['effective_question'])['values'])
    result=agent.query('删除地区筛选，按月统计',session_id='negative')
    assert result['status']=='ok', result['context_resolution']['reason']
    assert result['result']['rows']==independent(agent,region=None)
    assert '排除' not in result['effective_question']


def test_compound_mutations_are_atomic(agent):
    agent.query('2025年华东销售额趋势',session_id='compound')
    result=agent.query('删除地区筛选，增加渠道筛选为线上，时间改成2024年，按月统计',session_id='compound')
    assert result['status']=='ok'
    assert result['result']['rows']==independent(agent,year=2024,region=None,channel='线上')


@pytest.mark.parametrize('command',[
    '增加地区筛选为华南，按月统计',
    '增加渠道筛选为线上，增加渠道筛选为线下，按月统计',
    '删除地区筛选，增加渠道筛选为火星，按月统计',
    '删除地区筛选，删除地区筛选，按月统计',
    '增加地区筛选为线上，按月统计',
    '增加渠道筛选为线上或者线下，按月统计',
    '删除全部筛选，按月统计','删除华，按月统计',
    '增加渠道筛选为线上;DROP TABLE sales_orders','删除渠道筛选，按月统计'])
def test_failed_mutation_preserves_original_and_can_continue(agent,command):
    original=agent.query('2025年华东销售额趋势',session_id='reject')
    result=agent.query(command,session_id='reject')
    assert result['status']=='clarification'
    assert result['effective_question']==original['effective_question']
    assert result['result']['sql'] is None
    assert result['context_resolution']['mode']=='pending_sql_clarification_retained'
    assert agent.query('按月统计',session_id='reject')['result']['rows']==independent(agent)


def test_redundant_add_is_idempotent_and_can_complete(agent):
    original=agent.query('2025年华东线上销售额趋势',session_id='retain')
    result=agent.query('增加渠道筛选为线上，按月统计',session_id='retain')
    assert result['status']=='ok'
    assert result['result']['rows']==independent(agent,channel='线上')
    assert result['context_resolution']['replacements'][0]['operation']=='retain'
    closed=agent.query(f"继续待补查询编号{original['query_reference_id']}",session_id='retain')
    assert closed['result']['clarification_code']=='pending_reference_completed'


def test_positive_add_does_not_silently_retain_opposite_negative_filter(agent):
    original=agent.query('2025年排除华东销售额趋势',session_id='opposite')
    rejected=agent.query('增加地区筛选为华东，按月统计',session_id='opposite')
    assert rejected['status']=='clarification'
    assert rejected['effective_question']==original['effective_question']
    assert rejected['context_resolution']['reason']=='pending_filter_already_constrained'
    final=agent.query('按月统计',session_id='opposite')
    assert final['status']=='ok'
    assert final['result']['rows']==independent(agent,region=None,exclude_region='华东')


def test_source_update_and_new_session_have_no_edit_authority(agent):
    agent.query('2025年华东销售额趋势',session_id='source')
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1')
    changed=agent.query('增加渠道筛选为线上，按月统计',session_id='source')
    assert changed['result']['clarification_code']=='pending_source_changed'
    foreign=agent.query('删除地区筛选',session_id='foreign')
    assert foreign['context_resolution'].get('mode')!='server_verified_pending_sql_edit'
