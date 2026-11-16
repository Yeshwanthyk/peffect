"""Queue operational contract at read-only Effect v4 pin 289a53f7.

Source anchors below refer to packages/effect/test/Queue.test.ts unless prefixed
Queue.ts. Python adaptations: method names, Some | None, explicit generic E,
run_fork starts immediately; a manually drained creator dispatcher replaces
upstream's guessed yield counts. Stream.fromQueue tests assert Queue's drain
contract directly; Stream integration itself is not claimed. Local Queue imports
keep the RED checkpoint collectable, without a production stub or import skip.
"""

from math import inf, nan
from typing import Any, Literal, Never

import pytest
from test_suspend_exit import OperationScheduler

from peffect import (
    Cause,
    Die,
    Done,
    Effect,
    Fail,
    Failure,
    Fiber,
    Interrupt,
    Success,
    die,
    fail,
    never,
    run_fork,
    succeed,
    sync,
)
from peffect.option import Some


class Harness:
    """Every operation runs to completion/parking; only queued tasks need pumping."""

    def __init__(self) -> None:
        self.scheduler = OperationScheduler(lambda _: False)

    def spawn[A, E](self, effect: Effect[A, E, Never]) -> Fiber[A, E]:
        return run_fork(effect, scheduler=self.scheduler)

    def value[A, E](self, effect: Effect[A, E, Never]) -> A:
        fiber = self.spawn(effect)
        assert isinstance(fiber.exit, Success), fiber.exit
        return fiber.exit.value

    def queue(
        self, capacity: float = inf, strategy: Literal["suspend", "dropping", "sliding"] = "suspend"
    ) -> Any:
        from peffect import Queue

        return self.value(Queue[int, Done | str].make(capacity=capacity, strategy=strategy))

    def pump(self) -> None:
        self.scheduler.flush()


def then(effect: Any, following: Any) -> Any:
    def continue_with(_: object) -> Any:
        return following

    return effect.flat_map(continue_with)


def successful(fiber: Fiber[Any, Any], value: object) -> None:
    assert isinstance(fiber.exit, Success), fiber.exit
    assert fiber.exit.value == value


def failed(fiber: Fiber[Any, Any], cause: Cause[Any]) -> None:
    assert isinstance(fiber.exit, Failure), fiber.exit
    assert fiber.exit.cause == cause


def interrupted(fiber: Fiber[Any, Any]) -> None:
    assert isinstance(fiber.exit, Failure), fiber.exit
    assert fiber.exit.cause.is_interrupted


def test_constructors_guards_and_same_object_capability_views():
    """37-79; Queue.ts:429-644,1812-1927. All five constructors and guards."""
    from peffect import Queue
    from peffect.queue import is_dequeue, is_enqueue, is_queue

    h = Harness()
    for effect, capacity in [
        (Queue[int, Done].make(), inf),
        (Queue[int, Done].bounded(2), 2),
        (Queue[int, Done].dropping(2), 2),
        (Queue[int, Done].sliding(2), 2),
        (Queue[int, Done].unbounded(), inf),
    ]:
        q = h.value(effect)
        assert q.as_enqueue() is q and q.as_dequeue() is q
        assert is_queue(q) and is_enqueue(q) and is_dequeue(q)
        for other in [None, {}, object(), {"offer": True}]:
            assert not is_queue(other) and not is_enqueue(other) and not is_dequeue(other)
        assert q.capacity == capacity
        assert h.value(q.size()) == q.size_unsafe() == 0
        assert h.value(q.is_full()) is False
        assert q.is_full_unsafe() is False


def test_offer_all_copies_input_and_retains_fifo_at_batch_boundaries():
    """82-89,234-242,377-389,407-415."""
    h = Harness()
    q = h.queue()
    values = [1, 2]
    assert h.value(q.offer_all(values)) == []
    values[0] = 99
    assert q.offer_all_unsafe([3]) == []
    assert h.value(q.take_n(2)) == [1, 2]
    assert h.value(q.size()) == 1
    assert h.value(q.peek()) == 3
    assert h.value(q.take()) == 3
    assert h.value(q.offer(99)) is True
    assert h.value(q.take()) == 99
    assert h.value(q.size()) == 0


