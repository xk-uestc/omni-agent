"""Successful dialogue edits must preserve scope and match independent SQL."""
from copy import deepcopy
from contextlib import closing
import sqlite3
import pytest
from backend.executed_scope_edit import ExecutedScopeEditAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


def expected(agent,year,region,metric='SUM(sales_amount)',channel=None):
    # Test-only metric strings are fixed, never supplied by a user.
    sql=f'SELECT {metric} FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?'
    parameters=[f'{year}-01-01',f'{year+1}-01-01',region]
    if channel is not None:sql+=' AND channel=?';parameters.append(channel)
    with closing(sqlite3.connect(agent.engine.database_path)) as connection:
        return connection.execute(sql,parameters).fetchone()[0]


def test_compound_completed_query_restarts_store_and_matches_independent_sql(agent):
    first=agent.query('2025年华东销售额',session_id='edits')
    assert first['status']=='ok'
    second=agent.query('时间改成2024年，地区改成华南，指标改成订单数',session_id='edits')
    assert second['status']=='ok'
    assert second['effective_question']=='2024年华南订单数'
    assert second['result']['rows']==[{'订单数':expected(agent,2024,'华南','COUNT(order_id)')}]
    assert len(second['context_resolution']['replacements'])==3
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    third=agent.query('增加渠道筛选为线上',session_id='edits')
    assert third['result']['rows']==[{'订单数':expected(agent,2024,'华南','COUNT(order_id)','线上')}]
    fourth=agent.query('地区改成华北',session_id='edits')
    assert fourth['result']['rows']==[{'订单数':expected(agent,2024,'华北','COUNT(order_id)','线上')}]
    fifth=agent.query('删除渠道筛选',session_id='edits')
    assert fifth['result']['rows']==[{'订单数':expected(agent,2024,'华北','COUNT(order_id)')}]
    assert '线上' not in fifth['effective_question']


@pytest.mark.parametrize('command',[
    '时间改成2024年，地区改成火星', '地区改成华南，时间改成0000年',
    '时间改成2024年，时间改成2023年', '时间改成2024年，删除未知字段',
    '时间改成2024年;DROP TABLE sales_orders', '地区改成订单数',
    '指标改成销售额并排除线上', '时间改成2024年，地区改成华南，指标改成订单数，渠道改成线上，品类改成手机',
])
def test_rejected_edit_never_executes_calls_model_or_replaces_success_history(agent,monkeypatch,command):
    first=agent.query('2025年华东销售额',session_id='reject')
    history=agent.conversations.context('reject')
    original=agent.engine.answer
    def forbidden(*args,**kwargs):raise AssertionError('rejected edit must not execute or call model')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    agent.client=type('Forbidden',(),{'generate':forbidden})()
    result=agent.query(command,session_id='reject')
    assert result['status']=='clarification' and result['result']['sql'] is None
    assert result['context_resolution']['mode']=='executed_sql_edit_rejected'
    assert result['effective_question']==first['effective_question']
    assert agent.conversations.context('reject')==history
    assert 'query_reference_id' not in result
    agent.client=None;monkeypatch.setattr(agent.engine,'answer',original)
    corrected=agent.query('地区改成华南',session_id='reject')
    assert corrected['result']['rows']==[{'销售额':expected(agent,2025,'华南')}]


def test_rejected_edit_does_not_duplicate_comparison_sources(agent):
    for q in ('2025年华东销售额','2025年华南销售额','地区改成火星'):
        agent.query(q,session_id='comparison')
    answer=agent.query('比较刚才两次查询',session_id='comparison')
    assert answer['status']=='ok'
    evidence=answer['result']['comparison_evidence']
    assert evidence['sources'][0]['question']=='2025年华东销售额'
    assert evidence['sources'][1]['question']=='2025年华南销售额'


@pytest.mark.parametrize('alteration',['filters','hash','revision','missing_record'])
def test_unverified_saved_context_cannot_authorize_named_edits(agent,alteration):
    agent.query('2025年华东销售额',session_id='tamper')
    history=deepcopy(agent.conversations.context('tamper'))
    state=history[-1].state
    if alteration=='filters':state['filters']=[]
    elif alteration=='hash':state['executed_sql_context']['sha256']='0'*64
    elif alteration=='revision':state['executed_sql_context']['payload']['source_revision']='wrong'
    else:state.pop('executed_sql_context')
    result=ExecutedScopeEditAgent(agent.engine).run('地区改成华南',history)
    assert result.verified is False and result.scope=='2025年华东销售额'


def test_changed_database_rejects_edit_and_new_independent_question_recovers(agent):
    agent.query('2025年华东销售额',session_id='changed')
    with closing(sqlite3.connect(agent.engine.database_path)) as connection:
        connection.execute('UPDATE sales_orders SET quantity=quantity+1');connection.commit()
    rejected=agent.query('地区改成华南',session_id='changed')
    assert rejected['status']=='clarification'
    assert rejected['context_resolution']['reason']=='executed_edit_context_invalid'
    fresh=agent.query('2025年华南销售额',session_id='changed')
    assert fresh['result']['rows']==[{'销售额':expected(agent,2025,'华南')}]


def test_explicit_history_reference_can_apply_multiple_named_edits(agent):
    first=agent.query('2025年华东销售额',session_id='reference')
    agent.query('2025年华北销售额',session_id='reference')
    answer=agent.query(f"回到SQL查询编号{first['query_reference_id']}，时间改成2024年，地区改成华南",session_id='reference')
    assert answer['status']=='ok'
    assert answer['result']['rows']==[{'销售额':expected(agent,2024,'华南')}]
    assert answer['context_resolution']['context_reference']['turn_id']==first['query_reference_id']


def test_other_session_and_context_reset_do_not_borrow_successful_scope(agent):
    agent.query('2025年华东销售额',session_id='owner')
    for session,reset in [('other',False),('owner',True)]:
        result=agent.query('地区改成华南',session_id=session,reset_context=reset)
        assert result['context_resolution']['mode']!='server_verified_executed_sql_edit'
        assert not result['result'].get('sql')


def test_named_edits_preserve_grouping_and_monthly_result_rows(agent):
    assert agent.query('2025年华东按月销售额趋势',session_id='monthly')['status']=='ok'
    result=agent.query('地区改成华南，时间改成2024年',session_id='monthly')
    assert result['status']=='ok'
    with closing(sqlite3.connect(agent.engine.database_path)) as connection:
        rows=connection.execute("SELECT strftime('%Y-%m',order_date),SUM(sales_amount) FROM sales_orders "
            "WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1",('2024-01-01','2025-01-01','华南')).fetchall()
    assert result['result']['rows']==[{'月份':month,'销售额':value} for month,value in rows]
