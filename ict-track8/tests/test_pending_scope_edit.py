"""Atomic changes before clarification completes, including failure recovery."""
from copy import deepcopy
import pytest
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.pending_scope_edit import PendingScopeEditAgent


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


@pytest.mark.parametrize('edit,expected',[
    ('改成2024年','2024年华东销售额趋势'),
    ('把年份改为2024年吧','2024年华东销售额趋势'),
    ('那把时间改成2024年吧','2024年华东销售额趋势'),
    ('2025年改成2024年','2024年华东销售额趋势'),
    ('把月份换成2025年3月','2025年3月华东销售额趋势'),
    ('改成华南','2025年华南销售额趋势'),
    ('地区改成华南','2025年华南销售额趋势'),
    ('华东改成华南','2025年华南销售额趋势'),
    ('指标换成订单数','2025年华东订单数趋势'),
    ('销售额改成订单数','2025年华东订单数趋势'),
    ('时间改成2024年，地区改成华南，指标改成订单数','2024年华南订单数趋势'),
])
def test_edits_preserve_task_and_resume_from_sqlite(agent,edit,expected):
    session='pending-edit'
    agent.query('2025年华东销售额趋势',session_id=session)
    edited=agent.query(edit,session_id=session)
    assert edited['status']=='clarification'
    assert edited['effective_question']==expected
    assert edited['result']['clarification_code']=='missing_time_grain'
    assert edited['result']['sql'] is None
    assert edited['context_resolution']['mode']=='server_verified_pending_sql_edit'
    assert 'executed_sql_context' not in edited['state']
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    final=agent.query('按月统计',session_id=session)
    assert final['status']=='ok'
    assert final['result']['rows']==agent.engine.answer(expected+' 按月').to_dict()['rows']


def test_chained_edits_and_help_preserve_latest_scope_without_execution(agent,monkeypatch):
    agent.query('2025年华东销售额趋势',session_id='chain')
    def forbidden(*args,**kwargs):raise AssertionError('uncompleted task cannot execute or call model')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    agent.client=type('Forbidden',(),{'generate':forbidden})()
    for reply in ['改成2024年','地区改成华南','指标换成订单数','选项有什么区别']:
        result=agent.query(reply,session_id='chain')
        assert result['status']=='clarification'
        assert result['result']['sql'] is None
    assert result['effective_question']=='2024年华南订单数趋势'


@pytest.mark.parametrize('edit',[
    '时间改成2024年，地区改成火星',
    '时间改成2024年，时间改成2023年',
    '改成2024年并排除华东',
    '时间改成2024年，地区改成华南，指标改成订单数，改成华北',
    '改成0000年','改成2025-13','地区改成订单数','指标改成华南',
    '改成2024年;DROP TABLE sales_orders',
])
def test_failed_multi_edit_keeps_original_scope_atomically(agent,edit):
    initial=agent.query('2025年华东销售额趋势',session_id='reject-edit')
    result=agent.query(edit,session_id='reject-edit')
    assert result['status']=='clarification'
    assert result['effective_question']==initial['effective_question']
    assert result['state']['filters']==initial['state']['filters']
    assert result['context_resolution']['mode']=='pending_sql_clarification_retained'
    assert result['result']['sql'] is None
    final=agent.query('按月',session_id='reject-edit')
    assert final['result']['rows']==agent.engine.answer('2025年华东销售额趋势 按月').to_dict()['rows']


def test_new_question_and_other_session_do_not_inherit_pending_edits(agent):
    agent.query('2025年华东销售额趋势',session_id='source')
    assert PendingScopeEditAgent(agent.engine).run('2024年华南订单数',agent.conversations.context('source')) is None
    foreign=agent.query('改成2024年',session_id='foreign')
    assert foreign['context_resolution'].get('mode')!='server_verified_pending_sql_edit'
    reset=agent.query('改成2024年',session_id='source',reset_context=True)
    assert reset['context_resolution'].get('mode')!='server_verified_pending_sql_edit'


