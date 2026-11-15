"""Clock-routed `sleep`: contracts read from Effect v4's source, not from an upstream test.

Upstream is pinned at vendor/effect-v4 commit 289a53f (Effect v4.1.4). No upstream test outside
TestClock pins these (batch D ports those), so each case cites the source lines it checks in
packages/effect/src/internal/effect.ts:

    6476  sleep(d) = clockWith(clock => clock.sleep(d))     the fiber's Clock does the sleeping
    6419  sleepMillis(millis <= 0) = yieldNow                no timer, but still a yield
    6420  sleepMillis(!isFinite(millis)) = never             no timer, parked until interrupted
    6421  callback(setTimeout(...); return clearTimeout)     a timer; interrupting clears it

Adaptations:
- `sleep` takes seconds as a float (D5). Upstream's `MAX_TIMER_MILLIS` chunking has no Python
  counterpart: asyncio's `call_later` has no 2**31 ms limit.
- The timer is observed by spying on the running loop's `call_later`. The harness itself
  registers no timer: it advances the loop only with `asyncio.sleep(0)`, which CPython runs
  without `call_later`. So every recorded timer is one `sleep` registered.
- `Clock` doesn't exist yet: the one case that needs it looks it up inside the test, so it alone
  fails with AttributeError.
"""

import asyncio
import math
from typing import Any

import pytest

import peffect
from peffect import (
    Effect,
    Exit,
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


class TimerSpy:
    """Records each `call_later` on the running loop, keeping the real handle."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.real = loop.call_later
        self.timers: list[tuple[float, asyncio.TimerHandle]] = []

    def __call__(self, delay: float, callback: Any, *args: Any, **kwargs: Any) -> Any:
        handle = self.real(delay, callback, *args, **kwargs)
        self.timers.append((delay, handle))
        return handle


@pytest.fixture
async def timers(monkeypatch: pytest.MonkeyPatch) -> TimerSpy:
    loop = asyncio.get_running_loop()
    spy = TimerSpy(loop)
    monkeypatch.setattr(loop, "call_later", spy)
    return spy


async def ticks(n: int = 5) -> None:
    for _ in range(n):
        await asyncio.sleep(0)  # no call_later: the loop just turns


def is_interrupted(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Failure) and ex.cause.is_interrupted_only


@pytest.mark.parametrize("seconds", [0, -1])
async def test_a_non_positive_sleep_is_a_yield_without_a_timer(timers: TimerSpy, seconds: float):
    """effect.ts:6419 `if (millis <= 0) return yieldNow`.

    The yield is observed as order: a forked fiber is queued before the sleep, so it runs between
    "before" and "after" only if the sleep gives the loop up. `succeed(None)` in place of the
    sleep would log before, after, child."""
    log: list[str] = []

    @gen
    def program():
        child = yield from fork(sync(lambda: log.append("child")))
        yield from sync(lambda: log.append("before"))
        yield from sleep(seconds)
        yield from sync(lambda: log.append("after"))
        yield from child.join()

    assert isinstance(await run_exit(program()), Success)
    assert log == ["before", "child", "after"]
    assert timers.timers == []


async def test_an_infinite_sleep_parks_without_a_timer_until_interrupted(timers: TimerSpy):
    """effect.ts:6420 `else if (!Number.isFinite(millis)) return never`."""
    fiber: Fiber[None, Any] = await run(fork_daemon(sleep(math.inf)))
    await ticks()

    assert fiber.exit is None  # still parked
    assert timers.timers == []

    await run(fiber.interrupt())
    assert is_interrupted(fiber.exit)
    assert timers.timers == []


async def test_interrupting_a_finite_sleep_clears_its_timer(timers: TimerSpy):
    """effect.ts:6421-6427: `setTimeout(...)`, and the returned `clearTimeout` runs on interrupt."""
    fiber: Fiber[None, Any] = await run(fork_daemon(sleep(5)))
    await ticks()

    assert fiber.exit is None
    assert [delay for delay, _ in timers.timers] == [5]
    (_, handle) = timers.timers[0]
    assert not handle.cancelled()  # registered and live before the interrupt

    await run(fiber.interrupt())
    assert is_interrupted(fiber.exit)
    assert handle.cancelled()


class RecordingClock:
    """The Clock service contract (Clock.ts:53-150; ClockImpl, effect.ts:6401-6429), with a
    `sleep` that records its argument and completes at once instead of setting a timer."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def current_time_millis_unsafe(self) -> int:
        return 0

    def current_time_nanos_unsafe(self) -> int:
        return 0

    def monotonic_time_nanos_unsafe(self) -> int:
        return 0

    @property
    def current_time_millis(self) -> Effect[int, Any, Any]:
        return succeed(0)

    @property
    def current_time_nanos(self) -> Effect[int, Any, Any]:
        return succeed(0)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Any, Any]:
        return succeed(0)

    def sleep(self, seconds: float) -> Effect[None, Any, Any]:
        return sync(lambda: self.slept.append(seconds))


async def test_sleep_uses_the_provided_clock(timers: TimerSpy):
    """effect.ts:6476 `sleep = clockWith((clock) => clock.sleep(duration))`: the Clock in the
    fiber's context does the sleeping, so a provided one replaces the live timer."""
    missing = "Clock"  # not in peffect yet: AttributeError until it is
    clock_ref: Any = getattr(peffect, missing)
    recording = RecordingClock()

    ex = await run_exit(provide(clock_ref.context(recording))(sleep(5)))

    assert isinstance(ex, Success)
    assert recording.slept == [5]
    assert timers.timers == []
