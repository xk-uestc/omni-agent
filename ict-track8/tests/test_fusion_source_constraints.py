"""Original fusion clauses constrain real SQL; planner stubs are not API scores."""
import json
import sqlite3
from copy import deepcopy

import pytest

from backend.dependency_agent import DependencyAgent
from backend.fusion_constraints import (SourceConstraintError, VerifiedFormulaTarget, bind_source_constraints,
                                        extract_source_clauses, verify_required_intent)
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.models import FilterSpec, MetricSpec, QueryPlan
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


QUESTION = '依据文档的核算结果公式，对数据库中2025年包裹台账费用金额合计（不含北岭）进行核算。'
SQL_QUESTION = '2025年包裹台账费用金额合计，不含北岭'


class ProtocolPlanner:
    audit = {'status': 'completed', 'http_status': 200}

    def __init__(self, question, tasks):
        self.question, self.tasks = question, tasks

    def generate(self, *args, **kwargs):
        return {'route': 'fusion', 'effective_question': self.question,
                'clarification': '', 'tasks_json': json.dumps(self.tasks, ensure_ascii=False)}


@pytest.fixture
def environment(tmp_path):
    database = tmp_path / 'unseen.sqlite'
    connection = sqlite3.connect(database)
    connection.execute('CREATE TABLE 包裹台账(包裹ID INTEGER PRIMARY KEY, 区域 TEXT NOT NULL, 出库日期 TEXT NOT NULL, 费用金额 REAL NOT NULL)')
    connection.executemany('INSERT INTO 包裹台账 VALUES(?,?,?,?)', [
        (1, '北岭', '2025-03-01', 100), (2, '南湾', '2025-04-01', 40),
        (3, '南湾', '2024-02-01', 200), (4, '南湾', '2026-02-01', 900),
        (5, '南湾', '2025-05-01', 20)])
    connection.commit()
    connection.close()
    engine = Nl2SqlEngine(database, metric_catalog_path=tmp_path / 'absent.json')
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest('核算结果 = 基准费用 * 2'.encode(), document_id='parcel-formula',
                     title='包裹核算方法', modality='txt', filename='parcel.txt')
    knowledge.ingest('每笔核算额 = 总费用 / 记录数'.encode(), document_id='per-record-formula',
                     title='每笔核算方法', modality='txt', filename='per-record.txt')
    return engine, knowledge


def cell(task, column='费用金额', function='SUM'):
    return {'ref': task, 'path': ['aggregate_cells', '包裹台账', column, function, 0]}


def tasks(sql_question=SQL_QUESTION):
    return [
        {'id': 'f', 'tool': 'document_formula', 'args': {'document_id': 'parcel-formula', 'label': '核算结果'}},
        {'id': 's', 'tool': 'sql', 'args': {'question': sql_question}},
        {'id': 'c', 'tool': 'calculate', 'args': {'formula': {'ref': 'f', 'path': []},
                                                'parameters': {'基准费用': cell('s')}}}]


def run(environment, question, execution_tasks):
    engine, knowledge = environment
    return OmniAgent(engine, knowledge, ConversationStore(), ProtocolPlanner(question, execution_tasks)).query(question)


def test_explicit_source_preserves_time_and_exclusion_on_unseen_schema(environment):
    result = run(environment, QUESTION, tasks())
    assert result['status'] == 'ok', result['result']
    assert result['result']['results']['c']['value'] == 120
    validation = result['result']['user_constraint_validation']
    assert validation['status'] == 'verified'
    assert validation['bindings'][0]['text'] == '2025年包裹台账费用金额合计（不含北岭）'
    assert result['result']['results']['s']['provenance']['source_constraint_validation']['status'] == 'verified'


@pytest.mark.parametrize('sql_question', [
    '2025年包裹台账费用金额合计',
    '包裹台账费用金额合计，不含北岭',
    '2025年北岭包裹台账费用金额合计',
    '2026年包裹台账费用金额合计，不含北岭',
    '2025年包裹台账费用金额平均，不含北岭',
])
def test_lost_reversed_or_changed_user_slot_stops_before_business_sql(environment, monkeypatch, sql_question):
    import backend.nl2sql.engine as module
    calls = []
    original = module.execute_read_only
    def observed(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'execute_read_only', observed)
    result = run(environment, QUESTION, tasks(sql_question))
    assert result['status'] == 'incomplete', result
    assert result['result']['error_code'] == 'source_constraint_mismatch'
    assert result['result']['failed_task'] == 's' and result['result']['skipped_tasks'] == ['c']
    assert 'c' not in result['result']['results'] and calls == []
    failure = result['result']['trace'][-1]
    assert failure['source_constraint_code'] == 'source_constraint_mismatch'
    assert failure['constraint_error_codes']
    assert all(code.startswith('source_') for code in failure['constraint_error_codes'])
    assert result['result']['execution_plan'] == tasks(sql_question)


