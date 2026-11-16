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
| D1 | A service key is a string (`Context.Service("ContextTest/A")`) | Closed: `class A(Tag[int], key="ContextTest/A")`; the key string is the identity | Order assertions compare `A.key` |
| D2 | `Scope` strategy `"sequential"` (default) or `"parallel"` | Both | Parallel cases are batch G; its Scope.test.ts ports pass |
| D3 | `Context.makeUnsafe(map)` keeps the caller's map | The map is copied on install | That case is not ported |
| D4 | Interrupt-at-install forced by a custom `Scheduler.shouldYield` | Written before the scheduler was ported: the interrupt is requested inside the `update_context` merge, at the same point. A `should_yield` hook now exists; the adaptation is unchanged | Same scenario and assertion (`observed is original`) |
| D5 | Duration strings (`"1 minute"`) | Ported as is: `sleep`, `TestClock.adjust` take a `Duration.Input` (`peffect.duration`); a bare number is milliseconds | None |
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

Key identity: `tests/test_context_identity_upstream.py` repeats the applicable Context.test.ts
ports above (:17, :31, :104, :278, :292, :304, :332) asserting string keys, and adds source-derived
cases: same-key collision (Context.ts:546, 1192), per-declaration Reference defaults
(:1089-1096), the missing-key message (:1098-1101), and upstream's internal keys
(internal/effect.ts:4030, :6395; Layer.ts:600). All green.

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

Source-derived sleep contracts, `tests/test_clock_sleep.py` and
`tests/test_clock_duration_upstream.py` (the `Duration.Input` contract): no upstream test outside
TestClock pins these, so each case cites its source line (CL1-CL4 in INVARIANTS). All green.

| Source | Python test |
|---|---|
| :6476 `sleep = clockWith(c => c.sleep(d))` | `test_sleep_uses_the_provided_clock` |
| :6477 `fromInputUnsafe` inside the callback | `test_a_provided_clock_receives_the_normalized_duration`, `test_invalid_input_is_a_defect_when_run_and_the_clock_is_not_called` |
| :6419 `millis <= 0` yields | `test_a_non_positive_sleep_is_a_yield_without_a_timer[0, -1]` |
| :6420 non-finite is `never` | `test_an_infinite_sleep_parks_without_a_timer_until_interrupted` |
| :6421-6427 `setTimeout` / `clearTimeout` | `test_interrupting_a_finite_sleep_clears_its_timer` |

NaN: `sleep(nan)` yields, like 0: `Duration.make` (Duration.ts:465) turns NaN into zero before
`sleepMillis` sees it. `-Infinity` yields: `toMillis` gives `-Infinity`, which is `<= 0`. Both are
pinned in `test_clock_duration_upstream.py`.

## Batches D–G

