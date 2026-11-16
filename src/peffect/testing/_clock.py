"""TestClock: a Clock whose time moves only when the test moves it.

    provide_layer(TestClock.layer())(program)    # Clock and Scheduler for the program
    yield from fork(sleep("1 hour").flat_map(...))
    yield from TestClock.adjust("1 hour")        # wakes the sleeper, waits for it to settle

Sleeps register a deadline and park; ``adjust`` and ``set_time`` wake every deadline they pass,
in time order, moving the clock to each deadline first. After each wake the clock waits until
the dispatcher tasks the wake scheduled have run (the layer's tracking scheduler counts them),
or ``warning_delay`` of live time, then prints a busy warning and goes on. A sleep on a clock
nobody advances prints a warning after ``warning_delay``, through the live Console and live
Clock; ``adjust``, ``set_time`` and closing the clock's Scope stop it.

Effect v4: src/testing/TestClock.ts. Adaptations: ``copy.copy`` of a clock shares its state
(upstream's own-arrow methods survive a spread); ``repr`` stands in for ``toJSON``; a Clock that
is a TestClock (or a copy) is its own way back to the live clock, where upstream keeps a WeakMap
of the clocks ``make`` built; ``test_clock_with`` dies with a TypeError on another Clock, where
upstream casts.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Never, Protocol, final

from ..clock import Clock, ClockService, sleep
from ..console import Console, ConsoleService
from ..core import (
    Effect,
    async_,
    die,
    gen,
    succeed,
    suspend,
    sync,
    update_context,
    yield_now,
)
from ..duration import Duration, Input, from_input_unsafe, to_millis, to_nanos_unsafe
from ..fiber import Fiber, SchedulerDispatcher, SchedulerService, fork
from ..latch import Latch
from ..layer import Layer
from ..logger import log_warning
from ..resources import acquire_use_release, add_finalizer
from ..scheduler import Scheduler
from ..scope import Scope
from ..semaphore import Semaphore
from ..timeout import timeout_or_else

WARNING_MESSAGE = (
    "A test is using time, but is not advancing the test clock, which may result in the test "
    "hanging. Use TestClock.adjust to manually advance the time."
)
BUSY_WARNING_MESSAGE = (
    "A fiber woken by the test clock is still running without suspending, so the test clock "
    "stopped waiting for it. Sleeps it starts later may be scheduled relative to a later time."
)

type _Void = Effect[None, Never, Never]
type _Warning = Literal["start", "done"] | Fiber[None, Never]
"""TestClock.ts WarningState: Start, Pending (the warning fiber), Done."""


class TestClockService(ClockService, Protocol):
    """A Clock the test drives (TestClock.ts `TestClock`)."""

    def adjust(self, duration: Input) -> _Void: ...
    def set_time(self, timestamp: float) -> _Void: ...
    def with_live[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R]: ...


@final
@dataclass(slots=True, eq=False)  # identity: release removes this entry, not an equal one
class _Sleep:
    timestamp: float
    latch: Latch = field(default_factory=Latch.make_unsafe)


@final
@dataclass(slots=True, eq=False)
class _WakeTracker:
    """Dispatcher tasks a wake scheduled that have not finished yet."""

    pending: int = 0
    on_idle: Callable[[], None] | None = None


def _millis_to_nanos(millis: float) -> int:
    """millisToNanos (TestClock.ts:234-238): whole millis, plus the fraction's nanos floored."""
    whole = math.floor(millis)
    return whole * 1_000_000 + math.floor((millis - whole) * 1_000_000)


def _js_round(x: float) -> int:
    """Math.round: half up, so 2.5 gives 3 (Python's round gives 2)."""
    floor = math.floor(x)
    return floor + 1 if x - floor >= 0.5 else floor


