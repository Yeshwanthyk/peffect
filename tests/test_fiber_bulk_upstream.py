"""Fiber prerequisites, pinned Effect v4 289a53f7. Ports: Fiber.test.ts:32, :49, :90.
Source-derived: internal/effect.ts:876-908 (ordered await, removable current observer),
:992-1003 (signal every target before awaiting), :5614-5637 (explicit inherited mask).

Python names: public await_all/interrupt_all for Fiber.awaitAll/interruptAll; fork and
fork_daemon take upstream's uninterruptible="inherit" explicitly. No missing API fallback.
Observer/state reads below are white-box only, not proposed B3 permissions for consumers.
The observer ports wait for actual fiber termination before the original array assertions:
Python interrupt schedules evaluation, whereas upstream interruptUnsafe evaluates synchronously.
Deferred is a one-shot replacement for these tests' single latch opening, not a worker substitute.
Test-owned gates are opened in ensuring on assertion failure to avoid stranding child cleanup.
Await-all captures target failures as Exits; its own external interruption is not caught.
"""

import asyncio
from collections.abc import Callable, Iterator
from typing import Any, Never

import pytest

from peffect import (
    Cause,
    Deferred,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    Success,
    await_all,
    ensuring,
    fail,
    fail_cause,
    fork,
    fork_daemon,
    gen,
    interrupt_all,
    never,
    on_interrupt,
    run,
    succeed,
    sync,
    uninterruptible,
)
from peffect.core import WithFiber


def observer_count(fiber: Fiber[Any, Any], field: str = "_observers") -> int:
    """White-box observation, like upstream's cast of _observers in Fiber.test.ts:76."""
    observers: list[object] = getattr(fiber, field)
    return len(observers)


def has_interrupts[A, E](ex: Exit[A, E]) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


async def test_notifies_all_observers_when_an_observer_cancels_during_exit():
    """Fiber.test.ts:32."""
    observed: list[int] = []

    @gen
    def program():
        fiber = yield from fork(never, start_immediately=True)
        def do_nothing() -> None:
            pass

        cancel: Callable[[], None] = do_nothing

        def first(_: Exit[Never, Never]) -> None:
            observed.append(1)
            cancel()

        cancel = fiber.add_observer(first)
        fiber.add_observer(lambda _: observed.append(2))
        yield from fiber.interrupt()
        assert observed == [1, 2]

    await run(program())


async def test_stops_notifying_an_observer_once_it_is_removed():
    """Fiber.test.ts:49."""
    observed: list[int] = []

    @gen
    def program():
        fiber = yield from fork(never, start_immediately=True)
        cancels = [fiber.add_observer(lambda _, i=i: observed.append(i)) for i in range(3)]
        cancels[1]()
        cancels[1]()
        yield from fiber.interrupt()
        assert observed == [0, 2]

    await run(program())


async def test_interrupt_all_awaits_fibers_passed_as_a_one_shot_iterable():
    """Fiber.test.ts:90: original cleaned assertion, with Deferred for the single latch opening."""
    gate = Deferred[None].make_unsafe()
    flags = {"cleaned": False}

    @gen
    def program():
        cleanup = gate.await_().flat_map(lambda _: sync(lambda: flags.__setitem__("cleaned", True)))
        fiber = yield from fork(
            never.pipe(on_interrupt(lambda _: cleanup)), start_immediately=True
        )
        yield from fork(gate.succeed(None))
        yield from interrupt_all(f for f in [fiber])
        assert flags["cleaned"] is True

    await run(program().pipe(ensuring(gate.succeed(None))))


# Source-derived, not claimed as Fiber.test.ts ports.


async def test_source_await_all_returns_ordered_exits_including_failures_and_interruptions():
    """internal/effect.ts:876-908: walk one iterator, append each Exit in input order."""
    gate = Deferred[None].make_unsafe()

    @gen
    def program():
        first = yield from fork(gate.await_().as_(1), start_immediately=True)
        failed = yield from fork(fail("boom"), start_immediately=True)
        interrupted = yield from fork(fail_cause(Cause.interrupt(-1)), start_immediately=True)
        waiting: Fiber[list[Exit[int, str]], Never] = yield from fork(
            await_all(iter([first, failed, interrupted])), start_immediately=True
        )
        assert waiting.exit is None
        yield from gate.succeed(None)
        exits = yield from waiting.join()
        assert len(exits) == 3
        assert isinstance(exits[0], Success) and exits[0].value == 1
        assert isinstance(exits[1], Failure) and exits[1].cause == Cause.fail("boom")
        assert isinstance(exits[2], Failure) and exits[2].cause == Cause.interrupt(-1)

    await run(program().pipe(ensuring(gate.succeed(None))))


