"""Latch unit at Effect v4 pin 289a53f7.

Upstream: test/Latch.test.ts (every case, :6-:192); implementation Latch.ts and the `Latch`
class in internal/effect.ts:5928-6017. The first section ports each upstream case; the second
is source-derived and labelled with the source lines it reads; the third is a generated
operation-sequence property against an independent model of that source.

Names are snake_case forms of Latch.ts, as instance methods like Deferred's: `Latch.make(open)`,
`Latch.make_unsafe(open)`, `open()`, `open_unsafe()`, `release()`, `await_()`, `close()`,
`close_unsafe()`, `when_open(eff)`, `is_open()`. Upstream's `open`/`release`/`await`/`close`
are Effect-valued fields; here they are methods returning the effect (Deferred.await_ precedent).

Adaptations used throughout (as test_deferred_upstream.py and test_scheduler_upstream.py):
- `forkChild(eff, { startImmediately: true })` is `fork(eff, start_immediately=True)`;
  `fiber.pollUnsafe()` is `fiber.exit`; `Fiber.interrupt(f)` is `f.interrupt()`;
  `Fiber.join(f)` is `f.join()`; `Fiber.await(f)` is `f.await_()`.
- `Effect.provideService(Scheduler.Scheduler, s)` is `provide(Scheduler.context(s))`.
- `peffect.latch` does not exist yet, so it is looked up when each test runs: every test fails
  until it lands instead of the file failing to import. The lookup becomes an import then.
"""

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Never, Protocol

from hypothesis import given, settings
from hypothesis import strategies as st

from peffect import Effect, Exit, Failure, Fiber, Interrupt, fork, gen, never, provide, run, sync
from peffect import yield_now as yield_now_
from peffect.scheduler import Scheduler

type Task = Callable[[], None]


