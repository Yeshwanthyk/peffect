"""Semaphore unit at Effect v4 pin 289a53f7.

Upstream: test/Semaphore.test.ts (the `Semaphore` cases; the PartitionedSemaphore cases in the
same file belong to another module); implementation src/Semaphore.ts. The first section ports
upstream cases; the second is source-derived and labelled with the source lines it reads; the
third is a generated operation-sequence property.

Names are snake_case instance methods of Semaphore.ts: `Semaphore.make(n)`, `make_unsafe(n)`,
`take(n)`, `take_scoped(n)`, `take_if_available(n)`, `release(n)`, `release_all()`,
`resize(n)`, `with_permits(n)(eff)`, `with_permit(eff)`, `with_permits_if_available(n)(eff)`.
Upstream's module-level duals (`Semaphore.take(sem, n)`, `take(n)(sem)`) only delegate to these
(Semaphore.ts:447-696) and are not separate API here; `releaseAll` is a getter upstream.

Adaptations used throughout (as test_latch_upstream.py and test_deferred_upstream.py):
- `forkChild(eff, { startImmediately: true })` is `fork(eff, start_immediately=True)`;
  `fiber.pollUnsafe()` is `fiber.exit`; `Fiber.interrupt/join/await` are fiber methods;
  `Effect.provideService(Scheduler.Scheduler, s)` is `provide(Scheduler.context(s))`;
  `Exit.void` is `Success(None)`; `fiber.interruptUnsafe()` is `fiber.interrupt_unsafe()`.
- Option is `Some[A] | None` (peffect.option): `Option.some(a)` is `Some(a)` and
  `Option.isSome(o)` is `isinstance(o, Some)`.

Not ported: :7 "take admits requests in FIFO order when a holder immediately reacquires" needs
TestClock.adjust, Effect.repeat and Effect.delay (TestClock is deferred); the
source-derived test below pins the queue-behind rule (Semaphore.ts:256) it relies on.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Never

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from peffect import (
    Cause,
    Deferred,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    Scope,
    Success,
    exit_,
    fail,
    fork,
    gen,
    never,
    on_exit_with_dispatcher,
    provide,
    run,
    succeed,
    suspend,
    sync,
)
from peffect import yield_now as yield_now_
from peffect.option import Some
from peffect.scheduler import MixedScheduler, Scheduler, SchedulerDispatcher
from peffect.semaphore import Semaphore

type Task = Callable[[], None]


async def bounded(eff: Effect[None, Any, Never], seconds: float = 5) -> None:
    """Watchdog only (test_scheduler_upstream.py `bounded`): a lost wakeup fails, not hangs."""
    async with asyncio.timeout(seconds):
        await run(eff)


def has_interrupts[A, E](ex: Exit[A, E] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def succeeded[A, E](ex: Exit[A, E] | None, value: A) -> bool:
    return isinstance(ex, Success) and ex.value == value


def one_available(sem: Semaphore) -> Effect[bool, Never, Never]:
    """`Option.isSome(yield* sem.withPermitsIfAvailable(1)(Effect.void))`."""
    return sem.with_permits_if_available(1)(succeed(None)).map(lambda r: isinstance(r, Some))


def on[A, E, R](scheduler: Any, eff: Effect[A, E, R]) -> Effect[A, E, R]:
    return provide(Scheduler.context(scheduler))(eff)


class QueueingScheduler:
    """Semaphore.test.ts's inline schedulers (:307-325, :436-451): tasks are kept until the test
    runs them; ``on_operation`` sees every should_yield ask; ``yield_once`` forces one yield."""

    execution_mode: Literal["async"] = "async"

    def __init__(self, on_operation: Callable[[Any], None] = lambda _: None) -> None:
        self.tasks: list[Task] = []
        self.on_operation = on_operation
        self.yield_once = False

    def should_yield(self, fiber: Any) -> bool:
        self.on_operation(fiber)
        if self.yield_once:
            self.yield_once = False
            return True
        return False

    def make_dispatcher(self) -> "QueueingScheduler":
        return self

    def schedule_task(self, task: Task, priority: int) -> None:
        self.tasks.append(task)

    def flush(self) -> None:
        pass

    def step(self) -> Effect[None, Never, Never]:
        return sync(lambda: self.tasks.pop(0)() if self.tasks else None)

    def drain(self) -> Effect[None, Never, Never]:
        def go() -> None:
            while self.tasks:
                self.tasks.pop(0)()

        return sync(go)


class CountingScheduler:
    """Semaphore.test.ts:385-393, :406-413: a MixedScheduler that observes each should_yield."""

    execution_mode: Literal["async"] = "async"

    def __init__(self, on_operation: Callable[[Any], None]) -> None:
        self.base = MixedScheduler()
        self.on_operation = on_operation

    def should_yield(self, fiber: Any) -> bool:
        self.on_operation(fiber)
        return self.base.should_yield(fiber)

    def make_dispatcher(self) -> SchedulerDispatcher:
        return self.base.make_dispatcher()


# == upstream: Semaphore.test.ts ===============================================================


async def test_admits_queued_requests_covered_by_a_release_in_one_dispatcher_turn():
    """Semaphore.test.ts:27."""
    turns: list[Task] = []

    def set_immediate(run_: Task) -> Task:
        turns.append(run_)
        return lambda: None

    scheduler = MixedScheduler("async", set_immediate)
    admitted: list[int] = []

    @gen
    def program():
        sem = yield from Semaphore.make(2)
        yield from sem.take(2)
        waiters: list[Fiber[None, Never]] = []
        for ticket in [0, 1]:
            taker = sem.take(1).flat_map(lambda _, t=ticket: sync(lambda: admitted.append(t)))
            waiters.append((yield from fork(on(scheduler, taker), start_immediately=True)))
        yield from on(scheduler, sem.release(2))
        yield from sync(lambda: turns.pop(0)())
        assert admitted == [0, 1]
        for w in waiters:
            yield from w.join()

    await bounded(program())


async def test_take_interruption_does_not_leak_permits():
    """Semaphore.test.ts:304."""
    scheduler = QueueingScheduler()

    @gen
    def program():
        sem = yield from Semaphore.make(0)
        waiter: Fiber[int, Never] = yield from fork(on(scheduler, sem.take(1)))
        yield from yield_now_
        yield from on(scheduler, sem.release(1))
        assert waiter.exit is None
        scheduler.yield_once = True
        yield from scheduler.step()
        assert waiter.exit is None
        yield from waiter.interrupt()
        yield from scheduler.step()
        assert (yield from one_available(sem)) is True

    await bounded(program())


async def test_with_permits_interruption_does_not_leak_permits():
    """Semaphore.test.ts:355."""
    acquired: list[bool] = [False]

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        waiter: Fiber[None, Never] = yield from fork(
            sem.with_permits(2)(sync(lambda: acquired.__setitem__(0, True)))
        )
        yield from yield_now_
        assert waiter.exit is None
        yield from waiter.interrupt()
        assert acquired[0] is False
        assert (yield from one_available(sem)) is True
        yield from sem.with_permits(1)(sync(lambda: acquired.__setitem__(0, True)))
        assert acquired[0] is True

    await bounded(program())


async def test_with_permits_interrupted_at_any_operation_does_not_leak_permits():
    """Semaphore.test.ts:382."""
    operations = [0]
    ran_under_permits = [False]
    body = sync(lambda: ran_under_permits.__setitem__(0, True))

    @gen
    def program():
        counted = CountingScheduler(lambda _: operations.__setitem__(0, operations[0] + 1))
        baseline_sem = yield from Semaphore.make(1)
        baseline = yield from fork(on(counted, baseline_sem.with_permits(1)(succeed(None))))
        yield from baseline.await_()
        assert operations[0] > 0

        recovered: list[bool] = []
        for at in range(1, operations[0] + 1):
            sem = yield from Semaphore.make(1)
            seen = [0]

            def interrupt_at(fiber: Any, at: int = at, seen: list[int] = seen) -> None:
                seen[0] += 1
                if seen[0] == at:
                    fiber.interrupt_unsafe()

            holder = yield from fork(on(CountingScheduler(interrupt_at), sem.with_permits(1)(body)))
            yield from holder.await_()
            recovered.append((yield from one_available(sem)))

        assert recovered == [True] * operations[0]
        assert ran_under_permits[0] is True

    await bounded(program())


async def test_queued_with_permits_interrupted_at_any_operation_does_not_leak_permits():
    """Semaphore.test.ts:433."""
    operations = [0]
    ran_under_permits = [False]
    body = sync(lambda: ran_under_permits.__setitem__(0, True))

    @gen
    def program():
        baseline_sem = yield from Semaphore.make(1)
        baseline_sched = QueueingScheduler(lambda _: operations.__setitem__(0, operations[0] + 1))
        baseline_holder = yield from fork(baseline_sem.with_permits(1)(never))
        yield from yield_now_
        yield from fork(
            on(baseline_sched, baseline_sem.with_permits(1)(succeed(None))), start_immediately=True
        )
        yield from baseline_holder.interrupt()
        yield from yield_now_
        yield from baseline_sched.drain()
        assert operations[0] > 0

        recovered: list[bool] = []
        for at in range(1, operations[0] + 1):
            sem = yield from Semaphore.make(1)
            seen = [0]

            def interrupt_at(fiber: Any, at: int = at, seen: list[int] = seen) -> None:
                seen[0] += 1
                if seen[0] == at:
                    fiber.interrupt_unsafe()

            scheduler = QueueingScheduler(interrupt_at)
            holder = yield from fork(sem.with_permits(1)(never))
            yield from yield_now_
            yield from fork(on(scheduler, sem.with_permits(1)(body)), start_immediately=True)
            yield from holder.interrupt()
            yield from yield_now_
            yield from scheduler.drain()
            recovered.append((yield from one_available(sem)))

        assert recovered == [True] * operations[0]
        assert ran_under_permits[0] is True

    await bounded(program())


async def test_take_if_available_acquires_permits_when_they_are_available():
    """Semaphore.test.ts:505."""

    @gen
    def program():
        sem = yield from Semaphore.make(2)
        assert (yield from sem.take_if_available(2)) is True
        assert (yield from sem.take_if_available(1)) is False
        assert (yield from sem.release(2)) == 2

    await bounded(program())


async def test_take_if_available_returns_immediately_when_permits_are_unavailable():
    """Semaphore.test.ts:519."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        assert (yield from sem.take_if_available(1)) is False
        yield from sem.release(1)
        assert (yield from sem.take_if_available(1)) is True

    await bounded(program())