| Batch | File | Ports | Needs | Status |
|---|---|---|---|---|
| prerequisites | `tests/test_fiber_start_upstream.py` | `Effect.test.ts` :2987 :3013 :3160 :3169, `Fiber.test.ts` :137 :225; source-derived cases (labelled) for forkUnsafe :5614-5637, forkDetach :5728, exit :3873-3889, exitFailCause core.ts:611-624 | — | green: `fork`/`fork_daemon(start_immediately=)`, `exit_` |
| lifecycle | `tests/test_fiber_lifecycle_upstream.py` | `Fiber.test.ts` :10 :22, `Effect.test.ts` forkIn :3039 (×2) :3057 :3073; source-derived cases (labelled) for runForkWith :5773-5799, fiberRunIn :5801-5822, forkIn :5717-5737, forkUnsafe mask :5622, fiberScopeFinalizer :5690-5693, scopeCloseFinalizers skip :4090-4093, scopeRemoveFinalizerUnsafe :4159-4172, FiberImpl parent clearing :652-655 | E | green (26): `run_fork`, `run_fork_with`, `run_in`, `fork_in`, `Scope.remove_finalizer_unsafe`, `Fiber.parent` |
| D: TestClock | `tests/test_testclock.py` | all 25 `TestClock.test.ts` cases (warnings, sleep, setTime, adjust, layer) plus source-derived S1–S14 | C, prerequisites, Scheduler, Console | green; see the TestClock section below |
| E: Scope, sequential | `tests/test_scope.py` | `Scope.test.ts` sequential variants: interrupted close, direct close after interrupt, masked `closeUnsafe`, throw-then-continue, sole throwing finalizer keeps the failure, the 4 fork tests | A, B, prerequisites | green (20, with the Scope.ts doc examples and source-derived cases). `Scope.use` (scopeUse) not ported, deferred to F |
| F: remaining Scope effects | `tests/test_scope_effects.py` | `Effect.test.ts` scoped `:86`, forkIn `:3038-3071` (ported in lifecycle), forkScoped `:3083-3116`, updateServiceScoped `:5334` `:5345` `:5359` | E | planned |
| G: parallel Scope | `tests/test_scope_parallel.py` | `Scope.test.ts` parallel variants (4), `Effect.test.ts:3073`, TestClock "handles multiple sleeps" (needs `forkScoped`) | D, E, F | `Scope.test.ts` :7 (harness adaptation: Deferred gates for TestClock), :22, :39, :124 green; independently verified by Sol 6.1. `Effect.test.ts:3073` green in the lifecycle file with the closing-fiber skip (`key !== parent`, `internal/effect.ts:4090-4093`); "handles multiple sleeps" waits on D and `forkScoped` |

Decisions already made for these batches:
- **E and F** may use a small test-only latch built on the public `async_`. No fake Scope or Clock behavior.
- **Nanoseconds.** Python ints are exact, so nanosecond precision is not unavailable in general. Only a case that needs the unported Duration API is deferred, and the reason is recorded when it is.
- **`copy.copy`.** TestClock's "detached, copied or inherited" case uses JS object spread. Before using `copy.copy` in its place, check that it gives the same observable result the test asserts; don't assume the two are equivalent.

Deferred elsewhere in these upstream files:
- TestClock warning tests: now ported with TestConsole (TestClock section below).
- The timeout variant of the cancelled-deadline test: tier 3.
- Layer tests: see the Layer section below.
- `MaxOpsBeforeYield`: the scheduler is now ported (Scheduler section below). These Clock-era
  adaptations still use their original stand-ins; they are unchanged here.
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

Clock Duration migration: `sleep` takes `Duration.Input` (bare numbers are milliseconds, as
upstream), `ClockService.sleep` receives a `Duration`, and the live clock converts to seconds only
at `call_later`. Every seconds caller was migrated preserving its physical interval; the TestClock
draft now uses upstream's literal inputs (`"1 minute"`, `Duration.seconds(1)`).


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

## Racing and timeouts — `tests/test_race_timeout_upstream.py`, `tests/test_duration_format_upstream.py`

`Effect.test.ts` ports: :1430 raceAll, :1449 raceAll elects, :1455 raceAllFirst, :1475 :1494
:1513 loser interrupts, :1534 :1552 :1572 while starting and pending loser, :2054 :2105 :2119
:2158 :2209 timeoutOrElse, :2244 :2253 :2268 :2281 timeoutOption, :2289 :2304 :2317 timeout.
`test_source_*` cases were measured against the pinned vendor under bun; the property oracle
`race_model` reproduced all 966 vendor runs (n ≤ 3, every sync/gated and ok/fail/die mix and gate
order). TestClock adaptation: a provided `GateClock` whose sleeps park on gates the test opens.
Data-first only; no pipe forms.

The first 32 cases were committed red (ad754c8), reading upcoming names through a lookup helper. The
implementation replaced the helpers with typed imports; every behavioral assertion is kept. One
assertion compared two `Failure` objects, which compare by identity, so it could never pass; it
now checks `isinstance(exit, Failure)` and compares the causes by value.

`Duration.format` tests were independently verified red against the base checkout (not committed red): Duration.test.ts :497-507 and :740-742, plus 42
source cases (zero forms, fractional millis, exact nanos, negatives, infinities, days beyond
1e21 and beyond the double range, the timeout inputs), each expected string printed by the pinned
`Duration.format` under bun.

