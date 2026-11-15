# peffect — Effect-style primitives for Python, rebuilt from the kernel up

v2 of this doc: revised against Effect v4.1.4 (`vendor/effect-v4`, commit 289a53f) using the scout
notes in `docs/scouts/*.md`. Citations are `src/<path>:<line>` inside the vendored package.

## 0. Usage first (what callers write)

```python
class Users(Tag[UserRepo], key="app/Users"): ...   # TAG: string key, nominal R, Protocol shape
users = yield from Users()                          #   -> R gains Users
return (yield from UserNotFound(id=1))              # TaggedError is yieldable: fails with itself

f = yield from fork(job())                          # FIBER: Fiber[A, E], supervised by the parent
a = yield from f.join()                             #   -> E gains job's E
yield from f.interrupt()                            #   interrupt AND wait for its finalizers
x, y = yield from all_(a(), b(), concurrency=4)     #   first failure interrupts the rest
w = yield from race(a(), b())                       #   loser interrupted
yield from sleep("1 second")                        # Duration.Input; a bare number is millis

yield from eff.pipe(ensuring(cleanup))              # finalizers always run, uninterruptibly
conn = yield from acquire_release(open_(), close)   # SCOPE: R gains Scope

DbLive = Db.scoped(acquire_release(...))            # LAYER = build(memo, scope) -> Context
app = program.pipe(provide(UsersLive.provide_merge(DbLive)))

user = yield from decode(UserCodec, payload)        # CODEC[T, I, RD, RE] over pydantic
yield from task().pipe(retry(exponential(0.1).pipe(up_to(5))))   # SCHEDULE: effectful step fn
```

## 1. Kernel

An Effect is a node the fiber's run loop evaluates. Evaluating a node returns the next node, so
composition never becomes Python recursion. Matches `internal/effect.ts:673-770` (run loop).
Wakes do nest: a resume or interrupt runs the woken fiber inside the caller's step, as upstream
does (`effect.ts:605-611`, `:1220-1226`), so a deep chain of synchronous wakes is bounded by
Python's recursion limit (INVARIANTS SC7, measured).

| node | evaluates to | v4 |
|---|---|---|
| `Success(a)` | pop to the next success handler, else the fiber is done | `core.ts:532` |
| `Failure(cause)` | pop to the next failure handler, else done | `core.ts:601` |
| `Sync(thunk)` | `Success(thunk())`; a raise becomes `Die` | `effect.ts:1032` |
| `Suspend(thunk)` | `thunk()`: build the next effect lazily (gen, recursion) | `effect.ts:1045` |
| `Async(register)` | park; `resume(effect)` once; optional cancel effect | `effect.ts:1206` |
| `WithFiber(f)` | `f(fiber)`: read/modify the running fiber | `core.ts:640` |
| `YieldNow` | queue the next step on the fiber's dispatcher, continue later | `effect.ts:773-781` |

`Success`/`Failure` are the `Exit` type *and* effects: an Exit is an effect that's already done.

Frames are pushed on the fiber's stack and have up to three hooks: `on_ok(value)`, `on_err(cause)`,
`on_any(fiber, ok)` (runs whenever the frame is popped, even when skipped).

| frame | hooks | used by |
|---|---|---|
| `OnSuccess(eff, f)` | on_ok | `map`, `flat_map` |
| `OnFailure(eff, f)` | on_err | `catch_cause`, `catch_tag`, `catch_all` |
| `OnExit(eff, f)` | on_any (mask), on_ok, on_err | `on_exit`, `ensuring`, `acquire_release` |
| `SetInterruptible(b)` | on_any | restoring a mask; re-checks pending interrupt |
| `GenFrame(gen)` | on_ok (step), on_any (close on failure) | `@gen` |
| `AsyncCancel(cancel)` | on_any (mask), on_err (run cancel) | parked `Async` |

Data (`cause.py`), flat like v4 (`Cause.ts:79-82`):
- `Cause[E] = Cause(reasons: tuple[Fail[E] | Die | Interrupt, ...])`, `+` concatenates, drops dups
- `Exit[A, E] = Success[A] | Failure[E]`

## 2. Fiber: the one owner of runtime state

`Fiber`: id, stack, context, interruptible, interrupted (Cause | None), running, children,
observers, exit. (`effect.ts:521-567`)

- **Interrupt** records the cause. Running → set `deferred`, delivered at the next loop step.
  Parked and interruptible → mark the park dead and schedule `Failure(interrupt)`. Uninterruptible →
  delivered when a `SetInterruptible(True)` frame pops. (`effect.ts:594-614`, `effect.ts:4671`)
