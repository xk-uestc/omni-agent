import pytest
import sqlite3
from contextlib import closing
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'engine',Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')))
    monkeypatch.setattr(app_module,'knowledge_store',KnowledgeStore(tmp_path/'knowledge'))
    monkeypatch.setattr(app_module,'conversation_store',ConversationStore(storage_path=tmp_path/'sessions.sqlite'))
    monkeypatch.setattr(app_module,'generation_client',None)
    return TestClient(app_module.app)


def pending(client):
    question='2025年华东地区的情况'
    answer=client.post('/api/v1/omni/query',json={'question':question,'session_id':'clarify'}).json()
    assert answer['route']=='sql' and answer['status']=='clarification'
    assert answer['result']['clarification_code']=='missing_metric'
    return {'original_question':answer['effective_question'],'session_id':'clarify',
            'clarification_code':'missing_metric','selected_value':'sales_amount'}


def test_unified_clarification_retains_state_and_next_followup(client):
    request=pending(client)
    response=client.post('/api/v1/omni/clarify',json=request)
    assert response.status_code==200
    answer=response.json()
    assert answer['status']=='ok' and answer['result']['rows']==[{'销售额':29584.0}]
    turns=app_module.conversation_store.context('clarify')
    assert len(turns)==2 and turns[-1].state['route']=='sql'
    followup=client.post('/api/v1/omni/query',json={'question':'那华南呢','session_id':'clarify'}).json()
    assert followup['status']=='ok' and '华南' in followup['effective_question']
    assert '华东' not in followup['effective_question'] and '2025年' in followup['effective_question']


@pytest.mark.parametrize('change',[{'selected_value':'fake_column'},{'clarification_code':'ambiguous_value'}])
def test_forged_selection_is_rejected(client,change):
    request=pending(client);request.update(change)
    assert client.post('/api/v1/omni/clarify',json=request).status_code==400
    assert len(app_module.conversation_store.context('clarify'))==1


def test_client_cannot_override_offered_label(client):
    request=pending(client);request['selected_label']='订单数'
    answer=client.post('/api/v1/omni/clarify',json=request).json()
    assert answer['result']['plan']['metric_column']=='sales_amount'


def test_old_selection_is_stale_after_new_question(client):
    request=pending(client)
    client.post('/api/v1/omni/query',json={'question':'2024年销售额','session_id':'clarify'})
    assert client.post('/api/v1/omni/clarify',json=request).status_code==409


def test_document_region_question_remains_document_route(client):
    app_module.knowledge_store.ingest('华东团队经验为先核对需求。'.encode(),document_id='experience',title='经验',modality='txt',filename='e.txt')
    answer=client.post('/api/v1/omni/query',json={'question':'华东团队经验是什么'}).json()
    assert answer['route']=='document' and answer['status']=='ok'


def test_duplicate_metric_names_offer_distinct_qualified_choices(client):
    request=pending(client)
    offered=app_module.engine.answer(request['original_question']).to_dict()['clarification_options']
    assert len({o['value'] for o in offered})==len(offered)
    assert len({o['label'] for o in offered})==len(offered)
    assert {'customers.customer_id','sales_orders.customer_id'} <= {o['value'] for o in offered}
    request['selected_value']='customers.customer_id'
    answer=client.post('/api/v1/omni/clarify',json=request).json()
    assert answer['result']['plan']['metric_table']=='customers'
    assert answer['result']['plan']['metric_column']=='customer_id'


@pytest.mark.parametrize('selection,expression',[
    ('sales_amount','SUM(sales_amount)'),('quantity','SUM(quantity)'),
    ('unit_price','AVG(unit_price)'),('order_id','COUNT(*)'),
    ('sales_orders.customer_id','COUNT(DISTINCT customer_id)'),
])
def test_every_offered_demo_metric_executes_against_sql_gold(client,selection,expression):
    request=pending(client);request['selected_value']=selection
    response=client.post('/api/v1/omni/clarify',json=request)
    assert response.status_code==200
    answer=response.json()
    with closing(sqlite3.connect(app_module.engine.database_path)) as connection:
        expected=connection.execute(f"SELECT {expression} FROM sales_orders WHERE region='华东' AND order_date>='2025-01-01' AND order_date<'2026-01-01'").fetchone()[0]
    assert answer['status']=='ok'
    assert list(answer['result']['rows'][0].values())[0]==pytest.approx(expected)


def test_customer_master_date_is_not_invented_from_order_date(client):
    request=pending(client);request['selected_value']='customers.customer_id'
    answer=client.post('/api/v1/omni/clarify',json=request).json()
    assert answer['status']=='clarification'
    assert answer['result']['clarification_code']=='missing_date_column'
    assert not answer['result']['rows']


def test_demo_price_contract_and_unimplemented_weighting(client):
    engine=app_module.engine
    average=engine.answer('2025年华东单价')
    assert average.status=='ok' and average.plan['metric_function']=='AVG'
    assert average.plan['metric_label']=='平均单价'
    total=engine.answer('2025年华东单价合计')
    assert total.status=='ok' and total.plan['metric_function']=='SUM'
    assert engine.answer('2025年华东加权平均单价').status=='clarification'


@pytest.mark.parametrize('product',['星河 Pro','星河 Lite'])
def test_entity_prefix_selection_matches_exact_product_sql(client,product):
    question='星河的销售额'
    pending=client.post('/api/v1/omni/query',json={'question':question,'session_id':'entity'}).json()
    assert pending['result']['clarification_code']=='ambiguous_value'
    option=next(o for o in pending['result']['clarification_options'] if o['value'].endswith('=>'+product))
    response=client.post('/api/v1/omni/clarify',json={'original_question':question,'session_id':'entity',
        'clarification_code':'ambiguous_value','selected_value':option['value']})
    answer=response.json()
    with closing(sqlite3.connect(app_module.engine.database_path)) as connection:
        expected=connection.execute('SELECT SUM(sales_amount) FROM sales_orders WHERE product_name=?',(product,)).fetchone()[0]
    assert response.status_code==200 and answer['status']=='ok'
    assert answer['result']['rows'][0]['销售额']==expected


