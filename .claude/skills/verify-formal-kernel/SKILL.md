---
name: verify-formal-kernel
description: Model-check peffect's fiber protocol (park, resume, interrupt, deferred delivery, children, observers) with TLA+/TLC and run the property tests tied to it. Run after any change to src/peffect/fiber.py, or to Async, Failure, SetInterruptible, interruptible or uninterruptible in src/peffect/core.py.
---

# verify-formal-kernel

Checks that the run loop's concurrency protocol keeps the kernel invariants in
`docs/INVARIANTS.md` under every interleaving of two fibers, async completions and an
interrupter. The model is `FiberKernel.tla`. The same promises are tested on the real code by
`tests/test_kernel_props.py`.

## Scope

| model part | code it describes |
|---|---|
| `cur`, `stk`, `Push`, `EndDrive`, `Replace` | nested `Fiber.evaluate` calls: whoever wakes a fiber runs it before returning; `running` is `cur` or in `stk` |
| `LoopTop`, `Ready`, `DriveTail` | `Fiber.evaluate` (loop-top delivery; the tail's fresh `_interrupt_now` for a fiber that parked with an interrupt waiting) |
| `Drive` | a dispatcher task: `_start`'s queued `child.evaluate` (no-op once exited) or `yield_now`'s `step` (no-op once its `Park` is done); run by the batch or a nested `flush` |
| `Yield` | `Fiber.yield_now` (schedule the step, then `_parked`), from `YieldNow` or the op budget |
| `IntUnsafe`, `Claim` | `Fiber.interrupt_unsafe`, `_interrupt_now`, `take_interrupt` |
| `Park`, `ExtResume` | `core.Async._eval` (register, `_park`), its `resume` with the `Park.done` guard calling `fiber.evaluate` |
| `Eval` "mask" / "unmask" / "enable" | `uninterruptible`; `SetInterruptible.on_any` delivering through `take_interrupt` |
| `Done`, `AwaitPark` | `Fiber._done` (`_ending`, interrupt children), `_await_all`, `_publish`, `add_observer` |
| `Eval` "fork", `Init` | `fiber._start` via `fork` (`fork_daemon` shares it; a parentless child isn't modeled); default or inherited interruptibility is installed before either startup path; `start_immediately=True` pushes the child's `evaluate` over the parent's, otherwise the start is queued on the parent's dispatcher. `Init` starts P running: `run_exit` evaluates the root at once |

**Not modeled: context.** The model has no context state. `update_context`, `RestoreContext`,
`Fiber.swap_context` and the context a fork passes on are proven by `tests/test_context.py`
(restore on success, failure, interrupt and interrupt-at-install; fork snapshot), not here.
Why the model's pass still means something with them added (an argument, not a check):
`RestoreContext.on_any` returns nothing and touches no field in this table, so it neither delivers
nor holds back an interrupt, and the model's "the interrupt unwinds to the top" still describes
the code.

**Sync:** 2026-11-16, composition/dispatcher correction: no model change. OnExit receives the
running fiber; its mask and cause combination are unchanged. The author measured 2232 states,
depth 21, 24 linked tests passing and all seven break configurations exiting 1. A scratch
no-mask mutation fails linked runtime tests. GenFrame cause composition and coordination
protocols remain outside the model; independent review exercised their runtime regressions.

Previous Sync, 2026-11-15, ManagedRuntime unit: no model change. `run_exit`'s wait moved unchanged
into `await_exit(fiber)` (one observer, `asyncio.shield`, interrupt then wait on cancel), and
`run_exit` is `_start` then `await_exit`, so `Init` still evaluates the root at once. `Fiber`
gained a `__weakref__` slot; it is not a modeled field. 2232 states, depth 21, every configured
rule holds; every `Break_*.cfg` is red. The scratch `_done` break makes three linked tests fail
through `run.sh`; `fiber.py`'s hash was unchanged. Repeated asyncio cancellation and runtime
disposal are proven by `tests/test_managed_runtime_upstream.py` and
`tests/test_managed_runtime_interleavings.py`, not here.

Previous Sync, 2026-11-15, lifecycle unit (`run_fork`, `run_in`, `fork_in`, `Fiber.parent`): no model
change. `_publish` now also clears `_parent` before observers run (:652-655); `_parent` is not a
modeled field and the `children` write was already there. A `run_fork` root installs its mask
before its first `evaluate`, which `Init` already explores both ways. `fork_in` forks a daemon
(a parentless child, not modeled) or, on a closed scope, interrupts an unstarted fiber, the
atomic publish `IntUnsafe` already describes. 2232 states, depth 21, every configured rule
holds; every `Break_*.cfg` is red. The scratch `_done` break (`if not live:` forced true in a
temp copy put first on `PYTHONPATH`) makes three linked tests fail through `run.sh`;
`fiber.py`'s hash was unchanged. Scope bookkeeping and the hook are proven by
`tests/test_fiber_lifecycle_upstream.py`, not here.

Previous Sync, 2026-11-15, scheduler unit: resume, interrupt of a fiber that is not running, and
root start are synchronous nested `evaluate` calls (internal/effect.ts:590-611, :1220-1226,
:5777); `yield_now` parks on a queued step that an interrupt can claim first (:773-781);
scheduled starts go on the forker's dispatcher (:5629-5633). Wakers (`ExtResume`, `Interrupt`,
`Drive`) may act from the loop or nested inside any drive. 2232 states, depth 21, every
configured rule holds. Every `Break_*.cfg` is red, including the new `no_token_claim`. The
isolated scratch `_done` break (`if not live:` forced true, applied in memory by a pytest plugin
outside the repo) makes three linked tests fail through `run.sh`; `fiber.py`'s hash was unchanged.
Modeling correction made during this update: the first run let a nested `flush` re-enter C
while C's interrupt was being evaluated before its queued start ran. The code can't take that
step: that `Failure` meets an empty stack and goes straight to `_done`/`_publish` with no user
code in between, so `IntUnsafe` now publishes an unstarted fiber atomically.

Previous Sync, 2026-11-15, fiber bulk/inherited-mask unit: fork copies either the caller's
interruptibility or the default `True` before scheduled or immediate startup, matching
`internal/effect.ts:5614-5637`. The model explores both caller masks and both options; 988
states, depth 22, every configured rule holds. Every `Break_*.cfg` remains red. An isolated
scratch `_done` break makes three linked tests fail; production bytes remain unchanged.
Earlier immediate-fork model: 673 states, depth 20. `exit_` and finalizer bodies remain
abstracted into "the interrupt unwinds to the top"; their cause semantics are not modeled.
A change to any symbol in the table, or new code that writes
`_deferred`, `_parked`, `Park.done`, `_exit`, `children` or `interruptible`, means the model
needs another look before its pass means anything.

## Property

| rule | promise | harm if broken |
|---|---|---|
| `K1_ExitOnce` | a fiber publishes once | joiners see two different results |
| `K2_ResumeOnce` | a Park continues its fiber at most once | the fiber runs a continuation twice |
| `K3_OneRunner` | per fiber, at most one `evaluate` is running (nested or not) or live in a dispatcher queue | two run loops share one stack |
| `K6_MasksHold` | no interrupt is delivered inside a mask | half-done acquire/release |
| `K7_InterruptsLand` | an interrupt requested before exit lands once execution regains interruptibility | `interrupt()` silently ignored |
| `K8_NoOrphans` | a parent publishes only after its forked child has | leaked work running past its scope |
| deadlock, `AllPublish` | every started fiber publishes; nothing gets stuck | a joiner waits forever |

**Reach:** every interleaving of this instance: P starts at once, masked or interruptible,
forks C with default or inherited interruptibility, yields, explicitly enables interruption,
parks, and ends. C yields, parks, explicitly enables interruption, masks, parks inside the mask,
and unmasks. One interrupt of P, async completions (late ones included) and dispatcher tasks can
each arrive from the loop or nested inside any running drive; one register can interrupt its own
fiber. Startup may be scheduled or immediate. 2232 states, depth 21. Nesting is at most two
drives deep (two fibers, each at most once on the stack, which K3 checks): this is a bounded
check of one instance, not a proof for arbitrary nesting depth, and it says nothing about
Python's recursion limit (see INVARIANTS SC7). Both fibers regain interruptibility before normal
completion: K7 does not claim that a permanently masked worker must publish an interrupt.

**Cancellation cause correction:** `_AsyncCancel.on_err` combines the original interrupt
with cleanup failure and elects defects (upstream `asyncFinalizer`, :1268-1271). This changes
unmodeled cause semantics, not masking or the protocol transitions. Runtime regressions cover
it; the protocol rerun then explored 988 states, depth 22, with 24 linked tests passing.

**Bulk limits:** public `await_all` / `interrupt_all` reuse the modeled Async/observer protocol,
but ordered iteration, multiple targets, signal-before-wait and cancellation subscription
cleanup are checked by `test_fiber_bulk_upstream.py` and runtime mutations, not this model.
The separate supervision `_await_all` remains unchanged. Parentless daemon supervision is
still outside this two-fiber model.

**Assumptions:**
- One asyncio loop. `evaluate` never awaits.
- Dispatcher tasks of different fibers, and loop callbacks, may run in any order; one fiber's
  tasks run in FIFO order. Priorities, batch boundaries and the op budget's count (`resuming`,
  `MaxOpsBeforeYield`, `PreventSchedulerYield`, `should_yield`) are not modeled: an automatic
  yield is the same `Yield` step. They are pinned by `tests/test_scheduler_upstream.py` and the
  scheduler mutations.