async def test_take_scoped_holds_permits_until_the_scope_closes():
    """Semaphore.test.ts:532."""

    @gen
    def program():
        sem = yield from Semaphore.make(2)
        scope = yield from Scope.make()
        yield from Scope.provide(scope)(sem.take_scoped(2))
        assert (yield from sem.take_if_available(1)) is False
        yield from Scope.close(scope, Success(None))
        assert (yield from sem.take_if_available(2)) is True

    await bounded(program())


async def test_take_scoped_takes_no_permits_when_interrupted_while_waiting():
    """Semaphore.test.ts:544."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        scope = yield from Scope.make()  # stays open: only the interruption ends the wait
        waiter = yield from fork(Scope.provide(scope)(sem.take_scoped(1)), start_immediately=True)
        yield from waiter.interrupt()
        yield from sem.release(1)
        assert (yield from sem.take_if_available(1)) is True

    await bounded(program())


async def test_instance_api_round_trip():
    """Semaphore.test.ts:557 "module-level combinators delegate to the instance api", through
    the instance methods; pipe forms are `eff.pipe(sem.with_permit)` / `sem.with_permits(1)`."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        assert (yield from sem.take(1)) == 1
        assert (yield from sem.release(1)) == 1
        yield from sem.resize(2)
        assert (yield from sem.with_permit(succeed(1))) == 1
        assert (yield from sem.with_permits(2)(succeed(2))) == 2
        assert (yield from sem.with_permits_if_available(1)(succeed("ok"))) == Some("ok")
        assert (yield from succeed(3).pipe(sem.with_permit)) == 3
        assert (yield from succeed(4).pipe(sem.with_permits(1))) == 4
        assert (yield from succeed("pipe").pipe(sem.with_permits_if_available(1))) == Some("pipe")
        assert (yield from sem.release_all()) == 2

    await bounded(program())


