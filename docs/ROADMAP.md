# Roadmap: from the kernel to the Effect v4 surface

The rule Effect v4 follows, and this port keeps: only the kernel knows about fibers. In v4,
`internal/core.ts` (768 lines) and `internal/effect.ts` (7,081 lines) hold the run loop, `Scope`
and the Clock/Reference plumbing. Everything else (`Deferred`, `Queue`, `Layer`, `Schedule`, `Stream`)
imports the public Effect API plus at most `core.withFiber`. peffect has the same split:
`core.py`, `fiber.py` and `cause.py` are the kernel, and each piece below is written in peffect
itself.

Enforced, not hoped for: `scripts/check_boundaries.py` fails when code outside the kernel imports a
kernel-internal name, reaches through the `peffect` module, or touches a `Fiber` member outside the
list in rule B3 (rules B1–B3 in `docs/INVARIANTS.md`). `WithFiber` is the one escape hatch.

## Build order: foundations before parallel composition (2026-11-15)

This order supersedes the original tier ordering where dependencies conflict. Build shared
primitives once against pinned upstream contracts; do not recreate them inside consumers.

1. Finish the runtime base: Effect/Cause/Exit, async suspension, fibers, interruption and
   finalization, immediate startup, context restoration, and Scheduler dispatch/tracking.
2. Complete shared service and value contracts: Context identity and equality parity,
   Reference, precise Duration inputs and Clock behavior.
3. Build resource and coordination primitives in dependency order: Scope and acquire/release,
   Ref, Deferred, Latch, Semaphore, then Queue and required concurrent operations. Sequential
   Scope is an intermediate delivery; parallel finalization remains required work.
4. Open independent composition lanes only when each lane's prerequisites are verified:
   Layer/MemoMap/ManagedRuntime; TestClock and its test environment; concurrency operators;
   Schedule/retry/repeat. Stream and Pydantic integration follow their own dependency gates.

A prerequisite is ready when its upstream tests are ported, source-derived additions are
labelled, runtime and type checks pass, independent review is resolved, and it is committed.
Integration tests exercise compositions as they land. Keep one writer for shared kernel and
exports; give parallel writers disjoint modules. A discovered primitive defect is fixed in its
owner, with a regression, then dependent lanes reverify. No fixed-yield scheduler substitutes,
consumer-local substitutes for missing primitives, or silent sequential fallback for parallel.

Current accepted base: immediate startup and Exit capture committed as `eb7a11a`, independently
reviewed (90 tests pass excluding the two future Scope/TestClock files). This is a verified
increment, not a claim that the full runtime or its fidelity gaps are complete.

## Tiers

| tier | piece | v4 source (lines) | peffect module | built from | proof beyond unit tests |
|---|---|---|---|---|---|
| 0 ✓ | kernel: Effect, Exit, Cause, Fiber, fork, masks, gen | internal/core.ts, effect.ts | `core`, `fiber`, `cause` | — | K1–K10 props + `verify-formal-kernel` |
| 1 ✓ | verify skill + agent guide skeleton, boundary lint | — | `.claude/skills/verify-peffect/`, `AGENTS.md`, `scripts/check_boundaries.py` | — | the skill drives `examples/quickstart.py` end to end; boundary probes red/green |
| 2 (impl) | Context, Tag, Reference, `provide` | Context.ts (1,397), References.ts | `context.py` | kernel `update_context` (swap + `RestoreContext` frame in one step) | R-typing typetests: a missing service is a type error; C1–C8 tests |
| 2 ✓ | Clock as a Reference, TestClock | Clock.ts (333), testing/TestClock.ts | `clock.py`, `testing/` | Reference + `Async` | `sleep` under TestClock never touches the loop timer |
| 2 | Scope | Scope.ts (573) | `scope.py` | finalizers run masked, reverse order, get the Exit | prop: every added finalizer runs once, in reverse |
| 3 | Deferred, Latch | Deferred.ts (946), Latch.ts (394) | `deferred.py` | waiter list + `Async` | formal: waiter-list model (no lost wakeup, interrupted waiter leaves the list) |
| 3 ✓ | Ref, SynchronizedRef | Ref.ts, SynchronizedRef.ts | `ref.py`, `synchronized_ref.py` | Sync + Semaphore | upstream ports, generated models, interrupt sweeps |
| 3 | Semaphore | Semaphore.ts (696) | `semaphore.py` | waiter list + `Async` | prop: permits never negative, never above max, under random interrupts |
| 3 | Queue (bounded, dropping, sliding) | Queue.ts (2,175) | `queue.py` | two waiter lists | prop: no item lost or duplicated when takers are interrupted |
| 3 (part ✓) | `all_`, `for_each(concurrency=)` ✓, `race` ✓, `timeout` ✓, FiberSet | effect.ts | `concurrent.py` | fork + observers + Semaphore + Clock | prop: losers interrupted and awaited before the result (K8 for combinators) |
| 4 | Layer, MemoMap, ManagedRuntime | Layer.ts (2,856), ManagedRuntime.ts (474) | `layer.py`, `runtime.py` | Context + Scope + Deferred | a shared layer builds once; release in reverse; typetests on `Layer[ROut, E, RIn]` |
| 5 | Duration, Schedule, `retry`, `repeat` | Schedule.ts (1,583) | `schedule.py` | Clock + `sleep` | TestClock: recorded delays match the schedule exactly |
| 6 | TaggedError, Codec over pydantic, Config | Schema.ts (19,389), Config.ts (1,630) | `codec.py`, `config.py` | pydantic `TypeAdapter`; issues from `ValidationError` | round-trip props: `decode(encode(x)) == x` |
| 7 | Pull, Stream, Sink | Pull.ts (408), Stream.ts (12,299), Channel.ts (9,253) | `stream.py` | Pull = an effect that fails with Done; Queue + Scope | prop: early exit runs every upstream finalizer |
| 8 | Logger, Tracer spans, Metric | Logger.ts, Tracer.ts, Metric.ts | `observe.py` | References + fiber annotations | spans nest by fiber tree |

