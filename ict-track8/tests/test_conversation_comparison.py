from copy import deepcopy
from dataclasses import replace
import sqlite3

import pytest

from backend.conversation_comparison import parse_request,build_snapshot,seal,unseal
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.sql_history_scope import saved_sql_context


@pytest.fixture
def agent(tmp_path):
    engine=Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite'))
    return OmniAgent(engine,KnowledgeStore(tmp_path/'knowledge'),ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


def query_pair(agent,left='2025年华东销售额',right='2025年华南销售额'):
    for question in (left,right):
        result=agent.query(question,session_id='comparison')
        assert result['status']=='ok'
    return agent.query('比较刚才两次查询',session_id='comparison')


@pytest.mark.parametrize('question',[
    '比较刚才两次查询','对比最近两次SQL查询结果','比较最近两次成功的SQL查询',
    '计算上面两个结果的差值','这两个结果相差多少','两者差多少','两者变化率是多少',
    '比较刚才两次查询，以较晚结果为基准','按月份对齐，比较最近两次SQL查询',
    '把基准换成较晚结果','按月份对齐',
])
def test_comparison_intent_has_an_explicit_protocol(question):
    assert parse_request(question) is not None


@pytest.mark.parametrize('question',[
    '2025年销售额同比','比较2025与2024年销售额','比较两个政策的生效时间',
    '比较刚才两次查询，删除旧数据','比较刚才两次查询;DROP TABLE sales_orders',
    '最近两次登录是什么时候',
])
def test_other_business_queries_and_extra_actions_are_not_comparison_protocol(question):
    assert parse_request(question) is None


def test_scalar_comparison_and_reverse_use_snapshots_not_new_queries(agent,monkeypatch):
    result=query_pair(agent)
    assert result['route']=='comparison' and result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['基准值']==29584 and row['比较值']==22992 and row['差值']==-6592
    assert row['变化率(%)']==pytest.approx(-6592/29584*100)
    assert row['单位']=='CNY'
    assert 'comparison_context' not in result['state']
    def prohibited(*args,**kwargs):raise AssertionError('comparison must not reexecute or ask the model')
    monkeypatch.setattr(agent.engine,'answer',prohibited)
    result=agent.query('把基准换成较晚结果',session_id='comparison')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['基准值']==22992 and row['差值']==6592
    assert row['变化率(%)']==pytest.approx(6592/22992*100)
    result=agent.query('两者差多少',session_id='comparison')
    assert result['result']['rows'][0]['差值']==6592


def test_grouped_queries_align_by_keys_not_sql_sort_order(agent):
    result=query_pair(agent,'2024年各渠道销售额','2025年各渠道销售额')
    assert result['status']=='ok' and len(result['result']['rows'])==2
    left=agent.engine.answer('2024年各渠道销售额').rows
    right=agent.engine.answer('2025年各渠道销售额').rows
    expected={r['channel']:r['销售额'] for r in right}
    for row in left:expected[row['channel']]-=row['销售额']
    import json
    assert {json.loads(r['分组'])['channel']:r['差值'] for r in result['result']['rows']}==expected


def test_catalogued_derived_metric_keeps_formula_and_unit(agent):
    result=query_pair(agent,'2025年华东客单价','2025年华南客单价')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['指标']=='客单价' and row['单位']=='CNY'
    assert row['差值']==pytest.approx(row['比较值']-row['基准值'])


@pytest.mark.parametrize('right,code',[
    ('2025年华南订单数','comparison_metric_mismatch'),
    ('2025年各地区销售额','comparison_dimension_mismatch'),
])
def test_incompatible_metric_and_group_contracts_require_clarification(agent,right,code):
    result=query_pair(agent,right=right)
    assert result['status']=='clarification' and result['result']['clarification_code']==code
    assert result['result']['rows']==[]


def test_failed_or_pending_query_is_not_silently_skipped(agent):
    query_pair(agent)
    agent.query('2025年华北地区的情况',session_id='comparison')
    result=agent.query('比较最近两次SQL查询',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_reference_not_successful'
    result=agent.query('比较最近两次成功的SQL查询',session_id='comparison')
    assert result['status']=='ok'


def test_document_interruption_needs_explicit_sql_reference(agent):
    query_pair(agent)
    agent.query('保修政策是什么',session_id='comparison')
    result=agent.query('比较刚才两次查询',session_id='comparison')
    assert result['status']=='clarification'
    result=agent.query('比较最近两次SQL查询',session_id='comparison')
    assert result['status']=='ok'


@pytest.mark.parametrize('question',[
    '比较倒数第三次和倒数第一次SQL查询',
    '对比倒数第3次SQL查询和倒数第1次SQL查询',
    '比较SQL查询“2024年华东销售额”和“2025年华南销售额”',
])
def test_explicit_pair_selects_nonadjacent_sources_and_ignores_document_topic(agent,question,monkeypatch):
    for text in ['2024年华东销售额','2025年华北销售额','2025年华南销售额']:
        assert agent.query(text,session_id='comparison')['status']=='ok'
    agent.conversations.remember('comparison',question='definition',effective_question='definition',state={'route':'document'})
    monkeypatch.setattr(agent.engine,'answer',lambda *args,**kwargs:pytest.fail('selected comparison must not reexecute SQL'))
    result=agent.query(question,session_id='comparison')
    assert result['status']=='ok'
    with sqlite3.connect(agent.engine.database_path) as connection:
        values=[connection.execute('SELECT SUM(sales_amount) FROM sales_orders WHERE substr(order_date,1,4)=? AND region=?',args).fetchone()[0]
                for args in [('2024','华东'),('2025','华南')]]
    row=result['result']['rows'][0]
    assert [row['基准值'],row['比较值']]==values
    assert row['差值']==values[1]-values[0]
    sources=result['result']['comparison_evidence']['sources']
    assert [item['history_reference']['history_index'] for item in sources]==[0,2]


def test_new_selected_pair_overrides_previous_comparison_then_can_reverse(agent):
    for text in ['2024年华东销售额','2025年华北销售额','2025年华南销售额']:
        agent.query(text,session_id='comparison')
    first=agent.query('比较倒数第三次和倒数第一次SQL查询',session_id='comparison')
    assert first['result']['rows'][0]['基准值']==15492
    second=agent.query('比较倒数第二次和倒数第一次SQL查询',session_id='comparison')
    assert second['result']['rows'][0]['基准值']==17987
    reversed_=agent.query('把基准换成较晚结果',session_id='comparison')
    assert reversed_['result']['rows'][0]['基准值']==22992
    assert reversed_['result']['rows'][0]['比较值']==17987


@pytest.mark.parametrize('suffix,baseline,current',[('',15492,22992),('，以较晚结果为基准',22992,15492)])
def test_written_reference_order_does_not_reverse_baseline_without_explicit_instruction(agent,suffix,baseline,current):
    for text in ['2024年华东销售额','2025年华北销售额','2025年华南销售额']:
        agent.query(text,session_id='comparison')
    result=agent.query('比较倒数第一次和倒数第三次SQL查询'+suffix,session_id='comparison')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['基准值']==baseline and row['比较值']==current
    assert row['差值']==current-baseline
    assert any('书写顺序无关' in notice for notice in result['result']['notices'])


def test_selected_month_pair_keeps_exact_sources_when_alignment_requires_clarification(agent):
    for text in ['2024年销售额趋势 按月','2025年华北订单数','2025年销售额趋势 按月']:
        assert agent.query(text,session_id='comparison')['status']=='ok'
    pending=agent.query('比较倒数第三次和倒数第一次SQL查询',session_id='comparison')
    assert pending['result']['clarification_code']=='comparison_no_pairs'
    assert pending['result']['comparison_actions'][0]['question']=='按月份对齐'
    result=agent.query('按月份对齐',session_id='comparison')
    assert result['status']=='ok'
    with sqlite3.connect(agent.engine.database_path) as connection:
        rows=connection.execute("SELECT substr(order_date,6,2), SUM(CASE WHEN substr(order_date,1,4)='2024' THEN sales_amount ELSE 0 END), SUM(CASE WHEN substr(order_date,1,4)='2025' THEN sales_amount ELSE 0 END) FROM sales_orders GROUP BY substr(order_date,6,2) HAVING COUNT(DISTINCT substr(order_date,1,4))=2").fetchall()
    import json
    observed={list(json.loads(row['分组']).values())[0]:row['差值'] for row in result['result']['rows'] if row['对应状态']=='可比较'}
    assert observed=={month:right-left for month,left,right in rows}
    assert [s['history_reference']['history_index'] for s in result['result']['comparison_evidence']['sources']]==[0,2]


@pytest.mark.parametrize('question,code',[
    ('比较倒数第一次和倒数第一次SQL查询','comparison_same_reference'),
    ('比较倒数第九次和倒数第一次SQL查询','comparison_selected_reference_unavailable'),
    ('比较倒数第零次和倒数第一次SQL查询','comparison_reference_expression_unsupported'),
    ('比较SQL查询“未执行的问题”和“2025年华南销售额”','comparison_selected_reference_unavailable'),
])
def test_invalid_explicit_pair_never_falls_back_to_latest_two(agent,question,code):
    query_pair(agent)
    result=agent.query(question,session_id='comparison')
    assert result['route']=='comparison' and result['status']=='clarification'
    assert result['result']['clarification_code']==code
    assert result['result']['rows']==[]


def test_selected_pair_success_filter_is_explicit_and_duplicate_quotes_clarify(agent):
    for text in ['2025年华东销售额','2025年华南销售额','2025年华北地区的情况']:
        agent.query(text,session_id='comparison')
    failed=agent.query('比较倒数第二次和倒数第一次SQL查询',session_id='comparison')
    assert failed['result']['clarification_code']=='comparison_reference_not_successful'
    success=agent.query('比较倒数第二次和倒数第一次成功的SQL查询',session_id='comparison')
    assert success['status']=='ok'
    agent.query('2025年华东销售额',session_id='comparison')
    ambiguous=agent.query('比较SQL查询“2025年华东销售额”和“2025年华南销售额”',session_id='comparison')
    assert ambiguous['result']['clarification_code']=='comparison_reference_ambiguous'


def test_selected_pair_survives_persistent_restart_and_checks_source_revision(agent):
    for text in ['2024年华东销售额','2025年华北销售额','2025年华南销售额']:
        agent.query(text,session_id='comparison')
    store=ConversationStore(storage_path=agent.conversations.storage_path)
    restarted=OmniAgent(agent.engine,agent.knowledge,store)
    result=restarted.query('比较倒数第三次和倒数第一次SQL查询',session_id='comparison')
    assert result['status']=='ok'
    other=restarted.query('比较倒数第三次和倒数第一次SQL查询',session_id='other')
    assert other['result']['clarification_code']=='comparison_selected_reference_unavailable'
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute("UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id='SO-001'")
    changed=restarted.query('比较倒数第三次和倒数第一次SQL查询',session_id='comparison')
    assert changed['result']['clarification_code']=='comparison_source_changed'


def test_restart_isolation_and_context_reset(agent):
    query_pair(agent)
    reopened=OmniAgent(agent.engine,agent.knowledge,ConversationStore(storage_path=agent.conversations.storage_path))
    assert reopened.query('把基准换成较晚结果',session_id='comparison')['status']=='ok'
    assert reopened.query('两者差多少',session_id='another')['status']=='clarification'
    assert reopened.query('两者差多少',session_id='comparison',reset_context=True)['status']=='clarification'


def test_source_and_catalog_changes_invalidate_snapshot_authority(agent):
    query_pair(agent)
    agent.engine.metric_catalog.sources['revenue']=replace(agent.engine.metric_catalog.sources['revenue'],currency='USD')
    result=agent.query('两者差多少',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_metric_definition_changed'


def test_database_change_is_not_reported_as_current_comparison(agent):
    query_pair(agent)
    with sqlite3.connect(agent.engine.database_path) as connection:
        cursor = connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id=(SELECT order_id FROM sales_orders LIMIT 1)')
        assert cursor.rowcount == 1
    result=agent.query('两者差多少',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_source_changed'


def test_changed_snapshot_values_and_execution_record_are_rejected(agent):
    query_pair(agent)
    turns=agent.conversations.context('comparison')
    state=deepcopy(turns[-1].state)
    context=state['comparison_context']['payload']
    context['sources'][0]['snapshot']['payload']['rows'][0]['销售额']=0
    # Even re-sealing the outer envelope does not repair the nested record.
    state['comparison_context']=seal(context)
    agent.conversations.remember('comparison',question='stored',effective_question='stored',state=state)
    result=agent.query('两者差多少',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_snapshot_invalid'


def test_partial_previews_and_unknown_limit_scope_do_not_become_full_snapshots(agent):
    result=agent.engine.answer('2025年各地区销售额').to_dict()
    proof=saved_sql_context(result['question'],result)
    result['provenance']['result_completeness']='limit_reached_total_unknown'
    assert build_snapshot(result,proof,agent.engine) is None
    result['provenance']['result_completeness']='within_return_limit'
    result['result_state']='partial_rows'
    assert build_snapshot(result,proof,agent.engine) is None


def test_complete_result_request_with_small_materialized_rows_is_comparable(agent):
    for question in ['2024年各渠道销售额','2025年各渠道销售额']:
        assert agent.query(question,session_id='comparison',complete_results=True)['status']=='ok'
    assert agent.query('两者差多少',session_id='comparison')['status']=='ok'


def test_missing_month_groups_are_not_filled_with_zero_and_alignment_is_explicit(agent):
    result=query_pair(agent,'2024年销售额趋势 按月','2025年销售额趋势 按月')
    assert result['status']=='clarification' and result['result']['clarification_code']=='comparison_no_pairs'
    assert result['result']['comparison_actions'][0]['question']=='按月份对齐'
    result=agent.query('按月份对齐',session_id='comparison')
    assert result['status']=='ok'
    for row in result['result']['rows']:
        if row['基准值'] is None or row['比较值'] is None:
            assert row['差值'] is None and row['变化率(%)'] is None
    agent.query('把基准換成较晚结果'.replace('換','换'),session_id='comparison')
    result=agent.query('两者差多少',session_id='comparison')
    assert result['result']['comparison_evidence']['alignment']=='month_of_year'
    assert result['result']['comparison_evidence']['baseline']=='较晚查询'


@pytest.mark.parametrize('value',[0])
def test_zero_and_negative_baselines_keep_difference_without_default_growth_rate(agent,value):
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute("UPDATE sales_orders SET sales_amount=? WHERE region='华东'",[value])
    result=query_pair(agent)
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['差值']==row['比较值']-row['基准值']
    assert row['变化率(%)'] is None
    assert any('零或负数' in notice for notice in result['result']['notices'])


def test_negative_baseline_snapshot_has_no_default_growth_rate(agent):
    # Sales schema prohibits negative amounts; exercise the comparison arithmetic
    # with a controlled server-owned snapshot instead of invalid database input.
    query_pair(agent)
    state=deepcopy(agent.conversations.context('comparison')[-1].state)
    context=unseal(state['comparison_context'])
    snapshot=unseal(context['sources'][0]['snapshot'])
    snapshot['rows'][0]['销售额']=-1
    context['sources'][0]['snapshot']=seal(snapshot)
    state['comparison_context']=seal(context)
    agent.conversations.remember('comparison',question='test negative',effective_question='test negative',state=state)
    result=agent.query('两者差多少',session_id='comparison')
    row=result['result']['rows'][0]
    assert row['差值']==row['比较值']+1
    assert row['变化率(%)'] is None


def test_unmatched_groups_are_visible_and_nulls_are_not_zero(agent):
    result=query_pair(agent,'2025年华东销售额 按渠道','2025年华南销售额 按渠道')
    assert result['status']=='ok'
    turns=agent.conversations.context('comparison')
    state=deepcopy(turns[-1].state)
    context=unseal(state['comparison_context'])
    snapshot=unseal(context['sources'][0]['snapshot'])
    snapshot['rows']=[snapshot['rows'][0]]
    context['sources'][0]['snapshot']=seal(snapshot)
    state['comparison_context']=seal(context)
    agent.conversations.remember('comparison',question='test subset',effective_question='test subset',state=state)
    result=agent.query('两者差多少',session_id='comparison')
    assert result['status']=='ok'
    missing=next(row for row in result['result']['rows'] if row['基准值'] is None)
    assert missing['差值'] is None and missing['变化率(%)'] is None
    assert '仅比较结果' in missing['对应状态']


def test_duplicate_months_across_years_cannot_be_collapsed_without_user_scope(agent):
    # Independent full SQL results already contain two years for the same month.
    query_pair(agent,'按月销售额趋势','按月销售额趋势')
    result=agent.query('按月份对齐',session_id='comparison')
    assert result['status']=='clarification'
    assert result['result']['clarification_code']=='comparison_duplicate_group'


def test_unspecified_operand_is_clarified_then_selected_without_losing_year(agent,monkeypatch):
    query_pair(agent)
    original=agent.engine.answer
    calls=[]
    def tracked(question,**kwargs):
        calls.append(question)
        return original(question,**kwargs)
    monkeypatch.setattr(agent.engine,'answer',tracked)
    result=agent.query('那华北呢',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_edit_role_required'
    assert calls==[]
    assert [a['question'] for a in result['result']['comparison_actions']]==['基准值','比较值']
    result=agent.query('比较值',session_id='comparison')
    assert result['status']=='ok'
    assert calls==['2025年华北销售额']
    row=result['result']['rows'][0]
    assert row['基准值']==29584
    assert row['比较值']==original('2025年华北销售额').rows[0]['销售额']


def test_explicit_operand_edit_keeps_other_source_and_survives_restart(agent):
    query_pair(agent)
    agent.query('把基准换成较晚结果',session_id='comparison')
    reopened=OmniAgent(agent.engine,agent.knowledge,ConversationStore(storage_path=agent.conversations.storage_path))
    result=reopened.query('把比较值改成2024年',session_id='comparison')
    assert result['status']=='ok'
    assert result['result']['comparison_evidence']['baseline']=='较晚查询'
    assert result['result']['edit_evidence']['replacement_question']=='2024年华东销售额'
    row=result['result']['rows'][0]
    assert row['基准值']==22992
    assert row['比较值']==agent.engine.answer('2024年华东销售额').rows[0]['销售额']


def test_failed_edit_preserves_original_comparison_without_new_execution(agent,monkeypatch):
    query_pair(agent)
    def forbidden(*args,**kwargs):raise AssertionError('unknown exclusion must not execute')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    result=agent.query('把比较值改成排除华南',session_id='comparison')
    assert result['status']=='clarification'
    assert result['result']['clarification_code']=='comparison_edit_scope_unverified'
    result=agent.query('两者差多少',session_id='comparison')
    assert result['status']=='ok' and result['result']['rows'][0]['差值']==-6592


def test_complete_new_query_after_comparison_is_independent(agent):
    query_pair(agent)
    result=agent.query('2024年华北销售额呢',session_id='comparison')
    assert result['route']=='sql' and result['status']=='ok'
    assert result['result']['rows']==list(agent.engine.answer('2024年华北销售额').rows)


def test_pending_operand_selection_is_session_scoped(agent):
    query_pair(agent)
    agent.query('那华北呢',session_id='comparison')
    result=agent.query('把比较值改成华北',session_id='another')
    assert result['status']=='clarification'
    assert result['result']['rows']==[]


def test_document_topic_switch_is_not_mistaken_for_operand_selection(agent):
    query_pair(agent)
    result=agent.query('那保修政策呢',session_id='comparison')
    assert result['route']=='document'


def test_edit_verifies_source_revision_before_any_replacement_query(agent,monkeypatch):
    query_pair(agent)
    with sqlite3.connect(agent.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id=(SELECT order_id FROM sales_orders LIMIT 1)')
    def forbidden(*args,**kwargs):raise AssertionError('changed source must not execute')
    monkeypatch.setattr(agent.engine,'answer',forbidden)
    result=agent.query('把比较值改成华北',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_source_changed'


def test_failed_replacement_execution_keeps_previous_complete_sources(agent,monkeypatch):
    query_pair(agent)
    original=agent.engine.answer
    def incomplete(question,**kwargs):
        value=original(question,**kwargs)
        return replace(value,status='incomplete',result_state='partial_rows')
    monkeypatch.setattr(agent.engine,'answer',incomplete)
    result=agent.query('把比较值改成华北',session_id='comparison')
    assert result['result']['clarification_code']=='comparison_edit_query_not_complete'
    result=agent.query('两者差多少',session_id='comparison')
    assert result['status']=='ok' and result['result']['rows'][0]['差值']==-6592


def test_explicit_complete_operand_replacement_does_not_readd_old_filters(agent):
    query_pair(agent)
    result=agent.query('把比较查询改成2024年华北销售额',session_id='comparison')
    assert result['status']=='ok'
    assert result['result']['edit_evidence']['replacement_question']=='2024年华北销售额'
    assert result['result']['rows'][0]['比较值']==agent.engine.answer('2024年华北销售额').rows[0]['销售额']
    assert result['result']['rows'][0]['基准值']==29584


def nested_catalog(agent):
    from backend.nl2sql.semantics import MetricCatalog
    payload=deepcopy(agent.engine.metric_catalog.payload)
    base=next(item for item in payload['derived_metrics'] if '客单价' in item['aliases'])
    payload['derived_metrics'].append({'id':'double_aov','label':'双倍客单价',
        'aliases':['双倍客单价'],'expression':{'op':'multiply','left':{'ref':base['id']},'right':{'constant':2}}})
    agent.engine.metric_catalog=MetricCatalog(payload)
    return base['id']


def test_nested_derived_query_comparison_uses_executed_formula_chain(agent):
    nested_catalog(agent)
    result=query_pair(agent,'2025年华东双倍客单价','2025年华南双倍客单价')
    assert result['status']=='ok'
    row=result['result']['rows'][0]
    assert row['指标']=='双倍客单价' and row['单位']=='CNY'
    with sqlite3.connect(agent.engine.database_path) as connection:
        expected=[connection.execute(
            "SELECT SUM(sales_amount)*2.0/COUNT(*) FROM sales_orders WHERE substr(order_date,1,4)='2025' AND region=?",
            [region]).fetchone()[0] for region in ['华东','华南']]
    assert row['基准值']==pytest.approx(expected[0])
    assert row['比较值']==pytest.approx(expected[1])
    assert row['差值']==pytest.approx(expected[1]-expected[0])
    assert row['差值']==pytest.approx(row['比较值']-row['基准值'])
    snapshot=unseal(agent.conversations.context('comparison')[-1].state['comparison_context'])
    contract=unseal(snapshot['sources'][0]['snapshot'])['metrics'][0]['contract']
    assert contract['expression']['left']['kind']=='derived'
    assert contract['expression']['left']['expression']['op']=='divide'


@pytest.mark.parametrize('fault',['cycle','undefined','duplicate','missing_audit','changed_nested_unit'])
def test_nested_formula_records_are_checked_at_every_dependency(agent,fault):
    base_id=nested_catalog(agent)
    result=agent.engine.answer('2025年华东双倍客单价').to_dict()
    proof=saved_sql_context(result['question'],result)
    assert build_snapshot(result,proof,agent.engine)
    plan=result['plan']
    base=next(d for d in plan['derived_metrics'] if d['id']==base_id)
    if fault in {'cycle','undefined'}:
        base['expression']={'ref':'double_aov' if fault=='cycle' else 'unknown'}
        next(f for f in plan['grain_audit']['formulas'] if f['metric_id']==base_id)['expression']=base['expression']
    elif fault=='duplicate':plan['derived_metrics'].append(deepcopy(base))
    elif fault=='missing_audit':
        plan['grain_audit']['formulas']=[f for f in plan['grain_audit']['formulas'] if f['metric_id']!=base_id]
    else:
        before=unseal(build_snapshot(result,proof,agent.engine))
        next(f for f in plan['grain_audit']['formulas'] if f['metric_id']==base_id)['unit']='unknown'
        after=unseal(build_snapshot(result,proof,agent.engine))
        assert before['metrics'][0]['contract']!=after['metrics'][0]['contract']
        return
    assert build_snapshot(result,proof,agent.engine) is None


def test_multiple_metric_results_compare_each_physical_contract(agent):
    result=query_pair(agent,'2025年华东销售额和订单数','2025年华南销售额和订单数')
    assert result['status']=='ok'
    assert {r['指标'] for r in result['result']['rows']}=={'销售额','订单数'}
    for row in result['result']['rows']:
        assert row['差值']==row['比较值']-row['基准值']
