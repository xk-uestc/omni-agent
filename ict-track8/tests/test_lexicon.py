from __future__ import annotations

from datetime import date

import pytest

from backend.nl2sql import lexicon
from backend.nl2sql.schema import normalize_text


def test_cn_to_int_basic_and_composite():
    assert lexicon.cn_to_int("三") == 3
    assert lexicon.cn_to_int("十二") == 12
    assert lexicon.cn_to_int("二十") == 20
    assert lexicon.cn_to_int("一万五千") == 15000
    assert lexicon.cn_to_int("100") == 100
    assert lexicon.cn_to_int("abc") is None


@pytest.mark.parametrize(
    "question, start, end",
    [
        ("2025年第一季度销售额", "2025-01-01", "2025-04-01"),
        ("2025年Q1销售额", "2025-01-01", "2025-04-01"),
        ("2025年1月到3月的销售额", "2025-01-01", "2025-04-01"),
        ("2025年1-3月销售额", "2025-01-01", "2025-04-01"),
        ("2025年上半年", "2025-01-01", "2025-07-01"),
        ("2025年3月", "2025-03-01", "2025-04-01"),
        ("2025-03的销售额", "2025-03-01", "2025-04-01"),
        ("二〇二五年销售额", "2025-01-01", "2026-01-01"),
        ("2025年12月到2026年2月", "2025-12-01", "2026-03-01"),
        ("2025年前3个月的销售额", "2025-01-01", "2025-04-01"),
    ],
)
def test_parse_time_ranges_are_half_open_and_correct(question, start, end):
    result = lexicon.parse_time(normalize_text(question), date(2026, 9, 22))
    assert result.error_code is None
    assert len(result.ranges) == 1
    assert result.ranges[0].iso() == (start, end)


def test_parse_time_multiple_ranges_returns_clarification():
    result = lexicon.parse_time("2024年和2025年销售额", date(2026, 9, 22))
    assert result.error_code == "multiple_time_ranges"


def test_parse_time_relative_requires_reference_date():
    result = lexicon.parse_time("去年的销售额", None)
    assert result.error_code == "missing_reference_date"


def test_parse_time_relative_uses_reference_date():
    result = lexicon.parse_time("去年的销售额", date(2026, 9, 22))
    assert result.ranges[0].iso() == ("2025-01-01", "2026-01-01")
    assert result.assumptions


@pytest.mark.parametrize(
    "question, operator, value",
    [
        ("销售额不超过10000的地区", "<=", 10000.0),
        ("销售额不低于平均", None, None),
        ("销售额超过1万的地区", ">", 10000.0),
        ("10万元以上", ">=", 100000.0),
        ("至少五千", ">=", 5000.0),
    ],
)
def test_parse_threshold_polarity_and_units(question, operator, value):
    result = lexicon.parse_threshold(question)
    assert result is not None
    if value is None:
        assert result.mode == "scalar_avg"
    else:
        assert result.operator == operator
        assert result.value == pytest.approx(value)


def test_parse_top_n_excludes_month_unit():
    assert lexicon.parse_top_n("前3个月") is None
    top = lexicon.parse_top_n("前3个产品")
    assert top is not None and top.n == 3 and top.descending is True


def test_parse_top_n_supports_descending_and_ascending():
    assert lexicon.parse_top_n("销量前2的产品").descending is True
    assert lexicon.parse_top_n("倒数3名").descending is False
