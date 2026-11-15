"""Clock: time and sleeping as a service, so a test can swap in its own.

    now = yield from current_time_millis           # read through the fiber's Clock
    yield from sleep(1.5)                          # the fiber's Clock does the sleeping
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
    def sleep(self, seconds: float) -> Effect[None, Never, Never]: ...


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

    def sleep(self, seconds: float) -> Effect[None, Never, Never]:
        # Duration normalizes NaN to zero (Duration.ts:465), and sleepMillis yields for <= 0 and
        # never wakes for an infinite duration (internal/effect.ts:6419-6420)
        if math.isnan(seconds) or seconds <= 0:
            return yield_now
        if math.isinf(seconds):
            return never

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


def sleep(seconds: float) -> Effect[None, Never, Never]:
    """Suspend for ``seconds`` on the fiber's Clock. ``<= 0`` (and NaN) is a yield; ``inf`` never
    wakes. Interrupting it cancels the timer (internal/effect.ts:6476-6477)."""
    normalized = 0.0 if math.isnan(seconds) else seconds
    return clock_with(lambda c: c.sleep(normalized))
