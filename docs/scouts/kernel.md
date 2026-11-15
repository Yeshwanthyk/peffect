# Effect v4 kernel & run loop (source read)

Scope: `packages/effect/src` in the checked-out Effect v4 tree. All citations below are paths relative to that `src/` directory and line numbers in the read source. This is an implementation note, not a claim that every internal opcode is required in a Python port.

## 1. Runtime instructions: actual set vs useful abstraction

The draft's seven names do not match the implementation's opcode vocabulary. `Effect` values carry an `identifier`, an evaluator, and optional success, failure, and all-exit continuation methods (`internal/core.ts:428-444`, `456-485`). `args` is the common payload slot (`internal/core.ts:35-38`, `488-529`). In the relevant `effect.ts` and `core.ts` primitive definitions, the opcode labels are:

| Opcode | Payload / behavior | Runtime role |
|---|---|---|
| `Success` | `value` | Exit-shaped instruction; passes success to next continuation or completes (`internal/core.ts:532-588`). |
| `Failure` | `cause` | Exit-shaped instruction; passes failure to next handler or completes (`internal/core.ts:601-637`). |
| `Sync` | thunk in `args` | Evaluate thunk synchronously, then success-continuation (`internal/effect.ts:1032-1043`). Thrown defects are converted by run loop. |
| `Suspend` | thunk yielding an Effect | Lazily construct/choose next Effect (`internal/effect.ts:1045-1055`). It is not the draft's `Sync`; both are needed to avoid eagerly building recursive programs. |
| `Async` | `register(resume[, signal])`, `withSignal` | Register callback; suspend; optional abort signal and returned cancel Effect (`internal/effect.ts:1206-1256`, `1275-1282`). |
| `Yield` | optional priority | Explicit scheduler hand-off (`internal/effect.ts:1091-1099`). |
| `WithFiber` | `fiber => Effect` | Access fiber and return the next Effect (`internal/core.ts:640-647`). |
| `WithFiberSucceed` | `fiber => value` | Fiber access with immediate success continuation (`internal/core.ts:649-664`). |
| `OnSuccess` | input Effect + success callback + payload | Continuation frame; used for map/flatMap and value transformations (`internal/effect.ts:1539-1574`). |
| `OnFailure` | input Effect + failure callback | Failure continuation, e.g. `catchCause` (`internal/effect.ts:2673-2698`). |
| `OnSuccessAndFailure` | input Effect + both callbacks | Two-sided continuation frame (`internal/effect.ts:3651-3682`). |
| `OnExit` | effect, `onExit(exit)`, optional `interruptible` | Ensure/finalizer frame for either exit (`internal/effect.ts:4270-4319`). |
| `AsyncFinalizer` | interrupt cleanup callback | Internal async cancellation/finalizer frame; enters masked region and runs cleanup on interrupt (`internal/effect.ts:1218-1245`, `1258-1273`). |
| `SetInterruptible` | boolean | Internal restoration instruction; when turning interruptibility back on, re-raises pending interruption (`internal/effect.ts:4671-4680`). |
| `Iterator` | iterator + initial value | `Effect.gen` stepping frame (`internal/effect.ts:1487-1519`). |
| `While` | `{ while, body, step }` | Stack-safe looping instruction (`internal/effect.ts:5005-5027`). |
| `Exit` | exit payload | Internal exit wrapper used by combinators; not the public Success/Failure instruction (`internal/effect.ts:3878`—the implementation is elsewhere in the same module). |
| `YieldableError` | error instance | Custom Effect-like error class, not a run-loop control instruction (`internal/core.ts:667-680`). |

`Fail` is not a separate primitive opcode: it constructs a `Failure` Exit around a `Cause.Fail`; `Die`/`Interrupt` are Cause reasons, not Effect instructions (`internal/effect.ts:1023-1030`; `internal/core.ts:346-385`; `internal/effect.ts:111-149`). Also, `Cause` is a list of reasons in this version, not the draft's recursive `Then`/`Both` tree (`internal/core.ts:137-149`).

