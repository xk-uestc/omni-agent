"""Grounded model proposals preserve explicit intent without bypassing validation."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from backend.dependency_agent import DependencyAgent
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.nl2sql.seed import initialize_database


def payload():
    return {"version": 2, "metrics": [{"id": "model_revenue", "table": "sales_orders",
        "column": "sales_amount", "function": "SUM", "label": "销售总金额",
        "unit": "currency", "currency": "CNY"}], "dimensions": [],
        "filters": [{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"},
                    {"table": "sales_orders", "column": "order_date", "operator": "RANGE",
                     "value": ["2025-01-01", "2026-01-01"]}],
        "confidence": .95, "rewritten_question": "2025年华东销售额"}


class CapturingSession:
    def __init__(self, plan, barrier=None):
        self.plan, self.calls, self.barrier = plan, [], barrier

    def post(self, url, **kwargs):
        self.calls.append(deepcopy(kwargs["json"]))
        if self.barrier:
            self.barrier.wait(timeout=10)
        data = {"model": "gpt-6-luna", "status": "completed", "output": [{"type": "message",
            "content": [{"type": "output_text", "text": json.dumps({"plan": self.plan})}]}]}
        class Response:
            status_code = 200
            content = json.dumps(data).encode()
            def raise_for_status(self):
                pass
            def json(self):
                return data
        return Response()


def provider(plan, barrier=None):
    session = CapturingSession(plan, barrier)
    return ResponsesModelPlanProvider("https://example.com/v1", "test-secret", model="gpt-6-luna",
                                      max_retries=0, session=session), session


def test_verified_slots_reach_actual_model_request_and_business_label_is_audited(tmp_path):
    p, session = provider(payload())
    engine = Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite"), model_plan_provider=p)
    result = engine.answer("2025年华东销售额")
    slots = json.loads(session.calls[0]["input"])["verified_intent"]
    assert slots["metrics"][0]["column"] == "sales_amount"
    assert slots["metrics"][0]["function"] == "SUM"
    assert slots["metrics"][0]["label"] == "销售额"
    assert any(f["value"] == "华东" for f in slots["filters"])
    assert any(f["operator"] == "RANGE" and f["value"] == ["2025-01-01", "2026-01-01"]
               for f in slots["filters"])
    assert slots["dimensions"] == []
    assert "sql" not in slots and "plan" not in slots
    assert result.plan["planner_source"] == "model_validated"
    assert result.plan["metrics"][0]["id"] == "model_revenue"
    assert result.plan["planner_audit"]["label_normalizations"] == [
        {"metric_id": "model_revenue", "from": "销售总金额", "to": "销售额",
         "reason": "verified_business_metric_label"}]
    # The same canonical column is available to dependency execution, rather
    # than fixing only a display card while leaving downstream refs broken.
    assert DependencyAgent.resolve({"ref": "sql", "path": ["rows", 0, "销售额"]},
                                   {"sql": result.to_dict()}) == 29584


@pytest.mark.parametrize("mutation", ["fake_column", "different_aggregation", "missing_filter", "different_count"])
def test_verified_intent_does_not_make_unsafe_or_semantically_wrong_proposal_accepted(tmp_path, mutation):
    plan = payload()
    question = "2025年华东销售额"
    if mutation == "fake_column":
        plan["metrics"][0]["column"] = "imaginary_amount"
    elif mutation == "different_aggregation":
        plan["metrics"][0]["function"] = "AVG"
    elif mutation == "missing_filter":
        plan["filters"] = []
    else:
        question = "2025年华东订单数"
        plan["metrics"][0].update(column="order_id", function="COUNT_DISTINCT", label="订单数", unit="count", currency=None)
    p, session = provider(plan)
    result = Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite"), model_plan_provider=p).answer(question)
    assert len(session.calls) == 1
    assert result.plan["planner_source"] == "rules_fallback"
    assert result.plan["planner_audit"]["fallback"] is True
    assert "label_normalizations" not in result.plan["planner_audit"]


def test_request_local_intent_is_not_shared_between_concurrent_calls():
    p, session = provider(payload(), threading.Barrier(2))
    intents = [{"filters": [{"value": "华东"}], "clarification_code": None},
               {"filters": [{"value": "华南"}], "clarification_code": "missing_metric"}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(p.propose, q, (), intent) for q, intent in zip(("东区问题", "南区问题"), intents)]
        assert all(f.result()["version"] == 2 for f in futures)
    contexts = {json.loads(call["input"])["question"]: json.loads(call["input"])["verified_intent"]
                for call in session.calls}
    assert contexts == {"东区问题": intents[0], "南区问题": intents[1]}
    assert not hasattr(p, "verified_intent")


def test_grouping_and_clarification_constraints_reach_provider(tmp_path):
    p, session = provider(payload())
    engine = Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite"), model_plan_provider=p)
    engine.answer("2025年按月统计销售额")
    slots = json.loads(session.calls[-1]["input"])["verified_intent"]
    assert any(d["column"] == "order_date" and d["transform"] == "month" for d in slots["dimensions"])
    engine.answer("2025年各未知维度的销售额")
    slots = json.loads(session.calls[-1]["input"])["verified_intent"]
    assert slots["clarification_code"] == "missing_group_dimension"


def test_actual_having_constraint_is_serialized_to_model_request(tmp_path):
    p, session = provider(payload())
    engine = Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite"), model_plan_provider=p)
    engine.answer("各地区销售额高于平均")
    assert len(session.calls) == 1
    slots = json.loads(session.calls[0]["input"])["verified_intent"]
    assert slots["having"]["operator"] == ">"
    assert slots["having"]["mode"] == "scalar_avg"
    assert slots["having"]["value"] is None
    assert any(d["column"] == "region" for d in slots["dimensions"])


def test_legacy_callable_provider_still_works_without_propose(tmp_path):
    result = Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite"),
        model_plan_provider=lambda question, tables: payload()).answer("2025年华东销售额")
    assert result.plan["planner_source"] == "model_validated"
    assert result.rows == ({"销售额": 29584.0},)