class LatchLike(Protocol):
    """The proposed instance API (Latch.ts:53-127); replaced by the real class on landing."""

    def open(self) -> Effect[bool, Never, Never]: ...
    def open_unsafe(self) -> bool: ...
    def release(self) -> Effect[bool, Never, Never]: ...
    def await_(self) -> Effect[None, Never, Never]: ...
    def close(self) -> Effect[bool, Never, Never]: ...
    def close_unsafe(self) -> bool: ...
    def when_open[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[A, E, R]: ...
    def is_open(self) -> bool: ...


class LatchClass(Protocol):
    """Constructors (Latch.ts:164, :200): closed unless ``open`` is True."""

    def make(self, open: bool = False, /) -> Effect[LatchLike, Never, Never]: ...
    def make_unsafe(self, open: bool = False, /) -> LatchLike: ...


def latch_api() -> LatchClass:
    """`peffect.latch.Latch`, imported when the test runs: ModuleNotFoundError until it exists."""
    return importlib.import_module("peffect.latch").Latch


async def bounded(eff: Effect[None, Any, Never], seconds: float = 5) -> None:
    """Watchdog only (test_scheduler_upstream.py `bounded`): a lost wakeup fails, not hangs."""
    async with asyncio.timeout(seconds):
        await run(eff)


def has_interrupts[A, E](ex: Exit[A, E] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


class QueueingScheduler:
    """Latch.test.ts's inline scheduler (:60-:73): every dispatched task is kept in ``tasks`` and
    runs only when the test calls it; ``flush`` does nothing and the fiber never yields."""

    execution_mode: Literal["async"] = "async"

    def __init__(self) -> None:
        self.tasks: list[Task] = []

    def should_yield(self, fiber: Any) -> bool:
        return False

    def make_dispatcher(self) -> "QueueingScheduler":
        return self

    def schedule_task(self, task: Task, priority: int) -> None:
        self.tasks.append(task)

    def flush(self) -> None:
        pass


def on[A, E, R](scheduler: QueueingScheduler, eff: Effect[A, E, R]) -> Effect[A, E, R]:
    return provide(Scheduler.context(scheduler))(eff)


def run_task(scheduler: QueueingScheduler) -> Effect[None, Never, Never]:
    """`tasks.shift()!()` from inside the test fiber."""
    return sync(lambda: scheduler.tasks.pop(0)())


# == upstream: Latch.test.ts ===================================================================


async def test_release_wakes_current_waiters_and_keeps_the_latch_closed():
    """Latch.test.ts:6."""
    Latch = latch_api()

    @gen
    def program():
        latch = yield from Latch.make(False)
        waiter: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        assert waiter.exit is None
        yield from latch.release()
        yield from yield_now_
        yield from yield_now_
        assert waiter.exit is not None

    await bounded(program())


async def test_is_open_reflects_the_state_of_the_latch():
    """Latch.test.ts:23."""
    Latch = latch_api()

    @gen
    def program():
        latch = yield from Latch.make(False)
        assert latch.is_open() is False
        yield from latch.open()
        yield from yield_now_
        assert latch.is_open() is True

    await bounded(program())


async def test_open_then_close_does_not_resume_waiters_registered_after_close():
    """Latch.test.ts:35."""
    Latch = latch_api()

    @gen
    def program():
        latch = Latch.make_unsafe(False)
        before: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        yield from latch.open()
        yield from latch.close()
        after: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        yield from yield_now_
        yield from yield_now_
        assert before.exit is not None
        assert after.exit is None

    await bounded(program())


async def test_release_while_a_flush_is_pending_covers_the_new_waiters():
    """Latch.test.ts:57."""
    Latch = latch_api()
    scheduler = QueueingScheduler()

    @gen
    def program():
        latch = yield from Latch.make(False)
        first: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        yield from on(scheduler, latch.release())
        assert len(scheduler.tasks) == 1
        second: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        yield from on(scheduler, latch.release())
        assert len(scheduler.tasks) == 1
        yield from run_task(scheduler)
        yield from first.join()
        yield from second.join()
        assert latch.is_open() is False

    await bounded(program())


async def test_release_does_not_resume_waiters_registered_after_the_release():
    """Latch.test.ts:95."""
    Latch = latch_api()

    @gen
    def program():
        latch = yield from Latch.make(False)
        covered: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        yield from latch.release()
        late: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        yield from yield_now_
        yield from yield_now_
        assert covered.exit is not None
        assert late.exit is None

    await bounded(program())


async def test_open_unsafe_does_not_resume_waiters_registered_after_a_reentrant_close():
    """Latch.test.ts:116."""
    Latch = latch_api()
    scheduler = QueueingScheduler()
    second: list[Fiber[None, Never]] = []

    @gen
    def program():
        latch = Latch.make_unsafe(False)

        @gen
        def first():
            yield from latch.await_()
            yield from sync(latch.close_unsafe)
            second.append((yield from fork(latch.await_(), start_immediately=True)))
            return (yield from never)

        yield from fork(first(), start_immediately=True)
        yield from on(scheduler, latch.release())
        yield from sync(latch.open_unsafe)
        assert second[0].exit is None
        assert len(scheduler.tasks) == 1
        yield from run_task(scheduler)
        assert second[0].exit is None

    await bounded(program())


async def test_interrupting_a_waiter_removes_it_from_a_pending_flush():
    """Latch.test.ts:153."""
    Latch = latch_api()
    scheduler = QueueingScheduler()
    resumed: list[str] = []

    @gen
    def program():
        latch = yield from Latch.make(False)
        waiter: Fiber[None, Never] = yield from fork(
            latch.await_().flat_map(lambda _: sync(lambda: resumed.append("waiter"))),
            start_immediately=True,
        )
        yield from on(scheduler, latch.release())
        assert len(scheduler.tasks) == 1
        yield from waiter.interrupt()
        assert has_interrupts((yield from waiter.await_()))
        yield from run_task(scheduler)
        assert resumed == []
        assert latch.is_open() is False

    await bounded(program())


async def test_await_is_interruptible_and_cleans_up_interrupted_waiters():
    """Latch.test.ts:192."""
    Latch = latch_api()
    scheduler = QueueingScheduler()
    resumed: list[str] = []

    def waiting(latch: LatchLike, label: str) -> Effect[None, Never, Never]:
        return latch.await_().flat_map(lambda _: sync(lambda: resumed.append(label)))

    @gen
    def program():
        latch = yield from Latch.make(False)
        interrupted: Fiber[None, Never] = yield from fork(
            waiting(latch, "interrupted"), start_immediately=True
        )
        assert interrupted.exit is None
        yield from interrupted.interrupt()
        assert has_interrupts((yield from interrupted.await_()))
        yield from on(scheduler, latch.release())
        assert len(scheduler.tasks) == 0
        live: Fiber[None, Never] = yield from fork(waiting(latch, "live"), start_immediately=True)
        assert live.exit is None
        yield from on(scheduler, latch.release())
        assert len(scheduler.tasks) == 1
        assert live.exit is None
        yield from run_task(scheduler)
        yield from live.join()
        assert resumed == ["live"]
        assert latch.is_open() is False

    await bounded(program())


# == source-derived ============================================================================
# Each assertion is read off the pinned source named in its docstring; none has an upstream test.


async def test_make_defaults_closed_and_open_close_report_transitions():
    """internal/effect.ts:5982-5986, :6002-6006, :6014 (`open ?? false`); Latch.ts:212, :233
    ("true when this call changed the latch"), release :252-253 ("false if already open")."""
    Latch = latch_api()

    @gen
    def program():
        latch = yield from Latch.make()
        assert latch.is_open() is False
        assert (yield from latch.close()) is False
        assert (yield from latch.release()) is True
        assert (yield from latch.open()) is True
        assert (yield from latch.open()) is False
        assert (yield from latch.release()) is False
        assert (yield from latch.close()) is True
        assert latch.close_unsafe() is False
        assert latch.open_unsafe() is True
        assert latch.open_unsafe() is False
        assert Latch.make_unsafe(True).is_open() is True

    await bounded(program())


async def test_open_effect_wakes_waiters_on_the_dispatcher_not_inline():
    """internal/effect.ts:5976-5980: `open` goes through scheduleUnsafe, so waiters resume when
    the dispatcher runs the flush, while `openUnsafe` (:5982-5986) resumes them before
    returning. Awaiting while open completes without suspending (:5987-5990)."""
    Latch = latch_api()
    scheduler = QueueingScheduler()

    @gen
    def program():
        latch = yield from Latch.make(False)
        waiter: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        assert (yield from on(scheduler, latch.open())) is True
        assert latch.is_open() is True
        assert waiter.exit is None
        passer: Fiber[None, Never] = yield from fork(latch.await_(), start_immediately=True)
        assert passer.exit is not None
        yield from run_task(scheduler)
        assert waiter.exit is not None

        inline = Latch.make_unsafe(False)
        sleeper: Fiber[None, Never] = yield from fork(inline.await_(), start_immediately=True)
        yield from sync(inline.open_unsafe)
        assert sleeper.exit is not None

    await bounded(program())


async def test_when_open_gates_the_effect_and_preserves_its_result():
    """internal/effect.ts:6008 (`flatMap(this.await, () => self)`); Latch.ts:349-353."""
    Latch = latch_api()
    ran: list[int] = []

    @gen
    def program():
        latch = yield from Latch.make(False)
        gated: Fiber[int, Never] = yield from fork(
            latch.when_open(sync(lambda: ran.append(1) or 7)), start_immediately=True
        )
        assert ran == []
        yield from latch.open()
        assert (yield from gated.join()) == 7
        assert ran == [1]

    await bounded(program())


# == generated: operation sequences against a model of internal/effect.ts:5928-6011 ============
#
# Invariant: after any sequence of await / open / open_unsafe / release / close / close_unsafe /
# interrupt / turn, each waiter's state (pending, resumed, interrupted) and `is_open()` equal
# the model's. The model is written from the pinned source's two lists (`waiters`, `scheduled`)
# and never reads peffect code. `turn` is one `yield_now` of the driving fiber, which runs the
# flush task queued ahead of it on the same default dispatcher. No ordering among resumed
# waiters is asserted: the source promises none.

Op = (
    tuple[Literal["await", "open", "open_unsafe", "release", "close", "close_unsafe", "turn"],]
    | (tuple[Literal["interrupt"], int])
)


@dataclass
class LatchModel:
    is_open: bool
    waiters: list[int]
    scheduled: list[int] | None
    state: dict[int, str]

    def schedule(self) -> None:  # scheduleUnsafe :5937-5951
        if self.waiters:
            self.scheduled = [*(self.scheduled or []), *self.waiters]
            self.waiters = []

    def flush_scheduled(self) -> None:  # flushScheduled :5952-5959
        scheduled, self.scheduled = self.scheduled or [], None
        for w in scheduled:
            self.state[w] = "resumed"

    def apply(self, op: Op) -> None:
        match op:
            case ("await",):
                w = len(self.state)
                self.state[w] = "resumed" if self.is_open else "pending"
                if not self.is_open:
                    self.waiters.append(w)
            case ("open",):
                if not self.is_open:
                    self.is_open = True
                    self.schedule()
            case ("release",):
                if not self.is_open:
                    self.schedule()
            case ("open_unsafe",):  # flushWaiters :5960-5970
                if not self.is_open:
                    self.is_open = True
                    waiters, self.waiters = self.waiters, []
                    self.flush_scheduled()
                    for w in waiters:
                        self.state[w] = "resumed"
            case ("close",) | ("close_unsafe",):
                self.is_open = False
            case ("turn",):
                self.flush_scheduled()
            case ("interrupt", w):  # cancel :5991-6001
                if self.state.get(w) == "pending":
                    self.state[w] = "interrupted"
                    self.waiters = [x for x in self.waiters if x != w]
                    if self.scheduled is not None:
                        self.scheduled = [x for x in self.scheduled if x != w]
            case _:
                raise AssertionError(op)


ops = st.lists(
    st.one_of(
        st.sampled_from(
            ["await", "open", "open_unsafe", "release", "close", "close_unsafe", "turn"]
        ).map(lambda name: (name,)),
        st.tuples(st.just("interrupt"), st.integers(0, 7)),
    ),
    max_size=24,
)


def observed(fiber: Fiber[None, Never]) -> str:
    ex = fiber.exit
    return "pending" if ex is None else "interrupted" if has_interrupts(ex) else "resumed"


@settings(max_examples=150, deadline=None)
@given(ops, st.booleans())
async def test_latch_matches_its_source_model_over_operation_sequences(
    sequence: list[Op], start_open: bool
):
    Latch = latch_api()
    model = LatchModel(start_open, [], None, {})

    @gen
    def program():
        latch = Latch.make_unsafe(start_open)
        fibers: list[Fiber[None, Never]] = []
        for op in sequence:
            match op:
                case ("await",):
                    fibers.append((yield from fork(latch.await_(), start_immediately=True)))
                case ("open",):
                    yield from latch.open()
                case ("release",):
                    yield from latch.release()
                case ("close",):
                    yield from latch.close()
                case ("open_unsafe",):
                    yield from sync(latch.open_unsafe)
                case ("close_unsafe",):
                    yield from sync(latch.close_unsafe)
                case ("turn",):
                    yield from yield_now_
                case ("interrupt", w):
                    if w < len(fibers):
                        yield from fibers[w].interrupt()
                case _:
                    raise AssertionError(op)
            model.apply(op)
            assert latch.is_open() is model.is_open, op
            assert [observed(f) for f in fibers] == [model.state[i] for i in range(len(fibers))]
        # Pending waiters are supervised children: the run interrupts them when this body ends.

    await bounded(program(), 1)
