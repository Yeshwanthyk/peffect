"""Controlled concurrency for Queue.ts:2042-2160 / Queue.test.ts:18-35,283-308,
355-375,507-555. No clocks, guessed yields, sleeps, or prototype oracle.

Each sweep discovers the actual finite prefix's operation boundaries and then
injects once at every boundary. 256 is a failure bound, never an expected step
count. The first accepting map logs delivery independently of the fiber's later
exit: interruption after acceptance does not roll back a destructive receive.
"""

from math import inf
from typing import Any

import pytest
from test_queue_upstream import Harness, failed, interrupted, successful
from test_suspend_exit import OperationScheduler, start

from peffect import Cause, Die, Effect, Failure, Fiber
from peffect.core import WithFiber
from peffect.option import Some

RECEIVERS = ["take", "take_all", "take_n", "take_between", "poll", "clear"]
WAITING = ["take", "take_all", "take_n", "take_between", "peek"]


def receive(q: Any, name: str) -> Effect[Any, Any, Any]:
    if name == "take_n":
        return q.take_n(1)
    if name == "take_between":
        return q.take_between(1, 2)
    return getattr(q, name)()


def messages(name: str, result: Any) -> list[int]:
    if name == "take":
        return [result]
    if name == "poll":
        return poll_messages(result)
    return list(result)


def poll_messages(result: Some[int] | None) -> list[int]:
    return [] if result is None else [result.value]


@pytest.mark.parametrize(
    ("name", "capacity", "offer_first"),
    [
        (name, capacity, offer_first)
        for name in RECEIVERS
        for capacity in [0, inf]
        for offer_first in [False, True]
        if name not in {"poll", "clear"} or offer_first
    ],
)
def test_interrupt_each_observed_operation_preserves_exactly_one_owner(
    name: str, capacity: float, offer_first: bool
):
    """507-555, extended to acceptance logging rather than only successful Exit."""
    reached = 0
    for at in range(1, 257):
        h = Harness()
        q = h.queue(capacity)
        accepted: list[int] = []
        injected: list[int] = []
        seen = 0

        def boundary(fiber: Fiber[Any, Any], at: int = at, injected: list[int] = injected) -> bool:
            nonlocal seen
            seen += 1
            if seen == at:
                injected.append(seen)
                fiber.interrupt_unsafe(991)
                return True
            return False

        scheduler = OperationScheduler(boundary)
        producer = h.spawn(q.offer(1)) if offer_first else None

        def accept(a: Any, accepted: list[int] = accepted) -> None:
            accepted.extend(messages(name, a))

        taker = start(receive(q, name).map(accept), scheduler)
        if not offer_first:
            producer = h.spawn(q.offer(1))
        h.pump()
        scheduler.flush()
        h.pump()
        assert taker.exit is not None, (name, capacity, offer_first, at, "lost wakeup")
        remaining = h.value(q.clear())
        assert accepted + remaining == [1], (name, capacity, offer_first, at, taker.exit)
        assert producer is not None
        successful(producer, True)
        if not injected:
            assert seen < at
            break
        reached += 1
    else:
        raise AssertionError("operation sweep did not exhaust within 256 boundaries")
    assert reached > 0, "the scheduler must reach the real dequeue operations"


@pytest.mark.parametrize("name", WAITING)
def test_offer_at_every_initial_wait_registration_boundary_cannot_be_lost(name: str):
    """283-308. Includes arrival before check, between checks, and before async registration."""
    h0 = Harness()
    q0 = h0.queue()
    baseline = OperationScheduler(lambda _: False)
    parked = start(receive(q0, name), baseline)
    assert parked.exit is None
    boundaries = baseline.checks
    assert 0 < boundaries < 256
    q0.shutdown_unsafe()
    for at in range(1, boundaries + 1):
        h = Harness()
        q = h.queue()
        seen = 0
        fired: list[bool] = []

        def boundary(
            _: Fiber[Any, Any], at: int = at, fired: list[bool] = fired, q: Any = q
        ) -> bool:
            nonlocal seen
            seen += 1
            if seen == at:
                fired.append(q.offer_unsafe(1))
                return True
            return False

        scheduler = OperationScheduler(boundary)
        taker = start(receive(q, name), scheduler)
        h.pump()
        scheduler.flush()
        h.pump()
        assert fired == [True], (name, at)
        successful(taker, 1 if name in {"take", "peek"} else [1])
        assert q.size_unsafe() == (1 if name == "peek" else 0)