def test_prediction_target_does_not_replace_historical_sql_baseline(environment):
    question = '依据文档核算结果公式，用数据库中2025年包裹台账费用金额合计（不含北岭）作为基准，预测2026年的费用。'
    result = run(environment, question, tasks())
    assert result['status'] == 'ok' and result['result']['results']['c']['value'] == 120
    binding = result['result']['user_constraint_validation']['bindings'][0]
    assert binding['role'] == 'baseline' and '2026年' not in binding['text']
    wrong = run(environment, question, tasks('2026年包裹台账费用金额合计，不含北岭'))
    assert wrong['status'] == 'incomplete' and wrong['result']['error_code'] == 'source_constraint_mismatch'


@pytest.mark.parametrize('question', [
    '请排除北岭；依据文档核算结果公式，用数据库中2025年包裹台账费用金额合计作为输入。',
    '仅北岭地区；依据文档核算结果公式，用数据库中2025年包裹台账费用金额合计作为输入。',
    '针对北岭区域；依据文档核算结果公式，用数据库中2025年包裹台账费用金额合计作为输入。',
    '按文档公式核算2025年的费用，费用金额从数据库取值。',
    '按文档公式核算，用数据库中2025年包裹台账未知口径费用金额合计作为输入。',
])
def test_ambiguous_or_unconsumed_source_requirements_clarify_without_tools(environment, question):
    result = run(environment, question, tasks())
    assert result['status'] == 'clarification', result
    assert result['result']['error_code'] == 'source_scope_unverified'
    assert result['result']['results'] == {} and result['result']['trace'] == []


def split_tasks(count_question='2025年包裹台账包裹ID记录计数，不含北岭'):
    return [
        {'id': 'f', 'tool': 'document_formula', 'args': {'document_id': 'per-record-formula', 'label': '每笔核算额'}},
        {'id': 'amount', 'tool': 'sql', 'args': {'question': SQL_QUESTION}},
        {'id': 'count', 'tool': 'sql', 'args': {'question': count_question}},
        {'id': 'c', 'tool': 'calculate', 'args': {'formula': {'ref': 'f', 'path': []},
                                                'parameters': {'总费用': cell('amount'), '记录数': cell('count', '包裹ID', 'COUNT')}}}]


SPLIT_QUESTION = '按文档每笔核算额公式，用数据库中2025年包裹台账费用金额合计和包裹ID记录计数（不含北岭）作为输入进行核算。'


def test_split_sum_and_count_cover_source_metrics_and_keep_shared_filters(environment):
    result = run(environment, SPLIT_QUESTION, split_tasks())
    assert result['status'] == 'ok', result
    values = result['result']['results']
    assert values['c']['value'] == 30
    assert values['amount']['rows'][0]['总费用金额'] == 60
    assert values['count']['aggregate_cells']['包裹台账']['包裹ID']['COUNT'][0]['value'] == 2
    assert len(result['result']['user_constraint_validation']['bindings']) == 2


def test_split_count_cannot_drop_original_scope_time(environment):
    result = run(environment, SPLIT_QUESTION, split_tasks('包裹台账包裹ID记录计数，不含北岭'))
    assert result['status'] == 'incomplete', result
    assert result['result']['failed_task'] == 'count' and 'c' not in result['result']['results']


def test_missing_source_metric_is_not_covered_by_reusing_sum(environment):
    execution_tasks = split_tasks()
    execution_tasks.pop(2)
    execution_tasks[-1]['args']['parameters']['记录数'] = cell('amount')
    result = run(environment, SPLIT_QUESTION, execution_tasks)
    # Single SQL step is bound to the entire source, not to its invented
    # partial subquestion, and the missing COUNT is rejected before execute.
    assert result['status'] == 'incomplete'
    assert result['result']['failed_task'] == 'amount'


