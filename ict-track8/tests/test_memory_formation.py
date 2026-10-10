import json
import os
import sqlite3
from dataclasses import asdict
import pytest
from .test_memory_adapter import setup,value
from backend.memory.core import MemoryStore, MemoryCore, encoded
from backend.memory.extraction import digest
from backend.memory.formation import MemoryFormation,LocalReviewContext


@pytest.fixture
def flow(setup,tmp_path):
    a,m,_=setup;m.formation_enabled=True
    body={'term':'云杉额','definition':'线上销售额合计','binding':{'table':'sales_orders','column':'sales_amount','function':'SUM','filters':[{'column':'channel','value':'线上'}]},
        'valid_from':'2026-10-01T00:00:00+08:00','valid_to':'2026-11-01T00:00:00+08:00'}
    def source(change=None):
        c=body| (change or {})
        a.knowledge.ingest(('云杉额的定义\n业务口径：'+json.dumps(c,ensure_ascii=False)).encode(),document_id='terms',title='云杉额定义',modality='txt',filename='terms.txt')
    source();form=MemoryFormation(m)
    config=tmp_path/'admin.json';config.write_text(json.dumps({'scope':asdict(m.core.scope),'memory_db':str(m.core.store.path)}));config.chmod(0o600)
    return a,m,form,LocalReviewContext.from_config(config),source


def capture(flow):
    a,m,f,ctx,source=flow
    response=a.query('云杉额是什么',session_id='formation-A')
    assert response['memory']['formation']['candidate_ids']
    return f.candidates()[0]


def promote(flow,ident='approve-1'):
    c=capture(flow);a,m,f,ctx,_=flow
    assert f.validate(c['candidate_id'],c['digest'])['valid']
    receipt=f.review(c['candidate_id'],c['digest'],context=ctx,request_id=ident,decision='confirm',reason='local fixture review')
    return c,receipt


def test_real_agent_capture_then_review_reopen_new_session(flow):
    a,m,f,ctx,_=flow;c=capture(flow)
    assert c['verification_state']=='candidate'
    assert not a.query('2025年华东云杉额',session_id='before')['memory']['consumed']
    assert f.validate(c['candidate_id'],c['digest'])['valid']
    receipt=f.review(c['candidate_id'],c['digest'],context=ctx,request_id='approval',decision='confirm',reason='checked explicit source')
    assert receipt==f.review(c['candidate_id'],c['digest'],context=ctx,request_id='approval',decision='confirm',reason='checked explicit source')
    m.core.store=MemoryStore(m.core.store.path)
    result=a.query('2024年华南云杉额',session_id='independent-B')
    assert result['context_turns']==0 and value(result)==500 and result['memory']['consumed']==[receipt['memory_id']]


def test_duplicate_events_and_untrusted_admin(flow):
    c=capture(flow);a,m,f,ctx,_=flow;capture(flow)
    assert len(f.candidates())==1
    with m.core.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM memory_candidate_events').fetchone()[0]==2
    with pytest.raises(PermissionError):f.review(c['candidate_id'],c['digest'],context='operator=admin',request_id='x',decision='confirm',reason='I confirm')
    with pytest.raises(ValueError,match='validation required'):f.review(c['candidate_id'],c['digest'],context=ctx,request_id='x',decision='confirm',reason='checked')


def test_changed_source_stale_digest_and_re_review(flow):
    a,m,f,ctx,source=flow;c=capture(flow);assert f.validate(c['candidate_id'],c['digest'])['valid']
    source({'definition':'线上销售额新版本'})
    with pytest.raises(ValueError,match='promotion refused'):f.review(c['candidate_id'],c['digest'],context=ctx,request_id='stale',decision='confirm',reason='stale approval')
    a.query('云杉额是什么',session_id='formation-new')
    new=next(x for x in f.candidates() if x['candidate_id']!=c['candidate_id'])
    assert f.validate(new['candidate_id'],new['digest'])['valid']
    with pytest.raises(ValueError,match='digest'):f.review(new['candidate_id'],c['digest'],context=ctx,request_id='old-digest',decision='confirm',reason='checked')
    assert f.review(new['candidate_id'],new['digest'],context=ctx,request_id='fresh',decision='confirm',reason='new review')['memory_id']


