"""Queue's atomic-delivery prerequisite, pinned to Effect v4 289a53f7.

Source-derived: Queue.ts:2042-2049 (suspendTake) evaluates terminal Exits in the
removing step; :517-555 in Queue.test.ts sweeps interruption at yield checks.
The new public spelling is suspend_exit; no Queue prototype is used here.

Oracle: the one message is either still buffered or has reached the first
consumer, exactly once. The scheduler interrupts only at explicit operation
boundaries. There are no timers, fixed yields, or guessed operation numbers.
The ordinary-sync control demonstrates the unsafe removal/delivery boundary.
"""

from collections import deque
from collections.abc import Callable
from typing import Any, Literal, Never

import pytest

from peffect import (
    Cause,
    Effect,
    Failure,
    Fiber,
    Success,
    async_,
    exit_,
    provide,
    run,
    run_fork,
    succeed,
    sync,
)
from peffect.scheduler import MaxOpsBeforeYield, SchedulerDispatcher


class OperationScheduler:
    """White-box scheduler precedent: test_semaphore_upstream.QueueingScheduler.

    Each should_yield call is an observable boundary before the next node.
    Interrupting a running fiber records a pending interrupt; returning True
    parks its next node, so the kernel delivers the interrupt before resumption.
    """

    execution_mode: Literal["async"] = "async"

    def __init__(self, on_operation: Callable[[Fiber[Any, Any]], bool]) -> None:
        self.on_operation = on_operation
        self.tasks: deque[Callable[[], None]] = deque()
        self.checks = 0

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        self.checks += 1
        return self.on_operation(fiber)

    def make_dispatcher(self) -> SchedulerDispatcher:
        return self

    def schedule_task(self, task: Callable[[], None], priority: int) -> None:
        self.tasks.append(task)

    def flush(self) -> None:
        for _ in range(1000):
            if not self.tasks:
                return
            self.tasks.popleft()()
        raise AssertionError("operation scheduler did not quiesce within 1000 tasks")


def start[A, E](effect: Effect[A, E, Never], scheduler: OperationScheduler) -> Fiber[A, E]:
    return run_fork(provide(MaxOpsBeforeYield.context(0))(effect), scheduler=scheduler)


def test_sync_control_exposes_the_removal_delivery_interrupt_boundary():
    """Harness sensitivity on existing production code, not a mock of the new node."""
    messages = deque([41])
    delivered: list[int] = []
    interrupted: list[bool] = []

    def at_boundary(fiber: Fiber[Any, Any]) -> bool:
        if not messages and not delivered and not interrupted:
            interrupted.append(True)
            fiber.interrupt_unsafe(99)
            return True
        return False

    scheduler = OperationScheduler(at_boundary)
    fiber = start(sync(messages.popleft).map(lambda a: delivered.append(a)), scheduler)
    scheduler.flush()
    assert interrupted == [True], "the unsafe post-removal boundary must be reached"
    assert isinstance(fiber.exit, Failure)
    assert fiber.exit.cause.is_interrupted
    assert list(messages) == []
    assert delivered == []


def test_terminal_success_delivers_the_removed_message_before_another_yield_check():
    from peffect import suspend_exit

    messages = deque([41])
    delivered: list[int] = []
    interrupted: list[bool] = []

    def at_boundary(fiber: Fiber[Any, Any]) -> bool:
        if not messages and not delivered and not interrupted:
            interrupted.append(True)
            fiber.interrupt_unsafe(99)
            return True
        return False

    scheduler = OperationScheduler(at_boundary)
    effect = suspend_exit(lambda: Success(messages.popleft())).map(delivered.append)
    fiber = start(effect, scheduler)
    scheduler.flush()
    assert delivered + list(messages) == [41], "removed message lost before delivery"
    assert delivered == [41]
    assert interrupted == [], "there must be no operation boundary inside the handoff"
    assert isinstance(fiber.exit, Success)


def test_terminal_failure_reaches_its_handler_before_another_yield_check():
    from peffect import suspend_exit

    produced: list[bool] = []
    handled: list[Cause[str]] = []
    cause = Cause.fail("terminal")
    interrupted: list[bool] = []

    def produce() -> Effect[Never, str, Never]:
        produced.append(True)
        return Failure(cause)

    def handle(error: Cause[str]) -> Effect[None, Never, Never]:
        handled.append(error)
        return succeed(None)

    def at_boundary(fiber: Fiber[Any, Any]) -> bool:
        if produced and not handled and not interrupted:
            interrupted.append(True)
            fiber.interrupt_unsafe(99)
            return True
        return False

    scheduler = OperationScheduler(at_boundary)
    fiber = start(suspend_exit(produce).catch_cause(handle), scheduler)
    scheduler.flush()
    assert handled == [cause]
    assert interrupted == []
    assert isinstance(fiber.exit, Success)


