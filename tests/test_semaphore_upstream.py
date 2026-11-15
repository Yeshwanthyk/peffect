"""Semaphore unit at Effect v4 pin 289a53f7.

Upstream: test/Semaphore.test.ts (the `Semaphore` cases; the PartitionedSemaphore cases in the
same file belong to another module); implementation src/Semaphore.ts. The first section ports
upstream cases; the second is source-derived and labelled with the source lines it reads; the
third is a generated operation-sequence property.

Names are snake_case instance methods of Semaphore.ts: `Semaphore.make(n)`, `make_unsafe(n)`,
`take(n)`, `take_scoped(n)`, `take_if_available(n)`, `release(n)`, `release_all()`,
`resize(n)`, `with_permits(n)(eff)`, `with_permit(eff)`, `with_permits_if_available(n)(eff)`.
Upstream's module-level duals (`Semaphore.take(sem, n)`, `take(n)(sem)`) only delegate to these
(Semaphore.ts:574-696) and are not separate API here; `releaseAll` is a getter upstream.

Adaptations used throughout (as test_latch_upstream.py and test_deferred_upstream.py):
- `forkChild(eff, { startImmediately: true })` is `fork(eff, start_immediately=True)`;
  `fiber.pollUnsafe()` is `fiber.exit`; `Fiber.interrupt/join/await` are fiber methods;
  `Effect.provideService(Scheduler.Scheduler, s)` is `provide(Scheduler.context(s))`;
  `Exit.void` is `Success(None)`; `fiber.interruptUnsafe()` is `fiber.interrupt_unsafe()`.
- Option is `A | None` (docs/ROADMAP.md:157), so `Option.isSome(withPermitsIfAvailable(1)(
  Effect.void))` is `with_permits_if_available(1)(succeed(True))` and a non-None result: a
  None payload would make Some and None indistinguishable. Open decision for the coordinator.
- `peffect.semaphore` does not exist yet, so it is looked up when each test runs.

Not ported: :7 "take admits requests in FIFO order when a holder immediately reacquires" needs
TestClock.adjust, Effect.repeat and Effect.delay (TestClock is in flight, not landed); the
source-derived test below pins the queue-behind rule (Semaphore.ts:256) it relies on.
"""

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Never, Protocol

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
    provide,
    run,
    succeed,
    sync,
)
from peffect import yield_now as yield_now_
from peffect.scheduler import MixedScheduler, Scheduler, SchedulerDispatcher

type Task = Callable[[], None]