# == source-derived ============================================================================
# Each assertion is read off the pinned src/Semaphore.ts lines in its docstring.


async def test_new_request_queues_behind_a_queued_request_that_fits():
    """:256 (immediate path only when no queued request fits): a holder that releases and at
    once asks again is admitted after the waiter already queued, not before it."""
    order: list[str] = []

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        waiter = yield from fork(
            sem.take(1).flat_map(lambda _: sync(lambda: order.append("waiter"))),
            start_immediately=True,
        )
        yield from sem.release(1)
        again = yield from fork(
            sem.take(1).flat_map(lambda _: sync(lambda: order.append("again"))),
            start_immediately=True,
        )
        assert again.exit is None
        yield from waiter.join()
        assert order == ["waiter"]
        yield from sem.release(1)
        yield from again.join()
        assert order == ["waiter", "again"]

    await bounded(program())


async def test_smaller_later_request_may_overtake_a_larger_earlier_one():
    """:125-130 ("a smaller later request may overtake a larger earlier request"), :243-247,
    :256: a request fits immediately when no queued request fits; a release wakes only fits."""

    @gen
    def program():
        sem = yield from Semaphore.make(2)
        yield from sem.take(1)
        big: Fiber[int, Never] = yield from fork(sem.take(2), start_immediately=True)
        assert big.exit is None
        assert (yield from sem.take(1)) == 1  # free 1, the queued 2 does not fit: immediate
        small: Fiber[int, Never] = yield from fork(sem.take(1), start_immediately=True)
        yield from sem.release(1)
        yield from yield_now_
        assert succeeded(small.exit, 1)
        assert big.exit is None
        yield from sem.release(2)
        assert (yield from big.join()) == 2

    await bounded(program())


