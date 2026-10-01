"""结构化数据库 + 文档库的可解释多跳问答编排。

本模块只协调来源，不把文档内容拼进 SQL，也不允许文档检索结果改变 SQL 安全
策略。真实生产手册可通过 ``HttpManualRetriever`` 接入；测试和离线演示使用
本地 JSON 文档检索器，保证结果可复现。
"""

from __future__ import annotations

import hashlib
import difflib
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from math import log
from pathlib import Path
from typing import Any, Callable, Protocol

from .nl2sql.engine import Nl2SqlEngine
from .text_quality import simplify_for_retrieval, NORMALIZATION_ID


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")
_DOC_CUES = ("政策", "说明", "依据", "原因", "规则", "标准", "如何", "介绍", "文档", "相关")


def _terms(text: str) -> set[str]:
    return set(_tokenize(text))


def _tokenize(text: str) -> tuple[str, ...]:
    """Tokenize mixed Chinese/Latin text without external segmentation libraries.

    CJK bigrams/trigrams preserve useful phrases such as ``销售额`` and ``政策``;
    the full span is retained for short exact terms. Latin identifiers remain whole.
    """

    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(simplify_for_retrieval(text or "")):
        value = raw.lower()
        if not value.strip():
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            if len(value) <= 4:
                tokens.append(value)
            for size in (2, 3):
                tokens.extend(value[index : index + size] for index in range(len(value) - size + 1))
        else:
            tokens.append(value)
    return tuple(tokens)


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    title: str
    content: str
    source_uri: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DocumentHit:
    document_id: str
    title: str
    score: float
    matched_terms: tuple[str, ...]
    snippet: str
    source_uri: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DocumentRetriever(Protocol):
    def search(self, query: str, *, top_k: int = 4) -> list[DocumentHit]:
        ...


class DocumentRetrievalError(RuntimeError):
    """外部文档检索不可用；调用方必须保留结构化结果并显式降级。"""


class JsonDocumentRetriever:
    """小型、确定性的 BM25 离线文档检索器，用于开发和评测基线。"""

    _K1 = 1.2
    _B = 0.75

    def __init__(self, documents: list[DocumentRecord]):
        self.documents = tuple(documents)
        self._term_frequencies = tuple(
            Counter(_tokenize(f"{document.title} {document.content}")) for document in self.documents
        )
        self._title_terms = tuple(set(_tokenize(document.title)) for document in self.documents)
        self._document_frequency = Counter(
            term for frequencies in self._term_frequencies for term in frequencies.keys()
        )
        self._average_document_length = (
            sum(sum(frequencies.values()) for frequencies in self._term_frequencies) / len(self.documents)
            if self.documents
            else 0.0
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "JsonDocumentRetriever":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        documents = [
            DocumentRecord(
                document_id=str(item["document_id"]),
                title=str(item["title"]),
                content=str(item["content"]),
                source_uri=str(item.get("source_uri") or item["document_id"]),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload
        ]
        return cls(documents)

    def search(self, query: str, *, top_k: int = 4) -> list[DocumentHit]:
        query_terms = tuple(dict.fromkeys(_tokenize(query)))
        if not query_terms:
            return []
        hits: list[DocumentHit] = []
        total_documents = len(self.documents)
        for index, document in enumerate(self.documents):
            frequencies = self._term_frequencies[index]
            title_terms = self._title_terms[index]
            document_length = sum(frequencies.values())
            if not document_length:
                continue
            contributions: list[tuple[str, float]] = []
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency[term]
                idf = log(1 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5))
                denominator = frequency + self._K1 * (
                    1 - self._B + self._B * document_length / max(1.0, self._average_document_length)
                )
                contribution = idf * (frequency * (self._K1 + 1) / denominator)
                if term in title_terms:
                    contribution *= 1.8
                contributions.append((term, contribution))
            if not contributions:
                continue
            score = sum(value for _, value in contributions)
            # Keep the public score bounded while preserving BM25 ordering.
            bounded_score = score / (score + 1.0)
            matched = tuple(term for term, _ in sorted(contributions, key=lambda item: (-item[1], item[0])))
            hits.append(
                DocumentHit(
                    document_id=document.document_id,
                    title=document.title,
                    score=round(min(1.0, bounded_score), 4),
                    matched_terms=matched[:12],
                    snippet=self._snippet(document.content, matched),
                    source_uri=document.source_uri,
                    metadata={**document.metadata, "bm25_raw": round(score, 6), "retrieval_channel": "bm25", "text_normalization": NORMALIZATION_ID},
                )
            )
        hits.sort(key=lambda item: (-item.score, item.document_id))
        max_raw = max((float(item.metadata.get("bm25_raw", 0.0)) for item in hits), default=0.0)
        enriched = [
            DocumentHit(
                document_id=item.document_id,
                title=item.title,
                score=item.score,
                matched_terms=item.matched_terms,
                snippet=item.snippet,
                source_uri=item.source_uri,
                metadata={
                    **item.metadata,
                    "bm25_relative": round(float(item.metadata.get("bm25_raw", 0.0)) / max_raw, 4) if max_raw else 0.0,
                },
            )
            for item in hits
        ]
        return enriched[: max(1, min(20, int(top_k)))]

    @staticmethod
    def _snippet(content: str, matched: set[str], width: int = 180) -> str:
        normalized = simplify_for_retrieval(content)
        positions = [normalized.find(term) for term in matched if normalized.find(term) >= 0]
        position = min(positions) if positions else 0
        # Map the retrieval-only normalized offset back to the literal source.
        # OpenCC phrase conversion is not assumed to preserve string length.
        if normalized != content and positions:
            for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, content, normalized).get_opcodes():
                if b0 <= position < b1:
                    position = a0 + (position-b0 if tag == 'equal' else 0)
                    break
        start = max(0, position - 50)
        return content[start : start + width].strip()


