# Kernel invariants

What the fiber kernel (`src/peffect/core.py`, `src/peffect/fiber.py`) promises, where each promise is
enforced, and how it is proven. Everything above the kernel (layers, schedules, streams, codecs)
leans on these, so they are checked three ways: example tests (`tests/test_kernel.py`), generated
programs against the real code (`tests/test_kernel_props.py`), and a TLA+ model of the
park/resume/interrupt protocol (`.claude/skills/verify-formal-kernel/`).

## Authoritative state

One owner: the `Fiber`. Nothing else writes these fields except through the methods named.

| state | field | written by |
|---|---|---|
| continuation stack | `Fiber.stack` | node `_eval`s (push), `Fiber._pop` (pop) |
| may be interrupted now | `Fiber.interruptible` | `_start` (before startup), `uninterruptible*`, `interruptible`, `SetInterruptible.on_any`, `_mask_while_popping` |
| interrupt requested | `Fiber.interrupted` (Cause or None) | `Fiber.interrupt_unsafe` only |
| interrupt not yet delivered | `Fiber._deferred` | `interrupt_unsafe` (set), `Fiber.take_interrupt` (clear; every delivery site calls it) |
| parked on an Async or a queued step | `Fiber._parked` + `Park.done` | `Async._eval` / `yield_now` (park), `resume` / the queued `step` / `_interrupt_now` (flip done) |
| run loop active | `Fiber.running` | `evaluate` only (set on entry, restored to its previous value on exit) |
| dispatcher | `Fiber._dispatcher`, `_scheduler` | `current_dispatcher` (made on first use), `_read_scheduler` (dropped when the scheduler changes) |
| result | `Fiber._exit` | `Fiber._publish` only |
| live children | `Fiber.children` | `_start` (add), child `_publish` (discard) |
| body's exit while children finish | `Fiber._ending` | `Fiber._done` only |
| services in scope | `Fiber._context` (read-only map; public read via the `context` property) | `Fiber.swap_context` only, called by `update_context` (install) and `RestoreContext.on_any` (put back) |

Derived: `Fiber.exit` (property), `Cause.is_interrupted` etc. Nothing is cached.

## Transitions

| transition | trigger | precondition | write | code |
|---|---|---|---|---|
| start | `fork` / `fork_daemon` / `run_exit` | — | default or inherited mask installed before startup; child in `parent.children` (not for a daemon); its start queued at priority 0 on the forking fiber's dispatcher, or with `start_immediately=True` evaluated now, nested in the caller's drive, until it parks or ends (v4 forkUnsafe, internal/effect.ts:5614-5637); a root (`run_exit`) and a concurrent `for_each` worker are evaluated now (:5777) | `fiber._start` |
| step | run loop | `running` | eval one node; maybe push/pop frames | `Fiber.evaluate` |
| yield | `YieldNow`, or the op budget (`MaxOpsBeforeYield`, unless `PreventSchedulerYield`) | — | queue `step` on `current_dispatcher`, then `_parked = park`; the step claims the park and evaluates, or returns if an interrupt claimed it first (internal/effect.ts:773-781) | `Fiber.yield_now` |
| park | `Async._eval` | `register` didn't resume synchronously | `_parked = park`; push `_AsyncCancel` if cancel | `Async._eval` |
| resume | callback from asyncio / another fiber | `not park.done` | `park.done = True`; `evaluate(eff)` now, before `resume` returns (internal/effect.ts:1220-1226) | `Async._eval.resume` |
| interrupt | `interrupt_unsafe(by)` | not finished | add to `interrupted`; running or masked → `_deferred`; otherwise (parked, queued, or not started) mark the parked `Park` done and evaluate the Failure now (internal/effect.ts:590-611) | `Fiber.interrupt_unsafe`, `_interrupt_now` |
| deliver | loop top / `SetInterruptible` pop / `interruptible()` / unwind / parked wake | interrupt pending and interruptible | `take_interrupt()` clears `_deferred`; the node becomes (or the unwinding cause gains) `interrupted` | `Fiber.take_interrupt` |
| unwind | `Failure._eval` while interrupted+interruptible | — | pop handlers until a frame masks (OnExit, AsyncCancel); `exit_`'s `ExitFrame` is popped like any handler, so it never turns its own fiber's interrupt into a value (v4 exitFailCause, internal/core.ts:611-624) | `Failure._eval` |
| finish | stack empty | — | remember the exit in `_ending`; interrupt live children; park on `_await_all` *inside the run loop* (so an interrupt during the wait is delivered and merged into the exit); publish once no child is live | `Fiber._done`, `_await_all`, `_publish` |

## Invariants

| id | must stay true | enforced at |
|---|---|---|
| K1 | **Exit once.** A fiber publishes exactly one Exit; every observer is called exactly once (late observers immediately). | `_publish` (single call site: `_done`), `add_observer` |
| K2 | **Resume once.** An Async continues its fiber at most once, whether resume or interrupt wins. | `Park.done` |
| K3 | **One runner.** Per fiber, at most one `evaluate` is running (nested or not) or live in a dispatcher queue. A queued step or start that lost its claim stays queued but returns at once. | every entry is a claim of `Park.done` or the `_exit` check; `running` defers an interrupt |
| K4 | **Finalizers run.** Every `OnExit` frame that was pushed runs its finalizer exactly once before the fiber publishes. | `OnExit.on_any` masks as it pops, stopping the unwind |
| K5 | **Interrupts aren't caught.** A user error handler never runs with an interrupt in flight while the fiber is interruptible. | `Failure._eval` skip loop |
| K6 | **Masks hold.** No interrupt is delivered while `interruptible` is False. | every delivery site checks `interruptible` |
| K7 | **Interrupts land.** A requested interrupt is delivered at the next interruptible point; if the fiber reaches one before finishing, its cause contains `Interrupt`. A worker that stays masked through completion need not deliver it. | `_deferred`, `SetInterruptible`, `evaluate` tail |
| K8 | **No orphans.** A fiber publishes only after every child it forked has published. | `_done` |
| K9 | **Causes don't lose reasons**, except: the typed `Fail`s of a skipped handler's region are dropped on interrupt (v4 `core.ts:601-629`). Stronger than v4 in two places: a defect unwinding out of a mask, and a body's exit when an interrupt lands while waiting on children, keep their reasons (v4 replaces them with the interrupt). A failing or raising finalizer adds its cause to the body's, then defects are elected as v4 does (`combineFinalizerCause`, `causeElectDefects`, internal/effect.ts:4069-4075, 184-191): with a `Die` present, each `Fail` becomes a `Die` holding the same value, so a `Die`'s defect is any value. | `Cause.__add__`, `Cause.elect_defects`, `OnExit._finalizer`, `OnExit.on_err`, `Failure._eval`, `Fiber._done` |
| K10 | **Stack safety.** One fiber's composition depth never becomes Python recursion depth. Wakes across fibers do: see SC7. | nodes return the next node |

## Proof strategy

| claim | how | where |
|---|---|---|
| K1, K2, K3, K6, K7, K8 (orderings between drive, resume, interrupt, child exit) | TLA+ model of one fiber + one child + async callbacks + an interrupter, every interleaving | `verify-formal-kernel` |
| K4, K5, K9 over arbitrary programs | Hypothesis: random effect trees, interrupted at random scheduler ticks, on the real kernel; oracle = an independent recursive reference interpreter for the no-interrupt case, and trace rules for the interrupt case | `tests/test_kernel_props.py` |
| K10 | 100k `flat_map` example | `tests/test_kernel.py` |

Atomicity assumption the model relies on: asyncio runs one callback at a time and `evaluate` never
awaits. Code a node calls synchronously (an Async `register`, a `Sync` thunk, an observer) can
resume, interrupt or `flush` for any fiber, including the running one; the model lets those wakers
act nested inside any drive, so a drive is atomic only between them.

