# Real PDF end-to-end execution — 2026-10-11

## What was exercised

Question: “What was the total amount of Sales/Use Tax collected from 2017 to 2021?” The source is one public OHR-Bench development PDF page. It is a scanned financial table: native text extraction does not return the answer, while OCR merges the columns and corrupts punctuation. The OHR gold answer was kept scoring-only and was never put in the knowledge corpus or sent in model context.

The run called `OmniAgent.query()` with the production `KnowledgeStore`, PDF OCR/structure store, existing `GroundedGenerator`, existing dependency/routing code and read-only SQL engine. The model itself selected the document route. Before the change, model generation fell back to a long OCR excerpt; that is the baseline recorded in `runs/real_document/real-agent-finance-20261011-prediction.json`.

## Change in the production path

`BudgetedPlanner` now accepts application-validated PDF `VisualAsset` inputs and maps them to DeepSeek's image-content format. It keeps the existing hash, image-size and image-count checks. Its audit now records the model identifier returned by the provider, and each response remains capped at 1,000 output tokens with no transport retry.

`route_visual_source_fallback()` permits this explicitly opted-in `deepseek-flash` client to use the existing source-first visual workflow. The workflow:

1. Uses retrieval only to choose a registered document/page.
2. Re-renders the immutable original PDF page and sends the image, not OCR text, for literal quote selection.
3. Runs a second independent call to check the whole question, row/column alignment, exact value, and quote locations.
4. Rechecks the source and rendered-image hashes and maps each accepted normalized box to PDF display points.
5. Keeps supporting labels and headings as citations. For a single-field currency question, it can display the one exact reviewed currency literal without appending the row label. It does not authorize calculation.
6. Leaves the prior OCR excerpt fallback in place when any visual check refuses or fails.

## Observed results

The first integrated visual run selected the correct source value and row, but included the row label in the display answer. The amount was correct, but exact match stayed 0. After adding the narrowly scoped literal-currency projection, a full OmniAgent run returned `$ 343,215,345`, normalized EM/F1 1/1. A second full OmniAgent run was refused by the independent visual-review step and retained OCR excerpts; it is recorded in `runs/real_document/real-agent-finance-20261011-final.json`. A separate scoped replay of the original-page production branch again returned the exact normalized answer and saved the complete proof in `runs/real_document/real-agent-finance-20261011-visual-replay.json`.

The successful proof binds three literal regions on page 1: `Sales/Use Tax`, `$ 343,215,345`, and `Total`. A replay script reloaded the registered source, recomputed its SHA and rendered-page SHA, checked the question hash, remapped all three boxes, matched each citation to its exact quote and coordinate, and confirmed all eight independent-review fields were true. The independent visual review is still model review, not a formal proof of entailment.

Two more frozen OHR development questions from the same PDF were run through the full Agent path. For the Highways & Streets CIP question, the model selected `Highways & Streets` and `198,687,347`, and the independent reviewer approved both source regions. The first displayed response included the row label (EM 0/F1 .5); a server-side projection now returns the one literal amount while keeping the row citation. Applying this deterministic projection to the saved reviewed evidence yields EM/F1 1/1 with no new model call; the raw model selection and review are preserved in `real-agent-finance-20261011-table-queries.json`. For Events Center Non-Taxable Bonds debt service, the independent reviewer rejected the candidate and the agent retained OCR excerpts. That failure is also preserved in the same report.

The runs used 29 calls under a 30-call cap. They consumed 113,138 input tokens and 3,455 output tokens. The approximate peak-rate cost is $0.038 using DeepSeek's published token prices; actual cost depends on cache billing. Only public OHR PDF page images and related questions/retrieval excerpts were sent to the API. No full private business document was sent.

## Focused verification

`python -m pytest ict-track8/tests/test_m1c_model_budget.py ict-track8/tests/test_visual_source_fallback.py -q` completed with **11 passed** after the final numeric projection extension. `py_compile` passed for the changed client and visual-answer module. Formula safety's existing targeted suite previously completed with 70 passed before this visual change. No full regression suite was run in this round.

## Open work

The visual review can refuse a later attempt, in which case the application keeps the OCR excerpts instead of presenting an unchecked answer. This is safe but means the new path needs further stability evaluation over additional OHR development examples before it can contribute to an aggregate result. The current data inventory does not contain a semantically aligned real SQL corpus and real business documents for the three cross-source workflows, so no cross-source success is claimed here. Complex table-cell semantics, reviewed formula/parameter binding and formula calculation still need real end-to-end examples. The retained split and full acceptance matrix remain open.
