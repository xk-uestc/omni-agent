# F2 Final Review

**Production source freeze:** `6608785ac364d4188948e1e4167cfb21c9da5b3d` (branch `memory`). The only post-freeze tracked code change is a test-fixture adjustment: `test_store_selects_third_same_source_and_verifies_original` now pins the dynamic control it stubs. No production file changed after the source freeze. F2 checkpoints 1–5 were individually committed and pushed.

## Retrieval and evidence results

The F2 benchmark contains 48 synthetic tasks / 30 parsed PDF and TXT sources, split by source family. Its validator confirms literal Gold, no source-family leakage, and all frozen input hashes. Answer EM, generated correctness, and fidelity remain `not_run`.

| Split / method | Source Recall@4 | Evidence Recall@4 | MRR / NDCG | Complete required evidence | Wrong-source bindings | Unsupported-query contamination | P50 / P95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dev dynamic BM25 | .8095 | .4325 | .3571 / .3941 | 6/21 | 6 | 3/3 | 22.68 / 70.14 ms |
| Dev persistent BM25 | .8095 | .4325 | .3571 / .3941 | 6/21 | 6 | 3/3 | 22.48 / 75.69 ms |
| Retained dynamic BM25 | .8095 | .4167 | .3333 / .3750 | 6/21 | 6 | 3/3 | 22.46 / 70.45 ms |
| Retained persistent BM25 | .8095 | .4167 | .3333 / .3750 | 6/21 | 6 | 3/3 | 22.55 / 75.02 ms |

The persistent index preserves every retrieval/grounding metric and SHA/locator validation. It does not improve the small F2 set's latency. It builds in about 7.1 ms and occupies 1.73 MB for the dev corpus and 1.75 MB for retained. Separate native-PDF scale tests show hot P50 improvements at 100/500/1000 pages (107.41→30.79, 715.83→529.24, 1461.08→1102.45 ms) with index sizes up to 16.76 MB; a 10-page corpus is slightly slower. These are local engineering measurements on generated documents, not public benchmark performance.

The F1 benchmark's 32 tasks remain saturated: BM25 and offline BGE both scored Source Recall, Evidence Recall, MRR, and NDCG 1.0, with all 21/21 dev and 7/7 retained answerable cases complete and verified SHA/locators. BGE added no recall or evidence gain. Dense P50 was 10.10 ms versus BM25 8.36 ms on dev and 10.63 versus 8.21 ms retained; irrelevant source ratio remained .6458. All seven model asset hashes match the pinned manifest; revision `7999e1d3359715c523056ef9478215996d62a620` was loaded offline. No weights were downloaded in F2.

On the six difficult dev table tasks, Top-K / F1 dossier / bounded table-chain evidence recall was .0417 / .7222 / 1.0 and complete tasks were 0/6 / 4/6 / 6/6. The table chain read 3–4 native pages (maximum budget six), checked 30 unique row IDs, source binding, repeated headers, units, filters, exclusion notes, and SHA. It proves only the declared page/row scope, not full-task semantic sufficiency, and it remains a study path rather than production routing.

## Semantic correctness and runtime

The AVG/SUM regression is fixed: a single explicit aggregation cue tied to a catalog metric survives catalog application and remains independently checked against dynamic source intent. SQLite gold execution covers grouped AVG, `COUNT(DISTINCT)`, NULL behavior, and group grain. A historical SUM memory binding cannot satisfy a current explicit AVG request. Related regressions passed 217/217.

The F2 model/runtime protocol suite passed 50 tests with two existing FastAPI deprecation warnings. Invalid JSON/DSML remains rejected; audit stores a text fingerprint and parse location without copying model output. Existing repair remains limited to one structural-handle repair, followed by the full server validation chain. SSE reports truthful timeout/worker status; the active worker retains its semaphore slot until return, and thread-start failure returns its slot. There is no forced cancellation of an active thread/provider/tool call, durable task-status retrieval, or exactly-once guarantee across client retries.

## Frozen baselines and final regression