async def test_source_await_all_external_interruption_removes_its_observer_not_the_workers():
    """internal/effect.ts:889-908: cancellation removes the current subscription. It does not
    turn the awaiting fiber's external interruption into a successful list of target Exits."""
    gate = Deferred[None].make_unsafe()

    @gen
    def program():
        first = yield from fork(gate.await_().as_(1), start_immediately=True)
        second = yield from fork(gate.await_().as_(2), start_immediately=True)
        before = [observer_count(first), observer_count(second)]
        waiting: Fiber[list[Exit[int, Never]], Never] = yield from fork(
            await_all([first, second]), start_immediately=True
        )
        assert [observer_count(first), observer_count(second)] == [before[0] + 1, before[1]]
        yield from waiting.interrupt()
        assert has_interrupts((yield from waiting.await_()))
        assert [observer_count(first), observer_count(second)] == before
        assert first.exit is None and second.exit is None
        yield from gate.succeed(None)
        assert (yield from first.join()) == 1
        assert (yield from second.join()) == 2

    await run(program().pipe(ensuring(gate.succeed(None))))


async def test_source_interrupt_all_signals_every_worker_before_waiting_for_cleanup():
    """internal/effect.ts:992-1003: signal and collect all targets, then fiberAwaitAll."""
    release = Deferred[None].make_unsafe()
    cleaned: list[int] = []

    def cleanup(n: int) -> Effect[None, Never, Never]:
        return release.await_().flat_map(lambda _: sync(lambda: cleaned.append(n)))

    @gen
    def program():
        first = yield from fork(
            never.pipe(on_interrupt(lambda _: cleanup(1))), start_immediately=True
        )
        second = yield from fork(
            never.pipe(on_interrupt(lambda _: cleanup(2))), start_immediately=True
        )
        stopping: Fiber[None, Never] = yield from fork(
            interrupt_all(iter([first, second])), start_immediately=True
        )
        # Capture before opening the cleanup gate: a sequential interrupt-and-wait would
        # have signalled only the first target. Always open before asserting, so it cannot hang.
        signalled = [first.interrupted is not None, second.interrupted is not None]
        yield from release.succeed(None)
        yield from stopping.join()
        assert signalled == [True, True]
        assert sorted(cleaned) == [1, 2]
        for fiber in [first, second]:
            result = yield from fiber.await_()
            assert isinstance(result, Failure)
            assert result.cause == Cause.interrupt(stopping.id)

    await run(program().pipe(ensuring(release.succeed(None))))


@pytest.mark.parametrize("fork_name", ["fork", "fork_daemon"])
@pytest.mark.parametrize(
    ("inherit", "masked", "expected"),
    [(False, True, True), (True, True, False), (True, False, True)],
    ids=["default-stays-interruptible", "inherits-masked", "inherits-interruptible"],
)
async def test_source_worker_interruptibility_is_inherited_only_when_explicit(
    fork_name: str, inherit: bool, masked: bool, expected: bool
):
    """internal/effect.ts:5614-5637, :5663: explicit inherit copies the parent's mask; the
    default remains interruptible, even when fork runs under an uninterruptible caller."""
    forker = fork if fork_name == "fork" else fork_daemon
    read_status = WithFiber(lambda fiber: succeed(fiber.interruptible))

    @gen
    def program():
        spawn = (
            forker(read_status, start_immediately=True, uninterruptible="inherit")
            if inherit
            else forker(read_status, start_immediately=True)
        )
        child: Fiber[bool, Never] = yield from uninterruptible(spawn) if masked else spawn
        assert (yield from child.join()) is expected

    await run(program())


async def test_iterator_failure_after_pending_target_does_not_corrupt_its_exit():
    """Python regression: iterator errors belong to the waiter, never its target.

    Unlike the upstream callback hazard, Python's run loop can catch an escaping
    observer exception as a second target completion. Keep the exception inside await_all.
    """
    gate = Deferred[int, Never].make_unsafe()
    target = await run(fork_daemon(gate.await_(), start_immediately=True))
    observed: list[Exit[int, Never]] = []
    target.add_observer(observed.append)
    error = ValueError("iterator failed after pending target")

    def source() -> Iterator[Fiber[int, Never]]:
        yield target
        raise error

    waiter = await run(fork_daemon(await_all(source()), start_immediately=True))
    await run(gate.succeed(7))
    try:
        result = await asyncio.wait_for(run(waiter.await_()), timeout=1)
        assert isinstance(result, Failure)
        assert result.cause.defects == [error]
        assert isinstance(target.exit, Success)
        assert target.exit.value == 7
        assert observed == [target.exit]
    finally:
        await run(waiter.interrupt())
