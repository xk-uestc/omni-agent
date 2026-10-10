"""M1-B1 contracts, written before the implementation. No production fixtures."""
from dataclasses import replace
import sqlite3
import pytest

from backend.memory.core import MemoryCore, MemoryStore, MemoryRecord, TrustedScope, RecallContext, Budget

SCOPE = TrustedScope('deployment-test', 'project-test', ('db', 'terms'))


def record(**changes):
    base = dict(memory_id='term-1', memory_type='business_semantics', term='晨光值', content='确认的业务口径',
        binding={'table': 'orders', 'column': 'revenue', 'function': 'SUM', 'filters': []}, scope=SCOPE,
        provenance={'authority': 'server_fixture', 'confirmation_id': 'confirm-1', 'evidence_id': 'line-1', 'verification_id':'independent-1'},
        source_version={'schema': 'schema-v1', 'sources': {'db': 'db-v1', 'terms': 'doc-v1'}},
        valid_from='2026-10-01T00:00:00+08:00', valid_to='2026-11-01T00:00:00+08:00',
        verification_state='confirmed', created_at='2026-10-01T00:00:00+08:00',
        updated_at='2026-10-01T00:00:00+08:00', task_trace_id=None)
    return MemoryRecord(**(base | changes))


def context(**changes):
    base = dict(question='2025年华东晨光值', now='2026-10-10T12:00:00+08:00', schema_version='schema-v1',
        sources={'db': 'db-v1', 'terms': 'doc-v1'}, metrics=frozenset({('orders','revenue','SUM'), ('orders','cost','SUM')}),
        filter_values=frozenset({('orders','channel','"线上"')}), protected_spans=())
    return RecallContext(**(base | changes))


def core(tmp_path, records=None, enabled=True, scope=SCOPE):
    store = MemoryStore(tmp_path/'memory.sqlite')
    for r in records or [record()]: store.put(r)
    return MemoryCore(store, scope=scope, enabled=enabled)


def test_persistent_reopen_and_two_independent_contexts(tmp_path):
    c=core(tmp_path)
    for query in ['2025年华东晨光值','2024年华南晨光值']:
        reopened=MemoryCore(MemoryStore(tmp_path/'memory.sqlite'),scope=SCOPE,enabled=True)
        assert [r.memory_id for r in reopened.recall(context(question=query),SCOPE,Budget()).selected]==['term-1']


@pytest.mark.parametrize('change,reason', [
    ({'verification_state':'candidate'},'unverified'),
    ({'verification_state':'revoked'},'revoked'),
    ({'valid_to':'2026-10-09T00:00:00+08:00'},'expired'),
    ({'valid_from':'2026-10-11T00:00:00+08:00'},'not_yet_valid'),
    ({'source_version':{'schema':'schema-v0','sources':{'db':'db-v1','terms':'doc-v1'}}},'schema_changed'),
    ({'source_version':{'schema':'schema-v1','sources':{'db':'db-v1','terms':'doc-v0'}}},'source_changed'),
    ({'binding':{'table':'orders','column':'absent','function':'SUM','filters':[]}},'invalid_metric'),
    ({'binding':{'table':'orders','column':'revenue','function':'DROP','filters':[]}},'invalid_metric'),
    ({'binding':{'table':'orders','column':'revenue','function':'SUM','filters':[{'column':'channel','value':'不存在'}]}},'invalid_filter'),
    ({'provenance':{}},'missing_confirmation'),
])
def test_invalid_candidates_rejected_before_selection(tmp_path,change,reason):
    r=core(tmp_path,[record(**change)]).recall(context(),SCOPE,Budget())
    assert not r.selected and reason in [v['reason'] for v in r.decisions]


def test_scope_filtered_without_disclosing_foreign_record(tmp_path):
    c=core(tmp_path,[record(scope=TrustedScope('other','project-test',('db','terms')))])
    r=c.recall(context(),SCOPE,Budget())
    assert not r.selected
    assert {'reason':'foreign_scope','count':1} in r.decisions
    assert 'term-1' not in str(r.decisions)
    assert not c.recall(context(),TrustedScope('fake','fake',('db',)),Budget()).selected


def test_conflict_checked_before_top_one_budget(tmp_path):
    other=record(memory_id='term-2',binding={'table':'orders','column':'cost','function':'SUM','filters':[]})
    r=core(tmp_path,[record(),other]).recall(context(),SCOPE,Budget(max_items=1))
    assert not r.selected and sum(d['reason']=='conflict' for d in r.decisions)==2


def test_unrelated_and_quoted_terms_do_not_recall(tmp_path):
    c=core(tmp_path)
    assert not c.recall(context(question='2025年华东销量'),SCOPE,Budget()).selected
    assert not c.recall(context(question='查询“晨光值”产品销量',protected_spans=((2,7),)),SCOPE,Budget()).selected


def test_disabled_no_store_access_and_missing_scope_fails_closed(tmp_path,monkeypatch):
    c=core(tmp_path,enabled=False)
    monkeypatch.setattr(c.store,'scan',lambda *_: pytest.fail('disabled store touched'))
    assert not c.recall(context(),SCOPE,Budget()).selected
    with pytest.raises(ValueError,match='scope'): MemoryCore(c.store,scope=None,enabled=True)


def test_store_failure_falls_back_and_events_idempotent(tmp_path,monkeypatch):
    c=core(tmp_path)
    def broken(*_): raise sqlite3.OperationalError('sensitive failure detail')
    monkeypatch.setattr(c.store,'scan',broken)
    r=c.recall(context(),SCOPE,Budget())
    assert r.degraded and not r.selected and 'sensitive' not in str(r)
    event={'event_id':'trace-1','question_sha256':'qhash','category':'verified_result','selected':['term-1']}
    assert c.observe(event,{'execution_verified':True})['stored']
    assert not c.observe(event,{'execution_verified':True})['stored']
    assert len(c.store.events())==1
    # observe never promotes or inserts a business memory.
    assert len(MemoryStore(tmp_path/'memory.sqlite').scan(SCOPE)[0])==1


def test_server_configuration_required_and_default_off(tmp_path):
    assert not MemoryCore.from_env({}).enabled
    with pytest.raises(ValueError,match='scope'):
        MemoryCore.from_env({'ICT8_MEMORY_ENABLED':'1','session_id':'admin'})
    env={'ICT8_MEMORY_ENABLED':'1','ICT8_MEMORY_DEPLOYMENT':'d','ICT8_MEMORY_PROJECT':'p',
         'ICT8_MEMORY_SOURCES':'db,terms','ICT8_MEMORY_DATABASE_SOURCE':'db',
         'ICT8_MEMORY_DB_PATH':str(tmp_path/'configured.sqlite')}
    assert MemoryCore.from_env(env).enabled
    env['ICT8_MEMORY_DB_PATH']=str(tmp_path)  # directory cannot be SQLite
    failed=MemoryCore.from_env(env)
    assert failed.recall(context(),failed.scope,Budget()).degraded