class CrossSourceAgent:
    """把 SQL 和文档证据串成可审计的多跳结果。"""

    def __init__(self, sql_engine: Nl2SqlEngine, document_retriever: DocumentRetriever):
        self.sql_engine = sql_engine
        self.document_retriever = document_retriever

    def answer(
        self,
        question: str,
        *,
        top_k_documents: int = 4,
        context_questions: tuple[str, ...] = (),
        trace_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        effective_question, context_rewrite = self._with_context(question, context_questions)
        trace_id = hashlib.sha256(f"{effective_question}:{time.time_ns()}".encode()).hexdigest()[:16]
        trace: list[dict[str, Any]] = []
        def record(item: dict[str, Any]) -> None:
            trace.append(item)
            if trace_callback:
                try:
                    trace_callback({"trace_id": trace_id, **item})
                except Exception:
                    # 审计观察者不能改变问答结果；生产网关会记录自身的传输错误。
                    pass

        record(
            {
                "stage": "intent",
                "status": "completed",
                "input": question,
                "effective_question": effective_question,
                "context_turns": len(context_questions),
                "context_rewrite": context_rewrite,
                "output": "hybrid_structured_document",
            }
        )

        structured = self.sql_engine.answer(effective_question)
        record(
            {
                "stage": "structured_query",
                "status": structured.status,
                "sql": structured.sql,
                "parameters": list(structured.parameters),
                "rows": len(structured.rows),
                "field_links": structured.provenance.get("field_links", []),
                "plan": structured.plan,
                "explanation": list(structured.explanation),
                "query_hash": structured.provenance.get("query_hash"),
            }
        )

        document_query = self._document_query(effective_question, structured)
        if structured.status == "clarification" and not self._has_document_cue(effective_question):
            record({"stage": "clarification", "status": "required", "question": structured.clarification})
            return {
                "status": "clarification",
                "trace_id": trace_id,
                "question": question,
                "effective_question": effective_question,
                "context_turns": len(context_questions),
                "answer": structured.clarification,
                "structured": structured.to_dict(),
                "document_query": document_query,
                "document_evidence": [],
                "trace": trace,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }

        retrieval_error: str | None = None
        try:
            hits = self.document_retriever.search(document_query, top_k=top_k_documents)
        except DocumentRetrievalError as exc:
            hits = []
            retrieval_error = str(exc)
        except Exception as exc:  # 任何检索侧异常都不能吞掉已完成的结构化结果
            hits = []
            retrieval_error = f"文档检索异常（{type(exc).__name__}），已降级为仅结构化结果"
        record(
            {
                "stage": "document_retrieval",
                "status": "degraded" if retrieval_error else "completed",
                "query": document_query,
                "hit_count": len(hits),
                "sources": [hit.document_id for hit in hits],
                "candidates": [
                    {
                        "document_id": hit.document_id,
                        "title": hit.title,
                        "score": hit.score,
                        "matched_terms": list(hit.matched_terms),
                        "snippet": hit.snippet,
                        "source_uri": hit.source_uri,
                        # 保留检索器真实输出，供生产右侧审计映射使用。
                        # 这里不会把 bounded score 重新解释成 BM25 raw。
                        "metadata": dict(hit.metadata),
                    }
                    for hit in hits
                ],
                **({"error": retrieval_error} if retrieval_error else {}),
            }
        )
        record(
            {
                "stage": "evidence_fusion",
                "status": "completed",
                "structured_rows": len(structured.rows),
                "document_sources": len(hits),
                "selected_evidence": [
                    {
                        "document_id": hit.document_id,
                        "title": hit.title,
                        "snippet": hit.snippet,
                        "source_uri": hit.source_uri,
                        "role": hit.metadata.get("evidence_role", "supporting"),
                    }
                    for hit in hits
                ],
            }
        )
        return {
            "status": "partial" if retrieval_error else "ok",
            "trace_id": trace_id,
            "question": question,
            "effective_question": effective_question,
            "context_turns": len(context_questions),
            "answer": self._summary(structured, hits),
            "structured": structured.to_dict(),
            "document_query": document_query,
            "document_evidence": [hit.to_dict() for hit in hits],
            "trace": trace,
            "warnings": [retrieval_error] if retrieval_error else [],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    def _with_context(self, question: str, context_questions: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
        """多轮：由 NL2SQL 引擎做槽位级改写（取值/时间/指标覆盖），得到独立问题。

        旧实现把上一轮与本轮字符串拼接交给规划器，导致"那 X 呢"式追问仍按上一轮取值统计、
        独立新问题被误判为多指标歧义。改写结果写入 trace，保证可解释。
        """

        current = str(question or "").strip()
        if not current or not context_questions:
            return current, {"mode": "none"}
        previous = str(context_questions[-1] or "").strip()
        if not previous or previous == current:
            return current, {"mode": "none"}
        rewrite = getattr(self.sql_engine, "contextualize", None)
        if callable(rewrite):
            try:
                return rewrite(previous, current)
            except Exception as exc:  # 改写失败时保持本轮原问题，不拼接
                return current, {"mode": "error", "error": type(exc).__name__}
        return current, {"mode": "unsupported"}

    @staticmethod
    def _has_document_cue(question: str) -> bool:
        return any(cue in question for cue in _DOC_CUES)

    @staticmethod
    def _document_query(question: str, structured: Any) -> str:
        parts = [question]
        if structured.status == "ok":
            plan = structured.plan
            parts.extend(
                str(item.get("value"))
                for item in plan.get("filters", [])
                if item.get("value") is not None
            )
            for row in structured.rows[:3]:
                parts.extend(str(value) for value in row.values() if value is not None)
        return " ".join(dict.fromkeys(part for part in parts if str(part).strip()))

    @staticmethod
    def _summary(structured: Any, hits: list[DocumentHit]) -> str:
        pieces: list[str] = []
        if structured.status == "ok":
            pieces.append(f"结构化查询返回 {len(structured.rows)} 行，SQL 和字段映射已记录。")
            if structured.rows:
                first = structured.rows[0]
                pieces.append("首行结果：" + "，".join(f"{key}={value}" for key, value in first.items()))
        if hits:
            pieces.append(
                "文档证据："
                + "；".join(
                    f"{hit.title}（匹配 {', '.join(hit.matched_terms[:6])}）" for hit in hits
                )
            )
        else:
            pieces.append("未找到达到阈值的文档证据，未对缺失内容进行推断。")
        return "".join(pieces)