def test_reentrant_interrupt_during_removal_still_delivers_to_the_first_consumer():
    """Queue.ts:2094-2145 can resume producers inside a destructive take.

    A producer can interrupt the taking fiber synchronously. Its pending
    interrupt must not insert another run-loop step before value delivery.
    Later interruption of the continuation is allowed; removal is not rollback.
    """
    from peffect import suspend_exit
    from peffect.core import WithFiber

    messages = deque([41])
    delivered: list[int] = []

    def remove(current: Fiber[Any, Any]) -> Effect[int, Never, Never]:
        message = messages.popleft()
        current.interrupt_unsafe(99)
        return Success(message)

    scheduler = OperationScheduler(lambda _: False)
    effect = WithFiber(lambda current: suspend_exit(lambda: remove(current)).map(delivered.append))
    fiber = run_fork(effect, scheduler=scheduler)
    scheduler.flush()
    assert delivered + list(messages) == [41]
    assert delivered == [41]
    assert isinstance(fiber.exit, Failure)
    assert fiber.exit.cause.is_interrupted


async def test_thunk_is_lazy_and_reexecuted_for_each_run():
    from peffect import suspend_exit

    calls: list[int] = []

    def produce() -> Effect[int, Never, Never]:
        calls.append(len(calls) + 1)
        return Success(calls[-1])

    effect = suspend_exit(produce)
    assert calls == []
    assert await run(effect) == 1
    assert await run(effect) == 2
    assert calls == [1, 2]


def test_returned_nonterminal_is_delegated_to_a_later_operation():
    from peffect import suspend_exit

    events: list[str] = []
    yielded: list[bool] = []

    def produce() -> Effect[int, Never, Never]:
        events.append("thunk")
        return sync(lambda: events.append("inner") or 7)

    def at_boundary(_: Fiber[Any, Any]) -> bool:
        if events == ["thunk"] and not yielded:
            yielded.append(True)
            return True
        return False

    scheduler = OperationScheduler(at_boundary)
    fiber = start(suspend_exit(produce), scheduler)
    assert events == ["thunk"], "only terminal Exits may be evaluated inside the thunk step"
    assert fiber.exit is None
    assert yielded == [True]
    scheduler.flush()
    assert events == ["thunk", "inner"]
    assert isinstance(fiber.exit, Success)
    assert fiber.exit.value == 7


def test_returned_async_can_park_and_resume_normally():
    from peffect import suspend_exit

    resumes: list[Callable[[Effect[int, str, Never]], None]] = []

    def register(resume: Callable[[Effect[int, str, Never]], None]) -> None:
        resumes.append(resume)

    scheduler = OperationScheduler(lambda _: False)
    fiber = start(suspend_exit(lambda: async_(register)), scheduler)
    assert fiber.exit is None
    assert len(resumes) == 1
    resumes[0](Success(17))
    assert isinstance(fiber.exit, Success)
    assert fiber.exit.value == 17


async def test_raising_thunk_becomes_one_defect_without_reexecution():
    from peffect import suspend_exit

    boom = ValueError("thunk boom")
    calls: list[bool] = []

    def produce() -> Effect[int, str, Never]:
        calls.append(True)
        raise boom

    result = await run(exit_(suspend_exit(produce)))
    assert isinstance(result, Failure)
    assert result.cause.defects == [boom]
    assert result.cause.failures == []
    assert calls == [True]


@pytest.mark.parametrize("interrupt_at", range(1, 17))
def test_operation_boundary_interrupt_sweep_conserves_the_message(interrupt_at: int):
    """Bounded 16-boundary sweep; unvisited late boundaries are separately identified."""
    from peffect import suspend_exit

    messages = deque([7])
    delivered: list[int] = []
    checks = 0
    interrupted: list[bool] = []

    def at_boundary(fiber: Fiber[Any, Any]) -> bool:
        nonlocal checks
        checks += 1
        if checks == interrupt_at:
            interrupted.append(True)
            fiber.interrupt_unsafe(99)
            return True
        return False

    scheduler = OperationScheduler(at_boundary)
    effect = suspend_exit(lambda: Success(messages.popleft())).map(delivered.append)
    fiber = start(effect, scheduler)
    scheduler.flush()
    assert fiber.exit is not None, (interrupt_at, checks)
    assert delivered + list(messages) == [7], (interrupt_at, checks, fiber.exit)
    assert interrupted == ([True] if checks >= interrupt_at else [])
