from __future__ import annotations

from fastapi.testclient import TestClient

import backend.app as app_module
from backend.session import ConversationStore


def test_agent_callback_receives_each_trace_stage(tmp_path):
    events = []
    agent = app_module.CrossSourceAgent(
        app_module.engine,
        app_module.document_retriever,
    )
    result = agent.answer("2025年华东地区的销售额政策", trace_callback=events.append)
    assert result["status"] == "ok"
    assert [item["stage"] for item in events] == [
        "intent",
        "structured_query",
        "document_retrieval",
        "evidence_fusion",
    ]
    assert all(item["trace_id"] == result["trace_id"] for item in events)


def test_stream_endpoint_sends_trace_before_done(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "conversation_store", ConversationStore(storage_path=tmp_path / "sessions.sqlite"))
    client = TestClient(app_module.app)
    with client.stream(
        "POST",
        "/api/v1/agent/query/stream",
        json={"question": "2025年华东地区的销售额政策", "session_id": "stream-demo"},
    ) as response:
        assert response.status_code == 200
        body = response.read().decode("utf-8")
    assert "event: trace" in body
    assert "event: audit" in body
    assert "event: done" in body
    assert body.index("event: trace") < body.index("event: done")
    assert body.index("event: audit") < body.index("event: done")
    assert '"stage": "document_retrieval"' in body
    assert len(app_module.conversation_store.context("stream-demo")) == 1