## Findings log

| date | found by | invariant | what | fix |
|---|---|---|---|---|
| 2026-11-15 | Hypothesis `test_interrupt_at_any_tick`, `Bind(Park, Fork(Defect))`, tick 1 | K7 | Body finished, a child still running; an interrupt sent then set `_deferred`, which nothing read after the run loop ended: published `Success`. | `_done` waits via `_await_all` inside the run loop (as v4 does); regression `test_interrupt_while_waiting_for_children_lands` |
| 2026-11-15 | sensitivity run: deleting `cause + interrupted` in the skip loop survived 600 examples | K7 (test gap) | the generator rarely builds "interrupt waits out a mask, then meets an unwinding failure" | pinned `@example`s; `--hypothesis-profile=deep` finds it unaided in ~12s |
| 2026-11-15 | review of delivery sites | K7/K9 (latent) | `SetInterruptible`, `interruptible()` and the skip loop delivered without clearing `_deferred`, so the loop top could deliver again | all delivery goes through `Fiber.take_interrupt()` |

## Sensitivity (each guard is load-bearing)

`tests/test_kernel_props.py` goes red when any of these is removed from the kernel:

| break | red on |
|---|---|
| `_mask_while_popping` does nothing (finalizers interruptible) | K4: `enter:i` without `fin:i` |
| skip loop disabled (handlers run during an interrupt) | K9: a finalizer's defect missing from the cause |
| skip loop stops adding `interrupted` | K7: `Failure(Fail('a'))` with no `Interrupt` (pinned `@example`) |
| old `_done` (wait outside the run loop) | K7: `Success(0)` after an interrupt |
| `OnExit.on_err` skips `elect_defects` | K9: `test_kernel_matches_reference`, a `fail` left beside a finalizer's `die` |

## Scheduler

Pinned `Scheduler.ts:33-271` and `internal/effect.ts:567-570, :590-611, :615-781, :794-828,
:1220-1226, :5629-5637, :5777`. Tests: `tests/test_scheduler_upstream.py` (Scheduler.test.ts
ports plus source-derived cases) and `tests/test_fiber_resume_upstream.py`. Test names keep their
design labels (S1-S10, L1, A7); the rows below are numbered SC so they do not clash with Scope.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| SC1 | **Wakes are synchronous.** A resume, an interrupt of a fiber that is not running and interruptible, and a root start evaluate the fiber before returning. | `Async._eval.resume`, `interrupt_unsafe`, `_start`, `run_exit` | `test_s1_*`, `test_s10_*`, `test_a7_*`; `async_wake` mutation |
| SC2 | **One dispatcher per fiber per scheduler**, made on first dispatch and dropped when a provide changes the scheduler, entering or leaving. | `current_dispatcher`, `_read_scheduler` | `test_s2_*`, `test_s3_*`; `no_dispatcher_cache`, `no_dispatcher_reset` mutations |
| SC3 | **Scheduled children start on the forker's dispatcher** at priority 0; immediate ones evaluate inline. | `_start(spawner=...)` | `test_s4_s5_*`; `child_own_dispatcher` mutation |
| SC4 | **A queued step is one-shot.** An interrupt that claims a yielded fiber's park runs now; the step then returns, even if a finalizer has parked the fiber again. | `yield_now`'s `step` checks `park.done` | `test_l1_*` (P3, P8); `queued_step_ignores_claim` mutation; `Break_no_token_claim` |
| SC5 | **The budget cannot livelock.** The slice resuming an automatic yield runs its first op unchecked (`currentOpCount` starts at 1), so `MaxOpsBeforeYield` 1 still progresses; `PreventSchedulerYield` never asks `should_yield`. | `evaluate`'s `resuming`, `_read_scheduler` | budget 1-3 ports, prevent port; `no_resuming`, `ignore_prevent` mutations |
| SC6 | **A throwing dispatch is a defect, not a retry.** | `evaluate`'s `yielding` flag | throwing-dispatch port; `retry_dispatch` mutation |
| SC7 | **Wake chains recurse (platform limitation, not a promised threshold).** Each synchronous wake nests one `evaluate`, as upstream; there is no trampoline. Measured 2026-11-15 on Python 3.13.1 (uv), default recursion limit 1000 (scratch `python_chain_impl.py`: fiber i awaits a Deferred, then completes the next): chains of 160 complete; at 170 or more, 164 complete, one fiber dies with `RecursionError` and the rest stay pending. Upstream has no guard either: the same chain aborted Node 22 at 647 nested resumes, and Bun threw `RangeError` at 3948 with the rest hung. | none (upstream behavior) | measured only |

Exact source behavior reviewed for this unit:
- **`_running` reset and publication.** Upstream `runLoop` saves `prevRunning`, sets `_running`,
  and restores it in `finally` (internal/effect.ts:676-677, :731-733); `evaluate` then sets
  `_exit` and calls observers (:637-659), so upstream publishes with `_running` already restored.
  peffect restores `running` the same way, but publishes from `_done` inside the loop, with
  `running` still set. White-box observers can see this difference; B3 excludes `running` from
  ordinary callers. Exited-fiber reentry remains harmless: `_publish` sets `_exit` before any observer runs, and every
  entry to a fiber (`evaluate`, `interrupt_unsafe`) returns at the `_exit` check first. The formal
  model ends the publishing drive (`Replace`) on that argument.
- **Deferred delivery after a park.** Upstream handles an interrupt that arrived during register
  inside `runLoop` (`yielded()` then `continue`, :714-719, `_running` still set); peffect restores
  `running` and then calls `_interrupt_now`, a fresh `evaluate` at the same nesting level. No
  other code runs in between.
- **Batch exceptions.** `_after_scheduled` clears `_running` and `_run_tasks` drains every bucket
  before running any task (Scheduler.ts:241-262); a task that raises ends the batch, and the rest
  of the drained tasks are dropped, as upstream. Fiber steps never raise (`evaluate` turns
  exceptions into defects), so only a user task scheduled directly can. Platform difference: the
  exception reaches asyncio's loop exception handler, where Node would report an uncaught
  exception.
- **Sync mode.** asyncio has no microtask queue, so `MixedScheduler("sync")` also arms with
  `loop.call_soon`; `execution_mode` is only reported.

Not modeled formally: priorities, batch boundaries, the op budget and `should_yield` (the model
treats an automatic yield as a plain yield step); those are pinned by tests and mutations.

## Context

`update_context(eff, f)` (kernel, `core.py`) and `RestoreContext` own every change to a fiber's
services; `context.py` (Tag, Reference, Context, `provide`) is built on them and on `WithFiber`.