def test_revoke_history_and_audit_preserved(flow):
    c,r=promote(flow);a,m,f,ctx,_=flow
    assert value(a.query('2025年华东云杉额',session_id='old-history'))==100
    memory=next(x for x in m.core.store.scan(m.core.scope)[0] if x.memory_id==r['memory_id'])
    f.revoke(memory.memory_id,digest(asdict(memory)),context=ctx,request_id='revoke',reason='business withdrew')
    result=a.query('那华南呢',session_id='old-history')
    assert result['status']=='clarification' and not result['result'].get('sql')
    with m.core.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM memory_history WHERE memory_id=?',(memory.memory_id,)).fetchone()[0]>=2


def test_missing_binding_and_locator_tamper_are_not_validated(flow):
    a,m,f,ctx,source=flow;source({'binding':None});c=capture(flow)
    assert 'missing_explicit_binding' in f.validate(c['candidate_id'],c['digest'])['reasons']
    source();a.query('云杉额是什么',session_id='good');c=next(x for x in f.candidates() if x['binding'])
    with m.core.store.connect() as db:
        raw=json.loads(db.execute('SELECT payload FROM memory_candidates WHERE candidate_id=?',(c['candidate_id'],)).fetchone()[0]);raw['evidence']['locator']['line']=1
        db.execute('UPDATE memory_candidates SET payload=? WHERE candidate_id=?',(encoded(raw),c['candidate_id']))
    with pytest.raises(ValueError,match='digest'):f.validate(c['candidate_id'],c['digest'])


def test_v1_migration_retains_memory_and_event(setup,tmp_path):
    a,m,_=setup;record=m.core.store.scan(m.core.scope)[0][0]
    path=tmp_path/'legacy.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE memory_metadata(version INTEGER NOT NULL);INSERT INTO memory_metadata VALUES(1);CREATE TABLE memories(scope TEXT,memory_id TEXT,payload TEXT,PRIMARY KEY(scope,memory_id));CREATE TABLE memory_events(scope TEXT,event_id TEXT,payload TEXT,payload_sha256 TEXT,PRIMARY KEY(scope,event_id));')
        db.execute('INSERT INTO memories VALUES(?,?,?)',(record.scope.key,record.memory_id,encoded(asdict(record))))
        db.execute('INSERT INTO memory_events VALUES(?,?,?,?)',(record.scope.key,'old','{}','legacy'))
    reopened=MemoryStore(path)
    assert reopened.scan(record.scope)[0]==[record] and reopened.events()==[{}]
    with reopened.connect() as db:assert db.execute('SELECT version FROM memory_metadata').fetchone()[0]==2


def test_superseded_version_and_new_definition_preserve_old(flow):
    c,old=promote(flow);a,m,f,ctx,source=flow
    source({'binding':{'table':'sales_orders','column':'cost_amount','function':'SUM','filters':[]},'definition':'销售成本合计'})
    a.query('云杉额是什么',session_id='revision')
    new=next(x for x in f.candidates() if x['candidate_id']!=c['candidate_id'])
    assert f.validate(new['candidate_id'],new['digest'])['valid']
    receipt=f.review(new['candidate_id'],new['digest'],context=ctx,request_id='revision-approved',decision='confirm',reason='business definition changed',supersedes=old['memory_id'])
    memories={x.memory_id:x for x in m.core.store.scan(m.core.scope)[0]}
    assert memories[old['memory_id']].verification_state=='superseded'
    assert memories[receipt['memory_id']].verification_state=='confirmed'
    assert value(a.query('2025年华东云杉额',session_id='revised-target'))==120


def test_schema_change_and_event_without_source_cannot_promote(flow):
    c=capture(flow);a,m,f,ctx,_=flow
    with sqlite3.connect(a.engine.database_path) as db:db.execute('ALTER TABLE sales_orders ADD COLUMN changed TEXT')
    assert 'schema_changed' in f.validate(c['candidate_id'],c['digest'])['reasons']
    assert f.capture({'route':'sql','status':'ok','result':{'sql':'SELECT 1','rows':[{'x':1}]}},'sql-only')==[]


def test_conflicting_valid_candidates_rejected(flow):
    a,m,f,ctx,source=flow
    contract={'term':'云杉额','definition':'销售成本合计','binding':{'table':'sales_orders','column':'cost_amount','function':'SUM','filters':[]},'valid_from':'2026-10-01T00:00:00+08:00','valid_to':'2026-11-01T00:00:00+08:00'}
    original=a.knowledge.original('terms')[0].read_text()
    a.knowledge.ingest((original+'\n业务口径：'+json.dumps(contract,ensure_ascii=False)).encode(),document_id='terms',title='云杉额定义',modality='txt',filename='terms.txt')
    capture(flow)
    assert len(f.candidates())==2
    for c in f.candidates():assert 'candidate_conflict' in f.validate(c['candidate_id'],c['digest'])['reasons']
