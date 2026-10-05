"""Recovery must preserve the actual pending task, not execute a default."""
import pytest
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


def test_multiple_invalid_replies_keep_task_then_recover_after_sqlite_reload(agent):
    session='recover-pending'
    initial=agent.query('销售额趋势',session_id=session)
    for index,reply in enumerate(['2025-13','不知道','什么意思'],1):
        retained=agent.query(reply,session_id=session)
        assert retained['status']=='clarification'
        assert retained['effective_question']==initial['effective_question']
        assert retained['result']['clarification_options']==initial['result']['clarification_options']
        assert retained['context_resolution']['retry_count']==index
        assert retained['result']['sql'] is None
        assert 'executed_sql_context' not in retained['state']
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    pending=agent.query('看2025年吧',session_id=session)
    assert pending['result']['clarification_code']=='missing_time_grain'
    retained=agent.query('按月或者按年',session_id=session)
    assert retained['effective_question']==pending['effective_question']
    assert retained['context_resolution']['reason']=='multiple_grains_not_confirmed'
    final=agent.query('按月统计',session_id=session)
    assert final['status']=='ok'
    assert final['result']['rows']==agent.engine.answer('2025年按月销售额趋势').to_dict()['rows']


def test_retry_does_not_call_sql_execution_or_model(agent,monkeypatch):
    agent.query('2025年华东销售额趋势',session_id='no-execute')
    def forbidden(*args,**kwargs):
        raise AssertionError('retry must not execute SQL or ask a model for defaults')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    agent.client=type('ForbiddenModel',(),{'generate':forbidden})()
    result=agent.query('随便',session_id='no-execute')
    assert result['status']=='clarification'
    assert result['effective_question']=='2025年华东销售额趋势'


@pytest.mark.parametrize('reply',['看2025-13吧','0000年','选择0000-03即可','2025年0月'])
def test_invalid_time_wrappers_retain_the_same_pending_scope(agent,reply):
    agent.query('销售额趋势',session_id='invalid-wrapped')
    result=agent.query(reply,session_id='invalid-wrapped')
    assert result['effective_question']=='销售额趋势'
    assert result['context_resolution']['reason']=='invalid_time_reply'
    assert result['result']['sql'] is None


def test_independent_query_and_session_reset_exit_retry(agent):
    agent.query('销售额趋势',session_id='reset-pending')
    agent.query('不知道',session_id='reset-pending')
    result=agent.query('2024年华南订单数',session_id='reset-pending')
    assert result['status']=='ok'
    assert result['result']['rows']==agent.engine.answer('2024年华南订单数').to_dict()['rows']
    result=agent.query('不知道',session_id='reset-pending',reset_context=True)
    assert result['context_resolution'].get('mode')!='pending_sql_clarification_retained'


def test_retry_cannot_turn_into_successful_history_reference(agent):
    agent.query('销售额趋势',session_id='no-reference')
    retained=agent.query('不确定',session_id='no-reference')
    result=agent.query(f"回到SQL查询编号{retained['query_reference_id']}，那华南呢",session_id='no-reference')
    assert result['status']=='clarification'
    assert not result['result'].get('sql')


def test_foreign_session_never_inherits_pending_task(agent):
    agent.query('销售额趋势',session_id='original')
    result=agent.query('不知道',session_id='other')
    assert result['context_resolution'].get('mode')!='pending_sql_clarification_retained'


def test_retry_recomputes_options_and_rejects_changed_clarification(agent,monkeypatch):
    from backend.clarification_continuation import ClarificationContinuationAgent
    from types import SimpleNamespace
    previous=SimpleNamespace(effective_question='按国家统计金额',state={
        'route':'sql','pending_question':'按国家统计金额','clarification_code':'ambiguous_dimension'})
    plan=SimpleNamespace(clarification='请选择',clarification_code='ambiguous_dimension',
        clarification_options=[{'label':'国家','value':'Customer.Country'},{'label':'国家','value':'Invoice.BillingCountry'}])
    monkeypatch.setattr(agent.engine,'extract_required_intent',lambda _:plan)
    decision=ClarificationContinuationAgent(agent.engine).run('选择国家',[previous])
    assert decision.reason=='duplicate_option_label'
    plan.clarification_code='missing_metric'
    assert ClarificationContinuationAgent(agent.engine).run('不知道',[previous]) is None


@pytest.mark.parametrize('reply',['什么意思','怎么选','选项有什么区别','解释一下吧'])
def test_help_explains_actual_missing_condition_without_losing_filters(agent,reply,monkeypatch):
    initial=agent.query('2025年华东销售额趋势',session_id='help-grain')
    def forbidden(*args,**kwargs):
        raise AssertionError('explanation must not execute or invoke model')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    result=agent.query(reply,session_id='help-grain')
    assert result['status']=='clarification'
    assert result['context_resolution']['reason']=='clarification_help_requested'
    assert result['effective_question']==initial['effective_question']
    assert result['state']['filters']==initial['state']['filters']
    assert result['result']['sql'] is None
    assert '逐月列出' in result['result']['clarification']
    assert '逐年列出' in result['result']['clarification']


