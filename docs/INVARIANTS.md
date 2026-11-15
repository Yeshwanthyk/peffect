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
| may be interrupted now | `Fiber.interruptible` | `uninterruptible*`, `interruptible`, `SetInterruptible.on_any`, `_mask_while_popping` |
| interrupt requested | `Fiber.interrupted` (Cause or None) | `Fiber.interrupt_unsafe` only |
| interrupt not yet delivered | `Fiber._deferred` | `interrupt_unsafe` (set), `Fiber.take_interrupt` (clear; every delivery site calls it) |
| parked on an Async | `Fiber._parked` + `Park.done` | `Async._eval` (park), `resume` / `_interrupt_parked` (flip done) |
| run loop active | `Fiber.running` | `_drive` only |
| result | `Fiber._exit` | `Fiber._publish` only |
| live children | `Fiber.children` | `_start` (add), child `_publish` (discard) |
| body's exit while children finish | `Fiber._ending` | `Fiber._done` only |

Derived: `Fiber.exit` (property), `Cause.is_interrupted` etc. Nothing is cached.

## Transitions

| transition | trigger | precondition | write | code |
|---|---|---|---|---|
| start | `fork` / `run_exit` | — | child in `parent.children`; one `_drive` scheduled | `fiber._start` |
| step | run loop | `running` | eval one node; maybe push/pop frames | `Fiber._drive` |
| park | `Async._eval` | `register` didn't resume synchronously | `_parked = park`; push `_AsyncCancel` if cancel | `Async._eval` |
| resume | callback from asyncio / another fiber | `not park.done` | `park.done = True`; schedule `_drive(eff)` | `Async._eval.resume` |
| interrupt | `interrupt_unsafe(by)` | not finished | add to `interrupted`; parked+interruptible → wake with Failure, else `_deferred` | `Fiber.interrupt_unsafe` |
| deliver | loop top / `SetInterruptible` pop / `interruptible()` / unwind / parked wake | interrupt pending and interruptible | `take_interrupt()` clears `_deferred`; the node becomes (or the unwinding cause gains) `interrupted` | `Fiber.take_interrupt` |
| unwind | `Failure._eval` while interrupted+interruptible | — | pop handlers until a frame masks (OnExit, AsyncCancel) | `Failure._eval` |
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
| K7 | **Interrupts land.** A requested interrupt is delivered at the next interruptible point; a fiber that finishes after one has `Interrupt` in its cause, unless it had already finished. | `_deferred`, `SetInterruptible`, `_drive` tail |
| K8 | **No orphans.** A fiber publishes only after every child it forked has published. | `_done` |
| K9 | **Causes don't lose reasons**, except: the typed `Fail`s of a skipped handler's region are dropped on interrupt (v4 `core.ts:601-629`). Stronger than v4 in two places: a defect unwinding out of a mask, and a body's exit when an interrupt lands while waiting on children, keep their reasons (v4 replaces them with the interrupt). | `Cause.__add__`, `OnExit.on_err`, `Failure._eval`, `Fiber._done` |
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
| B3 | **Fibers stay opaque.** On a Fiber, code outside the kernel uses only `context`, `await_`, `join`, `interrupt`: the data a service lookup needs, and the three effects the kernel's own tests and the example use to wait on or stop a fiber. Run-loop state (`stack`, `running`, `interrupted`, `interruptible`, `children`, `exit`) and unsafe primitives (`interrupt_unsafe`, `take_interrupt`, `add_observer`) stay inside. This list is a specification: a new public Fiber member is a deliberate edit to `FIBER_PUBLIC`. | same |

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
