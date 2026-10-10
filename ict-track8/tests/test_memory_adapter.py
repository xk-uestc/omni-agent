"""Real SQLite + OmniAgent memory contracts with unseen terms, no task-ID cases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import sqlite3
import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.memory.core import MemoryCore, MemoryRecord, MemoryStore, TrustedScope
from backend.memory.adapter import MemoryAdapter, classify_outcome


@pytest.fixture
def setup(tmp_path):
    db=tmp_path/'orders.sqlite'
    with sqlite3.connect(db) as c:
        c.executescript('CREATE TABLE sales_orders(order_id TEXT PRIMARY KEY,order_date TEXT,region TEXT,channel TEXT,sales_amount REAL,cost_amount REAL,gross_profit REAL);')
        c.executemany('INSERT INTO sales_orders VALUES(?,?,?,?,?,?,?)',[
            ('1','2025-01-01','华东','线上',100,40,60),('2','2025-01-02','华东','门店',200,80,120),
            ('3','2024-02-01','华南','线上',500,250,250),('4','2025-02-01','华南','门店',50,20,30)])
    aliases=tmp_path/'aliases.json'
    aliases.write_text(json.dumps([{'table':'sales_orders','column':col,'role':'metric','metric_function':'SUM','aliases':[label]}
        for col,label in [('sales_amount','销售额'),('cost_amount','销售成本'),('gross_profit','毛利')]],ensure_ascii=False))
    engine=Nl2SqlEngine(db,aliases_path=aliases,reference_date=date(2026,10,9),metric_catalog_path=tmp_path/'no-catalog.json')
    knowledge=KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('晨光额：销售成本。银杉额：线上销售额。'.encode(),document_id='terms',title='业务确认卡',modality='txt',filename='terms.txt')
    scope=TrustedScope('unit-deployment','unit-project',('db','terms'))
    core=MemoryCore(MemoryStore(tmp_path/'memory.sqlite'),scope=scope,enabled=True)
    adapter=MemoryAdapter(core,engine,knowledge,database_source='db',clock=lambda:'2026-10-10T12:00:00+08:00')
    def add(term='晨光额',field='cost_amount',filters=None,ident='cost',**changes):
        r=dict(memory_id=ident,memory_type='business_semantics',term=term,content='fixture confirmation',
            binding={'table':'sales_orders','column':field,'function':'SUM','filters':filters or []},scope=scope,
            provenance={'authority':'server_fixture','confirmation_id':ident,'verification_id':'independent-check','evidence_id':'terms:1'},
            source_version=adapter.source_version(('terms',)), valid_from='2026-10-01T00:00:00+08:00',valid_to=None,
            verification_state='confirmed',created_at='2026-10-01T00:00:00+08:00',updated_at='2026-10-01T00:00:00+08:00',task_trace_id=None)
        core.store.put(MemoryRecord(**(r|changes)))
    add();add('银杉额','sales_amount',[{'column':'channel','value':'线上'}],'online')
    agent=OmniAgent(engine,knowledge,ConversationStore(),memory=adapter)
    return agent,adapter,add


def value(response):
    return next(iter(response['result']['rows'][0].values()))


def test_cross_session_actual_execution_and_original_question(setup):
    a,m,_=setup
    first=a.query('2025年华东晨光额',session_id='one')
    second=a.query('2024年华南晨光额',session_id='two')
    assert value(first)==120 and value(second)==250
    assert second['question']=='2024年华南晨光额'
    assert a.conversations.context('two')[0].question=='2024年华南晨光额'
    assert second['memory']['consumed']==['cost'] and second['memory']['recall_count']==1
    assert 'cost_amount' in second['result']['sql'] and second['context_turns']==0
    assert len(m.core.store.events())==2


def test_reopen_and_disabled_restores_behavior(setup):
    a,m,_=setup
    m.core=MemoryCore(MemoryStore(m.core.store.path),scope=m.core.scope,enabled=True)
    assert value(a.query('2025年华东晨光额',session_id='new'))==120
    m.core.enabled=False
    r=a.query('2025年华东晨光额',session_id='off')
    old=OmniAgent(a.engine,a.knowledge,ConversationStore()).query('2025年华东晨光额')
    assert r['status']==old['status'] and r['route']==old['route'] and 'memory' not in r


def test_filter_binding_and_explicit_conflict(setup):
    a,_,_=setup
    assert value(a.query('2025年华东银杉额'))==100
    assert value(a.query('2024年华南线上银杉额'))==500
    r=a.query('2025年华东门店银杉额')
    assert r['status']=='clarification' and not r['result'].get('sql')
    assert 'explicit_condition_conflict' in str(r['memory'])


@pytest.mark.parametrize('change,reason',[
    ({'verification_state':'candidate'},'unverified'),({'valid_to':'2026-10-09T00:00:00+08:00'},'expired'),
    ({'binding':{'table':'sales_orders','column':'missing','function':'SUM','filters':[]}},'invalid_metric')])
def test_matching_invalid_memory_clarifies(setup,change,reason):
    a,m,add=setup;add(**change)
    r=a.query('2025年华东晨光额')
    assert r['status']=='clarification' and reason in str(r['memory']) and not r['result'].get('sql')


def test_document_and_schema_changes_rejected(setup):
    a,m,add=setup
    a.knowledge.ingest('已撤回'.encode(),document_id='terms',title='业务确认卡',modality='txt',filename='terms.txt')
    assert 'source_changed' in str(a.query('2025年华东晨光额')['memory'])
    add()
    with sqlite3.connect(a.engine.database_path) as c:c.execute('ALTER TABLE sales_orders ADD COLUMN marker TEXT')
    assert 'schema_changed' in str(a.query('2025年华东晨光额')['memory'])


def test_conflict_and_foreign_scope(setup):
    a,m,add=setup
    add(field='sales_amount',ident='other')
    assert 'conflict' in str(a.query('2025年华东晨光额')['memory'])
    add(term='隔离额',ident='foreign',scope=TrustedScope('foreign','other',('db','terms')))
    r=a.query('2025年华东隔离额',session_id='foreign:other')
    assert not r['memory']['selected'] and 'foreign' not in str(r['memory'].get('bindings',[]))


def test_store_error_safe_fallback_and_unrelated_zero_consumption(setup,monkeypatch):
    a,m,_=setup
    r=a.query('2025年华东销售额')
    assert value(r)==300 and r['memory']['selected']==[]
    def fail(*args):raise sqlite3.OperationalError('private detail')
    monkeypatch.setattr(m.core.store,'scan',fail)
    r=a.query('2025年华东销售额')
    assert value(r)==300 and r['memory']['degraded'] and 'private detail' not in str(r)


def test_concurrency_no_shared_binding_or_alias_mutation(setup):
    a,m,_=setup
    # Warm linker caches, then check rule identity/content rather than cache counters.
    with a.engine._connect() as db:rules_before=a.engine.planner.linker.rules_for(a.engine.introspector.introspect(db,include_row_count=False))
    qs=['2025年华东晨光额','2025年华东银杉额','2024年华南晨光额','2025年华东销售额']*4
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda x:a.query(x[1],session_id='parallel-'+str(x[0])),enumerate(qs)))
    assert [value(r) for r in results]==[120,100,250,300]*4
    assert all(r['memory']['recall_count']==1 for r in results)
    with a.engine._connect() as db:rules_after=a.engine.planner.linker.rules_for(a.engine.introspector.introspect(db,include_row_count=False))
    assert rules_before==rules_after


def test_memory_does_not_rewrite_unsafe_request(setup):
    a,m,_=setup
    r=a.query('DELETE FROM sales_orders; 2025年华东晨光额')
    assert not r['memory']['consumed'] and not r['result'].get('sql')
    with sqlite3.connect(a.engine.database_path) as c:assert c.execute('SELECT COUNT(*) FROM sales_orders').fetchone()[0]==4


@pytest.mark.parametrize('response,error,category',[
    ({'status':'clarification','result':{}},None,'clarification'),
    ({'status':'insufficient_evidence','result':{}},None,'clarification'),
    ({'status':'clarification','result':{'clarification_code':'read_only_query_required'}},None,'sql_safety_rejected'),
    ({'status':'clarification','result':{'clarification_code':'memory_source_changed'}},None,'source_conflict'),
    ({'status':'clarification','result':{'clarification_code':'model_plan_failed'}},None,'model_failure'),
    (None,sqlite3.OperationalError('nope'),'tool_failure')])
def test_observe_categories(response,error,category):
    assert classify_outcome(response,error)==category


def test_history_binding_cannot_outlive_source_or_store(setup,monkeypatch):
    a,m,_=setup
    assert value(a.query('2025年华东晨光额',session_id='history'))==120
    assert value(a.query('那华南呢',session_id='history'))==20
    for _ in range(9):a.query('那华南呢',session_id='history')
    assert all(t.state.get('memory_bindings') for t in a.conversations.context('history'))
    a.knowledge.ingest('撤回晨光额'.encode(),document_id='terms',title='业务确认卡',modality='txt',filename='terms.txt')
    r=a.query('那2024年呢',session_id='history')
    assert r['status']=='clarification' and not r['result'].get('sql')
    assert r['memory']['observe']['category']=='source_conflict'
    def fail(*args):raise sqlite3.OperationalError('unavailable')
    monkeypatch.setattr(m.core.store,'scan',fail)
    r=a.query('那2024年呢',session_id='history')
    assert r['status']=='clarification' and r['memory']['degraded']


@pytest.mark.parametrize('unsafe_model',[False,True])
def test_model_sql_receives_binding_and_existing_validator_runs(setup,unsafe_model):
    a,m,_=setup;seen=[]
    def model(q,tables):
        seen.append(q)
        return {'version':1,'table':'sales_orders','metric':{'table':'sales_orders','column':'cost_amount','function':'SUM','label':'销售成本'},
            'dimensions':[],'filters':[{'table':'sales_orders','column':'order_date','operator':'RANGE','value':['2025-01-01','2026-01-01']},
                {'table':'sales_orders','column':'region','operator':'=','value':'华东'}],
            'analysis_mode':'aggregate','limit':100,'confidence':.95,'rewritten_question':q,
            **({'sql':'DELETE FROM sales_orders','metric':{'table':'sales_orders','column':'cost_amount; DELETE FROM sales_orders','function':'SUM'}} if unsafe_model else {})}
    a.engine.model_plan_provider=model
    r=a.query('2025年华东晨光额')
    assert value(r)==120 and r['memory']['consumed']==['cost']
    assert seen and all('销售成本' in q and '晨光额' not in q for q in seen)
    assert r['result']['plan']['planner_source']==('rules_fallback' if unsafe_model else 'model_validated')
    with sqlite3.connect(a.engine.database_path) as db:assert db.execute('SELECT COUNT(*) FROM sales_orders').fetchone()[0]==4
