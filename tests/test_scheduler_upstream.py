"""Scheduler: the dispatcher a fiber yields, resumes and starts children on. Upstream is pinned at
vendor/effect-v4 commit 289a53f (Effect v4.1.4).

The first section ports test/Scheduler.test.ts. Not ported: the three runSyncExit tests
(peffect has no synchronous runner) and the microtask fallback (Cloudflare's setImmediate).
The second section is source-derived and labelled: assertions read off internal/effect.ts
(currentDispatcher :567-570, setContext :801-803, forkUnsafe :5629-5633) and Scheduler.ts,
with the expected values measured against the pinned source (scratch upstream_scheduler.ts).

Names are snake_case forms of Scheduler.ts: `Scheduler`, `MaxOpsBeforeYield` and
`PreventSchedulerYield` References, `MixedScheduler(execution_mode, set_immediate)`,
`execution_mode`, `should_yield(fiber)`, `make_dispatcher()`, `schedule_task(task, priority)`,
`flush()`, and `fiber.current_op_count` (Fiber.ts:77).

Adaptations used throughout:
- `Effect.provideService(Ref, v)` is `provide(Ref.context(v))`; `Effect.all` over effects is
  `for_each(effects, lambda e, _: e)`; `Effect.forkDetach` is `fork_daemon`.
- `Effect.runFork(eff, { scheduler })` then `pollUnsafe()` is a daemon started immediately with
  the scheduler provided, then `fiber.exit`: both run the first slice before returning.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Literal, Never

import pytest

from peffect import (
    Cause,
    Effect,
    Failure,
    Fiber,
    Success,
    for_each,
    fork,
    fork_daemon,
    gen,
    provide,
    run,
    succeed,
    sync,
    yield_now,
)
from peffect.scheduler import (
    MaxOpsBeforeYield,
    MixedScheduler,
    PreventSchedulerYield,
    Scheduler,
    SchedulerDispatcher,
)

type Task = Callable[[], None]


async def bounded[T](work: Coroutine[Any, Any, T]) -> T:
    """Watchdog only: a scheduler that drops a task would otherwise hang the run."""
    async with asyncio.timeout(5):
        return await work


def manual_immediate(tasks: list[Task]) -> Callable[[Task], Task]:
    """Scheduler.test.ts's setImmediate stand-in: queue the batch runner, return its canceller."""

    def set_immediate(task: Task) -> Task:
        cancelled = [False]
        tasks.append(lambda: None if cancelled[0] else task())
        return lambda: cancelled.__setitem__(0, True)

    return set_immediate


# == upstream ==================================================================================


async def test_mixed_scheduler_orders_by_priority_sync():
    """Scheduler.test.ts:42 "MixedScheduler orders by priority (sync)"."""
    dispatcher = MixedScheduler("sync").make_dispatcher()
    order: list[str] = []
    for label, priority in [("p0-1", 0), ("p10-1", 10), ("p-1-1", -1), ("p10-2", 10), ("p0-2", 0)]:
        dispatcher.schedule_task(lambda label=label: order.append(label), priority)
    assert order == []
    dispatcher.flush()
    assert order == ["p-1-1", "p0-1", "p0-2", "p10-1", "p10-2"]


async def test_mixed_scheduler_is_fifo_within_a_priority():
    """Scheduler.test.ts:66 "MixedScheduler is FIFO within a priority"."""
    dispatcher = MixedScheduler("sync").make_dispatcher()
    order: list[int] = []
    for n in [1, 2, 3]:
        dispatcher.schedule_task(lambda n=n: order.append(n), 5)
    dispatcher.flush()
    assert order == [1, 2, 3]


class CountingScheduler:
    """Scheduler.test.ts:81's scheduler: counts should_yield calls and never yields."""

    execution_mode: Literal["sync"] = "sync"

    def __init__(self) -> None:
        self.calls = 0

    def should_yield(self, fiber: Any) -> bool:
        self.calls += 1
        return False

    def make_dispatcher(self) -> Any:
        return object()


