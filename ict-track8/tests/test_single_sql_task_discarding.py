"""Ignored redundant task text cannot change execution; no actual API calls."""
import json
import pytest

from backend.omni_agent import OmniAgent, explicit_cross_source_request
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


QUESTION='2025年华东地区销售额'


def agent_for(tmp_path, tasks, *, route='sql', effective=QUESTION):
    class Planner:
        audit={'status':'completed','http_status':200}
        calls=0
        def generate(self,*args,**kwargs):
            self.calls+=1
            return {'route':route,'effective_question':effective,'clarification':'',
                    'tasks_json':json.dumps(tasks,ensure_ascii=False)}
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),
        KnowledgeStore(tmp_path/'knowledge'),ConversationStore(),Planner())


def sql_task(question):
    return {'id':'unused','tool':'sql','args':{'question':question}}


@pytest.mark.parametrize('rewrite',[
    '计算2025年华东地区的销售额合计',
    '2024年华南地区销售额，按交易日期分组，只保留大于100000的订单',
    '一个完全陌生的业务问题',
])
def test_changed_redundant_text_never_changes_original_execution(tmp_path,rewrite):
    result=agent_for(tmp_path,[sql_task(rewrite)],effective='2024年华北订单数').query(QUESTION)
    assert result['planner_source']=='model_validated'
    assert result['effective_question']==QUESTION and result['result']['rows']==[{'销售额':29584}]
    assert 'discarded_redundant_sql_read_not_executed' in result['trace'][0]['normalizations']
    assert not any(event.get('task_id')=='unused' for event in result['trace'])
    assert rewrite not in json.dumps(result['trace'],ensure_ascii=False)


def test_followup_ignores_redundant_text_and_uses_server_verified_slots(tmp_path):
    agent=agent_for(tmp_path,[sql_task('2024年华北订单数，按交易日期分组')])
    agent.query(QUESTION,session_id='followup')
    calls_before=agent.client.calls
    result=agent.query('那华南呢',session_id='followup')
    assert result['planner_source']=='rules_basic'
    assert agent.client.calls==calls_before
    assert result['result']['rows']==[{'销售额':22992}]
    assert result['context_resolution']['mode']=='server_verified_sql_followup'
    assert result['effective_question']=='2025年华南地区销售额'
    plan=result['result']['plan']
    assert (plan['table'],plan['metric_column'],plan['metric_function'])==('sales_orders','sales_amount','SUM')
    assert plan['dimensions']==[] and plan['having'] is None and plan['top_n'] is None
    filters={item['column']:item for item in plan['filters']}
    assert (filters['order_date']['operator'],tuple(filters['order_date']['value']))==('RANGE',('2025-01-01','2026-01-01'))
    assert (filters['region']['operator'],filters['region']['value'])==('=','华南')
    assert not any(event.get('task_id')=='unused' for event in result['trace'])


@pytest.mark.parametrize('rewrite',['DROP TABLE sales_orders','SELECT * FROM sales_orders','DELETE FROM sales_orders',
                                      'PRAGMA writable_schema=1','UPDATE sales_orders SET sales_amount=0'])
def test_raw_sql_is_rejected_and_report_contains_only_static_code(tmp_path,rewrite):
    result=agent_for(tmp_path,[sql_task(rewrite)]).query(QUESTION)
    assert result['planner_source']=='rules_fallback' and result['result']['rows']==[{'销售额':29584}]
    assert result['trace'][0]['error']=='GenerationError'
    assert result['trace'][0]['rejection_code']=='single_source_raw_sql_rejected'
    assert rewrite not in json.dumps(result['trace'])


@pytest.mark.parametrize('tasks',[
    [sql_task(QUESTION),{'id':'second','tool':'sql','args':{'question':QUESTION}}],
    [{'id':'comparison','tool':'compare','args':{}}],
    [{'id':'calculation','tool':'calculate','args':{}}],
    [sql_task('x'*1001)],
    [sql_task('')],
    [{'id':'unused','tool':'sql','args':{'question':QUESTION,'raw_sql':'SELECT 1'}}],
    [{'id':'unused','tool':'sql','args':{'question':['2025年',{'ref':'other','path':[]}]}}],
])
def test_non_discardable_graphs_bounds_and_args_remain_rejected(tmp_path,tasks):
    result=agent_for(tmp_path,tasks).query(QUESTION)
    assert result['planner_source']=='rules_fallback'
    assert 'discarded_redundant_sql_read_not_executed' not in result['trace'][0]['normalizations']
    assert result['trace'][0]['rejection_code'] in {'single_source_task_not_discardable','single_source_task_graph_invalid'}


@pytest.mark.parametrize('question',[
    '根据文档公式和数据库2025年华东销售额计算目标',
    '结合Excel参数与数据库销售额计算目标',
    '先从数据库查询销售额最高地区，再检索该地区操作经验',
    '先查询2025年销售额最高地区，然后搜索该地区规则',
])
def test_explicit_sources_and_dependency_operations_prevent_discard(tmp_path,question):
    assert explicit_cross_source_request(question)
    result=agent_for(tmp_path,[sql_task(QUESTION)],effective=question).query(question)
    assert result['planner_source']=='rules_fallback'
    assert result['trace'][0]['rejection_code']=='single_source_cross_source_request'
    assert 'discarded_redundant_sql_read_not_executed' not in result['trace'][0]['normalizations']


@pytest.mark.parametrize('question',[
    '2025年神秘业务ApprovedAmount合计', '比较2024年与2025年数据库销售额',
    '数据库和文档各自是什么', '按Customer.AccountNumber统计SalesOrderHeader.SubTotal',
])
def test_unknown_themes_and_sql_comparisons_do_not_invent_cross_source_intent(question):
    assert not explicit_cross_source_request(question)


def test_fusion_task_is_actually_executed_not_discarded(tmp_path):
    result=agent_for(tmp_path,[{'id':'actual','tool':'sql','args':{'question':QUESTION}}],route='fusion').query(QUESTION)
    assert result['planner_source']=='model_validated' and result['route']=='fusion'
    assert any(event.get('task_id')=='actual' and event.get('status')=='complete' for event in result['trace'])
    assert 'discarded_redundant_sql_read_not_executed' not in result['trace'][0]['normalizations']


def test_explicit_policy_comparison_cannot_be_replaced_by_redundant_sql(tmp_path):
    question='比较政策历史版本2022-12-31与2023-01-01的维修期限是否变化'
    result=agent_for(tmp_path,[sql_task(QUESTION)]).query(question)
    assert result['planner_source']=='rules_fallback' and result['route']=='clarify'
    assert result['trace'][0]['rejection_code']=='requested_operations_incomplete'
    assert 'discarded_redundant_sql_read_not_executed' not in result['trace'][0]['normalizations']
