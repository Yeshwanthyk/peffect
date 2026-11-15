"""`sleep` takes a `Duration.Input` and the Clock sleeps on a `Duration`: Effect v4's contract,
ported before the seconds-float API is migrated. Upstream is pinned at vendor/effect-v4 commit
289a53f (Effect v4.1.4). No upstream test pins the Input contract directly (Clock.test.ts covers
only wall/monotonic time), so every case here is source-derived and cites the lines it checks:

    Effect.ts:4930      sleep: (duration: Duration.Input) => Effect<void>
    effect.ts:6476-6477 sleep = clockWith(clock => clock.sleep(Duration.fromInputUnsafe(duration)))
    Clock.ts:150        sleep(duration: Duration.Duration): Effect<void>
    effect.ts:6415-6417 ClockImpl.sleep(duration) = sleepMillis(Duration.toMillis(duration))
    effect.ts:6419-6421 millis <= 0: yieldNow; !isFinite(millis): never; else setTimeout(millis)

Upstream callers pass bare numbers as milliseconds (Effect.test.ts:1028 `Effect.sleep(20)`,
TestClock.test.ts:30 `Effect.sleep("1 minute")`).

Platform adaptations (same as tests/test_clock_sleep.py):
- The live timer is asyncio's `call_later`, which takes seconds: the millisecond count is divided
  by 1000 only there, at the final boundary. asyncio has no 2**31 ms limit, so upstream's
  `MAX_TIMER_MILLIS` chunking has no counterpart: one timer for the whole interval.
- Timers are observed by spying on the running loop's `call_later`; the harness turns the loop
  only with `asyncio.sleep(0)`, which registers no timer.
- `fromInputUnsafe` throws inside `clockWith`'s callback, so invalid input is a defect when the
  effect runs, not an exception when it is built.
"""

import asyncio
from typing import Any, Never

import pytest

import peffect.duration as D
from peffect import (
    Clock,
    Effect,
    Failure,
    Fiber,
    Success,
    fork,
    fork_daemon,
    gen,
    provide,
    run,
    run_exit,
    sleep,
    succeed,
    sync,
)
from peffect.duration import Duration

SAFE = 9_007_199_254_740_993  # 2**53 + 1: no float holds it


class TimerSpy:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.real = loop.call_later
        self.delays: list[float] = []

    def __call__(self, delay: float, callback: Any, *args: Any, **kwargs: Any) -> Any:
        self.delays.append(delay)
        return self.real(delay, callback, *args, **kwargs)


@pytest.fixture
async def timers(monkeypatch: pytest.MonkeyPatch) -> TimerSpy:
    loop = asyncio.get_running_loop()
    spy = TimerSpy(loop)
    monkeypatch.setattr(loop, "call_later", spy)
    return spy


async def ticks(n: int = 5) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


