from copy import deepcopy
import pytest
from backend.omni_agent import OmniAgent
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.knowledge_store import KnowledgeStore
from backend.session import ConversationStore
from backend.conversation_comparison import unseal


@pytest.fixture
def agent(tmp_path):
    result=OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))
    result.query('2025年华东销售额',session_id='batch')
    result.query('2025年华南销售额',session_id='batch')
    assert result.query('比较刚才两次查询',session_id='batch')['status']=='ok'
    return result


def context(agent):return agent.conversations.context('batch')[-1].state['comparison_context']


def test_two_time_edits_commit_complete_pair_and_evidence(agent):
    result=agent.query('把基准查询改成2024年；把比较查询改成2024年',session_id='batch')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert [row['基准值'],row['比较值'],row['差值']]==[15492,4998,-10494]
    assert len(result['result']['batch_edit_evidence'])==2
    assert 'comparison_pending_batch' not in result['state']


@pytest.mark.parametrize('question',['两边都换成2024年','两项查询同时改成2024年'])
def test_explicit_shared_condition_updates_both_roles(agent,question):
    result=agent.query(question,session_id='batch')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert [row['基准值'],row['比较值'],row['差值']]==[15492,4998,-10494]


def test_both_metric_edits_may_cross_a_temporary_metric_mismatch(agent):
    result=agent.query('把基准值改成订单数，把比较值改成订单数',session_id='batch')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['指标']=='订单数' and row['基准值']==row['比较值']==3


def test_second_failure_keeps_original_pair_not_partially_modified_first(agent):
    before=deepcopy(context(agent))
    result=agent.query('把基准查询改成2024年；把比较查询改成完整问题：火星库存',session_id='batch')
    assert result['status']=='clarification'
    assert context(agent)==before
    recovered=agent.query('两者差多少',session_id='batch')
    assert recovered['result']['rows'][0]['基准值']==29584


def test_duplicate_target_roles_are_rejected_before_any_execution(agent,monkeypatch):
    before=deepcopy(context(agent))
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('same-side batch must not execute'))
    result=agent.query('把基准值改成2024年；把较早查询改成订单数',session_id='batch')
    assert result['result']['clarification_code']=='comparison_batch_two_roles_required'
    assert context(agent)==before


def test_reverse_baseline_resolves_both_roles_against_original_direction(agent):
    agent.query('把基准换成较晚结果',session_id='batch')
    result=agent.query('把基准查询改成2024年；把比较查询改成2024年',session_id='batch')
    assert result['status']=='ok'
    assert [result['result']['rows'][0][key] for key in ('基准值','比较值','差值')]==[4998,15492,10494]


def test_continuous_clarification_stages_first_side_and_commits_only_after_second(agent):
    original=deepcopy(context(agent))
    result=agent.query('把基准查询改成完整问题：销售额趋势；把比较查询改成完整问题：销售额趋势',session_id='batch')
    assert result['result']['comparison_batch_progress']['completed_count']==0
    for reply,completed in [('2025年',0),('按月',1),('2025年',1)]:
        result=agent.query(reply,session_id='batch')
        assert result['status']=='clarification'
        assert result['result']['comparison_batch_progress']['completed_count']==completed
        assert context(agent)==original
        assert 'comparison_pending_batch' not in result['state']
    restarted=OmniAgent(agent.engine,agent.knowledge,ConversationStore(storage_path=agent.conversations.storage_path))
    result=restarted.query('按月',session_id='batch')
    assert result['status']=='ok'
    assert len(result['result']['batch_edit_evidence'])==2
    assert all(row['差值']==0 for row in result['result']['rows'])
    assert context(restarted)!=original


def test_cancel_pending_batch_discards_staged_first_side(agent):
    original=deepcopy(context(agent))
    pending=agent.query('把基准查询改成2024年；把比较查询改成完整问题：销售额趋势',session_id='batch')
    assert pending['result']['comparison_batch_progress']['completed_count']==1
    assert context(agent)==original
    result=agent.query('取消修改',session_id='batch')
    assert result['status']=='ok' and context(agent)==original
    assert 'comparison_pending_batch' not in result['state']


