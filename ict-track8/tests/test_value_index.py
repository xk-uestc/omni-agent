from __future__ import annotations

import sqlite3

import pytest

from backend.nl2sql.schema import SchemaIntrospector, normalize_text
from backend.nl2sql.seed import initialize_database
from backend.nl2sql.value_index import ValueIndex


@pytest.fixture()
def index(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    with sqlite3.connect(path) as connection:
        tables = SchemaIntrospector().introspect(connection, include_row_count=False)
        return ValueIndex.build(connection, tables)


def test_exact_value_match_without_column_alias(index):
    matches, ambiguities = index.match(normalize_text("2025年华东销售额"), set())
    assert not ambiguities
    assert any(m.column == "region" and m.value == "华东" for m in matches)


def test_longest_match_wins_and_is_non_overlapping(index):
    matches, _ = index.match(normalize_text("星河 Pro的销量"), set())
    values = {m.value for m in matches}
    assert "星河 Pro" in values
    assert "星河" not in values


def test_single_character_value_requires_linked_column(index):
    # 客户等级"高"是单字取值：未链接列时不应被当作过滤条件误识别
    matches, _ = index.match(normalize_text("销售额高于平均的产品"), set())
    assert not any(m.value == "高" for m in matches)
    matches, _ = index.match(normalize_text("高等级客户的销售额"), {("customers", "customer_level")})
    assert any(m.value == "高" for m in matches)


def test_head_word_ambiguity_triggers_clarification(index):
    matches, ambiguities = index.match(normalize_text("星河的销售额"), set())
    assert not any(m.span == "星河" for m in matches)
    assert any(a.span == "星河" and len(a.candidates) >= 2 for a in ambiguities)
