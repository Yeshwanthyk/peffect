# Fibers and concurrency in Effect v4

Scope: read-only study of the vendored Effect v4 source in this checkout. Claims are read from source, not runtime-probed. References are paths relative to packages/effect/src.

## 1. Fiber interface and state

Fiber<A, E> exposes an ID, operation count, context/cache/dispatcher access, observer registration/removal, unsafe interruption, and unsafe exit polling. It has no public status field or instance join method; join, await, and interrupt are module functions. (Fiber.ts:73-103, Fiber.ts:296-310, Fiber.ts:387-389)

FiberImpl owns the interpreter stack, exit, observers, children and parent pointer, accumulated interruption Cause, interruptibility, running/deferred-interrupt flags, yield state, and async context. There is no standalone interruptor set: provenance lives in interruption reasons in the accumulated Cause. (internal/effect.ts:521-560, internal/effect.ts:600-613; extraction: internal/effect.ts:224-245)

Observers are callbacks. addObserver invokes immediately if exit exists; otherwise it appends and returns a remover. Completion stores exit before notifying observers, then clears observers, stack, children, and context. (internal/effect.ts:575-592, internal/effect.ts:650-670)

“Status” is best modeled as a derived view over lifecycle state, not copied from a public v4 enum. The interface offers pollUnsafe(): Exit | undefined. (Fiber.ts:73-103, internal/effect.ts:548-559)

## 2. Forking, context, supervision

Fork creates FiberImpl from the parent’s current context. Deferred start captures async context and schedules evaluation on the parent dispatcher; startImmediately evaluates inline. (internal/effect.ts:5614-5634)

forkChild enables the interruptChildren middleware and forks with daemon=false, registering the child in the parent’s child set and storing its parent pointer. When the parent finishes, middleware interrupts live children and waits for them before publishing parent exit. Child completion removes itself from its parent. (internal/effect.ts:5578-5612, internal/effect.ts:642-660, internal/effect.ts:856-861, internal/effect.ts:7028-7031)

forkDetach uses daemon=true: it inherits context but is not in the parent tree. forkIn also creates a daemon, but registers a scope finalizer that interrupts and waits; forkScoped obtains the current scope and calls forkIn. A closed scope yields an unstarted fiber which is interrupted. (internal/effect.ts:5639-5664, internal/effect.ts:5696-5740, internal/effect.ts:5742-5766)

The draft is right that supervised children are interrupted and awaited on parent completion, but generic “fork” is too broad: that guarantee belongs to forkChild. Detached/daemon fibers escape parent supervision; scoped forks instead belong to scope lifetime. (internal/effect.ts:5578-5612, internal/effect.ts:5639-5664, internal/effect.ts:5696-5766; draft: ../../../../../docs/DESIGN.md:56-57, ../../../../../docs/DESIGN.md:66-67)

Interruptibility is inherited only when the option is "inherit"; otherwise it follows the uninterruptible flag. An interrupt arriving in an uninterruptible region is recorded and delivered when execution becomes interruptible. (internal/effect.ts:600-613, internal/effect.ts:5614-5624)

## 3. Await, join, interrupt

await registers an observer and resumes with full Exit; join uses the same observer path but resumes with the child Exit as the effect result, propagating failure. Both check exit before and during callback registration to close the completion/registration race. (internal/effect.ts:864-873, internal/effect.ts:911-919)

interrupt records an interruption Cause tagged with the caller fiber ID, then awaits target termination: it is interrupt-and-wait. If target is executing, interruptUnsafe sets deferredInterrupt, which the run loop delivers at an instruction boundary; if suspended and interruptible it evaluates interruption immediately. (internal/effect.ts:600-613, internal/effect.ts:683-693, internal/effect.ts:961-988)

fiberInterruptAll interrupts all fibers first, then awaits all exits. Observer waits return a cleanup action to unregister on cancellation. (internal/effect.ts:876-909, internal/effect.ts:992-1004)

## 4. all / forEach concurrency

