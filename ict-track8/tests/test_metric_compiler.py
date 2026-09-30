"""指标粒度回归：金标直接从原事实表独立查询，不复用被测编译器。"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.metric_compiler import MetricCompiler, MetricPlanError
from backend.nl2sql.model_contract import ModelPlanError, ModelPlanValidator
from backend.nl2sql.models import DerivedMetricSpec, FilterSpec, MetricSpec, QueryPlan
from backend.nl2sql.planner import SingleTablePlanner
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import execute_read_only
from backend.nl2sql.seed import initialize_database


@pytest.fixture
def business(tmp_path):
    path = tmp_path / "facts.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE orders(order_id INTEGER PRIMARY KEY, region TEXT, amount REAL, status TEXT);
            CREATE TABLE items(item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id), kind TEXT, quantity INTEGER);
            CREATE TABLE refunds(refund_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id), amount REAL);
            INSERT INTO orders VALUES (1,'华东',100,'完成'),(2,'华东',100,'完成'),(3,'华南',200,'完成'),(4,NULL,50,'完成'),(5,'无退款',80,'取消');
            INSERT INTO items VALUES (1,1,'手机',1),(2,1,'手机',2),(3,2,'手机',3),(4,3,'平板',1),(5,4,'手机',1);
            INSERT INTO refunds VALUES (1,1,10),(2,1,20),(3,2,15),(4,4,5);
        """)
    connection = sqlite3.connect(path)
    yield path, connection, SchemaIntrospector().introspect(connection)
    connection.close()


def plan():
    return QueryPlan(table="orders", metric_table="orders", metric_column="amount", metric_function="SUM",
                     metric_label="销售额", dimensions=["region"], dimension_tables={"region": "orders"},
                     metrics=[MetricSpec("gross", "orders", "amount", "SUM", "销售额", "currency", "CNY"),
                              MetricSpec("refund", "refunds", "amount", "SUM", "退款", "currency", "CNY", "zero")])


def execute(connection, tables, query_plan):
    sql, params = MetricCompiler(SingleTablePlanner()).compile(query_plan, tables)
    return sql, execute_read_only(connection, sql, params, max_steps=1_000_000)[1]


def test_independent_facts_do_not_multiply_and_equal_orders_are_not_deduplicated(business):
    path, db, tables = business
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    p = plan()
    p.derived_metrics = [DerivedMetricSpec("net", "净收入", {"op": "subtract", "left": {"ref": "gross"}, "right": {"ref": "refund"}})]
    sql, rows = execute(db, tables, p)
    actual = {r["region"]: (r["销售额"], r["退款"], r["净收入"]) for r in rows}
    gold = {}
    for region, total in db.execute("SELECT region,SUM(amount) FROM orders GROUP BY region"):
        refund = db.execute("SELECT SUM(r.amount) FROM refunds r JOIN orders o ON o.order_id=r.order_id WHERE o.region IS ?", (region,)).fetchone()[0] or 0
        gold[region] = total, refund, total - refund
    assert actual == gold
    assert "SUM(DISTINCT" not in sql
    assert actual["华东"] == (200, 45, 155)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("function", ["SUM", "AVG"])
def test_child_filter_is_semijoin_and_keeps_equal_value_facts(business, function):
    _, db, tables = business
    p = plan()
    p.metrics = [MetricSpec("gross", "orders", "amount", function, "金额")]
    p.filters = [FilterSpec("kind", "=", "手机", "手机", "筛选手机订单", "items")]
    sql, rows = execute(db, tables, p)
    gold = dict(db.execute(f"SELECT region,{function}(amount) FROM orders o WHERE EXISTS(SELECT 1 FROM items i WHERE i.order_id=o.order_id AND i.kind='手机') GROUP BY region"))
    assert {r["region"]: r["金额"] for r in rows} == gold
    assert p.grain_audit["metrics"][0]["filter_strategy"] == "primary_key_semijoin"
    assert "SELECT DISTINCT" in sql


def test_filters_on_two_child_facts_do_not_multiply(business):
    _, db, tables = business
    p = plan()
    p.filters = [FilterSpec("kind", "=", "手机", "手机", "", "items"),
                 FilterSpec("amount", ">", 10, "退款>10", "", "refunds")]
    _, rows = execute(db, tables, p)
    assert rows == ({"region": "华东", "销售额": 200.0, "退款": 35.0},)


def test_sum_by_child_dimension_needs_allocation_but_count_distinct_is_valid(business):
    _, db, tables = business
    p = plan()
    p.dimensions, p.dimension_tables = ["kind"], {"kind": "items"}
    p.metrics = p.metrics[:1]
    with pytest.raises(MetricPlanError, match="分摊"):
        execute(db, tables, p)
    p.metrics = [MetricSpec("count", "orders", "order_id", "COUNT_DISTINCT", "订单数", "count")]
    _, rows = execute(db, tables, p)
    gold = dict(db.execute("SELECT i.kind,COUNT(DISTINCT o.order_id) FROM orders o LEFT JOIN items i ON i.order_id=o.order_id GROUP BY i.kind"))
    assert {r["kind"]: r["订单数"] for r in rows} == gold


def test_formula_chain_constants_are_bound_once_and_zero_division_is_null(business):
    _, db, tables = business
    p = plan()
    p.derived_metrics = [
        DerivedMetricSpec("ratio", "退款率", {"op": "divide", "left": {"ref": "refund"}, "right": {"ref": "gross"}}),
        DerivedMetricSpec("percent", "退款率百分比", {"op": "multiply", "left": {"ref": "ratio"}, "right": {"constant": 100}}),
        DerivedMetricSpec("double", "两倍退款率", {"op": "multiply", "left": {"ref": "percent"}, "right": {"constant": 2}}),
    ]
    _, rows = execute(db, tables, p)
    east = next(r for r in rows if r["region"] == "华东")
    assert east["退款率百分比"] == 22.5
    assert east["两倍退款率"] == 45
    db.execute("UPDATE orders SET amount=0 WHERE region='华东'")
    _, rows = execute(db, tables, p)
    assert next(r for r in rows if r["region"] == "华东")["退款率"] is None


