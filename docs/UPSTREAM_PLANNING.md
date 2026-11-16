# Upstream contracts for future ports

These are planning constraints from source inspection of Effect v4 revision
`289a53f7b1fc40049219c6e69022db2fb859c356`, not implemented peffect features or test results.
Current behavior and proofs belong in [INVARIANTS](INVARIANTS.md); implementation order belongs
in [ROADMAP](ROADMAP.md). Source paths below are inside `vendor/effect-v4/packages/effect/src`.

## Schedule and retry

`Schedule.ts:55-62,262-301` initializes an effectful stepper with independent per-run state;
a step consumes time/input and returns output/delay or typed completion. Preserve separate
output, input, error and environment types. `internal/schedule.ts:13-80` drives repeat from
successful values and retry from recoverable errors, preserving the last error at exhaustion.
Use Clock/TestClock for exact attempt/delay traces and cancellation during backoff. Fixed-rate
and spaced delays differ; jitter adds randomness. Consult this pinned v4 API rather than
assuming v3 union/intersection names. Start with deterministic finite schedules.

## Pull, Channel and Stream

`Pull.ts:23-42` distinguishes ordinary failure from Done completion. The accepted Python
Done/Pull boundary is documented in INVARIANTS QP1-QP4. `Channel.ts:259-309` transforms an
upstream pull under a Scope into an output pull; `Stream.ts:130-137` wraps nonempty chunks.
A successful chunk is never an end sentinel. `Channel.ts:8678-8726` serializes pull evaluation.
Start sequentially; prove lazy advancement, take(0), early exit and resource release before
concurrent operators. Concurrent merge owns child scopes, fibers and bounded output buffers
(`Channel.ts:6538-6590`); early downstream termination must release upstream work.

## Config, validation and data

`Config.ts:120-144,486-497,854-884` resolves the active provider at evaluation time. Missing
values can trigger defaults; malformed present input and source failure remain errors.
`ConfigProvider.ts:58-75,278-313` separates provider nodes from decoding. Python validation
adapters may use Pydantic without claiming Effect Schema equivalence: preserve encoded versus
decoded types and decode versus encode service requirements if those capabilities are added.
Schema's AST, transformations and issue trees are a separate large port, not a prerequisite
for ordinary request validation. `Data.ts:727-781` defines yieldable Error subclasses upstream;
Python exception inheritance, typed failure values and generator yieldability are separate
choices. Structural equality requires a compatible hash and stable hashed values.

## Observability and batching

`Tracer.ts:326-411` ends spans with the complete Exit. A future span scope must restore the
parent and end once on success, failure and interruption. Fiber Context owns current span and
logging state; Python contextvars bridge ambient libraries without replacing fiber ownership.
Logger adapters should preserve level filtering, annotations and Cause before exporting.

`RequestResolver.ts:83-107` defines delay, batch key, collection policy and batch completion.
`internal/request.ts:127-151,197-245` removes cancelled entries, cancels empty batches and defects
requests left incomplete by a finished resolver. This is in-flight batching, not a persistent
result cache. Add it for an actual batched backend; do not infer a fixed one-tick batch window.

## Resource families and scope boundaries

Layer memoization coordinates in-flight construction and per-consumer resource ownership;
a plain dictionary of completed values is insufficient. Scope close is one-way; late finalizers
receive the recorded Exit. These implemented contracts are covered by INVARIANTS and Layer/Scope
tests. Future Cache, ScopedRef, RcMap and Pool have distinct ownership: cached values, replacement
generations, borrower leases and bounded resource borrowing respectively. Their tradeoffs and
source anchors are retained in the [ROI synthesis](research/2026-11-15-roi/REPORT.md).

## Historical evidence

The original ten source-reading reports are available in [Git history](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/scouts).
Their draft comparisons describe the old prototype, not current peffect. Raw ROI scout outputs
are likewise available in [Git history](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi); the retained synthesis
reconciles their disagreements. These source notes are leads for future work, not fresh runtime
or compatibility proof.
