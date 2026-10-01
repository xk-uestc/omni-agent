"""Exact ordinal selection must not silently become Top-N or erase scope."""

import sqlite3
from datetime import date

import pytest

from backend.nl2sql import lexicon
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


@pytest.mark.parametrize("phrase,descending", [
    ("排名第一", True), ("排名第1名", True), ("排名首位", True),
    ("位居首位", True), ("名列第一位", True), ("排名倒数第一", False),
])
def test_first_rank_lexical_binding(phrase, descending):
    parsed = lexicon.parse_top_n("2025年销售额" + phrase + "的地区")
    assert parsed.n == 1 and parsed.descending is descending
    assert parsed.span == phrase


@pytest.mark.parametrize("phrase", [
    "2025年第一季度销售额", "第一产品销售额", "产品第一名的销售额",
    "销量2025", "销售额超过第一季度预算", "排名第一季度的销售额",
])
def test_nonrank_ordinal_is_not_consumed(phrase):
    assert lexicon.parse_ordinal_ranks(phrase) == []
    assert lexicon.parse_top_n(phrase) is None


@pytest.mark.parametrize("phrase,n", [
    ("排名第三", 3), ("排名第十二名", 12), ("排名第25位", 25),
    ("排名倒数第二", 2), ("排名第零", 0),
])
def test_exact_later_rank_is_not_top_n(phrase, n):
    assert lexicon.parse_ordinal_ranks(phrase)[0].n == n
    assert lexicon.parse_top_n(phrase) is None


@pytest.fixture
def sales(tmp_path):
    path = initialize_database(tmp_path / "sales.sqlite")
    # Tie two complete groups; year 2024 has a larger distracting amount.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE sales_orders SET sales_amount=100 WHERE order_date LIKE '2025%'")
        connection.execute("UPDATE sales_orders SET sales_amount=100000 WHERE order_date LIKE '2024%'")
        connection.execute("UPDATE sales_orders SET sales_amount=150 WHERE order_date LIKE '2025%' AND region='华北'")
    return path, Nl2SqlEngine(path, reference_date=date(2026, 9, 22), model_fallback=False)


@pytest.mark.parametrize("phrase,direction", [
    ("排名第一", "DESC"), ("位居首位", "DESC"), ("排名倒数第一", "ASC"),
])
def test_first_rank_preserves_ties_metric_and_year(sales, phrase, direction):
    path, engine = sales
    result = engine.answer("查2025年销售额" + phrase + "的地区")
    assert result.status == "ok", result
    with sqlite3.connect(path) as connection:
        expected = connection.execute(
            f"WITH totals AS (SELECT region, SUM(sales_amount) AS amount FROM sales_orders "
            "WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region), "
            f"ranked AS (SELECT *, DENSE_RANK() OVER (ORDER BY amount {direction}) AS r FROM totals) "
            "SELECT region, amount FROM ranked WHERE r=1"
        ).fetchall()
    actual = {(row[result.columns[0]], row[result.columns[1]]) for row in result.rows}
    assert actual == set(expected)
    assert "DENSE_RANK" in result.sql.upper()


@pytest.mark.parametrize("phrase", ["排名第三", "排名第十二名", "排名第0位", "排名第一或排名第二"])
def test_unsupported_exact_rank_clarifies(sales, phrase):
    result = sales[1].answer("2025年销售额" + phrase + "的地区")
    assert result.status == "clarification"
    assert result.clarification_code == "unsupported_exact_rank"
    assert not result.rows


def test_first_quarter_keeps_time_scope(sales):
    result = sales[1].answer("2025年第一季度各地区销售额")
    assert result.status == "ok"
    assert result.plan['top_n'] is None
    assert "2025-04-01" in result.parameters


@pytest.mark.parametrize("phrase", ["排名第一且前3", "排名第一且倒数1", "排名倒数第一且前1"])
def test_conflicting_rank_selections_are_not_dropped(sales, phrase):
    result = sales[1].answer("2025年销售额" + phrase + "的地区")
    assert result.status == "clarification"
    assert result.clarification_code == "conflicting_rank_selection"
    assert not result.rows