@pytest.mark.parametrize('reply',['按季度','按周统计','按日汇总','选择按半年趋势吧'])
def test_unoffered_grain_retains_scope_then_recovers_after_reload(agent,reply):
    scope='2025年华东销售额趋势'
    initial=agent.query(scope,session_id='unsupported-grain')
    retained=agent.query(reply,session_id='unsupported-grain')
    assert retained['effective_question']==scope
    assert retained['context_resolution']['reason']=='unsupported_time_grain'
    assert retained['result']['sql'] is None
    assert retained['result']['clarification_options']==initial['result']['clarification_options']
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    result=agent.query('按月统计',session_id='unsupported-grain')
    assert result['status']=='ok'
    assert result['result']['rows']==agent.engine.answer(scope+' 按月').to_dict()['rows']


def test_reverse_grain_conflict_is_not_a_new_question(agent):
    agent.query('2025年华南销售额趋势',session_id='reverse-conflict')
    result=agent.query('按年和按月',session_id='reverse-conflict')
    assert result['context_resolution']['reason']=='multiple_grains_not_confirmed'
    assert result['effective_question']=='2025年华南销售额趋势'


def test_complete_query_with_quarter_does_not_enter_short_reply_recovery(agent):
    from backend.clarification_continuation import ClarificationContinuationAgent
    agent.query('2025年华东销售额趋势',session_id='fresh-query')
    history=agent.conversations.context('fresh-query')
    assert ClarificationContinuationAgent(agent.engine).run('2024年华南按季度销售额',history) is None


def test_unavailable_monthly_option_is_not_silently_selected(agent,monkeypatch):
    from backend.clarification_continuation import ClarificationContinuationAgent
    from types import SimpleNamespace
    previous=SimpleNamespace(effective_question='2025年销售额趋势',state={
        'route':'sql','pending_question':'2025年销售额趋势','clarification_code':'missing_time_grain'})
    plan=SimpleNamespace(clarification='请选择',clarification_code='missing_time_grain',
        clarification_options=[{'label':'按年趋势','value':'yearly_trend'}])
    monkeypatch.setattr(agent.engine,'extract_required_intent',lambda _:plan)
    decision=ClarificationContinuationAgent(agent.engine).run('按月统计',[previous])
    assert decision.reason=='unsupported_time_grain'
    assert ClarificationContinuationAgent(agent.engine).run('按年统计',[previous]) is None


@pytest.mark.parametrize('reply',['好的','继续吧','明白了','就这样','确认','没问题'])
def test_acknowledgement_never_confirms_a_missing_condition(agent,reply,monkeypatch):
    scope='2025年华东销售额趋势'
    initial=agent.query(scope,session_id='acknowledgement')
    with monkeypatch.context() as guarded:
        def forbidden(*args,**kwargs):
            raise AssertionError('an acknowledgement must not execute a query')
        guarded.setattr(agent.engine,'answer',forbidden)
        agent.client=type('ForbiddenModel',(),{'generate':forbidden})()
        retained=agent.query(reply,session_id='acknowledgement')
    agent.client=None
    assert retained['context_resolution']['reason']=='condition_not_supplied'
    assert retained['effective_question']==scope
    assert retained['state']['filters']==initial['state']['filters']
    assert retained['result']['clarification_options']==initial['result']['clarification_options']
    assert retained['result']['sql'] is None
    final=agent.query('按月统计',session_id='acknowledgement')
    assert final['result']['rows']==agent.engine.answer(scope+' 按月').to_dict()['rows']


@pytest.mark.parametrize('reply',['这是什么意思？','这些选项是什么意思','请解释一下','能解释一下吗'])
def test_help_names_current_options_and_keeps_pending_scope(agent,reply):
    initial=agent.query('2025年华东销售额趋势',session_id='actual-options')
    result=agent.query(reply,session_id='actual-options')
    assert result['context_resolution']['reason']=='clarification_help_requested'
    assert result['effective_question']==initial['effective_question']
    assert result['result']['sql'] is None
    for option in initial['result']['clarification_options']:
        assert option['label'] in result['result']['clarification']


def test_complete_new_question_with_acknowledgement_is_not_retained(agent):
    from backend.clarification_continuation import ClarificationContinuationAgent
    agent.query('2025年华东销售额趋势',session_id='new-after-ack')
    history=agent.conversations.context('new-after-ack')
    assert ClarificationContinuationAgent(agent.engine).run('好的，查询2024年华南订单数',history) is None