async def test_release_wakes_on_the_releasing_fibers_dispatcher():
    """:320-339: release adjusts the count at once and queues one wake task on the releasing
    fiber's dispatcher only when requests are queued; the waiter acquires when it runs."""
    scheduler = QueueingScheduler()

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        assert (yield from on(scheduler, sem.release(0))) == 1
        assert scheduler.tasks == []
        yield from sem.take(1)
        waiter: Fiber[int, Never] = yield from fork(sem.take(1), start_immediately=True)
        assert (yield from on(scheduler, sem.release(1))) == 1
        assert len(scheduler.tasks) == 1
        assert waiter.exit is None
        yield from scheduler.step()
        assert succeeded(waiter.exit, 1)

    await bounded(program())


async def test_woken_waiter_that_leaves_wakes_the_next_one():
    """:263-266 (`remove` calls releaseUnsafe(fiber, 0)) with :328-335 (a wake budgets the free
    permits across queued requests): a woken waiter interrupted before it acquires hands the
    wake on, so the request the budget skipped is not lost."""
    scheduler = QueueingScheduler()

    @gen
    def program():
        sem = yield from Semaphore.make(0)
        first: Fiber[int, Never] = yield from fork(on(scheduler, sem.take(1)))
        second: Fiber[int, Never] = yield from fork(sem.take(1))
        yield from yield_now_
        yield from on(scheduler, sem.release(1))
        scheduler.yield_once = True
        yield from scheduler.step()
        assert first.exit is None
        assert second.exit is None
        yield from first.interrupt()
        yield from scheduler.drain()
        yield from yield_now_
        assert succeeded(second.exit, 1)

    await bounded(program())


async def test_woken_request_leaves_the_queue_when_it_acquires():
    """:278-281: a woken request that acquires calls `remove` before onAcquire, so while it runs
    its old entry cannot make a fresh request that fits queue behind it (:256)."""

    @gen
    def program():
        sem = yield from Semaphore.make(2)
        first = yield from Deferred[None, Never].make()
        second = yield from Deferred[None, Never].make()
        holder = yield from fork(sem.with_permits(2)(first.await_()), start_immediately=True)
        woken = yield from fork(sem.with_permits(1)(second.await_()), start_immediately=True)
        yield from first.succeed(None)
        yield from holder.join()
        yield from yield_now_
        assert sem.taken == 1 and woken.exit is None  # the woken request holds 1 and runs
        fresh: Fiber[int, Never] = yield from fork(sem.take(1), start_immediately=True)
        assert succeeded(fresh.exit, 1)
        yield from second.succeed(None)
        yield from woken.join()

    await bounded(program())


async def test_take_if_available_ignores_queued_requests():
    """:312-318 checks only the free count, unlike take (:256); the woken waiter re-checks,
    finds nothing free and waits again (:277-278)."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        waiter: Fiber[int, Never] = yield from fork(sem.take(1), start_immediately=True)
        yield from sem.release(1)
        assert (yield from sem.take_if_available(1)) is True
        yield from yield_now_
        yield from yield_now_
        assert waiter.exit is None
        yield from sem.release(1)
        assert (yield from waiter.join()) == 1

    await bounded(program())


async def test_resize_wakes_only_when_permits_are_free():
    """:341-347: resize sets the total; a non-negative free count wakes queued requests.
    release returns the resulting free count, which a shrink can make negative (:321, :338)."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        waiter: Fiber[int, Never] = yield from fork(sem.take(2), start_immediately=True)
        yield from sem.resize(2)
        yield from yield_now_
        assert waiter.exit is None
        yield from sem.resize(3)
        assert (yield from waiter.join()) == 2
        yield from sem.resize(1)
        assert (yield from sem.release(1)) == -1
        assert (yield from sem.release(2)) == 1

    await bounded(program())


