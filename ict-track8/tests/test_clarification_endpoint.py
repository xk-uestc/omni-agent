from __future__ import annotations

from fastapi.testclient import TestClient

import backend.app as app_module
from backend.session import ConversationStore


def test_clarification_endpoint_returns_full_agent_result_and_remembers_once(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "conversation_store", ConversationStore(storage_path=tmp_path / "sessions.sqlite"))
    client = TestClient(app_module.app)
    response = client.post(
        "/api/v1/nl2sql/clarify",
        json={
            "original_question": "2025年华东地区的情况",
            "clarification_code": "missing_metric",
            "selected_value": "sales_amount",
            "selected_label": "销售额",
            "session_id": "clarify-demo",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["status"] == "ok"
    assert payload["result"]["structured"]["sql"]
    assert len(payload["result"]["trace"]) == 4
    turns = app_module.conversation_store.context("clarify-demo")
    assert len(turns) == 1
    assert turns[0].question == "2025年华东地区的情况"
    assert turns[0].effective_question.endswith("销售额")
