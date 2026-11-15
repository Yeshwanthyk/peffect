# Upstream tests ported from Effect v4

Upstream is pinned at `vendor/effect-v4`, commit `289a53f7b1fc40049219c6e69022db2fb859c356` (Effect v4.1.4). Paths below are under `vendor/effect-v4/packages/effect/test/`. Each Python test's docstring names its upstream `file:line` and title.

Rule: upstream semantics are the contract. A test keeps upstream's scenario and assertions; only syntax is translated, and each translation is written down in the test file's docstring. No skip or xfail. A name peffect doesn't have yet is looked up inside the test (`upcoming(...)`), so that test fails with `AttributeError` while the rest of its file still runs.

Status values:
- **green**: ported, passes against current code.
- **red: missing API**: ported, fails with `AttributeError` on a name that doesn't exist yet.
- **green (was red …)**: ported red first (commit 1c2828b), then fixed.
- **red: behavioral**: ported, an assertion fails against an API that exists.
- **planned**: batch not ported yet.
- **deferred**: not ported, with the reason.

## Departures from upstream

| # | Upstream | peffect | Effect on the tests |
|---|---|---|---|
| D1 | A service key is a string (`Context.Service("ContextTest/A")`) | The key is the tag class | Order assertions compare classes |
| D2 | `Scope` strategy `"sequential"` (default) or `"parallel"` | Both | Parallel cases are batch G; its Scope.test.ts ports pass |
| D3 | `Context.makeUnsafe(map)` keeps the caller's map | The map is copied on install | That case is not ported |
| D4 | Interrupt-at-install forced by a custom `Scheduler.shouldYield` | No scheduler hook: the interrupt is requested inside the `update_context` merge, at the same point | Same scenario and assertion (`observed is original`) |
| D5 | Duration strings (`"1 minute"`) | Seconds as `float`, as `sleep` takes today | Only cases that need the unported Duration API are deferred (see below) |
| D6 | `fiber.pollUnsafe()` | `fiber.exit`, read in tests | none |
| D7 | `Effect.forever`, `Effect.delay` | `@gen` `while True`, `sleep(...).flat_map(...)` | No new API |

## Batch A: Context gaps — `tests/test_context_upstream.py`

| Upstream | Python test | Status |
|---|---|---|
| Context.test.ts:17 keeps the source immutable across additions | `test_keeps_the_source_immutable_across_additions` | green |
| Context.test.ts:31 preserves Map insertion order for replacements and appends | `test_preserves_insertion_order_for_replacements_and_appends` | green (was red: missing API `Context.empty`) |
| Context.test.ts:104 distinguishes an undefined service from an absent service | `test_distinguishes_an_undefined_service_from_an_absent_service` | green |
| Context.test.ts:278 supports the ReadonlyMap surface through mapUnsafe | `test_supports_the_readonly_map_surface_through_services` | green |
| Context.test.ts:292 supports equality and JSON materialization | `test_supports_equality` | green (was red: behavioral, `==` was identity) |
| Context.test.ts:304 merges with right bias and preserves empty operand identity | `test_merges_with_right_bias_and_preserves_empty_operand_identity` | green (was red: missing API `Context.empty`) |
| Context.test.ts:332 resolves reference defaults lazily and caches them | `test_resolves_reference_defaults_lazily_and_caches_them` | green |
| Effect.test.ts:405 Context.Service | `test_context_service` | green |
| Effect.test.ts:131 provideService restores the original context if interrupted before its body | `test_provide_service_restores_the_original_context_if_interrupted_before_its_body` | green (D4) |

Equality follows `Context.ts:637-649`, not dict equality: both sides must be Contexts, same size, same keys, value comparison in any order; the hash is the size. A plain mapping with the same items is not equal.

Deferred from `Context.test.ts`: `toJSON`, Redactable, `pick` / `omit` / `addOrOmit` (no such API yet), the `makeUnsafe`-retains-map case (D3), and tests of upstream internals. The pure getters (`getOption`, `getUnsafe`, `get`) have no peffect counterpart; membership in `services` and a lookup inside `provide` assert the same facts.

## Batch B: kernel finalization — `tests/test_finalization_upstream.py`

All of `Effect.test.ts:3153-3270` (describe "finalization").

