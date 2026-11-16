# Runtime hardening checkpoint

## Changes

- Fiber publication records the first Exit before observers; each observer is notified even if
  another raises. Nonfatal observer failures reach the asyncio loop exception handler.
- User `CancelledError` becomes an interrupt. Other `BaseException` values become defects
  during unwind. `KeyboardInterrupt` and `SystemExit` propagate after cleanup and publication.
- Each fiber owns a Python `contextvars.Context`, distinct from peffect's service Context.
  A wake enters that context after leaving any earlier drive; thread resumes marshal onto the
  fiber's loop before claiming a Park.
- Nested wakes are counted per event loop and deferred after 24 drives. Deferral installs a
  fresh Park checked by its queued step; an interrupt can claim that Park first. The independent
  `_deferred` and `_parked` facts remain separate.
- Cause election retains `Fail(Done)` when defects exist. `catch_tag` inspects only the first
  recoverable failure, matching upstream `catchTag` -> `catchIf` -> `findRecoverableError`.
  `Done` now pickles through its constructor, retaining the void singleton. Finalizer callback
  `CancelledError` also becomes interruption in core and Scope.

## Reviewed related modules

`Deferred.done_unsafe` detaches callbacks after storing completion; `Latch.open_unsafe`
swaps its waiter list before callbacks; `Semaphore._wake` rechecks both membership and permit
budget against its snapshot. These distinct rules were retained. Scope finalizer callback
conversion was widened to `BaseException`; ManagedRuntime's public `run` squash and disposal
path were retained. TestClock's wake tracker and layer finalizer paths use the kernel's wake
and cleanup protocol; their existing tests remain the boundary checks. No service Context is
cleared from completed fibers, because observers and diagnostic access can still read it.

## Complexity gate review

The policy changed for exactly `cause.py`, `core.py`, `fiber.py` and `scope.py`; no family identities or
budgets in other files changed. Cause's one new method is `Done.__reduce__`; the authoritative
value is its immutable payload, and reconstruction passes through the constructor. Core's
extra branches marshal only before a Park claim and distinguish the first typed failure.
Fiber's added helpers own publication reporting and drive entry; `evaluate` alone owns the
per-loop counter and fresh queued Park, while `_drive` alone owns execution and unwind.
Scope's one additional branch classifies cancellation separately from fatal defects before
returning the finalizer's failed effect.
Writers remain serialized by the owning event loop. A separate context manager or general
scheduler layer would add protocol state without removing a current writer. The increased
file totals are measured new work; `_drive` is now 7 cyclomatic / 10 cognitive and the fiber
file's maximum cognitive score is 15, the pre-change ceiling. `_MixedDispatcher._run_tasks`
owns batch traversal, while `_report_task_error` owns loop reporting and fatal propagation.
Behavioral tests and the
formal sensitivity checks are separate from this static policy gate.

## Proof and limits

Focused runtime hardening, kernel, context, semaphore and managed-runtime tests pass. A
The subprocess sweep completes 80, 170, 400 and 1,000 Deferred links and 400 join links at
Python recursion limit 350; Queue and Semaphore each complete 220-step chains. A low-budget
subprocess pins both stale-queued-step masking failures. Thread handoff against cancellation,
`from_async` context persistence, and completed-fiber context retention have outcome tests.
The formal model uses `WakeBudget=1` to exercise fresh deferred Park claims in two fibers;
it does not prove arbitrary depth, thread scheduling, Python contextvars or cause content.
All eight Break configurations and an isolated real `_done` mutation were red. Evidence:
`/tmp/peffect-formal-corrective/summary.txt`,
`/tmp/peffect-formal-break-corrective.kOUwF6/result.txt`, and
`/var/folders/rf/y4ygjrb13bd2nzm69xrc22h40000gn/T/peffect-verify/20261116T161913Z-10755/summary.txt`.

Error reporting disposition: `run` and `ManagedRuntime.run` still squash their causes as
before. Exception grouping and accumulated generator-location notes were investigated but
left out because no concrete caller or public reporting contract requires them; changing
those boundaries here would enlarge the behavior change without improving the proven races.