| id | must stay true | enforced at | proof |
|---|---|---|---|
| C1 | **Restored on every exit.** After `update_context(eff, f)` ends (success, failure, defect, interrupt while parked), the fiber's context is the one from before. | `RestoreContext.on_any` (`_pop` runs `on_any` on every popped frame, including skipped ones) | `test_restored_after_success_failure_and_defect`, `test_nested_provide_layers_and_unwinds`, `test_restored_when_interrupted_while_parked_inside` |
| C2 | **No window.** The swap and the restore frame push happen in one `WithFiber` callback, so no interrupt lands between them; if `f` raises, nothing changed. | `update_context.go` | `test_interrupt_at_install_still_restores` (the interrupt is requested inside `f`, and the test checks the new map was installed and the body never ran), `test_raising_update_changes_nothing` |
| C3 | **Owned snapshot.** The fiber holds a read-only copy: later writes to the caller's mapping don't show, and the fiber's map, its `context` attribute and its deletion all refuse writes. A forked child starts with the parent's map at fork time and can't change the parent's. | `MappingProxyType(dict(...))` in `update_context`; `Fiber.context` property; `fork` passes `p.context` | `test_fiber_context_is_read_only_and_owned`, `test_child_inherits_context_at_fork_and_cannot_leak_back`, `test_root_starts_empty`; should_fail 12–14 |
| C4 | **Reference default: once per declaration, lazy.** Built on first unprovided use, cached in that class's own `__dict__` (never inherited), shared by every fiber and root; an override never builds it. | `Reference._find` | `test_reference_default_built_once_across_roots_and_override_restores`, `test_reference_defaults_are_per_declaration_despite_one_key` |
| C5 | **A raising default is a defect and is not cached**; the next use calls it again. | `Reference._find` (the raise escapes before the cache write; `WithFiber` turns it into a defect) | `test_failed_default_is_a_defect_and_not_kept` |
| C6 | **Concrete tags are final.** R is covariant, so a parent-tag context would type-check against a child-tag request (under another key) it can't answer. Subclassing any tag other than `Tag`/`Reference` raises `TypeError`; `Tag`/`Reference` themselves build no context. | `Tag.__init_subclass__`, `Tag.context` | `test_concrete_tags_cannot_be_subclassed`, `test_base_tags_build_no_context` |
| C7 | **A `Context[T]` holds a service for every tag in T.** No public constructor (a private token); built only by `Tag.context` and `\|` (right side wins); its `services` view is read-only. Contravariant in T. | `Context.__init__`, `Context.__or__` | `test_context_has_no_public_constructor_and_merges_right_wins`, `test_merged_context_right_side_wins_at_runtime`; should_fail 10–11 |
| C8 | **A missing service is a `MissingService` defect**, never `None`, with upstream's message `Service not found: <key>` (Context.ts:1098-1101). With C7 and the R typing, only an `update_context` that drops services can reach it. | `Tag._find` | `test_missing_service_is_a_defect`, `test_missing_service_reports_its_key` |
| C9 | **Empty identity and Context comparison.** `Context.empty()` is one shared instance; `a \| empty` and `empty \| a` return `a` itself. Two Contexts are `==` when they have the same keys and each value matches by identity, NaN equality, recursive list/tuple/dict comparison (cycles allowed), or Python object equality, in any order; anything that is not a Context is unequal; the hash is the size (Context.ts:637-649, 744-745, 1192-1193). | `Context.empty`, `Context.__or__`, `Context.__eq__`, `Context.__hash__` | `tests/test_context_upstream.py`: `test_supports_equality`, `test_merges_with_right_bias_and_preserves_empty_operand_identity`, `test_preserves_insertion_order_for_replacements_and_appends` |
| C10 | **The explicit key string is the identity** (Context.ts:256-262, 546). Each concrete tag declares `key=` (a `str`, no derived default; else `TypeError`). Contexts and fiber maps are keyed by it; two declarations with one key read, replace in place (first position kept, right side wins) and compare equal, as upstream. Internal keys are upstream's: `effect/Scope`, `effect/Clock`, `effect/Layer/CurrentMemoMap`. | `Tag.__init_subclass__`, `Tag.context`, `Tag._find`, `Reference._find` | `tests/test_context_identity_upstream.py` |

**R typing** (`provide`, two overloads, exact match first). Python has no `Exclude`, so:

| R of the program | after `provide(Context[T])` | pinned by |
|---|---|---|
| exactly T, or a part of T | `Never` | should_pass (full, over-provided) |
| a union naming T's tags plus others | the others | should_pass (partial 2→1→0, overlap) |
| a bare type variable `R` | `R`, kept (sound, not narrowed) | should_pass `keeps` |
| `Users \| R` with `Users` provided | `R` | should_pass `subtracts` |
| still non-empty at `run` / `run_exit` | type error | should_fail 5–7 |
| wrong service type, wrong Reference override, context claiming more than it holds, forged `Context` | type error | should_fail 8–11 |

**Limits.** Finality (C6) and a missing or non-`str` key (C10) are runtime checks: pyright
accepts `class Admins(Users)` and `class Users(Tag[Repo])`; only the class statement raises.
Pyright keeps two same-key declarations distinct in R, while at runtime they are one slot, as
upstream; unique keys are the caller's job. `Tag[str].context(...)` type-checks and raises at runtime. Overloads
are checked with pyright only, not mypy. Providing a Reference override leaves R unchanged
(should_pass, `Level`).

**Sensitivity** (scratch breaks, 2026-11-15, each restored byte-identical): no restore frame
(6 tests red), swap and frame push in two steps (interrupt-at-install red), no snapshot copy
(read-only test red), fork drops the context (2), no default cache (2), subclassing allowed (1),
merge with left side winning (2), public `Context` constructor (1), a miss returns `None` (1); the
partial overload returning `Never` turns `test_types` red. Key identity (2026-11-15, scratch copy):
a replaced key moving to the end (5 red), left side winning (13), default cache per key instead of
per declaration (2).

**Not in the formal model.** `verify-formal-kernel` does not model context. C1–C8 rest on the
tests above. Its SKILL.md argues why the model's pass still holds with `RestoreContext` added: the
frame returns nothing and touches no modeled field.

## Clock

`src/peffect/clock.py`, a leaf: it imports `Reference` and the public `async_`, and the kernel
does not import it. Upstream: `internal/effect.ts:6395-6486`.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| CL1 | **Sleep goes through the fiber's Clock.** `sleep(d)` is `clock_with(lambda c: c.sleep(from_input_unsafe(d)))`; a provided Clock receives the normalized `Duration` and replaces the live timer for everything inside its `provide`. The conversion runs inside the callback, so invalid input is a defect when the effect runs and the Clock is not called (effect.ts:6476-6477). | `clock.sleep`, `clock_with` | `test_sleep_uses_the_provided_clock`, `test_a_provided_clock_receives_the_normalized_duration`, `test_invalid_input_is_a_defect_when_run_and_the_clock_is_not_called`, `examples/clock.py` |
| CL2 | **Non-positive or NaN is a yield, with no timer.** The live clock sleeps `sleepMillis(toMillis(d))`; NaN became zero in `Duration` (Duration.ts:465) and `-Infinity` is `<= 0` (effect.ts:6419). A positive sleep sets one `call_later(millis / 1000)`, the only seconds conversion. | `_LiveClock.sleep` | `test_a_non_positive_sleep_is_a_yield_without_a_timer[0, -1]` |
| CL3 | **Infinite parks with no timer** until interrupted (effect.ts:6420). | `_LiveClock.sleep` returns `never` | `test_an_infinite_sleep_parks_without_a_timer_until_interrupted` |
| CL4 | **Interrupting a finite sleep cancels its timer** (`clearTimeout`, effect.ts:6421-6427). | `async_` cancel effect runs `handle.cancel` | `test_interrupting_a_finite_sleep_clears_its_timer` |
| CL5 | **Wall nanos stay within 1s of wall time.** Monotonic plus a module-level origin, re-anchored when the skew is strictly greater than 1e9 ns; wall time is whole milliseconds like `Date.now()`. Integer arithmetic only. | `_wall_nanos` | `test_keeps_wall_time_aligned_while_exposing_the_monotonic_source` |

**Not in the formal model.** `verify-formal-kernel` models no clock. Moving `sleep` out of
`core.py` changed no kernel node: it was already `async_` plus `call_later`, and still is.

## Scope