| Upstream | Python test | Status |
|---|---|---|
| :3160 onExit preserves the original failure when the finalizer throws | `test_on_exit_preserves_the_original_failure_when_the_finalizer_throws` | green (was red: behavioral, the body failure was lost) |
| :3169 onExit reports a thrown finalizer on success | `test_on_exit_reports_a_thrown_finalizer_on_success` | green |
| :3175 an outer onInterrupt still sees the interruption after an inner finalizer throws | `test_an_outer_on_interrupt_still_sees_the_interruption_after_an_inner_finalizer_throws` | green (was red: missing API `on_interrupt`) |
| :3195 nested throwing finalizers do not overflow the stack | `test_nested_throwing_finalizers_do_not_overflow_the_stack` | green (20,000 deep) |
| :3203 fail ensuring | `test_fail_ensuring` | green |
| :3216 fail on error | `test_fail_on_error` | green (was red: missing API `on_error`) |
| :3231 finalizer errors merged | `test_finalizer_errors_merged` | green (was red: behavioral, no defect election) |
| :3249 finalizer errors reported | `test_finalizer_errors_reported` | green |

Fixed in `on_exit`, the primitive that owns it, not in Scope (INVARIANTS K9). A finalizer callback that raises becomes that finalizer's `Die` (upstream `exitDie(defect)` in OnExit, `internal/effect.ts:4285-4300`); on a failure its cause is added to the body's and defects are elected (`combineFinalizerCause`, `:4069-4075`; `causeElectDefects`, `:184-191`). Election means a `Die` can hold a non-exception value, as upstream's `Die.defect: unknown`, so `Die.defect` is now `object`; `squash()` raises such a value inside `DefectError`. `on_error` and `on_interrupt` are compositions of `on_exit` (`:4411-4425`, `:4485-4499`).

Old expectations changed to upstream's, each because election now applies:
- `tests/test_kernel.py::test_finalizer_failure_combines_causes`: was `failures == ["first"]` beside a `Boom` defect; now both reasons are defects, `"first"` then `Boom`. Source: Effect.test.ts:3231 and `causeElectDefects`.
- `tests/test_kernel_props.py` reference interpreter: `Ensure` with a dying finalizer now elects (`_elect`). Source: `combineFinalizerCause`. Reason retention (`_add`), the masking checks and the stack-safety test are unchanged.

## Batch C: Clock — `tests/test_clock.py`

| Upstream | Python test | Status |
|---|---|---|
| Clock.test.ts:5 keeps wall time aligned while exposing the monotonic source | `test_keeps_wall_time_aligned_while_exposing_the_monotonic_source` | green (was red: missing API `Clock`) |

`time.time_ns` and `time.monotonic_ns` are monkeypatched in place of `Date.now` and `process.hrtime.bigint`.

Source-derived sleep contracts, `tests/test_clock_sleep.py`: no upstream test outside TestClock
pins these, so each case cites `internal/effect.ts` (CL1-CL4 in INVARIANTS). All green.

| Source | Python test |
|---|---|
| :6476 `sleep = clockWith(c => c.sleep(d))` | `test_sleep_uses_the_provided_clock` |
| :6419 `millis <= 0` yields | `test_a_non_positive_sleep_is_a_yield_without_a_timer[0, -1]` |
| :6420 non-finite is `never` | `test_an_infinite_sleep_parks_without_a_timer_until_interrupted` |
| :6421-6427 `setTimeout` / `clearTimeout` | `test_interrupting_a_finite_sleep_clears_its_timer` |

NaN: `sleep(nan)` yields, like 0. Decided from `Duration.make` (Duration.ts:465), which turns NaN
into zero before `sleepMillis` sees it; `sleepMillis` alone would send NaN to `never`
(`!isFinite`). No test pins it. `-inf` yields: `toMillis` gives `-Infinity`, which is `<= 0`.

## Batches D–G

