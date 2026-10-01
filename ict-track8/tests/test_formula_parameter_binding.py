"""Formula operand swaps are checked by schema semantics, never their value."""
from io import BytesIO
import sqlite3

import pytest
from openpyxl import Workbook

from backend.dependency_agent import DependencyAgent
from backend.formula_parameter_binding import validate_formula_sql_parameters
from backend.fusion_constraints import SourceConstraintError
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


@pytest.fixture
def parcel(tmp_path):
    path = tmp_path / 'parcel.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE 包裹台账(包裹ID INTEGER PRIMARY KEY, 区域 TEXT, 出库日期 TEXT, 费用金额 REAL)')
        connection.executemany('INSERT INTO 包裹台账 VALUES(?,?,?,?)', [(1, '南湾', '2025-01-01', 40), (2, '南湾', '2025-02-01', 20)])
    engine = Nl2SqlEngine(path, metric_catalog_path=tmp_path / 'absent.json')
    agent = DependencyAgent(engine, KnowledgeStore(tmp_path / 'knowledge'))
    amount = agent.execute('sql', {'question': '2025年南湾包裹台账费用金额合计'}, {}, {})
    count = agent.execute('sql', {'question': '2025年南湾包裹台账包裹ID记录计数'}, {}, {})
    return engine, {'amount': amount, 'count': count}


def bindings(results, aggregate=True, swapped=False, amount_name='总费用'):
    source_amount, source_count = ('count', 'amount') if swapped else ('amount', 'count')
    original = {}
    for name, source in [(amount_name, source_amount), ('记录数', source_count)]:
        result = results[source]
        plan = result['plan']
        path = (['aggregate_cells', plan['metric_table'], plan['metric_column'], plan['metric_function'], 0]
                if aggregate else ['rows', 0, plan['metric_label']])
        original[name] = {'ref': source, 'path': path}
    return original, {name: DependencyAgent.resolve(ref, results) for name, ref in original.items()}


@pytest.mark.parametrize('aggregate', [True, False])
@pytest.mark.parametrize('amount_name', ['总费用', '基准费用'])
def test_unknown_schema_sum_and_record_count_have_correct_physical_contract(parcel, aggregate, amount_name):
    engine, results = parcel
    original, resolved = bindings(results, aggregate, amount_name=amount_name)
    audit = validate_formula_sql_parameters(engine, {'parameters': [amount_name, '记录数']}, original, resolved, results)
    assert audit['status'] == 'verified'
    assert {(item['parameter'], item['column'], item['function']) for item in audit['bindings']} == {
        (amount_name, '费用金额', 'SUM'), ('记录数', '包裹ID', 'COUNT')}


@pytest.mark.parametrize('aggregate', [True, False])
def test_swapped_sum_count_is_rejected_even_with_valid_sql_provenance(parcel, aggregate):
    engine, results = parcel
    original, resolved = bindings(results, aggregate, swapped=True)
    with pytest.raises(SourceConstraintError) as error:
        validate_formula_sql_parameters(engine, {'parameters': ['总费用', '记录数']}, original, resolved, results)
    assert error.value.code == 'formula_parameter_semantics_mismatch'


def test_equal_values_and_same_unknown_units_cannot_authorize_swap(parcel):
    engine, results = parcel
    for result in results.values():
        label = result['plan']['metric_label']
        result['rows'][0][label] = 2
        from backend.sql_evidence import aggregate_evidence
        result['aggregate_cells'] = aggregate_evidence(result)[0]
    original, resolved = bindings(results, swapped=True)
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': ['总费用', '记录数']}, original, resolved, results)


def test_aggregate_leaf_descriptor_cannot_claim_another_physical_metric(parcel):
    engine, results = parcel
    original, resolved = bindings(results)
    resolved['总费用']['metric']['column'] = '包裹ID'
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': ['总费用', '记录数']}, original, resolved, results)


def test_display_label_cannot_turn_count_into_total_amount(parcel):
    engine, results = parcel
    count = results['count']
    old_label = count['plan']['metric_label']
    count['rows'][0]['总费用'] = count['rows'][0].pop(old_label)
    count['plan']['metric_label'] = '总费用'
    original = {'总费用': {'ref': 'count', 'path': ['rows', 0, '总费用']}}
    resolved = {'总费用': DependencyAgent.resolve(original['总费用'], results)}
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': ['总费用']}, original, resolved, results)


@pytest.mark.parametrize('name', ['伪造费用', '预测费用', '总不明费用'])
def test_unknown_business_prefix_is_not_discarded(parcel, name):
    engine, results = parcel
    original, resolved = bindings(results, amount_name=name)
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)


