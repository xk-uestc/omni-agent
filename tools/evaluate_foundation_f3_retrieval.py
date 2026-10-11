"""Frozen F2 development comparison for bounded F3 retrieval experiments.

The frozen Gold is opened after the result set is constructed and is used only
for scoring. This tool never calls a generator or accesses private settings.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from foundation_common import ROOT, dump, environment, sha, source_hashes, percentile
from backend.knowledge_store import KnowledgeStore
from evaluate_foundation_f2 import raw_file
from freeze_foundation_f2 import BENCH


def decompose(query: str) -> list[str]:
    """Return at most two bounded clause queries; keep exact source/year anchors."""
    parts = [part.strip(" ，,；;。！？?！\n\t") for part in
             re.split(r"(?:并且|同时|以及|分别|且|并|；|;|。|？|！|\?|！|\n)", query)
             if len(part.strip()) >= 4]
    if len(parts) < 2:
        return []
    years = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query)
    identifiers = re.findall(r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9_-]{2,63}(?![A-Za-z0-9_])", query)
    anchors = list(dict.fromkeys(identifiers + years))
    return [" ".join([part, *anchors])[:1000] for part in parts[:2]]


def run(split: str, output: str, mode: str, bench: Path = BENCH):
    manifest_path = bench / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert all(sha(ROOT / path) == value for path, value in manifest["files"].items())
    before = source_hashes()
    tasks = json.loads((bench / f"{split}.json").read_text())
    families = {task["source_family"] for task in tasks}
    all_docs = json.loads((bench / "documents.json").read_text())
    docs = [doc for doc in all_docs if doc["family"] in families]
    gold = json.loads((bench / "scoring.json").read_text())
    records, dense_status = [], "not_configured"
    with tempfile.TemporaryDirectory(prefix="f3-retrieval-") as temp:
        embedder = None
        if mode == "bm25_bge":
            from backend.dense_retrieval import LocalBgeEmbedder
            try:
                embedder = LocalBgeEmbedder(ROOT / "models/bge-small-zh-v1.5")
                # Fail clearly before indexing instead of silently claiming a BGE run.
                embedder.embed(["离线 BGE 依赖探测"], query=True)
            except (ImportError, ModuleNotFoundError) as exc:
                dense_status = f"not_run_missing_local_dependency:{type(exc).__name__}"
                embedder = None
            else:
                dense_status = "enabled"
        store = KnowledgeStore(temp, embedder=embedder)
        for doc in docs:
            store.ingest(raw_file(doc), document_id=doc["id"], title=doc["title"],
                         modality=doc["type"], filename=doc["id"] + "." + doc["type"])
        chunk_map = {record.document_id: record for record in store.records()}
        latencies = []
        for task in tasks:
            start = time.perf_counter()
            hits, audit = store.search(task["query"], top_k=task["top_k"], with_audit=True)
            candidate_audits = [audit]
            query_set = [task["query"]]
            if mode == "bounded_decomposition":
                query_set += decompose(task["query"])
                # Each clause gets a bounded retrieval call. RRF combines only
                # returned Top-K evidence; source scope and SHA checks stay in
                # KnowledgeStore. No Gold data influences query construction.
                if len(query_set) > 1:
                    channel_hits = [hits]
                    for clause in query_set[1:]:
                        clause_hits, clause_audit = store.search(clause, top_k=task["top_k"], with_audit=True)
                        channel_hits.append(clause_hits)
                        candidate_audits.append(clause_audit)
                    # Bounded RRF over at most three query results; preserve the
                    # original query's selected evidence on exact score ties.
                    score = defaultdict(float)
                    first = {}
                    for channel_no, channel in enumerate(channel_hits):
                        for rank, hit in enumerate(channel, 1):
                            first.setdefault(hit.document_id, hit)
                            score[hit.document_id] += 1 / (60 + rank + channel_no * 0.01)
                    hits = [first[key] for key in sorted(score, key=lambda key: (-score[key], key))[:task["top_k"]]]
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)
            expected = gold[task["id"]]
            ids = list(dict.fromkeys(hit.metadata["document_id"] for hit in hits))
            hit_text = {did: "\n".join(chunk_map[hit.document_id].content for hit in hits
                                          if hit.metadata["document_id"] == did) for did in ids}
            required = expected["required"]
            spans = [(did, literal) for did, values in required.items() for literal in values]
            recovered = [literal in hit_text.get(did, "") for did, literal in spans]
            candidates = [item for candidate_audit in candidate_audits for item in candidate_audit.get("candidates", [])]
            candidate_sources = {item.get("source_document_id") for item in candidates}
            candidate_text = defaultdict(str)
            for item in candidates:
                chunk_id = item.get("chunk_id")
                did = item.get("source_document_id")
                if chunk_id in chunk_map and did:
                    candidate_text[did] += "\n" + chunk_map[chunk_id].content
            required_sources = set(required)
            candidate_spans = [(did, literal) for did, literal in spans]
            candidate_recovered = [literal in candidate_text.get(did, "") for did, literal in candidate_spans]
            decoys = set(task.get("wrong_binding_sources", []))
            wrong = sorted(set(ids).intersection(decoys))
            records.append({
                "id": task["id"], "family": task["source_family"], "kind": task["kind"],
                "query": task["query"], "decomposition_queries": query_set[1:],
                "candidate_source_recall_at_20": len(required_sources & candidate_sources) / len(required_sources) if required_sources else None,
                "candidate_evidence_recall_at_20": sum(candidate_recovered) / len(candidate_recovered) if candidate_recovered else None,
                "source_recall_at_k": len(required_sources & set(ids)) / len(required_sources) if required_sources else None,
                "evidence_recall_at_k": sum(recovered) / len(recovered) if recovered else None,
                "required_evidence_count": len(spans), "recovered_evidence_count": sum(recovered),
                "complete_required_evidence": all(recovered) if recovered else None,
                "gold_sources": sorted(required_sources), "retrieved_sources": ids,
                "hit_chunk_ids": [hit.document_id for hit in hits], "hit_evidence": hit_text,
                "wrong_entity_source_binding": bool(wrong), "wrong_binding_sources": wrong,
                "expected_abstain": expected["expected_abstain"], "retrieval_empty": not hits,
                "unsupported_retrieval_contamination": bool(expected["expected_abstain"] and hits),
                "query_ms": elapsed, "answer_metrics": "not_run",
            })
        valued = [record for record in records if record["source_recall_at_k"] is not None]
        mean = lambda key: sum(r[key] for r in valued if r[key] is not None) / len([r for r in valued if r[key] is not None])
        summary = {
            "task_count": len(records), "source_family_count": len(families), "document_count": len(docs),
            "source_recall_at_k": mean("source_recall_at_k"),
            "candidate_source_recall_at_20": mean("candidate_source_recall_at_20"),
            "candidate_evidence_recall_at_20": mean("candidate_evidence_recall_at_20"),
            "evidence_recall_at_k": mean("evidence_recall_at_k"),
            "complete_required_evidence": sum(r["complete_required_evidence"] is True for r in records),
            "wrong_entity_source_binding": sum(r["wrong_entity_source_binding"] for r in records),
            "unsupported_retrieval_contamination": sum(r["unsupported_retrieval_contamination"] for r in records),
            "query_p50_ms": percentile(latencies, .5), "query_p95_ms": percentile(latencies, .95),
            "dense_status": dense_status, "answer_metrics": "not_run",
        }
        report = {
            "environment": environment(), "split": split, "mode": mode,
            "source_families": sorted(families), "source_before": before,
            "source_after": source_hashes(), "input_manifest_sha256": sha(manifest_path) if manifest_path.exists() else None,
            "scorer_sha256": sha(__file__), "dense_status": dense_status,
            "query_policy": "at most original plus two deterministic clauses; exact identifier/year anchors preserved",
            "query_budget": {"max_queries_per_task": 3, "max_top_k_per_query": 20},
            "metrics": summary, "records": records,
            "claim": "authored synthetic parser-real retrieval experiment; not a public or official benchmark",
        }
        assert before == source_hashes()
        dump(output, report)
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "retained"], required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=["bm25", "bm25_bge", "bounded_decomposition"], required=True)
    parser.add_argument("--input-dir", type=Path, default=BENCH)
    args = parser.parse_args()
    run(args.split, args.output, args.mode, args.input_dir)
