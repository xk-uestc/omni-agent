from copy import deepcopy
import sqlite3
import pytest
from backend.omni_agent import OmniAgent
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.knowledge_store import KnowledgeStore
from backend.session import ConversationStore


@pytest.fixture(params=['memory','sqlite'])
def setup(request,tmp_path):
    now=[100.]
    options={'clock':lambda:now[0],'ttl_seconds':10,'pending_ttl_seconds':100}
    if request.param=='sqlite':options['storage_path']=tmp_path/'sessions.sqlite'
    store=ConversationStore(**options)
    agent=OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),KnowledgeStore(tmp_path/'knowledge'),store)
    agent.query('2025年华东销售额',session_id='compare')
    agent.query('2025年华南销售额',session_id='compare')
    assert agent.query('比较刚才两次查询',session_id='compare')['status']=='ok'
    return agent,now,options


def test_role_selection_survives_interruption_and_history_expiry(setup,monkeypatch):
    agent,now,_=setup
    waiting=agent.query('换成2024年',session_id='compare')
    assert waiting['result']['clarification_code']=='comparison_edit_role_required'
    identifier=waiting['query_reference_id']
    agent.query('2025年华北订单数',session_id='compare')
    now[0]+=11
    before=agent.conversations.context('compare')
    tasks=agent.query('查看待补问题',session_id='compare')['pending_tasks']
    assert len(tasks)==1 and tasks[0]['available'] and tasks[0]['task_type']=='comparison'
    assert agent.conversations.context('compare')==before
    result=agent.query(f'继续待补查询编号{identifier}，基准值',session_id='compare')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    with sqlite3.connect(agent.engine.database_path) as connection:
        left=connection.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?",('2024-01-01','2025-01-01','华东')).fetchone()[0]
        right=connection.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?",('2025-01-01','2026-01-01','华南')).fetchone()[0]
    assert [row[key] for key in ('基准值','比较值','差值')]==[left,right,right-left]
    assert agent.query('查看待补问题',session_id='compare')['pending_tasks']==[]
    assert agent.query(f'继续待补查询编号{identifier}',session_id='compare')['result']['clarification_code']=='pending_reference_completed'


def test_pure_restore_no_execution_then_query_grain_completion(setup,monkeypatch):
    agent,now,_=setup
    waiting=agent.query('把基准查询改成完整问题：2025年华东销售额趋势',session_id='compare')
    identifier=waiting['query_reference_id']
    now[0]+=11
    original=agent.engine.answer
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('restore cannot execute'))
    restored=agent.query(f'继续待补查询编号{identifier}',session_id='compare')
    assert restored['route']=='comparison' and restored['result']['clarification_code']=='missing_time_grain'
    assert restored['result']['clarification_options']
    assert 'pending_comparison_result' not in restored['state']
    monkeypatch.setattr(agent.engine,'answer',original)
    # Comparing grouped versus scalar will still require matching dimensions,
    # but the selected operand itself must have executed with its original scope.
    result=agent.query('按月统计',session_id='compare')
    assert result['route']=='comparison'
    assert 'missing_time_grain'!=result['result'].get('clarification_code')


def test_staged_batch_and_cancel_survive_restart(setup):
    agent,now,options=setup
    waiting=agent.query('把基准查询改成2024年；把比较查询改成完整问题：销售额趋势',session_id='compare')
    assert waiting['result']['comparison_batch_progress']['completed_count']==1
    identifier=waiting['query_reference_id']
    now[0]+=11
    if options.get('storage_path'):agent.conversations=ConversationStore(**options)
    restored=agent.query(f'继续待补查询编号{identifier}',session_id='compare')
    assert restored['route']=='comparison'
    assert restored['result']['comparison_batch_progress']['completed_count']==1
    cancelled=agent.query('取消修改',session_id='compare')
    assert cancelled['status']=='ok'
    assert cancelled['result']['rows'][0]['基准值']==29584
    assert agent.query('查看待补问题',session_id='compare')['pending_tasks']==[]


def test_foreign_expired_and_source_changed_are_not_restored(setup):
    agent,now,_=setup
    waiting=agent.query('换成2024年',session_id='compare')
    identifier=waiting['query_reference_id']
    assert agent.query(f'继续待补查询编号{identifier}',session_id='foreign')['result']['clarification_code']=='pending_reference_unavailable'
    with sqlite3.connect(agent.engine.database_path) as connection:connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1')
    item=agent.query('查看待补问题',session_id='compare')['pending_tasks'][0]
    assert not item['available'] and item['reason']=='comparison_source_changed'
    result=agent.query(f'继续待补查询编号{identifier}',session_id='compare')
    assert result['result']['clarification_code']=='comparison_source_changed'
    now[0]=200
    assert agent.query('查看待补问题',session_id='compare')['pending_tasks']==[]


def test_corrupted_pending_envelope_is_rejected(setup):
    agent,_,_=setup
    waiting=agent.query('换成2024年',session_id='compare')
    turn=agent.conversations.context('compare')[-1]
    state=deepcopy(turn.state)
    state['pending_comparison_result']['payload']['clarification_code']='missing_time_range'
    agent.conversations.remember('compare',question=turn.question,effective_question=turn.effective_question,state=state)
    latest=agent.conversations.context('compare')[-1]
    result=agent.query(f'继续待补查询编号{latest.turn_id}',session_id='compare')
    assert result['result']['clarification_code']=='comparison_snapshot_invalid'


