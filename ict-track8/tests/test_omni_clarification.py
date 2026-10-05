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


def test_unified_query_and_clarification_forward_complete_results(client,monkeypatch):
    original=app_module.engine.answer
    observed=[]
    def recording(question,**kwargs):
        observed.append((question,kwargs.get('complete_results',False)))
        return original(question,**kwargs)
    monkeypatch.setattr(app_module.engine,'answer',recording)
    response=client.post('/api/v1/omni/query',json={
        'question':'2025年华东销售额','session_id':'complete','complete_results':True})
    assert response.status_code==200 and response.json()['status']=='ok'
    assert any(enabled for _,enabled in observed)
    selection=pending(client)
    observed.clear()
    selection['complete_results']=True
    response=client.post('/api/v1/omni/clarify',json=selection)
    assert response.status_code==200 and response.json()['status']=='ok'
    assert any(enabled for _,enabled in observed)


def test_model_free_text_clarification_keeps_server_time_options(client,monkeypatch):
    class ClarifyingClient:
        audit={'status':'completed','http_status':200}
        def generate(self,*args,**kwargs):
            return {'route':'clarify','effective_question':'销售额趋势',
                    'tasks_json':'[]','clarification':'请补充时间范围和粒度。'}
    monkeypatch.setattr(app_module,'generation_client',ClarifyingClient())
    def must_not_execute(*args,**kwargs):
        raise AssertionError('Missing trend choices must not enter model SQL execution')
    monkeypatch.setattr(app_module.engine,'answer',must_not_execute)
    body=client.post('/api/v1/omni/query',json={'question':'销售额趋势','session_id':'model-pending'}).json()
    assert body['route']=='sql' and body['status']=='clarification'
    assert body['result']['clarification_code']=='missing_time_range'
    assert 'year' in {option['value'] for option in body['result']['clarification_options']}
    assert body['result']['sql'] is None


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


def test_retained_pending_task_still_accepts_validated_ui_choice(client):
    original=pending(client)
    retained=client.post('/api/v1/omni/query',json={'question':'不知道','session_id':'clarify'}).json()
    assert retained['status']=='clarification' and retained['effective_question']==original['original_question']
    filled=client.post('/api/v1/omni/clarify',json=original)
    assert filled.status_code==200
    assert filled.json()['result']['rows']==[{'销售额':29584.0}]


def test_ui_choice_closes_original_pending_task(client):
    body=client.post('/api/v1/omni/query',json={'question':'2025年华东销售额趋势','session_id':'ui-close'}).json()
    completed=client.post('/api/v1/omni/clarify',json={'session_id':'ui-close',
        'original_question':body['effective_question'],'clarification_code':body['result']['clarification_code'],
        'selected_value':'monthly_trend'}).json()
    assert completed['status']=='ok'
    restored=client.post('/api/v1/omni/query',json={'session_id':'ui-close',
        'question':f"继续待补查询编号{body['query_reference_id']}"}).json()
    assert restored['result']['clarification_code']=='pending_reference_completed'


def test_ui_multi_choice_retains_one_task_lineage(client):
    body=client.post('/api/v1/omni/query',json={'question':'销售额趋势','session_id':'ui-lineage'}).json()
    identifiers=[body['query_reference_id']]
    for value, time_value in [('year','2025'),('monthly_trend',None)]:
        response=client.post('/api/v1/omni/clarify',json={'session_id':'ui-lineage',
            'original_question':body['effective_question'],'clarification_code':body['result']['clarification_code'],
            'selected_value':value,'selected_time':time_value})
        assert response.status_code==200, response.json()
        body=response.json()
        assert 'query_reference_id' in body, body
        identifiers.append(body['query_reference_id'])
    assert body['status']=='ok'
    for identifier in identifiers[:-1]:
        restored=client.post('/api/v1/omni/query',json={'session_id':'ui-lineage',
            'question':f'继续待补查询编号{identifier}'}).json()
        assert restored['result']['clarification_code']=='pending_reference_completed'


@pytest.mark.parametrize('reply',['按月统计','解释一下','时间改成2024年，按月统计',
    '时间改成2024年，指标改成订单数，按月统计'])