@pytest.mark.parametrize('field,expected',[
    ('Invoice.BillingCountry',{'GB':30,'CA':35}),('Customer.Country',{'US':50,'CA':15}),
])
def test_dimension_role_choice_changes_query_result(client,tmp_path,monkeypatch,field,expected):
    db=tmp_path/'roles.sqlite'
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript("CREATE TABLE Customer(CustomerId INTEGER PRIMARY KEY,Country TEXT);"
          "CREATE TABLE Invoice(InvoiceId INTEGER PRIMARY KEY,CustomerId INTEGER REFERENCES Customer(CustomerId),Total REAL,BillingCountry TEXT);"
          "INSERT INTO Customer VALUES(1,'US'),(2,'CA');"
          "INSERT INTO Invoice VALUES(10,1,30,'GB'),(11,1,20,'CA'),(12,2,15,'CA');")
    monkeypatch.setattr(app_module,'engine',Nl2SqlEngine(db))
    question='按国家统计订单金额'
    pending=client.post('/api/v1/omni/query',json={'question':question,'session_id':'role'}).json()
    assert pending['result']['clarification_code']=='ambiguous_dimension'
    assert field in {o['value'] for o in pending['result']['clarification_options']}
    response=client.post('/api/v1/omni/clarify',json={'original_question':question,'session_id':'role',
        'clarification_code':'ambiguous_dimension','selected_value':field})
    answer=response.json();result=answer['result']
    assert response.status_code==200 and answer['status']=='ok'
    dimension=field.split('.')[1]
    assert {row[dimension]:row[result['plan']['metric_label']] for row in result['rows']}==expected


@pytest.mark.parametrize('question,selection,time_value',[
    ('销售额同比','year','2025'),('销售额环比','month','2025-03'),
    ('销售额趋势','year','2025'),('销售额趋势','month','2025年3月'),
])
def test_time_editor_selection_replans_with_explicit_period(client,question,selection,time_value):
    pending=client.post('/api/v1/omni/query',json={'question':question,'session_id':'period'}).json()
    assert pending['status']=='clarification'
    response=client.post('/api/v1/omni/clarify',json={'original_question':question,'session_id':'period',
        'clarification_code':pending['result']['clarification_code'],'selected_value':selection,'selected_time':time_value})
    assert response.status_code==200
    answer=response.json()
    if '趋势' in question:
        assert answer['status']=='clarification'
        assert answer['result']['clarification_code']=='missing_time_grain'
        answer=client.post('/api/v1/omni/clarify',json={
            'original_question':answer['effective_question'],'session_id':'period',
            'clarification_code':'missing_time_grain','selected_value':'monthly_trend'}).json()
        assert answer['result']['plan']['dimension_transforms']['order_date']=='month'
    assert answer['status']=='ok'
    assert '2025年' in answer['effective_question']
    if selection=='month':
        assert '3月' in answer['effective_question']


@pytest.mark.parametrize('time_value',['2025-13','2025-03;DROP TABLE x','now','0000-01'])
def test_invalid_time_editor_values_do_not_consume_pending_turn(client,time_value):
    question='销售额环比'
    pending=client.post('/api/v1/omni/query',json={'question':question,'session_id':'period'}).json()
    response=client.post('/api/v1/omni/clarify',json={'original_question':question,'session_id':'period',
        'clarification_code':pending['result']['clarification_code'],'selected_value':'month','selected_time':time_value})
    assert response.status_code==400
    assert len(app_module.conversation_store.context('period'))==1


def test_explicit_year_trend_is_not_replaced_by_month_grain(client):
    result=app_module.engine.answer('按年销售额趋势')
    assert result.status=='ok'
    assert result.plan['dimension_transforms']['order_date']=='year'
    with closing(sqlite3.connect(app_module.engine.database_path)) as connection:
        expected=dict(connection.execute("SELECT substr(order_date,1,4),SUM(sales_amount) FROM sales_orders GROUP BY 1"))
    assert {str(row[result.columns[0]]):row['销售额'] for row in result.rows}==expected
    assert app_module.engine.answer('按年按月销售额趋势').status=='clarification'


@pytest.mark.parametrize('question,grain',[
    ('2025年按月销售额趋势','month'),('按年销售额趋势','year'),
    ('2025年按月销售额和销量趋势','month'),('按年销售额和销量趋势','year'),
])
def test_trend_rows_are_chronological_with_actual_sql_gold(client,question,grain):
    result=app_module.engine.answer(question)
    assert result.status=='ok'
    periods=[str(row[result.columns[0]]) for row in result.rows]
    assert periods==sorted(periods)
    assert result.plan['dimension_transforms']['order_date']==grain
    with closing(sqlite3.connect(app_module.engine.database_path)) as connection:
        length=7 if grain=='month' else 4
        where=" WHERE substr(order_date,1,4)='2025'" if grain=='month' else ''
        expected=dict(connection.execute(f'SELECT substr(order_date,1,{length}),SUM(sales_amount) FROM sales_orders{where} GROUP BY 1'))
    assert {str(row[result.columns[0]]):row['销售额'] for row in result.rows}==expected


def test_explicit_month_ranking_still_orders_by_metric(client):
    result=app_module.engine.answer('2025年按月销售额排名')
    assert result.status=='ok'
    values=[row['销售额'] for row in result.rows]
    assert values==sorted(values,reverse=True)
