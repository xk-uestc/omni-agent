from __future__ import annotations

from pathlib import Path as _Path
import sqlite3
from datetime import datetime, timezone

DATA_DIR = _Path(__file__).resolve().parents[1] / "data"

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.industry_seed import initialize_industry_database
from backend.nl2sql.seed import initialize_database


def test_year_over_year_window_is_explicit_and_parameterized(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / "sales.sqlite", force=True))
    result = engine.answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert result.plan["comparison_mode"] == "同比"
    assert result.columns == ("region", "本期销售额", "同期销售额", "同比(%)")
    assert result.parameters[:6] == (
        "2025-01-01",
        "2026-01-01",
        "2024-01-01",
        "2025-01-01",
        "2024-01-01",
        "2026-01-01",
    )
    assert "CASE WHEN" in result.sql
    assert "同比(%)" in result.sql
    assert result.plan["intent_audit"]["time_range"] == ["2025-01-01", "2026-01-01"]
    assert result.plan["intent_audit"]["comparison_window"] == {
        "date_table": "sales_orders",
        "date_column": "order_date",
        "current": ["2025-01-01", "2026-01-01"],
        "previous": ["2024-01-01", "2025-01-01"],
    }


def test_month_over_month_uses_previous_calendar_month(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / "sales.sqlite", force=True))
    result = engine.answer("2025年1月各地区销售额环比")
    assert result.status == "ok"
    assert result.plan["comparison_mode"] == "环比"
    assert result.parameters[2:6] == (
        "2024-12-01",
        "2025-01-01",
        "2024-12-01",
        "2025-02-01",
    )


def test_comparison_without_period_does_not_guess(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / "sales.sqlite", force=True))
    result = engine.answer("各地区销售额同比")
    assert result.status == "clarification"
    assert result.clarification_code == "missing_comparison_period"
    assert result.sql is None


def test_comparison_adds_date_table_to_industry_join_graph(tmp_path):
    database = initialize_industry_database(tmp_path / "industry.sqlite")
    engine = Nl2SqlEngine(database, aliases_path=DATA_DIR / "industry_aliases.json")
    result = engine.answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert 'JOIN "orders"' in result.sql
    assert 'JOIN "regions"' in result.sql
    assert result.plan["comparison_period"]["date_table"] == "orders"


def _comparison_epoch_db(path, *, milliseconds=False, mixed=False):
    scale = 1000 if milliseconds else 1
    epoch = lambda value: int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * scale)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE sales_orders (
                order_id TEXT PRIMARY KEY,
                order_date,
                sales_amount REAL NOT NULL,
                region TEXT NOT NULL
            );
        """)
        rows = [
            ("P", epoch("2025-01-01"), 10.0, "华东"),
            ("Q", epoch("2025-02-01"), 20.0, "华东"),
            ("Y", epoch("2024-01-01"), 5.0, "华东"),
            ("D", epoch("2024-12-01"), 5.0, "华东"),
        ]
        if mixed:
            rows[-1] = ("D", "2024-12-01", 5.0, "华东")
        connection.executemany("INSERT INTO sales_orders VALUES (?, ?, ?, ?)", rows)
    return path


def test_year_over_year_epoch_seconds_executes_both_windows(tmp_path):
    result = Nl2SqlEngine(_comparison_epoch_db(tmp_path / "yoy-s.sqlite")).answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert result.rows[0]["本期销售额"] == 30.0
    assert result.rows[0]["同期销售额"] == 10.0
    assert result.plan["comparison_period"]["current_start"] == 1735689600
    assert result.plan["comparison_period"]["previous_start"] == 1704067200


def test_month_over_month_epoch_milliseconds_executes_previous_window(tmp_path):
    result = Nl2SqlEngine(_comparison_epoch_db(tmp_path / "mom-ms.sqlite", milliseconds=True)).answer("2025年1月各地区销售额环比")
    assert result.status == "ok"
    assert result.rows[0]["当前销售额"] == 10.0
    assert result.rows[0]["上期销售额"] == 5.0
    assert result.plan["comparison_period"]["current_start"] == 1735689600000


def test_comparison_mixed_date_storage_clarifies(tmp_path):
    result = Nl2SqlEngine(_comparison_epoch_db(tmp_path / "mixed-yoy.sqlite", mixed=True)).answer("2025年各地区销售额同比")
    assert result.status == "clarification"
    assert result.clarification_code == "ambiguous_date_storage"


def test_model_comparison_uses_same_epoch_storage_gate(tmp_path):
    path = _comparison_epoch_db(tmp_path / "model-yoy.sqlite", milliseconds=True)
    payload = {
        "version": 1,
        "table": "sales_orders",
        "metric": {"table": "sales_orders", "column": "sales_amount", "function": "SUM", "label": "销售额"},
        "dimensions": [{"table": "sales_orders", "column": "region", "label": "地区"}],
        "filters": [],
        "analysis_mode": "aggregate",
        "limit": 20,
        "confidence": 0.95,
        "rewritten_question": "2025年各地区销售额同比",
        "comparison": {
            "mode": "同比", "date_table": "sales_orders", "date_column": "order_date",
            "current_start": "2025-01-01", "current_end": "2026-01-01",
            "previous_start": "2024-01-01", "previous_end": "2025-01-01",
        },
    }
    engine = Nl2SqlEngine(path, model_plan_provider=lambda _question, _tables: payload)
    result = engine.answer("2025年各地区销售额同比")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "model_validated"
    assert result.plan["comparison_period"]["current_start"] == 1735689600000
    assert result.rows[0]["本期销售额"] == 30.0
