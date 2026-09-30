"""把赛题八离线/混合 trace 映射为正式右侧审计栏可消费的最小契约。

只复制真实链路字段。离线检索只有 BM25 时，不会填充 Dense、RRF 或 rerank 的伪分数；
``retrieval.channels`` 明确记录可用通道，正式 VNext 的完整三路字段仍由 8014 自己提供。
"""

from __future__ import annotations

from typing import Any, Iterable


def _declared_channels(metadata: dict[str, Any]) -> set[str]:
    channels: set[str] = set()
    channel = metadata.get("retrieval_channel")
    if isinstance(channel, str) and channel.strip():
        channels.add(channel.strip().lower())
    raw_channels = metadata.get("retrieval_channels")
    if isinstance(raw_channels, (list, tuple, set)):
        channels.update(
            str(item).strip().lower()
            for item in raw_channels
            if str(item).strip()
        )
    return channels


def to_production_audit(trace: Iterable[dict[str, Any]], *, status: str = "ok", latency_ms: float | None = None) -> dict[str, Any]:
    stages = [item for item in trace if isinstance(item, dict)]
    by_stage = {str(item.get("stage")): item for item in stages}
    intent = by_stage.get("intent", {})
    structured = by_stage.get("structured_query", {})
    retrieval = by_stage.get("document_retrieval", {})
    fusion = by_stage.get("evidence_fusion", {})
    candidates: list[dict[str, Any]] = []
    channels: set[str] = set()
    selected_ids = {
        str(item.get("document_id") or item.get("chunk_id"))
        for item in fusion.get("selected_evidence", [])
        if isinstance(item, dict) and (item.get("document_id") or item.get("chunk_id"))
    }
    for item in retrieval.get("candidates", []):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        channels.update(_declared_channels(metadata))
        document_id = str(item.get("document_id") or item.get("chunk_id") or "unknown")
        row = {
            "chunk_id": document_id,
            "heading": item.get("title") or item.get("heading") or "",
            "excerpt": item.get("snippet") or item.get("content") or "",
            "selected": document_id in selected_ids,
            "evidence_role": "primary" if document_id in selected_ids and len(selected_ids) == 1 else ("supporting" if document_id in selected_ids else None),
            "bm25_raw": metadata.get("bm25_raw"),
            "bm25_relative": metadata.get("bm25_relative"),
            "retrieval_channel": metadata.get("retrieval_channel"),
            "source_uri": item.get("source_uri") or "",
        }
        candidates.append({key: value for key, value in row.items() if value is not None})
    selected: list[dict[str, Any]] = []
    for item in fusion.get("selected_evidence", []):
        if not isinstance(item, dict):
            continue
        selected.append({
            "chunk_id": item.get("document_id") or item.get("chunk_id") or "unknown",
            "heading": item.get("title") or item.get("heading") or "",
            "excerpt": item.get("snippet") or item.get("excerpt") or "",
            "source_uri": item.get("source_uri") or "",
            "role": item.get("role") or "supporting",
        })
    query = {
        "original": intent.get("input") or "",
        "effective": intent.get("effective_question") or "",
        "sparse": retrieval.get("query") or "",
    }
    audit = {
        "audit_contract_version": 1,
        "execution_path": "ict8_structured_document",
        "mode": "structured_document",
        "status": status,
        "query": query,
        "retrieval": {
            "channels": sorted(channels),
            "candidates": candidates,
            "candidate_count": len(candidates),
            "filtered_count": 0,
        },
        "evidence": {"selected": selected, "context_chars": sum(len(str(item.get("excerpt") or "")) for item in selected)},
        "structured": {
            "status": structured.get("status"),
            "sql": structured.get("sql"),
            "query_hash": structured.get("query_hash"),
            "rows": structured.get("rows"),
            "field_links": structured.get("field_links", []),
        },
        "availability": {
            "bm25": "bm25" in channels,
            "dense": "dense" in channels,
            "rrf": "rrf" in channels,
            "rerank": "rerank" in channels,
        },
    }
    if latency_ms is not None:
        audit["timings"] = {"total_ms": round(float(latency_ms), 2)}
    return audit