def test_average_parameter_cannot_use_sum_slot(parcel):
    engine, results = parcel
    original, resolved = bindings(results, amount_name='平均费用')
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)


@pytest.mark.parametrize('aggregate', [True, False])
def test_catalog_sales_and_order_count_bindings(tmp_path, aggregate):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    agent = DependencyAgent(engine, KnowledgeStore(tmp_path / 'knowledge'))
    result = agent.execute('sql', {'question': '2025年华东销售额和订单数'}, {}, {})
    results, original = {'sales': result}, {}
    for name, column, function in [('基准销售额', 'sales_amount', 'SUM'), ('订单数', 'order_id', 'COUNT')]:
        metric = next(metric for metric in result['plan']['metrics'] if metric['column'] == column)
        original[name] = {'ref': 'sales', 'path': ['aggregate_cells', 'sales_orders', column, function, 0]
                          if aggregate else ['rows', 0, metric['label']]}
    resolved = {name: DependencyAgent.resolve(ref, results) for name, ref in original.items()}
    assert validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)['status'] == 'verified'
    original['基准销售额'], original['订单数'] = original['订单数'], original['基准销售额']
    resolved = {name: DependencyAgent.resolve(ref, results) for name, ref in original.items()}
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)


def test_distinct_is_never_substituted_for_record_count(parcel):
    engine, results = parcel
    agent = DependencyAgent(engine, KnowledgeStore(engine.database_path.parent / 'distinct-corpus'))
    results['count'] = agent.execute('sql', {'question': '2025年南湾包裹台账包裹ID去重计数'}, {}, {})
    assert results['count']['plan']['metric_function'] == 'COUNT_DISTINCT'
    original, resolved = bindings(results)
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': ['总费用', '记录数']}, original, resolved, results)
    original['去重记录数'], resolved['去重记录数'] = original.pop('记录数'), resolved.pop('记录数')
    assert validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)['status'] == 'verified'


def test_document_growth_parameter_keeps_complete_executed_cell(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    workbook = Workbook()
    workbook.active.append(['地区', '年份', '目标增长率'])
    workbook.active.append(['华东', 2026, .12])
    stream = BytesIO()
    workbook.save(stream)
    knowledge.ingest(stream.getvalue(), document_id='targets', title='目标', modality='xlsx', filename='target.xlsx')
    agent = DependencyAgent(engine, knowledge)
    results = {'base': agent.execute('sql', {'question': '2025年华东销售额'}, {}, {}),
               'growth': agent.execute('document_cell', {'document_id': 'targets', 'where': {'地区': '华东', '年份': 2026}, 'column': '目标增长率'}, {}, {})}
    original = {'基准销售额': {'ref': 'base', 'path': ['rows', 0, '销售额']}, '目标增长率': {'ref': 'growth', 'path': []}}
    resolved = {name: DependencyAgent.resolve(ref, results) for name, ref in original.items()}
    audit = validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)
    assert audit['bindings'][1]['source_type'] == 'document'
    resolved['目标增长率'] = {**resolved['目标增长率'], 'value': .10}
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)


def test_unknown_parameter_and_ambiguous_same_named_schema_fail_closed(parcel):
    engine, results = parcel
    original, resolved = bindings(results)
    original['不明输入'], resolved['不明输入'] = original.pop('总费用'), resolved.pop('总费用')
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)
    with sqlite3.connect(engine.database_path) as connection:
        connection.execute('CREATE TABLE 另一台账(记录ID INTEGER PRIMARY KEY, 费用金额 REAL)')
    original, resolved = bindings(results)
    with pytest.raises(SourceConstraintError):
        validate_formula_sql_parameters(engine, {'parameters': ['总费用', '记录数']}, original, resolved, results)


def test_explicit_pinned_formula_contract_can_extend_unknown_parameter(parcel):
    engine, results = parcel
    original, resolved = bindings(results)
    original['投入'], resolved['投入'] = original.pop('总费用'), resolved.pop('总费用')
    formula = {'parameters': list(original), 'parameter_contracts': {'投入': {'table': '包裹台账', 'column': '费用金额', 'function': 'SUM'}}}
    assert validate_formula_sql_parameters(engine, formula, original, resolved, results)['status'] == 'verified'


def test_validator_does_not_invoke_or_mutate_provider(parcel):
    engine, results = parcel
    class NeverCall:
        def generate(self, *args, **kwargs):
            raise AssertionError('API not permitted')
    provider = NeverCall()
    engine.model_plan_provider = provider
    original, resolved = bindings(results)
    assert validate_formula_sql_parameters(engine, {'parameters': list(original)}, original, resolved, results)['status'] == 'verified'
    assert engine.model_plan_provider is provider
