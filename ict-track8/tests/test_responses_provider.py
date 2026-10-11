import json

import pytest

from backend.nl2sql.model_contract import ModelPlanError
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.responses_client import StructuredResponses


class Response:
    def __init__(self, data):
        self.data = data
        self.status_code = 200
        self.content = json.dumps(data).encode()

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    def __init__(self, data):
        self.data, self.calls = data, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.data)


def _provider(data, model, monkeypatch):
    session = Session(data)
    client = StructuredResponses("https://example.com/v1", "test-secret", model=model, session=session)
    monkeypatch.setattr("backend.nl2sql.responses_provider.configured_responses_client",
        lambda **kwargs: client)
    return ResponsesModelPlanProvider("https://example.com/v1", "test-secret", model=model), session


def test_responses_adapter_uses_structured_schema_and_extracts_only_plan(monkeypatch):
    session = Session({"status": "completed", "model": "gpt-6-luna", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"plan": {"version": 2, "metrics": []}})}]}],
        "usage": {"input_tokens": 10, "output_tokens": 20}})
    provider, session = _provider(session.data, "gpt-6-luna", monkeypatch)
    assert provider("销售额", ()) == {"version": 2, "metrics": []}
    url, kwargs = session.calls[0]
    assert url == "https://example.com/v1/responses"
    assert kwargs["json"]["store"] is False
    assert kwargs["json"]["text"]["format"]["strict"] is True
    assert kwargs["json"]["reasoning"]["effort"] == "medium"
    assert "test-secret" not in json.dumps(provider.audit)
    assert provider.audit["output_tokens"] == 20
    assert provider.audit['model_verified'] is True


@pytest.mark.parametrize("data", [
    {"model": "test-model", "status": "incomplete", "output": []},
    {"model": "test-model", "status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
    {"model": "test-model", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "DROP TABLE orders"}]}]},
])
def test_incomplete_refused_or_non_json_response_never_becomes_plan(data, monkeypatch):
    provider, _ = _provider(data, "test-model", monkeypatch)
    with pytest.raises(ModelPlanError):
        provider("销售额", ())


def test_dsml_mixed_into_json_is_rejected_and_audit_keeps_only_a_fingerprint(monkeypatch):
    malformed = '{"plan": <|property|>"metrics"}'
    data = {"model": "test-model", "status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": malformed}]}],
        "usage": {"input_tokens": 12, "output_tokens": 7}}
    provider, _ = _provider(data, "test-model", monkeypatch)

    with pytest.raises(ModelPlanError):
        provider("销售额", ())

    audit = provider.audit
    assert audit["output_failure"]["kind"] == "invalid_json"
    assert audit["output_failure"]["characters"] == len(malformed)
    assert audit["output_failure"]["error_position"] > 0
    assert malformed not in json.dumps(audit)
    assert "test-secret" not in json.dumps(audit)
