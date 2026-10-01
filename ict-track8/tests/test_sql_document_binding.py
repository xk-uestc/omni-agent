"""Ranked SQL -> search provenance and complete-winner coverage, without APIs."""
from copy import deepcopy
import sqlite3
import hashlib
import json

import pytest

from backend.fusion_constraints import SourceConstraintError
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.sql_document_binding import authorize_sql_document_search, validate_sql_document_search
from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore


QUESTION = '先从数据库查2025年销售额排名第一的地区，再根据该地区检索冠军团队的方法，保留来源。'


def graph():
    return [{'id': 'winner', 'tool': 'sql', 'args': {'question': '2025年销售额排名第一的地区'}},
            {'id': 'method', 'tool': 'search', 'args': {'query': [
                {'ref': 'winner', 'path': ['rows', 0, 'region']}, '冠军团队的方法']}}]


@pytest.fixture
def engine(tmp_path):
    return Nl2SqlEngine(initialize_database(tmp_path / 'demo.sqlite'))


def prepared(engine, tasks=None):
    tasks = tasks or graph()
    bundle = authorize_sql_document_search(QUESTION, tasks, engine)
    required = engine.extract_required_intent(bundle['bindings'][0]['scope_question'])
    result = engine.answer(tasks[0]['args']['question'], required_intent=required).to_dict()
    return tasks, bundle, {'winner': result}


def validate(engine, tasks, bundle, results):
    return validate_sql_document_search(bundle, task=tasks[1], tasks=tasks, results=results, engine=engine)


def make_tie(engine):
    with sqlite3.connect(engine.database_path) as connection:
        connection.execute("UPDATE sales_orders SET sales_amount=0 WHERE region IN ('华东','华南')")
        connection.execute("UPDATE sales_orders SET sales_amount=100000 WHERE order_id=(SELECT MIN(order_id) FROM sales_orders WHERE region='华东' AND order_date LIKE '2025%')")
        connection.execute("UPDATE sales_orders SET sales_amount=100000 WHERE order_id=(SELECT MIN(order_id) FROM sales_orders WHERE region='华南' AND order_date LIKE '2025%')")


def test_actual_rank1_reference_retains_original_search_target(engine):
    tasks, bundle, results = prepared(engine)
    left, right = bundle['source_ranges'][0]
    assert QUESTION[left:right] == '再根据该地区检索冠军团队的方法'
    audit = validate(engine, tasks, bundle, results)
    assert audit['status'] == 'verified' and audit['value'] == '华东'
    assert audit['target_text'] == '冠军团队的方法'


@pytest.mark.parametrize('query', ['华东冠军团队的方法', ['华东', '冠军团队的方法'],
    [{'ref': 'winner', 'path': ['rows', True, 'region']}, '冠军团队的方法'],
    [{'ref': 'winner', 'path': ['rows', 0, '销售额']}, '冠军团队的方法'],
    [{'ref': 'winner', 'path': ['rows', 0, 'region']}, '团队的方法'],
    [{'ref': 'winner', 'path': ['rows', 0, 'region']}, '冠军团队的方法2026'],
    [{'ref': 'winner', 'path': ['rows', 0, 'region']}, '华东冠军团队的方法']])
def test_constant_wrong_path_or_changed_search_scope_rejected(engine, query):
    tasks = graph()
    tasks[1]['args']['query'] = query
    try:
        tasks, bundle, results = prepared(engine, tasks)
    except SourceConstraintError:
        return
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


@pytest.mark.parametrize('question', [
    QUESTION.replace('冠军团队的方法', '2026年冠军团队的方法'),
    QUESTION.replace('冠军团队的方法', '不含华南的冠军团队的方法'),
    QUESTION.replace('再根据', '仅已批准记录再根据'),
    QUESTION.replace('该地区', '该客户'),
    QUESTION.replace('排名第一', '排名第三'),
    QUESTION.replace('排名第一', '倒数第一'),
])
def test_original_limits_never_become_general_document_exemption(engine, question):
    with pytest.raises(SourceConstraintError):
        authorize_sql_document_search(question, graph(), engine)


