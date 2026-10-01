"""Pure original-question binding, including label substring regression."""
from copy import deepcopy

import pytest

from backend.visual_table_reader import bind_visual_table_question


def manifest(row='West', header='2025 Actual'):
    fact = {'fact_id': 'cell-west', 'fact_key': {
        'row_header': row, 'column_header_path': [header]}, 'raw_value': '3578'}
    return {'tables': [{'table_id': 't1', 'row_headers': [row],
                        'column_header_paths': [[header]], 'facts': [fact]}]}


@pytest.mark.parametrize('question', ['What is West 2025 Actual?', '请查询 West 2025 Actual 的值是多少？',
                                     '  show WEST 2025   ACTUAL  '])
def test_binding_preserves_question_and_manifest(question):
    tables = manifest()
    original = deepcopy(tables)
    result = bind_visual_table_question(question, tables)
    assert result == {'status': 'bound', 'clarification_code': None, 'fact': tables['tables'][0]['facts'][0]}
    assert tables == original


@pytest.mark.parametrize('question', ['West 2025 Actual showWest', 'West 2025 Actual Westshow',
                                     'West 2025 Actual meWest', 'West 2025 Actual West2',
                                     'West 2025 Actual if approved', 'West 2025 Actual above 5000',
                                     'West 2025 Actual increased by 10', 'West 2025 Actual 2026'])
def test_extra_scope_or_embedded_label_is_not_erased(question):
    result = bind_visual_table_question(question, manifest())
    assert result['status'] == 'incomplete' and result['fact'] is None
    assert result['clarification_code'] == 'single_cell_question_has_unbound_terms'


@pytest.mark.parametrize('question', ['West amount', '2025 Actual', 'Southwest 2025 Actual'])
def test_missing_or_embedded_row_label_cannot_bind(question):
    assert bind_visual_table_question(question, manifest())['clarification_code'] == 'cell_scope_ambiguous_or_not_explicit'


def test_duplicate_cell_across_tables_requires_clarification():
    tables = manifest()
    tables['tables'].append(deepcopy(tables['tables'][0]))
    assert bind_visual_table_question('West 2025 Actual', tables)['clarification_code'] == 'cell_scope_ambiguous_or_not_explicit'


def test_year_in_row_does_not_satisfy_column_period_scope():
    result = bind_visual_table_question('West 2025 Actual', manifest(row='West 2025', header='Actual'))
    assert result['clarification_code'] == 'cell_period_scope_mismatch'


def test_operation_word_inside_actual_header_is_allowed():
    assert bind_visual_table_question('West 2025 Growth', manifest(header='2025 Growth'))['status'] == 'bound'


def test_empty_registry_and_registry_budgets():
    assert bind_visual_table_question('West 2025 Actual', {'tables': []})['clarification_code'] == 'no_verified_native_grid'
    tables = manifest()
    tables['tables'] *= 9
    assert bind_visual_table_question('West 2025 Actual', tables)['clarification_code'] == 'table_selection_budget_exceeded'
    assert bind_visual_table_question('West 2025 Actual', manifest(header='X' * 40001))['clarification_code'] == 'table_selection_budget_exceeded'


@pytest.mark.parametrize('question', ['', ' ', None, 123, 'x' * 1001])
def test_invalid_question_raises_without_model(question):
    with pytest.raises(ValueError):
        bind_visual_table_question(question, manifest())