def test_two_source_clauses_with_same_metric_bind_by_verified_local_filters(environment):
    engine, knowledge = environment
    question = '比较数据库中2025年北岭包裹台账费用金额合计，与数据库中2025年南湾包裹台账费用金额合计。'
    execution_tasks = [
        {'id': 'north', 'tool': 'sql', 'args': {'question': '2025年北岭包裹台账费用金额合计'}},
        {'id': 'south', 'tool': 'sql', 'args': {'question': '2025年南湾包裹台账费用金额合计'}},
        {'id': 'compare', 'tool': 'compare', 'args': {'left': cell('north'), 'right': cell('south')}}]
    result = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert result['status'] == 'ok', result
    assert result['results']['compare']['difference'] == 40
    assert len(result['user_constraint_validation']['bindings']) == 2
    execution_tasks[1]['args']['question'] = '2025年包裹台账费用金额合计'
    wrong = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert wrong['status'] == 'clarification' and wrong['error_code'] == 'source_binding_ambiguous'
    assert wrong['results'] == {}


def test_database_scope_cannot_be_replaced_by_the_other_year(environment):
    engine, knowledge = environment
    question = '比较数据库中2025年包裹台账费用金额合计，与数据库中2026年包裹台账费用金额合计。'
    execution_tasks = [
        {'id': 'prior', 'tool': 'sql', 'args': {'question': '2025年包裹台账费用金额合计'}},
        {'id': 'next', 'tool': 'sql', 'args': {'question': '2026年包裹台账费用金额合计'}},
        {'id': 'compare', 'tool': 'compare', 'args': {'left': cell('prior'), 'right': cell('next')}}]
    result = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert result['status'] == 'ok' and result['results']['compare']['difference'] == -740
    execution_tasks[0]['args']['question'] = execution_tasks[1]['args']['question']
    wrong = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert wrong['status'] == 'clarification' and wrong['results'] == {}


def test_followup_without_server_verifiable_source_scope_clarifies(environment):
    result = run(environment, '那南湾呢，仍按同一公式？', tasks('2025年南湾包裹台账费用金额合计'))
    assert result['status'] == 'clarification' and result['result']['results'] == {}


def test_self_contained_sql_question_on_fusion_route_preserves_the_whole_original(environment):
    engine, knowledge = environment
    question = '2025年南湾费用金额合计'
    execution_tasks = [{'id': 'read', 'tool': 'sql', 'args': {'question': question}}]
    result = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert result['status'] == 'ok' and result['results']['read']['rows'][0]['总费用金额'] == 60
    assert result['user_constraint_validation']['bindings'][0]['text'] == question
    execution_tasks[0]['args']['question'] = '2026年南湾费用金额合计'
    wrong = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert wrong['status'] == 'incomplete' and wrong['error_code'] == 'source_constraint_mismatch'


@pytest.mark.parametrize('question', [QUESTION, '按文档核算结果公式，用2025年包裹台账费用金额合计作为输入。'])
def test_omitting_all_database_tasks_does_not_complete_the_user_request(environment, question):
    execution_tasks = [{'id': 'f', 'tool': 'document_formula', 'args': {'document_id': 'parcel-formula', 'label': '核算结果'}}]
    result = run(environment, question, execution_tasks)
    assert result['status'] == 'clarification'
    assert result['result']['results'] == {} and result['result']['error_code'] == 'source_scope_unverified'


def test_source_contract_compares_physical_metrics_not_display_labels():
    required = QueryPlan(table='unseen_table', metric_column='amount', metric_function='SUM', metric_label='合计')
    actual = deepcopy(required)
    actual.metric_label = '另一种展示名'
    assert verify_required_intent(actual, required) == []
    actual.metric_function = 'AVG'
    assert 'source_metric_mismatch' in verify_required_intent(actual, required)


def test_typed_filter_retains_polarity_and_half_open_time():
    required = QueryPlan(table='unseen_table', metric_column='amount', metric_function='SUM', filters=[
        FilterSpec('zone', '!=', 'unseen-zone', 'unseen-zone', '', 'unseen_table'),
        FilterSpec('when', 'RANGE', ['2025-01-01', '2026-01-01'], '2025年', '', 'unseen_table')])
    actual = deepcopy(required)
    actual.filters[0] = FilterSpec('zone', '=', 'unseen-zone', 'unseen-zone', '', 'unseen_table')
    assert 'source_filter_mismatch' in verify_required_intent(actual, required)


