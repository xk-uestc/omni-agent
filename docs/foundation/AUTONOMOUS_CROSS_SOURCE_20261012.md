# Autonomous cross-source engineering — 2026-10-12

Start: `8e0c42c3d40e0cebc978ff28ad12141fd36e092d`, branch `memory`.
The older `/home/viva/omni-agent` checkout and its uncommitted reports are
preserved. Development uses a separate clone of the latest remote `memory`.

## Checkpoint 1: ranked SQL -> real PDF cell

The exact saved comma failure from `sql-to-public-pdf-grammar-1.json` now
executes. The scope parser previously separated `取最高的1个` from the database
read; the uncovered ranking clause caused `source_scope_unverified` before
any tool ran. Literal ranking continuations now remain in the same original
source span, and the existing typed QueryPlan verifies the complete scope.

`sql_document_cell_binding.py` derives a small server-owned source contract
from the original request, Schema and replayed native table. The model chooses
its own graph. Before a cell consumes a SQL dimension, it reuses the existing
independent original-scope SQL compilation, read-only execution, physical-row
and complete-tie replay. Document identity, SHA, requested column and dynamic
selector are checked separately. Constants, substituted metrics/directions,
unrequested sources and omitted winners cannot authorize a cell. Physical
units outside the verified column still do not authorize calculation.

| Same development task/data | Before | After |
|---|---|---|
| Exact saved question, manual graph | scope refusal | SQL and real PDF cell execute, 10.26 |
| Exact saved question, real text model | model proposes graph; scope refusal | model proposes SQL -> cell; complete answer |
| Short natural question | initially omitted Top1 and repeated invalid plan | explicit rank constraint in navigation/instructions; complete answer |
| Ordinary HTTP / SSE | not repeated at baseline | real model plan, actual tools, complete answer and original link replay pass |

SQL actually returns 高级熟练工 with SUM(工时)=110. The public textbook cell
returns 10.26. The deterministic answer retains both results and the PDF SHA,
page/selector/target coordinates and original endpoint citation. HTTP/SSE use
the existing application endpoints in TestClient, not a remote deployment.

The PDF is the prior pinned development original. SQL rows and their business
relationship to textbook grades are **artificial integration mapping**, saved
in each trace. This is one workflow/source task with wording variants, not
multiple independent benchmark successes or an official competition score.
There is no graph/winner/answer in the real model context; document previews
are actual retrieved source data. `server_ranked_cell_intent` supplies only
independently checked source/column/rank navigation. Frozen Gold is untouched.

Guard fixtures: 24 passed. Existing SQL -> search / source constraint /
cross-source fixtures: 135 passed. Formula/dynamic source/history targeted
checks: 112 passed, one failure. That reset-history failure is reproduced
unchanged at the starting SHA. Broader regression details follow after the
remaining production checkpoint; no whole-project green claim is made.

## Reproduction and evidence

Use `/tmp/omni-native-venv/bin/python` (the prior pinned native environment).
Data root: `/home/viva/omni-agent/runtime/real_document_upgrade/data`.
Credentials stay in the user-specified private config, never committed.
All this round's model commands share `runtime/autonomous_cross_source/model-ledger.jsonl`.
The user explicitly approved at most 20 text calls / RMB 5 for this round.
The former visual/API budget is not reused. No training or GPU rental occurs.

```bash
python tools/replay_ranked_pdf_planning.py --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --output runtime/autonomous_cross_source/manual-after.json
python tools/replay_ranked_pdf_planning.py --mode model --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --config /home/viva/omni-agent/runtime/private/m1c-model.env --ledger runtime/autonomous_cross_source/model-ledger.jsonl --output runtime/autonomous_cross_source/model-replay.json
```

Use `--code-root /tmp/omni-ranked-baseline` for an independently archived
starting SHA, `--mode http` or `--mode sse` for application endpoint execution,
and `--question` for an explicit wording. These model commands consume budget;
offline raw-trace inspection and manual replay do not.