Each tier ships a lesson and a diagram in `docs/lessons/`, its rows in `docs/INVARIANTS.md`, and
an AGENTS.md section with the idiom and the mistakes it prevents.

## Progress

- Tier 0: done.
- Tier 1 (2026-11-15): accepted after independent Sol 6.1 verification and structure-review; two checker findings corrected and regression-tested (31 tests). `examples/quickstart.py`,
  `verify-peffect`, `AGENTS.md`, the boundary lint with its probes (B1–B3), and lesson 03.
  Marked ✓ once the independent review passes.
- Tier 2a, Context (2026-11-15): independently reviewed; known parity gaps below. `context.py`, kernel
  `update_context` + `RestoreContext`, read-only `Fiber.context`, `examples/context.py` in
  `verify-peffect`, rows C1–C8, lesson 04.
- Tier 2a, upstream test port (2026-11-15): batches A (Context) and B (kernel finalization) from
  `docs/UPSTREAM_TESTS.md` pass: `Context.empty`, Context equality (C9), `on_error`,
  `on_interrupt`, and `on_exit` keeping the body's failure with defect election (K9). Batch C
  (Clock) is ported; TestClock (D) and Scope (E) tests are drafted, red only on their missing API.
- Tier 2b, Clock (2026-11-15): independently reviewed; `sleep` takes `Duration.Input` (Duration lane). Leaf `clock.py` (`Clock` Reference,
  `ClockService` Protocol, `clock_with`, time reads, `sleep` moved out of `core.py`), batch C and
  the source-derived sleep tests green, `examples/clock.py` in `verify-peffect`, rows CL1–CL5,
  lesson 05. TestClock is the remaining half of this roadmap row.
- Kernel prerequisites for Scope/TestClock (2026-11-15): `fork`/`fork_daemon(start_immediately=True)`
  (v4 forkUnsafe immediate start) and `exit_` (v4 `Effect.exit`), ported with their upstream
  tests in `tests/test_fiber_start_upstream.py`; formal model updated (nested immediate drive).
- Tier 2, Scope, sequential (2026-11-15): `scope.py` (`Scope` tag and module functions,
  `Closeable`, `ScopeService`, `scope`, `scoped`) over public combinators only; batch E green,
  rows S1–S8, typetests. Pending: `Scope.use`, remaining batch F, lesson 06. Unmodeled by the
  formal kernel.
- Resource unit (2026-11-15): `resources.py` provides default masked `acquire_release`,
  `acquire_use_release`, and `add_finalizer` over public primitives. All 22 upstream/source-derived
  cases pass, with resource/error requirement typetests. Independent Sol review, structure review
  and structural re-search passed; 132 tests pass excluding TestClock. Optional interruptible
  acquisition and remaining Scope effects are incomplete; no
  Layer, Deferred or TestClock implementation is included.

- Deferred prerequisite unit (2026-11-15): `deferred.py` stores completion Effects and waiter
  callbacks, with direct synchronous completion and interruption-safe unregistering. Ten tests
  and A/E/environment-free completion typetests pass; independent Sol and structural review
  passed, with 142 tests green excluding TestClock. Public `fiber_id` reads the executing fiber
  without widening B3. Broader Deferred
  operations and TestClock remain incomplete. Latch and bounded traversal are now implemented.
  Deferred is not formally modeled.