def test_ambiguous_required_scope_is_never_accepted():
    required = QueryPlan(table='unseen_table', metric_column='amount', metric_function='SUM', clarification='哪个日期字段？')
    assert verify_required_intent(deepcopy(required), required) == ['source_scope_unverified']


def test_same_physical_metric_with_two_local_filters_cannot_hide_first_filter():
    first = FilterSpec('zone', '=', 'a', 'a', '', 'unseen_table')
    second = FilterSpec('zone', '=', 'b', 'b', '', 'unseen_table')
    required = QueryPlan(table='unseen_table', metrics=[
        MetricSpec('a', 'unseen_table', 'amount', 'SUM', 'a金额', filters=[first]),
        MetricSpec('b', 'unseen_table', 'amount', 'SUM', 'b金额', filters=[second])])
    actual = deepcopy(required)
    assert verify_required_intent(actual, required) == []
    actual.metrics[0].filters = [second]
    assert 'source_metric_semantics_mismatch' in verify_required_intent(actual, required)


@pytest.mark.parametrize('field,value,code', [
    ('missing', 'zero', 'source_metric_missing_policy_mismatch'),
    ('unit', 'yuan', 'source_metric_unit_mismatch'),
    ('currency', 'USD', 'source_metric_currency_mismatch'),
])
def test_metric_semantic_rejection_has_static_specific_diagnostic(field, value, code):
    required = QueryPlan(table='unseen_table', metrics=[
        MetricSpec('amount', 'unseen_table', 'amount', 'SUM', '金额', unit='万元', currency='CNY', missing='null')])
    actual = deepcopy(required)
    setattr(actual.metrics[0], field, value)
    errors = verify_required_intent(actual, required)
    assert 'source_metric_semantics_mismatch' in errors
    assert code in errors
    assert verify_required_intent(deepcopy(required), required) == []


def test_table_identifier_substring_is_not_an_explicit_source():
    with pytest.raises(SourceConstraintError):
        extract_source_clauses('按文档核算PreOrdersArchive的费用', {'tables': [{'name': 'Orders'}]})


@pytest.mark.parametrize('question', [
    '2025年南湾每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '按指标计算文档中的每笔核算额公式计算2025年南湾区域每笔核算额，费用金额合计和包裹ID记录计数从数据库取值。',
])
def test_reverse_source_preserves_literal_calculation_target_scope(environment, question):
    engine, knowledge = environment
    execution_tasks = split_tasks('2025年南湾包裹台账包裹ID记录计数')
    execution_tasks[1]['args']['question'] = '2025年南湾包裹台账费用金额合计'
    result = run(environment, question, execution_tasks)
    assert result['status'] == 'ok', result
    assert result['result']['results']['c']['value'] == 30
    binding = result['result']['user_constraint_validation']['bindings'][0]
    assert '2025年南湾' in binding['text']
    assert '每笔核算额' not in binding['text']
    assert len(binding['qualifier_ranges']) == 1
    left, right = binding['qualifier_ranges'][0]
    assert question[left:right] in {'2025年南湾', '2025年南湾区域'}
    # The model may not drop/change inherited scope in any split read.
    execution_tasks[2]['args']['question'] = '包裹台账包裹ID记录计数'
    wrong = run(environment, question, execution_tasks)
    assert wrong['status'] == 'incomplete'
    assert wrong['result']['error_code'] == 'source_constraint_mismatch'


@pytest.mark.parametrize('question', [
    '2025年南湾每笔核算额，2026年文档计算公式，费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾北岭每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年2026年南湾每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾特殊口径每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾每笔核算额，计算公式用文档，2026年费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾每笔核算额，计算公式用文档，北岭费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾每笔核算额，计算公式用文档，不含南湾费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾每笔核算额，费用金额合计从数据库取值，包裹ID记录计数从数据库取值。',
    '2025年南湾每笔核算额，按2026年文档预测，费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年不含北岭每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
])
def test_reverse_source_does_not_guess_conflicting_or_unknown_target_scope(environment, question):
    result = run(environment, question, split_tasks())
    assert result['status'] == 'clarification', result
    assert result['result']['results'] == {}