@pytest.mark.parametrize('change', [
    lambda result: result['rows'][0].update(region='华南'),
    lambda result: result['plan'].update(top_n=3),
    lambda result: result['plan'].update(dimension_transforms={'region': 'upper'}),
    lambda result: result['provenance'].update(result_completeness='return_limit_reached'),
    lambda result: result['provenance']['source_constraint_validation'].update(status='unverified'),
    lambda result: result['provenance']['source_constraint_validation'].update(scope_question_sha256='0' * 64),
    lambda result: result.update(parameters=['2026-01-01', '2027-01-01', 1, 100]),
])
def test_runtime_rows_plan_provenance_and_scope_are_rechecked(engine, change):
    tasks, bundle, results = prepared(engine)
    change(results['winner'])
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def test_each_tied_first_place_requires_real_search_reference(engine):
    # Make two regions have the same yearly sum in the actual local DB.
    make_tie(engine)
    tasks, bundle, results = prepared(engine)
    assert len(results['winner']['rows']) == 2
    with pytest.raises(SourceConstraintError) as error:
        validate(engine, tasks, bundle, results)
    assert error.value.code == 'sql_document_tied_winners_not_covered'
    second = deepcopy(tasks[1])
    second['id'] = 'method2'
    second['args']['query'][0]['path'][1] = 1
    tasks.append(second)
    bundle = authorize_sql_document_search(QUESTION, tasks, engine)
    assert validate(engine, tasks, bundle, results)['status'] == 'verified'
    assert validate_sql_document_search(bundle, task=tasks[2], tasks=tasks, results=results, engine=engine)['status'] == 'verified'


def test_changed_actual_database_invalidates_replayed_rows(engine):
    tasks, bundle, results = prepared(engine)
    with sqlite3.connect(engine.database_path) as connection:
        connection.execute('UPDATE sales_orders SET sales_amount=sales_amount+1')
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def test_raw_query_modified_after_authorization_rejected(engine):
    tasks, bundle, results = prepared(engine)
    tasks[1]['args']['query'][0]['path'][1] = 1
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def test_no_deictic_search_has_no_document_exemption(engine):
    assert authorize_sql_document_search('数据库2025年销售额', graph()[:1], engine) == {'source_ranges': [], 'bindings': []}


def test_duplicate_references_cannot_claim_complete_winner_coverage(engine):
    tasks = graph()
    second = deepcopy(tasks[1])
    second['id'] = 'again'
    tasks.append(second)
    tasks, bundle, results = prepared(engine, tasks)
    with pytest.raises(SourceConstraintError) as error:
        validate(engine, tasks, bundle, results)
    assert error.value.code == 'sql_document_tied_winners_not_covered'


def test_other_winner_search_target_is_rechecked_before_first_search(engine):
    make_tie(engine)
    tasks = graph()
    second = deepcopy(tasks[1])
    second['id'] = 'again'
    second['args']['query'][0]['path'][1] = 1
    tasks.append(second)
    tasks, bundle, results = prepared(engine, tasks)
    # A task mutated after authorization cannot inherit the original exemption.
    tasks[2]['args']['query'][-1] = '常识猜测的方法'
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


@pytest.mark.parametrize('path', [['rows', -1, 'region'], ['rows', 0.0, 'region'],
                                ['rows', 0, 'region', 'value'], []])
def test_ref_paths_are_strict_typed_actual_row_coordinates(engine, path):
    tasks = graph()
    tasks[1]['args']['query'][0]['path'] = path
    with pytest.raises(SourceConstraintError):
        authorize_sql_document_search(QUESTION, tasks, engine)