- Python publishes from inside the loop with `running` still set; upstream clears `_running`
  first (effect.ts:731-733 then :637-659). The model ends C's drive as it publishes (`Replace`),
  which is sound because every entry to an exited fiber returns at its `_exit` check.
- Callbacks may run in any order. This is more general than asyncio's FIFO, so the check may report traces asyncio can't produce. It can't miss a real one.
- Finalizer and handler bodies (`OnExit`, catch) are abstracted into "the interrupt unwinds to the top". K4, K5 and K9 are left to the property tests.
- One child and one interrupt. Three fibers, or repeated interrupts, are not covered.

## Run

```sh
.claude/skills/verify-formal-kernel/run.sh                  # model + linked tests, ~3s
.claude/skills/verify-formal-kernel/run.sh Break_old_done.cfg   # one mirrored break, expected red
```

`tlc_chart.py` is a copy of the invariants skill's runner (`tlc_chart 3 (TLC 1.7.4)`). It
downloads `tla2tools.jar` 1.7.4 to `~/.cache/tlaplus/` and checks its sha256. It needs Java 11+;
this machine has 17. Exit codes: 0 every rule holds, 1 a rule broke, 2 error or a rule was
not checked. A pass lists every rule as `holds`. The "stays on None" text in traces only means
the model has no statechart.