def test_tampered_batch_context_is_not_resumed(agent):
    agent.query('把基准查询改成2024年；把比较查询改成完整问题：销售额趋势',session_id='batch')
    turn=agent.conversations.context('batch')[-1]
    state=deepcopy(turn.state)
    state['comparison_pending_batch']['payload']['cursor']=0
    agent.conversations.remember('batch',question=turn.question,effective_question=turn.effective_question,state=state)
    result=agent.query('2025年',session_id='batch')
    assert result['status']=='clarification'
    assert result['result']['clarification_code']=='comparison_snapshot_invalid'


def test_completed_cross_year_pair_waits_for_alignment_without_partial_publish(agent,monkeypatch):
    original=deepcopy(context(agent))
    pending=agent.query('把基准查询改成完整问题：2024年销售额趋势 按月；把比较查询改成完整问题：2025年销售额趋势 按月',session_id='batch')
    assert pending['status']=='clarification'
    assert pending['result']['comparison_batch_progress']['completed_count']==2
    assert pending['result']['clarification_code']=='comparison_no_pairs'
    assert context(agent)==original
    monkeypatch.setattr(agent.engine,'answer',lambda *args,**kw:pytest.fail('alignment must not rerun either staged query'))
    reversed_pending=agent.query('把基准换成较晚结果',session_id='batch')
    assert reversed_pending['status']=='clarification' and context(agent)==original
    result=agent.query('按月份对齐',session_id='batch')
    assert result['status']=='ok'
    assert result['result']['comparison_evidence']['alignment']=='month_of_year'
    assert result['result']['comparison_evidence']['baseline']=='较晚查询'
    assert len(result['result']['batch_edit_evidence'])==2
    assert context(agent)!=original


def test_confirmed_api_options_restore_the_staged_dual_edit(agent,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as module
    monkeypatch.setattr(module,'engine',agent.engine)
    monkeypatch.setattr(module,'knowledge_store',agent.knowledge)
    monkeypatch.setattr(module,'conversation_store',agent.conversations)
    monkeypatch.setattr(module,'generation_client',None)
    original=deepcopy(context(agent))
    with TestClient(module.app) as client:
        response=client.post('/api/v1/omni/query',json={'session_id':'batch','question':
            '把基准查询改成完整问题：销售额趋势；把比较查询改成完整问题：销售额趋势'})
        assert response.status_code==200
        body=response.json()
        for value,time in [('year','2025'),('monthly_trend',None),('year','2025'),('monthly_trend',None)]:
            result=body['result']
            selected=next(option for option in result['clarification_options'] if option['value']==value)
            response=client.post('/api/v1/omni/clarify',json={'session_id':'batch','original_question':body['effective_question'],
                'clarification_code':result['clarification_code'],'selected_value':selected['value'],'selected_time':time})
            assert response.status_code==200,response.text
            body=response.json()
            if body['status']=='clarification':assert context(agent)==original
        assert body['status']=='ok'
        assert len(body['result']['batch_edit_evidence'])==2
        assert 'comparison_pending_batch' not in body['state']


def test_source_change_before_resuming_batch_never_executes_another_query(agent,monkeypatch):
    import sqlite3
    original=deepcopy(context(agent))
    agent.query('把基准查询改成2024年；把比较查询改成完整问题：销售额趋势',session_id='batch')
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute("UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id='SO-001'")
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('source change must stop the batch before execution'))
    result=agent.query('2025年',session_id='batch')
    assert result['result']['clarification_code']=='comparison_source_changed'
    assert context(agent)==original


def test_staged_snapshots_are_not_sent_to_the_model_when_user_changes_topic(agent):
    agent.query('把基准查询改成2024年；把比较查询改成完整问题：销售额趋势',session_id='batch')
    class InspectModel:
        called=False
        def generate(self,instructions,context,*args,**kwargs):
            self.called=True
            assert context['history']
            for turn in context['history']:
                assert not {'comparison_pending_batch','comparison_pending_query','comparison_context','comparison_snapshot'} & set(turn['state'])
            return {'route':'document','effective_question':context['question'],'tasks_json':'[]','clarification':''}
    inspector=InspectModel();agent.client=inspector
    agent.query('保修政策是什么',session_id='batch')
    assert inspector.called
