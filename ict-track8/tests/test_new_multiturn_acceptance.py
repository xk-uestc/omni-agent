"""Contract gates for frozen independent audit; stubs never count as model effect."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def evaluator(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'tools'))
    spec = importlib.util.spec_from_file_location('test_new_multiturn_evaluation', root/'tools/evaluate_new_multiturn.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inputs_are_frozen_and_gold_is_independent_sql(evaluator, tmp_path):
    directory = evaluator.ROOT/'benchmarks/new_multiturn'
    definitions, digests = evaluator.frozen_inputs(directory)
    assert len(digests)==6 and definitions['few_shot_examples']==definitions['domain_alias_rules']==0
    database = tmp_path/'gold.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript((directory/'schema.sql').read_text(encoding='utf-8'))
    gold = [evaluator.expected_rows(database, row) for row in definitions['cases']]
    assert gold == [[(1500,)],[(3600,)],[('支持部',3600)],[(1800,)],[(600,)]]
    assert [r['context_turns'] for r in definitions['cases']]==list(range(5))


def test_manifest_change_is_rejected_before_credentials(evaluator, tmp_path):
    (tmp_path/'questions.json').write_text('{}',encoding='utf-8')
    (tmp_path/'MANIFEST.json').write_text(json.dumps({'files':[{'file':'questions.json','sha256':'0'*64}]}),encoding='utf-8')
    with pytest.raises(ValueError, match='冻结输入'):
        evaluator.frozen_inputs(tmp_path)


def test_physical_metric_uses_actual_plan_label_not_domain_alias(evaluator):
    result = {'plan':{'metrics':[{'table':'Orders','column':'ApprovedAmount','function':'SUM','label':'模型任意展示标签'}]},
              'rows':[{'模型任意展示标签':1500}]}
    metric = {'table':'Orders','column':'ApprovedAmount','function':'SUM'}
    assert evaluator.physical_value(result, metric)==1500
    assert evaluator.physical_value(result, {**metric,'function':'COUNT'}) is None
    result['plan']['metrics'].append(dict(result['plan']['metrics'][0]))
    assert evaluator.physical_value(result, metric) is None


def test_legacy_rule_value_is_reported_without_pretending_model_pass(evaluator):
    result={'plan':{'table':'Orders','metric_table':'Orders','metric_column':'ApprovedAmount',
        'metric_function':'SUM','metric_label':'总ApprovedAmount','metrics':[],'planner_source':'rules_fallback'},
        'rows':[{'总ApprovedAmount':1500}]}
    assert evaluator.physical_value(result,{'table':'Orders','column':'ApprovedAmount','function':'SUM'})==1500
    assert result['plan']['planner_source']=='rules_fallback'


def test_dependency_requires_completed_actual_edges(evaluator):
    data = {'trace':[{'task_id':'a','tool':'sql','status':'complete'},{'task_id':'b','tool':'search','status':'complete'}],
            'edges':[{'from':'a','to':'b'}]}
    assert evaluator.has_edge(data, 'sql', 'search')
    data['edges']=[]
    assert not evaluator.has_edge(data, 'sql', 'search')
    data['edges']=[{'from':'a','to':'b'}]
    data['trace'][0]['status']='failed'
    assert not evaluator.has_edge(data, 'sql', 'search')


def test_preview_never_loads_credentials(evaluator, monkeypatch):
    import model_runtime
    monkeypatch.setattr(model_runtime,'enable_local_model',lambda *a: pytest.fail('must not read credentials'))
    monkeypatch.setattr('sys.argv',['evaluate_new_multiturn.py','--preview'])
    assert evaluator.main()==0


def test_correct_champion_snippet_without_dynamic_sql_dependency_is_rejected(evaluator, tmp_path):
    directory=evaluator.ROOT/'benchmarks/new_multiturn'
    definitions,_=evaluator.frozen_inputs(directory)
    database=tmp_path/'gold.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript((directory/'schema.sql').read_text(encoding='utf-8'))
    sql={'plan':{'planner_source':'model_validated'}, 'provenance':{}, 'rows':[{'Department':'支持部','合计':3600}]}
    search={'hits':[{'metadata':{'document_id':'new-approval-support'}, 'snippet':'支持部须双人审批'}]}
    result={'planner_source':'model_validated','status':'ok','route':'fusion','context_turns':2,
        'result':{'results':{'sql':sql,'search':search}, 'trace':[
            {'task_id':'sql','tool':'sql','status':'complete'}, {'task_id':'search','tool':'search','status':'complete'}], 'edges':[]}}
    checks=evaluator.evaluate_result(definitions['cases'][2],result,database)
    assert checks['actual_champion'] and checks['source_and_champion']
    assert not checks['sql_to_search_dependency']
    result['result']['edges']=[{'from':'sql','to':'search'}]
    assert all(evaluator.evaluate_result(definitions['cases'][2],result,database).values())


def test_new_topic_rejects_old_department_filter_even_when_value_is_correct(evaluator, tmp_path):
    directory=evaluator.ROOT/'benchmarks/new_multiturn'
    definitions,_=evaluator.frozen_inputs(directory)
    database=tmp_path/'gold.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript((directory/'schema.sql').read_text(encoding='utf-8'))
    data={'plan':{'planner_source':'model_validated','metrics':[{'table':'WarrantyRequests','column':'RefundedAmount',
        'function':'SUM','label':'退款合计'}], 'filters':[{'column':'Department','value':'支持部'}]},'rows':[{'退款合计':1800}]}
    result={'planner_source':'model_validated','status':'ok','route':'sql','context_turns':3,'result':data}
    checks=evaluator.evaluate_result(definitions['cases'][3],result,database)
    assert checks['physical_metric_value'] and not checks['no_previous_topic_filter']


def test_frozen_formula_is_parsed_from_real_original_document(evaluator, tmp_path):
    from backend.knowledge_store import KnowledgeStore
    from backend.dependency_agent import DependencyAgent
    source=evaluator.ROOT/'benchmarks/new_multiturn/refund-calculation.md'
    knowledge=KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest(source.read_bytes(), document_id='new-refund-calculation',title='退款核算说明',modality='md',filename=source.name)
    result=DependencyAgent(None,knowledge).run([{'id':'formula','tool':'document_formula',
        'args':{'document_id':'new-refund-calculation','label':'每笔退款核算额'}}])
    assert result['status']=='ok'
    assert set(result['results']['formula']['parameters'])=={'退款总额','退款记录数'}
    assert result['source_validation']['status']=='verified'
