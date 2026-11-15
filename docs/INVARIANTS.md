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
| parked on an Async | `Fiber._parked` + `Park.done` | `Async._eval` (park), `resume` / `_interrupt_parked` (flip done) |
| run loop active | `Fiber.running` | `_drive` only |
| result | `Fiber._exit` | `Fiber._publish` only |
| live children | `Fiber.children` | `_start` (add), child `_publish` (discard) |
| body's exit while children finish | `Fiber._ending` | `Fiber._done` only |
| services in scope | `Fiber._context` (read-only map; public read via the `context` property) | `Fiber.swap_context` only, called by `update_context` (install) and `RestoreContext.on_any` (put back) |

Derived: `Fiber.exit` (property), `Cause.is_interrupted` etc. Nothing is cached.

## Transitions

| transition | trigger | precondition | write | code |
|---|---|---|---|---|
| start | `fork` / `fork_daemon` / `run_exit` | — | default or inherited mask installed before startup; child in `parent.children` (not for a daemon); one `_drive` scheduled, or with `start_immediately=True` run now, nested in the caller's drive, until it parks or ends (v4 forkUnsafe, internal/effect.ts:5614-5637) | `fiber._start` |
| step | run loop | `running` | eval one node; maybe push/pop frames | `Fiber._drive` |
| park | `Async._eval` | `register` didn't resume synchronously | `_parked = park`; push `_AsyncCancel` if cancel | `Async._eval` |
| resume | callback from asyncio / another fiber | `not park.done` | `park.done = True`; schedule `_drive(eff)` | `Async._eval.resume` |
| interrupt | `interrupt_unsafe(by)` | not finished | add to `interrupted`; parked+interruptible → wake with Failure, else `_deferred` | `Fiber.interrupt_unsafe` |
| deliver | loop top / `SetInterruptible` pop / `interruptible()` / unwind / parked wake | interrupt pending and interruptible | `take_interrupt()` clears `_deferred`; the node becomes (or the unwinding cause gains) `interrupted` | `Fiber.take_interrupt` |
| unwind | `Failure._eval` while interrupted+interruptible | — | pop handlers until a frame masks (OnExit, AsyncCancel); `exit_`'s `ExitFrame` is popped like any handler, so it never turns its own fiber's interrupt into a value (v4 exitFailCause, internal/core.ts:611-624) | `Failure._eval` |
| finish | stack empty | — | remember the exit in `_ending`; interrupt live children; park on `_await_all` *inside the run loop* (so an interrupt during the wait is delivered and merged into the exit); publish once no child is live | `Fiber._done`, `_await_all`, `_publish` |

## Invariants

| id | must stay true | enforced at |
|---|---|---|
| K1 | **Exit once.** A fiber publishes exactly one Exit; every observer is called exactly once (late observers immediately). | `_publish` (single call site: `_done`), `add_observer` |
| K2 | **Resume once.** An Async continues its fiber at most once, whether resume or interrupt wins. | `Park.done` |
| K3 | **One runner.** At most one `_drive` is scheduled or running per fiber. | every scheduling site goes through park/resume or yield |
| K4 | **Finalizers run.** Every `OnExit` frame that was pushed runs its finalizer exactly once before the fiber publishes. | `OnExit.on_any` masks as it pops, stopping the unwind |
| K5 | **Interrupts aren't caught.** A user error handler never runs with an interrupt in flight while the fiber is interruptible. | `Failure._eval` skip loop |
| K6 | **Masks hold.** No interrupt is delivered while `interruptible` is False. | every delivery site checks `interruptible` |
| K7 | **Interrupts land.** A requested interrupt is delivered at the next interruptible point; if the fiber reaches one before finishing, its cause contains `Interrupt`. A worker that stays masked through completion need not deliver it. | `_deferred`, `SetInterruptible`, `_drive` tail |
| K8 | **No orphans.** A fiber publishes only after every child it forked has published. | `_done` |
| K9 | **Causes don't lose reasons**, except: the typed `Fail`s of a skipped handler's region are dropped on interrupt (v4 `core.ts:601-629`). Stronger than v4 in two places: a defect unwinding out of a mask, and a body's exit when an interrupt lands while waiting on children, keep their reasons (v4 replaces them with the interrupt). A failing or raising finalizer adds its cause to the body's, then defects are elected as v4 does (`combineFinalizerCause`, `causeElectDefects`, internal/effect.ts:4069-4075, 184-191): with a `Die` present, each `Fail` becomes a `Die` holding the same value, so a `Die`'s defect is any value. | `Cause.__add__`, `Cause.elect_defects`, `OnExit._finalizer`, `OnExit.on_err`, `Failure._eval`, `Fiber._done` |
| K10 | **Stack safety.** Composition depth never becomes Python recursion depth. | nodes return the next node |

