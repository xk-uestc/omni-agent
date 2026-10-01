"""Static contracts checked against real local outputs of every DAG tool."""
from copy import deepcopy
from io import BytesIO

from openpyxl import Workbook
import pytest
import requests

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.text_reference_contract import _projection_type, text_reference_errors


@pytest.fixture(scope='module')
def outputs(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail('Actual shape checks must remain local and offline')
        patch.setattr(requests.sessions.Session, 'request', forbidden)
        root = tmp_path_factory.mktemp('actual-tool-shapes')
        store = KnowledgeStore(root / 'knowledge')
        documents = {
            'formulas': ('客单价 = 销售额 / 订单数\n'
                         '参数绑定: 销售额 = SUM(sales_orders.sales_amount)\n'
                         '参数绑定: 订单数 = COUNT(sales_orders.order_id)'),
            'service': '紧急工单应在2小时内首次响应。',
            'facts': '标准名称：紧急响应',
            'policy': ('2024版期限为5日，生效区间为2024-01-01至2024-12-31。\n'
                       '2025版期限为7日，自2025-01-01起生效。'),
        }
        for identifier, text in documents.items():
            store.ingest(text.encode(), document_id=identifier, title=identifier,
                         modality='txt', filename=identifier + '.txt')
        workbook = Workbook()
        workbook.active.title = '区域目标'
        workbook.active.append(['地区', '年份', '首次响应小时', '标志'])
        workbook.active.append(['华东', 2026, 2, True])
        stream = BytesIO()
        workbook.save(stream)
        store.ingest(stream.getvalue(), document_id='targets', title='区域目标',
                     modality='xlsx', filename='targets.xlsx')
        agent = DependencyAgent(Nl2SqlEngine(initialize_database(root / 'data.sqlite')), store)
        def execute(tool, args, original=None, results=None):
            return agent.execute(tool, args, args if original is None else original,
                                 {} if results is None else results)
        data = {}
        data['sql'] = execute('sql', {'question': '2025年华东地区销售额和订单数'})
        data['grouped_sql'] = execute('sql', {'question': '2025年各地区销售额'})
        data['document_formula'] = execute('document_formula', {'document_id': 'formulas', 'label': '客单价'})
        data['document_cell'] = execute('document_cell', {'document_id': 'targets',
            'where': {'地区': '华东', '年份': 2026}, 'column': '地区'})
        data['boolean_cell'] = execute('document_cell', {'document_id': 'targets',
            'where': {'地区': '华东', '年份': 2026}, 'column': '标志'})
        data['document_fact'] = execute('document_fact', {'document_id': 'facts', 'label': '标准名称'})
        data['policy_select'] = execute('policy_select', {'document_id': 'policy', 'label': '期限', 'as_of': '2024-12-31'})
        right = execute('policy_select', {'document_id': 'policy', 'label': '期限', 'as_of': '2025-01-01'})
        data['search'] = execute('search', {'query': '紧急工单首次响应', 'document_id': 'service'})
        data['table_search'] = execute('search', {'query': '华东', 'document_id': 'targets'})
        fact_args = {'evidence': data['search'], 'scope': '紧急工单', 'label': '首次响应', 'unit': '小时'}
        data['search_fact'] = execute('search_fact', fact_args,
            {**fact_args, 'evidence': {'ref': 'search', 'path': []}}, {'search': data['search']})
        calc_original = {'formula': {'ref': 'formula', 'path': []}, 'parameters': {
            '销售额': {'ref': 'sql', 'path': ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0]},
            '订单数': {'ref': 'sql', 'path': ['aggregate_cells', 'sales_orders', 'order_id', 'COUNT', 0]}}}
        sources = {'formula': data['document_formula'], 'sql': data['sql']}
        data['calculate'] = execute('calculate', DependencyAgent.resolve(calc_original, sources), calc_original, sources)
        data['compare'] = execute('compare', {'left': data['policy_select'], 'right': right, 'operator': 'le'},
            {'left': {'ref': 'left', 'path': []}, 'right': {'ref': 'right', 'path': []}, 'operator': 'le'},
            {'left': data['policy_select'], 'right': right})
        return data


def assert_navigation(tool, path, output):
    reference = {'ref': 'source', 'path': path}
    tasks = [{'id': 'source', 'tool': tool, 'args': {}},
             {'id': 'query', 'tool': 'sql', 'args': {'question': ['导航', reference]}}]
    original = deepcopy(tasks)
    assert text_reference_errors(tasks) == [], (tool, path, _projection_type(tool, path))
    value = DependencyAgent.resolve(reference, {'source': output})
    assert not isinstance(value, bool) and isinstance(value, (str, int, float))
    assert DependencyAgent.text(['导航', value]).startswith('导航 ')
    assert DependencyAgent.references(tasks[1]['args']) == {'source'} and tasks == original


def test_rows_and_physical_dimension_values_are_actual_scalar_leaves(outputs):
    grouped = outputs['grouped_sql']
    assert grouped['dimension_values']['sales_orders']['region']
    for field in grouped['rows'][0]:
        assert_navigation('sql', ['rows', 0, field], grouped)
    assert_navigation('sql', ['dimension_values', 'sales_orders', 'region', 0], grouped)
    for path in [['columns', 0], ['plan', 'dimensions', 0], ['plan', 'dimension_tables', 'region']]:
        assert_navigation('sql', path, grouped)


