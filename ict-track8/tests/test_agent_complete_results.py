"""Chat and SSE opt-in must return every real result row, not a preview claim."""
from contextlib import closing
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

import backend.app as app_module
from backend.cross_source import CrossSourceAgent, JsonDocumentRetriever
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.models import QueryPlan
from backend.nl2sql.result_artifact import ResultBudgets
from backend.session import ConversationStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    database = tmp_path / "facts.sqlite"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE facts(region TEXT, amount REAL)")
        db.executemany("INSERT INTO facts VALUES (?, ?)", [(f"group-{i:03}", float(i)) for i in range(241)])
    engine = Nl2SqlEngine(database, result_artifact_dir=tmp_path / "results")
    engine._rules_plan = lambda *a, **kw: QueryPlan(table="facts", metric_table="facts",
        metric_column="amount", metric_function="SUM", metric_label="金额", dimensions=["region"],
        dimension_tables={"region": "facts"}, rewritten_question="各地区金额")
    agent = CrossSourceAgent(engine, JsonDocumentRetriever([]))
    monkeypatch.setattr(app_module, "engine", engine)
    monkeypatch.setattr(app_module, "agent", agent)
    monkeypatch.setattr(app_module, "conversation_store", ConversationStore(storage_path=tmp_path / "sessions.sqlite"))
    return TestClient(app_module.app), engine, database


@pytest.mark.parametrize("stream", [False, True])
def test_chat_all_pages_equal_real_sql_and_default_keeps_preview(setup, stream):
    client, engine, database = setup
    request = {"question": "各地区金额", "session_id": "full-chat"}
    legacy = client.post("/api/v1/agent/query", json=request).json()
    assert len(legacy["structured"]["rows"]) == 100
    assert "complete_result" not in legacy["structured"]["provenance"]
    if stream:
        response = client.post("/api/v1/agent/query/stream", json={**request, "complete_results": True})
        blocks = response.text.split("\n\n")
        result = json.loads(next(block.split("data: ", 1)[1] for block in blocks if block.startswith("event: done")))
        assert response.text.index("event: trace") < response.text.index("event: done")
    else:
        response = client.post("/api/v1/agent/query", json={**request, "complete_results": True})
        result = response.json()
    assert response.status_code == 200
    structured = result["structured"]
    receipt = structured["provenance"]["complete_result"]
    assert receipt["row_count"] == 241 and receipt["preview_truncated"] is True
    assert "完整查询结果 241 行" in result["answer"]
    assert result["trace"][1]["complete_result"]["artifact_id"] == receipt["artifact_id"]
    rows, offset = [], 0
    while True:
        page = client.get("/api/v1/nl2sql/results/" + receipt["artifact_id"], params={
            "binding_sha256": receipt["binding_sha256"], "query_sha256": receipt["query_sha256"], "offset": offset}).json()
        rows.extend(page["rows"])
        if page["next_offset"] is None: break
        offset = page["next_offset"]
    with closing(sqlite3.connect(database)) as db:
        expected = [list(row) for row in db.execute(structured["sql"], structured["parameters"])]
    assert rows == expected and len(rows) == 241
    assert len(app_module.conversation_store.context("full-chat")) == 2


def test_opt_in_preserves_context_rewrite(setup):
    _, engine, _ = setup
    agent = app_module.agent
    seen = []
    original = engine.answer
    def record(question, **kwargs):
        seen.append((question, kwargs)); return original(question, **kwargs)
    engine.answer = record
    agent._with_context = lambda question, context: ("各地区金额", {"applied": True})
    result = agent.answer("那去年呢", context_questions=("各地区金额",), complete_results=True)
    assert seen == [("各地区金额", {"complete_results": True})]
    assert result["context_turns"] == 1


def test_partial_budget_never_claims_complete_or_exposes_paging_id(setup):
    client, engine, _ = setup
    engine.complete_result_budgets = ResultBudgets(max_rows=110)
    response = client.post("/api/v1/agent/query", json={"question": "各地区金额", "complete_results": True})
    result = response.json(); receipt = result["structured"]["provenance"]["complete_result"]
    assert receipt["status"] == "partial" and receipt["artifact_id"] is None
    assert "完整结果未取得" in result["answer"]
    assert "完整查询结果 241" not in result["answer"]


def test_changed_source_is_refused_after_chat_result(setup):
    client, _, database = setup
    result = client.post("/api/v1/agent/query", json={"question": "各地区金额", "complete_results": True}).json()
    receipt = result["structured"]["provenance"]["complete_result"]
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE facts SET amount=999 WHERE region='group-000'")
    page = client.get("/api/v1/nl2sql/results/" + receipt["artifact_id"], params={
        "binding_sha256": receipt["binding_sha256"], "query_sha256": receipt["query_sha256"]})
    assert page.status_code == 409


def test_clarification_keeps_explicit_complete_result_option(setup, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(app_module.clarification_resolver, "apply", lambda *args: "各地区金额")
    response = client.post("/api/v1/nl2sql/clarify", json={"original_question": "各地区", "clarification_code": "metric",
        "selected_value": "amount", "complete_results": True})
    assert response.status_code == 200
    assert response.json()["result"]["structured"]["provenance"]["complete_result"]["row_count"] == 241
