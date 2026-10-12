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

At this checkpoint 14 of the approved 20 calls have been reserved. No image
input, training, rental or legacy budget use occurred. Final token/cost and
regression receipts are appended with the post-freeze acceptance below.

```bash
python tools/replay_public_formula_integration.py --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --model-config /home/viva/omni-agent/runtime/private/m1c-model.env --ledger runtime/autonomous_cross_source/model-ledger.jsonl --output runtime/autonomous_cross_source/formula-model-replay.json
python tools/replay_ranked_pdf_planning.py --mode model --dataset standards --data-root /home/viva/omni-agent/runtime/real_document_upgrade/data --config /home/viva/omni-agent/runtime/private/m1c-model.env --ledger runtime/autonomous_cross_source/model-ledger.jsonl --output runtime/autonomous_cross_source/standard-model-replay.json
```