def test_pending_source_change_blocks_dependent_text_without_execution(client,reply,monkeypatch):
    client.post('/api/v1/omni/query',json={'question':'2025年华东销售额趋势','session_id':'source-change'})
    with sqlite3.connect(app_module.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE region=?',('华东',))
    def forbidden(*args,**kwargs):raise AssertionError('stale pending scope must not execute')
    monkeypatch.setattr(app_module.engine,'answer',forbidden)
    body=client.post('/api/v1/omni/query',json={'question':reply,'session_id':'source-change'}).json()
    assert body['status']=='clarification'
    assert body['result']['clarification_code']=='pending_source_changed'
    assert body['result']['sql'] is None


def test_pending_source_change_rejects_ui_and_reference_but_allows_new_query(client):
    body=client.post('/api/v1/omni/query',json={'question':'2025年华东销售额趋势','session_id':'source-change'}).json()
    with sqlite3.connect(app_module.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE region=?',('华东',))
    response=client.post('/api/v1/omni/clarify',json={'session_id':'source-change',
        'original_question':body['effective_question'],'clarification_code':body['result']['clarification_code'],
        'selected_value':'monthly_trend'})
    assert response.status_code==409
    old=client.post('/api/v1/omni/query',json={'session_id':'source-change',
        'question':f"继续待补查询编号{body['query_reference_id']}"}).json()
    assert old['result']['clarification_code']=='pending_reference_source_changed'
    fresh=client.post('/api/v1/omni/query',json={'session_id':'source-change','question':'2025年华东销售额 按月'}).json()
    assert fresh['status']=='ok'


def test_same_schema_new_database_cannot_reuse_pending_identity(client,tmp_path,monkeypatch):
    body=client.post('/api/v1/omni/query',json={'question':'2025年华东销售额趋势','session_id':'new-database'}).json()
    monkeypatch.setattr(app_module,'engine',Nl2SqlEngine(initialize_database(tmp_path/'replacement.sqlite')))
    response=client.post('/api/v1/omni/query',json={'session_id':'new-database',
        'question':f"继续待补查询编号{body['query_reference_id']}"}).json()
    assert response['result']['clarification_code']=='pending_reference_source_changed'


def test_unbound_legacy_pending_requires_fresh_confirmation(client):
    body=client.post('/api/v1/omni/query',json={'question':'2025年华东销售额趋势','session_id':'legacy'}).json()
    turn=app_module.conversation_store.context('legacy')[-1]
    state=dict(turn.state);state.pop('pending_source_revision')
    app_module.conversation_store.remember('legacy',question=turn.question,effective_question=turn.effective_question,state=state)
    response=client.post('/api/v1/omni/query',json={'question':'按月统计','session_id':'legacy'}).json()
    assert response['result']['clarification_code']=='pending_source_changed'


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


def comparison_pending(client,question='把比较值改成完整问题：销售额趋势'):
    session='comparison-pending'
    for text in ['2024年销售额趋势 按月','2025年销售额趋势 按月','比较最近两次SQL查询','按月份对齐']:
        response=client.post('/api/v1/omni/query',json={'question':text,'session_id':session})
        assert response.status_code==200
    assert response.json()['status']=='ok'
    response=client.post('/api/v1/omni/query',json={'question':question,'session_id':session})
    assert response.status_code==200
    return response.json()


def fill_comparison(client,answer,value,time=None):
    payload={'session_id':'comparison-pending','original_question':answer['effective_question'],
        'clarification_code':answer['result']['clarification_code'],'selected_value':value}
    if time:payload['selected_time']=time
    return client.post('/api/v1/omni/clarify',json=payload)


def test_comparison_time_range_then_grain_restores_selected_operand(client):
    pending=comparison_pending(client)
    assert pending['result']['clarification_code']=='missing_time_range'
    assert pending['effective_question']=='销售额趋势'
    assert 'comparison_pending_query' not in pending['state']
    response=fill_comparison(client,pending,'year','2025')
    assert response.status_code==200
    grain=response.json()
    assert grain['result']['clarification_code']=='missing_time_grain'
    assert grain['effective_question']=='销售额趋势 2025年'
    response=fill_comparison(client,grain,'monthly_trend')
    assert response.status_code==200
    final=response.json()
    assert final['route']=='comparison' and final['status']=='ok'
    assert final['result']['edit_evidence']['replacement_question']=='销售额趋势 2025年 按月'
    sources=final['result']['comparison_evidence']['sources']
    assert sources[0]['question']=='2024年销售额趋势 按月'
    assert sources[1]['question']=='销售额趋势 2025年 按月'
    assert final['result']['comparison_evidence']['alignment']=='month_of_year'
    assert len(final['result']['rows'])==8


def test_comparison_field_selection_and_stale_old_options(client):
    pending=comparison_pending(client,'把比较值改成完整问题：2025年华东地区的情况')
    assert pending['result']['clarification_code']=='missing_metric'
    response=fill_comparison(client,pending,'sales_amount')
    assert response.status_code==200
    # The replacement executed; differing scalar/monthly grouping needs its own
    # comparison clarification, not a lost pending query or a fabricated row.
    body=response.json()
    assert body['result']['clarification_code']=='comparison_dimension_mismatch'
    assert body['result']['edit_evidence']['query_status']=='ok'
    assert fill_comparison(client,pending,'sales_amount').status_code==409


def test_comparison_unknown_reply_preserves_choices_and_cancel_restores_original(client):
    pending=comparison_pending(client)
    response=client.post('/api/v1/omni/query',json={'question':'乱七八糟','session_id':'comparison-pending'})
    assert response.status_code==200
    body=response.json()
    assert body['effective_question']==pending['effective_question']
    assert body['result']['clarification_options']==pending['result']['clarification_options']
    response=client.post('/api/v1/omni/query',json={'question':'取消修改','session_id':'comparison-pending'})
    assert response.json()['status']=='ok'
    sources=response.json()['result']['comparison_evidence']['sources']
    assert sources[1]['question']=='2025年销售额趋势 按月'
    assert fill_comparison(client,pending,'year','2025').status_code==409


def test_comparison_pending_text_replies_and_sqlite_rebuild(client):
    comparison_pending(client)
    storage=app_module.conversation_store.storage_path
    app_module.conversation_store=ConversationStore(storage_path=storage)
    response=client.post('/api/v1/omni/query',json={'question':'2025年','session_id':'comparison-pending'})
    assert response.json()['result']['clarification_code']=='missing_time_grain'
    response=client.post('/api/v1/omni/query',json={'question':'按月','session_id':'comparison-pending'})
    assert response.json()['status']=='ok'
    assert response.json()['result']['edit_evidence']['replacement_question']=='销售额趋势 2025年 按月'


def test_forged_comparison_choice_does_not_consume_pending_state(client):
    pending=comparison_pending(client)
    assert fill_comparison(client,pending,'fake_field').status_code==400
    assert fill_comparison(client,pending,'year','2025').status_code==200


def test_comparison_pending_document_topic_switch_does_not_trap_the_user(client):
    pending=comparison_pending(client)
    response=client.post('/api/v1/omni/query',json={'question':'那保修政策呢','session_id':'comparison-pending'})
    assert response.json()['route']=='document'
    assert fill_comparison(client,pending,'year','2025').status_code==409


def test_comparison_pending_fresh_sql_ignores_stale_source_revision(client):
    comparison_pending(client)
    with sqlite3.connect(app_module.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id=(SELECT order_id FROM sales_orders LIMIT 1)')
    response=client.post('/api/v1/omni/query',json={'question':'2024年华东销售额','session_id':'comparison-pending'})
    assert response.json()['route']=='sql' and response.json()['status']=='ok'


def test_comparison_qualified_field_choice_reexecutes_before_metric_mismatch(client):
    pending=comparison_pending(client,'把比较值改成完整问题：2025年华东地区的情况')
    response=fill_comparison(client,pending,'sales_orders.customer_id')
    assert response.status_code==200
    result=response.json()['result']
    assert result['clarification_code']=='comparison_metric_mismatch'
    assert result['edit_evidence']['query_status']=='ok'
    assert '[field:metric:sales_orders.customer_id]' in result['edit_evidence']['replacement_question']


def test_comparison_pending_source_change_prevents_selection_execution(client,monkeypatch):
    pending=comparison_pending(client)
    with sqlite3.connect(app_module.engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id=(SELECT order_id FROM sales_orders LIMIT 1)')
    def forbidden(*args,**kwargs):raise AssertionError('stale pending source must not execute')
    monkeypatch.setattr(app_module.engine,'answer',forbidden)
    response=fill_comparison(client,pending,'year','2025')
    assert response.status_code==200
    assert response.json()['result']['clarification_code']=='comparison_source_changed'


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
