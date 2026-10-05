"""Explicit unfinished-task selection must not inherit an unrelated query."""
from copy import deepcopy
import pytest
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.pending_task_resume import PendingTaskResumeAgent


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


def test_two_unfinished_tasks_resume_exact_snapshot_after_another_topic(agent):
    a=agent.query('2025年华东销售额趋势',session_id='tasks')
    b=agent.query('2024年华南订单数趋势',session_id='tasks')
    agent.query('2025年华北销售额',session_id='tasks')
    restored=agent.query(f"继续待补查询编号{a['query_reference_id']}",session_id='tasks')
    assert restored['effective_question']==a['effective_question']
    assert restored['result']['sql'] is None
    assert restored['query_reference_id']!=a['query_reference_id']
    answer=agent.query('按月统计',session_id='tasks')
    assert answer['result']['rows']==agent.engine.answer('2025年华东销售额趋势 按月').to_dict()['rows']
    selected=agent.query(f"继续待补查询编号{b['query_reference_id']}，时间改成2025年，按年统计",session_id='tasks')
    assert selected['status']=='ok'
    assert selected['result']['rows']==agent.engine.answer('2025年华南订单数趋势 按年').to_dict()['rows']
    assert selected['context_resolution']['pending_task_reference']['turn_id']==b['query_reference_id']


def test_inline_resume_records_one_actual_request_and_keeps_completed_snapshot(agent):
    pending=agent.query('2025年华东销售额趋势',session_id='inline')
    agent.query('2025年华北销售额',session_id='inline')
    before=agent.conversations.context('inline')
    request=f"恢复待补查询编号{pending['query_reference_id']}，按月统计"
    result=agent.query(request,session_id='inline')
    after=agent.conversations.context('inline')
    assert len(after)==len(before)+1
    assert after[-1].question==request and result['question']==request
    assert 'executed_sql_context' in after[-1].state
    assert 'comparison_snapshot' in after[-1].state
    assert result['context_resolution']['followup_question']=='按月统计'
    followup=agent.query('那华南呢',session_id='inline')
    assert followup['result']['rows']==agent.engine.answer('2025年华南销售额趋势 按月').to_dict()['rows']


def test_sqlite_reload_preserves_specific_pending_identity(agent):
    pending=agent.query('2025年华东销售额趋势',session_id='persist')
    agent.query('2024年华南销售额',session_id='persist')
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    result=agent.query(f"继续待补查询编号{pending['query_reference_id']}，按月统计",session_id='persist')
    assert result['status']=='ok'
    assert result['result']['rows']==agent.engine.answer('2025年华东销售额趋势 按月').to_dict()['rows']


@pytest.mark.parametrize('reply',['按季度统计','火星','2024年华南订单数','回到SQL查询编号q_bad，那华南呢'])
def test_unverified_inline_reply_restores_task_but_does_not_execute(agent,reply,monkeypatch):
    pending=agent.query('2025年华东销售额趋势',session_id='unverified')
    agent.query('2025年华北销售额',session_id='unverified')
    def forbidden(*args,**kwargs):raise AssertionError('unknown resume reply must not execute or call a model')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    agent.client=type('Forbidden',(),{'generate':forbidden})()
    result=agent.query(f"继续待补查询编号{pending['query_reference_id']}，{reply}",session_id='unverified')
    assert result['status']=='clarification'
    assert result['effective_question']==pending['effective_question']
    assert result['result']['sql'] is None
    assert 'executed_sql_context' not in result['state']


def test_foreign_or_completed_reference_never_falls_back_to_current_pending(agent):
    source=agent.query('2025年华东销售额趋势',session_id='source')
    current=agent.query('2024年华南订单数趋势',session_id='other')
    foreign=agent.query(f"继续待补查询编号{source['query_reference_id']}，按月统计",session_id='other')
    assert foreign['result']['clarification_code']=='pending_reference_unavailable'
    assert foreign['result']['sql'] is None
    completed=agent.query('2025年华北销售额',session_id='other')
    agent.query(f"继续待补查询编号{current['query_reference_id']}",session_id='other')
    invalid=agent.query(f"继续待补查询编号{completed['query_reference_id']}",session_id='other')
    assert invalid['result']['clarification_code']=='pending_reference_not_pending'
    assert invalid['result']['sql'] is None


@pytest.mark.parametrize('change',['filters','code','duplicate','execution'])
def test_changed_or_ambiguous_source_cannot_restore(change,agent):
    pending=agent.query('2025年华东销售额趋势',session_id='changed')
    history=list(deepcopy(agent.conversations.context('changed')))
    if change=='filters':history[0].state['filters']=[]
    if change=='code':history[0].state['clarification_code']='missing_metric'
    if change=='duplicate':history.append(history[0])
    if change=='execution':history[0].state['executed_sql_context']={'payload':{}}
    result=PendingTaskResumeAgent(agent.engine).run(f"继续待补查询编号{pending['query_reference_id']}",history)
    assert result.turn is None


def test_history_eviction_preserves_pending_but_reset_clears_it(agent):
    pending=agent.query('2025年华东销售额趋势',session_id='evicted')
    for index in range(8):
        agent.conversations.remember('evicted',question=str(index),effective_question=str(index),state={'route':'document'})
    result=agent.query(f"继续待补查询编号{pending['query_reference_id']}",session_id='evicted')
    assert result['result']['clarification_code']=='missing_time_grain'
    assert result['effective_question']==pending['effective_question']
    new=agent.query('2025年华东销售额趋势',session_id='evicted')
    reset=agent.query(f"继续待补查询编号{new['query_reference_id']}",session_id='evicted',reset_context=True)
    assert reset['result']['clarification_code']=='pending_reference_unavailable'


@pytest.mark.parametrize('expression',['继续待补查询','恢复待补查询编号q_bad','继续待补查询编号q_'+'a'*32+'，'])
def test_malformed_reference_does_not_execute(agent,expression):
    result=agent.query(expression,session_id='malformed')
    assert result['status']=='clarification'
    assert result['result']['clarification_code']=='pending_reference_invalid'
    assert result['result']['sql'] is None
