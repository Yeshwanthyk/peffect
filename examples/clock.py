"""Time through the fiber's Clock, using only the public API.

uv run python examples/clock.py
"""

import asyncio
import math
import time
from typing import Never

from peffect import (
    Clock,
    Effect,
    current_time_millis,
    fork,
    gen,
    monotonic_time_nanos,
    provide,
    run,
    sleep,
    sync,
)
from peffect.duration import Duration, to_millis


class FakeClock:
    """A Clock whose time moves only when something sleeps: no real waiting."""

    def __init__(self) -> None:
        self.now_ms = 1_000

    def current_time_millis_unsafe(self) -> int:
        return self.now_ms

    def current_time_nanos_unsafe(self) -> int:
        return self.now_ms * 1_000_000

    def monotonic_time_nanos_unsafe(self) -> int:
        return self.now_ms * 1_000_000

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
        def advance() -> None:
            self.now_ms += round(to_millis(duration))

        return sync(advance)


@gen
def wait_an_hour():
    start = yield from current_time_millis
    yield from sleep("1 hour")
    end = yield from current_time_millis
    return end - start


@gen
def live():
    lines: list[str] = []
    wall = yield from current_time_millis
    assert abs(wall - time.time() * 1000) < 1000  # the live clock is the wall clock
    lines.append("live wall clock matches time.time()")

    before = yield from monotonic_time_nanos
    yield from sleep(50)  # milliseconds: a real timer on the asyncio loop
    slept_ms = ((yield from monotonic_time_nanos) - before) // 1_000_000
    assert slept_ms >= 49, slept_ms
    lines.append("sleep(50) waited at least 50ms")

    parked = yield from fork(sleep(math.inf))  # never wakes: no timer at all
    yield from parked.interrupt()
    lines.append("sleep(inf) parked until interrupted")
    return lines


@gen
def program():
    lines = yield from live()
    fake = FakeClock()
    waited = yield from provide(Clock.context(fake))(wait_an_hour())
    assert waited == 3_600_000 and fake.now_ms == 3_601_000
    lines.append(f"fake clock: slept an hour in no time ({waited} ms)")
    return lines


async def main() -> None:
    # the fake hour must take no real time: a sleep that bypassed the fake Clock would wait an
    # hour, so give up after 2s (TimeoutError, exit 1)
    lines = await asyncio.wait_for(run(program()), timeout=2)
    for line in lines:
        print(line)
    print("clock ok")


if __name__ == "__main__":
    asyncio.run(main())
