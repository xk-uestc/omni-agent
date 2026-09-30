from __future__ import annotations

import pytest

from backend.image_quality import ImageEnhancer
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.nl2sql.model_contract import ModelPlanError


def _payload(**overrides):
    payload = {
        "version": 1,
        "table": "sales_orders",
        "metric": {"table": "sales_orders", "column": "sales_amount", "function": "SUM", "label": "销售额"},
        "dimensions": [{"table": "sales_orders", "column": "region", "label": "地区"}],
        "filters": [{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}],
        "analysis_mode": "aggregate",
        "limit": 20,
        "confidence": 0.9,
        "rewritten_question": "华东地区销售额",
    }
    payload.update(overrides)
    return payload


def test_low_confidence_model_plan_falls_back_to_rules(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: _payload(confidence=0.2), model_min_confidence=0.5)
    result = engine.answer("2025年华东地区的销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"


def test_ungrounded_filter_value_falls_back_to_rules(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(filters=[{"table": "sales_orders", "column": "region", "operator": "=", "value": "华南"}])
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("2025年华东地区的销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"


def test_high_confidence_model_plan_that_omits_explicit_time_is_rejected(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: _payload())
    result = engine.answer("2025年华东地区的销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert any("回退规则规划" in item for item in result.plan["assumptions"])
    assert result.plan["intent_audit"]["execution_allowed"] is True
    assert result.plan["planner_audit"] == {
        "candidate_source": "external_model",
        "final_source": "rules_fallback",
        "fallback": True,
        "decision": "rejected_or_unavailable",
        "reason_type": "ModelPlanError",
        "reason_code": "missing_explicit_filter",
        "reason": "模型计划遗漏了问题中的明确过滤条件",
    }


def test_accepted_model_plan_has_non_sensitive_planner_audit(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: _payload())
    result = engine.answer("华东的销售额")
    # 这个样例必须与规则规划器的真实槽位完全一致，才能测试“接受”分支。
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: _payload(dimensions=[], filters=[{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}]))
    result = engine.answer("华东的销售额")
    assert result.plan["planner_source"] == "model_validated"
    assert result.plan["planner_audit"] == {
        "candidate_source": "external_model",
        "final_source": "model_validated",
        "fallback": False,
        "decision": "accepted",
    }
    assert result.plan["intent_audit"]["planner_audit"] == result.plan["planner_audit"]


def test_intent_audit_exposes_verified_slots(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    result = Nl2SqlEngine(path).answer("2025年华东地区的销售额")
    audit = result.plan["intent_audit"]
    assert audit["metric"]["column"] == "sales_amount"
    assert audit["time_range"] == ["2025-01-01", "2026-01-01"]
    assert any(item["column"] == "region" and item["value"] == "华东" for item in audit["filters"])
    assert audit["execution_allowed"] is True


def test_high_confidence_model_plan_omitting_grouping_falls_back(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(dimensions=[])
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("2025年各地区的销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["dimensions"] == ["region"]


def test_high_confidence_model_plan_omitting_time_falls_back(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(filters=[{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}])
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("2025年华东地区的销售额")
    assert result.status == "ok"
    assert result.plan["planner_source"] == "rules_fallback"
    assert any(item["operator"] == "RANGE" for item in result.plan["filters"])


def test_model_cannot_bypass_rule_clarification_and_drop_explicit_time(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(
        dimensions=[],
        filters=[],
        confidence=0.99,
    )
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("2025年各未知维度的销售额")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["clarification_code"] == "missing_group_dimension"


def test_model_cannot_turn_explicit_rank_into_aggregate(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "product_name", "label": "产品"}],
        filters=[],
        analysis_mode="aggregate",
        confidence=0.99,
    )
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("销量最高的产品")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["top_n"] == 1


def test_model_cannot_change_explicit_top_n(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "product_name", "label": "产品"}],
        filters=[],
        confidence=0.99,
    )
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("销售额前3的产品")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["top_n"] == 3


def test_model_cannot_change_metric_aggregation_function(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(metric={"table": "sales_orders", "column": "sales_amount", "function": "AVG", "label": "销售额"})
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("华东的销售额")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["metric_function"] == "SUM"


def test_model_cannot_drop_explicit_having_threshold(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "region", "label": "地区"}],
        filters=[],
    )
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("各地区销售额高于平均")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["having"] is not None


def test_model_cannot_invent_having_threshold(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(
        dimensions=[],
        having={"operator": ">", "mode": "scalar", "value": 1000},
    )
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("华东的销售额")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["having"] is None


def test_model_cannot_reverse_top_n_sort_direction(tmp_path):
    path = initialize_database(tmp_path / "demo.sqlite")
    payload = _payload(
        dimensions=[{"table": "sales_orders", "column": "product_name", "label": "产品"}],
        filters=[],
        order_desc=False,
    )
    engine = Nl2SqlEngine(path, model_plan_provider=lambda q, t: payload)
    result = engine.answer("销售额前3的产品")
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["order_desc"] is True


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("模型计划增加了问题中没有依据的分组维度", "extra_dimension"),
        ("模型计划绕过了明确的分组维度澄清", "clarification_bypass"),
        ("模型计划遗漏了问题中的明确分组维度", "missing_group_dimension"),
        ("模型计划的指标聚合口径与问题不一致", "metric_aggregation_mismatch"),
        ("模型计划遗漏了问题中的明确聚合阈值", "missing_having"),
        ("模型计划增加了问题中没有依据的聚合阈值", "extra_having"),
        ("模型计划的聚合阈值与问题不一致", "having_mismatch"),
        ("模型计划的 Top-N 排序方向与问题不一致", "top_n_order_mismatch"),
    ],
)
def test_model_rejection_reason_codes_keep_specific_categories(message, code):
    assert Nl2SqlEngine._model_rejection_code(ModelPlanError(message)) == code


def test_model_rejection_reason_is_bounded_and_control_chars_are_removed():
    reason = Nl2SqlEngine._safe_model_rejection_reason(ModelPlanError("坏\x00\x1b" + "x" * 500))
    assert "\x00" not in reason and "\x1b" not in reason
    assert len(reason) == 200


@pytest.mark.parametrize("transform", ["brighten", "reduce_highlights", "contrast", "adaptive_threshold", "sharpen", "upscale"])
def test_every_recommendable_transform_is_in_enhancer_allowlist(transform):
    # 属性测试：ImageQualityAnalyzer 可能推荐的每个变换名，ImageEnhancer 都必须认识，
    # 否则重试计划会静默失败（这是此前 OCR 3 次重试 100% 失败的根因）。
    assert transform in ImageEnhancer.ALLOWED_TRANSFORMS
