# Production capability upgrade — 2026-10-12

Base: `662b6864d70ae9a4ab57b770275a64d8d6257c56`, branch `memory`.
Scope follows the uploaded May 2026 formal Track 8 requirements. No training,
paid inference, GPU rental, private-host access or main-branch modification.

## Problems actually reproduced and fixed

1. Real tables existed in PDFs but were rejected by the numeric/vision-specific
   adapter: inferred external headers, font-metric boxes crossing grid lines,
   merged body cells and text-valued cells. The existing PyMuPDF table engine
   now also produces bounded native table records using glyph-outline boxes,
   physical spans and one literal internal header row. Raster/hidden OCR is
   excluded from this authority. MinerU remains the complex/scanned observation
   backend; its unreviewed outputs do not acquire calculation permission.
2. Structured row retrieval lost headers. Rows now retain explicit column
   paths and covering merged anchors. Native records enter the existing
   `document_elements` store, search index and bounded generator evidence.
   Before consumption, the relation and row are reconstructed from the pinned
   original. No database/storage framework was replaced.
3. `DependencyAgent.document_cell` only consumed Excel. It now consumes
   unique typed PDF cells with selector/header/value coordinates, SHA and
   complete-document uniqueness. Unknown units/scales cannot become formula
   operands through this adapter. Boolean selectors cannot equal numeric keys.
4. OmniAgent's keyword routing sent document “地区/数量” questions to the sales
   database. A complete original-bound literal question now selects the
   document route before keyword fallback. Existing fully verified SQL routing
   retains precedence; explicit database intent cannot be overridden.
5. Retrieval or failed generation returned excerpts with `status=ok`. These
   now return `insufficient_evidence`, completeness and separate excerpts.
   SQL→document retrieval also separates successful tools from an incomplete
   answer. Short labelled source previews remain visible. Source-only dialogue
   references and candidate-only Memory capture remain usable; local approval,
   revision, scope isolation and task-experience success gates remain intact.
6. Native PDF formula detection could accept a partial regex expression. The
   finite arithmetic path now requires a complete horizontal equation, uniform
   font size, bounded optical baseline offsets, no nearby mathematical fragment,
   no hidden/raster text, safe AST and re-extraction before/after calculation.
   OCR and unsupported math remain observations. This is a limited literal
   grammar, not general visual mathematical verification.

Production changes: `knowledge_store.py`, `structured_document.py`,
`visual_tables.py`, `visual_table_reader.py`, `pdf_table_values.py`,
`omni_agent.py`, `dependency_agent.py`, `native_formula_pages.py`,
`formula_parameter_binding.py`, `document_dialogue.py`, `memory/formation.py`,
and the two existing frontend answer renderers.

## Actual outcomes

| Capability | Result | Important limit |
|---|---|---|
| Public native PDF answers | 10/10 exact after whitespace normalization versus 0/10 before, through production `OmniAgent.query()` | Engineering development questions, not official ODB QA or a retained-set score |
| Sources | Labour-cost textbook, engineering-standard list, bank-penalty report | Three existing development originals; unseen-source generalization not measured |
| Negative cases | Ambiguous chemistry tables and a numeric-plus-why question both abstain | No claim that a single number answers a compound question |
| HTTP/SSE | Real upload → `/api/v1/omni/query` and stream return `10.26` with original-linked evidence | Local TestClient deployment, not a remote production deployment |
| Formula → SQL → calculation | Actual textbook insurance formula; SQL mapping supplies 500 CNY and 0.02; result 10 CNY agrees with independent Decimal | Graph manually supplied; SQL rows and business mapping are artificial and declared |
| SQL ranking → PDF cell | Executed rows identify 高级熟练工, then PDF tool obtains `10.26` | Manual graph; a comma variant still fails source-scope binding |
| OHR frozen development | 18/18 attempted, zero complete answers without model/OCR configured | All failures retained; not comparable to historical OCR/API runs |
| Cost | Zero paid/model calls, CPU only | No local GPU or previous private runtime available |

The 10 value answers are 10.26/7.61/5.95/4.90, three statute identifiers and
107/20/2 penalty counts. Scoring uses the unchanged official OmniDocBench
annotation SHA `2fafe9329dc92fc426b30036aee51c716b3fcdcc1d20cb964dc7670579533817`;
predictions are persisted before annotations are loaded by the scorer.
No reference annotations enter the corpus or planning context. No retained
originals were downloaded or used to tune this implementation.

Four one-page development documents ingest in approximately 0.05–0.24 seconds
each on this shared CPU environment. A combined OmniAgent query plus an
explicit-source query has median ~0.36 seconds, maximum ~0.56 seconds in the
saved development run. This is not an isolated hardware latency benchmark.
New native ingestion is bounded to 40 pages/30 seconds and 4,000 grid slots per
page; incomplete extraction is not persisted as complete table authority.
Hot consumption currently replays native geometry and can be optimized later.

## Reproduction and raw evidence

Use the environment versions in `runs/work_upgrade_20261012/environment.txt`.
The originals live under `runtime/real_document_upgrade/data`, restored from
the existing pinned manifest URLs and checked by their existing SHA. Originals
and full reference annotations are not committed. Large raw JSON runs are gzip
compressed without dropping failures.

```bash
python tools/evaluate_native_table_tasks.py --output runtime/native-table-predictions.json --score
python tools/replay_public_formula_integration.py --output runtime/public-formula-integration.json
python tools/evaluate_native_table_tasks.py --code-root /tmp/omni-baseline --output runtime/native-table-before.json --score
```

The first command ingests the four originals, runs production rule routing and
answers, then separately scores official cells. `--output` is the prediction
artifact; `--score` opens reference annotations only after it is written.
The second command runs and records the explicitly artificial database mapping
and manual graph, including the refused natural wording and successful explicit
scope wording. The third uses an independent baseline checkout; recreate it
with `git archive 662b6864d70ae9a4ab57b770275a64d8d6257c56` into a scratch
directory rather than changing the active development branch.

Raw predictions, intermediate routing failures, HTTP/SSE, formula receipts,
SQL/PDF integration failures and successful executions are under
`docs/foundation/runs/work_upgrade_20261012/`.
Native table/vision/generator/tool regressions passed (targeted run: 1,059).
Memory, source binding, routing and SSE compatibility passed (249).
Formula/source authority checks passed. Frontend checks passed (45).
These overlap and must not be added into a unique-test total. Two older
failures were reproduced at the unchanged base: a SQL followup test expecting
model dispatch despite server routing, and a test expecting an unsafe request
to execute despite the read-only safety policy. Neither policy was weakened.
Not every project test or acceptance task was executed.

## Remaining largest blockers

The unconfigured real model prevents autonomous planning/generation acceptance
here. More importantly, the grammar-heavy source binder still rejects valid
punctuation variants, table-level units outside columns are not bound, and
complex/scanned formulas and multi-level headers need validated structure.
The genuine formula's variables are extracted and mapped to exact SQL slots;
general automatic prose definition linking is not established. The three
required autonomous cross-source workflows and unseen-source stability are
unproven. Do not report 70/80/90% competition accuracy from these probes.