async def test_release_all_frees_held_permits_and_holders_still_release():
    """:177-190 Gotchas: release_all returns permits held by running with_permits bodies, which
    release theirs again on exit, so free permits can exceed the configured total."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(sem.with_permit(gate.await_()), start_immediately=True)
        assert (yield from sem.release_all()) == 1
        yield from gate.succeed(None)
        yield from holder.join()
        assert (yield from sem.take_if_available(2)) is True

    await bounded(program())


async def test_with_permits_releases_after_failure_and_interruption():
    """:358-372: the body runs restored (interruptible) and the release runs on every exit."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        failed = yield from exit_(sem.with_permit(fail("boom")))
        assert isinstance(failed, Failure) and failed.cause == Cause.fail("boom")
        assert (yield from one_available(sem)) is True
        holder = yield from fork(sem.with_permit(never), start_immediately=True)
        assert (yield from sem.take_if_available(1)) is False
        yield from holder.interrupt()
        assert has_interrupts((yield from holder.await_()))
        assert (yield from sem.take_if_available(1)) is True

    await bounded(program())


async def test_with_permits_if_available_runs_nothing_when_short():
    """:376-386: no wait and no run when fewer than n are free; the result is none."""
    ran: list[int] = []

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        result = yield from sem.with_permits_if_available(1)(sync(lambda: ran.append(1) or 5))
        assert result is None
        assert ran == []

    await bounded(program())


async def test_with_permits_if_available_wraps_every_result_in_some():
    """:381 `asSome(self)`: a body that returns None is present, unlike running short (:379)."""

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        assert (yield from sem.with_permits_if_available(1)(succeed(None))) == Some(None)
        yield from sem.take(1)
        assert (yield from sem.with_permits_if_available(1)(succeed(None))) is None

    await bounded(program())


async def test_queued_take_interrupted_at_any_operation_leaves_no_queue_entry():
    """:267-268 queue the waiter and push its exit frame in one withFiber step
    (internal/effect.ts:4324-4329 pushes onto fiber._stack), and runLoop delivers an interrupt
    only between steps (internal/effect.ts:684-688). So wherever a queued take(1) is interrupted,
    its entry is gone before an outer finalizer runs. Probe: that finalizer frees the held permit
    and a fresh take(1) must acquire at once (:256). Same scenario as Semaphore.test.ts:382,
    with an unmasked take; differentially checked against the vendored Effect."""

    def scenario(at: int, ops: list[int], probes: list[bool]) -> Effect[None, Never, Never]:
        target: list[Any] = []

        def interrupt_at(fiber: Any) -> None:
            if not target:
                target.append(fiber)
            if fiber is target[0]:
                ops[0] += 1
                if ops[0] == at:
                    fiber.interrupt_unsafe()

        @gen
        def go():
            sem = yield from Semaphore.make(1)
            yield from sem.take(1)

            def outer(_: Exit[int, Never]) -> Effect[None, Never, Never]:
                acquired: list[int] = []
                fresh = fork(sem.take(1).map(acquired.append), start_immediately=True)
                probe = sync(lambda: probes.append(bool(acquired)))
                return sem.release(1).flat_map(lambda _: fresh).flat_map(lambda _: probe)

            taker = on(CountingScheduler(interrupt_at), sem.take(1).on_exit(outer))
            waiter: Fiber[int, Never] = yield from fork(taker, start_immediately=True)
            if waiter.exit is None:
                yield from waiter.interrupt()
            assert has_interrupts((yield from waiter.await_()))

        return go()

    @gen
    def program():
        operations, baseline = [0], list[bool]()
        yield from scenario(0, operations, baseline)
        assert baseline == [True]
        for at in range(1, operations[0] + 1):
            probes: list[bool] = []
            yield from scenario(at, [0], probes)
            assert False not in probes, f"queue entry outlived take interrupted at operation {at}"

    await bounded(program())


async def test_queued_take_that_yields_at_any_operation_still_sees_the_release():
    """:270-275: a wake reserves nothing and finds no resume while the queued fiber is yielded,
    so the callback re-checks eligibility before parking. A take(1) on a held semaphore yields
    at operation k; meanwhile the holder releases and that release's wake runs. With no further
    release, the take must still acquire once its fiber resumes."""

    def scenario(at: int, ops: list[int]) -> Effect[bool, Never, Never]:
        target: list[Any] = []
        scheduler = QueueingScheduler()

        def yield_at(fiber: Any) -> None:
            if not target:
                target.append(fiber)
            if fiber is target[0]:
                ops[0] += 1
                if ops[0] == at:
                    scheduler.yield_once = True

        scheduler.on_operation = yield_at

        @gen
        def go():
            sem = yield from Semaphore.make(1)
            yield from sem.take(1)
            taker = yield from fork(on(scheduler, sem.take(1)), start_immediately=True)
            yield from sem.release(1)
            yield from yield_now_  # the release's wake task runs here
            yield from scheduler.drain()
            return succeeded(taker.exit, 1)

        return go()

    @gen
    def program():
        operations = [0]
        assert (yield from scenario(0, operations)) is True
        missed: list[int] = []
        for at in range(1, operations[0] + 1):
            if not (yield from scenario(at, [0])):
                missed.append(at)
        assert operations[0] > 0
        assert missed == [], f"take parked past a release after yielding at operations {missed}"

    await bounded(program())


