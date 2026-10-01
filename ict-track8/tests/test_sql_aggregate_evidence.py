from copy import deepcopy

import pytest

from backend.sql_evidence import aggregate_evidence
from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def test_formula_uses_schema_address_instead_of_display_or_formula_label(tmp_path):
    store = KnowledgeStore(tmp_path/'knowledge')
    store.ingest('预测总额 = 基准金额 * 1.1'.encode(), document_id='formula', title='核算方法', modality='txt', filename='formula.txt')
    agent = DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')), store)
    result = agent.run([
        {'id':'formula','tool':'document_formula','args':{'document_id':'formula','label':'预测总额'}},
        {'id':'sql','tool':'sql','args':{'question':'2025年华东地区销售额'}},
        {'id':'result','tool':'calculate','args':{'formula':{'ref':'formula','path':[]},'parameters':{
            '基准金额':{'ref':'sql','path':['aggregate_cells','sales_orders','sales_amount','SUM',0]}}}},
    ])
    assert result['status']=='ok', result.get('error')
    assert result['results']['result']['value']==pytest.approx(29584*1.1)
    cell = result['results']['result']['parameters']['基准金额']
    assert cell['source_uri'].startswith('sql://') and cell['locator']=='rows/0/销售额'
    assert cell['unit']=='unknown'  # Legacy single-metric rules declare no currency.
    assert {'from':'sql','to':'result'} in result['edges']


def sample():
    return {'rows':[{'表述不固定':10}], 'provenance':{'query_hash':'abcd'}, 'plan':{'metrics':[
        {'id':'m1','table':'invoices','column':'amount','function':'SUM','label':'表述不固定','unit':'currency','currency':'CNY'}]}}


def test_display_labels_are_not_part_of_aggregate_address_and_none_is_not_zero():
    original=sample()
    original['rows'].append({'表述不固定':None})
    saved=deepcopy(original)
    cells, errors=aggregate_evidence(original)
    assert errors==[] and original==saved
    assert cells['invoices']['amount']['SUM'][0]['value']==10
    assert cells['invoices']['amount']['SUM'][1]['value'] is None


def test_same_source_field_with_different_functions_stays_distinct():
    result=sample()
    result['plan']['metrics'].append({**result['plan']['metrics'][0],'id':'m2','function':'AVG','label':'平均金额'})
    result['rows'][0]['平均金额']=5
    cells, errors=aggregate_evidence(result)
    assert errors==[]
    assert cells['invoices']['amount']['SUM'][0]['value']==10
    assert cells['invoices']['amount']['AVG'][0]['value']==5


def test_same_address_for_different_filtered_metrics_is_ambiguous_not_first_match():
    result=sample()
    result['plan']['metrics'].append({**result['plan']['metrics'][0],'id':'m2','label':'上期金额','filters':[{'value':'prior'}]})
    result['rows'][0]['上期金额']=4
    cells, errors=aggregate_evidence(result)
    assert cells=={} and errors==[['invoices','amount','SUM']]


@pytest.mark.parametrize('path', [
    ['aggregate_cells','sales_orders','sales_amount','AVG',0],
    ['aggregate_cells','sales_orders','wrong_column','SUM',0],
    ['aggregate_cells','sales_orders','sales_amount','SUM',1],
])
def test_nonexistent_aggregation_column_or_row_does_not_select_another_value(tmp_path,path):
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest('金额 = 原值 * 2'.encode(),document_id='formula',title='公式',modality='txt',filename='f.txt')
    agent=DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),store)
    result=agent.run([
        {'id':'f','tool':'document_formula','args':{'document_id':'formula','label':'金额'}},
        {'id':'s','tool':'sql','args':{'question':'2025年华东地区销售额'}},
        {'id':'r','tool':'calculate','args':{'formula':{'ref':'f','path':[]},'parameters':{'原值':{'ref':'s','path':path}}}},
    ])
    assert result['status']=='incomplete' and result['failed_task']=='r'


def test_compare_consumes_typed_sql_aggregate_evidence_and_keeps_dependencies(tmp_path):
    agent=DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),
                          KnowledgeStore(tmp_path/'knowledge'))
    ref=lambda task:{'ref':task,'path':['aggregate_cells','sales_orders','sales_amount','SUM',0]}
    result=agent.run([
        {'id':'east','tool':'sql','args':{'question':'2025年华东地区销售额'}},
        {'id':'south','tool':'sql','args':{'question':'2025年华南地区销售额'}},
        {'id':'compare','tool':'compare','args':{'left':ref('east'),'right':ref('south')}},
    ])
    assert result['status']=='ok'
    compared=result['results']['compare']
    assert compared['left']['value']==result['results']['east']['rows'][0]['销售额']
    assert compared['right']['value']==result['results']['south']['rows'][0]['销售额']
    assert compared['left']['source_uri']!=compared['right']['source_uri']
    assert {'from':'east','to':'compare'} in result['edges']
    assert {'from':'south','to':'compare'} in result['edges']