class WithPermits(Protocol):
    def __call__[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[A, E, R]: ...


class WithPermitsIfAvailable(Protocol):
    def __call__[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[A | None, E, R]: ...


class SemaphoreLike(Protocol):
    """The proposed instance API (Semaphore.ts:60-192); replaced by the real class on landing."""

    def take(self, permits: int, /) -> Effect[int, Never, Never]: ...
    def take_scoped(self, permits: int, /) -> Effect[None, Never, Scope]: ...
    def take_if_available(self, permits: int, /) -> Effect[bool, Never, Never]: ...
    def release(self, permits: int, /) -> Effect[int, Never, Never]: ...
    def release_all(self) -> Effect[int, Never, Never]: ...
    def resize(self, permits: int, /) -> Effect[None, Never, Never]: ...
    def with_permits(self, permits: int, /) -> WithPermits: ...
    def with_permit[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[A, E, R]: ...
    def with_permits_if_available(self, permits: int, /) -> WithPermitsIfAvailable: ...


class SemaphoreClass(Protocol):
    """Constructors (Semaphore.ts:234, :552)."""

    def make(self, permits: int, /) -> Effect[SemaphoreLike, Never, Never]: ...
    def make_unsafe(self, permits: int, /) -> SemaphoreLike: ...


def semaphore_api() -> SemaphoreClass:
    """`peffect.semaphore.Semaphore`, imported when the test runs."""
    return importlib.import_module("peffect.semaphore").Semaphore


async def bounded(eff: Effect[None, Any, Never], seconds: float = 5) -> None:
    """Watchdog only (test_scheduler_upstream.py `bounded`): a lost wakeup fails, not hangs."""
    async with asyncio.timeout(seconds):
        await run(eff)


def has_interrupts[A, E](ex: Exit[A, E] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def succeeded[A, E](ex: Exit[A, E] | None, value: A) -> bool:
    return isinstance(ex, Success) and ex.value == value


def one_available(sem: SemaphoreLike) -> Effect[bool, Never, Never]:
    """`Option.isSome(yield* sem.withPermitsIfAvailable(1)(Effect.void))`."""
    return sem.with_permits_if_available(1)(succeed(True)).map(lambda r: r is not None)


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
    """:385-392 and :399-406: a MixedScheduler whose should_yield is observed per operation."""

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
    Semaphore = semaphore_api()
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
    Semaphore = semaphore_api()
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
    Semaphore = semaphore_api()
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
    Semaphore = semaphore_api()
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
    Semaphore = semaphore_api()
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
    Semaphore = semaphore_api()

    @gen
    def program():
        sem = yield from Semaphore.make(2)
        assert (yield from sem.take_if_available(2)) is True
        assert (yield from sem.take_if_available(1)) is False
        assert (yield from sem.release(2)) == 2

    await bounded(program())


async def test_take_if_available_returns_immediately_when_permits_are_unavailable():
    """Semaphore.test.ts:519."""
    Semaphore = semaphore_api()

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
    Semaphore = semaphore_api()

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
    Semaphore = semaphore_api()

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
    Semaphore = semaphore_api()

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        assert (yield from sem.take(1)) == 1
        assert (yield from sem.release(1)) == 1
        yield from sem.resize(2)
        assert (yield from sem.with_permit(succeed(1))) == 1
        assert (yield from sem.with_permits(2)(succeed(2))) == 2
        assert (yield from sem.with_permits_if_available(1)(succeed("ok"))) == "ok"
        assert (yield from succeed(3).pipe(sem.with_permit)) == 3
        assert (yield from succeed(4).pipe(sem.with_permits(1))) == 4
        assert (yield from succeed("pipe").pipe(sem.with_permits_if_available(1))) == "pipe"
        assert (yield from sem.release_all()) == 2

    await bounded(program())


# == source-derived ============================================================================
# Each assertion is read off the pinned src/Semaphore.ts lines in its docstring.


async def test_new_request_queues_behind_a_queued_request_that_fits():
    """:256 (immediate path only when no queued request fits): a holder that releases and at
    once asks again is admitted after the waiter already queued, not before it."""
    Semaphore = semaphore_api()
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
    Semaphore = semaphore_api()

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
    """:320-334: release adjusts the count at once and queues one wake task on the releasing
    fiber's dispatcher only when requests are queued; the waiter acquires when it runs."""
    Semaphore = semaphore_api()
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
    """:263-266 (`remove` calls releaseUnsafe(fiber, 0)) with :328-334 (a wake budgets the free
    permits across queued requests): a woken waiter interrupted before it acquires hands the
    wake on, so the request the budget skipped is not lost."""
    Semaphore = semaphore_api()
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


async def test_take_if_available_ignores_queued_requests():
    """:312-318 checks only the free count, unlike take (:256); the woken waiter re-checks,
    finds nothing free and waits again (:277-278)."""
    Semaphore = semaphore_api()

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
    """:337-343: resize sets the total; a non-negative free count wakes queued requests.
    release returns the resulting free count, which a shrink can make negative (:320-321)."""
    Semaphore = semaphore_api()

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
    Semaphore = semaphore_api()

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
    """:359-372: the body runs restored (interruptible) and the release runs on every exit."""
    Semaphore = semaphore_api()

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
    """:375-385: no wait and no run when fewer than n are free; the result is none."""
    Semaphore = semaphore_api()
    ran: list[int] = []

    @gen
    def program():
        sem = yield from Semaphore.make(1)
        yield from sem.take(1)
        result = yield from sem.with_permits_if_available(1)(sync(lambda: ran.append(1) or 5))
        assert result is None
        assert ran == []

    await bounded(program())


# == generated: operation sequences ============================================================
#
# Domain: holders asking for 1..capacity permits through with_permits, each body held open by
# its own Deferred gate; ops spawn, finish (open a gate), interrupt, and turn (one yield_now).
# Invariants, from Semaphore.ts:60-191 and docs/ROADMAP.md:52, with no ordering claim:
# - safety, after every op: permits held by running bodies never exceed capacity;
# - liveness: once every gate is open, every holder not interrupted completes (no lost wake);
# - conservation: afterwards exactly `capacity` permits are free, whatever was interrupted.

Op = tuple[str, int] | tuple[Literal["turn"]]  # ("spawn" | "finish" | "interrupt", n) or turn


@dataclass
class Holder:
    fiber: Fiber[None, Never]
    gate: Deferred[None, Never]


def sem_ops(capacity: int) -> st.SearchStrategy[list[Op]]:
    return st.lists(
        st.one_of(
            st.tuples(st.just("spawn"), st.integers(1, capacity)),
            st.tuples(st.sampled_from(["finish", "interrupt"]), st.integers(0, 7)),
            st.just(("turn",)),
        ),
        max_size=24,
    )


@settings(max_examples=150, deadline=None)
@given(st.integers(1, 3).flatmap(lambda c: st.tuples(st.just(c), sem_ops(c))))
async def test_permits_are_safe_live_and_conserved_over_operation_sequences(
    case: tuple[int, list[Op]],
):
    capacity, sequence = case
    Semaphore = semaphore_api()
    held = [0]

    def body(k: int, gate: Deferred[None, Never]) -> Effect[None, Never, Never]:
        enter = sync(lambda: held.__setitem__(0, held[0] + k))
        leave = sync(lambda: held.__setitem__(0, held[0] - k))
        return enter.flat_map(lambda _: gate.await_()).ensuring(leave)

    @gen
    def program():
        sem = yield from Semaphore.make(capacity)
        holders: list[Holder] = []
        for op in sequence:
            match op:
                case ("spawn", k):
                    gate = yield from Deferred[None, Never].make()
                    fiber = yield from fork(
                        sem.with_permits(k)(body(k, gate)), start_immediately=True
                    )
                    holders.append(Holder(fiber, gate))
                case ("finish", i):
                    if i < len(holders):
                        yield from holders[i].gate.succeed(None)
                case ("interrupt", i):
                    if i < len(holders):
                        yield from holders[i].fiber.interrupt()
                case ("turn",):
                    yield from yield_now_
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
        assert (yield from sem.take_if_available(capacity)) is True
        assert (yield from sem.take_if_available(1)) is False

    await bounded(program(), 1)
