# F3 Checkpoint 1: F2 stage-loss attribution

**Starting source:** `d61ce23dfa32fcb2be01d6b71235f3e48e693aed` on `memory`. The F2 input manifest, questions, source family split, and scorer were not changed. `tools/diagnose_foundation_f2.py` runs ingestion, BM25, reranking, the existing `EvidenceCoverage` selector, PDF dossier construction, and (dev table tasks only) the existing bounded table chain before it reads the Gold literals. Gold is used only to audit the resulting stage receipts. No answer model is called.

The diagnostic preserves two evidence views. **Exact literal** matching reproduces the frozen F2 scorer byte-for-byte and is checked against its frozen output. **Field-normalized** matching identifies values preserved in parsed table chunks whose renderer changed their spelling from pipe-delimited rows to labelled fields, e.g. `E-17-02 | 北区 | 合格 | 12.5 | 2026-04-03` to `编号: E-17-02 | 区域: 北区 | 结论: 合格 | 读数(...): 12.5 | 日期: ...`. This normalization removes only those known labels, whitespace, and pipe separators; it is a failure-localization aid, not a new correctness scorer.

## Stage recall

Macro recall is averaged over the 21 answerable tasks, with each task weighted once. The exact Top-K row must match the committed F2 scorer; normalized rows localize content despite parser formatting. Candidate pool is the 20 BM25 hits after deterministic reranking; it has the same member set as raw BM25 because no Dense channel is configured. “Ranked Top-4” is the first four by post-retrieval rank, before greedy coverage selection. Dossier context is the union of Top-K chunks and selected native pages. The answerer itself remains `not_run`.

| Split / stage | Normalized evidence recall | Exact evidence recall | Complete answerable tasks |
|---|---:|---:|---:|
| Dev: raw source Gold literals | 1.0000 | 1.0000 | 21/21 |
| Dev: parsed chunks | 1.0000 | .7381 | 21/21 |
| Dev: BM25 candidate pool Top-20 | .7024 | not scored | 12/21 |
| Dev: reranked Top-4 before selector | .4714 | not separately scored | 6/21 |
| Dev: `EvidenceCoverage` selected Top-4 | .4754 | .4325 | 6/21 |
| Dev: Top-K + dossier pages | .6984 | not scored | 12/21 |
| Retained: raw source Gold literals | 1.0000 | 1.0000 | 21/21 |
| Retained: parsed chunks | .9841 | .7222 | 20/21 |
| Retained: BM25 candidate pool Top-20 | .6865 | not scored | 12/21 |
| Retained: reranked Top-4 before selector | .4595 | not separately scored | 6/21 |
| Retained: `EvidenceCoverage` selected Top-4 | .4595 | .4167 | 6/21 |
| Retained: Top-K + dossier pages | .6825 | not scored | 11/21 |

Field normalization finds all dev literals in parsed chunks and 98/99 in retained. The exact parser figures are lower because table labels and row layout change; this is not equivalent to source data disappearing. The largest measured loss is before/inside candidate retrieval: normalized BM25 Top-20 recall is .7024/.6865. Reducing the pool to ranked Top-4 loses a further .2310/.2270 macro recall. The selector changes this by only +.0040 on dev and 0 on retained; the current evidence does not support tuning the selector as the primary fix. Dossier page recovery raises potential context recall to .6984/.6825 but this has not been passed to or validated by an answerer.

## First-loss counts and difficult table tasks

For the 99 required literals per split, the dev first-loss counts are: survived selected Top-4 28; absent from BM25 Top-20 42; absent from ranked Top-4 but recovered by dossier 21; absent from ranked Top-4 but recovered by the table chain 2; absent from ranked Top-4 without recovery 5; selector-only recovery after the first four ranks 1. Retained counts are: survived Top-4 28; BM25 candidate miss 42; ranked Top-4 gap recovered by dossier 21; ranked Top-4 loss without recovery 7; and one normalized literal absent from parsed chunks. These are literal-level counts; task-level completeness remains the separate metric above.

On the six dev table tasks, normalized source-bound evidence recall is .5417 in the BM25 Top-20, .1500 in ranked Top-4, .1639 after `EvidenceCoverage`, .6667 after adding the dossier, and 1.0000 after the existing bounded table chain; complete tasks are 3/6, 0/6, 0/6, 4/6, and 6/6 respectively. The chain scans 3–4 pages per task within its six-page budget and checks source revision, entity binding, repeated headers, units, row inventory, filters, and exclusions. Its declared-page scope still does not prove general semantic sufficiency.

The F2 evidence-scope report's A score `.0417` is macro-averaged after concatenating all retrieved source texts. Re-auditing each literal against its designated source gives `.0139` exact Top-K macro recall on these six tasks: one unit/header literal is from the required source; two same-looking unit/header literals were matched only from a different source. Field-normalized matching separately finds ten correct-source values across the table queries, because selected chunks expose some rows with labelled fields. The old `.0417` historical result is retained; the source-bound re-score is the appropriate figure for source-reliability claims.

Wrong-source bindings remain **6/24** in each split. The same error pattern repeats in both source-family partitions: task `*-01` binds the 2025 report when asked for the 2026 report, and task `*-05` binds a same-name entity from a neighboring organization. Unsupported queries remain contaminated in **3/3** per split. The specific wrong bindings are the 2025 policy sources and neighboring-organization sources; title/year text alone is not trusted as a version filter. The retrieval layer has no independent authoritative business-version registry to safely discard a source based only on a heading.

## Per-query receipts and limits

The machine-readable records include every required literal, exact and normalized parsed chunk IDs, BM25 Top-20 candidates and BM25 ranks, reranked Top-4 membership, final selected chunks, dossier page receipts, table-chain page receipts and scope, first loss, wrong-source hits, query/dossier latency, and Gold use policy:

- `docs/foundation/runs/f3-cp1-stage-loss-dev-20261011.json`
- `docs/foundation/runs/f3-cp1-stage-loss-retained-20261011.json`

Reproduce with:

```bash
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/diagnose_foundation_f2.py --split dev --output docs/foundation/runs/f3-cp1-stage-loss-dev-20261011.json
PYTHONPATH=ict-track8:tools /tmp/omni-m1a-venv/bin/python tools/diagnose_foundation_f2.py --split retained --output docs/foundation/runs/f3-cp1-stage-loss-retained-20261011.json
```

The script asserts exact Top-K scorer parity with the frozen F2 output and confirms source hashes are unchanged. Its dossier/table-chain totals are diagnostic replays, not answer-accuracy results. No production ranking logic changed in this checkpoint. The measured pattern supports checking candidate recall and version/entity binding in checkpoint 2 before changing the coverage selector.