Raw outputs, full planning contexts/model outputs, actual SQL/cell provenance
and SSE events, including the initial failed natural-plan repair, are saved
without dropping failures in `runs/autonomous_cross_source_20261012/*.json.gz`.
## Checkpoint 2: fresh bounded protocol repair and formula execution

The real formula baseline model produced a JSON object followed by DSML/role
text, so strict JSON decoding correctly refused it. The production planner
now allows one fresh proposal in its existing two-call budget only for an
identified, completed HTTP response with an `invalid_json` fingerprint.
It never strips or executes malformed output and never feeds arbitrary raw
provider output back into context. Transport loss, truncation, refusal and
identity failure are not protocol repair eligibility. The research adapter
records the same audit fields and retains strict model-identity checking.

The explicit source question from the prior manual integration now runs via
real OmniAgent model planning: actual textbook formula -> SQL aggregates ->
independent variable/unit/business-slot validation -> safe calculation.
The model needed a fresh plan after it used illegal non-ASCII task IDs. Result
is 10.00 CNY, independently equal to Decimal(500)*Decimal(.02). The original
formula quote is presented as a conventional PDF citation; normalized formula
syntax is not misrepresented as a verbatim quote. SQL numbers are artificial
business data and remain disclosed. This is one real-source integration task,
not an official paired SQL/document benchmark.

The abbreviated request using only `费率` still refuses source scope after a
successful real malformed-JSON repair. Its short alias and combined database
parameter scope are not yet independently bound. No alias or filter guard was
relaxed to pass it. That failed output and its successful JSON repair are
retained in `formula-model-after.json.gz`.

A second development original (engineering standards list) now runs through
the same autonomous SQL -> PDF path with a different text-valued target:
SQL finds 《中华人民共和国劳动合同法》 with 130 citations in the declared
artificial integration data; the PDF returns `主席令第65 号`. No production
source-specific branch was added. Thus actual numeric table cells, textual
table cells and formula/SQL operands have been consumed by model-proposed
plans, across two originals and two distinct workflow types. These do not
prove arbitrary multi-document joint analysis, scans or multilevel headers.

At this checkpoint 14 of the approved 20 calls had been reserved. No image
input, training, rental or legacy budget use occurred.

```bash
python tools/replay_public_formula_integration.py --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --model-config /home/viva/omni-agent/runtime/private/m1c-model.env --ledger runtime/autonomous_cross_source/model-ledger.jsonl --output runtime/autonomous_cross_source/formula-model-replay.json
python tools/replay_ranked_pdf_planning.py --mode model --dataset standards --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --config /home/viva/omni-agent/runtime/private/m1c-model.env --ledger runtime/autonomous_cross_source/model-ledger.jsonl --output runtime/autonomous_cross_source/standard-model-replay.json
```

## Frozen acceptance, regressions and final limits

Production freeze / second pushed commit:
`644a097e0077a588f2ea525ca7eff50fe470d062`.
First pushed capability commit: `5fa17ed5de0ef6cf0196912ade091142b79ee84d`.
Later commits only save acceptance evidence, harness and status documentation.
Source fingerprints are in `production-freeze.json`.

One retained original was selected by its position as the first retained
manifest entry before parsing. It is the ceat.200600410 FCCU paper page,
SHA `8ec39e435894a6e887ee4f83a2d7f618755c2d813e5b6f1175cbfb2d7e85c428`.
This source was not used to develop this round's changes, although prior
project parser experiments may have processed this retained split. This is
not a claim of whole-project never-seen exposure. No Gold was read.

The frozen task asks SQL for the most monitored variable and the actual PDF
for its Description. It **fails**: the line-less scientific table generates
zero verified native tables/rows, so the server does not authorize the proposed
cell. Two real planning calls end in clarification. Original-page visual
inspection confirms a table is present; its missing structural authority is
a real parser/consumption gap. Neither a different retained source nor a
post-acceptance production fix was used. Raw refusal and both model plans are
in `frozen-acceptance.json.gz`.