**Primitive versus derived.** Constructors such as `succeed`/`fail` are aliases/builders over `Success`/`Failure`, and `ensuring` is `onExit(self, _ => finalizer)` (`internal/effect.ts:1023-1030`, `4343-4358`). `map`/`flatMap` are represented by continuation frames; `OnSuccess` is an implementation frame, not one universal `OnExit` frame (`internal/effect.ts:1539-1605`, `1874-1976`). `uninterruptible` / `interruptible` use `WithFiber` plus `SetInterruptible` restoration; there is no `Mask` opcode (`internal/effect.ts:4661-4724`). The frame vocabulary is therefore richer than the draft's one `OnExit` continuation, and masking is a state transition rather than a wrapped Effect node.

`Effect.ts` is mostly the public typed façade: the public `Effect` interface includes the iterator protocol (`Effect.ts:122-128`) and exports constructors by forwarding to `internal` (for example `succeed`, `suspend`, `sync`, and `fail`: `Effect.ts:1041`, `1171`, `1215`, `1574`).

## 2. Run loop, frames, yielding

A `FiberImpl` owns mutable interpreter state: op count, interruptibility, `_stack: Primitive[]`, observers, exit, children, pending interruption cause, yielded state, running/deferred-interrupt flags, parent, and context/cache (`internal/effect.ts:521-567`). The stack is not a Python/JS call stack of nested combinators; it is an explicit LIFO array of continuation/finalizer instructions.

For an instruction, the loop calls its evaluator, with optional tracer interception. A yielded `Yield` sentinel returns control to dispatcher; an ordinary result becomes the next `current` instruction (`internal/effect.ts:673-735`). Success calls `getCont(contA)` and failure calls `getCont(contE)`; `getCont` pops frames until a compatible continuation appears and invokes any `contAll` hook on the way, which is how finalizers and interruptibility restoration observe either exit (`internal/effect.ts:736-756`; `internal/core.ts:581-630`). For example, evaluating `OnSuccess` pushes itself and returns its child; a success invokes its callback, while failure skips it. `OnExit`'s `contA`/`contE` both run finalizer and then preserve the original exit unless finalizer composition changes cause semantics (`internal/effect.ts:1539-1542`, `4270-4302`).

There are two separate fairness controls. `succeedWith` inlines continuation calls but forces an `Exit` return every 32 inline steps (`internal/effect.ts:758-767`, `841`), limiting continuation-to-continuation nesting. The main loop increments `currentOpCount` and consults `scheduler.shouldYield(fiber)`; automatic yields route the current instruction through `yieldNow` (`internal/effect.ts:691-710`). Explicit `Yield` schedules the continuation through the fiber's dispatcher at a priority; callback completion also resumes through `fiber.evaluate` (`internal/effect.ts:773-783`, `1091-1099`, `1218-1228`). On automatic resume, the saved instruction runs before another budget check; a deferred interruption can cancel a pending hand-off and continue immediately (`internal/effect.ts:679-723`). Thus “yield every N ops” is a rough porting goal, not Effect's exact rule: its scheduler decides whether to yield, while 32 is specifically an inline-continuation cap.

Run-loop exceptions from an evaluator/continuation are caught and converted to defect failure (`Die` in a `Failure` Exit); malformed current values become a defect with an invalid-effect message (`internal/effect.ts:724-729`). On final exit, fiber middleware may run first; then exit is published, parent-child relation removed, observers called, and stack/children/context references released (`internal/effect.ts:642-671`).

## 3. Interruption and async cancellation

`interruptUnsafe` records/combines an interrupt Cause unless already complete. If interruptible and currently running, it sets `_deferredInterrupt`; if not running it evaluates a Failure immediately (`internal/effect.ts:594-614`). The run loop converts deferred interruption into current failure at its next iteration (`internal/effect.ts:685-690`); `getCont` can also return a deferred-interrupt continuation (`internal/effect.ts:736-743`). Therefore interruption is cooperative at interpreter boundaries, not an arbitrary preemption of synchronous user code.

When a Failure is evaluated, pending interrupt causes are merged while the fiber is interruptible. If interruption skips recovery handlers, typed Fail reasons may be discarded so catch handlers do not swallow interruption (`internal/core.ts:601-629`). Re-entering interruptible mode with a pending cause immediately fails (`internal/effect.ts:4671-4686`). The initial fiber defaults interruptible; run options can start it uninterruptible (`internal/effect.ts:521-530`, `5773-5793`).