@pytest.mark.parametrize('field', ['value', 'unit', 'source_uri', 'locator'])
def test_aggregate_cell_actual_leaf_fields(outputs, field):
    assert_navigation('sql', ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0, field], outputs['sql'])


@pytest.mark.parametrize('field', ['id', 'label', 'table', 'column', 'function'])
def test_aggregate_metric_descriptor_actual_deep_string_fields(outputs, field):
    assert_navigation('sql', ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0, 'metric', field], outputs['sql'])


@pytest.mark.parametrize('path', [
    ['expression'], ['parameters', 0], ['source_uri'], ['locator'],
    ['parameter_contracts', '销售额', 'table'],
    ['parameter_contract_sources', 0, 'quote_basis'],
])
def test_formula_real_output_projections(outputs, path):
    assert_navigation('document_formula', path, outputs['document_formula'])


@pytest.mark.parametrize('tool,path', [
    ('document_cell', ['value']), ('document_cell', ['matched_conditions', '地区']),
    ('document_fact', ['value']), ('policy_select', ['raw_value']),
    ('policy_select', ['valid_from']), ('policy_select', ['document_id']),
    ('policy_select', ['value']), ('search_fact', ['validation']),
    ('search_fact', ['sources', 0, 'quote']),
    ('search_fact', ['sources', 0, 'label_binding', 'literal_label']),
    ('calculate', ['result_unit']), ('calculate', ['formula_source']),
    ('calculate', ['formula_locator']), ('calculate', ['parameters', '销售额', 'locator']),
    ('calculate', ['normalized_parameters', '销售额']),
    ('calculate', ['trace', 1, 'sources', 0]),
    ('calculate', ['parameter_semantics_validation', 'bindings', 0, 'function']),
    ('compare', ['left', 'raw_value']), ('compare', ['right', 'valid_from']),
])
def test_every_tool_uses_its_actual_public_fields(outputs, tool, path):
    assert_navigation(tool, path, outputs[tool])


@pytest.mark.parametrize('path', [
    ['search_scope', 'mode'], ['search_scope', 'document_id'], ['search_scope', 'source_sha256'],
    ['hits', 0, 'metadata', 'retrieval_channel'],
    ['hits', 0, 'metadata', 'retrieval_language_policy', 'strategy'],
    ['hits', 0, 'metadata', 'evidence_selection', 'body_sha256'],
])
def test_search_actual_scope_and_metadata_string_projections(outputs, path):
    assert_navigation('search', path, outputs['search'])


@pytest.mark.parametrize('tail', [
    ['sheet_name'], ['title_path', 0], ['table_name'], ['headers', 0],
    ['values', 0, 'raw_value'], ['source_headers', 0], ['source_row_cells', 0, 'raw_value'],
])
def test_real_table_search_metadata_keeps_legal_navigation_projections(outputs, tail):
    search = outputs['table_search']
    index = next(i for i, hit in enumerate(search['hits']) if 'values' in hit['metadata'])
    assert_navigation('search', ['hits', index, 'metadata', *tail], search)


@pytest.mark.parametrize('tool,path', [
    ('calculate', ['unit']), ('document_fact', ['label']),
    ('document_cell', ['quote']), ('document_formula', ['value']),
    ('sql', ['aggregate_cells', 'sales_orders', 'sales_amount', 'SUM', 0, 'sha256']),
])
def test_nonexistent_fields_are_unknown_instead_of_invented_scalar_contracts(outputs, tool, path):
    assert _projection_type(tool, path) == 'unknown'
    with pytest.raises(DependencyPlanError):
        DependencyAgent.resolve({'ref': 'source', 'path': path}, {'source': outputs[tool]})


def test_known_nullable_and_boolean_cell_values_still_need_runtime_eligibility(outputs):
    search = outputs['search']
    assert search['hits'][0]['metadata']['sheet_name'] is None
    assert _projection_type('search', ['hits', 0, 'metadata', 'sheet_name']) == 'string|null'
    assert outputs['boolean_cell']['value'] is True
    for value in [None, outputs['boolean_cell']['value']]:
        with pytest.raises(DependencyPlanError):
            DependencyAgent.text(value)


def test_matched_terms_tuple_is_not_a_legal_resolve_array_in_existing_contract(outputs):
    assert isinstance(outputs['search']['hits'][0]['matched_terms'], tuple)
    with pytest.raises(DependencyPlanError):
        DependencyAgent.resolve({'ref': 'source', 'path': ['hits', 0, 'matched_terms', 0]}, {'source': outputs['search']})
    assert _projection_type('search', ['hits', 0, 'matched_terms', 0]) == 'unknown'


def test_search_numeric_reference_must_be_inside_a_flat_query_list(outputs):
    reference = {'ref': 'source', 'path': ['hits', 0, 'score']}
    root = [{'id': 'source', 'tool': 'search', 'args': {}},
            {'id': 'query', 'tool': 'search', 'args': {'query': reference}}]
    part = deepcopy(root)
    part[1]['args']['query'] = ['导航', reference]
    assert text_reference_errors(root)[0]['code'] == 'text_reference_incompatible_type'
    assert text_reference_errors(root)[0]['allowed_types'] == ['string', 'flat_array<string|number>']
    assert text_reference_errors(part) == []