all delegates iterables to forEach. Concurrency 1 uses a sequential loop; concurrent mode materializes the input, allocates indexed result slots unless discard, and calls the concurrent iterator. "unbounded" maps to infinity; finite concurrency is clamped to at least one. (internal/effect.ts:4770-4804, internal/effect.ts:5030-5074, internal/effect.ts:5085-5112)

The concurrent iterator starts effects as daemon fibers, bounded by the number of live fibers, observes completions, and keeps results at original indices. With bounded concurrency it admits more work as fibers finish; it does not fork the entire input at once. (internal/effect.ts:5162-5178, internal/effect.ts:5218-5268, internal/effect.ts:5300-5314)

On terminal failure it stops admitting work, interrupts remaining fibers, and waits before resuming the parent. Later sibling failures have interruption-only reasons stripped; remaining causes combine with the terminal cause, and defects are preserved/elected. A defect thrown by iterator callbacks also interrupts and awaits siblings. (internal/effect.ts:5180-5187, internal/effect.ts:5233-5262, internal/effect.ts:5272-5289)

Python implication (inference): asyncio.gather alone does not supply this policy. Preserve result indices, stop scheduling on failure, cancel active siblings, await their cleanup, and combine non-cancellation failures into the project Cause model.

## 5. Race, timeout, zip

raceAll forks contenders as daemon fibers and resolves on first success. Failures accumulate; if every contender fails, reasons form a failure cause with defects elected. On success, remaining contenders are interrupted immediately. (internal/effect.ts:1688-1739)

The loser set also has a parent-exit finalizer, so remaining contenders are awaited on exit. Immediate observer-side loser interruption avoids a scheduler-yield gap where another contender might finish and have its result discarded. (internal/effect.ts:1702-1706, internal/effect.ts:1721-1727, internal/effect.ts:1782-1790)

raceFirst resolves on the first Exit, including failure; race uses success-first raceAll semantics. Both interrupt losers. (internal/effect.ts:1743-1780, internal/effect.ts:1794-1830, internal/effect.ts:1834-1871)

timeoutOption races Some(effect) against sleep yielding None; timeout and timeoutOrElse map None to error or fallback. The losing computation is interrupted through race semantics. (internal/effect.ts:3929-3955, internal/effect.ts:3963-4008)

zip is sequential by default. With concurrent=true it delegates to all at concurrency two, inheriting its failure and cleanup policy. (internal/effect.ts:2461-2480, internal/effect.ts:2483-2507)

## 6. Deferred, Semaphore, Queue, FiberSet

- Deferred stores a completion effect and optional resume-callback list. Awaiters receive the stored effect or append a callback; completion stores once, detaches the callback list, then resumes all waiters. (Deferred.ts:124-127, Deferred.ts:180-193, Deferred.ts:881-895)
- Semaphore state is permits, permits taken, and insertion-ordered waiters of requested permit count plus optional resume callback. A waiter stays queued until acquisition or interruption; release schedules a dispatcher pass that wakes eligible waiters while accounting for those already selected. Wakeups do not reserve permits, so resumed waiters recheck eligibility. (Semaphore.ts:236-284, Semaphore.ts:286-338)
- Queue state combines a mutable message list, capacity/overflow policy, scheduled-release flag, and Open | Closing | Done state. Open tracks takers, blocked offers, and awaiters; Closing retains buffered messages and terminal exit; Done stores the exit. (Queue.ts:356-417, Queue.ts:480-499)
- Queue takers register a readiness predicate and callback atomically with checking readiness. Offers schedule one coalesced dispatcher pass; it wakes ready takers with void so they retry. Blocked offers have resume callbacks; finalization resumes takers and awaiters with terminal exit. (Queue.ts:2011-2019, Queue.ts:2050-2068, Queue.ts:2070-2084, Queue.ts:2161-2175)
- FiberSet wraps Open(backing Set) | Closed and a Deferred for failure/empty coordination. Observers remove completed fibers; non-interruption-only failures complete the deferred. Clear/close interrupts owned fibers. (FiberSet.ts:58-69, FiberSet.ts:327-357, FiberSet.ts:411-440, FiberSet.ts:729-780)