def test_unit_currency_conflicts_are_rejected(business):
    _, db, tables = business
    p = plan()
    p.metrics[1].currency = "USD"
    p.derived_metrics = [DerivedMetricSpec("net", "净收入", {"op": "subtract", "left": {"ref": "gross"}, "right": {"ref": "refund"}})]
    with pytest.raises(MetricPlanError, match="单位和币种"):
        execute(db, tables, p)


def test_share_denominator_covers_all_groups_before_top_n(business):
    _, db, tables = business
    p = plan()
    p.metrics = p.metrics[:1]
    p.analysis_mode, p.top_n = "share", 1
    _, rows = execute(db, tables, p)
    assert len(rows) == 2  # 华东、华南并列第一
    assert all(r["占比(%)"] == pytest.approx(100 * 200 / 530) for r in rows)


def test_rules_multi_metric_runs_end_to_end(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    result = Nl2SqlEngine(path).answer("2025年按地区统计销售额和订单数")
    assert result.status == "ok", result.clarification
    assert len(result.plan["metrics"]) == 2
    with sqlite3.connect(path) as db:
        gold = db.execute("SELECT region,SUM(sales_amount),COUNT(*) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region ORDER BY 2 DESC,1").fetchall()
    assert [tuple(r.values()) for r in result.rows] == gold
    assert result.provenance["result_cells"]


@pytest.mark.parametrize("question", ["2025年各地区客单价", "2025年各地区平均每单金额"])
def test_business_formula_uses_declared_ratio_of_aggregates(tmp_path, question):
    path = initialize_database(tmp_path / "demo.sqlite")
    result = Nl2SqlEngine(path).answer(question)
    assert result.status == "ok", result.clarification
    assert result.columns == ("region", "客单价")
    with sqlite3.connect(path) as db:
        gold = db.execute("SELECT region,1.0*SUM(sales_amount)/COUNT(*) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region ORDER BY 2 DESC,1").fetchall()
    assert [tuple(r.values()) for r in result.rows] == gold
    assert result.plan["semantic_audit"]["catalog_version"]


def test_formula_and_requested_source_metrics_have_no_duplicate_columns(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    result = Nl2SqlEngine(path).answer("按地区统计销售额和客单价")
    assert result.status == "ok", result.clarification
    assert result.columns == ("region", "销售额", "客单价")


def test_v2_model_contract_and_explicit_top_n(business):
    _, db, tables = business
    payload = {"version": 2, "metrics": [
        {"id": "gross", "table": "orders", "column": "amount", "function": "SUM", "label": "销售额", "unit": "currency", "currency": "CNY"},
        {"id": "refund", "table": "refunds", "column": "amount", "function": "SUM", "label": "退款", "unit": "currency", "currency": "CNY", "missing": "zero"}],
        "dimensions": [{"table": "orders", "column": "region"}], "top_n": 1, "order_metric": "gross"}
    p = ModelPlanValidator().validate(payload, tables, question="按地区销售额前1和退款")
    assert p.top_n == 1
    _, rows = execute(db, tables, p)
    assert len(rows) == 2


def test_model_cannot_hide_fanout_with_false_flag(business):
    _, _, tables = business
    payload = {"version": 1, "table": "orders", "metric": {"column": "amount", "function": "SUM"},
               "dimensions": [{"table": "items", "column": "kind"}], "fan_out": False}
    with pytest.raises(ModelPlanError, match="分摊"):
        ModelPlanValidator().validate(payload, tables, question="按种类统计金额")


def test_composite_foreign_key_uses_all_columns(tmp_path):
    path = tmp_path / "composite.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE accounts(tenant TEXT NOT NULL, code TEXT NOT NULL, region TEXT, PRIMARY KEY(tenant,code));
            CREATE TABLE entries(id INTEGER PRIMARY KEY,tenant TEXT,code TEXT,amount REAL, FOREIGN KEY(tenant,code) REFERENCES accounts(tenant,code));
            INSERT INTO accounts VALUES ('A','1','华东'),('B','1','华南');
            INSERT INTO entries VALUES (1,'A','1',100),(2,'B','1',200);
        """)
        tables = SchemaIntrospector().introspect(db)
        p = QueryPlan(table="entries", dimensions=["region"], dimension_tables={"region": "accounts"},
                      metrics=[MetricSpec("amount", "entries", "amount", "SUM", "金额")])
        sql, rows = execute(db, tables, p)
        assert {r["region"]: r["金额"] for r in rows} == {"华东": 100, "华南": 200}
        assert '"entries"."tenant" = "accounts"."tenant" AND "entries"."code" = "accounts"."code"' in sql


def test_non_unique_foreign_target_is_not_used_as_many_to_one(tmp_path):
    path = tmp_path / "invalid.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE parent(code TEXT,region TEXT); CREATE TABLE child(id INTEGER PRIMARY KEY,code TEXT REFERENCES parent(code),amount REAL);")
        tables = SchemaIntrospector().introspect(db)
        p = QueryPlan(table="child", dimensions=["region"], dimension_tables={"region": "parent"},
                      metrics=[MetricSpec("amount", "child", "amount", "SUM", "金额")])
        with pytest.raises(MetricPlanError, match="关联路径"):
            execute(db, tables, p)
