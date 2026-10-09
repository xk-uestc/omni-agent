import sqlite3
from pathlib import Path

import pytest

from backend.nl2sql.date_profile import storage_profiles
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.seed import initialize_rich_demo_data


class ForbiddenModel:
    supports_complex_queries = True
    supports_verified_rule_fast_path = True

    def __init__(self):
        self.calls = 0

    def propose(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("verified catalog queries should not need model planning")


def _engine(tmp_path):
    database = initialize_rich_demo_data(tmp_path / "rich-demo.sqlite")
    model = ForbiddenModel()
    catalog = Path(__file__).resolve().parents[1] / "data" / "demo_metric_catalog.json"
    return Nl2SqlEngine(database, metric_catalog_path=catalog, model_plan_provider=model), model


def _scalar(database, sql, parameters):
    with sqlite3.connect(database) as connection:
        return connection.execute(sql, parameters).fetchone()[0]


def test_catalog_metrics_bind_their_physical_source_and_time_column(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    database = engine.database_path
    cases = [
        ("2025年客户数", "customers", "customer_id", "created_date", "客户数",
         "SELECT COUNT(*) FROM customers WHERE created_date >= ? AND created_date < ?",
         ("2025-01-01", "2026-01-01")),
        ("2025年营销投入", "marketing_campaigns", "budget", "start_date", "营销预算",
         "SELECT SUM(budget) FROM marketing_campaigns WHERE start_date >= ? AND start_date < ?",
         ("2025-01-01", "2026-01-01")),
        ("2025年库存量", "inventory_snapshots", "on_hand_quantity", "snapshot_month", "库存量",
         "SELECT SUM(on_hand_quantity) FROM inventory_snapshots WHERE snapshot_month >= ? AND snapshot_month < ?",
         ("2025-01", "2026-01")),
        ("2025年销售目标", "sales_targets", "target_sales", "target_month", "销售目标",
         "SELECT SUM(target_sales) FROM sales_targets WHERE target_month >= ? AND target_month < ?",
         ("2025-01", "2026-01")),
    ]

    for question, table, field, date_field, label, sql, parameters in cases:
        result = engine.answer(question)
        assert result.status == "ok"
        assert result.plan["metric_table"] == table
        assert result.plan["metric_column"] == field
        assert f'"{table}"."{date_field}"' in result.sql
        assert result.parameters[:2] == parameters
        assert result.rows[0][label] == _scalar(database, sql, parameters)

    assert model.calls == 0


def test_simple_catalog_grouping_uses_one_resolved_dimension_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer("2025年按产品类别分别统计毛利")

    assert result.status == "ok"
    assert result.plan["dimensions"] == ["product_category"]
    assert result.plan["dimension_tables"] == {"product_category": "sales_orders"}
    assert "LEFT JOIN \"products\"" not in result.sql
    assert model.calls == 0


@pytest.mark.parametrize("question,table,dimension,metric", [
    ("2025年按营销类型分别统计营销预算", "marketing_campaigns", "campaign_type", "budget"),
    ("2025年按渠道分别统计营销预算", "marketing_campaigns", "channel", "budget"),
    ("2025年按费用类别分别统计费用金额", "operating_expenses", "expense_category", "expense_amount"),
    ("2025年按地区分别统计费用金额", "operating_expenses", "region", "expense_amount"),
    ("2025年按地区分别统计采购金额", "purchase_orders", "region", "purchase_amount"),
    ("2025年按地区分别统计薪酬总额", "payroll_records", "region", "salary_amount"),
    ("2025年按地区分别统计运费", "shipment_records", "region", "shipping_cost"),
    ("2025年按地区分别统计网页浏览量", "web_traffic_daily", "region", "page_views"),
    ("2025年各产品类别毛利", "sales_orders", "product_category", "gross_profit"),
    ("2025年各设备类型网页浏览量", "web_traffic_daily", "device_type", "page_views"),
    ("2025年按问题类型分别统计工单数", "support_tickets", "issue_type", "ticket_id"),
])
def test_catalog_grouping_uses_only_the_requested_dimension_without_model(
        tmp_path, monkeypatch, question, table, dimension, metric):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer(question)

    assert result.status == "ok"
    assert result.plan["metric_table"] == table
    assert result.plan["metric_column"] == metric
    assert result.plan["dimensions"] == [dimension]
    assert result.plan["dimension_tables"] == {dimension: table}
    assert result.parameters[:2] == ("2025-01-01", "2026-01-01")
    assert model.calls == 0


@pytest.mark.parametrize("question,table,field,date_field,label,sql,parameters", [
    ("2025年华东进货金额", "purchase_orders", "purchase_amount", "purchase_date", "采购金额",
     "SELECT SUM(purchase_amount) FROM purchase_orders WHERE purchase_date >= ? AND purchase_date < ? AND region = ?",
     ("2025-01-01", "2026-01-01", "华东")),
    ("2025年华东进货数量", "purchase_orders", "purchase_quantity", "purchase_date", "采购数量",
     "SELECT SUM(purchase_quantity) FROM purchase_orders WHERE purchase_date >= ? AND purchase_date < ? AND region = ?",
     ("2025-01-01", "2026-01-01", "华东")),
    ("2025年华东销售数量", "sales_orders", "quantity", "order_date", "销量",
     "SELECT SUM(quantity) FROM sales_orders WHERE order_date >= ? AND order_date < ? AND region = ?",
     ("2025-01-01", "2026-01-01", "华东")),
    ("2025年华东订单笔数", "sales_orders", "order_id", "order_date", "订单数",
     "SELECT COUNT(*) FROM sales_orders WHERE order_date >= ? AND order_date < ? AND region = ?",
     ("2025-01-01", "2026-01-01", "华东")),
    ("麻烦查下2025年华东经营费用，给个总数", "operating_expenses", "expense_amount", "expense_date", "费用金额",
     "SELECT SUM(expense_amount) FROM operating_expenses WHERE expense_date >= ? AND expense_date < ? AND region = ?",
     ("2025-01-01", "2026-01-01", "华东")),
    ("麻烦查下2025年库存价值，给个总数", "inventory_snapshots", "inventory_value", "snapshot_month", "库存金额",
     "SELECT SUM(inventory_value) FROM inventory_snapshots WHERE snapshot_month >= ? AND snapshot_month < ?",
     ("2025-01", "2026-01")),
])
def test_table_aware_colloquial_aliases_and_result_wording(
        tmp_path, monkeypatch, question, table, field, date_field, label, sql, parameters):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer(question)

    assert result.status == "ok", result.to_dict()
    assert result.plan["metric_table"] == table
    assert result.plan["metric_column"] == field
    assert result.rows[0][label] == _scalar(engine.database_path, sql, parameters)
    assert f'"{table}"."{date_field}"' in result.sql
    assert model.calls == 0


@pytest.mark.parametrize("question", [
    "2025年华东营业额和销量",
    "2025年华东销售额和销售数量",
])
def test_catalog_alias_hit_does_not_drop_another_requested_metric(tmp_path, monkeypatch, question):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer(question)

    assert result.status == "ok", result.to_dict()
    assert {metric["column"] for metric in result.plan["metrics"]} == {"sales_amount", "quantity"}
    assert set(result.rows[0]) == {"销售额", "销量"}
    assert model.calls == 0


def test_result_wording_does_not_override_average_metric_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer("麻烦查下2025年客户满意度，给个总数")

    assert result.status == "clarification"
    assert result.sql is None
    assert result.clarification_code == "ambiguous_aggregation"
    assert model.calls == 0


@pytest.mark.parametrize("question", [
    "麻烦查下2025年满意度平均分，给个总数",
    "麻烦查下2025年平均满意度，给个总量",
])
def test_explicit_average_wording_resolves_overall_result_request(tmp_path, monkeypatch, question):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer(question)

    assert result.status == "ok", result.to_dict()
    assert result.plan["metric_table"] == "support_tickets"
    assert result.plan["metric_column"] == "satisfaction_score"
    assert result.plan["metric_function"] == "AVG"
    assert 'AVG("support_tickets"."satisfaction_score")' in result.sql
    assert result.parameters[:2] == ("2025-01-01", "2026-01-01")
    expected = _scalar(engine.database_path,
        "SELECT AVG(satisfaction_score) FROM support_tickets WHERE created_date >= ? AND created_date < ?",
        result.parameters[:2])
    assert next(iter(result.rows[0].values())) == pytest.approx(expected)
    assert model.calls == 0


@pytest.mark.parametrize("question", [
    "2025年华东产品名称为本钱包的销售额",
    "2025年华东产品名称等于'本钱包'的销售额",
])
def test_explicit_unknown_text_value_uses_verified_local_filter(tmp_path, monkeypatch, question):
    monkeypatch.setenv("ICT8_FAST_SQL", "1")
    engine, model = _engine(tmp_path)
    result = engine.answer(question)

    assert result.status == "ok", result.to_dict()
    assert result.plan["planner_source"] == "server_verified_fast_rules"
    product_filter = next(item for item in result.plan["filters"] if item["column"] == "product_name")
    assert product_filter["table"] == "sales_orders"
    assert product_filter["operator"] == "="
    assert product_filter["value"] == "本钱包"
    assert '"sales_orders"."product_name" = ?' in result.sql
    assert "本钱包" not in result.sql
    assert "本钱包" in result.parameters
    expected = _scalar(engine.database_path,
        "SELECT SUM(sales_amount) FROM sales_orders WHERE order_date >= ? AND order_date < ? "
        "AND region = ? AND product_name = ?",
        (*result.parameters[:2], "华东", "本钱包"))
    assert next(iter(result.rows[0].values())) == expected
    assert model.calls == 0


def test_month_precision_rejects_day_level_filtering(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, model = _engine(tmp_path)
    result = engine.answer("2025年1月15日库存量")

    assert result.status == "clarification"
    assert result.sql is None
    assert model.calls == 0


def test_month_profile_is_verified_for_the_whole_column():
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(
            "CREATE TABLE snapshots(snapshot_month TEXT);"
            "INSERT INTO snapshots VALUES ('2025-01'), ('2025-02'), ('2025-11');"
        )
        tables = SchemaIntrospector().introspect(connection, include_row_count=False)
        profile = storage_profiles(connection, tables, "2025年snapshot_month")[0]

    assert profile["field"] == "snapshots.snapshot_month"
    assert profile["format"] == "iso_month_text"
    assert profile["time_comparison"]["precision_contract"] == "calendar_month"