@final
class _State:
    """One clock's state, shared by the clock and every copy of it."""

    __slots__ = (
        "current_tracker",
        "live_clock",
        "live_console",
        "mono",
        "now",
        "run_semaphore",
        "sleeps",
        "wall",
        "warning",
        "warning_delay",
        "warning_semaphore",
    )

    def __init__(
        self, warning_delay: Input, live_clock: ClockService, live_console: ConsoleService
    ) -> None:
        self.warning_delay = warning_delay
        self.live_clock = live_clock
        self.live_console = live_console
        self.sleeps: list[_Sleep] = []  # descending timestamp: pop() is the earliest
        self.warning_semaphore = Semaphore.make_unsafe(1)
        self.run_semaphore = Semaphore.make_unsafe(1)
        self.now: float = 0
        self.wall = 0
        self.mono = 0
        self.warning: _Warning = "start"
        self.current_tracker: _WakeTracker | None = None

    def set_warning(self, warning: _Warning) -> None:
        self.warning = warning

    def run_tracked(self, tracker: _WakeTracker, f: Callable[[], object]) -> None:
        previous = self.current_tracker
        self.current_tracker = tracker
        try:
            f()
        finally:
            self.current_tracker = previous

    def advance_to(self, timestamp: float) -> None:
        delta = timestamp - self.now
        if delta > 0 and math.isfinite(delta):
            self.mono += _js_round(delta * 1_000_000)
        if math.isfinite(timestamp):
            self.wall = _millis_to_nanos(timestamp)
        self.now = timestamp


