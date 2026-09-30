from __future__ import annotations

import json
import sqlite3

import pytest

from backend.nl2sql.annotation import validate_schema_annotations
from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker
from backend.nl2sql.seed import initialize_database


def _tables(tmp_path):
    path = initialize_database(tmp_path / "annotation.sqlite", force=True)
    with sqlite3.connect(path) as connection:
        return SchemaIntrospector().introspect(connection)


def test_annotation_report_detects_uncovered_columns(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps([
        {"table": "sales_orders", "column": "sales_amount", "role": "metric", "aliases": ["销售额"]},
    ], ensure_ascii=False), encoding="utf-8")
    report = validate_schema_annotations(_tables(tmp_path), aliases)
    assert report["status"] == "valid_with_warnings"
    assert report["rule_count"] == 1
    assert any(item["column"] == "region" for item in report["warnings"])


def test_annotation_report_rejects_unknown_columns_and_conflicts(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps([
        {"table": "sales_orders", "column": "missing", "role": "metric", "aliases": ["销售额"]},
        {"table": "sales_orders", "column": "region", "role": "dimension", "aliases": ["区域"]},
        {"table": "sales_orders", "column": "channel", "role": "dimension", "aliases": ["区域"]},
    ], ensure_ascii=False), encoding="utf-8")
    report = validate_schema_annotations(_tables(tmp_path), aliases)
    assert report["status"] == "invalid"
    assert {item["code"] for item in report["errors"]} >= {"unknown_column", "conflicting_alias"}


def test_annotation_report_supports_versioned_wrapper(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({"version": 1, "rules": [
        {"table": "sales_orders", "column": "region", "role": "dimension", "aliases": ["地区"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    report = validate_schema_annotations(_tables(tmp_path), aliases)
    assert report["version"] == 1
    assert report["status"] == "valid_with_warnings"


@pytest.mark.parametrize(
    "rule",
    [
        {"table": "sales_orders", "column": "region", "role": "unknown", "aliases": ["区域"]},
        {"table": "sales_orders", "column": "region", "role": "dimension", "aliases": "区域"},
        {"table": "sales_orders", "column": "region", "role": "dimension", "aliases": []},
    ],
)
def test_schema_linker_rejects_malformed_runtime_config(tmp_path, rule):
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps([rule], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        SchemaLinker.from_json(path)