def test_batch_alignment_restore_never_reruns_staged_queries(setup,monkeypatch):
    agent,now,_=setup
    pending=agent.query('把基准查询改成完整问题：2024年销售额趋势 按月；把比较查询改成完整问题：2025年销售额趋势 按月',session_id='compare')
    assert pending['result']['comparison_batch_progress']['completed_count']==2
    identifier=pending['query_reference_id']
    now[0]+=11
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('staged alignment must not execute again'))
    result=agent.query(f'继续待补查询编号{identifier}，按月份对齐',session_id='compare')
    assert result['status']=='ok' and result['result']['comparison_evidence']['alignment']=='month_of_year'
    assert agent.query('查看待补问题',session_id='compare')['pending_tasks']==[]


def test_restored_query_can_complete_via_real_clarification_endpoint(setup,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as module
    agent,now,_=setup
    monkeypatch.setattr(module,'engine',agent.engine)
    monkeypatch.setattr(module,'knowledge_store',agent.knowledge)
    monkeypatch.setattr(module,'conversation_store',agent.conversations)
    monkeypatch.setattr(module,'generation_client',None)
    client=TestClient(module.app)
    pending=agent.query('把基准查询改成2024年；把比较查询改成完整问题：销售额趋势',session_id='compare')
    now[0]+=11
    restored=agent.query(f"继续待补查询编号{pending['query_reference_id']}",session_id='compare')
    response=client.post('/api/v1/omni/clarify',json={'session_id':'compare','original_question':restored['effective_question'],
        'clarification_code':'missing_time_range','selected_value':'year','selected_time':'2024'})
    assert response.status_code==200
    body=response.json()
    assert body['route']=='comparison' and body['result']['clarification_code']=='missing_time_grain'
    assert body['result']['comparison_batch_progress']['completed_count']==1
    response=client.post('/api/v1/omni/clarify',json={'session_id':'compare','original_question':body['effective_question'],
        'clarification_code':'missing_time_grain','selected_value':'monthly_trend'})
    assert response.status_code==200 and response.json()['route']=='comparison'
    assert response.json()['result'].get('clarification_code')!='missing_time_grain'


def test_role_pending_cancel_and_unknown_reply_keep_original_sources(setup,monkeypatch):
    agent,now,_=setup
    pending=agent.query('换成2024年',session_id='compare')
    identifier=pending['query_reference_id']
    now[0]+=11
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('unsupported reply and cancel cannot execute'))
    restored=agent.query(f'继续待补查询编号{identifier}，随便你选',session_id='compare')
    assert restored['route']=='comparison' and restored['result']['clarification_code']=='comparison_edit_role_required'
    assert '本次补充未合并' in restored['result']['answer']
    cancelled=agent.query('取消修改',session_id='compare')
    assert cancelled['status']=='ok' and cancelled['result']['rows'][0]['基准值']==29584
    assert agent.query('查看待补问题',session_id='compare')['pending_tasks']==[]


@pytest.mark.parametrize('reply',['好的','继续吧','怎么选','这些选项是什么意思','随便'])
def test_role_help_retains_edit_and_sources_without_execution(setup,monkeypatch,reply):
    agent,_,_=setup
    waiting=agent.query('换成2024年',session_id='compare')
    original=deepcopy(agent.conversations.context('compare')[-1].state['comparison_context'])
    with monkeypatch.context() as guarded:
        guarded.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('help must not execute'))
        retained=agent.query(reply,session_id='compare')
    assert retained['route']=='comparison' and retained['status']=='clarification'
    assert retained['result']['clarification_code']=='comparison_edit_role_required'
    assert retained['effective_question']=='换成2024年'
    state=agent.conversations.context('compare')[-1].state
    assert state['comparison_context']==original
    assert state['comparison_pending_edit']=='换成2024年'
    assert retained['trace'][0]['executed'] is False
    final=agent.query('基准值',session_id='compare')
    assert final['status']=='ok'
    row=final['result']['rows'][0]
    assert row['基准值']==agent.engine.answer('2024年华东销售额').to_dict()['rows'][0]['销售额']
    assert row['比较值']==agent.engine.answer('2025年华南销售额').to_dict()['rows'][0]['销售额']


@pytest.mark.parametrize('reply',['确认','怎么选','按季度'])
def test_operand_help_retains_specific_query_then_cancels(setup,monkeypatch,reply):
    agent,_,_=setup
    agent.query('把基准查询改成完整问题：2025年华东销售额趋势',session_id='compare')
    old=deepcopy(agent.conversations.context('compare')[-1].state)
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('help and cancel must not execute'))
    result=agent.query(reply,session_id='compare')
    assert result['route']=='comparison' and result['result']['clarification_code']=='missing_time_grain'
    assert result['effective_question']=='2025年华东销售额趋势'
    state=agent.conversations.context('compare')[-1].state
    assert state['comparison_context']==old['comparison_context']
    assert state['comparison_pending_query']==old['comparison_pending_query']
    assert result['trace'][0]['executed'] is False
    if reply=='怎么选':
        for option in result['result']['clarification_options']:
            assert option['label'] in result['result']['clarification']
    assert agent.query('取消修改',session_id='compare')['result']['rows'][0]['基准值']==29584


def test_role_help_rechecks_source_revision_before_preserving_task(setup,monkeypatch):
    agent,_,_=setup
    agent.query('换成2024年',session_id='compare')
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1')
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**kw:pytest.fail('changed source must not execute'))
    rejected=agent.query('怎么选',session_id='compare')
    assert rejected['status']=='clarification'
    assert rejected['result']['clarification_code']=='comparison_source_changed'
