"""生产 VNext 检索服务的 HTTP 适配器。"""

from __future__ import annotations

import time
from typing import Any

import requests

from .cross_source import DocumentHit, DocumentRetrievalError


class HttpManualRetriever:
    """调用生产 ``/retrieve``，并将真实来源压缩成赛题八统一证据契约。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 8.0,
        max_retries: int = 1,
        backoff_seconds: float = 0.08,
        session: Any | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("生产检索 URL 必须使用 http 或 https")
        if not self.token.strip():
            raise ValueError("生产检索 token 不能为空")
        if timeout <= 0:
            raise ValueError("生产检索 timeout 必须大于 0")
        if not 0 <= int(max_retries) <= 3:
            raise ValueError("生产检索 max_retries 必须在 0 到 3 之间")
        if backoff_seconds < 0:
            raise ValueError("生产检索 backoff_seconds 不能为负数")
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff_seconds = float(backoff_seconds)
        self.session = session or requests.Session()

    def search(self, query: str, *, top_k: int = 4) -> list[DocumentHit]:
        if not str(query).strip():
            return []
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/retrieve",
                    json={"question": query, "top_k": max(1, min(20, int(top_k)))},
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if attempt >= self.max_retries or not retryable:
                    suffix = f"（HTTP {status}）" if status else ""
                    raise DocumentRetrievalError(f"生产文档检索暂不可用{suffix}，已保留结构化结果") from exc
                time.sleep(self.backoff_seconds * (2**attempt))
        if response is None:
            raise DocumentRetrievalError("生产文档检索没有返回响应，已保留结构化结果")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise DocumentRetrievalError("生产文档检索返回的 JSON 无法解析，已保留结构化结果") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise DocumentRetrievalError("生产文档检索响应缺少 data 对象，已保留结构化结果")
        rows = payload["data"].get("results")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise DocumentRetrievalError("生产文档检索响应 results 不是数组，已保留结构化结果")
        hits: list[DocumentHit] = []
        for item in rows:
            if not isinstance(item, dict):
                raise DocumentRetrievalError("生产文档检索响应包含无效证据项，已保留结构化结果")
            relevance = item.get("relevance") or {}
            if not isinstance(relevance, dict):
                relevance = {}
            raw_score = relevance.get("combined_relevance")
            if raw_score is None:
                raw_score = relevance.get("score", 0.0)
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            hits.append(
                DocumentHit(
                    document_id=str(item.get("chunk_id") or item.get("section") or "unknown"),
                    title=str(item.get("section") or item.get("manual") or "manual"),
                    score=float(score),
                    matched_terms=tuple(),
                    snippet=str(item.get("content") or "")[:500],
                    source_uri=str(item.get("chunk_id") or item.get("manual") or "manual"),
                    metadata={
                        "manual": item.get("manual"),
                        "rank": item.get("rank"),
                        "evidence_role": item.get("evidence_role"),
                        "pics": item.get("pics") or [],
                        "retrieval_channel": item.get("retrieval_channel"),
                        "retrieval_channels": item.get("retrieval_channels") or relevance.get("channels"),
                    },
                )
            )
        return hits

    def health(self) -> dict[str, Any]:
        """只读探针；不把 token 或响应正文带回调用方。"""
        started = time.perf_counter()
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return {"ok": True, "status_code": getattr(response, "status_code", 200), "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return {"ok": False, "status_code": status, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