`src/peffect/scope.py`, outside the kernel: it composes the public `uninterruptible`, `suspend`,
`on_exit`, `update_context`, `exit_`, `fork_daemon` and `await_all`, and reaches no fiber
internals (B1–B3). Upstream: `Scope.ts`, `internal/effect.ts:4033-4215`. Both strategies; S8.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| S1 | **Closed is set once, before any finalizer runs.** A second close runs nothing (`scopeCloseUnsafe`, :4040-4056). | `Scope.close_unsafe` checks `Closed` first | `test_closing_the_parent_closes_the_child_with_the_parents_exit_only_once` |
| S2 | **Newest first, each once, each given the close Exit.** | `_close_finalizers` (`reversed`) | `test_doc_close_runs_three_finalizers_newest_first_after_the_work`, `test_sequential_continues_and_awaits_remaining_finalizers_after_a_throw` |
| S3 | **`close` runs its finalizers masked.** Masking comes before the suspend that reads the state, so no interrupt lands between them (`scopeClose`, :4033-4038). | `uninterruptible(suspend(...))` | `test_sequential_completes_direct_close_after_interruption`, `test_sequential_completes_a_single_finalizer_when_its_callback_interrupts_close`, and their `parallel_` ports |
| S4 | **A direct close keeps every failure reason, in order, with no merging and no election** (`exitAsVoidAll`, :2221-2234). | `_as_void_all` builds `Cause(tuple(...))`, not `+` | `test_source_direct_close_keeps_every_failure_reason_and_elects_no_defect` |
| S5 | **A finalizer added after close runs at once with the closed Exit**; a fork of a closed scope starts Closed (:4113-4130). | `add_finalizer_exit`, `fork_unsafe` | `test_source_a_finalizer_added_after_close_runs_at_once_with_the_closed_exit`, `test_source_forking_a_closed_parent_gives_a_child_closed_with_the_parents_exit` |
| S6 | **A child leaves its parent when it closes**, however its close ends. | `close_unsafe` removes the child key before running finalizers | the three `..._child_...` tests |
| S7 | **`scoped` restores the context before closing.** `on_exit` sits outside `update_context`, so the restore frame pops first; a pending interrupt before install leaves only an unreachable empty scope. | `scoped` composition | `test_source_scoped_restores_the_context_before_running_finalizers` |
| S8 | **Parallel with several finalizers overlaps them.** Newest first, each is started in a daemon fiber that inherits the closing fiber's mask (`forkUnsafe(..., true, true, "inherit")`, :4089-4095); `await_all` returns their Exits in that order for S4. A finalizer keyed by the closing fiber (its own `fork_in`/`run_in` registration) is skipped, as upstream's `key !== parent` (:4090-4093), since running it elsewhere would wait for the closer itself; only the parallel branch skips, as upstream. | `_close_finalizers` (`fork_daemon(start_immediately=True, uninterruptible="inherit")`, then `await_all`; `key is not closing`) | `test_fork_in_does_not_wait_for_the_fiber_that_closes_a_parallel_scope` (`Effect.test.ts:3073`), `test_parallel_executes_finalizers_in_parallel`, `test_parallel_completes_direct_close_after_interruption`, `test_parallel_continues_and_awaits_remaining_finalizers_after_a_throw`; `test_source_parallel_finalizers_inherit_the_close_mask` (source-derived) |

**Not in the formal model.** `verify-formal-kernel` does not model Scope. It changes no kernel
node: it only composes public combinators the kernel already has.

## Resources

`resources.py` composes Scope and public kernel APIs. Pinned upstream `internal/effect.ts`
:4240-4269 and :4502-4515; tests in `tests/test_resources_upstream.py`.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| R1 | **Acquisition stays masked through cleanup registration.** Failure or defect in acquisition registers nothing. | `uninterruptible` around lookup, acquire and Scope registration | `test_acquire_release_releases_on_interrupt`, `test_source_acquisition_failure_never_uses_or_releases` |
| R2 | **Scoped release/finalizer services are captured at acquisition/registration.** Captured services override close-time services, with the closing Exit passed through. | read-only `WithFiber` context capture, `update_context` merge | `test_acquire_release_supports_release_dependencies`, `test_source_finalizer_captures_registration_context_and_close_exit` |
| R3 | **Use inherits interruptibility; acquisition and release are masked.** A thrown use callback still releases once. | `uninterruptible_mask`, suspended use below `on_exit` | the interrupt, uninterruptible, inherited-status, interrupted-use and thrown-use resource tests |
| R4 | **Use values and acquisition/use/release errors are preserved.** Combined failures keep upstream cause order and defect election. | existing `on_exit` runtime; generalized error annotations only | single-stage error, combined failures and scoped finalizer failure resource tests; positive and negative typetests |
| R5 | **Scope and captured release dependencies remain caller requirements.** `scoped` removes only Scope; acquire/use/release also exposes release errors. | resource API signatures | `typetests/should_pass.py`; negative blocks 23-29 |

Only default masked `acquire_release` acquisition is supported; the optional interruptible
acquisition mode has no API yet. No kernel runtime change, and no new formal-model claim.
Independent review and integration remain the coordinator's gate.

## Deferred and fiber identity

`deferred.py` follows pinned `Deferred.ts:151-193, :881-894`, using only public `async_`,
Effect/Exit constructors, `sync`, and `fiber_id`. The id effect is a lazy `WithFiber` read in
`core.py`, matching `internal/effect.ts:1196-1204`; it adds no run-loop state or transition.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| DF1 | **First completion wins and stores the Effect itself.** Each await evaluates it independently. | `done_unsafe` checks `effect` before mutation; `await_` resumes with it | success/failure ports; source-derived first-completion and lazy-replay tests |
| DF2 | **Completion detaches waiters before notification.** Reentrant completion returns false; every captured callback receives the original Effect. | `done_unsafe` clears `resumes` before its synchronous loop | interrupt-all-waiters port and source-derived callback test |
| DF3 | **Interrupted waiters remove only their own callback.** Cleanup after completion does nothing. | `async_` cancellation effect checks current `resumes` before removal | suspended-waiter removal and post-completion interruption ports |
| DF4 | **The cell's A and E are invariant; stored Effects need no environment.** | `Deferred[A, E]` fields/methods and completion signatures | positive typetests; negative blocks 30-41 |
| DF5 | **Interruption records the completing fiber's id.** The reusable `fiber_id` effect reads each executing fiber rather than caching an id. | `interrupt` composes `fiber_id`; kernel `WithFiber` + `succeed` | current-fiber interruption and parent/child id source tests |

Only the tested constructor/await/completion subset is supported. Broader Deferred APIs remain
unported. The :149 port uses a one-second harness watchdog instead of virtual TestClock timing;
callback tests independently pin synchronous notification. Deferred is not formally modeled;
the existing protocol model is unchanged. Independent review and integration remain pending.

## Public fiber bulk operations

Pinned `internal/effect.ts:876-908, :992-1003, :5614-5637`. The private supervision wait
remains separate; callers outside the kernel need no new B3 access.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| FB1 | **Await preserves input order and target Exits.** External cancellation interrupts the waiter, removes its current observer, and leaves targets running. | `await_all`: one iterator, completed-fiber loop, current subscription cleanup | ordered Exit and interrupted waiter tests; cleanup mutation |
| FB5 | **Iterator failures affect only the waiter.** They must not escape through target publication, overwrite its Exit or strand the waiter. | iterator advancement catches exceptions and resumes with a defect | pending-target iterator failure regression; independent initial/completed-prefix probes |
| FB2 | **Interrupt signals all targets before waiting.** The iterable is consumed once; interrupt causes use the executing caller's id. | `interrupt_all`: signal/collect loop then `await_all` | one-shot upstream port, cleanup gate and exact causes; sequential-signaling mutation |
| FB3 | **Mask inheritance is explicit and precedes startup.** Defaults stay interruptible; daemon workers remain unsupervised. | fork options and `_start` mask assignment before drive/scheduling | both fork API tests, scheduled-start probe; inherit/default mutations; extended protocol model |
| FB4 | **Bulk success contains typed Exits; worker requirements survive fork.** Unsupported mask options are rejected. | public generic signatures and Literal option validation | positive typetests and negative blocks 42-49 |