def test_bounded_batch_waits_and_releases_only_its_remaining_suffix():
    """91-111,469-488: interruption leaves the admitted prefix owned by Queue."""
    h = Harness()
    q = h.queue(2)
    input_ = [1, 2, 3, 4]
    producer = h.spawn(q.offer_all(input_))
    input_[2] = 99
    assert producer.exit is None
    assert h.value(q.take_all()) == [1, 2]
    successful(producer, [])
    assert h.value(q.take_all()) == [3, 4]
    cancelled = h.spawn(q.offer_all([5, 6, 7, 8]))
    assert cancelled.exit is None
    cancelled.interrupt_unsafe(900)
    interrupted(cancelled)
    assert h.value(q.take_all()) == [5, 6]
    assert h.value(q.offer(9)) is True
    assert h.value(q.take_all()) == [9]


@pytest.mark.parametrize("batch", [False, True])
def test_cancel_offer_after_end_withdraws_only_its_owned_suffix(batch: bool):
    """113-139: cancelling the last pending offer can finalize Closing."""
    h = Harness()
    q = h.queue(0 if batch else 1)
    if not batch:
        h.value(q.offer(1))
    producer = h.spawn(q.offer_all([1, 2]) if batch else q.offer(2))
    assert producer.exit is None
    assert h.value(q.end()) is True
    awaiter = h.spawn(q.await_())
    assert awaiter.exit is None
    producer.interrupt_unsafe(900)
    interrupted(producer)
    assert h.value(q.collect()) == ([] if batch else [1])
    successful(awaiter, None)
    failed(h.spawn(q.take()), Cause.fail(Done()))


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("receive", ["take", "take_all", "clear"])
def test_rendezvous_reserves_before_resuming_reentrant_producer(batch: bool, receive: str):
    """157-186. First consumer owns the message even if producer immediately takes."""
    h = Harness()
    q = h.queue(0)
    offered = q.offer_all([1]) if batch else q.offer(1)
    producer = h.spawn(then(offered, q.take()))
    assert producer.exit is None
    first = h.value(getattr(q, receive)())
    assert ([first] if receive == "take" else first) == [1]
    assert producer.exit is None
    assert q.shutdown_unsafe() is True
    interrupted(producer)


def test_resuming_blocked_producer_does_not_enqueue_twice():
    """141-155."""
    h = Harness()
    q = h.queue(1)
    h.value(q.offer(0))
    producer = h.spawn(then(q.offer(1), q.take()))
    assert producer.exit is None
    assert h.value(q.take()) == 0
    successful(producer, 1)
    assert h.value(q.clear()) == []


@pytest.mark.parametrize("batch", [False, True])
def test_reentrant_shutdown_settles_other_offer_and_preserves_removed_value(batch: bool):
    """188-209: shutdown from a resumed producer occurs inside the taking step."""
    h = Harness()
    q = h.queue(1)
    h.value(q.offer(0))
    producer = h.spawn(then(q.offer_all([1]) if batch else q.offer(1), q.shutdown()))
    pending = h.spawn(q.offer(2))
    assert producer.exit is None and pending.exit is None
    successful(h.spawn(q.take()), 0)
    successful(producer, True)
    successful(pending, False)
    assert q.size_unsafe() == 0


@pytest.mark.parametrize("batch", [False, True])
def test_reentrant_offer_cannot_overfill_released_capacity(batch: bool):
    """211-231. Recompute capacity after producer callback reentry."""
    h = Harness()
    q = h.queue(2)
    h.value(q.offer_all([0, 1]))
    producer = h.spawn(then(q.offer_all([2]) if batch else q.offer(2), q.offer(3)))
    pending = h.spawn(q.offer(4))
    assert h.value(q.take_all()) == [0, 1]
    assert q.size_unsafe() <= 2
    rest = h.value(q.clear())
    rest += h.value(q.clear())
    assert sorted(rest) == [2, 3, 4]
    successful(producer, True)
    successful(pending, True)


