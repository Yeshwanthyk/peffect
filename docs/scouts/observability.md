# Effect v4 observability and requests

Scope: the vendored `packages/effect/src` tree was read only; these notes describe source behavior, not assumptions from docs. References are source-relative `path:line`.

## Tracing

- `Tracer.Span` exposes immutable identity/relationship/state (`name`, IDs, parent, annotations, attributes, links, sampled, kind, status) plus `end(endTime, exit)`, `attribute`, `event`, and `addLinks`: `Tracer.ts:394-411`. Ended status carries end time and the full `Exit`, so success and every failure Cause remain distinguishable to exporters: `Tracer.ts:326-337, 361-368`.
- The active span is effectively fiber-local Context data: a span is provided as `Tracer.ParentSpan`; `makeFiberContextCache` resolves that reference into `cache.span`, and `fiber.currentSpanLocal` exposes only native `Span` values: `internal/effect.ts:812-828, 806-809`. `Tracer.ParentSpan` is a fiber-cached Context service: `Tracer.ts:178`.
- `withSpan` makes a span from the current fiber, registers an exit observer, and runs the effect with that span as parent/current span. `endSpan` is idempotent and calls `span.end(time, exit)`; timing may be disabled, in which case time is zero: `internal/effect.ts:6242-6250, 6262-6275, 6308-6340`. The scoped form installs an exit finalizer: `internal/effect.ts:6177-6210`.
- `annotateCurrentSpan` mutates attributes on the current, not-yet-ended span; `annotateSpans` instead updates a Context reference so annotations can be applied to spans created downstream: `internal/effect.ts:6343-6384`.
- `Effect.fn` wraps the generator/effect result in a span named after the function (unless untraced); that span becomes parent for the body. It also installs a stack-frame chain for definition/call-site tracing: `internal/effect.ts:1374-1422`. This provides automatic function spans while preserving pipeable middleware around the returned effect.

## Logging

- A `Logger<Message, Output>` is a `log(options)` transformation. Each event carries message, `LogLevel`, Cause, fiber, and Date: `Logger.ts:72-105`. `CurrentLoggers` is a Context reference with default and tracer logger; `Logger.layer` replaces or merges the active set: `Logger.ts:133-167, 170-205`.
- Concrete levels are `Fatal`, `Error`, `Warn`, `Info`, `Debug`, and `Trace`; `All` and `None` are threshold sentinels: `LogLevel.ts:28-36, 69-90`. The internal logger reads the fiber's configured level and minimum, filters, then calls each logger with the assembled event: `internal/effect.ts:6725-6765`. Public `Effect.log*` helpers map to these severities: `Effect.ts:14971-15136`.
- Per-fiber References supply `CurrentLogAnnotations`, `CurrentLogSpans`, current level, and minimum level; annotations and spans are read by the structured formatter: `References.ts:193-205, 300-313, 234-245, 361-372; Logger.ts:609-630`. Structured output includes level, fiber ID, timestamp, message, cause, annotations, and named elapsed span values: `Logger.ts:609-630, 632-648`.
- The current span relationship is also available to the tracer logger; logger options include the fiber, and formatters read refs through `fiber.getRef`: `Logger.ts:72-105, 621-630`. (Exact default tracer-logger event conversion is in `Logger.ts:212-263`.)

## Requests and resolver batching

- A `Request<A,E,R>` is a typed description of work/result/error/service needs, completed by an associated resolver: `Request.ts:2-8, 27-29, 53`. Its entries carry request, completion callback, context and interruptibility (internal representation): `internal/request.ts:35-68`.
- Resolver contract: `delay` effect; `batchKey(entry)` groups compatible work; optional `preCheck` can short-circuit; `collectWhile(entries)` decides whether to keep gathering or dispatch early; `runAll(nonEmptyEntries,key)` completes the batch: `RequestResolver.ts:83-107`.
- Pending batches are shared by resolver identity via `WeakMap`, with separate maps by batch key. A resolver may opt keys into structural equality; otherwise ordinary identity-keyed `Map` applies: `internal/request.ts:73-95, 98-110, 167-195`.
- First request starts a delay fiber on the submitting fiber's scheduler. Entries arriving before delay completion join the keyed batch; if `collectWhile` says stop, the delay fiber is interrupted and `runAll` is forked immediately. Therefore batching is not inherently “one scheduler tick”: the resolver chooses the delay effect (and can choose zero delay), with scheduler controlling execution: `internal/request.ts:127-137, 197-210`; resolver controls delay/collection: `RequestResolver.ts:83-102`.
- Batch `run` ensures every still-pending entry is completed; a resolver that exits successfully without completing one yields a defect for that request. Cancellation removes an entry, and an empty batch is removed / its delay fiber interrupted: `internal/request.ts:135-151, 213-245`.
- This source path implements in-flight coalescing, not a durable completed-result cache: the batch map is deleted when dispatched, and completed entries are not retained. Any result cache must be a separate resolver/request-cache policy; the inspected resolver contract has no completed-value cache field: `internal/request.ts:242-245; RequestResolver.ts:83-108`.

## Kernel hooks and Python sketch

**Observed kernel hooks.** Effect relies on fiber-owned Context plus fast derived fiber cache for current span, tracer, logger set, log levels, scheduler, and related refs: `internal/effect.ts:812-828`. Log annotations/spans are Context References read from the active fiber: `Logger.ts:621-630; References.ts:193-205, 300-313`. Span lifetime needs an exit hook/finalizer receiving `Exit`; request batching needs a scheduler-backed delay, fork, interruption, per-request completion and cleanup: `internal/effect.ts:6242-6275; internal/request.ts:127-151, 197-245`.

**Minimal Python design (inference from observed contracts).**

1. Add immutable `Context` and fiber-local `Ref[T]` values to the runtime; child fibers inherit Context snapshots, while updates are scoped to the child. Keep current span, logger tuple, log threshold, annotations, and named log spans there. A `contextvars.ContextVar` can bridge ambient Python libraries at task switches, but should not be the authoritative store if the custom Fiber VM owns execution: asyncio tasks and custom fibers are distinct ownership models.
2. Define `Span` protocol with IDs, parent, attributes/events/links, status and `end(exit)`; `with_span` uses `WithFiber` + `OnExit`, restoring parent context and ending exactly once on success, typed failure, defect, or interruption. Map exporter boundary to OpenTelemetry `Tracer`/`Span`; adapt full `Cause/Exit` to span status plus exception/event attributes rather than throwing away structured failure.
3. Define `Logger.log(LogEvent)` and `LogEvent` (message, severity, cause, fiber id, timestamp, annotations, current span identifiers/named durations). Install logger implementations in Context; an stdlib `logging.Handler` or structlog processor is an adapter, with Effect filtering/annotation semantics retained before handing off.
4. Implement request entries as typed request + one-shot completion future. A resolver owns a keyed in-flight batch map; first entry schedules configurable async delay, subsequent fibers join by resolver/key, and dispatch cancels the delay. Use asyncio scheduling only as the bridge; cancellation removes only that waiter, and only empty batches cancel dispatch. Add completed cache explicitly as a separate optional policy with clear key/lifetime/invalidation semantics.

**Gaps / limits.** The inspected `RequestResolver` contract has batching but no persistent cache contract; cache semantics must be specified in the Python design. This note did not inspect all tests or every adapter/formatter; exact compatibility details beyond cited code remain open.