`fork` and `fork_daemon` still reject boolean masks. `fork_in` takes `True`, `False` (interruptible, not inherited) or `"inherit"`, as forkUnsafe does (:5622); see L4. The kernel model covers inherited startup
and the existing interrupt/observer protocol, not bulk ordering or multiple-target semantics.

## Fiber lifecycle

Pinned `internal/effect.ts:5690-5737, :5773-5822, :4077-4101, :650-660`. The kernel
(`fiber.py`) holds the ordering through `run_in_owner`/`fork_in_owner` over a minimal
`FiberOwner`; the Scope adapter (`scope.py`) owns the finalizers and exposes `run_in`/`fork_in`.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| L1 | **A root runs its first slice before anyone sees it.** `run_fork`/`run_fork_with` evaluate once; `on_fiber_start` runs after that, and only if the fiber is still live (:5782-5796). The `signal` option (:5785-5793) is not ported. `scheduler=` replaces the context's; `uninterruptible=True` masks the root. | `RootRunner.__call__` | the four `test_source_run_fork_*` tests |
| L2 | **`run_in` registers once, newest, and leaves on exit.** An exited fiber is left alone; a closed scope interrupts the fiber with its own id; otherwise the old key is removed, the finalizer re-added, and an observer removes it at exit (:5801-5822). | `run_in_owner` | `Fiber.test.ts:10`, `test_source_run_in_*` |
| L3 | **`fork_in` registers atomically with the start.** One `WithFiber` step forks a daemon (an unstarted fiber if the scope is closed) and, if it is live, attaches it or, if the scope closed meanwhile, interrupts it with the forker's id (:5717-5737). | `fork_in_owner` | `Effect.test.ts:3039` ×2, `:3057`, `test_source_fork_in_*` |
| L4 | **`fork_in` masks follow forkUnsafe.** `"inherit"` copies the forker's mask; `True` masks; `False` starts interruptible even under a masked forker (:5622). | `fork_in_owner` mask line | `test_source_fork_in_boolean_and_inherited_masks_under_a_masked_parent`, `test_source_fork_in_uninterruptible_starts_the_child_masked` |
| L5 | **The scope finalizer never waits for its own closer.** It interrupts and awaits the fiber unless that fiber runs the close (:5690-5693). | `_fiber_finalizer` | `Fiber.test.ts:22` |
| L6 | **`parent` is the live supervisor.** Cleared, with the parent's `children` entry, before exit observers run (:652-655). | `Fiber._publish` | `test_source_parent_is_*` |

Mutation matrix (18 mutants, scratch copies): 16 killed, each by its named test. Two survive
and are inferred equivalent: skipping `run_in`'s exited check, or `fork_in`'s `_exit` check before
attaching, is undone by `add_observer` firing at once on an exited fiber and `interrupt_unsafe`
being a no-op on one. The formal model is unchanged (verify-formal-kernel Sync line).

## ManagedRuntime

Pinned `ManagedRuntime.ts:325–474`; implementation in `managed_runtime.py`.
Independent review passed 54 focused tests covering disposal, interleavings, typing and boundaries.

| # | Contract | Enforcement and evidence |
|---|---|---|
| MR1 | Masking and synchronous invocation of the mask body occur in one operation. This does not make execution of the returned Effect atomic. | `uninterruptible_mask`; disposal's before-claim sweep and mask-then-WithFiber mutant |
| MR2 | Disposal decides and claims while masked, in the same operation. An interrupt before the claim performs no disposal. | Outer `WithFiber`, inner `uninterruptible_mask`; exhaustive bounded operation sweep |
| MR3 | The claiming disposer awaits cleanup under its mask and completes the disposal Deferred even if cleanup fails. Completion requires cleanup to terminate. | `_dispose`, `ensuring`; interruption-after-claim and finalizer-defect tests |
| MR4 | Later disposers restore their inherited interruptibility while waiting. | `restore(done.await_())`; interruptible-waiter tests |
| MR5 | Reentrant disposal and managed descendants do not wait on themselves. The claimer and its ancestors detach from the fiber scope. | `_DisposingRuntimes`, parent walk; reentry and ancestry tests |
| MR6 | Runtime hooks precede user hooks. Requests disposing in their first slice skip attachment; build fibers still attach. | Start-hook tests and vendor differential |
| MR7 | Runtime membership does not retain finished fibers. | WeakSets and `Fiber.__weakref__`; retention regression |
| MR8 | Async runners await the existing fiber without creating another. One cancellation interrupts without an interruptor and waits for cleanup; a second cancellation can stop that wait. | `await_exit`; differential tests against kernel `run_exit`. Second cancellation is a Python contract. |
| MR9 | Requirements are contravariant and build errors covariant. | Positive typing fixtures and negative blocks 89–93 |

Disposal is not a universal terminal-state guarantee: captured context effects and a build that
repopulates the cache after disposal preserve upstream's revival quirks. Cleanup waiting on an
unrelated disposer can deadlock, also as upstream. `context()` and `dispose()` use unmanaged roots.
The disposal defect is a Python `RuntimeError`, rather than a string.

Evidence covers 20 vendor scenarios and a bounded manual-scheduler world, including generated
interleavings. Operation counts differ from upstream. ManagedRuntime is outside the formal
kernel model; the author's 20,000-example run is bounded testing, not a proof. Synchronous
runners, callback runners and the signal option remain unported.

## Traversal

Pinned `internal/effect.ts:5030-5112, :5157-5320` (forEach, forEachSequential,
resolveConcurrency, iterateConcurrentImpl). Implemented and independently reviewed, including ordered worker cleanup and cancellation-cause corrections.

**Where it lives.** Two placements were weighed. (a) A non-kernel module over a newly public
`fork_unsafe`: not enough, since the state machine also needs `add_observer`, `interrupt_unsafe`
and `id`, all B3-forbidden, so worker management would leak across the boundary. (b) Chosen: the
state machine lives in `fiber.py`, as upstream keeps it in `internal/effect.ts` beside `forkUnsafe`.
No new public unsafe API; B3 and the boundary checker are unchanged.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| T1 | **Results follow input order**, whatever order workers finish; each run maps afresh. | `step` writes `out[index]`; all state is built inside `Suspend` | `test_bounded`, `test_string`, `test_source_concurrent_results_keep_input_order_when_workers_finish_out_of_order`, `test_source_each_run_maps_afresh_into_its_own_list` |
| T2 | **Concurrency below 1 is 1**; 1 runs sequentially over a lazy iterator; `"unbounded"` has no window. | `max(1, n)`; `_for_each_sequential` reads the next item after a step | `test_sequential`, `test_unbounded`, `test_length_0`, `test_source_concurrency_below_one_and_the_default_run_one_at_a_time` |
| T3 | **At most `concurrency` workers run; a finished worker refills the window.** Ready Exits are stepped inline without forking. | `go` pauses when the set is full; the pausing call's observers resume it | `test_bounded_interrupt`; refill mutation |
| T4 | **The first failure interrupts the rest and waits for them.** Their non-interrupt reasons join the terminal cause, defects elected. | observer fold after `terminal`; `done` and an empty set resume | :852, :882, :933; aggregation and cleanup-wait mutations |
| T5 | **An external interrupt interrupts workers, waits, and keeps a recorded failure.** | the Async canceller: `terminal ??= void`, `interrupt_all`, then the recorded Failure | :819, :834, :852 |
| T6 | **A mapper raising, at start or during an observer refill, is a defect** after started workers are interrupted, masked. | `fail_defect`: `uninterruptible(interrupt_all(...))` then the terminal | :907, :933, :960, `test_source_a_mapper_defect_leaves_only_after_started_workers_are_interrupted` (initial, refill); both failure-path mutations |
| T7 | **Traversal owns daemon workers that inherit the caller's mask and context.** | `_start(None, eff, parent.context, loop, True, parent.interruptible)` | `test_source_workers_inherit_the_callers_mask`; mask mutation |