@pytest.mark.parametrize("receive", ["take_n", "take_between"])
def test_partial_batch_does_not_wake_or_spin_and_later_completes(receive: str):
    """244-281,310-329. Empty task queue is an explicit scheduler settle point."""
    h = Harness()
    q = h.queue()
    taker = h.spawn(q.take_n(3) if receive == "take_n" else q.take_between(2, 5))
    assert taker.exit is None
    assert q.offer_unsafe(1) is True
    assert len(h.scheduler.tasks) == 1
    h.pump()  # bounded flush trips a wake/retry busy loop, without a timeout oracle
    assert taker.exit is None and not h.scheduler.tasks
    assert q.size_unsafe() == 1
    assert q.offer_all_unsafe([2, 3]) == []
    h.pump()
    successful(taker, [1, 2, 3])


@pytest.mark.parametrize("end", [True, False])
def test_closing_short_batch_drains_before_terminal_failure(end: bool):
    """331-353,668-683."""
    h = Harness()
    q = h.queue()
    h.value(q.offer_all([1, 2]))
    taker = h.spawn(q.take_between(5, 8))
    assert taker.exit is None
    assert h.value(q.end() if end else q.fail("boom")) is True
    h.pump()
    successful(taker, [1, 2])
    failed(h.spawn(q.take_n(5)), Cause.fail(Done() if end else "boom"))


@pytest.mark.parametrize(
    ("minimum", "maximum", "expected"),
    [
        (1.9, 2.9, [1, 2]),
        (nan, 2, []),
        (-1, 3, []),
        (0, inf, []),
        (1, nan, []),
        (2, 1, [1]),
        (1, inf, [1, 2, 3]),
    ],
)
def test_counts_normalize_independently_and_done_precedes_zero_bounds(
    minimum: float, maximum: float, expected: list[int]
):
    """391-405; Queue.ts:1444-1452,2022-2038. min > max is not rejected."""
    h = Harness()
    q = h.queue()
    h.value(q.offer_all([1, 2, 3]))
    assert h.value(q.take_between(minimum, maximum)) == expected
    assert h.value(q.clear()) == [x for x in [1, 2, 3] if x not in expected]
    h.value(q.end())
    failed(h.spawn(q.take_n(0)), Cause.fail(Done()))
    failed(h.spawn(q.take_between(0, 0)), Cause.fail(Done()))


def test_take_n_count_and_capacity_threshold_are_source_faithful():
    """Queue.ts:1398-1400,2052-2056: n > capacity uses capacity readiness."""
    h = Harness()
    q = h.queue(2)
    h.value(q.offer_all([1, 2]))
    assert h.value(q.take_n(5)) == [1, 2]
    h.value(q.offer(3))
    assert h.value(q.take_n(1.9)) == [3]
    assert h.value(q.take_n(nan)) == []
    infinite = h.spawn(q.take_n(inf))
    assert infinite.exit is None
    h.value(q.offer_all([4, 5]))
    h.pump()
    successful(infinite, [4, 5])


@pytest.mark.parametrize("strategy", ["dropping", "sliding"])
def test_overflow_strategy_results_and_retained_order(
    strategy: Literal["suspend", "dropping", "sliding"],
):
    """417-435."""
    h = Harness()
    q = h.queue(2, strategy)
    assert h.value(q.offer_all([1, 2, 3, 4])) == ([3, 4] if strategy == "dropping" else [])
    assert h.value(q.offer(5)) is (strategy == "sliding")
    assert h.value(q.take_all()) == ([1, 2] if strategy == "dropping" else [4, 5])


def test_zero_sliding_discards_without_waiter_and_keeps_newest_waiting_handoff():
    """437-467: offerAll schedules; the following single offer releases synchronously."""
    h = Harness()
    q = h.queue(0, "sliding")
    assert h.value(q.offer(0)) is True
    assert q.size_unsafe() == 0
    taker = h.spawn(q.take())
    cancelled = h.spawn(q.take())
    assert q.offer_all_unsafe([1, 2]) == []
    assert taker.exit is None and cancelled.exit is None
    cancelled.interrupt_unsafe(9)
    assert q.offer_unsafe(3) is True
    successful(taker, 3)
    interrupted(cancelled)
    assert q.size_unsafe() == 0
    h.pump()