@pytest.mark.parametrize('question', [
    '2025年华东平均客单价，计算公式用文档，销售额和订单数从数据库取值',
    '按指标计算文档中的客单价公式计算2025年华东地区客单价，销售額和订单数从数据库取值。',
    '按指标公式文档计算2025年华东地区客单价，销售额和订单数从数据库取。',
    '按指标文档公式计算2025年华东地区客单价，销售额和订单数从数据库取值。',
])
def test_historical_formula_questions_execute_server_bound_sales_scope(tmp_path, question):
    # This is the actual shipped sales schema/seed, not a rewritten model
    # question or an assertion over a precomputed expected SourceClause.
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'demo.sqlite'))
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest('客单价 = 销售额 / 订单数'.encode(), document_id='formula',
                     title='经营指标公式', modality='txt', filename='formula.txt')
    execution_tasks = [
        {'id': 'f', 'tool': 'document_formula', 'args': {'document_id': 'formula', 'label': '客单价'}},
        {'id': 's', 'tool': 'sql', 'args': {'question': '2025年华东地区销售额和订单数'}},
        {'id': 'c', 'tool': 'calculate', 'args': {
            'formula': {'ref': 'f', 'path': []}, 'parameters': {
                '销售额': {'ref': 's', 'path': ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0]},
                '订单数': {'ref': 's', 'path': ['aggregate_cells', 'sales_orders', 'order_id', 'COUNT', 0]},
            }}},
    ]
    result = DependencyAgent(engine, knowledge).run(execution_tasks, original_question=question)
    assert result['status'] == 'ok', result
    assert result['results']['c']['value'] == pytest.approx(29584 / 3)
    scope = result['user_constraint_validation']['bindings'][0]
    left, right = scope['qualifier_ranges'][0]
    assert question[left:right] in {'2025年华东', '2025年华东地区'}
    assert '客单价' not in scope['text']
    assert result['results']['s']['provenance']['source_constraint_validation']['status'] == 'verified'


def test_reverse_target_year_keeps_explicit_database_exclusion(environment):
    question = '2025年每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数（不含北岭）从数据库取值。'
    result = run(environment, question, split_tasks())
    assert result['status'] == 'ok', result
    assert result['result']['results']['c']['value'] == 30
    scope = result['result']['user_constraint_validation']['bindings'][0]
    assert scope['text'] == '2025年 费用金额合计和包裹ID记录计数（不含北岭）'


@pytest.mark.parametrize('role', ['预测', '目标', '基准', '历史', '未来', '预计', '预估', '计划'])
def test_reverse_formula_does_not_turn_a_target_role_into_database_scope(environment, role):
    question = f'按文档{role}每笔核算额公式计算2026年南湾每笔核算额，费用金额合计和包裹ID记录计数从数据库取值。'
    result = run(environment, question, split_tasks())
    assert result['status'] == 'clarification', result
    assert result['result']['error_code'] == 'source_scope_unverified'
    assert result['result']['results'] == {}


def test_reverse_explicit_baseline_scope_stays_separate_from_forecast_target(environment):
    question = '按文档预测每笔核算额公式计算2026年南湾每笔核算额，2025年南湾费用金额合计和包裹ID记录计数从数据库取值。'
    execution_tasks = split_tasks('2025年南湾包裹台账包裹ID记录计数')
    execution_tasks[1]['args']['question'] = '2025年南湾包裹台账费用金额合计'
    result = run(environment, question, execution_tasks)
    assert result['status'] == 'ok', result
    scope = result['result']['user_constraint_validation']['bindings'][0]
    assert scope['text'] == '2025年南湾费用金额合计和包裹ID记录计数'
    assert scope['qualifier_ranges'] == ()
    assert result['result']['results']['c']['value'] == 30


@pytest.mark.parametrize('question', [
    '按公式文档计算2025年南湾未知核算额，费用金额合计和包裹ID记录计数从数据库取。',
    '按文档公式计算2025年南湾未知核算额，费用金额合计和包裹ID记录计数从数据库取。',
    '按公式文档预测计算2026年南湾每笔核算额，费用金额合计和包裹ID记录计数从数据库取。',
])
def test_generic_formula_document_does_not_authorize_an_unknown_target(environment, question):
    result = run(environment, question, split_tasks())
    assert result['status'] == 'clarification', result
    assert result['result']['results'] == {}