async def test_a_wake_leaves_waiters_its_budget_does_not_cover_untouched():
    """:323-336: the wake task counts the permits it hands out, so a release of one permit
    resumes only the first of two queued take(1)s. The uncovered waiter's fiber must not run an
    operation or queue a step; re-checking eligibility later would hide the extra wake from
    permit counts alone (independent review, surviving mutant "ignore wake budget")."""
    releasing = QueueingScheduler()
    first = QueueingScheduler()
    second_ops = [0]
    second = QueueingScheduler(on_operation=lambda _: second_ops.__setitem__(0, second_ops[0] + 1))

    @gen
    def program():
        sem = yield from Semaphore.make(0)
        a = yield from fork(on(first, sem.take(1)), start_immediately=True)
        b = yield from fork(on(second, sem.take(1)), start_immediately=True)
        yield from on(releasing, sem.release(1))
        assert len(releasing.tasks) == 1
        first.yield_once = second.yield_once = True
        before = second_ops[0]
        yield from releasing.step()
        assert len(first.tasks) == 1  # the covered waiter resumed and yielded
        assert second.tasks == []
        assert second_ops[0] == before
        first.yield_once = second.yield_once = False
        yield from a.interrupt()
        yield from b.interrupt()

    await bounded(program())


async def test_resize_to_a_negative_free_count_queues_no_wake():
    """:341-347: resize wakes queued requests only when free is non-negative. Shrinking under a
    holder (free -1) with a request queued must dispatch nothing, not a wake that finds no
    eligible waiter (independent review, surviving mutant "wake after resize")."""
    scheduler = QueueingScheduler()

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        waiter: Fiber[int, Never] = yield from fork(sem.take(1), start_immediately=True)
        yield from on(scheduler, sem.resize(0))
        assert sem.free == -1
        assert scheduler.tasks == []
        yield from waiter.interrupt()

    await bounded(program())


def _holding_one(sem: Semaphore, eff: Effect[None, Never, Never]) -> Effect[Any, Never, Never]:
    return sem.with_permit(eff)


def _holding_one_if_available(
    sem: Semaphore, eff: Effect[None, Never, Never]
) -> Effect[Any, Never, Never]:
    return sem.with_permits_if_available(1)(eff)


@pytest.mark.parametrize("hold", [_holding_one, _holding_one_if_available])
async def test_returned_permits_and_their_wake_are_published_together(
    hold: Callable[[Semaphore, Effect[None, Never, Never]], Effect[Any, Never, Never]],
):
    """:320-336, :365-368, :380-383: the exit callback of with_permits and
    with_permits_if_available returns the permits and queues the wake in one step on the current
    fiber's dispatcher, so no yield can fall between them. The holder is forced to yield as soon
    as the permit is back with a request queued; an interrupt of that request is queued after.
    Upstream (independent review, timing_cancel.ts) admits the request first: it enters its
    continuation and holds the permit."""
    entered: list[str] = []
    armed = [True]
    scheduler = QueueingScheduler()
    sem_box: list[Semaphore] = []

    def yield_after_return(_: Any) -> None:
        if armed[0] and sem_box and sem_box[0].free == 1 and sem_box[0].waiters:
            armed[0] = False
            scheduler.yield_once = True

    scheduler.on_operation = yield_after_return

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        sem_box.append(sem)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(on(scheduler, hold(sem, gate.await_())), start_immediately=True)
        waiter = yield from fork(
            sem.take(1).flat_map(lambda _: sync(lambda: entered.append("waiter"))),
            start_immediately=True,
        )
        yield from gate.succeed(None)
        scheduler.tasks.append(waiter.interrupt_unsafe)
        yield from scheduler.drain()
        assert holder.exit is not None
        assert entered == ["waiter"]
        assert sem.taken == 1
        assert isinstance(waiter.exit, Success)

    await bounded(program())


# == source-derived: the exit callback Semaphore releases through ==============================
# on_exit_with_dispatcher is upstream onExitPrimitive with a callback that returns undefined and
# reads getCurrentFiber() (internal/effect.ts:4270-4319; Semaphore.ts:365-368, :380-383).


class Defect(Exception):
    pass


CALLBACK_DEFECT = Defect("callback")