def test_cancelled_taker_does_not_steal_the_next_message():
    """490-505; cancellation removes only that taker."""
    h = Harness()
    q = h.queue()
    cancelled = h.spawn(q.take())
    cancelled.interrupt_unsafe(9)
    interrupted(cancelled)
    live = h.spawn(q.take())
    assert live.exit is None
    h.value(q.offer(1))
    h.pump()
    successful(live, 1)
    assert h.value(q.clear()) == []


@pytest.mark.parametrize("completion", ["end", "fail", "interrupt"])
def test_completion_drains_registered_batches_and_single_offers_in_order(completion: str):
    """558-619,668-683,763-776. Stream case adapted to direct Queue reads."""
    h = Harness()
    q = h.queue(2)
    first = h.spawn(q.offer_all([1, 2, 3, 4]))
    second = h.spawn(q.offer_all([5, 6, 7, 8]))
    third = h.spawn(q.offer(9))
    awaiter = h.spawn(q.await_())
    h.value(q.fail("boom") if completion == "fail" else getattr(q, completion)())
    assert awaiter.exit is None
    assert h.value(q.offer(10)) is False
    assert h.value(q.offer_all([10, 11])) == [10, 11]
    actual: list[int] = []
    while q.size_unsafe() > 0:
        actual.extend(h.value(q.take_all()))
    assert actual == list(range(1, 10))
    successful(first, [])
    successful(second, [])
    successful(third, True)
    terminal = h.spawn(q.take_all())
    if completion == "end":
        failed(terminal, Cause.fail(Done()))
        successful(awaiter, None)
        assert h.value(q.collect()) == []
    elif completion == "fail":
        failed(terminal, Cause.fail("boom"))
        failed(awaiter, Cause.fail("boom"))
    else:
        interrupted(terminal)
        interrupted(awaiter)


def test_collect_waits_then_completes_without_duplication():
    """407-415,569-580; collect removes Done, not real failures."""
    h = Harness()
    q = h.queue(2)
    collector = h.spawn(q.collect())
    assert collector.exit is None
    producer = h.spawn(q.offer_all([1, 2, 3, 4, 5]))
    h.value(q.end())
    h.pump()
    successful(producer, [])
    successful(collector, [1, 2, 3, 4, 5])


def test_poll_preserves_some_none_and_swallows_terminal_failures_only_as_empty():
    """621-640; Queue.ts:1534-1545 (including arbitrary terminal causes)."""
    from peffect import Queue

    h = Harness()
    q = h.value(Queue[int | None, str].unbounded())
    assert h.value(q.poll()) is None
    assert q.take_unsafe() is None
    assert h.value(q.offer(None)) is True
    assert h.value(q.poll()) == Some(None)
    q.offer_unsafe(7)
    taken = q.take_unsafe()
    assert isinstance(taken, Success) and taken.value == 7
    h.value(q.fail("boom"))
    assert h.value(q.poll()) is None
    terminal = q.take_unsafe()
    assert isinstance(terminal, Failure) and terminal.cause == Cause.fail("boom")


def test_peek_waits_without_removing_and_ignores_pending_rendezvous_offer():
    """642-666; Queue.ts:1574-1589: peek only sees buffered values."""
    h = Harness()
    q = h.queue()
    peeker = h.spawn(q.peek())
    assert peeker.exit is None
    h.value(q.offer(42))
    h.pump()
    successful(peeker, 42)
    assert h.value(q.peek()) == 42 and q.size_unsafe() == 1
    assert h.value(q.take()) == 42
    zero = h.queue(0)
    producer = h.spawn(zero.offer(1))
    peeker = h.spawn(zero.peek())
    assert producer.exit is None and peeker.exit is None
    assert h.value(zero.take()) == 1
    successful(producer, True)
    assert peeker.exit is None
    zero.shutdown_unsafe()
    interrupted(peeker)