@pytest.mark.parametrize('question', [
    '2025年南湾每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '按文档每笔核算额公式计算2025年南湾每笔核算额，费用金额合计和包裹ID记录计数从数据库取值。',
    '按文档预测每笔核算额公式计算2026年南湾每笔核算额，2025年南湾费用金额合计和包裹ID记录计数从数据库取值。',
])
def test_unknown_domain_target_requires_actual_exact_formula_label_proof(environment, question):
    engine, _ = environment
    proof = VerifiedFormulaTarget('每笔核算额', 'per-record-formula', 'a' * 64, 'f')
    clauses = extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                                     verified_formula_targets=(proof,))
    assert clauses[0].target_binding['mode'] == 'verified_document_formula_exact_label'
    assert clauses[0].target_binding['label'] == proof.label
    left, right = clauses[0].target_binding['range']
    assert question[left:right] == '每笔核算额'
    assert '2025年南湾' in clauses[0].text
    if '预测' in question:
        assert '2026年' not in clauses[0].text


@pytest.mark.parametrize('question', [
    '2025年南湾已批准每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '2025年南湾每笔已批准核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。',
    '按文档预测每笔核算额公式计算2026年南湾已批准每笔核算额，2025年南湾费用金额合计和包裹ID记录计数从数据库取值。',
    '按文档已批准每笔核算额公式计算2025年南湾每笔核算额，费用金额合计和包裹ID记录计数从数据库取值。',
])
def test_unknown_target_qualifier_never_disappears_even_with_formula_or_baseline(environment, question):
    engine, _ = environment
    proof = VerifiedFormulaTarget('每笔核算额', 'per-record-formula', 'a' * 64, 'f')
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                               verified_formula_targets=(proof,))


@pytest.mark.parametrize('target', ['已批准客单价', '有效客单价', '退款后客单价'])
def test_catalog_target_coverage_applies_to_standalone_business_qualifiers(tmp_path, target):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    question = f'2025年华东{target}，计算公式用文档，销售额和订单数从数据库取值。'
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine)


def test_internal_document_search_range_is_audited_without_masking_sql_scope(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    question = '先从数据库查2025年销售额排名第一的地区，再根据该地区检索冠军团队的方法，保留来源。'
    start, end = question.index('再根据'), question.index('，保留')
    sql_tasks = [{'id': 's', 'tool': 'sql', 'args': {'question': '2025年销售额排名第一的地区'}}]
    bound, audit = bind_source_constraints(question, sql_tasks, engine,
                                           verified_document_search_ranges=((start, end),))
    assert bound['s'].top_n == 1 and bound['s'].dimensions == ['region']
    assert audit[0]['document_search_ranges'] == ((start, end),)
    with pytest.raises(SourceConstraintError):
        bind_source_constraints(question, sql_tasks, engine,
                                 verified_document_search_ranges=((0, question.index('，')),))


def test_stored_date_year_head_cannot_impersonate_an_explicit_baseline_region(environment):
    engine, _ = environment
    question = '按文档预测每笔核算额公式计算2025年南湾每笔核算额，2026年费用金额合计和包裹ID记录计数从数据库取值。'
    proof = VerifiedFormulaTarget('每笔核算额', 'per-record-formula', 'a' * 64, 'f')
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                               verified_formula_targets=(proof,))


def test_formula_target_range_points_to_exact_original_noun_after_spaces(environment):
    engine, _ = environment
    question = '2025年南湾 每笔核算额  ，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。'
    proof = VerifiedFormulaTarget('每笔核算额', 'per-record-formula', 'a' * 64, 'f')
    clauses = extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                                     verified_formula_targets=(proof,))
    left, right = clauses[0].target_binding['range']
    assert question[left:right] == '每笔核算额'


def test_equivalent_formula_reads_keep_actual_task_identity_candidates(environment):
    engine, _ = environment
    question = '2025年南湾每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。'
    proofs = (VerifiedFormulaTarget('每笔核算额', 'per-record-formula', 'a' * 64, 'formula_unused'),
              VerifiedFormulaTarget('每笔核算额', 'per-record-formula', 'a' * 64, 'formula_used'))
    clauses = extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                                     verified_formula_targets=proofs)
    binding = clauses[0].target_binding
    assert set(binding['formula_task_ids']) == {'formula_unused', 'formula_used'}
    assert binding['task_id'] in binding['formula_task_ids']
    assert binding['source_clause_ids'] == ('sql_scope_1',)