## Read the result

Each counterexample step names a model action, and the Scope table maps that action to code.
Replay it on the code as a test: start fibers with `peffect.fiber._start`, step the loop with
`await asyncio.sleep(0)` (or `flush` a fiber's `current_dispatcher` for a nested `Drive`), and
call `interrupt_unsafe` or a captured resume at the step the trace does, from a `sync` node when
the trace shows it nested.
- **Product defect:** the replay fails the same way on the code.
- **Modeling error:** the code can't take one of the trace's steps. Check the "any order" assumption first. Fix the model, and say what was wrong with it.

## Breaks (each mirrors a scratch change to the real code)

| config | code change it mirrors | goes red on |
|---|---|---|
| `Break_old_done.cfg` | `_done` waits for children through observers outside the run loop (the pre-fix code) | K1, K7, deadlock, AllPublish |
| `Break_no_mask_check.cfg` | `interrupt_unsafe` runs a fiber that is not running without checking `interruptible` | K6 |
| `Break_no_park_done.cfg` | `Async` resume ignores `park.done` | K3, K7, deadlock, AllPublish |
| `Break_no_token_claim.cfg` | `yield_now`'s queued step ignores `park.done` (runs unless exited) | K1, K3, deadlock, AllPublish |
| `Break_no_loop_top.cfg` | `evaluate` drops the loop-top `_deferred` check | K7 |
| `Break_no_wait.cfg` | `_done` publishes without waiting for live children | K8 |
| `Break_no_notify.cfg` | `_await_all`'s observer never resumes the parent | deadlock, AllPublish |

## Linked tests

- `tests/test_kernel_props.py::test_interrupt_at_any_tick`: K1, K4, K7, K8 and K9 on generated programs, interrupted at any tick, on the real kernel. Search longer with `--hypothesis-profile=deep`.
- `tests/test_kernel.py::test_interrupt_while_waiting_for_children_lands`: replays the `Break_old_done` trace. The parent's body ends, its child is still finishing, and the parent is interrupted during the wait. Red on the pre-fix `_done`.
- `run.sh` runs both after the model.

## Update

When a change to the code in Scope is intended:
1. Update the matching model action in the same change.
2. Rerun every `Break_*.cfg` and confirm each still goes red.
3. Apply one real scratch break (the `_done` one is the quickest) and confirm `run.sh` goes red on the linked tests. Then restore it.
4. Move the Sync line.

## Known findings

None open. Fixed on 2026-11-15:
- **K7, `_done`.** Hypothesis found it, and `Break_old_done` reproduces it in the model. The fix moves the wait into the run loop.
- **`_deferred` cleared at only some delivery sites.** Every delivery now goes through `take_interrupt`.