@pytest.mark.parametrize("unsafe", [False, True])
def test_shutdown_discards_buffer_settles_suffix_and_interrupts_waiters(unsafe: bool):
    """685-756; shutdown is immediate, whereas fail/end/interrupt drain."""
    h = Harness()
    q = h.queue(1)
    producer = h.spawn(q.offer_all([1, 2, 3]))
    pending = h.spawn(q.offer(4))
    awaiter = h.spawn(q.await_())
    assert h.value(q.take()) == 1
    assert (q.shutdown_unsafe() if unsafe else h.value(q.shutdown())) is True
    successful(producer, [3])
    successful(pending, False)
    interrupted(awaiter)
    assert q.size_unsafe() == 0
    assert q.shutdown_unsafe() is False
    assert h.value(q.shutdown()) is False
    interrupted(h.spawn(q.take()))
    assert h.value(q.offer(5)) is False
    empty = h.queue()
    taker = h.spawn(empty.take())
    assert empty.shutdown_unsafe() is True
    interrupted(taker)  # no dispatcher pump: synchronous publication/notification


def test_shutdown_preserves_first_closing_or_completed_cause():
    """734-761; Queue.ts:989-1015,1211-1236."""
    h = Harness()
    for buffered in [False, True]:
        q = h.queue()
        if buffered:
            q.offer_unsafe(1)
        assert q.fail_cause_unsafe(Cause.fail("first")) is True
        assert q.end_unsafe() is False
        assert h.value(q.fail("second")) is False
        assert q.shutdown_unsafe() is buffered
        failed(h.spawn(q.take()), Cause.fail("first"))
        failed(h.spawn(q.await_()), Cause.fail("first"))


def test_await_waits_for_drain_and_can_be_cancelled_independently():
    """778-815; cancelled awaiter cannot cancel Queue or another awaiter."""
    h = Harness()
    q = h.queue()
    cancelled = h.spawn(q.await_())
    live = h.spawn(q.await_())
    cancelled.interrupt_unsafe(9)
    interrupted(cancelled)
    q.offer_unsafe(1)
    q.end_unsafe()
    assert live.exit is None
    assert h.value(q.take_all()) == [1]
    successful(live, None)
    failed(h.spawn(q.take()), Cause.fail(Done()))


@pytest.mark.parametrize("method", ["clear", "collect", "await_"])
@pytest.mark.parametrize("real", ["die", "fail", "interrupt", "mixed"])
def test_completion_filter_keeps_real_failures_and_order(method: str, real: str):
    """965-971; Queue.ts:1261-1272,1337-1353,1724-1740; Pull.ts:202-220."""
    h = Harness()
    q = h.queue()
    rest = {
        "die": Cause[Never]((Die("boom"),)),
        "fail": Cause.fail("boom"),
        "interrupt": Cause.interrupt(91),
        "mixed": Cause[str]((Interrupt(91), Die("boom"), Fail("typed"))),
    }[real]
    cause = Cause.fail(Done()) + rest
    assert h.value(q.fail_cause(cause)) is True
    result = h.spawn(getattr(q, method)())
    if real == "interrupt":  # Done + Interrupt is normal completion upstream
        successful(result, [] if method != "await_" else None)
    else:
        failed(result, rest)


@pytest.mark.parametrize("unsafe", [False, True])
def test_flush_wakes_synchronously_without_draining_or_completing(unsafe: bool):
    """817-849,884-918."""
    h = Harness()
    q = h.queue()
    taker = h.spawn(q.take())
    awaiter = h.spawn(q.await_())
    q.offer_unsafe(1)
    assert taker.exit is None
    if unsafe:
        q.flush_unsafe()
    else:
        h.value(q.flush())
    successful(taker, 1)
    assert awaiter.exit is None
    q.offer_unsafe(2)
    q.flush_unsafe()
    assert q.size_unsafe() == 1
    assert h.value(q.take()) == 2
    q.shutdown_unsafe()
    q.flush_unsafe()
    h.value(q.flush())
    assert q.offer_unsafe(3) is False