@pytest.mark.parametrize("capacity", [0, 1])
def test_capacity_release_at_every_offer_all_registration_boundary_cannot_be_lost(capacity: int):
    """18-35; Queue.ts:799-825: admission and pending suffix ownership share a step."""

    def scenario(at: int | None) -> tuple[int, bool]:
        h = Harness()
        q = h.queue(capacity)
        if capacity:
            h.value(q.offer(0))
        seen = 0
        fired: list[bool] = []
        readers: list[Fiber[Any, Any]] = []

        def boundary(_: Fiber[Any, Any]) -> bool:
            nonlocal seen
            seen += 1
            if seen == at:
                fired.append(True)
                readers.append(h.spawn(q.take()))
                return True
            return False

        scheduler = OperationScheduler(boundary)
        producer = start(q.offer_all([1]), scheduler)
        scheduler.flush()
        h.pump()
        if at is not None:
            successful(producer, [])
            successful(readers[0], 0 if capacity else 1)
            assert h.value(q.clear()) == ([1] if capacity else [])
        else:
            assert producer.exit is None
        registration_checks = seen
        q.shutdown_unsafe()
        return registration_checks, bool(fired)

    boundaries, _ = scenario(None)
    assert 0 < boundaries < 256
    for at in range(1, boundaries + 1):
        _, fired = scenario(at)
        assert fired, at


def test_failure_at_every_partial_batch_registration_boundary_short_drains():
    """355-375; termination before registration must trigger the readiness recheck."""
    h0 = Harness()
    q0 = h0.queue()
    q0.offer_all_unsafe([1, 2])
    baseline = OperationScheduler(lambda _: False)
    parked = start(q0.take_n(5), baseline)
    assert parked.exit is None
    boundaries = baseline.checks
    assert 0 < boundaries < 256
    q0.shutdown_unsafe()
    for at in range(1, boundaries + 1):
        h = Harness()
        q = h.queue()
        q.offer_all_unsafe([1, 2])
        seen = 0
        fired: list[bool] = []

        def boundary(
            _: Fiber[Any, Any], at: int = at, fired: list[bool] = fired, q: Any = q
        ) -> bool:
            nonlocal seen
            seen += 1
            if seen == at:
                fired.append(q.fail_cause_unsafe(Cause.fail("boom")))
                return True
            return False

        scheduler = OperationScheduler(boundary)
        taker = start(q.take_n(5), scheduler)
        h.pump()
        scheduler.flush()
        h.pump()
        assert fired == [True], at
        successful(taker, [1, 2])
        failed(h.spawn(q.take_n(5)), Cause.fail("boom"))


@pytest.mark.parametrize("capacity", [0, 1])
@pytest.mark.parametrize("name", ["take", "take_all", "take_n", "take_between", "poll", "clear"])
def test_reentrant_producer_interrupt_still_delivers_to_first_accepting_continuation(
    capacity: int, name: str
):
    """Queue.ts:2042-2049,2094-2145. Deliberately interrupt INSIDE removal/refill."""
    h = Harness()
    q = h.queue(capacity)
    current: list[Fiber[Any, Any]] = []
    accepted: list[int] = []
    if capacity:
        q.offer_unsafe(0)

    def interrupt_consumer(_: bool) -> None:
        current[0].interrupt_unsafe(991)

    producer = h.spawn(q.offer(1).map(interrupt_consumer))
    assert producer.exit is None
    effect = WithFiber(lambda f: record_and_receive(f))

    def record_and_receive(fiber: Fiber[Any, Any]) -> Effect[Any, Any, Any]:
        current.append(fiber)
        return receive(q, name).map(lambda result: accepted.extend(messages(name, result)))

    taker = h.spawn(effect)
    h.pump()
    assert accepted == ([0] if capacity else [1])
    interrupted(taker)  # acceptance already happened; later interruption is allowed
    successful(producer, None)
    assert h.value(q.clear()) == ([1] if capacity else [])


def test_throwing_resumed_producer_continuation_does_not_corrupt_queue_ownership():
    """Source-derived Queue.ts:2109-2145. Producer's own defect does not undo admission."""
    h = Harness()
    q = h.queue(1)
    q.offer_unsafe(0)

    def boom(_: bool) -> None:
        raise ValueError("producer continuation")

    producer = h.spawn(q.offer(1).map(boom))
    successful(h.spawn(q.take()), 0)
    assert isinstance(producer.exit, Failure)
    assert any(isinstance(r, Die) for r in producer.exit.cause.reasons)
    assert h.value(q.clear()) == [1]
    assert h.value(q.offer(2)) is True
    assert h.value(q.take()) == 2


def test_taker_reentry_registers_another_waiter_during_live_release_iteration():
    """Source-derived Queue.ts:1997-2009: deletion precedes synchronous resume/reentry."""
    h = Harness()
    q = h.queue()
    accepted: list[int] = []

    def sync_accept_then_take(a: int) -> Effect[Any, Any, Any]:
        accepted.append(a)
        return q.take()

    first = h.spawn(q.take().flat_map(sync_accept_then_take))
    second = h.spawn(q.take())

    q.offer_unsafe(1)
    q.flush_unsafe()  # first callback registers a new waiter while second is still present
    assert accepted == [1] and first.exit is None and second.exit is None
    q.offer_all_unsafe([2, 3])
    h.pump()
    successful(second, 2)
    successful(first, 3)
    assert h.value(q.clear()) == []
