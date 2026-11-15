"""Deferred unit at Effect v4 pin 289a53f7; Deferred.test.ts :10, :56, :93, :102, :121, :149.

Construct with Deferred[A, E].make/make_unsafe; call await_/done/done_unsafe/succeed/
fail/interrupt/interrupt_with on the cell. Tests import the real typed APIs.

Exit equality follows test_resources_upstream.py: Failure plus exact Cause equality.
Strings remain strings in the typed failure channel. `resumes` and `effect` are upstream's
fields; their inspection and injected callbacks below are white-box tests only. Immediate
fork, interrupt_unsafe, await_ and join follow test_fiber_start_upstream.py.

:149 uses Fiber.awaitAll/timeoutOption/TestClock.adjust(1000). Here both real fibers are
awaited and a 1-second asyncio test-harness watchdog detects a missing notification. This
preserves the liveness/interruption assertions, but does not claim virtual-clock parity.
Python resume schedules fiber evaluation, unlike upstream's synchronous resume. The labelled
source-derived callback test separately pins clear-before-notify at that exact boundary.
Broader Deferred complete/completeWith, polling and other wrappers are outside this unit.
"""

import asyncio
from typing import Never

from peffect import (
    Cause,
    Deferred,
    Die,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    exit_,
    fiber_id,
    fork,
    gen,
    run,
    succeed,
    sync,
    yield_now,
)
from peffect.core import WithFiber


def has_interrupts[A, E](ex: Exit[A, E]) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


async def test_succeed_should_propagate_the_value():
    """Deferred.test.ts:10."""

    @gen
    def program():
        deferred = yield from Deferred[int, Never].make()
        assert (yield from deferred.succeed(1)) is True
        assert (yield from deferred.succeed(1)) is False
        assert (yield from deferred.await_()) == 1

    await run(program())


async def test_fail_should_propagate_the_failure():
    """Deferred.test.ts:56."""

    @gen
    def program():
        deferred = yield from Deferred[int, str].make()
        assert (yield from deferred.fail("boom")) is True
        assert (yield from deferred.succeed(1)) is False
        result = yield from exit_(deferred.await_())
        assert isinstance(result, Failure)
        assert result.cause == Cause.fail("boom")

    await run(program())


async def test_interrupt_should_propagate_the_interruption():
    """Deferred.test.ts:93."""

    @gen
    def program():
        deferred = yield from Deferred[int, Never].make()
        assert (yield from deferred.interrupt_with(-1)) is True
        assert (yield from deferred.interrupt()) is False
        result = yield from exit_(deferred.await_())
        assert isinstance(result, Failure)
        assert result.cause == Cause.interrupt(-1)

    await run(program())


async def test_interrupting_a_suspended_waiter_removes_it():
    """Deferred.test.ts:102."""

    @gen
    def program():
        deferred = yield from Deferred[int, Never].make()
        interrupted: Fiber[int, Never] = yield from fork(
            deferred.await_(), start_immediately=True
        )
        live: Fiber[int, Never] = yield from fork(
            deferred.await_(), start_immediately=True
        )
        assert deferred.resumes is not None
        assert len(deferred.resumes) == 2
        yield from interrupted.interrupt()
        assert has_interrupts((yield from interrupted.await_()))
        assert deferred.resumes is not None
        assert len(deferred.resumes) == 1
        assert (yield from deferred.succeed(42)) is True
        assert (yield from live.join()) == 42

    await run(program())


async def test_interrupting_a_waiter_after_completion_does_not_die():
    """Deferred.test.ts:121; completion and unsafe interrupt occur inside the same sync callback."""

    @gen
    def program():
        deferred = yield from Deferred[int, Never].make()
        interrupted: Fiber[int, Never] = yield from fork(
            deferred.await_(), start_immediately=True
        )
        live: Fiber[int, Never] = yield from fork(
            deferred.await_(), start_immediately=True
        )
        assert deferred.resumes is not None
        assert len(deferred.resumes) == 2

        def complete_then_interrupt() -> None:
            deferred.done_unsafe(yield_now.as_(42))
            assert deferred.resumes is None
            interrupted.interrupt_unsafe()

        yield from sync(complete_then_interrupt)
        result = yield from interrupted.await_()
        assert has_interrupts(result)
        assert isinstance(result, Failure)
        assert not any(isinstance(r, Die) for r in result.cause.reasons)
        assert (yield from live.join()) == 42

    await run(program())