async def test_prevent_scheduler_yield_disables_should_yield_checks():
    """Scheduler.test.ts:79 "PreventSchedulerYield disables shouldYield checks"."""
    scheduler = CountingScheduler()
    await run(provide(Scheduler.context(scheduler))(sync(lambda: None)))
    assert scheduler.calls > 0

    scheduler.calls = 0
    both = Scheduler.context(scheduler) | PreventSchedulerYield.context(True)
    await run(provide(both)(sync(lambda: None)))
    assert scheduler.calls == 0


class Boom(Exception):
    pass


class ThrowingDispatcher:
    def __init__(self, owner: "ThrowingScheduler") -> None:
        self.owner = owner

    def schedule_task(self, task: Task, priority: int) -> None:
        self.owner.attempts += 1
        if self.owner.attempts <= 5:  # bounded, so a runtime that retries cannot spin forever
            raise self.owner.defect
        self.owner.tasks.append(task)

    def flush(self) -> None:
        pass


class ThrowingScheduler:
    execution_mode: Literal["async"] = "async"

    def __init__(self) -> None:
        self.defect = Boom("dispatch failed")
        self.attempts = 0
        self.tasks: list[Task] = []

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        return fiber.current_op_count >= 2

    def make_dispatcher(self) -> ThrowingDispatcher:
        return ThrowingDispatcher(self)


async def test_automatic_dispatch_failures_terminate_the_fiber_with_a_defect():
    """Scheduler.test.ts:105 "automatic dispatch failures terminate the fiber with a defect"."""
    scheduler = ThrowingScheduler()
    program = for_each([1, 2, 3], lambda n, _: succeed(n), concurrency="unbounded")
    provided = provide(Scheduler.context(scheduler))(program)
    fiber: Fiber[list[int], Never] = await run(fork_daemon(provided, start_immediately=True))
    ex = fiber.exit
    assert isinstance(ex, Failure)
    assert ex.cause == Cause.die(scheduler.defect)


@pytest.mark.parametrize("budget", [1, 2, 3])
async def test_concurrent_for_each_completes_with_max_ops_before_yield(budget: int):
    """Scheduler.test.ts:131 "concurrent forEach completes with MaxOpsBeforeYield ${budget}"."""
    tasks: list[Task] = []
    scheduler = MixedScheduler("async", manual_immediate(tasks))
    program = for_each([1, 2, 3], lambda n, _: succeed(n), concurrency="unbounded")
    budgeted = provide(MaxOpsBeforeYield.context(budget))(program)
    provided = provide(Scheduler.context(scheduler))(budgeted)
    fiber: Fiber[list[int], Never] = await run(fork_daemon(provided, start_immediately=True))
    for _ in range(1000):  # bounded: a yielding livelock fails instead of hanging
        if not tasks:
            break
        tasks.pop(0)()
    ex = fiber.exit
    assert isinstance(ex, Success)
    assert ex.value == [1, 2, 3]


async def test_inlined_map_steps_count_against_max_ops_before_yield():
    """Scheduler.test.ts:159 "inlined map steps count against MaxOpsBeforeYield"."""
    order: list[str] = []

    def chain(label: str) -> Effect[int, Never, Never]:
        def step(n: int) -> int:
            order.append(label)
            return n + 1

        effect: Effect[int, Never, Never] = succeed(0)
        for _ in range(100):
            effect = effect.map(step)
        return effect

    both = for_each([chain("a"), chain("b")], lambda e, _: e, concurrency="unbounded")
    await bounded(run(provide(MaxOpsBeforeYield.context(16))(both)))
    assert "".join(order) != "a" * 100 + "b" * 100


# == source-derived ============================================================================


class RecordingScheduler:
    """Delegates to a MixedScheduler; logs `name#n@priority` per task, n = dispatcher made."""

    execution_mode: Literal["async"] = "async"

    def __init__(self, name: str, log: list[str]) -> None:
        self.name, self.log, self.made = name, log, 0
        self.inner = MixedScheduler()

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        return self.inner.should_yield(fiber)

    def make_dispatcher(self) -> "RecordingDispatcher":
        self.made += 1
        return RecordingDispatcher(f"{self.name}#{self.made}", self.inner.make_dispatcher(), self)

    def provide[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R]:
        provided: Effect[A, E, R] = provide(Scheduler.context(self))(eff)
        return provided


