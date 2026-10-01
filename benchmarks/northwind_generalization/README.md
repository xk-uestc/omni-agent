# Cross-schema development evaluation

This is a new database/schema evaluation, not an official NL2SQL benchmark.
The pinned public database is a community Northwind port/extension. The 16
Chinese questions are synthetic development questions covering aggregates,
filters, nulls, FK joins, dates, grouping, distinct counts, dense-rank ties and
scalar subquery thresholds. They deliberately identify tables/columns; scores
must not be represented as unassisted natural-language user accuracy.

Original database, upstream MIT license and README are under
`D:/ICT8-OfficialDatasets/northwind`. URLs, revision and SHA-256 hashes are in
`ASSET_MANIFEST.json`. Do not add the database BLOB assets to source Git.

Questions and reference SQL were frozen before program/API testing.
Only `question` enters the existing engine. Reference SQL executes separately
for scoring after the engine returns; no gold plans, aliases, metric catalogue
or results are supplied to the model. All 16 cases remain in the denominator.

Verify only, without calling a model or running the program:

```powershell
python tools/evaluate_northwind_generalization.py --preview
```

After both compared program versions are frozen, evaluate each with the same
unchanged input and a separate new output file:

```powershell
python tools/evaluate_northwind_generalization.py --model --output docs/NORTHWIND_BASELINE.json
python tools/evaluate_northwind_generalization.py --model --output docs/NORTHWIND_OPTIMIZED.json
```

Only local excluded `runtime/model_config.json` is loaded through
`tools/model_runtime.py`; the evaluator requires `gpt-6-luna / medium`.
It checks source hashes before/after and reports every refusal/exception.
Result labels/column order are not scored, but cell types, row association,
duplicates, numeric tolerance and explicit ranking order are checked. This
measures execution agreement, not comprehensive semantic correctness.
