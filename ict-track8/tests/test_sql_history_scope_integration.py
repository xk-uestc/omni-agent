"""Rejected contextualizations must not be reapplied below the scope verifier."""
from copy import deepcopy

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


class SqlRouter:
    def generate(self, instructions, context, *args, **kwargs):
        return {"route": "sql", "effective_question": context["question"],
                "tasks_json": "[]", "clarification": ""}


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path / "sales.sqlite")),
                     KnowledgeStore(tmp_path / "knowledge"), ConversationStore(), SqlRouter())


def test_rejected_time_mutation_cannot_be_reapplied_after_model_router(agent, monkeypatch):
    assert agent.query("2025年华东销售额", session_id="reject-time")["status"] == "ok"
    monkeypatch.setattr(agent.engine, "contextualize", lambda *args: (
        "2024年华南销售额", {"mode": "merged", "appended": "", "replaced": [
            {"slot": "sales_orders.region", "from": "华东", "to": "华南"}]}))
    answer = agent.query("那华南呢", session_id="reject-time")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "那华南呢"
    assert answer["status"] == "clarification"
    assert answer["result"].get("rows", []) == []


def test_unverified_saved_sql_filters_do_not_supply_query_defaults(agent):
    first = agent.query("2025年华东销售额", session_id="reject-state")
    assert first["status"] == "ok"
    state = deepcopy(first["state"])
    state["filters"] = []
    agent.conversations.remember("reject-state", question=first["question"],
                                 effective_question=first["effective_question"], state=state)
    answer = agent.query("那华南呢", session_id="reject-state")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "那华南呢"
    assert answer["status"] == "clarification"


def test_unknown_exclusion_cannot_be_dropped_by_context_rewriter(agent, monkeypatch):
    assert agent.query("2025年华东销售额", session_id="reject-exclusion")["status"] == "ok"
    monkeypatch.setattr(agent.engine, "contextualize", lambda *args: (
        "2025年华南销售额", {"mode": "merged", "appended": "", "replaced": [
            {"slot": "sales_orders.region", "from": "华东", "to": "华南"}]}))
    question = "那华南呢，不含线上"
    answer = agent.query(question, session_id="reject-exclusion")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == question
    assert answer["status"] == "clarification"


def test_verified_five_turn_slots_still_execute_exact_regions_years_metrics(agent):
    turns = [("2025年华东销售额", "2025", "华东", "销售额"),
             ("那华南呢", "2025", "华南", "销售额"),
             ("那2024年呢", "2024", "华南", "销售额"),
             ("那订单数呢", "2024", "华南", "订单数"),
             ("那华北呢", "2024", "华北", "订单数")]
    for index, (question, year, region, metric) in enumerate(turns):
        answer = agent.query(question, session_id="verified-five")
        expected = agent.engine.answer(f"{year}年{region}{metric}").to_dict()
        assert answer["status"] == "ok"
        assert answer["result"]["rows"] == expected["rows"]
        assert answer["context_turns"] == index
        if index:
            assert answer["context_resolution"]["mode"] == "server_verified_sql_followup"


@pytest.mark.parametrize("metric", ["销售额", "订单数", "销售额平均值"])
def test_pending_missing_metric_accepts_short_answer_and_preserves_scope(agent, metric):
    first = agent.query("2025年华东地区", session_id="refill")
    assert first["status"] == "clarification"
    answer = agent.query(metric, session_id="refill")
    expected = agent.engine.answer("2025年华东地区 " + metric).to_dict()
    assert answer["status"] == "ok"
    assert answer["result"]["rows"] == expected["rows"]
    assert answer["context_resolution"]["mode"] == "server_verified_sql_clarification_fill"


def test_metric_reply_after_unresolved_constraint_does_not_reuse_failed_scope(agent):
    first = agent.query("2025年华东销售额超过", session_id="unresolved")
    assert first["status"] == "clarification"
    answer = agent.query("那订单数呢", session_id="unresolved")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "那订单数呢"
    assert answer["status"] == "clarification"
    assert not answer["result"].get("rows")


