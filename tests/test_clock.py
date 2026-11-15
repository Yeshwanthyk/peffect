"""Batch C: Effect v4's Clock test (Clock.test.ts), ported. Upstream is pinned at vendor/effect-v4
commit 289a53f (Effect v4.1.4). Mapping and status: docs/UPSTREAM_TESTS.md.

Adaptations:
- Upstream stubs the clock's two sources, `Date.now` (wall, milliseconds) and
  `process.hrtime.bigint` (monotonic, nanoseconds). Python's sources are `time.time_ns` and
  `time.monotonic_ns`, both int nanoseconds, so they are the stubs (pytest's monkeypatch
  replaces vitest's spyOn/mockRestore). The wall stub returns whole milliseconds, as Date.now does.
- `Clock` is looked up when the test runs (`upcoming`), so until it exists the test fails with
  AttributeError rather than stopping the file from importing.
- Upstream reads the clock with `yield* Clock.Clock` inside `it.live`; here `Clock().use()` runs
  with nothing provided, so the default (live) clock answers. All asserts use the clock's
  synchronous `*_unsafe` reads, as upstream's do.
"""

import time
from typing import Any

import pytest

import peffect
from peffect import run


def upcoming(name: str) -> Any:
    """A public name upstream has and peffect doesn't yet: AttributeError until it does."""
    return getattr(peffect, name)


async def test_keeps_wall_time_aligned_while_exposing_the_monotonic_source(
    monkeypatch: pytest.MonkeyPatch,
):
    """Clock.test.ts:5 "keeps wall time aligned while exposing the monotonic source"."""
    now = {"wall_millis": 1_000_000, "monotonic_nanos": 5_000_000_000}
    monkeypatch.setattr(time, "time_ns", lambda: now["wall_millis"] * 1_000_000)
    monkeypatch.setattr(time, "monotonic_ns", lambda: now["monotonic_nanos"])
    clock_tag = upcoming("Clock")
    clock = await run(clock_tag().use())
    nanos_per_milli = 1_000_000

    assert clock.current_time_nanos_unsafe() == now["wall_millis"] * nanos_per_milli
    assert clock.monotonic_time_nanos_unsafe() == now["monotonic_nanos"]

    now["wall_millis"] += 250
    now["monotonic_nanos"] += 250_000_000
    assert clock.current_time_nanos_unsafe() == now["wall_millis"] * nanos_per_milli

    # the wall clock jumps 5s while the monotonic one stands still (a suspended machine)
    now["wall_millis"] += 5_000
    before_suspend = clock.monotonic_time_nanos_unsafe()
    assert clock.current_time_nanos_unsafe() == now["wall_millis"] * nanos_per_milli
    assert clock.monotonic_time_nanos_unsafe() == before_suspend

    # the wall clock is set back 3s; monotonic time still only moves forward
    now["wall_millis"] -= 3_000
    now["monotonic_nanos"] += 100_000_000
    assert clock.current_time_nanos_unsafe() == now["wall_millis"] * nanos_per_milli
    assert clock.monotonic_time_nanos_unsafe() > before_suspend