def test_average_catalog_alias_still_binds_to_real_formula_task(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    question = '2025年华东平均客单价，计算公式用文档，销售额和订单数从数据库取值。'
    proof = VerifiedFormulaTarget('客单价', 'aov-formula', 'b' * 64, 'aov_read')
    clauses = extract_source_clauses(question, engine.schema(include_row_count=False), engine=engine,
                                     verified_formula_targets=(proof,))
    binding = clauses[0].target_binding
    assert binding['mode'] == 'verified_document_formula_catalog_target'
    assert binding['label'] == '客单价'
    assert binding['task_id'] == 'aov_read' and binding['formula_task_ids'] == ('aov_read',)


def test_unused_matching_formula_proof_cannot_authorize_different_actual_calculation(environment):
    engine, knowledge = environment
    knowledge.ingest('双倍核算额 = 总费用 * 2'.encode(), document_id='double-formula',
                     title='辅助公式', modality='txt', filename='double.txt')
    question = '2025年南湾每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。'
    execution_tasks = split_tasks('2025年南湾包裹台账包裹ID记录计数')
    execution_tasks[1]['args']['question'] = '2025年南湾包裹台账费用金额合计'
    execution_tasks.insert(1, {'id': 'other', 'tool': 'document_formula',
                              'args': {'document_id': 'double-formula', 'label': '双倍核算额'}})
    execution_tasks[-1]['args'] = {'formula': {'ref': 'other', 'path': []},
                                  'parameters': {'总费用': cell('amount')}}
    result = run(environment, question, execution_tasks)
    assert result['status'] != 'ok', result
    assert result['result']['results'].get('c', {}).get('value') != 120


def test_formula_target_contract_requires_every_explicit_target_to_be_consumed():
    # Internal contract audit fixture: two independently bound source/target
    # scopes must not be "covered" by a terminal for only the first target.
    tasks = [
        {'id': 'fa', 'tool': 'document_formula', 'args': {'document_id': 'a', 'label': '甲核算额'}},
        {'id': 'fb', 'tool': 'document_formula', 'args': {'document_id': 'b', 'label': '乙核算额'}},
        {'id': 'sa', 'tool': 'sql', 'args': {'question': '甲费用合计'}},
        {'id': 'sb', 'tool': 'sql', 'args': {'question': '乙费用合计'}},
        {'id': 'ca', 'tool': 'calculate', 'args': {'formula': {'ref': 'fa', 'path': []},
                                                'parameters': {'甲费用': {'ref': 'sa', 'path': ['rows', 0, '费用']}}}},
    ]
    dependencies = {task['id']: DependencyAgent.references(task['args']) for task in tasks}
    formula_results = {task: {'label': label, 'sha256': sha,
                              'source_uri': f'/api/v1/knowledge/documents/{document}/original',
                              'expression': f'{label}*1'}
                       for task, label, document, sha in [('fa', '甲核算额', 'a', 'a' * 64),
                                                           ('fb', '乙核算额', 'b', 'b' * 64)]}
    bindings = [{'task_id': sql, 'target_binding': {
        'mode': 'verified_document_formula_exact_label', 'target_id': target,
        'task_id': formula, 'formula_task_ids': (formula,), 'label': label,
        'document_id': document, 'source_sha256': sha}}
        for sql, formula, target, label, document, sha in [
            ('sa', 'fa', 'target:a', '甲核算额', 'a', 'a' * 64),
            ('sb', 'fb', 'target:b', '乙核算额', 'b', 'b' * 64)]]
    with pytest.raises(SourceConstraintError):
        DependencyAgent._formula_consumption_contracts(tasks, dependencies, bindings, formula_results)


def test_correct_formula_and_sql_ancestors_do_not_authorize_swapped_parameter_metrics(environment):
    question = '2025年南湾每笔核算额，计算公式用文档，费用金额合计和包裹ID记录计数从数据库取值。'
    execution_tasks = split_tasks('2025年南湾包裹台账包裹ID记录计数')
    execution_tasks[1]['args']['question'] = '2025年南湾包裹台账费用金额合计'
    execution_tasks[-1]['args']['parameters'] = {
        '总费用': cell('count', '包裹ID', 'COUNT'), '记录数': cell('amount')}
    result = run(environment, question, execution_tasks)
    assert result['status'] != 'ok', result
