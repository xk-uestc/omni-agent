"""Trusted executed history, bounded model rewriting and independent refusal."""
from datetime import date
from types import SimpleNamespace

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationTurn
from backend.sql_history_scope import resolve_sql_followup_scope, saved_sql_context
from backend.responses_client import GenerationError


class Client:
    def __init__(self,scope,approved=True,error=None):
        self.scope,self.approved,self.error,self.calls=scope,approved,error,[]
    def generate(self,instructions,context,schema,**kwargs):
        self.calls.append(kwargs['name'])
        if self.error:raise self.error
        if kwargs['name']=='sql_followup_rewrite':return {'question':self.scope,'clarification':None}
        return {k:self.approved for k in schema['properties']}


@pytest.fixture
def context(tmp_path):
    engine=Nl2SqlEngine(initialize_database(tmp_path/'demo.sqlite'))
    base='2025年华东销售额'
    result=engine.answer(base).to_dict()
    state={'route':'sql','pending_question':None,'clarification_code':None,
           'metrics':[],'filters':[],'dimensions':[],
           'executed_sql_context':saved_sql_context(base,result)}
    return engine,ConversationTurn(base,base,0,state)


def attach(engine,client):
    engine.model_plan_provider=SimpleNamespace(client=client,supports_complex_queries=True,reference_date=date(2026,1,1))


def test_reviewed_confirmation_tail_keeps_full_success_scope(context):
    engine,turn=context;client=Client('2024年华东销售额');attach(engine,client)
    scope,audit=resolve_sql_followup_scope('那2024年呢，仍按sales_amount合计？',[turn],engine)
    assert scope=='2024年华东销售额'
    assert audit['mode']=='model_reviewed_sql_followup'
    assert 'not_formal' in audit['verification']
    assert client.calls==['sql_followup_rewrite','sql_followup_independent_review']


def test_reviewer_refusal_never_becomes_success_context(context):
    engine,turn=context;client=Client('销售额',approved=False);attach(engine,client)
    scope,audit=resolve_sql_followup_scope('那2024年呢，仍按sales_amount合计？',[turn],engine)
    assert scope.startswith('那2024年') and audit.get('requires_clarification')


@pytest.mark.parametrize('change',['failed','tampered','different_scope','missing','document'])
def test_untrusted_history_never_calls_model(context,change):
    engine,turn=context;client=Client('2024年华东销售额');attach(engine,client)
    if change=='failed':turn.state['pending_question']='pending'
    if change=='tampered':turn.state['executed_sql_context']['payload']['sql']='SELECT 999'
    if change=='different_scope':turn=ConversationTurn(turn.question,'2025年华南销售额',0,turn.state)
    if change=='missing':turn.state.pop('executed_sql_context')
    if change=='document':turn.state['route']='document'
    resolve_sql_followup_scope('那2024年呢，仍按sales_amount合计？',[turn],engine)
    assert not client.calls


@pytest.mark.parametrize('question',['那排除华东呢','换个主题，2024年华南销售额','那只要大于100的呢'])
def test_complex_changes_and_topic_reset_do_not_rewrite(context,question):
    engine,turn=context;client=Client('销售额');attach(engine,client)
    resolve_sql_followup_scope(question,[turn],engine)
    assert not client.calls


@pytest.mark.parametrize('status',[401,403])
def test_auth_failure_stops_followup(context,status):
    engine,turn=context;client=Client('',error=GenerationError('auth',status=status));attach(engine,client)
    with pytest.raises(GenerationError):
        resolve_sql_followup_scope('那2024年呢，仍按sales_amount合计？',[turn],engine)
    assert len(client.calls)==1


def test_only_successful_executed_results_are_saved():
    assert saved_sql_context('q',{'status':'clarification','sql':None}) is None
    assert saved_sql_context('q',{'status':'ok','sql':'SELECT 1','result_state':'partial_rows'}) is None


def test_complete_new_scope_does_not_call_followup_model(context):
    engine,turn=context;client=Client('wrong');attach(engine,client)
    question='仍是2024年华南销售额，按地区分组查看合计'
    scope,audit=resolve_sql_followup_scope(question,[turn],engine)
    assert scope == question and audit['reason'] == 'self_contained_sql'
    assert not client.calls


def test_changed_database_cannot_authorize_saved_followup(context):
    import sqlite3
    engine,turn=context;client=Client('2024年华东销售额');attach(engine,client)
    with sqlite3.connect(engine.database_path) as connection:
        connection.execute('CREATE TABLE new_source(id INTEGER)')
    question='那2024年呢，仍按sales_amount合计？'
    scope,audit=resolve_sql_followup_scope(question,[turn],engine)
    assert scope == question and audit['requires_clarification']
    assert audit['reason'] == 'sql_history_source_revision_changed'
    assert not client.calls


def test_named_followup_of_unrepresented_success_gets_independent_review(context,monkeypatch):
    from backend.nl2sql.models import QueryPlan
    engine,turn=context
    client=Client('2024年华东销售额');attach(engine,client)
    # The actual rule planner cannot represent some successful relational SQL.
    monkeypatch.setattr(engine,'extract_required_intent',lambda *a,**kw:
        QueryPlan(clarification='规则槽位不能表示该查询',clarification_code='unresolved_terms'))
    scope,audit=resolve_sql_followup_scope('那改成2024年呢，仍按sales_amount合计？',[turn],engine)
    assert scope=='2024年华东销售额'
    assert audit['mode']=='model_reviewed_sql_followup'
    assert client.calls==['sql_followup_rewrite','sql_followup_independent_review']


def test_unrepresented_success_does_not_bypass_reviewer_or_source(context,monkeypatch):
    from backend.nl2sql.models import QueryPlan
    engine,turn=context
    monkeypatch.setattr(engine,'extract_required_intent',lambda *a,**kw:
        QueryPlan(clarification='unrepresented',clarification_code='unresolved_terms'))
    client=Client('2024年华东销售额',approved=False);attach(engine,client)
    question='那改成2024年呢，仍按sales_amount合计？'
    scope,audit=resolve_sql_followup_scope(question,[turn],engine)
    assert audit.get('requires_clarification') and audit['mode']=='independent'
    assert scope==question
    client.calls.clear();turn.state['executed_sql_context']['sha256']='0'*64
    scope,audit=resolve_sql_followup_scope(question,[turn],engine)
    assert audit.get('requires_clarification') and not client.calls
