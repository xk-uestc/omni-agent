# F2 Planner Protocol and Stream Reliability

## Model planning protocol

The M1-C formal run preserved the original model outputs and reported protocol failures in all four conditions: A=2, B=4, C=3, D=3. The observed forms included invalid JSON escaping and DSML mixed into output. The run did not establish a model-accuracy gain, and no real model was called for this F2 checkpoint.

The Responses planner requests strict JSON Schema output and accepts only completed Responses containing JSON objects. It does not extract SQL, plans, or task graphs from arbitrary text. The existing planner permits one repair only for structural handle/output errors; it validates the repaired full plan against the same question, source intent, schema, and safety checks. A second invalid plan stops, and semantic omissions or provider/protocol failures do not trigger structural repair. Fixed local-response tests confirm one structural repair can retain an explicit filter, while semantic omissions and repeated structural failure do not get promoted.

Invalid JSON audit now records its character count, SHA-256, and parser line/column/offset. It deliberately omits raw model text; the original M1-C request/response artifacts remain the raw audit record. This lets operators match a failure to the retained artifact without copying possibly sensitive model content into ordinary logs. A DSML-in-JSON fixture remains rejected and records only the safe fingerprint.

## SSE timeout and worker ownership

Stream workers acquire a bounded process-local semaphore slot before launch, retain it when the client-facing deadline expires, and release it in the worker's `finally` after completion. The agent-query stream now reports timeout phase/category, `worker_cancelled: false`, and whether the worker was still running or had completed. If thread startup fails, it returns the acquired slot and reports a 503. Existing Omni query timeout already reported that the worker was not cancelled.

The local fault tests exercise the actual endpoint worker and SSE generator with a held worker, verify the timeout does not free its slot early, then release it and verify eventual slot recovery. They also cover thread-start failure. The configured concurrency limit bounds active background workers (default 16; configuration range 1–256).

There is no safe forced cancellation for an already-running Python thread or underlying provider/tool call. Client disconnect likewise does not cancel it; the worker retains its slot until return. This checkpoint does not add a durable task-status endpoint or cross-request deduplication for clients that retry after a timeout. It therefore claims truthful timeout state and bounded active workers/resource release, not hard cancellation or exactly-once execution.

## Verification

The focused offline suite covered Responses provider/audit, strict model-plan parsing and repair behavior, and stream faults: **50 passed, 2 existing FastAPI deprecation warnings**. No credential, external API, or paid model was used. This is protocol/runtime regression evidence, not real-model correctness evidence.
