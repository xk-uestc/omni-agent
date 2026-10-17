# F2 follow-up reproduction and profiling

Date: 2026-10-11. Evaluation checkout: `memory` at `d96a2faa79315318dbeed8ccb070cd56b0ed4f5b`. The F2 manifest pins production source to `6608785ac364d4188948e1e4167cfb21c9da5b3d`; the final documentation commit is a descendant of that freeze. The tracked worktree was clean before this run; pre-existing untracked `Qimem/` was left untouched.

## Artifact integrity

`docs/foundation/runs/F2_FINAL_ARTIFACT_MANIFEST.json` lists 189 artifacts. All 189 exist and match both SHA-256 and byte length; none are missing or mismatched. Manifest SHA-256: `cdd391b11df936533ffbaf26435f5687fe17a5a2ebef30692d7d40e8bf5fcf80`. `origin/memory` and local `HEAD` both resolve to `d96a2faa79315318dbeed8ccb070cd56b0ed4f5b`.

## Frozen difficult RAG A/B reproduction

The F2 set has 48 synthetic document questions and 30 synthetic source documents, split by source family. A is no-change dynamic BM25; B is the versioned persistent BM25 index with identical scoring and top-k. This is an index implementation comparison, not a static-memory versus dynamic-memory experiment. All runs use `top_k=4`, no Dense channel, and no model calls. Each run records per-query query text, retrieved document IDs, chunks, page locators, evidence text, source SHA checks, audit trace, and query time. Answer correctness, fidelity, and citation correctness remain `not_run`.

| Split / mode | Source Recall@4 | Evidence Recall@4 | MRR | NDCG | Complete evidence | Wrong-source bindings | Unsupported-query contamination | P50 / P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Dev A dynamic | .8095 | .4325 | .3571 | .3941 | 6/21 | 6/24 | 3/3 | 23.74 / 79.54 |
| Dev B persistent | .8095 | .4325 | .3571 | .3941 | 6/21 | 6/24 | 3/3 | 23.53 / 78.81 |
| Retained A dynamic | .8095 | .4167 | .3333 | .3750 | 6/21 | 6/24 | 3/3 | 23.11 / 75.25 |
| Retained B persistent | .8095 | .4167 | .3333 | .3750 | 6/21 | 6/24 | 3/3 | 23.28 / 75.99 |

Retrieval quality is identical. At this corpus size, repeat timings show no reliable latency gain; the persistent index adds approximately 1.73 MB (dev) / 1.75 MB (retained), with a separately measured cold index build of 8.00 / 8.37 ms. Per-run ingestion/parse was about 472–480 ms. The repeated wall timings differ from the earlier frozen baseline due to machine scheduling; neither comparison supports a small-corpus speedup.

Independent synthetic native-PDF scaling on the actual `KnowledgeStore` shows the size-dependent tradeoff:

| Pages | Dynamic hot P50 ms | Persistent hot P50 ms | Persistent cold index build ms | Index size |
|---:|---:|---:|---:|---:|
| 10 | 4.50 | 4.89 | 4.97 | 0.20 MB |
| 100 | 106.68 | 30.30 | 67.50 | 1.69 MB |
| 500 | 710.71 | 525.92 | 342.07 | 8.34 MB |
| 1000 | 1452.26 | 1087.46 | 702.90 | 16.76 MB |

This is local engineering evidence on generated native PDFs, not a public benchmark. It supports retaining the persistent index for larger corpora; it does not solve cold-ingest cost or the remaining ~1.1 s hot latency at 1000 pages.

## Profile and SQL semantic checks

A warmed `cProfile` run covered 72 `KnowledgeStore.search` calls over the 24 dev questions and 189 chunks. The profile attributed the largest cumulative Python time to `_tokenize` (1.835 s across 15,336 calls), followed by `_page_scope_coverage` (1.097 s across 72 calls), persistent-index search (0.346 s) and evidence selection (0.262 s). This indicates repeated tokenization/page-coverage work is a more promising next optimization target than replacing the already-cached BM25 postings. The profile includes profiler overhead and is not used as a latency result. No ranking or tokenizer change is made from this single profile; such a change needs the frozen dev scorer and retained one-shot evaluation.

The F2 RAG corpus is document QA and contains no SQL gold queries or backing business databases. We therefore did not invent generated SQL or claim SQL answer accuracy for these 48 questions. Cross-source planning, metric binding/compiler, conversational SQL edits, and round-9/round-10 multi-source semantic fixture tests were run separately: **315 passed**. These are local fixture/oracle regressions, not a live external database benchmark. Explicit AVG/SUM semantic protections remain covered by `docs/foundation/F2_SEMANTIC_CORRECTNESS.md`.

```bash
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python -m pytest -q ict-track8/tests/test_cross_source_agent.py ict-track8/tests/test_dynamic_source_binding.py ict-track8/tests/test_metric_catalog_bindings.py ict-track8/tests/test_metric_compiler.py ict-track8/tests/test_conversational_sql_edit.py ict-track8/tests/test_round9_sql_schema_semantics.py ict-track8/tests/test_round10_multi_source_spans.py ict-track8/tests/test_round10_multi_source_store.py ict-track8/tests/test_required_source_engine.py
```

The repeat run's full pytest output is `docs/foundation/runs/f2-repeat-cross-source-tests-20261011.txt`.

## Reproduction artifacts and conclusion

Commands used:

```bash
/tmp/omni-m1a-venv/bin/python tools/validate_foundation_f2.py
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split dev --output docs/foundation/runs/f2-repeat-A-dev-20261011.json
/tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2.py --split retained --output docs/foundation/runs/f2-repeat-A-retained-20261011.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2_index.py --strategy persistent --split dev --output docs/foundation/runs/f2-repeat-B-dev-20261011.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/evaluate_foundation_f2_index.py --strategy persistent --split retained --output docs/foundation/runs/f2-repeat-B-retained-20261011.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/measure_f2_index_scale.py --output docs/foundation/runs/f2-repeat-scale-20261011.json
```

Detailed query traces and environment measurements are in the `f2-repeat-*-20261011` files beside this report; the warmed component profile is `f2-repeat-profile-20261011.json`. Their hashes are recorded in `docs/foundation/runs/F2_FOLLOWUP_ARTIFACT_MANIFEST_20261011.json`. The persistent index preserves retrieval quality and improves hot lookup at 100–1000 pages, but difficult-set evidence completeness remains 6/21 and unsupported-query contamination remains 3/3. F2 alone does not establish memory uplift or RL readiness. The next useful work is to address evidence selection/retrieval gaps and independently measure memory behavior change; do not start PPO/GRPO based on these results.