## Proof strategy

| claim | how | where |
|---|---|---|
| K1, K2, K3, K6, K7, K8 (orderings between drive, resume, interrupt, child exit) | TLA+ model of one fiber + one child + async callbacks + an interrupter, every interleaving | `verify-formal-kernel` |
| K4, K5, K9 over arbitrary programs | Hypothesis: random effect trees, interrupted at random scheduler ticks, on the real kernel; oracle = an independent recursive reference interpreter for the no-interrupt case, and trace rules for the interrupt case | `tests/test_kernel_props.py` |
| K10 | 100k `flat_map` example | `tests/test_kernel.py` |

Atomicity assumption the model relies on: asyncio runs one callback at a time and `_drive` never
awaits, so one `_drive` call is atomic with respect to other fibers and callbacks. The one exception
is code a node calls synchronously inside the drive (an Async `register`, a `Sync` thunk), which can
call `interrupt_unsafe` on the running fiber: the model includes that as "interrupt while running".

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

## Context

`update_context(eff, f)` (kernel, `core.py`) and `RestoreContext` own every change to a fiber's
services; `context.py` (Tag, Reference, Context, `provide`) is built on them and on `WithFiber`.

| id | must stay true | enforced at | proof |
|---|---|---|---|
| C1 | **Restored on every exit.** After `update_context(eff, f)` ends (success, failure, defect, interrupt while parked), the fiber's context is the one from before. | `RestoreContext.on_any` (`_pop` runs `on_any` on every popped frame, including skipped ones) | `test_restored_after_success_failure_and_defect`, `test_nested_provide_layers_and_unwinds`, `test_restored_when_interrupted_while_parked_inside` |
| C2 | **No window.** The swap and the restore frame push happen in one `WithFiber` callback, so no interrupt lands between them; if `f` raises, nothing changed. | `update_context.go` | `test_interrupt_at_install_still_restores` (the interrupt is requested inside `f`, and the test checks the new map was installed and the body never ran), `test_raising_update_changes_nothing` |
| C3 | **Owned snapshot.** The fiber holds a read-only copy: later writes to the caller's mapping don't show, and the fiber's map, its `context` attribute and its deletion all refuse writes. A forked child starts with the parent's map at fork time and can't change the parent's. | `MappingProxyType(dict(...))` in `update_context`; `Fiber.context` property; `fork` passes `p.context` | `test_fiber_context_is_read_only_and_owned`, `test_child_inherits_context_at_fork_and_cannot_leak_back`, `test_root_starts_empty`; should_fail 12–14 |
| C4 | **Reference default: once per class, lazy.** Built on first unprovided use, cached in that class's own `__dict__` (never inherited), shared by every fiber and root; an override never builds it. | `Reference._find` | `test_reference_default_built_once_across_roots_and_override_restores` |
| C5 | **A raising default is a defect and is not cached**; the next use calls it again. | `Reference._find` (the raise escapes before the cache write; `WithFiber` turns it into a defect) | `test_failed_default_is_a_defect_and_not_kept` |
| C6 | **Concrete tags are final.** Lookup is by exact class, and R is covariant, so a parent-tag context would type-check against a child-tag request it can't answer. Subclassing any tag other than `Tag`/`Reference` raises `TypeError`; `Tag`/`Reference` themselves build no context. | `Tag.__init_subclass__`, `Tag.context` | `test_concrete_tags_cannot_be_subclassed`, `test_base_tags_build_no_context` |
| C7 | **A `Context[T]` holds a service for every tag in T.** No public constructor (a private token); built only by `Tag.context` and `\|` (right side wins); its `services` view is read-only. Contravariant in T. | `Context.__init__`, `Context.__or__` | `test_context_has_no_public_constructor_and_merges_right_wins`, `test_merged_context_right_side_wins_at_runtime`; should_fail 10–11 |
| C8 | **A missing service is a `MissingService` defect**, never `None`. With C7 and the R typing, only an `update_context` that drops services can reach it. | `Tag._find` | `test_missing_service_is_a_defect` |
| C9 | **Empty identity and Context comparison.** `Context.empty()` is one shared instance; `a \| empty` and `empty \| a` return `a` itself. Two Contexts are `==` when they have the same keys and each value matches by identity, NaN equality, recursive list/tuple/dict comparison (cycles allowed), or Python object equality, in any order; anything that is not a Context is unequal; the hash is the size (Context.ts:637-649, 744-745, 1192-1193). | `Context.empty`, `Context.__or__`, `Context.__eq__`, `Context.__hash__` | `tests/test_context_upstream.py`: `test_supports_equality`, `test_merges_with_right_bias_and_preserves_empty_operand_identity`, `test_preserves_insertion_order_for_replacements_and_appends` |