class RecordingClock:
    """The Clock service contract (Clock.ts:53-150), with a `sleep` that records the Duration it
    is handed and completes at once."""

    def __init__(self) -> None:
        self.slept: list[Duration] = []

    def current_time_millis_unsafe(self) -> int:
        return 0

    def current_time_nanos_unsafe(self) -> int:
        return 0

    def monotonic_time_nanos_unsafe(self) -> int:
        return 0

    @property
    def current_time_millis(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    def sleep(self, duration: Duration) -> Effect[None, Never, Never]:
        return sync(lambda: self.slept.append(duration))


async def slept_on_provided_clock(input: D.Input) -> Duration:
    recording = RecordingClock()
    ex = await run_exit(provide(Clock.context(recording))(sleep(input)))
    assert isinstance(ex, Success)
    (duration,) = recording.slept
    assert isinstance(duration, Duration)
    return duration


# --- normalization before dispatch: effect.ts:6477 fromInputUnsafe, then clock.sleep ---


@pytest.mark.parametrize(
    ("input", "expected"),
    [
        (5, D.millis(5)),  # a number is milliseconds, not seconds
        (1.5, D.millis(1.5)),  # fractional millis: Nanos 1_500_000
        ("1 minute", D.minutes(1)),
        ("10 millis", D.millis(10)),
        ("1.5 nanos", D.nanos(2)),
        ("Infinity", D.infinity),
        (float("nan"), D.zero),  # Duration.ts:465
        (-5, D.millis(-5)),  # kept negative: the Clock decides what a negative sleep means
    ],
)
async def test_a_provided_clock_receives_the_normalized_duration(
    input: D.Input, expected: Duration
):
    assert await slept_on_provided_clock(input) == expected  # Duration equality: exact nanos


async def test_a_duration_is_passed_through_with_exact_nanoseconds():
    # Duration.ts:299 `if (TypeId in input) return input`: the same object, no float step
    exact = D.nanos(SAFE)
    duration = await slept_on_provided_clock(exact)
    assert duration is exact
    assert D.to_nanos_unsafe(duration) == SAFE


@pytest.mark.parametrize("bad", ["abc", "1second", True])
async def test_invalid_input_is_a_defect_when_run_and_the_clock_is_not_called(bad: Any):
    recording = RecordingClock()
    program = provide(Clock.context(recording))(sleep(bad))  # building it does not throw
    ex = await run_exit(program)
    assert isinstance(ex, Failure)
    (defect,) = ex.cause.defects
    assert isinstance(defect, ValueError) and str(defect).startswith("Invalid Input")
    assert recording.slept == []


# --- the live clock: effect.ts:6415-6427 sleepMillis(Duration.toMillis(duration)) ---


@pytest.mark.parametrize(
    ("input", "delay_seconds"),
    [
        (1500, 1.5),
        ("250 millis", 0.25),
        ("2 seconds", 2.0),
        (D.nanos(1_500_000), 0.0015),
        (2**31, 2**31 / 1000),  # past MAX_TIMER_MILLIS: one asyncio timer, no chunking
    ],
)
async def test_a_positive_sleep_sets_one_timer_for_its_milliseconds(
    timers: TimerSpy, input: D.Input, delay_seconds: float
):
    fiber: Fiber[None, Never] = await run(fork_daemon(sleep(input)))
    await ticks()
    assert fiber.exit is None
    assert timers.delays == [delay_seconds]
    await run(fiber.interrupt())


async def test_a_one_nanosecond_sleep_is_a_timer_not_a_yield(timers: TimerSpy):
    # toMillis(nanos(1n)) is 1e-6 > 0, so sleepMillis sets a timer
    fiber: Fiber[None, Never] = await run(fork_daemon(sleep(D.nanos(1))))
    await ticks(1)
    assert timers.delays == [D.to_millis(D.nanos(1)) / 1000]
    await run(fiber.interrupt())


@pytest.mark.parametrize(
    "input", [0, -1, float("nan"), D.zero, D.nanos(-1), D.negative_infinity, "-Infinity"]
)
async def test_a_non_positive_sleep_is_a_yield_without_a_timer(timers: TimerSpy, input: D.Input):
    # effect.ts:6419 `millis <= 0`: toMillis(negativeInfinity) is -Infinity, so a yield too
    log: list[str] = []

    @gen
    def program():
        child = yield from fork(sync(lambda: log.append("child")))
        yield from sync(lambda: log.append("before"))
        yield from sleep(input)
        yield from sync(lambda: log.append("after"))
        yield from child.join()

    assert isinstance(await run_exit(program()), Success)
    assert log == ["before", "child", "after"]
    assert timers.delays == []


@pytest.mark.parametrize("input", [D.infinity, "Infinity", float("inf")])
async def test_an_infinite_sleep_parks_without_a_timer(timers: TimerSpy, input: D.Input):
    # effect.ts:6420 `!Number.isFinite(millis)`: never
    fiber: Fiber[None, Never] = await run(fork_daemon(sleep(input)))
    await ticks()
    assert fiber.exit is None
    assert timers.delays == []
    await run(fiber.interrupt())
    assert isinstance(fiber.exit, Failure) and fiber.exit.cause.is_interrupted_only
