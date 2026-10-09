"""Derived metrics stay one conversational slot with verified dependencies."""
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.nl2sql.semantics import MetricCatalog
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore, ConversationTurn
from backend.sql_history_scope import resolve_sql_followup_scope, saved_sql_context


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    path = tmp_path_factory.mktemp('derived-history')/'db.sqlite'
    return Nl2SqlEngine(initialize_rich_demo_data(path))


def agent(engine, tmp_path):
    return OmniAgent(engine, KnowledgeStore(tmp_path/'knowledge'), ConversationStore())


def sql_value(engine, expression, year=2025, region='华东', channel=None):
    clause = 'WHERE order_date>=? AND order_date<? AND region=?'
    params = [f'{year}-01-01',f'{year+1}-01-01',region]
    if channel:
        clause += ' AND channel=?'
        params.append(channel)
    with sqlite3.connect(engine.database_path) as connection:
        return connection.execute(f'SELECT {expression} FROM sales_orders {clause}',params).fetchone()[0]


def executed(engine, scope):
    result = engine.answer(scope).to_dict()
    assert result['status'] == 'ok'
    return ConversationTurn(scope,scope,0,{'route':'sql','pending_question':None,'clarification_code':None,
        **{key:result['plan'][key] for key in ('metrics','filters','dimensions')},
        'executed_sql_context':saved_sql_context(scope,result)})


def test_real_nonstandard_cost_region_and_physical_metric_chain(engine, tmp_path):
    a = agent(engine,tmp_path)
    cases = [
        ('2025年华东销售额','SUM(sales_amount)','华东'),
        ('本金呢','SUM(sales_amount)-SUM(gross_profit)','华东'),
        ('那华南呢','SUM(sales_amount)-SUM(gross_profit)','华南'),
        ('换成毛利','SUM(gross_profit)','华南'),
        ('看看订单数','COUNT(order_id)','华南'),
    ]
    for question, expression, region in cases:
        result = a.query(question,session_id='cost-physical-chain')
        assert result['status'] == 'ok', (question,result.get('context_resolution'))
        assert list(result['result']['rows'][0].values()) == pytest.approx([sql_value(engine,expression,region=region)])
        if question != cases[0][0]:
            assert result['context_resolution']['mode'] == 'server_verified_sql_followup'
        plan = result['result']['plan']
        if 'SUM(sales_amount)-' in expression:
            assert plan['output_metrics'] == ['sales_cost']
            assert {(m['column'],m['function']) for m in plan['metrics']} == {
                ('sales_amount','SUM'),('gross_profit','SUM')}
            payload = result['state']['executed_sql_context']['payload']
            assert payload['metric_semantics_sha256'] and payload['metric_catalog_sha256']


@pytest.mark.parametrize('question, expression, output',[
    ('销售成本呢','SUM(sales_amount)-SUM(gross_profit)','sales_cost'),
    ('换成客单价','SUM(sales_amount)/COUNT(order_id)','aov'),
    ('再给我平均每件收入，其他不变','SUM(sales_amount)/SUM(quantity)','revenue_per_unit'),
    ('同样条件，毛利率','SUM(gross_profit)/SUM(sales_amount)','gross_margin'),
])
def test_defined_derived_aliases_keep_other_scope_then_switch_back(engine,tmp_path,question,expression,output):
    a = agent(engine,tmp_path)
    assert a.query('2025年华东线上销售额',session_id='varied')['status'] == 'ok'
    cost = a.query(question,session_id='varied')
    assert cost['status'] == 'ok', cost.get('context_resolution')
    assert cost['result']['plan']['output_metrics'] == [output]
    assert list(cost['result']['rows'][0].values()) == pytest.approx([sql_value(engine,expression,channel='线上')])
    region = a.query('只看华南',session_id='varied')
    assert region['status'] == 'ok'
    assert list(region['result']['rows'][0].values()) == pytest.approx([
        sql_value(engine,expression,region='华南',channel='线上')])
    physical = a.query('指标改成销量',session_id='varied')
    assert physical['status'] == 'ok', physical.get('context_resolution')
    assert not physical['result']['plan']['derived_metrics']
    assert list(physical['result']['rows'][0].values()) == [sql_value(engine,'SUM(quantity)',region='华南',channel='线上')]