**R typing** (`provide`, two overloads, exact match first). Python has no `Exclude`, so:

| R of the program | after `provide(Context[T])` | pinned by |
|---|---|---|
| exactly T, or a part of T | `Never` | should_pass (full, over-provided) |
| a union naming T's tags plus others | the others | should_pass (partial 2→1→0, overlap) |
| a bare type variable `R` | `R`, kept (sound, not narrowed) | should_pass `keeps` |
| `Users \| R` with `Users` provided | `R` | should_pass `subtracts` |
| still non-empty at `run` / `run_exit` | type error | should_fail 5–7 |
| wrong service type, wrong Reference override, context claiming more than it holds, forged `Context` | type error | should_fail 8–11 |

**Limits.** Finality (C6) is a runtime check: pyright accepts `class Admins(Users)` and only
the class statement raises. `Tag[str].context(...)` type-checks and raises at runtime. Overloads
are checked with pyright only, not mypy. Providing a Reference override leaves R unchanged
(should_pass, `Level`).

**Sensitivity** (scratch breaks, 2026-11-15, each restored byte-identical): no restore frame
(6 tests red), swap and frame push in two steps (interrupt-at-install red), no snapshot copy
(read-only test red), fork drops the context (2), no default cache (2), subclassing allowed (1),
merge with left side winning (2), public `Context` constructor (1), a miss returns `None` (1); the
partial overload returning `Never` turns `test_types` red.

**Not in the formal model.** `verify-formal-kernel` does not model context. C1–C8 rest on the
tests above. Its SKILL.md argues why the model's pass still holds with `RestoreContext` added: the
frame returns nothing and touches no modeled field.

## Clock

`src/peffect/clock.py`, a leaf: it imports `Reference` and the public `async_`, and the kernel
does not import it. Upstream: `internal/effect.ts:6395-6486`.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| CL1 | **Sleep goes through the fiber's Clock.** `sleep(s)` is `clock_with(lambda c: c.sleep(s))`; a provided Clock replaces the live timer for everything inside its `provide`. | `clock.sleep`, `clock_with` | `test_sleep_uses_the_provided_clock`, `examples/clock.py` |
| CL2 | **Non-positive or NaN is a yield, with no timer.** NaN follows `Duration.make` (Duration.ts:465: NaN becomes zero) and then `sleepMillis` (`<= 0` yields, effect.ts:6419). | `_LiveClock.sleep` | `test_a_non_positive_sleep_is_a_yield_without_a_timer[0, -1]` |
| CL3 | **Infinite parks with no timer** until interrupted (effect.ts:6420). | `_LiveClock.sleep` returns `never` | `test_an_infinite_sleep_parks_without_a_timer_until_interrupted` |
| CL4 | **Interrupting a finite sleep cancels its timer** (`clearTimeout`, effect.ts:6421-6427). | `async_` cancel effect runs `handle.cancel` | `test_interrupting_a_finite_sleep_clears_its_timer` |
| CL5 | **Wall nanos stay within 1s of wall time.** Monotonic plus a module-level origin, re-anchored when the skew is strictly greater than 1e9 ns; wall time is whole milliseconds like `Date.now()`. Integer arithmetic only. | `_wall_nanos` | `test_keeps_wall_time_aligned_while_exposing_the_monotonic_source` |

