# Real document evaluation freeze

This is a bounded research evaluation, not the full OHR-Bench or OmniDocBench
leaderboard. Original PDFs and reference annotations remain under
`runtime/real_document_upgrade/data`; they are not redistributed in Git.

- OHR-Bench: fixed revision `7f833e3eda9a571a9ea545a8f6d476fa1685033d`,
  12 documents, 6 development / 6 retained, 34 unchanged official questions.
  Historical manifests and diagnostic document references are excluded.
- OmniDocBench: fixed original-slice release
  `f5f559bddf50e36f7f9899d842d0006f13ce8afc` (v1.0, 981 annotated pages in
  this actual release), 24 original PDF slices, 12 development / 12 retained.
  Six source categories, ordered by SHA of input name, two distinct original
  document groups per category per split. Formula/table category presence can
  select a stratum; recognition labels are never passed to the parser.
- A parser worker receives only the input manifest and original PDF bytes.
  Prediction artifacts are persisted before any recognition/QA Gold is opened
  for scoring. The retained split requires an explicit switch and is reserved
  for the frozen implementation.
- PDF SHA, official revision, unchanged QA-row SHA, annotation-file SHA and
  research-use notices are in the manifests. Dataset code licenses do not
  replace original-document rights. Official source notices restrict the PDF
  collections to research, not commercial use.
- Historical OHR 36-question development replay is separately restored under
  `runtime/real_document_upgrade/ohr-dev36`; it is not an unseen test.

Initial parser pilot uses the first three development OmniDocBench slices,
the same original bytes for A/B/C. Expanded development runs use all 12 slices.
OHR uses the full six development documents; a declared page/time budget must
remain visible and failures stay in the denominator. No Gold-directed page
selection or inference routing is allowed.

OHR retrieval uses the unchanged project's document/page/LCS scorer at Top-4.
Evidence-page coverage is distinct from complete evidence text and answer
accuracy. All generation-model task metrics are `not_run` in this experiment.

Recreate:

```bash
/tmp/omni-m1a-venv/bin/python tools/prepare_real_document_evaluation.py --dataset ohr
/tmp/omni-m1a-venv/bin/python tools/prepare_real_document_evaluation.py --dataset odb
```

The preparation tool refuses to replace an existing changed frozen asset.
Original PDF slice availability is why this experiment pins v1.0 rather than
using the current image-only release. It does not claim current-release scores.
