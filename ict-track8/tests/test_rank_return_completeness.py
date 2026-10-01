"""Actual SQLite rank results separate rank count from model row-cap mistakes."""
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.model_contract import ModelPlanError
from backend.nl2sql.seed import initialize_database


QUESTION = '2025年销售额排名第一的地区'


def model_payload(*, limit=1, year=2025):
    return {'version': 2, 'metrics': [{'id': 'revenue', 'table': 'sales_orders',
        'column': 'sales_amount', 'function': 'SUM', 'label': '销售额',
        'unit': 'currency', 'currency': 'CNY', 'missing': 'null', 'filters': []}],
        'dimensions': [{'table': 'sales_orders', 'column': 'region', 'label': '地区'}],
        'filters': [{'table': 'sales_orders', 'column': 'order_date', 'operator': 'RANGE',
                     'value': [f'{year}-01-01', f'{year+1}-01-01']}],
        'analysis_mode': 'rank', 'top_n': 1, 'order_desc': True, 'limit': limit,
        'confidence': .99, 'rewritten_question': QUESTION}


def make_engine(tmp_path, *, ties=1, max_rows=100, payload=None):
    path = initialize_database(tmp_path / 'sales.sqlite')
    with sqlite3.connect(path) as connection:
        connection.execute('DELETE FROM sales_orders')
        # Keep actual seeded table constraints/columns, replacing only rows.
        columns = [row[1] for row in connection.execute('PRAGMA table_info(sales_orders)')]
        # Clone a known schema-valid seed row from a separate read source.
    source = initialize_database(tmp_path / 'template.sqlite')
    with sqlite3.connect(source) as connection:
        row = list(connection.execute('SELECT * FROM sales_orders LIMIT 1').fetchone())
    with sqlite3.connect(path) as connection:
        for index in range(ties + 1):
            values = list(row)
            for key, value in {'order_id': index + 1, 'region': ['华东', '华南', '华北', '西南'][index],
                               'order_date': '2025-02-01', 'sales_amount': 100 if index < ties else 10}.items():
                values[columns.index(key)] = value
            connection.execute('INSERT INTO sales_orders VALUES(' + ','.join('?' for _ in values) + ')', values)
    proposed = payload or model_payload()
    return Nl2SqlEngine(path, max_rows=max_rows, model_plan_provider=lambda question, tables: proposed,
                       model_fallback=False)


@pytest.mark.parametrize('ties', [1, 2, 3])
def test_model_limit_one_is_audited_and_all_tied_champions_survive(tmp_path, ties):
    engine = make_engine(tmp_path, ties=ties)
    result = engine.answer(QUESTION, required_intent=engine.extract_required_intent(QUESTION))
    assert result.status == 'ok', result
    assert result.plan['planner_source'] == 'model_validated'
    assert result.plan['top_n'] == 1 and result.plan['limit'] == 100
    assert len(result.rows) == ties
    assert {row['排名'] for row in result.rows} == {1}
    assert result.provenance['result_completeness'] == 'within_return_limit'
    assert result.provenance['rank_return_cap_normalization']['from'] == 1
    assert result.provenance['rank_return_cap_normalization']['to'] == 100


def test_single_source_uses_same_independent_scope_check(tmp_path):
    result = make_engine(tmp_path, ties=2).answer(QUESTION)
    assert result.status == 'ok'
    assert len(result.rows) == 2
    assert result.plan['planner_source'] == 'model_validated'


def test_wrong_model_year_never_receives_verified_cap_normalization(tmp_path):
    engine = make_engine(tmp_path, payload=model_payload(year=2024))
    with pytest.raises(ModelPlanError):
        engine.answer(QUESTION, required_intent=engine.extract_required_intent(QUESTION))


def test_inconsistent_original_source_rejects_before_cap_repair(tmp_path):
    engine = make_engine(tmp_path)
    result = engine.answer(QUESTION, required_intent=engine.extract_required_intent(QUESTION.replace('2025', '2024')))
    assert result.status != 'ok'
    assert 'rank_return_cap_normalization' not in result.provenance


@pytest.mark.parametrize('cap', [1, 2])
def test_safety_boundary_never_claims_complete_ties(tmp_path, cap):
    result = make_engine(tmp_path, ties=3, max_rows=cap).answer(QUESTION)
    assert result.status == 'ok'
    assert len(result.rows) == cap
    assert result.plan['limit'] == cap
    assert result.provenance['result_completeness'] == 'limit_reached_total_unknown'


@pytest.mark.parametrize('qualifier', ['只返回1行', '最多1条', '仅1行', '只要一条',
    '返回最多1行', 'return 1 row', 'show only 1 row', 'return at most 1 records',
    'SHOW ONLY AT MOST 1 ROWS', 'limit 1'])
def test_explicit_user_row_limit_is_not_enlarged(tmp_path, qualifier):
    try:
        result = make_engine(tmp_path, ties=2).answer(QUESTION + '，' + qualifier)
    except ModelPlanError:
        # Independently detected unsupported/ambiguous qualifiers reject
        # before execution, which also cannot enlarge the requested cap.
        return
    assert 'rank_return_cap_normalization' not in result.provenance
    if result.status == 'ok':
        assert result.plan['limit'] == 1


@pytest.mark.parametrize('qualifier', ['只取尽可能少的记录', '按照未说明的限制返回',
                                     '最多大约几条', 'at most a few records',
                                     'return a few rows', 'show some records', 'no more than a few rows'])
def test_unknown_row_restrictions_never_authorize_implicit_expansion(tmp_path, qualifier):
    result = make_engine(tmp_path, ties=2).answer(QUESTION + '，' + qualifier)
    assert 'rank_return_cap_normalization' not in result.provenance


def test_distinct_model_return_cap_not_assumed_to_be_top_n_error(tmp_path):
    result = make_engine(tmp_path, ties=3, payload=model_payload(limit=2)).answer(QUESTION)
    assert 'rank_return_cap_normalization' not in result.provenance
    assert result.plan['limit'] == 2
    assert result.provenance['result_completeness'] == 'limit_reached_total_unknown'


def test_per_request_row_boundary_is_preserved(tmp_path):
    result = make_engine(tmp_path, ties=3).answer(QUESTION, max_rows=2)
    assert result.plan['limit'] == 2
    assert len(result.rows) == 2
    assert result.provenance['result_completeness'] == 'limit_reached_total_unknown'