| Batch | File | Ports | Needs | Status |
|---|---|---|---|---|
| prerequisites | `tests/test_fiber_start_upstream.py` | `Effect.test.ts` :2987 :3013 :3160 :3169, `Fiber.test.ts` :137 :225; source-derived cases (labelled) for forkUnsafe :5614-5637, forkDetach :5728, exit :3873-3889, exitFailCause core.ts:611-624 | — | green: `fork`/`fork_daemon(start_immediately=)`, `exit_` |
| D: TestClock | `tests/test_testclock.py` | `TestClock.test.ts` sleep, setTime and adjust groups | C, prerequisites | drafted; red only on missing `peffect.testing` (16) |
| E: Scope, sequential | `tests/test_scope.py` | `Scope.test.ts` sequential variants: interrupted close, direct close after interrupt, masked `closeUnsafe`, throw-then-continue, sole throwing finalizer keeps the failure, the 4 fork tests | A, B, prerequisites | green (20, with the Scope.ts doc examples and source-derived cases). `Scope.use` (scopeUse) not ported, deferred to F |
| F: remaining Scope effects | `tests/test_scope_effects.py` | `Effect.test.ts` scoped `:86`, forkIn `:3038-3071`, forkScoped `:3083-3116`, updateServiceScoped `:5334` `:5345` `:5359` | E | planned |
| G: parallel Scope | `tests/test_scope_parallel.py` | `Scope.test.ts` parallel variants (4), `Effect.test.ts:3073`, TestClock "handles multiple sleeps" (needs `forkScoped`) | D, E, F | `Scope.test.ts` :7 (harness adaptation: Deferred gates for TestClock), :22, :39, :124 green; independently verified by Sol 6.1. `Effect.test.ts:3073` and "handles multiple sleeps" wait on F and D. Not reachable yet: upstream skips a finalizer keyed by the closing fiber (`key !== parent`, `internal/effect.ts:4090-4093`); only `forkIn`/`Fiber.runIn` register such a key, so it arrives with F, not as a guard now |

Decisions already made for these batches:
- **E and F** may use a small test-only latch built on the public `async_`. No fake Scope or Clock behavior.
- **Nanoseconds.** Python ints are exact, so nanosecond precision is not unavailable in general. Only a case that needs the unported Duration API is deferred, and the reason is recorded when it is.
- **`copy.copy`.** TestClock's "detached, copied or inherited" case uses JS object spread. Before using `copy.copy` in its place, check that it gives the same observable result the test asserts; don't assume the two are equivalent.

Deferred elsewhere in these upstream files:
- TestClock warning tests: they need TestConsole.
- The timeout variant of the cancelled-deadline test: tier 3.
- Layer tests: tier 4.
- `MaxOpsBeforeYield`: no scheduler hook.
- The `updateServiceScoped` reset option: no such option yet.

### Equality adaptation and independent review

NaN and recursive list/tuple/dict values follow Equal.ts's repeated-pair cycle rule, with
comparison-local path tracking. Tests also check unequal data beyond a cycle. Python numeric
values compare numerically (bool is distinct); other service objects use their Python equality
protocol. Dict keys use Python key semantics. This is not a full port of JavaScript Equal's
Map/Set, custom symbol, function, Date, RegExp, or typed-array protocols; those need their own
upstream tests before being claimed. No cross-call result cache is kept for mutable Python values.

Sol confirmed finalization semantics and 69 tests before these equality regressions were added.
The formal Sync note now names the committed Context baseline and explicitly excludes finalizer
body/cause semantics from its proof.

Clock review correction: `sleep(NaN)` now normalizes to zero before dispatching to a provided
Clock, matching Duration.make's position before the service boundary. The recording-clock test
failed before this correction and passes after it. Sol's other Clock probes passed. Parent reran
78 tests excluding the still-unimplemented TestClock draft, plus ruff and pyright successfully.
This does not establish full Duration input parity.


## Resource unit — `tests/test_resources_upstream.py`

Implementation `resources.py`, pinned `internal/effect.ts`: acquireRelease :4240-4256,
addFinalizer :4259-4269, acquireUseRelease :4502-4515. All 22 cases pass locally; independent
review and integration remain pending.

| Effect.test.ts | Ported behavior |
|---|---|
| :1389, :1407 | acquireRelease interruption and release dependencies |
| :322, :349, :377 | acquireUseRelease interruption, thrown use, uninterruptible execution |
| :2578, :2598 | inherited interrupt status and interrupted-use release |
| :3271, :3283 | scoped failure aggregation and usage result |
| :3292, :3305, :3318 | acquisition, release and use error Exits |
| :3331 | combined usage/release failures |
| :3344, :3354, :3367 | rethrown acquisition, release and use errors |

Six labelled source-derived cases: Context capture and closing Exit for acquireRelease and
addFinalizer (:4240-4269); no use/release after acquisition failure or defect for both
acquisition APIs (:4246-4254, :4509-4514). Repeated single-stage cases share parameterized
setup, retaining each upstream scenario id and assertion. Original 100 ms and 10 ms delays
are preserved. Interrupt-only Exits compare exact causes; the unported flip is checked as a
failure with the original error identity. Exception/Exit adaptations follow batch B.

