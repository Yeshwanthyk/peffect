"""Timeouts: race an effect against a sleep on the fiber's Clock.

    yield from timeout_option(fetch(), "2 seconds")          # Some(value), or None on timeout
    yield from timeout_or_else(fetch(), duration=10, or_else=lambda: succeed(cached))
    yield from timeout(fetch(), "1 minute")                  # fails with TimeoutError

Built from `race_first` and `sleep`: the source and the sleep race as two racers, so a timed-out
source is interrupted, and its cleanup finishes, before the timeout continues. Data-first only.
Effect v4: internal/effect.ts:3929-4007 (timeoutOrElse, timeout, timeoutOption), :6499
(TimeoutError).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import final

from .clock import sleep
from .core import Effect, fail, succeed
from .duration import Input, format, from_input_unsafe
from .fiber import race_first
from .option import Option, Some


@final
class TimeoutError(Exception):
    """What `timeout` fails with: ``Operation timed out after '<duration>'``. Catch it with
    ``catch_tag(TimeoutError, ...)``. Not the builtin `TimeoutError` (an OSError)."""


def timeout_option[A, E, R](self: Effect[A, E, R], duration: Input) -> Effect[Option[A], E, R]:
    """``Some(value)`` if ``self`` ends first, ``None`` if ``duration`` passes first. A failure
    of ``self`` that comes first is kept. ``duration`` is read when the effect runs, so an invalid
    one is a defect then (timeoutOption, :3988-4007)."""
    some: Effect[Option[A], E, R] = self.map(Some)
    none: Effect[Option[A], E, R] = sleep(duration).as_(None)
    return race_first(some, none)


def timeout_or_else[A, E, R](
    self: Effect[A, E, R], *, duration: Input, or_else: Callable[[], Effect[A, E, R]]
) -> Effect[A, E, R]:
    """``self``'s result if it ends within ``duration``; otherwise ``or_else()``, called only
    then, on the calling fiber (timeoutOrElse, :3929-3951).

    One type variable per channel: pyright joins the source's and the fallback's types."""

    def choose(option: Option[A]) -> Effect[A, E, R]:
        return or_else() if option is None else succeed(option.value)

    return timeout_option(self, duration).flat_map(choose)


def timeout[A, E, R](self: Effect[A, E, R], duration: Input) -> Effect[A, E | TimeoutError, R]:
    """``self``'s result, or a ``TimeoutError`` failure if ``duration`` passes first. Unlike the
    other two, ``duration`` is decoded now: an invalid one raises ValueError here (timeout,
    :3954-3985)."""
    decoded = from_input_unsafe(duration)

    def or_else() -> Effect[A, E | TimeoutError, R]:  # a fresh error per timeout, as upstream
        return fail(TimeoutError(f"Operation timed out after '{format(decoded)}'"))

    widened: Effect[A, E | TimeoutError, R] = self
    return timeout_or_else(widened, duration=decoded, or_else=or_else)


__all__ = ["TimeoutError", "timeout", "timeout_option", "timeout_or_else"]
