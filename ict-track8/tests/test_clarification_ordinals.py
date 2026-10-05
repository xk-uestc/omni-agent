"""Menu references must bind to current source, ordering and original scope."""
from copy import deepcopy
import pytest
from backend.clarification import clarification_ordinal
from backend.clarification_continuation import ClarificationContinuationAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.sql_history_scope import resolve_sql_followup_scope


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


@pytest.mark.parametrize('reply,index',[('选第二个',2),('选择第2项吧',2),('第一个选项',1),
    ('选2',2),('最后一个',-1),('第十二项',12),('第两项',2),('第0个',0)])
def test_only_whole_explicit_menu_references_are_recognized(reply,index):
    assert clarification_ordinal(reply)==index


@pytest.mark.parametrize('reply',['2','2025','第二个客户销售额','选第二个并排除线上','不要第一个','选项2是什么意思'])
def test_numeric_questions_and_extra_constraints_are_not_menu_choices(reply):
    assert clarification_ordinal(reply) is None


@pytest.mark.parametrize('reply',['选第二个','选择第2项吧','最后一个','选2'])
def test_grain_selection_preserves_region_and_time_after_sqlite_rebuild(agent,reply):
    initial=agent.query('2025年华东销售额趋势',session_id='menu')
    assert initial['result']['clarification_options'][1]['value']=='yearly_trend'
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    result=agent.query(reply,session_id='menu')
    assert result['status']=='ok'
    assert result['context_resolution']['selected_option_index']==2
    assert result['result']['rows']==[{'年份':'2025','销售额':29584.0}]
    assert '华东' in result['effective_question'] and '2025年' in result['effective_question']


@pytest.mark.parametrize('reply',['第0个','选第99项','第十二项'])
def test_out_of_range_reply_retains_original_pending_without_model_or_sql(agent,monkeypatch,reply):
    initial=agent.query('2025年华东销售额趋势',session_id='invalid')
    def forbidden(*args,**kwargs):raise AssertionError('invalid menu selection must not execute')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    agent.client=type('Forbidden',(),{'generate':forbidden})()
    result=agent.query(reply,session_id='invalid')
    assert result['status']=='clarification'
    assert result['effective_question']==initial['effective_question']
    assert result['context_resolution']['reason']=='option_ordinal_out_of_range'
    assert result['result']['sql'] is None


def test_selecting_time_type_never_invents_year_and_keeps_request(agent):
    initial=agent.query('销售额趋势',session_id='time')
    result=agent.query('第一个',session_id='time')
    assert result['status']=='clarification' and result['result']['sql'] is None
    assert result['context_resolution']['reason']=='selected_time_requires_value'
    assert result['effective_question']==initial['effective_question']
    pending=agent.query('2025年',session_id='time')
    assert pending['result']['clarification_code']=='missing_time_grain'
    final=agent.query('选第一个',session_id='time')
    assert final['status']=='ok' and final['context_resolution']['selected_value']=='monthly_trend'


def test_missing_metric_ordinal_keeps_original_filter_scope(agent):
    initial=agent.query('2025年华东地区',session_id='metric')
    options=initial['result']['clarification_options']
    index=next(i+1 for i,option in enumerate(options) if option['value']=='sales_amount')
    result=agent.query(f'选第{index}个',session_id='metric')
    assert result['status']=='ok'
    assert result['result']['rows']==[{'销售额':29584.0}]
    assert result['context_resolution']['selected_option_index']==index


def test_comparison_operand_pending_can_choose_grain_by_number(agent):
    for q in ('2025年华东按年销售额趋势','2025年华南按年销售额趋势','比较刚才两次查询'):
        assert agent.query(q,session_id='compare')['status']=='ok'
    pending=agent.query('把基准值改成完整问题：2025年华东销售额趋势',session_id='compare')
    assert pending['status']=='clarification'
    result=agent.query('选第二个',session_id='compare')
    assert result['status']=='ok' and result['route']=='comparison'


@pytest.mark.parametrize('alteration',['missing','reordered'])
def test_ordinal_cannot_silently_use_a_different_or_legacy_option_order(agent,alteration):
    agent.query('2025年华东销售额趋势',session_id='order')
    history=deepcopy(agent.conversations.context('order'))
    state=history[-1].state
    if alteration=='missing':state.pop('clarification_options')
    else:state['clarification_options'].reverse()
    scope,audit=resolve_sql_followup_scope('第二个',history,agent.engine)
    assert audit['mode']!='server_verified_sql_clarification_option_fill'
    retained=ClarificationContinuationAgent(agent.engine).run('第二个',history)
    assert retained.reason=='option_list_changed' and retained.scope=='2025年华东销售额趋势'