def test_pending_refill_keeps_unknown_original_conditions_and_clarifies(agent):
    first = agent.query("2025年华东地区排除火星", session_id="unknown")
    assert first["status"] == "clarification"
    answer = agent.query("销售额", session_id="unknown")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "销售额"
    assert answer["status"] == "clarification"
    assert not answer["result"].get("rows")
    assert answer["state"]["pending_sql_scope"] == first["effective_question"]
    again = agent.query("订单数", session_id="unknown")
    assert again["status"] == "clarification"
    assert not again["result"].get("rows")
    fresh = agent.query("2024年华南订单数", session_id="unknown")
    assert fresh["status"] == "ok"
    assert fresh["effective_question"] == "2024年华南订单数"


def test_execution_failure_is_persisted_and_independent_next_turn_recovers(agent, monkeypatch):
    from backend.nl2sql.security import SqlSafetyError
    original = agent.engine.answer
    def reject(question, **kwargs):
        raise SqlSafetyError('secret_candidate_must_not_leak')
    monkeypatch.setattr(agent.engine, 'answer', reject)
    first = agent.query('2025年华东销售额', session_id='execution-failure')
    assert first['status'] == 'incomplete'
    assert first['result']['result_state'] == 'unexecuted'
    assert 'secret_candidate' not in str(first)
    assert 'executed_sql_context' not in first['state']
    monkeypatch.setattr(agent.engine, 'answer', original)
    followup = agent.query('那华南呢', session_id='execution-failure')
    assert followup['status'] == 'clarification'
    assert followup['context_turns'] == 1
    recovered = agent.query('2024年华南销售额', session_id='execution-failure')
    assert recovered['status'] == 'ok' and recovered['context_turns'] == 2


def test_invalid_saved_scope_plus_metric_followup_cannot_query_all_rows(agent):
    first = agent.query("2025年华东销售额", session_id="invalid-metric")
    state = deepcopy(first["state"])
    state["filters"] = []
    agent.conversations.remember("invalid-metric", question=first["question"],
                                 effective_question=first["effective_question"], state=state)
    answer = agent.query("那订单数呢", session_id="invalid-metric")
    assert answer["status"] == "clarification"
    assert not answer["result"].get("rows")


@pytest.mark.parametrize("previous", ["2025年华东地区", "2025年华东销售额"])
@pytest.mark.parametrize("question", ["文档中销售额是如何定义的？", "那文档中的销售额定义呢？"])
def test_document_metric_definition_is_not_blocked_as_sql_reply(agent, previous, question):
    agent.knowledge.ingest("销售额定义：已经确认的订单收入，不包括尚未成交的报价。".encode(),
                           document_id="definition", title="业务指标定义", modality="txt", filename="definition.txt")
    agent.query(previous, session_id="definition")
    class DefinitionRouter(SqlRouter):
        def generate(self, instructions, context, *args, **kwargs):
            assert context["actual_question"] == question
            return {"route": "document", "effective_question": "错误的订单数定义",
                    "tasks_json": "[]", "clarification": ""}
    agent.client = DefinitionRouter()
    answer = agent.query(question, session_id="definition")
    assert answer["route"] == "document"
    assert answer["effective_question"] == question
    assert answer["status"] == "ok"
    assert "销售额定义" in answer["result"]["answer"]
    assert not answer["state"].get("pending_sql_scope")


def test_full_query_after_missing_metric_clears_planning_history(agent):
    assert agent.query("2025年华东地区", session_id="fresh-pending")["status"] == "clarification"
    class FreshRouter(SqlRouter):
        def generate(self, instructions, context, *args, **kwargs):
            assert context["history"] == []
            return super().generate(instructions, context, *args, **kwargs)
    agent.client = FreshRouter()
    answer = agent.query("2024年华南订单数", session_id="fresh-pending")
    assert answer["status"] == "ok"
    assert answer["effective_question"] == "2024年华南订单数"