class RecordingDispatcher:
    def __init__(self, label: str, inner: SchedulerDispatcher, owner: RecordingScheduler) -> None:
        self.label, self.inner, self.owner = label, inner, owner

    def schedule_task(self, task: Task, priority: int) -> None:
        self.owner.log.append(f"{self.label}@{priority}")
        self.inner.schedule_task(task, priority)

    def flush(self) -> None:
        self.inner.flush()


async def test_s2_a_fiber_makes_its_dispatcher_on_first_dispatch_and_keeps_it():
    """Source-derived S2: `currentDispatcher` is `_dispatcher ??= scheduler.makeDispatcher()`
    (internal/effect.ts:567-570). Measured upstream: [0, 1, ["s#1@0", "s#1@0", "s#1@0"]]."""
    log: list[str] = []
    s = RecordingScheduler("s", log)
    await bounded(run(s.provide(sync(lambda: 1))))
    quiet = s.made
    await bounded(
        run(s.provide(yield_now.flat_map(lambda _: yield_now).flat_map(lambda _: yield_now)))
    )
    assert [quiet, s.made, log] == [0, 1, ["s#1@0"] * 3]


async def test_s3_providing_a_scheduler_switches_the_dispatcher_and_leaving_drops_it():
    """Source-derived S3: setContext clears `_dispatcher` when the scheduler changes
    (internal/effect.ts:801-803), on entering a provide and on leaving it. Measured upstream,
    in time order: ["out#1@0", "in#1@0", "out#2@0"]."""
    log: list[str] = []
    outer, inner = RecordingScheduler("out", log), RecordingScheduler("in", log)

    @gen
    def program():
        yield from yield_now
        yield from inner.provide(yield_now)
        yield from yield_now

    await bounded(run(outer.provide(program())))
    assert log == ["out#1@0", "in#1@0", "out#2@0"]


@pytest.mark.parametrize("kind", ["fork", "fork_daemon"])
async def test_s4_s5_scheduled_children_start_on_the_forking_fibers_dispatcher(
    kind: Literal["fork", "fork_daemon"],
):
    """Source-derived S4 (forkChild) and S5 (forkDetach): forkUnsafe runs an immediate child
    inline and otherwise schedules its start on the parent's `currentDispatcher` at priority 0
    (internal/effect.ts:5629-5633). Measured upstream for both:
    [["s#1@0"], ["s#1@0", "s#1@0"], 1, 1]."""
    log: list[str] = []
    s = RecordingScheduler("s", log)
    start = fork if kind == "fork" else fork_daemon
    out: list[object] = []

    @gen
    def program():
        yield from yield_now  # the parent's dispatcher is s#1
        a: Fiber[None, Never] = yield from start(succeed(None), start_immediately=True)
        out.append(list(log))
        b: Fiber[None, Never] = yield from start(succeed(None))
        out.extend([list(log), s.made])
        yield from a.await_()
        yield from b.await_()
        out.append(s.made)

    await bounded(run(s.provide(program())))
    assert out == [["s#1@0"], ["s#1@0", "s#1@0"], 1, 1]


async def test_flush_runs_queued_tasks_once_and_cancels_the_pending_immediate():
    """Source-derived (Scheduler.ts MixedSchedulerDispatcher.flush): flush cancels the armed
    setImmediate, then drains; the next schedule arms a new one. Measured upstream: [1]."""
    tasks: list[Task] = []
    dispatcher = MixedScheduler("async", manual_immediate(tasks)).make_dispatcher()
    order: list[int] = []
    dispatcher.schedule_task(lambda: order.append(1), 0)
    dispatcher.flush()
    tasks.pop(0)()  # the cancelled batch runner fires anyway: it must do nothing
    dispatcher.schedule_task(lambda: order.append(2), 0)
    assert (order, len(tasks)) == ([1], 1)
    tasks.pop(0)()
    assert order == [1, 2]
