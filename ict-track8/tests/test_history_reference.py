from copy import deepcopy
import pytest
from backend.history_reference import ConversationReferenceAgent
from backend.session import ConversationTurn,ConversationStore


def turn(question,route='sql',success=True):
    return ConversationTurn(question,question,0,{'route':route,
        'executed_sql_context':{'payload':{},'sha256':'observed'} if success else None})


@pytest.mark.parametrize('position,offset',[('2',2),('二',2),('十二',12),('二十一',21),('两',2)])
def test_backward_position_is_sql_attempts_not_chat_turns(position,offset):
    history=[turn(str(i)) for i in range(24)]
    history.insert(10,turn('definition','document'))
    selected=ConversationReferenceAgent().select(f'回到倒数第{position}次SQL查询，那华南呢',history)
    assert selected.request.offset==offset
    assert history[selected.history_index].question==str(24-offset)


def test_failure_not_skipped_without_explicit_success_filter():
    history=[turn('good'),turn('failed',success=False),turn('new')]
    agent=ConversationReferenceAgent()
    assert agent.select('回到倒数第二次SQL查询，那华南呢',history).history_index==1
    assert agent.select('回到倒数第二次成功的SQL查询，那华南呢',history).history_index==0


def test_quote_matches_only_exact_original_or_effective_question_and_rejects_duplicates():
    history=[turn('2025年华东销售额'),turn('2024年华南订单数')]
    agent=ConversationReferenceAgent()
    assert agent.select('回到SQL查询“2025年华东销售额”，那华南呢',history).history_index==0
    assert agent.select('回到SQL查询“华东销售额”，那华南呢',history).reason=='requested_sql_reference_not_available'
    history.append(deepcopy(history[0]))
    assert agent.select('回到SQL查询“2025年华东销售额”，那华南呢',history).reason=='requested_sql_reference_ambiguous'


@pytest.mark.parametrize('expression',['回到第一次SQL查询，那华南呢','回到倒数第0次SQL查询，那华南呢',
                                      '回到倒数第九九次SQL查询，那华南呢','回到上一次SQL查询',
                                      '回到上一次SQL查询，回到倒数第二次SQL查询，那华南呢'])
def test_unsupported_or_outside_window_is_not_an_independent_query(expression):
    selected=ConversationReferenceAgent().select(expression,[turn('q')])
    assert selected is not None and selected.reason is not None
    assert selected.history_index is None


@pytest.mark.parametrize('persistent',[False,True])
def test_reference_window_survives_restart_and_isolates_sessions(tmp_path,persistent):
    path=tmp_path/'history.sqlite' if persistent else None
    store=ConversationStore(max_turns=3,storage_path=path)
    for i in range(5):store.remember('one',question=f'q{i}',effective_question=f'q{i}',state=turn('q').state)
    store.remember('two',question='other',effective_question='other',state=turn('q').state)
    if persistent:store=ConversationStore(max_turns=3,storage_path=path)
    agent=ConversationReferenceAgent()
    assert store.context('one')[agent.select('回到倒数第二次SQL查询，那华南呢',store.context('one')).history_index].question=='q3'
    assert agent.select('回到倒数第四次SQL查询，那华南呢',store.context('one')).reason=='requested_sql_reference_not_available'
    assert agent.select('回到SQL查询“q0”，那华南呢',store.context('one')).reason=='requested_sql_reference_not_available'
    assert agent.select('回到SQL查询“q3”，那华南呢',store.context('two')).reason=='requested_sql_reference_not_available'