These primitives share a pattern: register a resumable waiter and cleanup action, then wake through a dispatcher. Python should make registration versus completion atomic at event-loop level, make resume idempotent, remove cancelled waiters, and avoid consuming a queue item without delivering it. Queue’s take primitive explicitly protects the last invariant. (Deferred.ts:180-193, Queue.ts:2041-2048, Queue.ts:2056-2068)

## 7. Scheduler and asyncio mapping

The scheduler uses a cooperative operation budget: shouldYield compares currentOpCount to a cached maximum (default 2048). The interpreter yields through the fiber dispatcher when that budget is reached; explicit yieldNowWith(priority) uses the same dispatcher. (Scheduler.ts:34-38, Scheduler.ts:198-200, Scheduler.ts:273-297; internal/effect.ts:684-703, internal/effect.ts:1091-1099)

MixedScheduler uses microtasks (Promise.resolve().then) in sync mode and setImmediate (or setTimeout(0) fallback) in async mode. Dispatcher tasks are priority-bucketed, lower numeric priority first and FIFO within priority; each drain runs the current batch. (Scheduler.ts:87-105, Scheduler.ts:176-185, Scheduler.ts:217-256; priority contract: Scheduler.ts:40-54)

loop.call_soon is the closest asyncio analogue for scheduling on the ready queue, but it does not recreate separate microtask/macrotask queues, priority buckets, or operation-count fairness. A Python scheduler should encode priority/FIFO and yield after a bounded interpreter-operation count if those semantics matter. This is a mapping recommendation, not a source claim. (Scheduler.ts:87-105, Scheduler.ts:198-200, Scheduler.ts:231-256)

## 8. Python invariants and design-draft corrections

1. **Single terminal publication:** establish Exit once, detach from parent, notify observers; late observers fire immediately. (internal/effect.ts:575-592, internal/effect.ts:650-662)
2. **No lost wakeups:** condition check and waiter registration must be one atomic event-loop step; callbacks tolerate cancellation and late completion. Queue documents this explicitly. (Queue.ts:2056-2068)
3. **Interruption is cooperative and joinable:** record provenance, defer while uninterruptible, deliver at an instruction boundary, distinguish requesting cancellation from waiting for finalizers. (internal/effect.ts:600-613, internal/effect.ts:683-693, internal/effect.ts:983-988)
4. **Ownership is explicit:** structured child, detached daemon, and scope-owned fiber have different lifetimes. Parent exit supervises children; scope close owns scoped forks. (internal/effect.ts:5578-5612, internal/effect.ts:5639-5664, internal/effect.ts:5696-5766)
5. **Concurrency bounds live work:** preserve index order, stop admission on failure, interrupt and join active workers, combine real sibling failures while excluding cancellation-only noise. (internal/effect.ts:5162-5178, internal/effect.ts:5233-5262, internal/effect.ts:5272-5289)
6. **Race needs a winner linearization point:** establish winner before loser interruption can settle the parent; interrupt losers immediately and still join them on exit. (internal/effect.ts:1721-1727, internal/effect.ts:1782-1790)
7. **Kernel fit:** WithFiber can express fiber access/creation and Async can support observer waits, but fibers also require runtime-owned lifecycle, run loop, fairness, and supervision. The draft’s own-VM direction matches these requirements. (internal/effect.ts:521-560, internal/effect.ts:673-703, internal/effect.ts:5578-5634; draft: ../../../../../docs/DESIGN.md:43-57)
8. **Cause shape:** inspected v4 concurrency manipulates a collection of reasons; the draft’s Then/Both tree is a viable alternative but not a literal mirror. (internal/effect.ts:224-249, internal/effect.ts:1711-1718, internal/effect.ts:5236-5244; draft: ../../../../../docs/DESIGN.md:15-16)

### Gaps

- Source inspection only; no tests or runtime behavior were executed.
- Exact mapping of Effect scheduling to asyncio phases depends on the chosen Python runtime; source does not settle that design.
- The inspected public Fiber interface has no status enum; design a Python status API for the new runtime instead of copying a nonexistent v4 field.