`discard` and ambient concurrency are not ported. Independent review found two parity gaps:
Python's unordered worker set reordered cleanup causes; the worker collection is now an
insertion-ordered dict. `_AsyncCancel` now combines the original interrupt before a failing
canceller's cause and elects defects, matching `asyncFinalizer` (:1268-1271). Direct kernel
regressions and a traversal regression pin both ordering and election. The four new regression
cases failed before these corrections and pass afterward. Traversal owns daemon workers and
waits for cleanup itself, matching upstream rather than relying on caller supervision.
Traversal and cancellation cause semantics are not formally modeled; the kernel protocol is
unchanged.

## Layer

`src/peffect/layer.py`, outside the kernel: it composes `suspend`, `on_exit`, `WithFiber` (to read
`fiber.context`), `for_each`, `provide`, Deferred and the public Scope API, including
`Scope.add_finalizer_unsafe` (B1–B3). Upstream: `Layer.ts:240-420` (MemoMap, memoMapObserve,
fromBuild, fromBuildMemo), `:600-781` (CurrentMemoMap, buildWithMemoMap, build, buildWithScope),
mergeAll, provide, and `internal/layer.ts:8-22` (Effect.provide with a layer). Implemented
and independently verified by Sol 6.1.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| L1 | **One build per layer object per MemoMap.** Memoized layers key the map by identity; a request during a build waits for the first builder's result. | `Layer._from_build_memo` (self-keyed), `MemoMap.get_or_else_memoize` | :35, :512, :673, :693, :712; identity mutation (13 cases red) |
| L2 | **A requester's scope owns a share from the moment `get` is called**, whether or not the returned effect runs: count and finalizer registration happen together, synchronously. A closed scope owns nothing: the build runs unshared and stores no entry. | `_observe` called inside `MemoMap.get` and before the entry is stored | :606, :624, `test_source_get_takes_ownership_when_called_not_when_run`, `test_source_a_build_on_a_closed_scope_is_unshared_and_released_at_once`; lazy-observer mutation (only the source test red) |
| L3 | **The last observer to close releases.** Each observing scope's finalizer decrements; at zero the entry leaves the map and its scope closes with that scope's Exit. | `_Entry.finalizer` | :583, :673, :693, closed-scope source test |
| L4 | **The first builder publishes its Exit however the build ends**, interruption included, so no waiter or later requester hangs. A failed build closes the layer scope `from_build` forked, which drops that requester's share. | `on_exit(publish)` in `get_or_else_memoize`; `from_build`'s close-on-failure | :557 at three interrupt points; publication mutation (12 cases red) |
| L5 | **Nothing is memoized before the build effect runs.** | all map writes happen inside `suspend` | :638 |
| L6 | **Nested builds share the current map.** `build_with_memo_map` provides `CurrentMemoMap` while building and adds it to the built Context; `build` and `build_with_scope` fork the current map, or make a fresh one when there is none. A forked map reads its parent but never writes to it. | `build_with_memo_map`, `CurrentMemoMap.fork_or_create`, `MemoMap.get` parent fallback | :712, `test_source_a_nested_build_forks_the_current_memo_map`, `test_source_without_a_current_memo_map_each_build_gets_a_fresh_one` |
| L7 | **`merge_all` builds its layers concurrently**, each in a sequential child of one parallel scope. | `merge_all` (`for_each`, `concurrency=len(layers)`) | :15 |
| L8 | **`provide_layer(layer)(program)` scopes the layer to the program.** A fresh scope that is not put in the context, closed with the program's Exit; the built Context (including `CurrentMemoMap`) is provided. | `provide_layer.__call__` (scopedWith, effect.ts:4231) | `test_source_provide_layer_scopes_the_layer_to_the_effect` |
| L9 | **`Layer[ROut, E, RIn]` keeps every channel honest.** ROut is contravariant and E and RIn are covariant (inferred, as upstream's `in ROut, out E, out RIn`). Constructors remove only Scope from an effect's R; `provide` removes only what the provider supplies and adds its E; `build` adds Scope; a wrong service or tag is rejected. Only Layer's constructors make a Layer (private token), and its builder is `Final`. | PEP 695 variance, overloads, `_Token`, `Final` | should_pass `layer_types`, `layer_variance`; should_fail 54–70 |

**Limits.**
- `Final` on `_builder` is enforced by the type checker only, since a slot can still be assigned
  at runtime. The constructor token is also checked at runtime.
- `merge_all` is typed for 1 to 4 layers; zero layers, or five or more, are a type error.
  Pyright loses a union whose members all solve to `Never` (`resources.py:70`), so each arity
  covers the all-`Never` E and R channels before the general overload.
- Not ported: `Layer.fresh`, `suspend`, recovery, tracing and `provide`'s `local` option.
- The :557/:583 op-budget sweep is reduced to named interrupt points, so this is partial
  coverage.
- Layer is not in the formal model, and the kernel is unchanged.

## Racing and timeouts

Pinned `internal/effect.ts:1688-1840` (raceAll, raceAllFirst, interruptRaceLosers, race,
raceFirst) and `:3929-4007, :6499` (timeoutOrElse, timeout, timeoutOption, TimeoutError). The
race state machine is `_race` in `fiber.py`, beside `for_each`, for the same B3 reason: it needs
`_start`, `add_observer` and `interrupt_unsafe`. `timeout.py` is non-kernel and composes
`race_first` with `sleep`. Data-first only.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| RA1 | **Racers are daemons, started at once, always interruptible, in the caller's context.** | `_start(None, eff, parent.context, parent, True, True)` | `test_source_racers_see_the_callers_context_and_run_as_daemons`, `test_source_a_masked_race_still_interrupts_its_loser_and_finishes_first`, :2268, :2281; mask mutation (detected as a deadlock, see UPSTREAM_TESTS) |
| RA2 | **Winner mode.** `race`/`race_all`: first success wins; `race_first`/`race_all_first`: first exit wins. | `first_exit or isinstance(exit_, Success)` | :1430, :1455, property `test_source_race_matches_the_model_for_any_mix_and_completion_order`; both winner-mode mutations |
| RA3 | **All failed: reasons in completion order, defects elected.** | `failures` appended by observers; `Cause(...).elect_defects()` when `settled >= total` | :1449, the property; order mutation |
| RA4 | **A racer that settles the race while racers start stops later forks.** `race_all` checks after each fork (it read the iterable first); `race_all_first` checks before each fork and reads lazily. | the two `break`s; `list(effects)` only for `race_all`; close lazy iterator on early break | :1534, :1552, the property, `test_source_how_far_each_race_reads_a_lazy_iterable`; both cutoff mutations and the eager-iteration mutation; `test_race_review_regressions.py` pins cleanup and a throwing close |
| RA5 | **The winner resumes the race before losers are interrupted**, by the racing fiber's id, so a loser's interrupt cannot settle the race. | `win`: `resume` then `interrupt_unsafe(parent.id)` | :1475, :1494, :1513, :1572; loser-interrupt and order mutations |
| RA6 | **The race returns only after live losers are interrupted and awaited, masked.** An external interrupt interrupts every racer. | `OnExit(Async(register), cleanup)`, `cleanup` = `interrupt_all(live)` | `test_source_the_race_returns_after_loser_cleanup_and_masks_that_wait`, `test_source_interrupting_the_racing_fiber_interrupts_every_racer`; await mutation |
| RA7 | **`timeout_or_else` calls `or_else` lazily, on the caller's fiber, after the source stopped.** | `flat_map(choose)` over `timeout_option` | :2054-:2209, `..._external_interruption_stops_a_pending_fallback`; eager-fallback mutation |
| RA8 | **`timeout` decodes its duration when called and fails with a fresh `TimeoutError("Operation timed out after '<format>'")`.** `timeout_option`/`timeout_or_else` decode when run. | `from_input_unsafe` in `timeout`; `Duration.format` port | :2289, :2304, :2317, `test_source_invalid_durations_fail_where_upstream_decodes_them`, `test_duration_format_upstream.py` |
| RA9 | **Typed channels join**: one type variable per channel, so pyright widens A, E and R to both sides' unions (`Never` with `Never` stays `Never`); `timeout` adds `TimeoutError`, `timeout_option` wraps in `Option`. | signatures | typetests `race_types`, `timeout_types`; should_fail 106-113 |

**Formal coverage.** The race adds no kernel transition: it reuses `_start`, observers, `Async`
resume, `OnExit` and `interrupt_all`, and writes none of the modeled fields. Racers are
parentless daemons, outside the model's parent/child scope. The modeled protocol (park, wake,
interrupt, mask) is proven by `verify-formal-kernel`; the race's own ordering (RA1-RA8) is
proven only by runtime tests, the 966-run vendor-replayed property model and the mutations.