def _logging_scheduler(log: list[str]) -> QueueingScheduler:
    return QueueingScheduler(on_operation=lambda _: log.append("op"))


async def test_exit_callback_runs_synchronously_with_the_dispatcher_on_success():
    """contA calls onExit(exit) as the frame pops: no operation between the body's end and the
    callback beyond the one delivering the value. The dispatcher is the running fiber's
    (getCurrentFiber().currentDispatcher)."""
    log: list[str] = []
    scheduler = _logging_scheduler(log)
    seen: list[tuple[Exit[int, Never], object]] = []

    def callback(ex: Exit[int, Never], d: SchedulerDispatcher) -> None:
        log.append("callback")
        seen.append((ex, d))

    def last_body_op() -> int:
        log.append("body")
        return 7

    body = sync(last_body_op)  # the body's only and last operation
    result = await run(exit_(on(scheduler, on_exit_with_dispatcher(body, callback))))
    assert succeeded(result, 7)
    # one operation, delivering the value, pops the frame; an effect finalizer adds 3 or more
    assert log[log.index("body") + 1 : log.index("body") + 3] == ["op", "callback"]
    ((ex, d),) = seen
    assert succeeded(ex, 7)
    assert d is scheduler


async def test_exit_callback_runs_synchronously_with_the_dispatcher_on_failure():
    """contE: the callback sees the failure; the cause is re-emitted unchanged. The only operation
    between the body's mark and the callback is evaluating `fail` itself, which pops the frame."""
    log: list[str] = []
    scheduler = _logging_scheduler(log)
    seen: list[tuple[Exit[Never, Defect], object]] = []

    def callback(ex: Exit[Never, Defect], d: SchedulerDispatcher) -> None:
        log.append("callback")
        seen.append((ex, d))

    def mark_then_fail() -> Effect[Never, Defect, Never]:
        log.append("body")
        return fail(CALLBACK_DEFECT)  # evaluated as the body's last operation

    body = suspend(mark_then_fail)
    result = await run(exit_(on(scheduler, on_exit_with_dispatcher(body, callback))))
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(CALLBACK_DEFECT)
    assert log[log.index("body") + 1 : log.index("body") + 3] == ["op", "callback"]
    ((ex, d),) = seen
    assert isinstance(ex, Failure)
    assert ex.cause == Cause.fail(CALLBACK_DEFECT)
    assert d is scheduler


async def test_exit_callback_runs_on_interrupt_with_the_dispatcher():
    """An interrupt unwinding past the frame still runs the callback (contAll masks first), with
    the interrupted fiber's dispatcher, and the interrupt is re-emitted."""
    scheduler = QueueingScheduler()
    seen: list[tuple[bool, object]] = []

    def callback(ex: Exit[Never, Never], d: SchedulerDispatcher) -> None:
        seen.append((has_interrupts(ex), d))

    @gen
    def program():
        child = yield from fork(
            on(scheduler, on_exit_with_dispatcher(never, callback)), start_immediately=True
        )
        yield from child.interrupt()
        return child.exit

    result = await run(program())
    assert has_interrupts(result)
    assert seen == [(True, scheduler)]


def _throwing(_: object, __: object) -> None:
    raise CALLBACK_DEFECT


async def test_throwing_exit_callback_is_a_defect_like_a_throwing_finalizer():
    """contA/contE wrap a throw as exitDie. Success: the result is that Die. Failure: the original
    cause plus the Die, defects elected (combineFinalizerCause, :4069-4075), the same cause as
    on_exit with a throwing finalizer. Interrupt: [Interrupt, Die]."""

    def throwing_finalizer(_: object) -> Effect[None, Never, Never]:
        raise CALLBACK_DEFECT

    ok = await run(exit_(on_exit_with_dispatcher(succeed(1), _throwing)))
    assert isinstance(ok, Failure)
    assert ok.cause == Cause.die(CALLBACK_DEFECT)

    body = Defect("body")
    failed = await run(exit_(on_exit_with_dispatcher(fail(body), _throwing)))
    explicit = await run(exit_(fail(body).on_exit(throwing_finalizer)))
    assert isinstance(failed, Failure)
    assert isinstance(explicit, Failure)
    assert failed.cause == explicit.cause
    assert [type(r).__name__ for r in failed.cause.reasons] == ["Die", "Die"]

    @gen
    def interrupted():
        child = yield from fork(on_exit_with_dispatcher(never, _throwing), start_immediately=True)
        yield from child.interrupt()
        return child.exit

    ex = await run(interrupted())
    assert isinstance(ex, Failure)
    assert len(ex.cause.reasons) == 2
    assert isinstance(ex.cause.reasons[0], Interrupt)
    assert ex.cause.reasons[1] == Cause.die(CALLBACK_DEFECT).reasons[0]