- Fiber bulk prerequisite unit (2026-11-15): public `await_all` / `interrupt_all` and explicit
  `fork` / `fork_daemon(uninterruptible="inherit")`; defaults remain interruptible. Thirteen tests,
  type fixtures and targeted mutations pass; the kernel model now covers inherited startup.
  Independent Sol review and recheck passed. The iterator-error regression preserves target
  publication and fails only the waiter; 155 tests pass excluding TestClock. Boolean mask modes and
  concurrent traversal remain incomplete; consumers must compose these public primitives.

- Parallel Scope close (2026-11-15): independently verified by Sol 6.1. Several finalizers
  start newest first in mask-inheriting daemon fibers, then `await_all` (`internal/effect.ts:4077-4101`);
  batch G's four Scope.test.ts ports pass (:7 adapted with Deferred gates, no TestClock). The
  closing-fiber key skip waits on forkIn (batch F). Unmodeled by the formal kernel.

- Traversal (2026-11-15): public `for_each(items, f, *, concurrency=1)` in `fiber.py`, a port of
  forEach and iterateConcurrentImpl (`internal/effect.ts:5030-5112, :5157-5320`), kept in the
  kernel because the worker state machine needs observers and interrupts that B3 keeps private.
  12 Effect.test.ts ports and 5 labelled source-derived tests (21 cases) pass. No `discard` and no ambient
  concurrency yet. Independently verified by Sol 6.1 after ordered-worker and primitive cancellation
  corrections; 185 tests pass excluding TestClock. Not formally modeled.

- Racing and timeouts (2026-11-15): implemented; independent review completed,
  iterator-close and large-duration formatting findings corrected with regressions.
  - `fiber.py`: `race`, `race_first`, `race_all`, `race_all_first` (raceAll/raceAllFirst,
    `internal/effect.ts:1688-1840`), one shared `_race` state machine beside `for_each`.
  - `timeout.py`: `timeout`, `timeout_option`, `timeout_or_else`, `TimeoutError` (:3929-4007, :6499),
    built from `race_first` and `sleep`. `duration.py`: `format` (Duration.ts:1789-1895).
  - 35 race/timeout cases (21 Effect.test.ts ports, source cases, a vendor-replayed property) and
    44 format cases pass; typetests `race_types`/`timeout_types`, should_fail 106-113. Rows RA1-RA9.
  - Data-first only. Not formally modeled: the race reuses kernel primitives without a new
    transition; its ordering is proven by runtime tests and mutations.

- Layer (2026-11-15): implemented and independently verified by Sol 6.1.
  - `layer.py`: `Layer[ROut, E, RIn]` (token-only constructor, `Final` builder), `MemoMap`,
    `CurrentMemoMap` and `provide_layer`.
  - Constructors: `effect`, `succeed`, `effect_context`, `effect_discard`, `from_build`.
  - Composition: `merge_all` (typed for 1–4 layers), and data-last `merge` and `provide`.
  - Builds: `build`, `build_with_scope`, `build_with_memo_map`; memo maps through
    `make_memo_map(_unsafe)` and `fork_memo_map(_unsafe)`.
  - 11 Layer.test.ts ports and 5 source-derived cases pass (21 test cases), along with
    typetests (should_fail 54–70) and three mutations.
  - Not yet ported: `Layer.fresh`, suspend, recovery and tracing.
  - Open parity gap: Context keys are classes, not strings.
  - Not formally modeled.

- Scheduler (2026-11-15): implemented and independently reviewed; boundary and publication-documentation corrections accepted. A TestClock prerequisite.
  - `scheduler.py`: `Scheduler`, `MaxOpsBeforeYield` and `PreventSchedulerYield` References,
    `MixedScheduler` (priority buckets, FIFO within one, one `loop.call_soon` per batch),
    `SchedulerDispatcher`; `with_dispatcher` and `DEFAULT_SCHEDULER` from the kernel.
  - Kernel: resume, interrupt and root start evaluate the woken fiber synchronously, as upstream;
    yields, automatic yields and scheduled forks go through the fiber's dispatcher.
  - Scheduler.test.ts :42-:159 ported (runSyncExit and microtask fallback not), with source-derived
    resume/dispatcher cases; rows SC1–SC7; nine targeted mutations go red.
  - Formally modeled, bounded: synchronous wake and interrupt, nested drives, the queued yield
    token (`verify-formal-kernel`, nesting ≤ 2). Not a proof at arbitrary depth; priorities,
    batches and the op budget are tested only. Wake chains recurse (SC7, measured limit).

