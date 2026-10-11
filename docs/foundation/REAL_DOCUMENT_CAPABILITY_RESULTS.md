# Real document capability results — 2026-10-11

## Scope

This is one public OHR-Bench **development** question, selected because its scanned PDF has a clear, independently annotated answer. It is not a benchmark-wide score or a retained-set result.

| Run | Production path | Answer | Normalized EM | Token F1 |
|---|---|---|---:|---:|
| Before visual source route | OmniAgent planner → KnowledgeStore OCR retrieval → grounded generation fallback | OCR page excerpts; answer not extracted | 0 | 0.019802 |
| Visual route, first integrated run | OmniAgent → OCR retrieval → original page image → selection + independent review | `$ 343,215,345` plus row label | 0 | 0.5 |
| Visual route with scalar money projection | Same complete OmniAgent path | `$ 343,215,345` | 1 | 1 |
| Integrated repeat | Same complete OmniAgent path | Review rejected; OCR excerpts retained | 0 | 0.019802 |
| Scoped original-page replay | Production KnowledgeStore visual fallback on the same registered PDF | `$ 343,215,345` | 1 | 1 |
| OHR Highways & Streets CIP table question | Complete OmniAgent route and review; saved reviewed literals projected by the server | `198,687,347` | 1 | 1 |
| OHR Events Center bond debt-service question | Complete OmniAgent route; independent visual review rejected | OCR excerpts retained | 0 | 0.020619 |

Gold is `$343,215,345`. Scores use the project OHR answer normalizer. The first integrated visual answer included the source row label, so its exact score stayed 0 despite a correct amount. The next code revision kept the label and Total header in the citations while projecting a single reviewed currency literal as the answer. The reviewer later refused one repeat; this safe fallback is included rather than excluded from the record.

## Source and replay

- OHR case: `a9189b43-d6c1-4fe5-80fa-e92a083ae2fb`.
- Original PDF SHA-256: `0dfec99b5368a44a20e9e8ca54f2898adab27d9c878929849615664e425fcfd0`.
- Page: 1. Evidence includes the literal row label `Sales/Use Tax`, value `$ 343,215,345`, and column header `Total`.
- The successful replay artifact stores the full visual proof, independent review, source/render/question hashes and three PDF display-point bounding boxes. A fresh replay confirmed the original SHA, rendered image SHA, question SHA, all three citation regions, and all eight review fields.
- The value came from the visible original table. No OCR correction or arithmetic was used; `calculator_input_eligible` remains false.

## Model and resource use

DeepSeek `deepseek-flash` was read from `runtime/private/m1c-model.env`; the private file was not copied or logged. Across all included experiments, 20/30 reserved calls completed: 76,642 input tokens and 2,626 output tokens. Using peak published rates of $0.30/M input and $1.20/M output gives an upper estimate near **$0.026** before tax; cached prompt tokens may cost less. The one-page public source image was the only image sent.

## Limits

The results exercise three distinct questions on one real scanned page. Two had model-selected values and independently approved visual evidence; the CIP answer needed a follow-up deterministic projection from its saved approved literals to remove the cited row label from the display answer, with no additional model call. The debt-service run was refused. A repeat Sales/Use Tax run was also refused, so the route is not yet stable enough to claim aggregate accuracy. No retained or blind OHR/OmniDocBench set was scored after this change. This run does not establish formula parameter binding or execution, multi-document completeness, SQL→document joins, or completion of competition tasks 6–9. See the complete execution log at [`REAL_DOCUMENT_END_TO_END.md`](REAL_DOCUMENT_END_TO_END.md).