Async registration is called once with a resume callback; resume is idempotent (`resumed` guard). If registration resumes synchronously, the returned Effect is consumed without parking. Otherwise the fiber stores a cancellation continuation and returns `Yield`. If a signal is requested, an `AbortController` signal is passed to registration; on interruption it aborts and runs returned `onCancel` Effect. `AsyncFinalizer` masks while this cleanup executes (`internal/effect.ts:1218-1245`, `1258-1273`). This is cancellation-on-interruption, not a generic cancel callback automatically run for every completion.

## 4. `Effect.gen`, `yield*`, iterator protocol, Effectable

`Effect[Symbol.iterator]()` returns a `SingleShotGen(this)` (`internal/core.ts:99-104`). The iterator yields that Effect once; the next `.next(value)` completes with the supplied value (`Utils.ts:53-79`). This is why source code writes `yield* someEffect`: the JS generator delegates to the Effect's one-yield iterator, and receives its success value.

`Effect.gen` defers generator creation/execution under `Suspend`, then adapts its iterator via `fromIteratorUnsafe` (`internal/effect.ts:1288-1311`). The `Iterator` frame calls `iterator.next(value)`; if the yielded value is an Effect rather than an already-completed Exit, it pushes itself and runs that Effect. A successful Exit is fed as the next `.next` value; a failed Exit is returned immediately (`internal/effect.ts:1487-1519`). `doNotation.ts` supplies generic `let_`, `bindTo`, and `bind` helpers in terms of map/flatMap; it is not the generator interpreter (`internal/doNotation.ts:28-50`, `54-73`, `76-117`).

Failure nuance: the adapter implements only a success continuation; it does not call `iterator.throw()` or `iterator.return()` when the yielded Effect fails/interruption escapes (`internal/effect.ts:1491-1509`). So JS `try/catch` around a `yield*` does not receive the Effect failure, and a suspended generator is not explicitly closed to run its `finally`. Use Effect error combinators/finalizers for those semantics. The sync-eager variant similarly returns a failed yielded Exit directly (`internal/effect.ts:1447-1485`).

`Effectable.Prototype` creates a custom Effect prototype whose evaluator calls the provided `evaluate(fiber)`; `Class` and `Mixin` provide class-based forms whose `asEffect()` is evaluated (`Effectable.ts:14-50`, `53-75`, `133-141`). Such domain values can be yielded like Effects because they participate in the Effect interface/prototype protocol; custom Effectable is an extension hook, not a fixed opcode set.

## 5. Run-loop invariants to preserve

- Each fiber has one active interpreter owner; `_running` and `_deferredInterrupt` coordinate interruption arriving during evaluation (`internal/effect.ts:558-560`, `673-690`).
- Every stack frame must either handle the current exit or be skipped; all-exit hooks run as frames unwind (`internal/effect.ts:736-756`). Finalizers and interruptibility restoration cannot be represented as success-only callbacks (`internal/effect.ts:4277-4302`, `4671-4678`).
- Async resume wins at most once; synchronous callback completion must not be lost, and a parked fiber must have its continuation/cancel path installed (`internal/effect.ts:1218-1245`).
- Pending interruption is sticky until incorporated into failure; enabling interruptibility re-checks it (`internal/effect.ts:605-614`, `4671-4686`).
- Evaluator and continuation throws become defects rather than escaping the runtime loop (`internal/effect.ts:724-729`).
- Continuation execution must remain stack-safe, including success-heavy loops (`internal/effect.ts:758-767`, `841`; `internal/effect.ts:5005-5027`).
- Completion publishes one Exit and releases fiber-owned references; observers added after completion are called immediately (`internal/effect.ts:575-585`, `650-671`).

## 6. Python asyncio mapping and minimal kernel recommendation

