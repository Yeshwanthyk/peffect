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
| 2 | Clock as a Reference, TestClock | Clock.ts (333) | `clock.py`, `testing.py` | Reference + `Async` | `sleep` under TestClock never touches the loop timer |
| 2 | Scope | Scope.ts (573) | `scope.py` | finalizers run masked, reverse order, get the Exit | prop: every added finalizer runs once, in reverse |
| 3 | Deferred, Latch | Deferred.ts (946), Latch.ts (394) | `deferred.py` | waiter list + `Async` | formal: waiter-list model (no lost wakeup, interrupted waiter leaves the list) |
| 3 | Ref, SynchronizedRef | Ref.ts (765) | `ref.py` | plain cell; Sync | — |
| 3 | Semaphore | Semaphore.ts (696) | `semaphore.py` | waiter list + `Async` | prop: permits never negative, never above max, under random interrupts |
| 3 | Queue (bounded, dropping, sliding) | Queue.ts (2,175) | `queue.py` | two waiter lists | prop: no item lost or duplicated when takers are interrupted |
| 3 | `all_`, `for_each(concurrency=)`, `race`, `timeout`, FiberSet | effect.ts | `concurrent.py` | fork + observers + Semaphore + Clock | prop: losers interrupted and awaited before the result (K8 for combinators) |
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
- Tier 2b, Clock (2026-11-15): independently reviewed; seconds-only API remains incomplete. Leaf `clock.py` (`Clock` Reference,
  `ClockService` Protocol, `clock_with`, time reads, `sleep` moved out of `core.py`), batch C and
  the source-derived sleep tests green, `examples/clock.py` in `verify-peffect`, rows CL1–CL5,
  lesson 05. TestClock is the remaining half of this roadmap row.
- Kernel prerequisites for Scope/TestClock (2026-11-15): `fork`/`fork_daemon(start_immediately=True)`
  (v4 forkUnsafe immediate start) and `exit_` (v4 `Effect.exit`), ported with their upstream
  tests in `tests/test_fiber_start_upstream.py`; formal model updated (nested immediate drive).

## Order and why

- Context/Clock/Scope come before concurrency: `timeout` needs Clock (else it is untestable
  without real time), and FiberSet/Queue shutdown need Scope. In v4 all three live inside
  `internal/effect.ts`, next to the kernel.
- Deferred is the base of Layer's MemoMap (`Layer.ts:253-275`), so tier 3 precedes tier 4.
- Schedule only needs Clock; it waits until tier 5 so retry examples can use real services.
- Stream needs Queue, Scope and Schedule, so it comes last but one.

## Not ported

- Data modules Python already has: Array, Chunk, HashMap, HashSet, Option (`None`), Equal/Hash
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
Known gaps to reconcile before calling the port faithful: class-only service identity versus
upstream string keys, incomplete Equal protocols, and seconds-only Clock inputs versus Duration
normalization and precision. Preserve observable upstream behavior; Python syntax/platform
adaptations must be distinguished from semantic omissions.

TestClock tests are drafted, not accepted as a complete contract yet. Port Scope and scheduler
tracking prerequisites before TestClock implementation. Keep upstream make's Scope requirement;
never replace scheduler settling with a fixed yield count. Upstream-required dependencies may
move ahead of their original roadmap tier rather than being recreated inside TestClock.