def test_creator_dispatcher_coalesces_even_across_flush_and_other_fiber_scheduler():
    """851-882; Queue.ts:480-502,2011-2020. Creator dispatcher owns wake tasks."""
    h = Harness()
    q = h.queue()
    other = OperationScheduler(lambda _: False)
    takers = [run_fork(q.take(), scheduler=other) for _ in range(10)]
    for i in range(8):
        q.offer_unsafe(i)
        q.flush_unsafe()
    assert len(h.scheduler.tasks) == 1
    assert not other.tasks
    for i, taker in enumerate(takers[:8]):
        successful(taker, i)
    assert all(t.exit is None for t in takers[8:])
    h.pump()
    q.offer_unsafe(8)
    assert len(h.scheduler.tasks) == 1
    h.pump()
    successful(takers[8], 8)
    q.shutdown_unsafe()
    interrupted(takers[9])


@pytest.mark.parametrize("batch", [False, True])
def test_zero_offer_after_buffered_handoff_waits_for_another_taker(batch: bool):
    """920-949. Zero capacity has transient buffer but no spare admission slot."""
    h = Harness()
    q = h.queue(0)
    taker = h.spawn(q.take())
    assert h.value(q.offer_all([1])) == []
    producer = h.spawn(q.offer_all([2]) if batch else q.offer(2))
    assert producer.exit is None
    h.pump()
    successful(taker, 1)
    assert producer.exit is None
    assert h.value(q.take()) == 2
    successful(producer, [] if batch else True)


def test_zero_take_all_consumes_buffer_before_pending_offer():
    """951-963."""
    h = Harness()
    q = h.queue(0)
    waiting = h.spawn(q.take())
    producer = h.spawn(q.offer_all([1, 2]))
    assert h.value(q.take_all()) == [1]
    h.pump()
    successful(waiting, 2)
    successful(producer, [])


def test_zero_batch_closing_drain_finishes_using_all_nonblocking_receivers():
    """973-990: clear/poll each consume one pending rendezvous element."""
    h = Harness()
    q = h.queue(0)
    producer = h.spawn(q.offer_all([1, 2, 3]))
    assert h.value(q.take_all()) == [1]
    assert h.value(q.end()) is True
    assert h.value(q.clear()) == [2]
    assert h.value(q.poll()) == Some(3)
    successful(producer, [])
    successful(h.spawn(q.await_()), None)
    assert h.value(q.clear()) == []


@pytest.mark.parametrize("outcome", ["success", "fail", "die", "interrupt"])
def test_into_completes_queue_from_producer_exit_without_enqueuing_result(outcome: str):
    """Queue.ts:1963-1991. into restores producer interruptibility then publishes masked."""
    h = Harness()
    q = h.queue()
    q.offer_unsafe(1)
    effect = {
        "success": succeed(999),
        "fail": fail("boom"),
        "die": die(ValueError("boom")),
        "interrupt": never,
    }[outcome]
    producer = h.spawn(q.into(effect))
    if outcome == "interrupt":
        assert producer.exit is None
        producer.interrupt_unsafe(91)
    assert h.value(q.take()) == 1
    terminal = h.spawn(q.take())
    if outcome == "success":
        failed(terminal, Cause.fail(Done()))
    elif outcome == "fail":
        failed(terminal, Cause.fail("boom"))
    elif outcome == "die":
        assert isinstance(terminal.exit, Failure)
        assert isinstance(terminal.exit.cause.reasons[0], Die)
        assert isinstance(terminal.exit.cause.reasons[0].defect, ValueError)
    else:
        interrupted(terminal)
    if outcome != "interrupt":
        successful(producer, True)
    # A second into still runs its producer but cannot replace the terminal cause.
    ran: list[bool] = []
    successful(h.spawn(q.into(sync(lambda: ran.append(True)))), False)
    assert ran == [True]


