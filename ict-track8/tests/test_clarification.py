from __future__ import annotations

import pytest

from backend.clarification import ClarificationResolver, ClarificationSelection
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def test_metric_selection_replans_original_question():
    question = ClarificationResolver().apply(
        "2025年华东地区的情况",
        ClarificationSelection("missing_metric", "sales_amount"),
    )
    assert question.endswith("销售额")


def test_dimension_selection_replans_comparison():
    question = ClarificationResolver().apply(
        "销售额对比",
        ClarificationSelection("missing_comparison_scope", "region"),
    )
    assert question.endswith("按地区")


def test_monthly_trend_selection_is_runnable(tmp_path):
    resolver = ClarificationResolver()
    question = resolver.apply("销售额趋势", ClarificationSelection("missing_time_range", "monthly_trend"))
    result = Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite")).answer("2025年 " + question)
    assert result.status == "ok"
    assert result.plan["dimension_transforms"]["order_date"] == "month"


def test_join_path_selection_is_encoded_for_auditable_replanning():
    question = ClarificationResolver().apply(
        "按客户区域统计金额",
        ClarificationSelection("ambiguous_join_path", "orders>customers>regions"),
    )
    assert question.endswith("[join_path:orders>customers>regions]")


@pytest.mark.parametrize("code,value", [("missing_comparison_scope", "time_range"), ("missing_time_range", "year")])
def test_ambiguous_time_selection_requires_explicit_date(code, value):
    with pytest.raises(ValueError):
        ClarificationResolver().apply("销售额", ClarificationSelection(code, value))