@final
class _TestClock:
    """A TestClock. ``copy.copy`` gives another handle on the same clock."""

    __slots__ = ("_s",)

    def __init__(self, state: _State) -> None:
        self._s = state

    @property
    def live_clock(self) -> ClockService:
        """The clock this one was made over (upstream's liveClocks WeakMap entry)."""
        return self._s.live_clock

    def __repr__(self) -> str:
        return f"TestClock(current_time_millis={self._s.now!r})"

    def current_time_millis_unsafe(self) -> float:
        return self._s.now

    def current_time_nanos_unsafe(self) -> int:
        return self._s.wall

    def monotonic_time_nanos_unsafe(self) -> int:
        return self._s.mono

    @property
    def current_time_millis(self) -> Effect[float, Never, Never]:
        return sync(self.current_time_millis_unsafe)

    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]:
        return sync(self.current_time_nanos_unsafe)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]:
        return sync(self.monotonic_time_nanos_unsafe)

    def with_live[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R]:
        """Run ``eff`` on the clock that was in place when this one was made."""
        live = self._s.live_clock
        return update_context(eff, lambda old: {**old, Clock.key: live})

    def sleep(self, duration: Duration) -> _Void:
        s = self._s

        def go() -> _Void:
            end = s.now + to_millis(duration)
            if end <= s.now:
                return succeed(None)

            def insert() -> _Sleep:
                # before entries with an equal timestamp, so they wake in sleep order
                entry, low, high = _Sleep(end), 0, len(s.sleeps)
                while low < high:
                    mid = (low + high) // 2
                    if s.sleeps[mid].timestamp > end:
                        low = mid + 1
                    else:
                        high = mid
                s.sleeps.insert(low, entry)
                return entry

            def remove(entry: _Sleep) -> None:
                if entry in s.sleeps:  # gone already if run popped it
                    s.sleeps.remove(entry)

            return acquire_use_release(
                sync(insert),
                lambda entry: self._warning_start().flat_map(lambda _: entry.latch.await_()),
                lambda entry, _exit: sync(lambda: remove(entry)),
            )

        return suspend(go)

    def adjust(self, duration: Input) -> _Void:
        decoded = from_input_unsafe(duration)
        millis = to_millis(decoded)
        nanos = to_nanos_unsafe(decoded) if math.isfinite(millis) else None
        return self.warning_done().flat_map(lambda _: self._run(lambda now: now + millis, nanos))

    def set_time(self, timestamp: float) -> _Void:
        return self.warning_done().flat_map(lambda _: self._run(lambda _now: timestamp, None))

    def _on_live_console(self, eff: _Void) -> _Void:
        live = self._s.live_console
        return update_context(eff, lambda old: {**old, Console.key: live})

    def _warning_start(self) -> _Void:
        """warningStart: the first sleep forks the warning, a child of the sleeping fiber,
        interruptible whatever that fiber's mask. It sleeps and logs on the live services."""
        s = self._s

        def go() -> _Void:
            if s.warning != "start":
                return succeed(None)
            warn = self._on_live_console(log_warning(WARNING_MESSAGE))
            delayed = self.with_live(sleep(s.warning_delay).flat_map(lambda _: warn))
            return fork(delayed).flat_map(lambda fiber: sync(lambda: s.set_warning(fiber)))

        return s.warning_semaphore.with_permits(1)(suspend(go))

    def warning_done(self) -> _Void:
        """warningDone: stop a pending warning; no warning starts after this."""
        s = self._s

        def go() -> _Void:
            state = s.warning
            if isinstance(state, Fiber):
                return state.interrupt().flat_map(lambda _: sync(lambda: s.set_warning("done")))
            s.warning = "done"
            return succeed(None)

        return s.warning_semaphore.with_permits(1)(suspend(go))

    def _wake(self, latch: Latch) -> _Void:
        s = self._s

        def go() -> _Void:
            tracker = _WakeTracker()
            s.run_tracked(tracker, latch.open_unsafe)
            if tracker.pending == 0:
                return yield_now

            def register(resume: Callable[[_Void], None]) -> _Void | None:
                if tracker.pending == 0:
                    resume(succeed(None))
                    return None
                tracker.on_idle = lambda: resume(succeed(None))

                def cancel() -> None:
                    tracker.on_idle = None

                return sync(cancel)

            def busy() -> _Void:
                return self._on_live_console(log_warning(BUSY_WARNING_MESSAGE))

            settled = timeout_or_else(async_(register), duration=s.warning_delay, or_else=busy)
            return self.with_live(settled).flat_map(lambda _: yield_now)

        return suspend(go)

    def _run(self, step: Callable[[float], float], adjustment: int | None) -> _Void:
        return self._s.run_semaphore.with_permits(1)(self._run_body(step, adjustment))

    @gen
    def _run_body(
        self, step: Callable[[float], float], adjustment: int | None
    ) -> Generator[Effect[Any, Never, Never], Any]:
        s = self._s
        barrier = yield from fork(yield_now)
        yield from barrier.await_()
        initial_wall, initial_mono = s.wall, s.mono
        end = step(s.now)
        while s.sleeps:
            if s.sleeps[-1].timestamp > end:
                break
            entry = s.sleeps.pop()
            s.advance_to(entry.timestamp)
            yield from self._wake(entry.latch)
        s.advance_to(end)
        if adjustment is not None and math.isfinite(end):
            s.wall = initial_wall + adjustment
            if adjustment > 0:
                s.mono = initial_mono + adjustment


@final
class _TrackingScheduler:
    """The parent scheduler, except that a task scheduled during a wake is counted in that
    wake's tracker, and runs with it as the current tracker, so the tasks it schedules count
    too (TestClock.ts TrackingScheduler). Not a MixedScheduler, so fibers ask ``should_yield``
    on every step; it answers as the parent does."""

    __slots__ = ("_parent", "_state")

    def __init__(self, state: _State, parent: SchedulerService) -> None:
        self._state = state
        self._parent = parent

    @property
    def execution_mode(self) -> Literal["sync", "async"]:
        return self._parent.execution_mode

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        return self._parent.should_yield(fiber)

    def make_dispatcher(self) -> SchedulerDispatcher:
        return _TrackingDispatcher(self._state, self._parent.make_dispatcher())


