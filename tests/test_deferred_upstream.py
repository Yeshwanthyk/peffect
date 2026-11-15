"""Deferred unit at Effect v4 pin 289a53f7; Deferred.test.ts :10, :56, :93, :102, :121, :149.

API names follow Scope's module style: peffect.Deferred.make/make_unsafe/await_/done/
done_unsafe/succeed/fail/interrupt/interrupt_with. Dynamic lookup keeps every test runnable
before that API exists; there is no Deferred implementation or substitute in this file.

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
from typing import Any, Never

import peffect
from peffect import (
    Cause,
    Die,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    exit_,
    fork,
    gen,
    run,
    succeed,
    sync,
    yield_now,
)
from peffect.core import WithFiber


def _deferred_api(name: str = "Deferred") -> Any:
    """Resolve the real API per test; missing Deferred raises AttributeError, without a fallback."""
    return getattr(peffect, name)


def _make(api: Any) -> Effect[Any, Never, Never]:
    return api.make()


def has_interrupts(ex: Exit[Any, Any]) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


async def test_succeed_should_propagate_the_value():
    """Deferred.test.ts:10."""
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        assert (yield from deferred_api.succeed(deferred, 1)) is True
        assert (yield from deferred_api.succeed(deferred, 1)) is False
        assert (yield from deferred_api.await_(deferred)) == 1

    await run(program())


async def test_fail_should_propagate_the_failure():
    """Deferred.test.ts:56."""
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        assert (yield from deferred_api.fail(deferred, "boom")) is True
        assert (yield from deferred_api.succeed(deferred, 1)) is False
        result = yield from exit_(deferred_api.await_(deferred))
        assert isinstance(result, Failure)
        assert result.cause == Cause.fail("boom")

    await run(program())


async def test_interrupt_should_propagate_the_interruption():
    """Deferred.test.ts:93."""
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        assert (yield from deferred_api.interrupt_with(deferred, -1)) is True
        assert (yield from deferred_api.interrupt(deferred)) is False
        result = yield from exit_(deferred_api.await_(deferred))
        assert isinstance(result, Failure)
        assert result.cause == Cause.interrupt(-1)

    await run(program())


async def test_interrupting_a_suspended_waiter_removes_it():
    """Deferred.test.ts:102."""
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        interrupted: Fiber[Any, Any] = yield from fork(
            deferred_api.await_(deferred), start_immediately=True
        )
        live: Fiber[Any, Any] = yield from fork(
            deferred_api.await_(deferred), start_immediately=True
        )
        assert len(deferred.resumes) == 2
        yield from interrupted.interrupt()
        assert has_interrupts((yield from interrupted.await_()))
        assert len(deferred.resumes) == 1
        assert (yield from deferred_api.succeed(deferred, 42)) is True
        assert (yield from live.join()) == 42

    await run(program())


async def test_interrupting_a_waiter_after_completion_does_not_die():
    """Deferred.test.ts:121; completion and unsafe interrupt occur inside the same sync callback."""
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        interrupted: Fiber[Any, Any] = yield from fork(
            deferred_api.await_(deferred), start_immediately=True
        )
        live: Fiber[Any, Any] = yield from fork(
            deferred_api.await_(deferred), start_immediately=True
        )
        assert len(deferred.resumes) == 2

        def complete_then_interrupt() -> None:
            deferred_api.done_unsafe(deferred, yield_now.as_(42))
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
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        first: Fiber[Any, Any] = yield from fork(
            deferred_api.await_(deferred), start_immediately=True
        )
        second: Fiber[Any, Any] = yield from fork(
            deferred_api.await_(deferred), start_immediately=True
        )
        assert len(deferred.resumes) == 2
        assert (yield from deferred_api.done(deferred, Failure(Cause.interrupt()))) is True
        return [(yield from first.await_()), (yield from second.await_())]

    exits = await asyncio.wait_for(run(program()), timeout=1.0)
    assert len(exits) == 2, "every waiter should settle"
    for result in exits:
        assert has_interrupts(result)


# Source-derived, not claimed as Deferred.test.ts ports.


async def test_source_fiber_id_reads_the_running_fiber_on_each_execution():
    """internal/effect.ts:1196-1204: fiberId is a lazy read through withFiberId."""
    read_id: Effect[int, Never, Never] = _deferred_api("fiber_id")

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
    deferred_api = _deferred_api()
    deferred = deferred_api.make_unsafe()
    completion = succeed(1)
    seen: list[Effect[Any, Any, Any]] = []

    def notified(effect: Effect[Any, Any, Any]) -> None:
        assert deferred.resumes is None
        assert deferred.effect is effect
        seen.append(effect)
        assert deferred_api.done_unsafe(deferred, succeed(99)) is False

    deferred.resumes = [notified, notified]
    assert deferred_api.done_unsafe(deferred, completion) is True
    assert deferred_api.done_unsafe(deferred, succeed(2)) is False
    assert deferred.effect is completion
    assert len(seen) == 2 and all(effect is completion for effect in seen)
    assert (await run(deferred_api.await_(deferred))) == 1


async def test_source_stored_completion_is_lazy_and_re_evaluated_by_each_await():
    """Deferred.ts:180-193, :881-894: store an Effect unchanged; await runs that same Effect.
    Also evidenced by the completeWith test at Deferred.test.ts:34, without porting its API."""
    deferred_api = _deferred_api()
    deferred = deferred_api.make_unsafe()
    values: list[int] = []

    def next_value() -> int:
        value = len(values) + 1
        values.append(value)
        return value

    completion = sync(next_value)
    assert deferred_api.done_unsafe(deferred, completion) is True
    assert deferred.effect is completion
    assert values == []
    assert (await run(deferred_api.await_(deferred))) == 1
    assert (await run(deferred_api.await_(deferred))) == 2
    assert values == [1, 2]


async def test_source_interrupt_records_the_completing_fibers_id():
    """Deferred.ts:647-648: interruptWith(self, fiber.id). Reading id here is white-box only;
    production B3 currently lacks a permitted current-fiber-id read/combinator."""
    deferred_api = _deferred_api()

    @gen
    def program():
        deferred = yield from _make(deferred_api)
        fiber_id = yield from WithFiber(lambda fiber: succeed(fiber.id))
        assert (yield from deferred_api.interrupt(deferred)) is True
        result = yield from exit_(deferred_api.await_(deferred))
        assert isinstance(result, Failure)
        assert result.cause == Cause.interrupt(fiber_id)

    await run(program())
