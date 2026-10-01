"""Dynamic document -> SQL scope tests with local sources, no model calls."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from backend.dependency_agent import DependencyAgent
from backend.dynamic_source_binding import bind_dynamic_source_filters, apply_dynamic_source_filters
from backend.fusion_constraints import SourceConstraintError
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.models import FilterSpec
from backend.nl2sql.seed import initialize_database


QUESTION = '从区域目标Excel中定位2026年目标增长率为12%的地区，作为过滤条件查询数据库中该地区2025年的销售额。'
SCOPE = '该地区2025年的销售额'


def tasks():
    return [{'id': 'region', 'tool': 'document_cell', 'args': {
        'document_id': 'region-targets', 'where': {'年份': 2026, '目标增长率': .12}, 'column': '地区'}},
        {'id': 'sales', 'tool': 'sql', 'args': {'question': ['2025年', {'ref': 'region', 'path': ['value']}, '地区销售额']}}]


@pytest.fixture
def context(tmp_path):
    store = KnowledgeStore(tmp_path / 'knowledge')
    wb = Workbook()
    wb.active.append(['地区', '年份', '目标增长率', '客户'])
    wb.active.append(['华东', 2026, .12, '东辰科技'])
    wb.active.append(['华南', 2026, .10, '云海传媒'])
    wb.active.append(['华东', 2025, .12, '东辰科技'])
    raw = BytesIO()
    wb.save(raw)
    store.ingest(raw.getvalue(), document_id='region-targets', title='区域目标表', modality='xlsx', filename='targets.xlsx')
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'demo.sqlite'))
    agent = DependencyAgent(engine, store)
    required = engine.extract_required_intent(SCOPE)
    return agent, required


def bind(context, graph=None, question=QUESTION, result_mutator=None):
    agent, required = context
    graph = graph or tasks()
    provider = graph[0]
    result = agent.execute('document_cell', provider['args'], provider['args'], {})
    if result_mutator:
        result_mutator(result)
    return bind_dynamic_source_filters(agent, task=graph[1], tasks=graph,
        results={'region': result}, required_intent=required, original_question=question)


def test_actual_cell_bound_independently_from_value_index(context):
    agent, original = context
    bound = bind(context)
    descriptor = bound.source_dynamic_filters[0]
    assert descriptor['table'] == 'sales_orders' and descriptor['column'] == 'region'
    assert descriptor['value'] == '华东' and descriptor['qualifier'] == '该地区'
    assert not hasattr(original, 'source_dynamic_filters')
    result = agent.sql_engine.answer('2025年华东地区销售额', required_intent=bound).to_dict()
    assert result['status'] == 'ok' and result['rows'][0]['销售额'] == 29584
    assert result['provenance']['source_constraint_validation']['status'] == 'verified'


@pytest.mark.parametrize('query', ['2025年华南地区销售额', '2025年地区销售额', '2026年华东地区销售额',
                                  '2025年华东地区平均销售额', '2025年华东地区手机销售额'])
def test_engine_cannot_omit_overwrite_or_expand_scope(context, query):
    agent, _ = context
    result = agent.sql_engine.answer(query, required_intent=bind(context)).to_dict()
    assert result['status'] != 'ok' and result['rows'] == []
    assert result.get('clarification_code') == 'source_constraint_mismatch'


@pytest.mark.parametrize('change', [
    lambda args: args['where'].update(年份=2025),
    lambda args: args['where'].update(目标增长率=.10),
    lambda args: args['where'].update(地区='华东'),
    lambda args: args.update(column='客户'),
    lambda args: args['where'].update(年份=2026.0),
])
def test_provider_selector_is_original_authorized_not_same_value(context, change):
    graph = tasks()
    change(graph[0]['args'])
    with pytest.raises(SourceConstraintError):
        bind(context, graph)


def test_wrong_document_and_duplicate_normalized_title_rejected(context):
    agent, _ = context
    path, filename = agent.knowledge_store.original('region-targets')
    agent.knowledge_store.ingest(path.read_bytes(), document_id='other', title='区域目标表', modality='xlsx', filename=filename)
    with pytest.raises(SourceConstraintError):
        bind(context)
    graph = tasks()
    graph[0]['args']['document_id'] = 'other'
    with pytest.raises(SourceConstraintError):
        bind(context, graph)


@pytest.mark.parametrize('path', [[], ['unit'], ['matched_conditions', '年份']])
def test_nonvalue_reference_not_authorized(context, path):
    graph = tasks()
    graph[1]['args']['question'][1]['path'] = path
    with pytest.raises(SourceConstraintError):
        bind(context, graph)


@pytest.mark.parametrize('field,value', [('value', '华南'), ('sha256', '0' * 64), ('locator', 'invented')])
def test_forged_runtime_result_fails_reexecution(context, field, value):
    with pytest.raises(SourceConstraintError):
        bind(context, result_mutator=lambda result: result.update({field: value}))


def test_independent_column_mapping_cannot_follow_coincidental_value(context, monkeypatch):
    agent, _ = context
    monkeypatch.setattr(agent.sql_engine, 'analyze_slots', lambda _: {
        'dimensions': [SimpleNamespace(table='sales_orders', column='region'),
                       SimpleNamespace(table='customers', column='customer_name')], 'values': []})
    with pytest.raises(SourceConstraintError):
        bind(context)


def test_descriptor_schema_and_qualifier_rechecked(context):
    agent, _ = context
    bound = bind(context)
    with agent.sql_engine._connect() as connection:
        tables, _, _ = agent.sql_engine._snapshot_for(connection)
    for key, value in [('column', 'nonexistent'), ('qualifier', '该客户'), ('value', True), ('value', 12)]:
        descriptors = deepcopy(bound.source_dynamic_filters)
        descriptors[0][key] = value
        with pytest.raises(SourceConstraintError):
            apply_dynamic_source_filters(bound, descriptors, tables=tables, scope_question=SCOPE)


def test_conflicting_expected_filter_not_replaced(context):
    agent, required = context
    descriptor = bind(context).source_dynamic_filters
    required.filters.append(FilterSpec('region', '=', '华南', 'original', 'original', 'sales_orders'))
    with agent.sql_engine._connect() as connection:
        tables, _, _ = agent.sql_engine._snapshot_for(connection)
    with pytest.raises(SourceConstraintError):
        apply_dynamic_source_filters(required, descriptor, tables=tables, scope_question=SCOPE)
    assert required.filters[-1].value == '华南'


def test_no_original_question_preserves_direct_graph(context):
    agent, _ = context
    assert agent.run(tasks())['status'] == 'ok'


def test_original_question_graph_uses_runtime_descriptor(context):
    agent, _ = context
    result = agent.run(tasks(), original_question=QUESTION)
    assert result['status'] == 'ok', result
    assert result['results']['sales']['rows'][0]['销售额'] == 29584
    assert result['results']['region']['matched_conditions'] == {'年份': 2026, '目标增长率': .12}


@pytest.mark.parametrize('extra', ['已批准', '有效', '未作废', '仅线上', '销售额最多'])
def test_unknown_selector_cannot_disappear(context, extra):
    question = QUESTION.replace('的地区', extra + '的地区')
    with pytest.raises(SourceConstraintError):
        bind(context, question=question)


def test_descriptor_cannot_point_to_other_real_dimension(context):
    agent, _ = context
    bound = bind(context)
    bound.source_dynamic_filters[0].update(table='customers', column='customer_name')
    result = agent.sql_engine.answer('2025年华东地区销售额', required_intent=bound).to_dict()
    assert result['status'] == 'incomplete' and result['rows'] == []
    assert result['clarification_code'] == 'source_constraint_mismatch'


def test_prior_selector_qualifier_not_ignored(context):
    with pytest.raises(SourceConstraintError):
        bind(context, question='仅允许已批准记录，' + QUESTION)


def test_formula_condition_not_authorized_by_cached_literal(context, monkeypatch):
    agent, _ = context
    original_document = agent.knowledge_store.document
    def formula_document(document_id):
        document = original_document(document_id)
        for chunk in document['chunks']:
            metadata = chunk['metadata']
            cells = dict(zip(metadata.get('headers', []), metadata.get('values', [])))
            if cells.get('年份', {}).get('raw_value') == 2026:
                cells['年份']['formula'] = '=2026'
        return document
    monkeypatch.setattr(agent.knowledge_store, 'document', formula_document)
    with pytest.raises(SourceConstraintError):
        bind(context)


def test_source_revision_between_cell_and_sql_is_rejected(context):
    agent, required = context
    graph = tasks()
    result = agent.execute('document_cell', graph[0]['args'], graph[0]['args'], {})
    path, filename = agent.knowledge_store.original('region-targets')
    # Same payload plus a ZIP comment changes source identity without changing
    # the selected value: provenance still must reject it.
    import zipfile
    raw = BytesIO(path.read_bytes())
    with zipfile.ZipFile(raw, 'a') as archive:
        archive.comment = b'new revision'
    agent.knowledge_store.ingest(raw.getvalue(), document_id='region-targets', title='区域目标表',
                               modality='xlsx', filename=filename)
    with pytest.raises(SourceConstraintError):
        bind_dynamic_source_filters(agent, task=graph[1], tasks=graph, results={'region': result},
                                    required_intent=required, original_question=QUESTION)


def test_dynamic_attributes_not_serialized_to_model_or_public_plan(context):
    bound = bind(context)
    assert hasattr(bound, 'source_dynamic_filters')
    assert 'source_dynamic_filters' not in bound.to_dict()


def test_plain_literal_query_cannot_fake_dependency(context):
    graph = tasks()
    graph[1]['args']['question'] = '2025年华东地区销售额'
    with pytest.raises(SourceConstraintError):
        bind(context, graph)