# == generated: operation sequences ============================================================
#
# Entry point: the real Semaphore through with_permits. Domain: capacity 1..4; holders asking
# for 1..capacity permits, each body held open by its own Deferred gate; ops spawn, finish (open
# a gate), interrupt (from the driver: while queued, woken or holding), turn (one yield_now),
# and settle (yield until no holder changes state for a full turn). Oracle, read off
# Semaphore.ts with no ordering or FIFO claim:
# - safety, after every op: permits held by running bodies never exceed capacity;
# - eligibility at every settle (:241-247, :323-336): no parked request fits in the free
#   permits, and the public `taken` count equals the permits held by running bodies;
# - liveness: once every gate is open, every holder not interrupted completes (no lost wake);
# - conservation: afterwards `taken` is 0 and exactly `capacity` permits are free.
# Preconditions: the domain has no release_all, resize, or bare take/release. release_all can
# free permits holders still release (:177-190), resize moves the total (:341-348), and an
# interrupted bare take keeps its permits (:299-301), so conservation is not claimed for them;
# the source-derived tests above pin those. Interrupts here come from the driver between
# operations; interrupts at every operation are the scheduler-driven tests above.

Op = tuple[str, int] | tuple[str]  # ("spawn" | "finish" | "interrupt", n), turn or settle


@dataclass
class Holder:
    fiber: Fiber[None, Never]
    gate: Deferred[None, Never]
    permits: int
    entered: list[bool]  # set by the body once the permits are held


def sem_ops(capacity: int) -> st.SearchStrategy[list[Op]]:
    return st.lists(
        st.one_of(
            st.tuples(st.just("spawn"), st.integers(1, capacity)),
            st.tuples(st.sampled_from(["finish", "interrupt"]), st.integers(0, 7)),
            st.sampled_from([("turn",), ("settle",)]),
        ),
        max_size=24,
    )


@settings(max_examples=150, deadline=None)
@given(st.integers(1, 4).flatmap(lambda c: st.tuples(st.just(c), sem_ops(c))))
async def test_permits_are_safe_live_and_conserved_over_operation_sequences(
    case: tuple[int, list[Op]],
):
    capacity, sequence = case
    held = [0]

    def body(
        k: int, gate: Deferred[None, Never], entered: list[bool]
    ) -> Effect[None, Never, Never]:
        def enter() -> None:
            entered.append(True)
            held[0] += k

        leave = sync(lambda: held.__setitem__(0, held[0] - k))
        return sync(enter).flat_map(lambda _: gate.await_()).ensuring(leave)

    def states(holders: list[Holder]) -> list[tuple[bool, bool]]:
        return [(bool(h.entered), h.fiber.exit is not None) for h in holders]

    @gen
    def program():
        sem = yield from Semaphore.make(capacity)
        holders: list[Holder] = []
        for op in sequence:
            match op:
                case ("spawn", k):
                    gate = yield from Deferred[None, Never].make()
                    entered: list[bool] = []
                    fiber = yield from fork(
                        sem.with_permits(k)(body(k, gate, entered)), start_immediately=True
                    )
                    holders.append(Holder(fiber, gate, k, entered))
                case ("finish", i):  # constructive: the index always names an existing holder
                    if holders:
                        yield from holders[i % len(holders)].gate.succeed(None)
                case ("interrupt", i):
                    if holders:
                        yield from holders[i % len(holders)].fiber.interrupt()
                case ("turn",):
                    yield from yield_now_
                case ("settle",):
                    before = None
                    while before != (now := states(holders)):
                        before = now
                        yield from yield_now_
                    parked = [h.permits for h in holders if not h.entered and h.fiber.exit is None]
                    assert all(k > capacity - held[0] for k in parked), (parked, held[0])
                    assert sem.taken == held[0]
                case _:
                    raise AssertionError(op)
            assert 0 <= held[0] <= capacity, (op, held[0])

        for h in holders:
            yield from h.gate.succeed(None)
        for _ in range(4 * len(holders) + 4):
            if all(h.fiber.exit is not None for h in holders):
                break
            yield from yield_now_
            assert held[0] <= capacity
        for h in holders:
            assert h.fiber.exit is not None, "a queued holder was never woken"
            assert succeeded(h.fiber.exit, None) or has_interrupts(h.fiber.exit)
        assert held[0] == 0
        assert sem.taken == 0
        assert (yield from sem.take_if_available(capacity)) is True
        assert (yield from sem.take_if_available(1)) is False

    await bounded(program(), 1)