def test_declared_dimension_mapping_rechecked_against_runtime_schema(engine, monkeypatch):
    from types import SimpleNamespace
    tasks, bundle, results = prepared(engine)
    monkeypatch.setattr(engine, 'analyze_slots', lambda _: {
        'dimensions': [SimpleNamespace(table='customers', column='customer_name')]})
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def search_agent(engine):
    store = KnowledgeStore(engine.database_path.parent / 'champion-corpus')
    for region, document_id in [('华东', 'east-champion'), ('华南', 'south-champion')]:
        store.ingest(f'{region}冠军团队的方法：执行每日复盘，并核查订单质量。'.encode(),
                     document_id=document_id, title=f'{region}冠军团队', modality='txt', filename=document_id + '.txt')
    return DependencyAgent(engine, store)


def test_full_original_question_dependency_graph_searches_actual_winner(engine):
    agent = search_agent(engine)
    result = agent.run(graph(), original_question=QUESTION)
    assert result['status'] == 'ok', result
    assert result['results']['winner']['rows'][0]['region'] == '华东'
    search = result['results']['method']
    assert search['dependency_reference_validation']['status'] == 'verified'
    assert search['dependency_reference_validation']['value'] == '华东'
    assert search['dependency_reference_validation']['target_text'] == '冠军团队的方法'
    assert search['hits'][0]['metadata']['document_id'] == 'east-champion'
    assert result['source_validation']['documents']['east-champion'] == search['hits'][0]['metadata']['source_sha256']
    assert {'from': 'winner', 'to': 'method'} in result['edges']
    assert result['user_constraint_validation']['status'] == 'verified'


def test_full_graph_wrong_actual_output_reference_stops_before_search(engine):
    agent = search_agent(engine)
    tasks = graph()
    tasks[1]['args']['query'][0]['path'][2] = '销售额'
    result = agent.run(tasks, original_question=QUESTION)
    assert result['status'] == 'incomplete'
    assert result['failed_task'] == 'method' and 'method' not in result['results']
    assert result['results']['winner']['status'] == 'ok'


def test_full_graph_literal_model_guess_has_no_authorized_sql_dependency(engine):
    agent = search_agent(engine)
    tasks = graph()
    tasks[1]['args']['query'] = '华东冠军团队的方法'
    result = agent.run(tasks, original_question=QUESTION)
    assert result['status'] == 'clarification' and result['results'] == {}
    assert result['user_constraint_validation']['status'] == 'unverified'


def test_full_graph_omitted_deictic_document_search_not_claimed_success(engine):
    result = search_agent(engine).run(graph()[:1], original_question=QUESTION)
    assert result['status'] == 'clarification' and result['results'] == {}


def test_full_graph_tied_winners_need_every_search_or_explicit_failure(engine):
    make_tie(engine)
    agent = search_agent(engine)
    incomplete = agent.run(graph(), original_question=QUESTION)
    assert incomplete['status'] == 'incomplete'
    assert incomplete['failed_task'] == 'method' and 'method' not in incomplete['results']
    assert len(incomplete['results']['winner']['rows']) == 2
    tasks = graph()
    second = deepcopy(tasks[1])
    second['id'] = 'method2'
    second['args']['query'][0]['path'][1] = 1
    tasks.append(second)
    complete = agent.run(tasks, original_question=QUESTION)
    assert complete['status'] == 'ok', complete
    assert {complete['results'][key]['dependency_reference_validation']['value']
            for key in ['method', 'method2']} == {'华东', '华南'}


