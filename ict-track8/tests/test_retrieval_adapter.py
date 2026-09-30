from __future__ import annotations

import pytest
import requests

from backend.cross_source import DocumentRetrievalError
from backend.retrieval_adapter import HttpManualRetriever


class FakeResponse:
    def __init__(self, payload=None, *, error: Exception | None = None, status_code: int = 200):
        self.payload = payload
        self.error = error
        self.status_code = status_code

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_http_retriever_maps_real_evidence_contract():
    session = FakeSession(
        FakeResponse(
            {
                "data": {
                    "results": [
                        {
                            "chunk_id": "manual-1#p2",
                            "section": "安全须知",
                            "content": "请断电后清洁。",
                            "manual": "oven",
                            "rank": 1,
                            "evidence_role": "primary",
                            "pics": ["pic-1"],
                            "relevance": {"combined_relevance": "0.91"},
                        }
                    ]
                }
            }
        )
    )
    hits = HttpManualRetriever("http://127.0.0.1:8014", "secret", session=session).search("注意事项", top_k=99)
    assert hits[0].document_id == "manual-1#p2"
    assert hits[0].score == pytest.approx(0.91)
    assert hits[0].metadata["pics"] == ["pic-1"]
    assert session.calls[0][1]["json"]["top_k"] == 20
    assert session.calls[0][1]["headers"]["Accept"] == "application/json"


def test_http_retriever_exposes_status_without_response_body_or_token():
    response = FakeResponse(error=requests.HTTPError("forbidden"))
    error = requests.HTTPError("forbidden")
    error.response = type("Response", (), {"status_code": 403})()
    response.error = error
    session = FakeSession(response)
    with pytest.raises(DocumentRetrievalError, match="HTTP 403"):
        HttpManualRetriever("https://example.test", "do-not-log", session=session).search("政策")


@pytest.mark.parametrize("payload", [{"data": {"results": {}}}, {"unexpected": True}, {"data": {"results": ["bad"]}}])
def test_http_retriever_rejects_malformed_payload(payload):
    session = FakeSession(FakeResponse(payload))
    with pytest.raises(DocumentRetrievalError):
        HttpManualRetriever("https://example.test", "secret", session=session).search("政策")


def test_http_retriever_validates_configuration():
    with pytest.raises(ValueError):
        HttpManualRetriever("ftp://example.test", "secret")
    with pytest.raises(ValueError):
        HttpManualRetriever("https://example.test", "")


def test_http_retriever_retries_transient_5xx_then_succeeds():
    transient = requests.HTTPError("server error")
    transient.response = type("Response", (), {"status_code": 503})()

    class RetrySession(FakeSession):
        def __init__(self):
            super().__init__(FakeResponse({"data": {"results": []}}))
            self.attempt = 0

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            self.attempt += 1
            if self.attempt == 1:
                raise transient
            return self.response

    session = RetrySession()
    hits = HttpManualRetriever("https://example.test", "secret", backoff_seconds=0, session=session).search("政策")
    assert hits == []
    assert len(session.calls) == 2


def test_http_retriever_health_does_not_expose_body_or_token():
    session = FakeSession(FakeResponse({"secret": "body"}, status_code=200))
    report = HttpManualRetriever("https://example.test", "secret-token", session=session).health()
    assert report["ok"] is True
    assert "secret-token" not in repr(report)
