"""Original fusion clauses constrain real SQL; planner stubs are not API scores."""
import json
import sqlite3
from copy import deepcopy

import pytest

from backend.dependency_agent import DependencyAgent
from backend.fusion_constraints import SourceConstraintError, extract_source_clauses, verify_required_intent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.models import FilterSpec, MetricSpec, QueryPlan
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


def test_table_identifier_substring_is_not_an_explicit_source():
    with pytest.raises(SourceConstraintError):
        extract_source_clauses('按文档核算PreOrdersArchive的费用', {'tables': [{'name': 'Orders'}]})