- TestClock, Console, Logger, fork_scoped (2026-11-15): implemented against red ccef1bb; awaiting
  independent review. No kernel module changed.
  - `console.py` (Console Reference, five methods), `logger.py` (header-only default logger,
    `CurrentLoggers`, `MinimumLogLevel`, `LogToStderr`, `log_with_level`, `log_warning`),
    `testing/` (`TestConsole`, `TestClock` with the tracking scheduler and live-console warnings),
    `scope.fork_scoped`. `current_time_millis` is now a float.
  - All 25 TestClock.test.ts cases, S1–S14, forkScoped :3083-:3116, and the console/logger
    source cases pass; typetests should_fail 114–118. Rows TC1–TC6, CO1, LG1, FS1; lesson 06.
  - Not formally modeled. Logger spans, annotations and the pretty/JSON loggers are not ported.

## Order and why

- Context/Clock/Scope come before concurrency: `timeout` needs Clock (else it is untestable
  without real time), and FiberSet/Queue shutdown need Scope. In v4 all three live inside
  `internal/effect.ts`, next to the kernel.
- Deferred is the base of Layer's MemoMap (`Layer.ts:253-275`), so tier 3 precedes tier 4.
- Schedule only needs Clock; it waits until tier 5 so retry examples can use real services.
- Stream needs Queue, Scope and Schedule, so it comes last but one.

## Not ported

- Data modules Python already has: Array, Chunk, HashMap, HashSet, Equal/Hash
  (`__eq__`/`__hash__`), Order, Struct, Record.
- Schema's AST, parser and JSON-schema machinery (≈33k lines): pydantic owns them.
- Platform packages (http, sql, cluster, workflow, ai, rpc) and STM (`Tx*`). Revisit after tier 7.

## Open decisions (none block tiers 1–5)

| decision | default | blocks |
|---|---|---|
| Stream: port Channel, or build Stream on Pull + Queue directly | Pull + Queue; add Channel only if operators need it | tier 7 |
| Codec: pydantic-only, or an Effect-style transformation layer on top | pydantic + optional effectful step | tier 6 |
| Runtimes other than asyncio (trio via anyio) | asyncio only | none yet |

## Fidelity gate (2026-11-15)

User requires a faithful port: documentation of a deviation is not acceptance of parity.
Known gaps to reconcile before calling the port faithful (string-key service identity closed
2026-11-15, INVARIANTS C10): incomplete Equal protocols (Clock inputs now follow `Duration.Input` normalization and nanosecond
precision). Preserve observable upstream behavior; Python syntax/platform
adaptations must be distinguished from semantic omissions.

TestClock tests are drafted, not accepted as a complete contract yet. Port Scope and scheduler
tracking prerequisites before TestClock implementation. Keep upstream make's Scope requirement;
never replace scheduler settling with a fixed yield count. Upstream-required dependencies may
move ahead of their original roadmap tier rather than being recreated inside TestClock.

## Application composition acceptance (2026-11-15)

The executable comparison in `examples/parity` runs 16 scenarios on real pinned Effect v4
and peffect: service sharing, typed error propagation/recovery, resource cleanup, replacement,
a shared checkout/risk dependency diamond, bounded parallel orders and batch cancellation.
All observed outcomes and ordered events match; only validation-library names and diagnostic
text are normalized. Nine negative typing scenarios are checked in both languages, with the
Python nonmatching-catch limitation documented explicitly.

Independent review caught a lossy TS error reporter; its regression now rejects extra causes.
The reference-quality audit also caught global trace contamination; per-run EventLog provision
and a concurrent-run regression fix it using existing APIs. Application and fixture modules
are separated. See `docs/APPLICATION_PATTERN_AUDIT.md` for findings and why no new broad skill
was added. No peffect production API was required for this application unit.

The 16 missing-TestClock draft tests remain deferred. ManagedRuntime's async slice is now
implemented and independently reviewed: 54 focused tests pass, including integrated typing
and boundaries. Its disposal contracts and proof limits are recorded in INVARIANTS MR1–MR9.
This does not extend the earlier application acceptance to untested runtime scenarios.

Queue prerequisite unit: `suspend_exit` folds terminal delivery into the thunk's step;
`Done`, `done` and the genuine minimal `pull` completion API provide typed end-of-input.
The kernel protocol and cause representation are unchanged. Invariants QP1-QP4 and the
source-anchored tests distinguish atomic delivery from later cancellation and preserve real
failures merged with Done. Queue strategies/lifecycle/batches and remaining Pull operators
are still pending; this prerequisite unit does not mark Queue or Stream complete.
