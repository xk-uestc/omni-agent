"""Source integrity and recoverable storage failures cannot produce final answers."""
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


@pytest.fixture
def setup(tmp_path,monkeypatch):
    store=KnowledgeStore(tmp_path/'knowledge')
    raw='标准硬件保修期为12个月。\n客单价 = 销售额 / 订单数'.encode()
    store.ingest(raw,document_id='manual',title='硬件保修',modality='txt',filename='manual.txt')
    engine=Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite'))
    sessions=ConversationStore(storage_path=tmp_path/'sessions.sqlite')
    monkeypatch.setattr(app_module,'knowledge_store',store)
    monkeypatch.setattr(app_module,'engine',engine)
    monkeypatch.setattr(app_module,'conversation_store',sessions)
    monkeypatch.setattr(app_module,'generation_client',None)
    return store,engine,sessions,raw,TestClient(app_module.app)


@pytest.mark.parametrize('action',['tamper','missing'])
@pytest.mark.parametrize('path,payload',[
    ('/api/v1/knowledge/query',{'question':'标准硬件保修期'}),
    ('/api/v1/omni/query',{'question':'标准硬件保修期','session_id':'integrity'}),
])
def test_changed_original_refuses_cached_answer_and_recovers(setup,action,path,payload):
    store,engine,sessions,raw,client=setup
    source,_=store.original('manual')
    assert client.post(path,json=payload).status_code==200
    original_history=sessions.context('integrity')
    if action=='tamper':source.write_bytes(b'tampered')
    else:source.unlink()
    failed=client.post(path,json=payload)
    assert failed.status_code==409
    assert failed.json()['detail']['code']=='evidence_integrity_failed'
    assert '12个月' not in failed.text
    assert sessions.context('integrity')==original_history
    source.write_bytes(raw)
    restored=client.post(path,json=payload)
    assert restored.status_code==200 and '12个月' in restored.text


def test_cached_formula_stops_chain_and_preserves_dependency_edges(setup):
    store,engine,sessions,raw,client=setup
    source,_=store.original('manual')
    source.write_bytes(b'changed')
    tasks=[{'id':'formula','tool':'document_formula','args':{'document_id':'manual','label':'客单价'}},
           {'id':'later','tool':'search','args':{'query':{'ref':'formula','path':['label']}}}]
    answer=client.post('/api/v1/fusion/execute',json={'tasks':tasks}).json()
    assert answer['status']=='incomplete' and answer['error_code']=='evidence_integrity_failed'
    assert answer['skipped_tasks']==['later'] and answer['results']=={}
    assert answer['edges']==[{'from':'formula','to':'later'}]


@pytest.mark.parametrize('failure',['missing','locked','corrupt'])
def test_sql_resource_failure_is_sanitized_and_stops_dependants(setup,failure):
    store,engine,sessions,raw,client=setup
    tasks=[{'id':'sql','tool':'sql','args':{'question':'2025年销售额'}},
           {'id':'later','tool':'search','args':{'query':{'ref':'sql','path':['rows',0,'销售额']}}}]
    db=engine.database_path
    baseline=db.read_bytes()
    locked=None
    try:
        if failure=='missing':db.unlink()
        elif failure=='corrupt':db.write_bytes(b'invalid sqlite file')
        else:
            locked=sqlite3.connect(db);locked.execute('BEGIN EXCLUSIVE')
        answer=client.post('/api/v1/fusion/execute',json={'tasks':tasks}).json()
        assert answer['status']=='incomplete' and answer['error_code']=='storage_unavailable'
        assert answer['failed_task']=='sql' and answer['skipped_tasks']==['later']
        assert not answer['results'] and str(db) not in str(answer)
    finally:
        if locked:locked.rollback();locked.close()
        else:db.write_bytes(baseline)
    restored=client.post('/api/v1/fusion/execute',json={'tasks':tasks[:1]}).json()
    assert restored['status']=='ok' and restored['results']['sql']['rows'][0]['销售额']==70563


def test_session_lock_does_not_discard_history_or_change_identity(setup):
    store,engine,sessions,raw,client=setup
    query={'question':'2025年华东销售额','session_id':'session'}
    assert client.post('/api/v1/omni/query',json=query).status_code==200
    before=sessions.context('session')
    with closing(sqlite3.connect(sessions.storage_path)) as locked:
        locked.execute('BEGIN EXCLUSIVE')
        response=client.post('/api/v1/omni/query',json={'question':'那华南呢','session_id':'session'})
        assert response.status_code==503 and response.headers['Retry-After']=='2'
        assert response.json()['detail']['code']=='storage_unavailable'
        assert str(sessions.storage_path) not in response.text
        locked.rollback()
    assert sessions.context('session')==before
    result=client.post('/api/v1/omni/query',json={'question':'那华南呢','session_id':'session'}).json()
    assert result['result']['rows'][0]['销售额']==22992 and result['context_turns']==1


@pytest.mark.parametrize('raise_inside',[False,True])
def test_sql_connection_closed_without_waiting_for_gc(setup,raise_inside):
    engine=setup[1]
    try:
        with engine._connect() as connection:
            assert connection.execute('SELECT 1').fetchone()==(1,)
            if raise_inside:raise ValueError('test scope exit')
    except ValueError:
        assert raise_inside
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connection.execute('SELECT 1')


def test_malformed_replacement_preserves_previous_original_and_chunks(setup):
    import base64
    store,engine,sessions,raw,client=setup
    before=store.document('manual')
    response=client.post('/api/v1/knowledge/ingest',json={'document_id':'manual','title':'bad replacement',
        'modality':'pdf','filename':'bad.pdf','file_base64':base64.b64encode(b'not a pdf').decode()})
    assert response.status_code==400
    assert store.document('manual')==before
    assert store.original('manual')[0].read_bytes()==raw


def test_failed_sql_snapshot_can_refresh_after_database_change(setup):
    engine=setup[1]
    assert engine.answer('2025年华东销售额').rows[0]['销售额']==29584
    with closing(sqlite3.connect(engine.database_path)) as connection:
        connection.execute("UPDATE sales_orders SET sales_amount=sales_amount+100 WHERE order_id='SO-005'")
        connection.commit()
    # The query and value/schema caches must not pin the prior DB transaction.
    with closing(sqlite3.connect(engine.database_path)) as connection:
        expected=connection.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE region='华东' AND substr(order_date,1,4)='2025'").fetchone()[0]
    assert engine.answer('2025年华东销售额').rows[0]['销售额']==expected