## Coordination and composition

Latch and Semaphore follow the pinned implementations using public primitives. Their runtime
and generated-operation tests live in `test_latch_upstream.py` and `test_semaphore_upstream.py`.

| # | Contract | Enforcement and evidence |
|---|---|---|
| CO1 | Opening a Latch swaps its waiter batches before notifying them; cancellation removes a waiter from either batch. | Latch methods; operation-sequence property and batch/cancellation regressions |
| CO2 | Semaphore permit return and wake enqueue happen in one synchronous exit callback. | `on_exit_with_dispatcher`; forced-yield wake/cancel regression and callback timing tests |
| CO3 | An interrupted queued take leaves no stale entry; an acquired waiter leaves the queue. | Exit frame installed before queue insertion; operation-boundary interruption/yield tests |
| CO4 | Semaphore conservation holds for balanced `with_permits` use at fixed capacity. Overtaking is permitted. | Generated holder/interrupt sequences; `release_all`, resize and bare take have separate contracts |
| CO5 | `Some` is immutable and covariant, and distinguishes `Some(None)` from absence. | Runtime tests and positive/negative typing fixtures |

`on_exit_with_dispatcher` runs synchronous bookkeeping while the exit frame is popped and
masked, with that fiber's dispatcher. Exceptions combine like failing finalizers. Use
`on_exit` or `ensuring` for effectful cleanup. Coordination is not covered by the formal model.

`Effect.pipe` and `Layer.pipe` accept zero to twenty typed steps. `tap` preserves the success
value and adds the callback's errors and requirements. The method checks its argument type;
the pipeable callback parameter is `Any`, a documented Python typing limitation.
Multiple `catch_tag` or `Layer.provide` steps in one pipe can retain extra errors or
requirements in the inferred type. Chained single-step pipes narrow precisely.

Python generators are closed on failure so ordinary `finally` blocks execute. If closing
raises (including yielding from `finally`), the original cause and cleanup defect are combined
with defect election, preserving interruption. This is a Python adaptation, not JavaScript
generator parity. Use effect finalizers for effectful cleanup. `test_gen_cleanup.py` pins it.

## TestClock, Console, Logger, fork_scoped

`src/peffect/testing/_clock.py`, `testing/_console.py`, `console.py`, `logger.py` and
`scope.fork_scoped`, all over public primitives: no kernel module changed. Upstream:
`testing/TestClock.ts`, `testing/TestConsole.ts`, `Console.ts`, `internal/effect.ts:6584-6634,
:6725-6760, :6810-6813, :6984-7003`.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| TC1 | **Finite deadlines wake when reached; a NaN target drains pending sleeps, matching upstream.** `sleep` inserts before equal deadlines in a descending list and waits on its own latch; an interrupted sleep removes its entry, so a later `adjust` skips it. | `_TestClock.sleep` (acquire_use_release) | `test_sleep_delays_effects_until_time_is_adjusted`, `test_source_s2_equal_deadlines_wake_in_sleep_order`, `test_adjust_skips_the_cancelled_deadline_*` |
| TC2 | **`adjust` waits for woken work to settle, bounded by the busy-warning timeout.** Under one run semaphore, after a fork-and-await barrier, each due sleep advances the clock to its deadline and opens its latch with tracking on; `adjust` then waits until every task the wake scheduled has run or the busy-warning timeout expires (or, on a clock without its Scheduler, yields once). | `_run_body`, `_wake`, `_TrackingDispatcher` | `test_adjust_waits_for_woken_fibers_*`, `test_adjust_runs_sleeps_registered_after_a_yield`, `test_source_s3_*` |
| TC3 | **Tracking is restored and counted even when a tracked task raises.** `run_tracked` and the dispatcher's decrement are `try/finally`. | `_State.run_tracked`, `_TrackingDispatcher` | `test_source_s12_a_raising_tracked_task_still_settles_the_wake_and_restores_tracking` |
| TC4 | **Exact time arithmetic.** Wall nanos floor the fraction (`millisToNanos`), monotonic adds `Math.round` (half up) and never goes back; non-finite steps leave nanos alone; `adjust` keeps the exact nanos of a Duration. | `_millis_to_nanos`, `_js_round`, `_State.advance_to` | `test_source_s5_*`, `test_source_s1_*`, `test_source_s6_*`, `test_source_s7_*`, `test_adjust_preserves_precision_*` |
| TC5 | **One warning per clock, on the live clock and console, owned by the first sleeper.** The warning fiber is a child of the first sleeping fiber (interruptible), stopped by `adjust`/`set_time` and by the `make` scope's finalizer. The busy warning is a `timeout_or_else` on the live clock. | `_warning_start`, `warning_done`, `_make_with_scheduler` | `test_warnings_bypass_test_console`, `test_source_s4_*`, `test_source_s10_*`, `test_source_s11_*`, `test_source_s13_*`, `test_source_s14_*` |
| TC6 | **A copy shares the clock.** `copy.copy` copies the one-slot wrapper; the state object is shared, as upstream's object spread shares its closures. | `_TestClock.__slots__ = ("_s",)` | `test_adjust_advances_the_clock_when_detached_or_copied` |
| CO1 | **Console is a Reference with the upstream key.** Using it never adds to R; the live console prints log/info/debug to stdout, warn/error to stderr. TestConsole records every call, flattened per method; each layer build is fresh. | `console.Console`, `_TestConsole` | `test_reference_keys_match_upstream`, `test_live_console_routes_*`, `test_test_console_*`, `test_each_test_console_layer_build_starts_empty` |
| LG1 | **A line goes to each distinct logger once, first registration first, unless below the minimum.** Emitted levels use the six-value Severity domain; All/None are filters only. With no loggers, time is not read. Otherwise the time is the fiber Clock's, read once per line; the header is `[HH:MM:SS.mmm] LEVEL (#fiber):` in local time, written to the fiber's Console (`error` when LogToStderr). | `log_with_level`, `_default_log` | `test_a_logger_registered_twice_logs_once_*`, `test_minimum_log_level_filters`, `test_default_logger_header_matches_upstream`, `test_log_to_stderr_*` |
| FS1 | **`fork_scoped` forks into the ambient Scope.** The fiber outlives its parent and is interrupted when the scope closes; a closed scope runs nothing. | `scope.fork_scoped` = `scope.flat_map(fork_in)` | `tests/test_fork_scoped_upstream.py`; typetests should_fail 114 |

