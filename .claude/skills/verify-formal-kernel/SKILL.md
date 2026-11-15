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
| `Drive`, `LoopTop`, `Ready`, `DriveTail` | `Fiber._drive` (entry, loop-top delivery, tail delivery to a parked fiber) |
| `IntUnsafe` | `Fiber.interrupt_unsafe`, `_interrupt_parked`, `take_interrupt`, `_wake` |
| `Park`, `ExtResume`, `Wake` | `core.Async._eval` (register, `_park`), its `resume` with the `Park.done` guard |
| `Eval` "mask" / "unmask" | `uninterruptible`; `SetInterruptible.on_any` delivering through `take_interrupt` |
| `Done`, `AwaitPark` | `Fiber._done` (`_ending`, interrupt children), `_await_all`, `_publish`, `add_observer` |
| `Eval` "fork", `under`, `EndDrive` | `fiber._start` via `fork` (`fork_daemon` shares it; a parentless child isn't modeled); `start_immediately=True` runs `child._drive` nested in the parent's drive (`under` holds the suspended parent until the child's slice ends) |

**Not modeled: context.** The model has no context state. `update_context`, `RestoreContext`,
`Fiber.swap_context` and the context a fork passes on are proven by `tests/test_context.py`
(restore on success, failure, interrupt and interrupt-at-install; fork snapshot), not here.
Why the model's pass still means something with them added (an argument, not a check):
`RestoreContext.on_any` returns nothing and touches no field in this table, so it neither delivers
nor holds back an interrupt, and the model's "the interrupt unwinds to the top" still describes
the code.

**Sync:** 2026-11-15, on top of `05a97f0` with the uncommitted immediate-fork change
(`_start(immediate)`, Effect v4 forkUnsafe internal/effect.ts:5614-5637): the fork action
chooses scheduled or immediate; 673 states, depth 20, all configured rules hold, every
`Break_*.cfg` still red, and a scratch `_done` break turns the linked tests red. `exit_`'s
`ExitFrame` is an ordinary handler frame, abstracted like other handlers into "the interrupt
unwinds to the top". Earlier: context baseline `b74fa2f`; finalization changes on `1c2828b`
(`OnExit` callbacks, cause election) are outside the modeled semantics.
A change to any symbol in the table, or new code that writes
`_deferred`, `_parked`, `Park.done`, `_exit`, `children` or `interruptible`, means the model
needs another look before its pass means anything.

## Property

| rule | promise | harm if broken |
|---|---|---|
| `K1_ExitOnce` | a fiber publishes once | joiners see two different results |
| `K2_ResumeOnce` | a Park continues its fiber at most once | the fiber runs a continuation twice |
| `K3_OneRunner` | at most one `_drive` per fiber is queued or running | two run loops share one stack |
| `K6_MasksHold` | no interrupt is delivered inside a mask | half-done acquire/release |
| `K7_InterruptsLand` | an interrupt requested before the exit ends up in the exit | `interrupt()` silently ignored |
| `K8_NoOrphans` | a parent publishes only after its forked child has | leaked work running past its scope |
| deadlock, `AllPublish` | every started fiber publishes; nothing gets stuck | a joiner waits forever |

**Reach:** every interleaving of this instance: P forks C, parks, and ends; C parks, masks,
parks inside the mask, and unmasks. One external interrupt of P can arrive at any loop turn,
async completions can arrive at any turn (late ones included), and one `register` can
interrupt its own fiber. A fork may start its child immediately, nested. 673 states, depth 20.

**Assumptions:**
- One asyncio loop. `_drive` never awaits.
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
`await asyncio.sleep(0)`, and call `interrupt_unsafe` at the step the trace does.
- **Product defect:** the replay fails the same way on the code.
- **Modeling error:** the code can't take one of the trace's steps. Check the "any order" assumption first. Fix the model, and say what was wrong with it.

## Breaks (each mirrors a scratch change to the real code)

| config | code change it mirrors | goes red on |
|---|---|---|
| `Break_old_done.cfg` | `_done` waits for children through observers outside the run loop (the pre-fix code) | K7 |
| `Break_no_mask_check.cfg` | `interrupt_unsafe` wakes a parked fiber without checking `interruptible` | K6 |
| `Break_no_park_done.cfg` | `Async` resume ignores `park.done` | K1, K2, K3, K7 |
| `Break_no_loop_top.cfg` | `_drive` drops the loop-top `_deferred` check | K7 |
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
