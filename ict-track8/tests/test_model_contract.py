from __future__ import annotations

import json

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.model_contract import HttpModelPlanProvider, ModelPlanError, ModelPlanValidator, parse_model_json
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.seed import initialize_database


def _tables(tmp_path):
    path = initialize_database(tmp_path / "model.sqlite")
    import sqlite3

    with sqlite3.connect(path) as connection:
        return path, SchemaIntrospector().introspect(connection)


def _valid_payload():
    return {
        "version": 1,
        "table": "sales_orders",
        "metric": {"table": "sales_orders", "column": "sales_amount", "function": "SUM", "label": "销售额"},
        "dimensions": [{"table": "sales_orders", "column": "region", "label": "地区"}],
        "filters": [{"table": "sales_orders", "column": "channel", "operator": "=", "value": "线上"}],
        "analysis_mode": "aggregate",
        "limit": 20,
        "confidence": 0.91,
        "rewritten_question": "按地区统计线上销售额",
    }


def test_model_plan_is_converted_and_executed_through_existing_sql_builder(tmp_path):
    path, tables = _tables(tmp_path)
    payload = _valid_payload()
    plan = ModelPlanValidator().validate(payload, tables, question="按地区统计线上销售额")
    assert plan.planner_source == "model_validated"
    engine = Nl2SqlEngine(path, model_plan_provider=lambda _question, _tables: payload)
    result = engine.answer("按地区统计线上销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "model_validated"
    assert "SELECT" in result.sql
    assert "线上" in result.parameters


def test_model_plan_cannot_inject_sql_or_arbitrary_join_condition(tmp_path):
    _, tables = _tables(tmp_path)
    payload = _valid_payload()
    payload["metric"]["column"] = "sales_amount\"; DROP TABLE sales_orders;--"
    with pytest.raises(ModelPlanError, match="非法字符|不存在"):
        ModelPlanValidator().validate(payload, tables, question="恶意")


def test_model_plan_rejects_unverified_join_path(tmp_path):
    _, tables = _tables(tmp_path)
    payload = _valid_payload()
    payload["dimensions"] = [{"table": "customers", "column": "customer_level"}]
    payload["join_path"] = ["sales_orders", "customers", "evil_table"]
    with pytest.raises(ModelPlanError):
        ModelPlanValidator().validate(payload, tables, question="按客户等级统计")


def test_model_json_parser_requires_plain_object():
    with pytest.raises(ModelPlanError):
        parse_model_json("```json\n{}\n```")
    assert parse_model_json(json.dumps({"version": 1})) == {"version": 1}


def test_model_comparison_window_is_schema_checked(tmp_path):
    path, tables = _tables(tmp_path)
    payload = _valid_payload()
    payload["dimensions"] = [{"table": "sales_orders", "column": "region"}]
    payload["comparison"] = {
        "mode": "同比",
        "date_table": "sales_orders",
        "date_column": "order_date",
        "current_start": "2025-01-01",
        "current_end": "2026-01-01",
        "previous_start": "2024-01-01",
        "previous_end": "2025-01-01",
    }
    engine = Nl2SqlEngine(path, model_plan_provider=lambda _question, _tables: payload)
    result = engine.answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert result.plan["comparison_mode"] == "同比"
    assert "同比(%)" in result.sql

    payload["comparison"]["date_column"] = "sales_amount"
    with pytest.raises(ModelPlanError, match="日期列"):
        ModelPlanValidator().validate(payload, tables, question="恶意")


def test_http_model_plan_provider_accepts_wrapped_json_without_exposing_token():
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"plan": {"version": 1, "table": "sales_orders"}}

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    provider = HttpModelPlanProvider("https://planner.example/plan", "secret-token", session=session)
    payload = provider("销售额", ())
    assert payload["version"] == 1
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer secret-token"


def test_engine_falls_back_to_rules_when_model_plan_is_invalid(tmp_path):
    path, _tables_snapshot = _tables(tmp_path)
    engine = Nl2SqlEngine(path, model_plan_provider=lambda _question, _tables: {"version": 1})
    result = engine.answer("2025年华东地区的销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert any("回退规则规划" in item for item in result.plan["assumptions"])
