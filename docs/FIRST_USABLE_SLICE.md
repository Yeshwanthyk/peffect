# First usable slice

Priority set by the user on 2026-11-15: the smallest faithful path through the important
pieces, maximizing practical use before broad API coverage. This narrows the first delivery
milestone; the remaining roadmap stays as backlog, not deleted scope.

## Acceptance scenario

An existing Pydantic model validates a request into a typed effect. The program consumes typed
service tags; Layers construct and share their dependencies, including a scoped resource.
The program handles a declared domain error. Success, failure and cancellation all release
resources. A test substitutes a service implementation without changing application code.
Pyright rejects wrong implementations and running a program with missing dependencies.

## Delivery order

1. Review and integrate the sequential Scope implementation already in progress.
2. Port acquire/release tests and implement resource-safe acquisition using Scope. Correct
   Context identity behavior needed by service composition; keep other known parity gaps
   explicit and fix them when they affect an exposed contract.
3. Port the real prerequisite subset for Layer/MemoMap (including Deferred and any required
   scope/concurrency operations). Follow pinned upstream dependencies; do not invent a
   simplified memoizer or recreate a missing primitive inside Layer.
4. Build two disjoint composition lanes once prerequisites are accepted: Layer/runtime wiring,
   and the Pydantic validation/typed-error adapter. Use upstream contracts for Effect APIs;
   label the Pydantic adapter as Python integration rather than claiming Schema equivalence.
5. Integrate the acceptance scenario as a runnable example and end-to-end test. Verify shared
   construction occurs once, overrides work, and cleanup runs once on all exit paths.

## After the first usable slice

Add bounded concurrency, precise Duration and schedule/retry/timeout support, completing their
required primitives and time tests first. Full TestClock scheduler tracking, Queue/Stream,
observability and broad API coverage remain planned. Pull any of them forward when an actual
upstream dependency requires it; otherwise they do not block the first usable slice.

## Delivery rules

Port relevant tests before implementation; preserve their assertions. Source-derived additions
are labelled separately. Every exposed supported behavior must be faithful; incomplete areas
are disclosed, never simulated. Review once per coherent unit with Sol and structure-review,
fix meaningful findings, and commit. Keep docs proportional; prioritize runnable examples and
integration evidence over separate lessons for each small API. Parallel writers own disjoint
modules; the coordinator owns shared exports and integration.