- **Failure while interrupted** skips every handler while the fiber is interruptible and drops the
  typed `Fail` reasons if it skipped one (`core.ts:601-629`). Finalizer frames survive because their
  `on_any` masks the fiber as they're popped, which stops the skipping (`effect.ts:4270-4302`).
  *This is the whole trick behind "finalizers always run".*
- **Async** resume wins once; a resume during `register` is used without parking; on interrupt the
  `AsyncCancel` frame runs the cancel effect masked (`effect.ts:1218-1273`).
- **Finish**: interrupt live children, wait for them, then publish the Exit once to observers.
- **Fairness**: after `MaxOpsBeforeYield` ops (default 2048) the loop asks the `Scheduler`
  Reference's `should_yield` and, if it agrees, queues its next step on the fiber's dispatcher;
  `PreventSchedulerYield` turns the check off. The default `MixedScheduler` batches tasks onto one
  `loop.call_soon` per batch, ordered by priority, FIFO within one (`Scheduler.ts:198-271`).
  `with_dispatcher(f)` hands `f` the running fiber's dispatcher, so work queued on it is ordered
  with the fiber's own steps; `DEFAULT_SCHEDULER` is exported for schedulers that wrap it. See INVARIANTS `## Scheduler`.
- **Generators**: a failed `yield from` never re-enters the generator (no `.throw()`, as v4,
  `effect.ts:1487-1519`), but we do `.close()` it so its `finally` runs at a known time.

## 3. Derived (no new kernel nodes)

| piece | built from |
|---|---|
| `uninterruptible`, `uninterruptible_mask(restore)` | `WithFiber` + `SetInterruptible` frame |
| `fork` / `join` / `await_` / `interrupt` | `WithFiber` (child, observers) + `Async` |
| `all_` / `race` / `timeout` | fork + observers; first failure / first winner interrupts the rest |
| `Deferred`, `Semaphore`, `Queue` | a waiter list + `Async`; register-and-check is one loop step |
| Tag lookup | `WithFiber(f -> f.context[tag.key])`, explicit string key (Context.ts:546); a miss is a `MissingService` defect |
| `Reference` (Clock, log level) | Tag with a default, built lazily once per Reference class (shared by all runs, as v4 `Context.ts:1091-1096`); never in R |
| `provide` | kernel `update_context`: swap in a read-only copy and push a `RestoreContext` frame in one step |
| `Scope` | finalizers get the Exit, run masked in reverse (`effect.ts:4069-4107`) |
| `Layer[ROut, E, RIn]` | `build(memo, scope)`; MemoMap entry = Deferred + own Scope + observer count (`Layer.ts:253-275`) |
| `Codec[T, I, RD, RE]` | pydantic parse + optional effectful step; issue tree from `ValidationError` |
| `Schedule` | effectful step `(now, input) -> (out, delay) or done`; `min`/`max`/`concat` |
| `from_async` / `attempt` | `Async` around `loop.create_task`; cancel = cancel task and wait |

## 4. Kernel shapes considered

**A. Own fiber VM on the asyncio loop (chosen).** v4 confirms it: interruption, masking and Cause are
interpreter state, not host-language exceptions.

**B. asyncio-native (Task, contextvars, cancel).** Rejected: `CancelledError` can be swallowed,
`shield` detaches instead of deferring, failures can't carry a Cause, every combinator nests a
Python frame.

## 5. Changes from v1 of this doc (from the scouts)

1. One `OnExit(eff, ok, err)` node → three frame kinds plus `on_any`. (kernel.md)
2. `Mask(eff)` node → fiber flag + `SetInterruptible` restore frame. (kernel.md)
3. Added `Suspend` and `YieldNow`. (kernel.md)
4. `Cause` tree (`Then`/`Both`) → flat reason tuple. (cause-exit.md)
5. Supervision is `fork` (child) vs `fork_daemon` vs `fork_in(scope)`. (fibers.md)
6. MemoMap needs Deferred + per-entry Scope + refcount; the v1 prototype (`prototypes/pfx`) used a dict memo, which is racy. (layer-scope.md)
7. `Schema[A, I, R]` → `Codec[T, I, RD, RE]`. (schema.md)
8. Schedule step may be effectful; combinators are `min`/`max`/`concat`. (schedule-clock.md)
9. Added `Reference` (default-valued tag) for Clock, log level, tracer. (context.md)
10. `TaggedError` yieldable. (config-data.md)

## 6. Types (carried over from v1, already proven in the v1 prototype, `prototypes/pfx`)

`Effect[A, E, R]` explicit covariant TypeVars; `@gen` + `yield from` gives per-site A and unions
E/R; `catch_tag`/`provide` use exact-match-first overloads; `Layer.ROut` invariant; `run` takes
`Effect[A, Never, Never]`.