Not covered: optional interruptible acquisition; the Scheduler-specific scoped installation
interruption (:85); tryPromise/AbortController (:566); unrelated AiError finalizers (:4892,
:4983). These remain incomplete, with no simulated replacements.


## Deferred prerequisite unit — `tests/test_deferred_upstream.py`

Ten cases pass locally; independent review and integration remain pending. Constructors use
`Deferred[A, E].make/make_unsafe`; completion and await are cell methods. Layer's required
stored completion Effect remains available as `effect` (Layer.ts:438).

| Pinned source | Port / source-derived contract |
|---|---|
| Deferred.test.ts:10, :56, :93 | success, failure and interruption propagation |
| Deferred.test.ts:102, :121, :149 | waiter removal, cleanup after completion, every interrupted waiter settles |
| Deferred.ts:151, :881-894 | source-derived direct completion, first-wins and clear-before-notify/reentrant callbacks |
| Deferred.ts:180-193, :881-894 | source-derived lazy stored Effect and re-evaluation on each await |
| Deferred.ts:647-648 | source-derived interruption by the completing fiber's id |
| internal/effect.ts:1196-1204 | source-derived public fiber_id read in parent and child |

Implementation maps to Deferred.ts:151/178 (constructors), :180-193 (await), :881-894
(doneUnsafe), :363-366/:323-326 (done), :400-404 (fail), :647-648/:683-695 (interrupt/
interruptWith), :806-810 (succeed). No environment-dependent completion is accepted.

The :149 liveness assertion uses a one-second asyncio harness watchdog in place of upstream
Fiber.awaitAll/timeoutOption/TestClock.adjust(1000); it does not establish virtual-clock parity.
Python schedules resumed fibers, so a white-box callback test independently checks clearing
before synchronous notification. No Deferred fallback, skipped test or suppression is used.
complete/completeWith, polling and the remaining convenience wrappers are not ported.


## Fiber bulk prerequisites — `tests/test_fiber_bulk_upstream.py`

Twelve cases: `Fiber.test.ts:32` (observer self-cancellation during publication), `:49`
(removed observer stays removed), `:90` (interruptAll awaits a one-shot iterable), plus labelled
source-derived ordered Exits / removable current observer (`internal/effect.ts:876-908`),
signal-all-before-wait and caller interruptor id (`:992-1003`), and explicit/default inheritance
for both fork APIs (`:5614-5637, :5663`). Observer ports await actual Python fiber termination
before the original array assertion; Deferred replaces only the single latch opening.

Typed migration preserves all 19 original assertion ASTs. Thirteen tests pass, including a
Python-specific regression for an iterator raising after a pending target completes; targeted mutations fail
on sequential signaling, omitted cancellation cleanup, ignored inheritance and inherited defaults.
Additional scratch probes cover scheduled startup, empty inputs, unsupported options and 10,000
already-completed fibers without recursive waiting. Bulk semantics are not formally modeled;
the extended kernel model checks inherited startup plus existing protocol properties. Independent
Sol review found an escaping iterator callback exception; the correction and regression passed
a fresh independent recheck. Iterator failures become waiter defects without altering target Exits.


## Traversal — `tests/test_traversal_upstream.py`

`Effect.test.ts` forEach ports: :771 sequential, :777 unbounded, :783 bounded, :819 bounded
interrupt (TestClock adaptation: per-item Deferred gates), :834, :852, :882 (`Effect.all` as
`for_each` with identity), :907, :933, :960, :990 length = 0, :996 string. Labelled
source-derived cases: ordered results when workers finish out of order, a fresh run per
execution, and the concurrency floor of 1 (parametrized: default, 1, 0, -3). Two more were
added after the implementation, because scratch mutations survived the ports: a mapper defect
leaves only after started workers are interrupted (initial pump and observer refill), and
workers inherit the caller's mask.

Status: independently verified by Sol 6.1. 23 traversal cases pass, plus two direct Async
cancellation regressions in test_kernel.py. The red commit's 8 ports were migrated
from a looked-up accessor to the public import with every test body AST-identical. Not ported:
"sequential interrupt", "unbounded interrupt" and "unbounded fail" (need TestClock), and
`discard`. Independent review corrections preserve worker insertion order and combine the
original interrupt before elected cancellation failures; upstream runtime replay and focused
recheck passed. Traversal is not formally modeled.