**Not in the formal model.** `verify-formal-kernel` models no clock. Moving `sleep` out of
`core.py` changed no kernel node: it was already `async_` plus `call_later`, and still is.

## Scope

`src/peffect/scope.py`, outside the kernel: it composes the public `uninterruptible`, `suspend`,
`on_exit`, `update_context` and `exit_`, and reaches no fiber internals (B1–B3). Upstream:
`Scope.ts`, `internal/effect.ts:4033-4215`. Sequential strategy only; see S8.

| # | Invariant | Enforced by | Pinned by |
|---|---|---|---|
| S1 | **Closed is set once, before any finalizer runs.** A second close runs nothing (`scopeCloseUnsafe`, :4040-4056). | `Scope.close_unsafe` checks `Closed` first | `test_closing_the_parent_closes_the_child_with_the_parents_exit_only_once` |
| S2 | **Newest first, each once, each given the close Exit.** | `_close_finalizers` (`reversed`) | `test_doc_close_runs_three_finalizers_newest_first_after_the_work`, `test_sequential_continues_and_awaits_remaining_finalizers_after_a_throw` |
| S3 | **`close` runs its finalizers masked.** Masking comes before the suspend that reads the state, so no interrupt lands between them (`scopeClose`, :4033-4038). | `uninterruptible(suspend(...))` | `test_sequential_completes_direct_close_after_interruption`, `test_sequential_completes_a_single_finalizer_when_its_callback_interrupts_close` |
| S4 | **A direct close keeps every failure reason, in order, with no merging and no election** (`exitAsVoidAll`, :2221-2234). | `_as_void_all` builds `Cause(tuple(...))`, not `+` | `test_source_direct_close_keeps_every_failure_reason_and_elects_no_defect` |
| S5 | **A finalizer added after close runs at once with the closed Exit**; a fork of a closed scope starts Closed (:4113-4130). | `add_finalizer_exit`, `fork_unsafe` | `test_source_a_finalizer_added_after_close_runs_at_once_with_the_closed_exit`, `test_source_forking_a_closed_parent_gives_a_child_closed_with_the_parents_exit` |
| S6 | **A child leaves its parent when it closes**, however its close ends. | `close_unsafe` removes the child key before running finalizers | the three `..._child_...` tests |
| S7 | **`scoped` restores the context before closing.** `on_exit` sits outside `update_context`, so the restore frame pops first; a pending interrupt before install leaves only an unreachable empty scope. | `scoped` composition | `test_source_scoped_restores_the_context_before_running_finalizers` |
| S8 | **Parallel with several finalizers raises `NotImplementedError`**, before any state change. Temporary: upstream forks them; not a contract, no test pins it. | `close_unsafe` | none (by decision) |

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

Boolean uninterruptible modes remain unsupported. The kernel model covers inherited startup
and the existing interrupt/observer protocol, not bulk ordering or multiple-target semantics.

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
| B3 | **Fibers stay opaque.** On a Fiber, code outside the kernel uses only `context`, `await_`, `join`, `interrupt`: the data a service lookup needs, and the three effects the kernel's own tests and the example use to wait on or stop a fiber. Run-loop state (`stack`, `running`, `interrupted`, `interruptible`, `children`, `exit`) and unsafe primitives (`interrupt_unsafe`, `take_interrupt`, `add_observer`, `swap_context`) stay inside. `context` is a read-only property over a read-only map: assigning or deleting it is a pyright error and an `AttributeError`, writing an item a `TypeError` (`typetests/should_fail.py` 12–14, `test_fiber_context_is_read_only_and_owned`), so the lint checks reads only. This list is a specification: a new public Fiber member is a deliberate edit to `FIBER_PUBLIC`. | same |

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
