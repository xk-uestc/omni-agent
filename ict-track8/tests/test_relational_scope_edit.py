"""Actual JOIN/CTE SQLite queries with independent results and adversarial edits."""
import sqlite3
from copy import deepcopy
import pytest
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.security import execute_read_only
from backend.sql_history_scope import saved_sql_context
from backend.knowledge_store import KnowledgeStore
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


JOIN_SQL='''SELECT c.name,SUM(p.amount) total,COUNT(p.id) n FROM payments p
 JOIN customers c ON p.customer_id=c.id
 WHERE c.region=? AND p.channel=? AND p.created_at>=? AND p.created_at<?
 GROUP BY c.id,c.name ORDER BY c.id LIMIT ?'''
CTE_SQL='''WITH per_customer AS (SELECT p.customer_id,SUM(p.amount) total FROM payments p
 WHERE p.channel=? AND p.created_at>=? AND p.created_at<? GROUP BY p.customer_id)
 SELECT c.name,t.total FROM customers c JOIN per_customer t ON c.id=t.customer_id
 WHERE c.region=? ORDER BY c.name LIMIT ?'''
BASE='2025年customers.region=华东和payments.channel=线上，按客户统计付款合计和记录数'


@pytest.fixture
def agent(tmp_path):
    path=tmp_path/'relational.sqlite'
    with sqlite3.connect(path) as con:
        con.executescript('''CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT,region TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(id),amount REAL,channel TEXT,created_at TEXT);
        INSERT INTO customers VALUES(1,'甲','华东'),(2,'乙','华南'),(3,'丙','华东');
        INSERT INTO payments VALUES(1,1,10,'线上','2025-02-01'),(2,1,50,'线下','2024-03-01'),
        (3,2,30,'线上','2025-03-01'),(4,2,80,'线下','2024-04-01'),(5,2,20,'线下','2024-06-01'),
        (6,3,100,'线上','2025-01-01');''')
    return OmniAgent(Nl2SqlEngine(path),KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


def original(agent,sql=JOIN_SQL,params=None,base=BASE):
    params=params or (['华东','线上','2025-01-01','2026-01-01',100] if sql==JOIN_SQL else ['线上','2025-01-01','2026-01-01','华东',100])
    with agent.engine._connect() as con:
        columns,rows=execute_read_only(con,sql,params)
        result={'status':'ok','sql':sql,'parameters':params,'result_state':'rows',
            'provenance':{'source_revision':agent.engine.current_source_revision()}}
    state={'route':'sql','pending_question':None,'clarification_code':None,'metrics':[],'filters':[],'dimensions':[],
        'executed_sql_context':saved_sql_context(base,result)}
    agent.conversations.remember('relational',question=base,effective_question=base,state=state)
    return rows


@pytest.mark.parametrize('sql',[JOIN_SQL,CTE_SQL])
def test_atomic_cross_table_edits_preserve_all_sql_bytes_and_match_reference(agent,sql):
    original(agent,sql)
    result=agent.query('customers.region改成华南，payments.channel改成线下，时间改成2024年',session_id='relational')
    assert result['status']=='ok',result
    assert result['result']['sql']==sql
    expected_params=['华南','线下','2024-01-01','2025-01-01',100] if sql==JOIN_SQL else ['线下','2024-01-01','2025-01-01','华南',100]
    assert result['result']['parameters']==expected_params
    with sqlite3.connect(agent.engine.database_path) as con:
        assert result['result']['rows']==list(execute_read_only(con,sql,expected_params)[1])
    assert result['result']['rows'][0]['total']==100
    assert result['context_resolution']['mode']=='server_verified_relational_parameter_edit'
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    result=agent.query('payments.channel改成线上，时间改成2025年',session_id='relational')
    assert result['status']=='ok' and result['result']['rows'][0]['total']==30


@pytest.mark.parametrize('edit',[
 'customers.region改成火星','customers.region改成华南，payments.channel改成不存在',
 'customers.region改成华南，时间改成0000年','customers.region改成华南，customers.region改成华东',
 'customers.region改成华南，删除payments.channel条件','payments.amount改成华南',
 'customers.region改成华南;DROP TABLE payments','customers.region改成华南，指标改成记录数',
])
def test_rejected_batch_does_not_execute_or_change_history(agent,edit,monkeypatch):
    original(agent)
    before=agent.conversations.context('relational')
    def forbidden(*args,**kwargs):raise AssertionError('unverified batch executed')
    monkeypatch.setattr('backend.relational_scope_edit.execute_read_only',forbidden)
    answer=agent.query(edit,session_id='relational')
    assert answer['status']=='clarification' and answer['result']['sql'] is None
    assert agent.conversations.context('relational')==before


@pytest.mark.parametrize('mutation',['source','hash','missing','scope'])
def test_changed_source_or_execution_record_is_not_authority(agent,mutation):
    original(agent)
    if mutation=='source':
        with sqlite3.connect(agent.engine.database_path) as con:
            con.execute('UPDATE payments SET amount=amount+1')
    else:
        turn=agent.conversations.context('relational')[-1]
        state=deepcopy(turn.state)
        if mutation=='hash':state['executed_sql_context']['sha256']='0'*64
        elif mutation=='missing':state['executed_sql_context']['payload']['parameters']=[]
        else:state['executed_sql_context']['payload']['question']='wrong'
        with agent.conversations._connect() as con:
            import json
            con.execute('UPDATE conversation_turns SET state=? WHERE turn_id=?',(json.dumps(state),turn.turn_id))
    answer=agent.query('customers.region改成华南',session_id='relational')
    assert answer['status']=='clarification'


def test_or_branch_join_and_having_are_never_editable_filter_slots(agent):
    sql=JOIN_SQL.replace('c.region=? AND p.channel=?','(c.region=? OR p.channel=?)')
    original(agent,sql=sql,params=['华东','线上','2025-01-01','2026-01-01',100])
    answer=agent.query('customers.region改成华南',session_id='relational')
    assert answer['status']=='clarification'


def test_other_session_and_reset_do_not_borrow_complex_query(agent):
    original(agent)
    for session,reset in [('stranger',False),('relational',True)]:
        answer=agent.query('customers.region改成华南',session_id=session,reset_context=reset)
        assert answer['context_resolution']['mode']!='server_verified_relational_parameter_edit'


def test_nested_cte_having_and_scalar_subquery_are_preserved(agent):
    sql='''WITH per_customer AS (SELECT p.customer_id,SUM(p.amount) total,COUNT(*) n FROM payments p
     WHERE p.channel=? AND p.created_at>=? AND p.created_at<? GROUP BY p.customer_id HAVING SUM(p.amount)>?)
     SELECT c.name,t.total,(SELECT COUNT(*) FROM payments p2 WHERE p2.customer_id=c.id) lifetime_n
     FROM customers c JOIN per_customer t ON c.id=t.customer_id WHERE c.region=? ORDER BY t.total DESC LIMIT ?'''
    original(agent,sql=sql,params=['线上','2025-01-01','2026-01-01',1,'华东',100])
    result=agent.query('customers.region改成华南，payments.channel改成线下，时间改成2024年',session_id='relational')
    assert result['status']=='ok',result
    assert result['result']['sql']==sql
    assert result['result']['parameters']==['线下','2024-01-01','2025-01-01',1,'华南',100]
    assert result['result']['rows']==[{'name':'乙','total':100.0,'lifetime_n':3}]


def test_two_hundred_cross_table_turns_match_independent_reference(agent):
    original(agent)
    expected_sql='''SELECT c.name,SUM(p.amount) total,COUNT(p.id) n FROM payments p JOIN customers c ON p.customer_id=c.id
      WHERE c.region=? AND p.channel=? AND p.created_at>=? AND p.created_at<? GROUP BY c.id,c.name ORDER BY c.id LIMIT 100'''
    for i in range(200):
        region=('华南','华东')[i%2];channel=('线下','线上')[i//2%2];year=2024+i//4%2
        if i%17==0:
            before=agent.conversations.context('relational')
            failed=agent.query('customers.region改成华南，payments.channel改成无效',session_id='relational')
            assert failed['status']=='clarification' and agent.conversations.context('relational')==before
        if i%25==0:
            agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
        result=agent.query(f'customers.region改成{region}，payments.channel改成{channel}，时间改成{year}年',session_id='relational')
        assert result['status']=='ok',result
        with sqlite3.connect(agent.engine.database_path) as con:
            con.row_factory=sqlite3.Row
            expected=[dict(row) for row in con.execute(expected_sql,(region,channel,f'{year}-01-01',f'{year+1}-01-01'))]
        assert result['result']['rows']==expected
        assert result['result']['sql']==JOIN_SQL


def test_parameter_values_cannot_bind_to_join_or_projection(agent):
    sql=JOIN_SQL.replace('c.region=? AND','c.name!=? AND').replace('p.channel=?','p.channel=?')
    original(agent,sql=sql,params=['华东','线上','2025-01-01','2026-01-01',100])
    assert agent.query('customers.region改成华南',session_id='relational')['status']=='clarification'


def test_real_model_julianday_date_predicates_support_atomic_and_time_only_edit(agent):
    sql=JOIN_SQL.replace('p.created_at>=?','JULIANDAY(p.created_at)>=JULIANDAY(?)').replace('p.created_at<?','JULIANDAY(p.created_at)<JULIANDAY(?)')
    original(agent,sql=sql,params=['华东','线上','2025-01-01','2026-01-01',100])
    answer=agent.query('customers.region改成华南，payments.channel改成线下，时间改成2024年',session_id='relational')
    assert answer['status']=='ok',answer
    assert answer['result']['sql']==sql and answer['result']['rows']==[{'name':'乙','total':100.0,'n':2}]
    answer=agent.query('时间改成2025年',session_id='relational')
    assert answer['status']=='ok' and answer['result']['rows']==[]


@pytest.mark.parametrize('fragment',[
    'JULIANDAY(p.created_at,\'unixepoch\')>=JULIANDAY(?)',
    'JULIANDAY(p.created_at)>=JULIANDAY(?,\'unixepoch\')',
    'p.created_at>=JULIANDAY(?)',
])
def test_unmatched_or_modified_date_wrappers_are_not_parameter_edit_proof(agent,fragment):
    sql=JOIN_SQL.replace('p.created_at>=?',fragment)
    original(agent,sql=sql,params=['华东','线上','2025-01-01','2026-01-01',100])
    assert agent.query('时间改成2024年',session_id='relational')['status']=='clarification'