async def test_done_with_an_interrupt_cause_resumes_every_suspended_waiter():
    """Deferred.test.ts:149; the upstream 1000 ms liveness guard is outside the runtime here."""

    @gen
    def program():
        deferred = yield from Deferred[int, Never].make()
        first: Fiber[int, Never] = yield from fork(
            deferred.await_(), start_immediately=True
        )
        second: Fiber[int, Never] = yield from fork(
            deferred.await_(), start_immediately=True
        )
        assert deferred.resumes is not None
        assert len(deferred.resumes) == 2
        assert (yield from deferred.done(Failure(Cause.interrupt()))) is True
        return [(yield from first.await_()), (yield from second.await_())]

    exits = await asyncio.wait_for(run(program()), timeout=1.0)
    assert len(exits) == 2, "every waiter should settle"
    for result in exits:
        assert has_interrupts(result)


# Source-derived, not claimed as Deferred.test.ts ports.


async def test_source_fiber_id_reads_the_running_fiber_on_each_execution():
    """internal/effect.ts:1196-1204: fiberId is a lazy read through withFiberId."""
    read_id: Effect[int, Never, Never] = fiber_id

    @gen
    def program():
        actual = yield from WithFiber(lambda fiber: succeed(fiber.id))
        assert (yield from read_id) == actual
        child: Fiber[int, Never] = yield from fork(read_id, start_immediately=True)
        child_id = yield from child.join()
        assert child_id == child.id
        assert child_id != actual

    await run(program())


async def test_source_done_unsafe_first_completion_wins_and_clears_before_notifying():
    """Deferred.ts:151, :881-894. Injected callbacks pin synchronous, reentrant notification;
    these white-box callbacks inspect the state at the moment doneUnsafe notifies them."""
    deferred = Deferred[int, Never].make_unsafe()
    completion = succeed(1)
    seen: list[Effect[int, Never, Never]] = []

    def notified(effect: Effect[int, Never, Never]) -> None:
        assert deferred.resumes is None
        assert deferred.effect is effect
        seen.append(effect)
        assert deferred.done_unsafe(succeed(99)) is False

    deferred.resumes = [notified, notified]
    assert deferred.done_unsafe(completion) is True
    assert deferred.done_unsafe(succeed(2)) is False
    assert deferred.effect is completion
    assert len(seen) == 2 and all(effect is completion for effect in seen)
    assert (await run(deferred.await_())) == 1


async def test_source_stored_completion_is_lazy_and_re_evaluated_by_each_await():
    """Deferred.ts:180-193, :881-894: store an Effect unchanged; await runs that same Effect.
    Also evidenced by the completeWith test at Deferred.test.ts:34, without porting its API."""
    deferred = Deferred[int, Never].make_unsafe()
    values: list[int] = []

    def next_value() -> int:
        value = len(values) + 1
        values.append(value)
        return value

    completion = sync(next_value)
    assert deferred.done_unsafe(completion) is True
    assert deferred.effect is completion
    assert values == []
    assert (await run(deferred.await_())) == 1
    assert (await run(deferred.await_())) == 2
    assert values == [1, 2]


async def test_source_interrupt_records_the_completing_fibers_id():
    """Deferred.ts:647-648: interruptWith(self, fiber.id). Reading id here is white-box only;
    production uses the public fiber_id effect instead of reading a Fiber member."""

    @gen
    def program():
        deferred = yield from Deferred[int, Never].make()
        fiber_id = yield from WithFiber(lambda fiber: succeed(fiber.id))
        assert (yield from deferred.interrupt()) is True
        result = yield from exit_(deferred.await_())
        assert isinstance(result, Failure)
        assert result.cause == Cause.interrupt(fiber_id)

    await run(program())
