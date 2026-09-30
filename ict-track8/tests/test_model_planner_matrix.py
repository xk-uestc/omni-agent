from __future__ import annotations

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def _payload(**overrides):
    value = {
        "version": 1,
        "table": "sales_orders",
        "metric": {"table": "sales_orders", "column": "sales_amount", "function": "SUM", "label": "销售额"},
        "dimensions": [],
        "filters": [],
        "analysis_mode": "aggregate",
        "limit": 20,
        "confidence": 0.95,
        "rewritten_question": "销售额",
    }
    value.update(overrides)
    return value


def _engine(tmp_path, payload):
    path = initialize_database(tmp_path / "model-matrix.sqlite")
    return Nl2SqlEngine(path, model_plan_provider=lambda _question, _tables: payload)


def test_valid_model_plan_is_used_when_all_slots_are_present(tmp_path):
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "region", "label": "地区"}],
        filters=[{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}],
    )
    result = _engine(tmp_path, payload).answer("各地区华东销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "model_validated"


def test_model_adding_unasked_group_falls_back(tmp_path):
    payload = _payload(dimensions=[{"table": "sales_orders", "column": "region", "label": "地区"}])
    result = _engine(tmp_path, payload).answer("销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["dimensions"] == []


def test_model_omitting_explicit_time_falls_back(tmp_path):
    payload = _payload(filters=[{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}])
    result = _engine(tmp_path, payload).answer("2025年华东销售额")
    assert result.plan["planner_source"] == "rules_fallback"
    assert any(item["operator"] == "RANGE" for item in result.plan["filters"])


def test_model_omitting_explicit_group_falls_back(tmp_path):
    result = _engine(tmp_path, _payload()).answer("2025年各地区销售额")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["dimensions"] == ["region"]


def test_model_invented_filter_falls_back(tmp_path):
    payload = _payload(filters=[{"table": "sales_orders", "column": "region", "operator": "=", "value": "华中"}])
    result = _engine(tmp_path, payload).answer("华东销售额")
    assert result.plan["planner_source"] == "rules_fallback"


def test_low_confidence_model_falls_back(tmp_path):
    result = _engine(tmp_path, _payload(confidence=0.1)).answer("销售额")
    assert result.plan["planner_source"] == "rules_fallback"


def test_invalid_model_payload_falls_back_without_sql_error(tmp_path):
    result = _engine(tmp_path, {"version": 99}).answer("销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"


def test_model_comparison_with_wrong_period_falls_back(tmp_path):
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "region", "label": "地区"}],
        comparison={
            "mode": "同比", "date_table": "sales_orders", "date_column": "order_date",
            "current_start": "2024-01-01", "current_end": "2025-01-01",
            "previous_start": "2023-01-01", "previous_end": "2024-01-01",
        },
    )
    result = _engine(tmp_path, payload).answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["comparison_period"]["current_start_text"] == "2025-01-01"


def test_model_comparison_with_wrong_mode_falls_back(tmp_path):
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "region", "label": "地区"}],
        comparison={
            "mode": "环比", "date_table": "sales_orders", "date_column": "order_date",
            "current_start": "2025-01-01", "current_end": "2025-02-01",
            "previous_start": "2024-12-01", "previous_end": "2025-01-01",
        },
    )
    result = _engine(tmp_path, payload).answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["comparison_mode"] == "同比"


def test_model_comparison_does_not_drop_explicit_dimension_filter(tmp_path):
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "region", "label": "地区"}],
        comparison={
            "mode": "同比", "date_table": "sales_orders", "date_column": "order_date",
            "current_start": "2025-01-01", "current_end": "2026-01-01",
            "previous_start": "2024-01-01", "previous_end": "2025-01-01",
        },
    )
    result = _engine(tmp_path, payload).answer("2025年华东地区销售额同比")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert any(item["column"] == "region" and item["value"] == "华东" for item in result.plan["filters"])