Independent review found early-exit generator cleanup and large-day decimal formatting gaps.
Both are corrected and pinned in `test_race_review_regressions.py`; the three reviewer
reproductions were observed red, then green. `race_all_first` calls the iterator's `close()`
on early break, matching JavaScript IteratorClose; formatting expands the shortest decimal
instead of the exact binary integer. The remaining review found no actionable issue.

Not ported: pipeable forms, spans/logging, the
TestClock-only breadth (`Effect.delay` timing, clock adjustment variants).

One source case was added after the implementation because a scratch mutation survived the
ports: `test_source_how_far_each_race_reads_a_lazy_iterable` (raceAll reads every racer first;
raceAllFirst reads lazily and forks nothing after a synchronous winner; bun probe p3.ts).

Scratch mutations, each in a temp copy of `src/` first on `PYTHONPATH`, against both test files:
winner mode (race_all as first-exit, race_all_first as first-success), failures in reverse
completion order, no start cutoff (race_all and race_all_first separately), losers not interrupted
when the race settles, losers not awaited (no OnExit cleanup), losers interrupted before the
winner resumes, race_all_first reading its iterable eagerly, and an eager timeout fallback: each
fails named tests. Racers inheriting the caller's mask deadlocks
`test_source_a_masked_race_still_interrupts_its_loser_and_finishes_first`: the uninterruptible
`never` loser can never be interrupted, and the `wait_for` hang guard cannot fire, because
cancelling `run` waits for the masked root. It is detected as a hang (pytest killed at 60 s,
faulthandler dump at 10 s), not as an assertion failure.

## Layer — `tests/test_layer_upstream.py`

`Layer.test.ts` ports:
- :15 parallel acquisition and :35 sharing with merge.
- :512 identity memoization, blocks 1–2. Block 3 needs `Layer.fresh`.
- :557 interrupted build, and :583 interrupted reuse.
- :606 and :624 for get effects.
- :638 no memoization before execution.
- :673 and :693 across builds.
- :712 forked memo maps. Its type-id read becomes `isinstance(child, MemoMap)`.

Five labelled source-derived cases cover:
- a build on a closed scope is unshared;
- `get` takes ownership when it is called;
- a nested build forks `CurrentMemoMap`;
- with no current map, each build gets a fresh one;
- `provide_layer`'s scope lifecycle.

Harness adaptations, written in the module docstring:
- `Counter` replaces Ref.
- Deferred gates replace Latch.
- The `MaxOpsBeforeYield` sweep becomes named interrupt points. This is partial coverage.
- Awaiting under a watchdog replaces settle + `pollUnsafe`.

Status: independently verified by Sol 6.1. 21 cases pass: the red commit's 15, :712,
and the 5 source cases. The full verifier reports 208 passed and 16 pending TestClock failures.

The red commit's dynamic `_layer(...)` wrappers were migrated to typed calls; `merge` and
`provide` are data-last (`self.pipe(Layer.provide(that))`). No assertion changed.

One test-port mistake was found and corrected, with the source cited: in :557 "parked-in-build",
every build attempt parked behind the gate. Upstream's `fromBuildMemo` observes on the scope that
`fromBuild` forked and closes it on failure (Layer.ts:360-372), so once both early requesters
fail the entry is dropped and the later requester rebuilds. Upstream's value is
`Effect.succeed`, so that rebuild does not park. Now only the first attempt parks.

## ManagedRuntime

`tests/test_managed_runtime_upstream.py` and `tests/test_managed_runtime_interleavings.py`
contain 43 collected cases: upstream ports, vendor-derived disposal scenarios, Python async
cancellation/retention seams, and bounded manual-scheduler interleavings. Each test records its
source. The two upstream Pool tests remain unported because Pool is unavailable.

