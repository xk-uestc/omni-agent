"""Actual SQLite cardinality and engine caps, including false proof traps."""
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.result_scope import scalar_aggregate_cardinality
from backend.nl2sql.seed import initialize_database


@pytest.mark.parametrize('sql', [
    'SELECT SUM(value) AS total FROM facts LIMIT 1',
    'SELECT COUNT(*), MIN(value), MAX(value), AVG(value) FROM facts',
    'WITH source AS (SELECT * FROM facts) SELECT SUM(value) FROM source',
    'SELECT COALESCE(SUM(value), 0) FROM facts WHERE value > 999',
    'SELECT SUM(value) FROM facts HAVING COUNT(*) > 999',
    'SELECT SUM(value) FROM facts LIMIT 1 OFFSET 1',
    'SELECT SUM(value) + (SELECT MAX(value) FROM facts) FROM facts',
    'WITH source AS (SELECT SUM(value) AS total FROM facts), '
    'combined AS (SELECT total FROM source) SELECT total FROM combined LIMIT 1',
    'SELECT total FROM (SELECT SUM(value) AS total FROM facts) LIMIT 1',
    'WITH a AS (SELECT SUM(value) AS total FROM facts), '
    'b AS (SELECT COUNT(*) AS n FROM facts) SELECT total, n FROM a CROSS JOIN b',
])
def test_proof_matches_real_sqlite_for_empty_and_populated_sources(sql):
    proof = scalar_aggregate_cardinality(sql)
    assert proof and proof['maximum_output_rows'] == 1
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE facts(value REAL)')
        for values in ([], [(None,), (None,)], [(2,), (3,), (None,) ]):
            connection.execute('DELETE FROM facts')
            connection.executemany('INSERT INTO facts VALUES (?)', values)
            assert len(connection.execute(sql).fetchall()) <= 1


@pytest.mark.parametrize('sql', [
    'SELECT value FROM facts LIMIT 1',
    'SELECT SUM(value) OVER () FROM facts',
    'SELECT MAX(value) OVER (PARTITION BY value) FROM facts',
    'SELECT (SELECT SUM(value) FROM facts) FROM facts',
    'SELECT value FROM facts WHERE value > (SELECT AVG(value) FROM facts)',
    'WITH source AS (SELECT SUM(value) AS total FROM facts) '
    'SELECT total FROM source JOIN facts ON facts.value = total',
    'WITH source AS (SELECT value FROM facts LIMIT 1) SELECT value FROM source',
    'WITH source AS (SELECT SUM(value) AS total FROM facts), '
    'other AS (SELECT MAX(value) AS maximum FROM facts) '
    'SELECT total, maximum FROM source FULL JOIN other ON total = maximum',
    'SELECT SUM(value) FROM facts GROUP BY value',
    'SELECT SUM(value) FROM facts UNION ALL SELECT SUM(value) FROM facts',
    'SELECT SUM(value) FROM facts; SELECT value FROM facts',
    'not valid SQL (',
])
def test_no_proof_for_windows_nested_aggregates_groups_or_sets(sql):
    assert scalar_aggregate_cardinality(sql) is None


def test_one_row_preview_keeps_actual_scalar_value_and_no_false_truncation(tmp_path):
    path = initialize_database(tmp_path / 'sales.sqlite')
    engine = Nl2SqlEngine(path)
    for question in ('销售额是多少', '2019年销售额'):
        result = engine.answer(question, max_rows=1)
        assert result.status == 'ok'
        assert result.provenance['result_completeness'] == 'within_return_limit'
        assert result.provenance['output_cardinality']['maximum_output_rows'] == 1
        assert not any('达到返回上限' in notice for notice in result.notices)
        with sqlite3.connect(path) as connection:
            replay = connection.execute(result.sql, result.parameters).fetchall()
        assert [tuple(row[column] for column in result.columns) for row in result.rows] == replay
    assert result.result_state == 'empty'
    assert result.rows[0]['销售额'] is None


def test_grouped_one_row_sql_limit_retains_unknown_completeness(tmp_path, monkeypatch):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    original = engine.planner.plan
    def limited(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.limit = 1
        return plan
    monkeypatch.setattr(engine.planner, 'plan', limited)
    result = engine.answer('按渠道统计订单数')
    assert result.status == 'ok' and len(result.rows) == 1
    assert result.provenance['result_completeness'] == 'limit_reached_total_unknown'
    assert 'output_cardinality' not in result.provenance
    assert any('达到返回上限' in notice for notice in result.notices)


def test_v2_actual_aggregate_cte_compiler_has_singleton_proof(tmp_path, monkeypatch):
    from backend.nl2sql.models import MetricSpec
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    original = engine.planner.plan
    def multi_metric(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.metrics = [MetricSpec(id='sales', table='sales_orders', column='sales_amount',
            function='SUM', label='销售额')]
        plan.order_metric = 'sales'
        return plan
    monkeypatch.setattr(engine.planner, 'plan', multi_metric)
    result = engine.answer('销售额是多少', max_rows=1)
    assert result.status == 'ok' and result.sql.startswith('WITH ')
    assert result.provenance['result_completeness'] == 'within_return_limit'
    assert result.provenance['output_cardinality']['verified_singleton_scopes'] >= 3
    with sqlite3.connect(engine.database_path) as connection:
        expected = connection.execute('SELECT SUM(sales_amount) FROM sales_orders').fetchone()[0]
    assert result.rows == ({'销售额': expected},)