@final
class _TrackingDispatcher:
    __slots__ = ("_dispatcher", "_state")

    def __init__(self, state: _State, dispatcher: SchedulerDispatcher) -> None:
        self._state = state
        self._dispatcher = dispatcher

    def schedule_task(self, task: Callable[[], None], priority: int) -> None:
        state = self._state
        tracker = state.current_tracker
        if tracker is None:
            self._dispatcher.schedule_task(task, priority)
            return

        def tracked() -> None:
            try:
                state.run_tracked(tracker, task)
            finally:
                tracker.pending -= 1
                if tracker.pending == 0 and tracker.on_idle is not None:
                    tracker.on_idle()

        tracker.pending += 1
        self._dispatcher.schedule_task(tracked, priority)

    def flush(self) -> None:
        self._dispatcher.flush()


def _make_with_scheduler(
    warning_delay: Input,
) -> Effect[tuple[_TestClock, _TrackingScheduler], Never, Scope]:
    """makeWithScheduler (TestClock.ts): the clock and its scheduler, built over the services in
    place now; closing the Scope stops a pending warning."""

    @gen
    def go() -> Generator[Effect[Any, Never, Any], Any, tuple[_TestClock, _TrackingScheduler]]:
        current = yield from Clock()
        live = current.live_clock if isinstance(current, _TestClock) else current
        state = _State(warning_delay, live, (yield from Console()))
        clock = _TestClock(state)
        yield from add_finalizer(lambda _exit: clock.warning_done())
        return clock, _TrackingScheduler(state, (yield from Scheduler()))

    return go()


def _test_clock_with[A, E, R](f: Callable[[TestClockService], Effect[A, E, R]]) -> Effect[A, E, R]:
    def check(clock: ClockService) -> Effect[A, E, R]:
        if isinstance(clock, _TestClock):
            return f(clock)
        return die(TypeError(f"the fiber's Clock is not a TestClock: {clock!r}"))

    return Clock().use().flat_map(check)


@final
class _TestClockModule:
    """The TestClock module's exports (TestClock.ts). An instance, not a class, so a test runner
    never takes it for a test class."""

    __slots__ = ()

    WARNING_MESSAGE: ClassVar[str] = WARNING_MESSAGE
    BUSY_WARNING_MESSAGE: ClassVar[str] = BUSY_WARNING_MESSAGE

    @staticmethod
    def make(*, warning_delay: Input = "1 second") -> Effect[TestClockService, Never, Scope]:
        """A new TestClock over the current Clock and Console; not installed anywhere."""
        return _make_with_scheduler(warning_delay).map(lambda pair: pair[0])

    @staticmethod
    def layer(*, warning_delay: Input = "1 second") -> Layer[Clock | Scheduler]:
        """Clock = a new TestClock, Scheduler = its tracking scheduler, per build."""
        return Layer.effect_context(
            _make_with_scheduler(warning_delay).map(
                lambda pair: Clock.context(pair[0]) | Scheduler.context(pair[1])
            )
        )

    @staticmethod
    def test_clock_with[A, E, R](
        f: Callable[[TestClockService], Effect[A, E, R]],
    ) -> Effect[A, E, R]:
        """Run ``f`` with the fiber's Clock, which must be a TestClock."""
        return _test_clock_with(f)

    @staticmethod
    def adjust(duration: Input) -> _Void:
        """Advance the fiber's TestClock by ``duration``."""
        return _test_clock_with(lambda c: c.adjust(duration))

    @staticmethod
    def set_time(timestamp: float) -> _Void:
        """Move the fiber's TestClock to ``timestamp`` milliseconds."""
        return _test_clock_with(lambda c: c.set_time(timestamp))

    @staticmethod
    def with_live[A, E, R](eff: Effect[A, E, R]) -> Effect[A, E, R]:
        """Run ``eff`` on the live clock behind the fiber's TestClock."""
        return _test_clock_with(lambda c: c.with_live(eff))


TestClock = _TestClockModule()

__all__ = ["BUSY_WARNING_MESSAGE", "WARNING_MESSAGE", "TestClock", "TestClockService"]