def test_capacity_pathologies_are_literal_vendor_characterizations():
    """Queue.ts:739-778,850-880,1926,2052; not the normal-capacity property domain."""
    h = Harness()
    zero = h.queue(0)
    assert zero.is_full_unsafe() is True
    fractional = h.queue(0.5)
    assert fractional.offer_all_unsafe([1, 2, 3]) == [2, 3]
    assert fractional.size_unsafe() == 1 and fractional.is_full_unsafe() is False
    not_number = h.queue(nan)
    assert not_number.offer_all_unsafe([1, 2]) == [1, 2]
    assert not_number.offer_unsafe(3) is True
    assert not_number.is_full_unsafe() is False
    overflowing = h.queue(0.5)
    producer = h.spawn(overflowing.offer_all([1, 2, 3, 4]))
    assert producer.exit is None
    assert h.value(overflowing.take()) == 1
    successful(producer, [])
    assert overflowing.size_unsafe() == 3  # literal n === 0 guard, not integer normalization
    assert h.value(overflowing.clear()) == [2, 3, 4]
    negative = h.queue(-1)
    result = h.spawn(negative.take_n(1))
    assert isinstance(result.exit, Failure)
    assert any(isinstance(r, Die) for r in result.exit.cause.reasons)
    negative.shutdown_unsafe()  # do not start negative-capacity take's unbounded retry


def test_finalization_publishes_done_before_reentrant_awaiters_and_takers():
    """Source-derived Queue.ts:2161-2175: terminal state precedes all callbacks."""
    h = Harness()
    q = h.queue()
    observed: list[bool] = []

    def await_done(_: None) -> None:
        observed.extend([q.offer_unsafe(9), q.shutdown_unsafe(), q.end_unsafe()])

    def take_failed(_: Cause[Any]) -> Effect[None, Never, Never]:
        observed.append(q.offer_unsafe(10))
        return succeed(None)

    awaiter = h.spawn(q.await_().map(await_done))
    taker = h.spawn(q.take().catch_cause(take_failed))
    assert q.end_unsafe() is True
    successful(taker, None)
    successful(awaiter, None)
    assert observed == [False, False, False, False]
    assert q.size_unsafe() == 0


@pytest.mark.parametrize("strategy", ["suspend", "dropping", "sliding"])
@pytest.mark.parametrize("batch", [False, True])
def test_zero_capacity_all_strategies_can_handoff_to_registered_taker(
    strategy: Literal["suspend", "dropping", "sliding"], batch: bool
):
    """Queue.ts:739-765,850-879. A waiting taker is a zero-capacity admission slot."""
    h = Harness()
    q = h.queue(0, strategy)
    taker = h.spawn(q.take())
    offered = h.spawn(q.offer_all([1]) if batch else q.offer(1))
    h.pump()
    successful(offered, [] if batch else True)
    successful(taker, 1)
    assert q.size_unsafe() == 0


@pytest.mark.parametrize("value", [True, False, None, 123])
def test_into_ignores_every_producer_success_value(value: object):
    """Queue.ts:1987: onSuccess always calls end, including boolean results."""
    h = Harness()
    q = h.queue()
    successful(h.spawn(q.into(succeed(value))), True)
    failed(h.spawn(q.take()), Cause.fail(Done()))
    assert q.size_unsafe() == 0


def test_fractional_sliding_trims_a_normalized_removal_count():
    """Queue.ts:858-864 delegates to MutableList.ts:516 (floor, not ceil)."""
    h = Harness()
    q = h.queue(0.5, "sliding")
    assert q.offer_all_unsafe([1, 2, 3]) == []
    assert h.value(q.clear()) == [3]


def test_nan_capacity_uses_js_falsey_readiness_and_still_schedules_empty_batch():
    """Queue.ts:868-882,2052-2056: NaN <= 0 is false; NaN || 1 is one."""
    h = Harness()
    q = h.queue(nan)
    taker = h.spawn(q.take_n(5))
    assert q.offer_all_unsafe([]) == []
    assert len(h.scheduler.tasks) == 1
    assert q.offer_unsafe(1) is True
    h.pump()
    successful(taker, [1])