**Maps well:** explicit `Success(value)` / `Failure(Cause)`; `Sync(thunk)` with thrown exceptions mapped to `Die`; `Suspend(thunk)` for lazy recursion; an `Async(register)` boundary with a once-only resume and cancellation action; explicit fiber state/context; explicit success/failure/all-exit frames; and a scheduler-yield instruction. The v4 evidence for each is its distinct instruction/frame behavior above (`internal/core.ts:428-485`; `internal/effect.ts:673-783`, `1032-1055`, `1215-1273`).

**Does not map 1:1:** JavaScript's scheduler/dispatcher and callback resume ordering are not asyncio's event loop semantics. Use `loop.call_soon`/Task or Futures as the host bridge, but define ordering and once-only resume yourself; do not assume JS microtask behavior. Python async cancellation raises `CancelledError` through coroutine stacks, whereas this VM stores interruption as Cause and checks it at safe interpreter points, so `Task.cancel()` alone cannot implement masked/deferred interruption. An explicit Python VM avoids Python recursion for Effect composition, but a synchronous user thunk can still block the event loop until it returns. Generator-based Python syntax is not the JS `yield*` single-shot protocol; a Python `yield from` implementation needs its own iterator adapter, and native generator throw/close semantics should be an explicit design choice.

**Recommended minimal set (inferred from the implementation and the stated Python target):**

1. `Succeed(value)`, `Fail(cause)`; Cause should distinguish typed failure, defect, and interruption, and should support composition if parallel/racing scopes need it.
2. `Sync(thunk)` and `Suspend(thunk)`; do not collapse laziness and execution.
3. `Async(register)` with a once-only resume and optional cancel/finalizer action.
4. Continuation frames: `OnSuccess`, `OnFailure`, and `OnExit`; model them as stack frame variants, not a single callback pair that loses exit/cause behavior.
5. Fiber-control instructions/state for current fiber and interruptibility restoration (`WithFiber` plus `SetInterruptible` or an equivalent scoped frame). Keep `Mask` as a public combinator if desired, but it is not a v4 opcode.
6. `YieldNow` / budget checkpoint as runtime scheduling machinery. Add `While` only if profiling or recursive traversals show value; it is an optimization, not a semantic minimum.

Compared with DESIGN.md: its core has no `Suspend`, although v4 uses it to defer gen construction and recursive decisions (`internal/effect.ts:1045-1055`, `1288-1311`); its one `OnExit(eff,on_ok,on_err)` conflates success-only mapping, failure recovery, and true finalization (`internal/effect.ts:1549-1574`, `2690-2698`, `4270-4302`); its `Mask` should be expressed as interruptibility state plus restoration/check-on-reentry (`internal/effect.ts:4661-4739`); and its `Async` model needs synchronous-resume safety and cancel-finalizer masking (`internal/effect.ts:1218-1273`). `WithFiber` is real but v4 also has `WithFiberSucceed` (`internal/core.ts:640-664`). `YieldNow` is missing as a primitive/scheduler boundary (`internal/effect.ts:1091-1099`). The proposed single-success/fail values omit the implementation's distinction between typed failure, defects, interruption, and async/finalizer cause composition (`internal/core.ts:137-149`, `internal/effect.ts:111-149`, `601-629`).

## Runtime entry points

`runForkWith` constructs a root fiber, injects an optional scheduler, evaluates immediately, and wires an optional abort signal to interruption (`internal/effect.ts:5773-5797`). `runCallback` returns an interrupt function and optionally reports Exit (`internal/effect.ts:5830-5851`); `runPromiseExit` resolves with Exit, while `runPromise` throws a squashed cause for failure (`internal/effect.ts:5854-5893`). `runSyncExit` uses a sync scheduler/flush and returns `AsyncFiberError` if the fiber remains unfinished; `runSync` throws the squashed failure (`internal/effect.ts:5895-5923`). Public `Effect.ts` re-exports the entry points (`Effect.ts:9222-9264`, `9408-9485`, `9593-9729`).

## Gaps

This note inspected the implementation paths named above and one nearby `Effect.gen` test search, but did not run the Effect tests or execute the TypeScript source (the source was treated as read-only). Scheduler policy is delegated to scheduler implementations and `shouldYield`; this note records the run-loop contract, not every scheduler's thresholds. No Python implementation was executed or type-checked.
