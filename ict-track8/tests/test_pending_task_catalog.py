import sqlite3
import pytest
from backend.session import ConversationStore
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent


@pytest.fixture(params=['memory','sqlite'])
def setup(request,tmp_path):
    now=[100.]
    options={'clock':lambda:now[0],'ttl_seconds':10,'pending_ttl_seconds':100}
    if request.param=='sqlite':options['storage_path']=tmp_path/'sessions.sqlite'
    store=ConversationStore(**options)
    agent=OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),KnowledgeStore(tmp_path/'knowledge'),store)
    return agent,now,options


def test_latest_snapshot_per_root_and_catalog_never_changes_pending_context(setup,monkeypatch):
    agent,now,_=setup
    first=agent.query('2025年华东销售额趋势',session_id='catalog')
    now[0]+=1
    edited=agent.query('时间改成2024年',session_id='catalog')
    now[0]+=1
    second=agent.query('2025年华南订单数趋势',session_id='catalog')
    history=agent.conversations.context('catalog')
    def no_query(*args,**kwargs):raise AssertionError('Catalog must never execute SQL')
    monkeypatch.setattr(agent.engine,'answer',no_query)
    catalog=agent.query('查看待补问题',session_id='catalog')
    assert catalog['route']=='tasks'
    assert 'query_reference_id' not in catalog
    assert agent.conversations.context('catalog')==history
    tasks=catalog['pending_tasks']
    assert len(tasks)==2
    assert [t['query_reference_id'] for t in tasks]==[second['query_reference_id'],edited['query_reference_id']]
    assert tasks[1]['question']=='2024年华东销售额趋势'
    assert all(t['available'] for t in tasks)
    assert all(t['query_reference_id']!=first['query_reference_id'] for t in tasks)


def test_generic_resume_lists_choices_instead_of_guessing_then_explicit_choice_executes(setup):
    agent,now,_=setup
    first=agent.query('2025年华东销售额趋势',session_id='catalog')
    now[0]+=1
    agent.query('2024年华南订单数趋势',session_id='catalog')
    tasks=agent.query('继续没完成的问题',session_id='catalog')['pending_tasks']
    target=next(t for t in tasks if t['query_reference_id']==first['query_reference_id'])
    completed=agent.query(f"继续待补查询编号{target['query_reference_id']}，按月统计",session_id='catalog')
    assert completed['status']=='ok'
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.row_factory=sqlite3.Row
        expected=[dict(row) for row in connection.execute("SELECT strftime('%Y-%m',order_date) AS 月份,SUM(sales_amount) AS 销售额 "
            "FROM sales_orders WHERE order_date>='2025-01-01' AND order_date<'2026-01-01' AND region='华东' GROUP BY 1 ORDER BY 1")]
    assert completed['result']['rows']==expected
    remaining=agent.query('查看待补问题',session_id='catalog')['pending_tasks']
    assert len(remaining)==1 and remaining[0]['question']=='2024年华南订单数趋势'


def test_identical_independent_questions_are_not_collapsed_into_one_task(setup):
    agent,now,_=setup
    first=agent.query('2025年华东销售额趋势',session_id='catalog')
    now[0]+=1
    # Explicit complete question is a new task, even when wording is identical.
    second=agent.query('2025年华东销售额趋势',session_id='catalog')
    tasks=agent.query('查看待补问题',session_id='catalog')['pending_tasks']
    assert {t['query_reference_id'] for t in tasks}=={first['query_reference_id'],second['query_reference_id']}


def test_history_ttl_and_catalog_do_not_extend_pending_expiry(setup):
    agent,now,_=setup
    agent.query('2025年华东销售额趋势',session_id='catalog')
    now[0]+=11
    assert not agent.conversations.context('catalog')
    tasks=agent.query('查看待补问题',session_id='catalog')['pending_tasks']
    assert len(tasks)==1 and tasks[0]['expires_at']==200
    now[0]=200
    assert agent.query('查看待补问题',session_id='catalog')['pending_tasks']==[]


def test_source_change_visible_but_never_reauthorizes_old_snapshot(setup):
    agent,_,_=setup
    first=agent.query('2025年华东销售额趋势',session_id='catalog')
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1')
    tasks=agent.query('查看待补问题',session_id='catalog')['pending_tasks']
    assert len(tasks)==1 and tasks[0]['available'] is False
    assert tasks[0]['reason']=='pending_reference_source_changed'
    rejected=agent.query(f"继续待补查询编号{first['query_reference_id']}",session_id='catalog')
    assert rejected['result']['clarification_code']=='pending_reference_source_changed'


def test_foreign_session_reset_and_stateless_catalog_never_reveal_tasks(setup):
    agent,_,_=setup
    agent.query('2025年华东销售额趋势',session_id='catalog')
    assert agent.query('查看待补问题',session_id='foreign')['pending_tasks']==[]
    assert agent.query('查看待补问题')['pending_tasks']==[]
    assert agent.query('查看待补问题',session_id='catalog',reset_context=True)['pending_tasks']==[]


def test_sqlite_restart_retains_catalog_without_restoring_chat(tmp_path):
    database=initialize_database(tmp_path/'db.sqlite')
    engine=Nl2SqlEngine(database)
    agent=OmniAgent(engine,KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))
    first=agent.query('2025年华东销售额趋势',session_id='restart')
    agent.conversations=ConversationStore(storage_path=tmp_path/'sessions.sqlite')
    tasks=agent.query('查看待补问题',session_id='restart')['pending_tasks']
    assert len(tasks)==1 and tasks[0]['query_reference_id']==first['query_reference_id']