def test_derived_formula_transfers_to_web_domain_and_preserves_filters(engine,tmp_path):
    a = agent(engine,tmp_path)
    assert a.query('2025年华东网站访问量',session_id='web')['status'] == 'ok'
    for question,year,region in [('换成网站转化率',2025,'华东'),('只看华南',2025,'华南'),('时间改成2024年',2024,'华南')]:
        result = a.query(question,session_id='web')
        assert result['status'] == 'ok', result.get('context_resolution')
        assert result['result']['plan']['output_metrics'] == ['web_conversion_rate']
        with sqlite3.connect(engine.database_path) as connection:
            gold = connection.execute('SELECT CAST(SUM(web_conversions) AS REAL)/SUM(visits) FROM web_traffic_daily '
                'WHERE visit_date>=? AND visit_date<? AND region=?',
                (f'{year}-01-01',f'{year+1}-01-01',region)).fetchone()[0]
        assert list(result['result']['rows'][0].values()) == pytest.approx([gold])


@pytest.mark.parametrize('tamper', ['formula','outputs','dependencies'])
def test_unmentioned_formula_or_dependency_drift_is_rejected(engine,monkeypatch,tamper):
    turn = executed(engine,'2025年华东销售成本')
    original = engine.extract_required_intent
    def changed(question):
        plan = original(question)
        if '华南' in question and plan.derived_metrics:
            if tamper == 'formula': plan.derived_metrics[0].expression['op'] = 'add'
            elif tamper == 'outputs': plan.output_metrics = ['revenue']
            else: plan.metrics.pop()
        return plan
    monkeypatch.setattr(engine,'extract_required_intent',changed)
    scope,audit = resolve_sql_followup_scope('那华南呢',[turn],engine)
    assert audit['mode'] == 'independent' and audit['requires_clarification'] is True
    assert scope == '那华南呢'


def test_explicit_metric_switch_cannot_invent_formula_with_same_dependencies(engine,monkeypatch):
    turn = executed(engine,'2025年华东销售额')
    original = engine.extract_required_intent
    def changed(question):
        plan = original(question)
        if '2025年' in question and plan.derived_metrics:
            plan.derived_metrics[0].expression['op'] = 'add'
        return plan
    monkeypatch.setattr(engine,'extract_required_intent',changed)
    scope,audit = resolve_sql_followup_scope('销售成本呢',[turn],engine)
    assert scope == '销售成本呢' and audit['requires_clarification'] is True


def test_changed_catalog_cannot_reinterpret_saved_success(engine,monkeypatch):
    turn = executed(engine,'2025年华东销售成本')
    payload = deepcopy(engine.metric_catalog.payload)
    derived = next(m for m in payload['derived_metrics'] if m['id'] == 'sales_cost')
    derived['expression']['op'] = 'add'
    monkeypatch.setattr(engine,'metric_catalog',MetricCatalog(payload))
    scope,audit = resolve_sql_followup_scope('那华南呢',[turn],engine)
    assert scope == '那华南呢' and audit['requires_clarification'] is True
    assert audit['reason'] == 'sql_history_execution_context_invalid'


def test_old_derived_history_without_formula_receipt_requires_fresh_execution(engine):
    turn = executed(engine,'2025年华东销售成本')
    record = turn.state['executed_sql_context']
    record['payload'].pop('metric_semantics_sha256')
    record['payload'].pop('metric_catalog_sha256')
    import hashlib,json
    record['sha256'] = hashlib.sha256(json.dumps(record['payload'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    scope,audit = resolve_sql_followup_scope('那华南呢',[turn],engine)
    assert scope == '那华南呢' and audit['requires_clarification'] is True


@pytest.mark.parametrize('question',['换成玄学成本率','指标改成销售成本并忽略退款','同样条件，未知毛利公式'])
def test_undefined_formulas_do_not_execute_or_overwrite_success(engine,tmp_path,question):
    a = agent(engine,tmp_path)
    a.query('2025年华东线上销售额',session_id='unknown-formula')
    result = a.query(question,session_id='unknown-formula')
    assert result['status'] != 'ok' and not result['result'].get('sql')