The final local authored evaluations recorded 16 tasks at 10/16 without memory and 14/16 with the M1-B1 fixture memory; the 185-task context suite was 178/185 for both arms. The 22-task M1-B2 formation run was A=9/22, B=22/22, C=22/22; candidate semantic precision was 20/23, source extraction fidelity 23/23, with zero model calls. Its verifier confirmed frozen hashes, read-only DB, the 16/185 legacy comparisons, equal B/C knowledge bindings and rows. These are local synthetic mechanism tests, not official task results or proof of general memory benefit.

Final combined regression: **950 passed, 6 failed, 12 subtests passed, 25 warnings**. The six failures exactly overlap the pre-existing F1 final failure list except the AVG/SUM failure, which now passes:

| Existing failure | Current interpretation |
|---|---|
| `test_fusion_history::test_new_topic_and_reset_do_not_inherit_sources` | Existing reset/follow-up contract mismatch: returns `missing_followup_context` where the historical assertion expects `independent`; no source execution is authorized by that state. |
| `test_omni_agent::test_model_sees_verified_sql_followup_before_route_selection_and_repairs_spurious_clarification` | Historical fake-model expectation; verified local rule planning now resolves the follow-up as `rules_basic`, while the SQL result is valid. |
| `test_nl2sql_v2::test_unsafe_natural_language_directive_is_ignored_but_read_only_intent_survives` | Historical test expects a mixed destructive request to execute its read portion. The current safety contract rejects the whole mixed request before SQL. |
| `test_nl2sql_pressure_regressions::test_unsafe_write_instruction_is_stripped_or_rejected_without_model` | Same historical partial-execution expectation; current whole-request refusal is retained and separately covered by the safety contract. |
| `test_evidence_recovery_round5::test_real_source_retrieval_recovery_answers_original_question_once` | Existing test monkeypatch omits `with_audit=True` but expects the two-value return shape; the documented API returns only hits by default. |
| `test_evidence_recovery_round5::test_navigation_sees_original_condition_beyond_retrieval_preview` | Existing assertion expects the condition to be absent from the snippet; current source navigation places the literal condition in the snippet. |

The old seven assertions and frozen Gold remain unchanged. No new failure was introduced in the combined set. The evidence-coverage test stub was updated only to explicitly select the dynamic-BM25 control it monkeypatches; the persistent-index behavior has separate lifecycle and ranking tests.

## Training readiness

No PPO, SFT, or GRPO training was performed. Current evidence supports a versioned local index and stronger table evidence scope, but F2 retrieval quality remains low, generated-answer quality is unmeasured, M1-C's historical real-model scores do not show a reliable memory gain, and remaining failures include model/planning/runtime limitations. PPO readiness is not established. Further work requires review and, for model-effect claims, a separately authorized real-model evaluation budget.

## Reproduction artifacts

- Frozen F2 inputs and scores: `benchmarks/foundation_f2_20261011/manifest.json`, `runs/f2-final-{dynamic,persistent}-{dev,retained}.json`.
- Evidence-scope comparison: `runs/f2-final-evidence-dev.json`.
- F1 BM25/BGE: `runs/f2-final-f1-{bm25,bge}-{dev,retained}.json`.
- Final regression output: `runs/f2-final-tests-v4.txt` and `runs/f2-final-tests-v4.xml`.
- 16/185/22 baseline outputs: `docs/memory_rl/runs/f2-final-m1b1-20261011.txt`, `f2-final-context-20261011.json`, `f2-final-m1b2-20261011.txt`, and `f2-final-m1b2-verification-20261011.txt`.
- Stage evidence: [`F2_HARD_RAG_BENCHMARK.md`](F2_HARD_RAG_BENCHMARK.md), [`F2_EVIDENCE_COMPLETENESS.md`](F2_EVIDENCE_COMPLETENESS.md), [`F2_SEMANTIC_CORRECTNESS.md`](F2_SEMANTIC_CORRECTNESS.md), [`F2_RUNTIME_RELIABILITY.md`](F2_RUNTIME_RELIABILITY.md).
