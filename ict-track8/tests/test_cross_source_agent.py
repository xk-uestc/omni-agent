from __future__ import annotations

import json

from backend.cross_source import CrossSourceAgent, DocumentHit, DocumentRetrievalError, JsonDocumentRetriever
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.production_audit import to_production_audit


def make_agent(tmp_path):
    documents = [
        {
            "document_id": "east-policy",
            "title": "华东销售政策",
            "content": "2025 年华东区域高等级客户享有重点产品组合优惠，销售额按已完成订单统计。",
            "source_uri": "knowledge://east-policy",
            "metadata": {"region": "华东"},
        },
        {
            "document_id": "level-policy",
            "title": "客户等级说明",
            "content": "高等级客户享有优先响应和专属运营支持。",
            "source_uri": "knowledge://level-policy",
            "metadata": {"domain": "customer"},
        },
    ]
    path = tmp_path / "docs.json"
    path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
    return CrossSourceAgent(
        Nl2SqlEngine(initialize_database(tmp_path / "data.sqlite")),
        JsonDocumentRetriever.from_file(path),
    )


def test_hybrid_query_contains_both_sources_and_trace(tmp_path):
    result = make_agent(tmp_path).answer("2025年华东地区的销售额政策")
    assert result["status"] == "ok"
    assert result["structured"]["status"] == "ok"
    assert result["document_evidence"]
    assert [step["stage"] for step in result["trace"]] == [
        "intent",
        "structured_query",
        "document_retrieval",
        "evidence_fusion",
    ]
    assert result["structured"]["sql"]
    assert result["document_evidence"][0]["source_uri"] == "knowledge://east-policy"
    structured_trace = result["trace"][1]
    assert structured_trace["plan"]["metric_column"] == "sales_amount"
    assert structured_trace["query_hash"]
    retrieval_trace = result["trace"][2]
    assert retrieval_trace["candidates"][0]["matched_terms"]
    assert result["trace"][3]["selected_evidence"][0]["role"] == "supporting"


def test_document_only_question_does_not_invent_sql(tmp_path):
    result = make_agent(tmp_path).answer("高等级客户的政策说明")
    assert result["status"] == "ok"
    assert result["structured"]["status"] == "clarification"
    assert result["structured"]["sql"] is None
    assert result["document_evidence"]


def test_explicit_context_rewrites_follow_up_without_changing_stateless_mode(tmp_path):
    agent = make_agent(tmp_path)
    stateless = agent.answer("按月统计")
    contextual = agent.answer("按月统计", context_questions=("2025年华东地区的销售额",))
    assert stateless["context_turns"] == 0
    assert stateless["effective_question"] == "按月统计"
    assert contextual["context_turns"] == 1
    # 多轮改写由字符串拼接改为槽位级改写：继承上一轮的时间与取值，追加“按月统计”
    assert contextual["trace"][0]["context_rewrite"]["mode"] == "merged"
    assert "按月" in contextual["effective_question"] and "华东" in contextual["effective_question"]
    assert "用户补充问题" not in contextual["effective_question"]
    assert contextual["structured"]["status"] == "ok"
    assert contextual["structured"]["plan"]["metric_column"] == "sales_amount"


def test_ambiguous_question_requests_clarification(tmp_path):
    result = make_agent(tmp_path).answer("华东地区的情况")
    assert result["status"] == "clarification"
    assert result["document_evidence"] == []
    assert result["trace"][-1]["stage"] == "clarification"


class FailingRetriever:
    def search(self, query: str, *, top_k: int = 4):
        raise DocumentRetrievalError("检索服务不可用")


def test_document_failure_is_explicit_partial_result(tmp_path):
    agent = make_agent(tmp_path)
    agent.document_retriever = FailingRetriever()
    result = agent.answer("2025年华东地区的销售额政策")
    assert result["status"] == "partial"
    assert result["structured"]["status"] == "ok"
    assert result["document_evidence"] == []
    assert result["trace"][2]["status"] == "degraded"


def test_local_retriever_uses_phrase_and_title_signal_over_common_characters(tmp_path):
    path = tmp_path / "docs.json"
    path.write_text(
        json.dumps(
            [
                {
                    "document_id": "generic",
                    "title": "通用说明",
                    "content": "本说明介绍一般规则和相关流程。",
                    "source_uri": "knowledge://generic",
                    "metadata": {},
                },
                {
                    "document_id": "oven-door",
                    "title": "烤箱门安全注意事项",
                    "content": "打开烤箱门时应缓慢操作，防止高温蒸汽烫伤。",
                    "source_uri": "knowledge://oven-door",
                    "metadata": {},
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    hits = JsonDocumentRetriever.from_file(path).search("烤箱门安全注意事项", top_k=2)
    assert hits[0].document_id == "oven-door"
    assert "烤箱" in hits[0].matched_terms or "烤箱门" in hits[0].matched_terms


def test_local_retriever_preserves_raw_and_relative_bm25_scores(tmp_path):
    path = tmp_path / "docs.json"
    path.write_text(json.dumps([
        {"document_id": "a", "title": "销售政策", "content": "销售政策 销售政策", "source_uri": "a", "metadata": {}},
        {"document_id": "b", "title": "通用说明", "content": "销售说明", "source_uri": "b", "metadata": {}},
    ], ensure_ascii=False), encoding="utf-8")
    hits = JsonDocumentRetriever.from_file(path).search("销售政策", top_k=2)
    assert hits[0].metadata["bm25_raw"] > 0
    assert hits[0].metadata["bm25_relative"] == 1.0
    assert hits[0].metadata["retrieval_channel"] == "bm25"


def test_production_audit_mapping_does_not_fabricate_dense_or_rrf(tmp_path):
    result = make_agent(tmp_path).answer("2025年华东地区的销售额政策")
    audit = to_production_audit(result["trace"], status=result["status"], latency_ms=result["latency_ms"])
    assert audit["availability"] == {"bm25": True, "dense": False, "rrf": False, "rerank": False}
    assert audit["retrieval"]["candidates"][0]["bm25_raw"] is not None
    assert "dense_cosine" not in audit["retrieval"]["candidates"][0]
    assert audit["evidence"]["selected"]


def test_production_audit_does_not_default_unknown_channel_to_bm25():
    audit = to_production_audit([
        {"stage": "intent", "input": "政策", "effective_question": "政策"},
        {"stage": "structured_query", "status": "ok", "rows": []},
        {
            "stage": "document_retrieval",
            "query": "政策",
            "candidates": [{"document_id": "remote#1", "title": "远端证据", "score": 0.8}],
        },
        {
            "stage": "evidence_fusion",
            "selected_evidence": [{"document_id": "remote#1", "role": "supporting"}],
        },
    ])
    assert audit["retrieval"]["channels"] == []
    assert audit["availability"] == {"bm25": False, "dense": False, "rrf": False, "rerank": False}
    assert "retrieval_channel" not in audit["retrieval"]["candidates"][0]