Final combined relevant regressions: **542 passed / 4 failed**, 23 dependency
deprecation warnings. All four failures were reproduced independently at
the unchanged starting SHA: reset-history mode expectation; unsafe request
test expecting partial execution; model-followup dispatch expectation;
single-source recovery fixture expecting `ok` for incomplete generation.
The SQL safety / incomplete-answer policies were not weakened. A final
protocol identity/repair audit after the last narrow adapter guard is
42 passed; these overlap and must not be added to 542. The 102-test formula /
protocol targeted receipt also overlaps. Not every project test was run.
The first broad run and isolated stream tests stalled in sandbox-local
async communication, with the same isolated baseline stall. In the host
environment HTTP/SSE fixtures pass (12); the final combined host run finishes
in 23.01 seconds. Partial sandbox runs are not counted as complete passes.

Production files changed across the round:
`dependency_agent.py`, `fusion_constraints.py`, `sql_document_binding.py`,
`sql_document_cell_binding.py`, `omni_agent.py`, `responses_client.py`,
`fusion_result_answer.py`. Research adapters/harnesses remain separate in
`tools/`; no global deployment configuration was changed. No source originals,
frozen Gold, `Qimem/`, `main` or older local uncommitted work were modified.

16 ledger reservations / 16 known responses, model `deepseek-flash`, text
only. Input: 100,640 tokens; output: 3,468; cached input: 51,840. Actual billed
currency is not available from these receipts. Using [official peak token
prices](https://api-docs.deepseek.com/quick_start/pricing/), estimated total is
$0.01911264 with reported cache hits, or a conservative $0.0343536 without
cache discounts, well within the approved RMB 5 cap. Sum of model response
wall times is 23.808 seconds. Full-task development wall times: ranked original
1.919s; short ranked question 1.617s; HTTP 1.974s; SSE 2.369s; standards 2.093s.
These are shared-machine observations, not isolated performance benchmarks.
All individual latencies, usage and failed calls are retained.

The next principal score limits are independently binding abbreviated formula
parameter scopes, extracting verifiable structure from line-less/scanned and
multilevel tables, and proving a full multi-document/database analysis task.
Workflow C remains unverified. No aggregate official RAG/SQL/competition
accuracy, public cross-source benchmark score or remote deployment is claimed.

```bash
python tools/accept_frozen_cross_source.py --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --config /home/viva/omni-agent/runtime/private/m1c-model.env --ledger runtime/autonomous_cross_source/model-ledger.jsonl --output runtime/autonomous_cross_source/frozen-acceptance-replay.json
PYTHONPATH=ict-track8 python -m pytest ict-track8/tests/test_sql_document_cells.py ict-track8/tests/test_sql_document_binding.py ict-track8/tests/test_fusion_source_constraints.py ict-track8/tests/test_cross_source_agent.py ict-track8/tests/test_formula_parameter_binding.py ict-track8/tests/test_document_formula_binding.py ict-track8/tests/test_dynamic_source_binding.py ict-track8/tests/test_fusion_history.py ict-track8/tests/test_native_table_consumption.py ict-track8/tests/test_memory_adapter.py ict-track8/tests/test_memory_formation.py ict-track8/tests/test_memory_core.py ict-track8/tests/test_omni_query_stream.py ict-track8/tests/test_foundation_stream_fault.py ict-track8/tests/test_nl2sql_baseline.py ict-track8/tests/test_nl2sql_v2.py ict-track8/tests/test_omni_agent.py ict-track8/tests/test_omni_plan_recovery_round4.py ict-track8/tests/test_completed_plan_protocol_repair.py ict-track8/tests/test_responses_audit.py ict-track8/tests/test_m1c_model_budget.py -q
```

Model replays consume remaining budget and are not automatic authorization
for new calls after this delivered round. Run local HTTP/SSE regression tests
in an environment permitting their local asynchronous communication.
