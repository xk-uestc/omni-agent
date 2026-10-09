from __future__ import annotations

from fastapi.testclient import TestClient

import backend.app as app_module
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def _model_payload(**overrides):
    payload = {
        "version": 1,
        "table": "sales_orders",
        "metric": {"table": "sales_orders", "column": "sales_amount", "function": "SUM", "label": "销售额"},
        "dimensions": [],
        "filters": [{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}],
        "analysis_mode": "aggregate",
        "limit": 20,
        "confidence": 0.95,
        "rewritten_question": "华东地区销售额",
    }
    payload.update(overrides)
    return payload


def test_query_api_exposes_planner_audit(monkeypatch, tmp_path):
    database = initialize_database(tmp_path / "api.sqlite")
    monkeypatch.setattr(
        app_module,
        "engine",
        Nl2SqlEngine(database, model_plan_provider=lambda _question, _tables: _model_payload()),
    )
    response = TestClient(app_module.app).post("/api/v1/nl2sql/query", json={"question": "华东的销售额"})
    assert response.status_code == 200
    payload = response.json()
    expected = {
        "candidate_source": "independent_rule_parser",
        "final_source": "server_verified_fast_rules",
        "fallback": False,
        "decision": "accepted",
        "model_called": False,
        "verification": "complete_coverage_single_table_current_snapshot",
    }
    assert payload["plan"]["planner_audit"] == expected
    assert payload["plan"]["intent_audit"]["planner_audit"] == expected
