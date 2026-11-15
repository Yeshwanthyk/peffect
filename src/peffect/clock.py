"""Clock: time and sleeping as a service, so a test can swap in its own.

    now = yield from current_time_millis           # read through the fiber's Clock
    yield from sleep("1.5 seconds")                # the fiber's Clock does the sleeping
    provide(Clock.context(my_clock))(program)      # replace it

`Clock` is a Reference, so using it never adds to R; its default is the live clock.
Effect v4: Clock.ts, internal/effect.ts:6395-6486 (ClockRef, ClockImpl, clockWith, sleep).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Never, Protocol, final

from .context import Reference
from .core import Effect, async_, never, succeed, sync, yield_now
from .duration import Duration, Input, from_input_unsafe, to_millis

NANOS_PER_MILLI = 1_000_000
REANCHOR_THRESHOLD_NANOS = 1_000_000_000  # wall time re-anchors when it drifts more than 1s


class ClockService(Protocol):
    """What a Clock provides (Clock.ts:53-150). Times are int: milliseconds, or nanoseconds."""

    def current_time_millis_unsafe(self) -> int: ...
    def current_time_nanos_unsafe(self) -> int: ...
    def monotonic_time_nanos_unsafe(self) -> int: ...
    @property
    def current_time_millis(self) -> Effect[int, Never, Never]: ...
    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]: ...
    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]: ...
    def sleep(self, duration: Duration) -> Effect[None, Never, Never]: ...


# -- the live clock (ClockImpl, internal/effect.ts:6401-6470) -------------------------------------

_origin: int | None = None  # wall minus monotonic, one per process, as upstream's module closure


def _wall_millis() -> int:
    return time.time_ns() // NANOS_PER_MILLI  # Date.now(): whole milliseconds


def _wall_nanos() -> int:
    """Monotonic time shifted onto the wall clock, re-anchored when the two drift apart by more
    than 1s (a suspended machine, a clock set back). All int: no float step."""
    global _origin
    monotonic = time.monotonic_ns()
    wall = _wall_millis() * NANOS_PER_MILLI
    if _origin is None or abs(wall - (_origin + monotonic)) > REANCHOR_THRESHOLD_NANOS:
        _origin = wall - monotonic
    return _origin + monotonic


@final
class _LiveClock:
    __slots__ = ()

    def current_time_millis_unsafe(self) -> int:
        return _wall_millis()

    def current_time_nanos_unsafe(self) -> int:
        return _wall_nanos()

    def monotonic_time_nanos_unsafe(self) -> int:
        return time.monotonic_ns()

    @property
    def current_time_millis(self) -> Effect[int, Never, Never]:
        return sync(self.current_time_millis_unsafe)

    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]:
        return sync(self.current_time_nanos_unsafe)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]:
        return sync(self.monotonic_time_nanos_unsafe)

    def sleep(self, duration: Duration) -> Effect[None, Never, Never]:
        # sleepMillis(toMillis(duration)) (internal/effect.ts:6415-6421): <= 0 (-Infinity too) is
        # a yield, Infinity never wakes; Duration has already turned NaN into zero
        millis = to_millis(duration)
        if millis <= 0:
            return yield_now
        if not math.isfinite(millis):
            return never
        seconds = millis / 1000  # asyncio's call_later takes seconds: the only unit conversion

        def register(
            resume: Callable[[Effect[None, Never, Never]], None],
        ) -> Effect[None, Never, Never]:
            handle = asyncio.get_running_loop().call_later(seconds, resume, succeed(None))
            return sync(handle.cancel)  # run on interrupt: clearTimeout

        return async_(register)


class Clock(Reference[ClockService], key="effect/Clock"):  # internal/effect.ts:6395
    """The fiber's Clock. ``yield from Clock()`` gives the service; the default is the live one."""

    @classmethod
    def default(cls) -> ClockService:
        return _LiveClock()


def clock_with[A, E, R](f: Callable[[ClockService], Effect[A, E, R]]) -> Effect[A, E, R]:
    """Run ``f`` with the fiber's Clock (internal/effect.ts:6472-6473)."""
    return Clock().use().flat_map(f)


current_time_millis: Effect[int, Never, Never] = clock_with(lambda c: c.current_time_millis)
current_time_nanos: Effect[int, Never, Never] = clock_with(lambda c: c.current_time_nanos)
monotonic_time_nanos: Effect[int, Never, Never] = clock_with(lambda c: c.monotonic_time_nanos)


def sleep(duration: Input) -> Effect[None, Never, Never]:
    """Suspend for ``duration`` on the fiber's Clock: a Duration, a string such as ``"5 seconds"``,
    or a number of milliseconds. ``<= 0`` is a yield; infinity never wakes. Interrupting it cancels
    the timer. The input is converted when the effect runs, so invalid input is a defect then
    (internal/effect.ts:6476-6477)."""
    return clock_with(lambda c: c.sleep(from_input_unsafe(duration)))