The author measured 20/20 vendor scenarios matching and a 20,000-example deep run without
failures. Independent review passed 54 focused tests including typing and boundaries, and
replayed the vendor disposal outcome classes. It did not repeat the full deep search or
mutation campaign. See INVARIANTS MR1–MR9 for contracts and limitations; no formal coverage of
ManagedRuntime is claimed.

## Scheduler — `tests/test_scheduler_upstream.py`, `tests/test_fiber_resume_upstream.py`

`Scheduler.test.ts` ports:
- :42 priority order (sync), :66 FIFO within a priority.
- :79 `PreventSchedulerYield` disables `should_yield` checks.
- :105 a throwing dispatch kills the fiber with that defect, with no retry.
- :131 concurrent `for_each` completes under `MaxOpsBeforeYield` 1, 2 and 3.
- :159 inlined `map` steps count against the budget.

Not ported: the three `runSyncExit` tests (peffect has no synchronous runner) and the microtask
fallback (Cloudflare's `setImmediate`).

Source-derived, each with the value measured against the pinned source under Node 22. Case
labels are the test-name labels; the invariants they pin are INVARIANTS SC1–SC7.

| case | reads | measured upstream |
|---|---|---|
| S2 dispatcher made on first dispatch, then kept | effect.ts:567-570 | `[0, 1, ["s#1@0" ×3]]` |
| S3 provide switches the dispatcher; leaving drops it | effect.ts:801-803 | `["out#1@0", "in#1@0", "out#2@0"]` |
| S4/S5 scheduled children start on the parent's dispatcher (fork, fork_daemon) | effect.ts:5629-5633 | `[["s#1@0"], ["s#1@0", "s#1@0"], 1, 1]` |
| flush cancels the armed immediate, then drains | Scheduler.ts dispatcher `flush` | `[1]` |
| S1 resume runs the parked fiber before `resume` returns | effect.ts:1220-1226 | `["A continues", "resume returned"]` |
| S10 interrupting a parked fiber publishes before returning | effect.ts:605-611 | `[true]` |
| S10 interrupt then resume of one park: interrupt only | effect.ts:605-611, :1213-1219 | `[0, true]` |
| L1 P3 interrupting a yielded fiber exits now, drops its queued step | effect.ts:773-781 | `[true, 0]` |
| L1 P8 the dropped step cannot resume a finalizer parked after the interrupt | effect.ts:773-781 | `[false, 0, true]` |
| S1 Deferred waiters resumed with an interrupt die inside `done_unsafe` | Deferred.ts:881-895 | `[true ×4]` |
| A7 the root runs its synchronous prefix when `run` starts | effect.ts:5777 | `"effect prefix"` first |

Adaptation: A7 pins only that the prefix runs before callbacks already queued on the loop;
whether the awaiting caller resumes before them is asyncio's choice.

The red commit's `api(name)` lookups were migrated to typed imports from `peffect.scheduler`. No
assertion changed. The scheduler's ordering is documented in INVARIANTS `## Scheduler`; its formal
check is `.claude/skills/verify-formal-kernel` (bounded, nesting ≤ 2, not a proof at arbitrary
depth).

Status: implemented and independently reviewed; boundary correction and scheduled-start regression accepted.

## Coordination and composition acceptance

`test_latch_upstream.py`, `test_semaphore_upstream.py`, `test_option.py`,
`test_composition_upstream.py` and `test_gen_cleanup.py`: 81 scoped tests passed in independent
review. The review also passed 12 existing finalizer tests and the typing harness. Both original
findings (split semaphore wake publication and invariant Some) are corrected; scheduler-visible
wake-budget and negative-free resize regressions catch the formerly surviving mutations.

Effect and Layer pipes support 0–20 steps. Tap supports a function or an Effect. Python generator
closing is explicitly an adaptation; INVARIANTS records its cause contract and typing limits.
The callback mutation harness must rebind the package export as well as the defining module,
otherwise tests importing `peffect.on_exit_with_dispatcher` do not exercise the mutation.

The paired Batch application now checks 21 scenarios against actual vendored Effect. Independent
review found application-level eager capacity checking and custom-resource wiring errors in both
languages. Execution-time append checks and a single chosen Ledger identity correct them; saved
and preconstructed append regressions plus custom-capacity sharing exercise those contracts.
This is application evidence, not exhaustive concurrency evidence or a formal library proof.

## TestClock, Console, Logger, fork_scoped — `tests/test_testclock.py`, `tests/test_console_logger.py`, `tests/test_fork_scoped_upstream.py`

`TestClock.test.ts`: all 25 cases, each docstring naming its line and title. The two warning
cases poll both the live output and TestConsole and stop at either, so a regression fails on the
assertion instead of a timeout. `Effect.test.ts` forkScoped :3083-:3116: both cases, plus one
source-derived case (the fiber outlives its forking fiber until the scope closes).

Source-derived TestClock cases. Values were measured against the pinned source under bun where the case states one:

| case | reads | pins |
|---|---|---|
| S1 negative adjust moves wall time back, not monotonic | run's `if (adj > 0)` | −1 s: nanos `(-1e9, 0)`; then +2 s: `(1e9, 2e9)` |
| S2 equal deadlines wake in sleep order | sleep's binary insert | `a, b, c` |
| S3 a clock without its Scheduler yields once per wake | wake, `pending == 0` | a woken fiber that yields once finishes before `adjust` returns |
| S4 closing the `make` scope stops the pending warning | make's `addFinalizer(warningDone)` | 0 warnings in a 150 ms window |
| S5 monotonic rounds half up, wall floors | advanceTo, millisToNanos | `(2.5e-6 → 2, 3)`, `(5e-7 → 0, 1)`, `(7e-7 → 0, 1)`, `(1.5 → 1.5e6, 1.5e6)` |
| S6 non-finite times | advanceTo, run | nanos stay 0; a sleep past +∞ returns |
| S7 sub-millisecond adjust keeps exact nanos | adjust's exact-nanos step | `D.nanos(1500)`: 0.0015 ms, nanos `(1500, 1500)` |
| S8 `test_clock_with` on the live clock | testClockWith | TypeError defect (upstream casts) |
| S9 each run gets a fresh clock | layer | two runs each read 1000 after `adjust(1s)` |
| S10 one warning per clock | warningStart from Start only | 1 warning for two sleeps |
| S11 the warning belongs to the first sleeper | warningStart `forkChild` | interrupting the sleeper: 0 warnings |
| S12 a raising tracked task still settles the wake | TrackingScheduler `try/finally` | `adjust` completes; later tasks reach the parent unwrapped |
| S13 the warning fiber is interruptible under a masked sleeper | `forkChild` default | `adjust` returns at once, 0 warnings |
| S14 `set_time` stops the warning while the sleeper still waits | setTime's `warningDone` | 0 warnings |

S12 is white-box through public seams: a recording `MixedScheduler` and the loop's exception
handler; nothing in production is faked.

`test_console_logger.py` (source-derived from Console.ts, TestConsole.ts and
`internal/effect.ts`): reference keys, live routing, TestConsole capture and fresh builds, the
default header, LogToStderr, minimum levels, Set-like logger dedup, no loggers, the default
logger list.

Corrections to the red tests, type-only: `LEVELS` and `MINIMUM_CASES` are annotated with
`LogLevel`, and `local_timezone` returns `Generator` (pyright flags `Iterator` on a
`@contextmanager`). No assertion changed. S12, S13, S14 and the `7e-7` S5 case were added during
implementation; the last three kill mutants that survived the first production run.

Adaptations: `repr` stands in for `toJSON`; `liveClocks`' WeakMap is an `isinstance` unwrap;
the logger is header-only (no spans, annotations or cause rendering); Console has five methods;
`LogOptions` carries the fiber id and the fiber's context, not the fiber (B3).

TestClock review regressions: `test_nan_set_time_wakes_pending_sleep` preserves the upstream
`>` break condition for unordered NaN targets; `test_no_loggers_does_not_read_clock` pins the
empty-logger fast path. Negative typetests 119–120 reject filter sentinels as emitted severities.
