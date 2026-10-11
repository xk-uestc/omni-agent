# F2 Explicit Aggregation Semantics

## Finding

`MetricCatalog.expand()` correctly selected a business metric such as revenue, and `SingleTablePlanner` recognized an explicit aggregation such as average. `MetricCatalog.apply()` then replaced the typed planner's function with the catalog default (`SUM`). This could change a user's explicit `AVG` request before SQL compilation and source-intent validation. The required-intent verifier behaved correctly for the plan it received; the lost intent originated earlier, at catalog application.

## Change

For a single catalog source metric, the engine now extracts aggregation cues only from metric-linked clauses in the original question. A single explicit cue may override the configured default while retaining the catalog's physical table, column, time binding, unit, and source identity. Conflicting explicit functions continue through the existing ambiguity clarification. The chosen function is recorded in `semantic_audit.explicit_aggregation_override` with its metric ID, configured and requested functions, and typed-cue provenance.

The verified-rule fast path accepts this override only when the audit identifies the same catalog metric, the configured function still matches the catalog, the requested function matches the compiled metric, and the label is consistent with that function. SQL still goes through `MetricCompiler` and read-only execution validation. Dynamic source binding independently confirms the original scope's function. An old memory alias that asks for `SUM` cannot silently satisfy a current explicit `AVG` request; that case clarifies without SQL or rows.

## Verification

SQLite oracle comparisons cover grouped `AVG`, `COUNT(DISTINCT ...)`, SQL NULL handling, and grouped grain. A dynamic Excel source binding test confirms that an explicitly averaged request retains `AVG` through catalog expansion and source validation. A memory adapter test confirms an explicit average cannot inherit a historical SUM binding.

The focused regression command covered catalog binding, metric compilation, dynamic source binding, memory adapter, and conversational SQL edits: **217 passed**. These are local correctness tests, not a public benchmark or a full-project acceptance result. Frozen F1 inputs, golds, scorers, and provenance contracts were not changed.
