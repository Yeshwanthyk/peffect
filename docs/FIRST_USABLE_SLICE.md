# First usable slice

Priority set by the user on 2026-11-15: the smallest faithful path through the important
pieces, maximizing practical use before broad API coverage. This narrows the first delivery
milestone; the remaining roadmap stays as backlog, not deleted scope.

## Executable delivery

`uv run python -m examples.parity.compare` now runs both real implementations and compares
16 scenarios, including a deeper checkout application with a shared Layer diamond, three domain
errors, nested resources, selective recovery, bounded concurrent orders and cancellation.
Source and commands: `examples/parity/README.md`. Python's Pydantic adapter stays at the
application boundary; no Schema port or Pydantic dependency in the kernel is claimed.

Runtime parity is scoped to these scenarios. The README records the validation and catch-tag
typing differences, and the positive/negative fixtures check the supported type guarantees.
ManagedRuntime and TestClock remain separate work.

## Acceptance scenario

An existing Pydantic model validates a request into a typed effect. The program consumes typed
service tags; Layers construct and share their dependencies, including a scoped resource.
The program handles a declared domain error. Success, failure and cancellation all release
resources. A test substitutes a service implementation without changing application code.
Pyright rejects wrong implementations and running a program with missing dependencies.

## Executable EffectJS comparison

The minimum is all four: Scope/acquire-release, Tags/Layer, Pydantic/typed errors, and an
end-to-end application. Acceptance requires a real TypeScript program running pinned Effect
v4 `289a53f7` and a Python program exercising the same application scenarios.

Both runners emit machine-readable outcomes and lifecycle events for:
- valid input and a successful service result;
- invalid input rejected before resource acquisition;
- a declared domain failure and its recovery;
- two consumers sharing one Layer resource (one acquire and one release);
- replacing the service in a test without changing application logic;
- failure and cancellation after acquisition, with cleanup completed before termination.

Compare result values, error categories, construction/release counts, and causally ordered
lifecycle events. Normalize only explicit cross-language representations such as generated
fiber ids and exception rendering. Do not normalize away behavior differences. Choose equivalent
validation constraints; Pydantic and Effect Schema retain their own diagnostic formats, and this
comparison does not claim complete schema equivalence. Separately compile positive and negative
typing fixtures in TypeScript and Python for missing dependencies and wrong implementations.

Run the TypeScript reference first; its actual output supplies the comparison baseline. A test
must report a mismatch when an observable outcome or cleanup event differs. Until both runners
exist and match, this acceptance gate is incomplete.

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