def replace_self_consistent_sql_result(engine, result, sql, parameters=()):
    from backend.nl2sql.security import execute_read_only
    with engine._connect() as connection:
        columns, rows = execute_read_only(connection, sql, tuple(parameters), max_rows=engine.max_rows,
            max_steps=engine.max_steps, max_seconds=engine.max_seconds)
    result.update(sql=sql, parameters=list(parameters), columns=list(columns), rows=list(rows))
    result['provenance']['query_hash'] = hashlib.sha256(json.dumps({'sql': sql, 'parameters': list(parameters)},
        ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()[:16]


@pytest.mark.parametrize('forged', [
    "SELECT '华南' AS region, 7 AS '销售额', 1 AS '排名'",
    "SELECT '华东' AS region, 7 AS '销售额', 1 AS '排名'",
    "SELECT region, AVG(sales_amount) AS '销售额', 1 AS '排名' FROM sales_orders "
    "WHERE region='华东' AND order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region",
])
def test_self_consistent_sql_rows_columns_hash_forgery_is_not_source_proof(engine, forged):
    tasks, bundle, results = prepared(engine)
    replace_self_consistent_sql_result(engine, results['winner'], forged)
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def test_correct_region_does_not_authorize_wrong_physical_metric_plan(engine):
    tasks, bundle, results = prepared(engine)
    result = results['winner']
    result['plan']['metric_function'] = 'AVG'
    replace_self_consistent_sql_result(engine, result,
        "SELECT region, AVG(sales_amount) AS '销售额', 1 AS '排名' FROM sales_orders "
        "WHERE region='华东' AND order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region")
    assert result['rows'][0]['region'] == '华东'
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def test_self_consistent_subset_cannot_hide_a_tied_winner(engine):
    make_tie(engine)
    tasks, bundle, results = prepared(engine)
    result = results['winner']
    sql, parameters = result['sql'], result['parameters']
    replace_self_consistent_sql_result(engine, result, 'SELECT * FROM (' + sql + ') WHERE region=?', [*parameters, '华东'])
    assert len(result['rows']) == 1
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)


def test_verified_physical_dimension_can_use_chinese_display_alias(engine):
    tasks, bundle, results = prepared(engine)
    result = results['winner']
    replace_self_consistent_sql_result(engine, result,
        'SELECT region AS "地区", "销售额", "排名" FROM (' + result['sql'] + ')', result['parameters'])
    result['plan']['dimension_labels']['region'] = '地区'
    tasks[1]['args']['query'][0]['path'][2] = '地区'
    bundle = authorize_sql_document_search(QUESTION, tasks, engine)
    audit = validate(engine, tasks, bundle, results)
    assert audit['status'] == 'verified' and audit['value'] == '华东'
    assert audit['result_verification'] == 'independent_original_scope_rules_and_complete_physical_result_replay'


def test_canonical_verification_never_invokes_or_changes_model_provider(engine):
    tasks, bundle, results = prepared(engine)
    class MustNotCall:
        def generate(self, *args, **kwargs):
            raise AssertionError('model API must not be called in result verification')
    provider = MustNotCall()
    engine.model_plan_provider = provider
    assert validate(engine, tasks, bundle, results)['status'] == 'verified'
    assert engine.model_plan_provider is provider


def test_metric_display_alias_is_bound_to_same_physical_sum(engine):
    tasks, bundle, results = prepared(engine)
    result = results['winner']
    replace_self_consistent_sql_result(engine, result,
        'SELECT region, "销售额" AS "总销售额", "排名" FROM (' + result['sql'] + ')', result['parameters'])
    result['plan']['metric_label'] = '总销售额'
    assert validate(engine, tasks, bundle, results)['status'] == 'verified'


def test_multi_metric_rank_uses_independent_metric_compiler(engine):
    question = QUESTION.replace('销售额排名', '销售额和订单数排名')
    tasks = graph()
    tasks[0]['args']['question'] = '2025年销售额和订单数排名第一的地区'
    bundle = authorize_sql_document_search(question, tasks, engine)
    required = engine.extract_required_intent(bundle['bindings'][0]['scope_question'])
    result = engine.answer(tasks[0]['args']['question'], required_intent=required).to_dict()
    assert result['status'] == 'ok' and len(result['plan']['metrics']) == 2
    results = {'winner': result}
    assert validate(engine, tasks, bundle, results)['status'] == 'verified'
    wrong_sort = deepcopy(results)
    wrong_sort['winner']['plan']['order_metric'] = next(metric['id'] for metric in result['plan']['metrics']
                                                      if metric['column'] == 'order_id')
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, wrong_sort)
    # Keeping a correct winning region and sales sum cannot hide a forged
    # second aggregate in the compiled multi-metric projection.
    result['rows'][0]['订单数'] += 1
    with pytest.raises(SourceConstraintError):
        validate(engine, tasks, bundle, results)