def test_saved_conditions_tampering_prevents_edit(agent):
    agent.query('2025年华东销售额趋势',session_id='tampered')
    history=deepcopy(agent.conversations.context('tampered'))
    history[-1].state['filters']=[]
    result=PendingScopeEditAgent(agent.engine).run('改成2024年',history)
    assert result.verified is False
    assert result.reason=='pending_edit_saved_scope_mismatch'
    assert result.scope=='2025年华东销售额趋势'


def test_named_schema_filter_edit_is_not_limited_to_region_and_channel(agent):
    agent.query('2025年手机销售额趋势',session_id='category')
    result=agent.query('品类改成耳机',session_id='category')
    assert result['effective_question']=='2025年耳机销售额趋势'
    assert result['context_resolution']['replacements']==[{'slot':'sales_orders.product_category','from':'手机','to':'耳机'}]
    final=agent.query('按月',session_id='category')
    assert final['result']['rows']==agent.engine.answer('2025年耳机销售额趋势 按月').to_dict()['rows']


def test_edit_that_resolves_last_missing_condition_reenters_normal_execution(agent):
    initial=agent.query('华东销售额同比',session_id='complete-edit')
    assert initial['result']['clarification_code']=='missing_comparison_period'
    result=agent.query('时间改成2024年',session_id='complete-edit')
    assert result['status']=='ok'
    assert result['context_resolution']['mode']=='server_verified_pending_sql_edit'
    assert result['result']['rows']==agent.engine.answer('华东销售额同比 2024年').to_dict()['rows']
    assert result['state']['pending_question'] is None


def test_failed_second_clause_does_not_keep_first_appended_time(agent):
    initial=agent.query('华东销售额同比',session_id='append-rollback')
    result=agent.query('时间改成2024年，地区改成火星',session_id='append-rollback')
    assert result['effective_question']==initial['effective_question']
    assert result['result']['sql'] is None
    assert result['state']['filters']==initial['state']['filters']


def test_atomic_edits_and_offered_grain_can_complete_query_in_one_reply(agent):
    agent.query('2025年华东销售额趋势',session_id='edit-and-fill')
    result=agent.query('时间改成2024年，地区改成华南，指标改成订单数，按月统计',session_id='edit-and-fill')
    assert result['status']=='ok'
    assert result['context_resolution']['mode']=='server_verified_pending_sql_edit'
    assert len(result['context_resolution']['replacements'])==4
    assert result['result']['rows']==agent.engine.answer('2024年华南订单数趋势 按月').to_dict()['rows']


@pytest.mark.parametrize('tail',['按季度统计','按月统计，按年统计','按月统计并排除华南'])
def test_invalid_final_choice_rolls_back_all_prior_edits(agent,tail):
    original=agent.query('2025年华东销售额趋势',session_id='invalid-fill')
    result=agent.query('时间改成2024年，'+tail,session_id='invalid-fill')
    assert result['status']=='clarification'
    assert result['effective_question']==original['effective_question']
    assert result['state']['filters']==original['state']['filters']
    assert result['result']['sql'] is None


def test_rank_edit_and_current_offered_dimension_preserve_original_analysis(agent):
    agent.query('2025年华东销售额排名',session_id='rank-edit')
    result=agent.query('时间改成2024年，按渠道',session_id='rank-edit')
    assert result['status']=='ok'
    assert result['context_resolution']['mode']=='server_verified_pending_sql_edit'
    assert result['result']['rows']==agent.engine.answer('2024年华东销售额排名 按渠道').to_dict()['rows']


def test_offered_choice_before_condition_edit_is_also_atomic(agent):
    agent.query('2025年华东销售额趋势',session_id='choice-first')
    result=agent.query('按月统计，时间改成2024年，地区改成华南',session_id='choice-first')
    assert result['status']=='ok'
    assert result['result']['rows']==agent.engine.answer('2024年华南销售额趋势 按月').to_dict()['rows']
    assert result['context_resolution']['mode']=='server_verified_pending_sql_edit'


def test_explicit_new_topic_is_not_intercepted_as_edit(agent):
    agent.query('2025年华东销售额趋势',session_id='new-topic')
    decision=PendingScopeEditAgent(agent.engine).run('新问题时间改成2024年',agent.conversations.context('new-topic'))
    assert decision is None
