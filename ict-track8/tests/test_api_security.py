from __future__ import annotations

from fastapi.testclient import TestClient

import backend.app as app_module


def test_production_api_requires_bearer_token_but_health_is_public(monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
    monkeypatch.setattr(app_module, "API_TOKEN", "test-token")
    client = TestClient(app_module.app)

    assert client.get("/health").status_code == 200
    unauthorized = client.post("/api/v1/nl2sql/query", json={"question": "华东的销售额"})
    assert unauthorized.status_code == 401
    assert unauthorized.json()["detail"]["code"] == "unauthorized"

    authorized = client.post(
        "/api/v1/nl2sql/query",
        headers={"Authorization": "Bearer test-token"},
        json={"question": "华东的销售额"},
    )
    assert authorized.status_code == 200


def test_cors_preflight_allows_production_authorization_header(monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
    monkeypatch.setattr(app_module, "API_TOKEN", "test-token")
    response = TestClient(app_module.app).options(
        "/api/v1/nl2sql/query",
        headers={
            "Origin": "http://localhost:8021",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert "authorization" in response.headers.get("access-control-allow-headers", "").lower()


def test_production_api_fails_closed_when_token_is_missing(monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    response = TestClient(app_module.app).post(
        "/api/v1/nl2sql/query", json={"question": "华东的销售额"}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "api_auth_not_configured"


def test_request_body_limit_applies_before_endpoint(monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION", False)
    monkeypatch.setattr(app_module, "API_MAX_BODY_BYTES", 1024)
    response = TestClient(app_module.app).post(
        "/api/v1/nl2sql/query",
        content=b"{" + b"x" * 2000 + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_production_upstream_rejects_public_http_and_allows_loopback():
    monkeypatch_state = app_module.IS_PRODUCTION
    try:
        app_module.IS_PRODUCTION = True
        app_module._validate_upstream_url("TEST_URL", "http://127.0.0.1:8090/plan")
        try:
            app_module._validate_upstream_url("TEST_URL", "http://planner.example/plan")
        except RuntimeError as exc:
            assert "HTTPS" in str(exc)
        else:
            raise AssertionError("public HTTP upstream must be rejected")
        try:
            app_module._validate_upstream_url("TEST_URL", "https://")
        except RuntimeError as exc:
            assert "主机名" in str(exc)
        else:
            raise AssertionError("malformed HTTPS upstream must be rejected")
    finally:
        app_module.IS_PRODUCTION = monkeypatch_state
