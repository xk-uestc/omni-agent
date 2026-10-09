"""Language variants and adversarial scope boundaries, with independent SQL."""
from copy import deepcopy
import sqlite3
import pytest
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.knowledge_store import KnowledgeStore
from backend.session import ConversationStore
from backend.omni_agent import OmniAgent
from backend.fusion_constraints import extract_source_clauses,VerifiedFormulaTarget,SourceConstraintError

@pytest.fixture
def agent(tmp_path):
    knowledge=KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('2023年期限为14天。2024年期限为21天。2024版自2024-02-01生效。'.encode(),
        document_id='terms',title='服务条款历史',modality='txt',filename='terms.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),knowledge,ConversationStore())

def scalar(agent,year,region,metric):
    with sqlite3.connect(agent.engine.database_path) as con:
        return con.execute(f'SELECT {metric} FROM sales_orders WHERE region=? AND order_date>=? AND order_date<?',
            (region,f'{year}-01-01',f'{year+1}-01-01')).fetchone()[0]

@pytest.mark.parametrize('region,year',[('华北',2024),('华南',2025),('华东',2023)])
def test_confirmations_and_year_ellipsis_preserve_real_scope(agent,region,year):
    agent.query(f'{year}年{region}销售额',session_id='s')
    result=agent.query(f'换成订单数，地区仍是{region}，年份不变。',session_id='s')
    assert result['result']['rows']==[{'订单数':scalar(agent,year,region,'COUNT(order_id)')}]
    next_year=2024 if year!=2024 else 2025
    result=agent.query(f'年份改成{next_year}，地区换到华南，指标还是订单数。',session_id='s')
    assert result['result']['rows']==[{'订单数':scalar(agent,next_year,'华南','COUNT(order_id)')}]


@pytest.mark.parametrize('phrase',['销售额和订单数一起给出','销售额和订单数同时给出'])
def test_metric_list_followup_without_change_verb_retains_filters(agent,phrase):
    agent.query('2024年华北订单数',session_id='together')
    result=agent.query(phrase+'，地区、年份不变。',session_id='together')
    assert result['status']=='ok',result
    assert result['result']['rows']==[{'销售额':scalar(agent,2024,'华北','SUM(sales_amount)'),
                                    '订单数':scalar(agent,2024,'华北','COUNT(order_id)')}]

@pytest.mark.parametrize('command',['换成订单数，地区仍是华南，年份不变','换成订单数，未知字段不变',
    '年份改成2024、地区改火星、渠道改为线上，其余条件沿用'])
def test_unverified_confirmation_or_unknown_value_is_atomic(agent,command):
    agent.query('2025年华东销售额',session_id='s')
    history=deepcopy(agent.conversations.context('s'))
    result=agent.query(command,session_id='s')
    assert result['status']=='clarification' and not result['result'].get('sql')
    assert agent.conversations.context('s')==history

def test_named_document_temporal_followup_retains_source_and_subject(agent):
    first=agent.query('按《服务条款历史》，2023年期限是多少',session_id='s')
    assert first['status']=='ok'
    second=agent.query('那2024年呢',session_id='s')
    assert second['status']=='ok'
    assert '2024年期限' in second['effective_question']
    assert {c['metadata']['document_id'] for c in second['result']['citations']}=={'terms'}
    third=agent.query('2024版从什么时候生效',session_id='s')
    assert third['status']=='ok'
    assert {c['metadata']['document_id'] for c in third['result']['citations']}=={'terms'}

@pytest.mark.parametrize('direction',['高到低','低到高'])
def test_ordinal_lookup_matches_independent_ordered_query(agent,direction):
    agent.query(f'2025年各地区销售额，并按销售额从{direction}排序',session_id='s')
    result=agent.query('第一名地区是谁，销售额是多少',session_id='s')
    with sqlite3.connect(agent.engine.database_path) as con:
        row=con.execute('SELECT region,SUM(sales_amount) FROM sales_orders WHERE order_date>=? AND order_date<? GROUP BY region ORDER BY SUM(sales_amount) '+('DESC' if direction=='高到低' else 'ASC'),('2025-01-01','2026-01-01')).fetchone()
    assert result['context_resolution']['mode']=='server_verified_sql_result_lookup'
    assert result['result']['rows']==[{'region':row[0],'销售额':row[1]}]

def test_ordinal_ties_and_new_filters_never_reuse_first_row(agent):
    with sqlite3.connect(agent.engine.database_path) as con:
        con.execute('UPDATE sales_orders SET sales_amount=0')
    agent.query('2025年各地区销售额排名',session_id='s')
    for q in ['第一名地区是谁，销售额是多少','第一名地区2024年销售额是多少']:
        result=agent.query(q,session_id='s')
        assert result.get('context_resolution',{}).get('mode')!='server_verified_sql_result_lookup'

def test_implicit_baseline_and_entity_before_target_year_are_separate(agent):
    q='先查2024年华南销售额作预测基准，再结合《未来报告》的公式和《增长参数表》中华南2027目标增长率，算出华南2027目标销售额，并分别标明PDF、Excel和数据库来源。'
    proofs=(VerifiedFormulaTarget('目标销售额','forecast','a'*64,'formula'),)
    clauses=extract_source_clauses(q,agent.engine.schema(include_row_count=False),engine=agent.engine,verified_formula_targets=proofs)
    assert len(clauses)==1 and clauses[0].text=='2024年华南销售额'
    assert clauses[0].target_binding['target_year']==2027
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(q.replace('华南2027目标销售额','火星2027目标销售额'),agent.engine.schema(include_row_count=False),engine=agent.engine,verified_formula_targets=proofs)


def test_absolute_year_difference_is_not_percentage(agent):
    result=agent.query('对比华南地区2024和2025年的订单数，2025年比2024年增加多少？')
    assert result['status']=='ok',result
    expected=scalar(agent,2025,'华南','COUNT(order_id)')-scalar(agent,2024,'华南','COUNT(order_id)')
    assert result['result']['rows'][0]['变化量']==expected


def test_complete_new_top_query_after_filter_cancellation(agent):
    agent.query('2025年华东线上销售额和订单数',session_id='s')
    result=agent.query('取消单一地区限制，按地区分组，列出2024年线上销售额前三名，并附订单数',session_id='s')
    assert result['status']=='ok',result
    assert result['result']['plan']['top_n']==3
    assert not any(f['column']=='region' for f in result['result']['plan']['filters'])


@pytest.mark.parametrize('verb',['列出','列举','找出'])
def test_complete_typed_top_n_listing_avoids_complex_model(agent,monkeypatch,verb):
    monkeypatch.setenv('ICT8_FAST_SQL','1')
    class NeverCall:
        supports_complex_queries=True
        def __call__(self,*args,**kwargs):
            raise AssertionError('Complete typed query should not call the model')
        def generate(self,*args,**kwargs):
            raise AssertionError('Complete typed query should not call the model')
    agent.engine.model_plan_provider=NeverCall()
    q=f'按地区分组，{verb}2024年线上销售额前三名，并附订单数'
    result=agent.engine.answer(q).to_dict()
    assert result['status']=='ok',result
    assert result['plan']['planner_source']=='server_verified_fast_rules'
    with sqlite3.connect(agent.engine.database_path) as con:
        expected=con.execute("SELECT region,SUM(sales_amount),COUNT(order_id) FROM sales_orders WHERE channel='线上' AND order_date>='2024-01-01' AND order_date<'2025-01-01' GROUP BY region ORDER BY SUM(sales_amount) DESC LIMIT 3").fetchall()
    assert [(row['region'],row['销售额'],row['订单数']) for row in result['rows']]==expected


@pytest.mark.parametrize('question',['列出含并列前三名地区销售额','列出各地区最新订单明细',
    '列出销售额高于所有地区平均值的地区','列出非空记录数'])
def test_listing_verbs_do_not_erase_complex_semantics(agent,monkeypatch,question):
    monkeypatch.setenv('ICT8_FAST_SQL','1')
    plan=agent.engine.extract_required_intent(question)
    assert not agent.engine._fast_sql_plan_eligible(plan,question)