**Not proven.** None of this is in the formal kernel model. Ordering claims rest on the tests
above and the production mutation run; warning-window tests count what was printed in a bounded
live window, not that nothing prints later.

## Kernel boundary

Only the kernel (`src/peffect/core.py`, `fiber.py`, `cause.py`, `__init__.py`) knows about fibers
and run-loop nodes. Everything else in `src/peffect/` and `examples/` uses the public surface,
plus one escape hatch, `WithFiber`, for library code that must read the running fiber. Many
kernel internals have no underscore (`Async`, `Frame`, `Park`, `Fiber.stack`, `Fiber.running`),
so the type checker's private-name rule doesn't cover them. `tests/` and `typetests/` are
white-box and exempt.

| id | must stay true | enforced at |
|---|---|---|
| B1 | **Imports are public.** Outside the kernel, a `peffect` name is imported from `peffect`, or from the kernel module that defines it (as re-exported in `__init__.py`: `from peffect.core import Effect`, relative `from .core import Effect`), or is `peffect.core.WithFiber`. Module-qualified kernel imports (`import peffect.core`, `from peffect import core`) and star imports from a kernel module are rejected; import the names. | `scripts/check_boundaries.py` |
| B2 | **No reach through the module.** `peffect.X` (any alias of the module) names something in `peffect.__all__`. | same |
| B3 | **Fibers stay opaque.** On a Fiber, code outside the kernel uses only `context`, `current_op_count`, `parent`, `await_`, `join`, `interrupt`: the data a service lookup needs, the operation count custom schedulers read in `should_yield` (upstream Fiber.ts:77), the live supervisor a runtime's disposal walks (ManagedRuntime.ts:404; read-only, `None` once exited), and the three effects the kernel's own tests and the example use to wait on or stop a fiber. Run-loop state (`stack`, `running`, `interrupted`, `interruptible`, `children`, `exit`) and unsafe primitives (`interrupt_unsafe`, `take_interrupt`, `add_observer`, `swap_context`) stay inside. `context` is a read-only property over a read-only map: assigning or deleting it is a pyright error and an `AttributeError`, writing an item a `TypeError` (`typetests/should_fail.py` 12–14, `test_fiber_context_is_read_only_and_owned`), so the lint checks reads only. This list is a specification: a new public Fiber member is a deliberate edit to `FIBER_PUBLIC`. | same |

**What B3 can see.** A value counts as a Fiber when it is the first parameter of a lambda or a
named `def` passed to `WithFiber` (or an alias of it, directly or as `f=`), or a parameter
annotated `Fiber` / `Fiber[...]` / `peffect.Fiber[...]`, including string annotations. Names
resolve lexically, so a same-named parameter in an inner function, or in a function not handed to
`WithFiber`, is not a Fiber. Passing anything other than a lambda or a named def to `WithFiber`
fails the lint (the callback can't be checked), and so does `getattr`/`setattr`/`delattr`/`vars`
on a known Fiber.

**What it can't see.** A Fiber passed into an unannotated helper, stored in a container, closure
or attribute, or returned from a call; a name rebound anywhere in its scope (scopes are read
flow-insensitively, and comprehension variables count as the enclosing scope's); imports through
`importlib` or `__import__`. Pyright strict requires annotations in `src/`, which closes most of
the helper gap. The lint parses source and never imports it.

**Proof:** `tests/test_boundaries.py` drives the CLI on probes in a temporary `peffect` package:
every forbidden line is reported exactly, every allowed spelling passes, a kernel lookalike path
is still checked, and unreadable input exits 2. Each of these checker breaks turns it red:
B3 attribute check removed, kernel exemption by basename, WithFiber callbacks not tracked, B1
kernel-module check removed (2026-11-15).

Definition defaults, decorators, and class bases are checked in their enclosing scope. Export
metadata must be a single literal list/tuple of strings; computed or mutated declarations fail
with exit 2. CLI regressions cover both rules (2026-11-15 independent review corrections).

## Ref and SynchronizedRef

| ID | Contract | Enforcement | Evidence |
|---|---|---|---|
| RF1 | Each pure cell operation reads, computes and assigns in one synchronous effect step. Raising callbacks assign nothing; user in-place mutations are not rolled back. | Ref sync bodies | test_ref_upstream.py; sequential model property |
| SR1 | Pure and effectful writes hold one semaphore permit; effectful callbacks read after acquisition. | with_permit and suspend | queued set, FIFO, laziness and mutual-exclusion tests |
| SR2 | Only a successful callback continuation assigns. Interruption before assignment leaves the slot unchanged; an interrupted exit can follow a completed assignment. Permit cleanup always runs. | success continuation and Semaphore finalization | four operation-by-operation sweeps; concurrent model property |
| SR3 | Reads are unlocked; writes are not reentrant. | get bypasses semaphore | unlocked-read test; manual-scheduler quiescence regression |

Both classes are invariant. Explicit generic classmethod constructors are required by strict
Pyright; this is not runtime validation. Some(None) is a present replacement. Evidence is
bounded runtime/property testing, not formal verification.

## Atomic delivery and Pull completion

Queue's prerequisites at Effect v4 pin `289a53f7`; Queue itself is a later unit.

| ID | Contract | Enforcement | Evidence |
|---|---|---|---|
| QP1 | A lazy thunk returning a terminal Exit delivers it to its first continuation in the same operation. A pending interrupt or budget yield cannot occur between removal and that delivery. Later interruption is allowed; this is not rollback. | Kernel `suspend_exit`, folding final Success/Failure nodes; nonterminal nodes delegate normally | `test_suspend_exit.py`: sync sensitivity control, terminal success/failure, reentrant interrupt, 16-index operation sweep, lazy reuse, async delegation, raising thunk |
| QP2 | Done is an ordinary Fail payload with a covariant, read-only leftover. The default void value is shared; Python None represents void. It adds no Cause reason or runtime protocol. | `cause.Done`, `is_done`; `pull.done` | `test_pull_upstream.py`; positive and negative prerequisite type fixtures |
| QP3 | A completion filter succeeds with the first Done only when no non-Done failure/defect exists. Interrupt reasons alone do not prevent completion. Mixed real failures retain their order and interrupts, with every Done stripped. Without Done, the original Cause is retained. | `pull.filter_done`, `done_exit_from_cause`, `catch_done` | Six Pull.test.ts ports plus mixed-reason, first-None, handler-failure and actual-interruption cases |
| QP4 | Handling Done removes it from the error channel while preserving ordinary errors, handler errors and both environments. Filters retain the leftover type. | Exact-Done-first overloads, shared environment type variable, bottom-channel overloads | `queue_prerequisite_type_contracts`; should_fail 127-133 |

Python uses Exit as the filter's data carrier because Result is not ported. No Pull wrapper
is introduced. Only the completion prefix is implemented; the remaining Pull operators are
deferred to the Stream lane. `catch_tag(Done, ...)` does not implement QP3: it may recover a
Done while discarding an ordinary typed failure merged with it.

The formal kernel model abstracts ordinary computation and continuation values. It does not
model QP1's message ownership, Done payloads or completion filtering. `suspend_exit` delegates
to existing Success/Failure evaluation and introduces no independent write to the protocol
fields. Protocol reruns and break sensitivity accompany runtime checks; a TLC pass is not a
proof of Queue no-loss behavior. The reentrant test captures its fiber with WithFiber:
`on_fiber_start` observes only a fiber still live after its initial evaluation slice.
