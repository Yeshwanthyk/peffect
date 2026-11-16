"""Logger: leveled log lines through the fiber's loggers. A header-only subset.

    yield from log_warning("disk low", pct)                  # [12:00:00.000] WARN (#3): disk low 9
    provide(MinimumLogLevel.context("Debug"))(program)       # let Debug lines through
    provide(CurrentLoggers.context((make(fn),)))(program)    # replace the loggers

The default logger writes ``[HH:MM:SS.mmm] LEVEL (#fiber):`` then the messages, as separate
arguments, to the fiber's Console (``console.error`` when ``LogToStderr``). The time is the fiber
Clock's, in local time, as upstream's ``Date``. Not ported: spans, annotations, cause rendering,
the tracer logger, the pretty and JSON loggers. Effect v4: internal/effect.ts:6594-6634,
:6725-6760, :6810-6813, :6984-7003; internal/references.ts:56-58.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, Literal, Never, final

from .clock import Clock
from .console import Console, ConsoleService
from .context import Reference
from .core import ContextMap, Effect, WithFiber, fiber_id, gen, succeed

type Severity = Literal["Fatal", "Error", "Warn", "Info", "Debug", "Trace"]
type LogLevel = Severity | Literal["All", "None"]

_ORDER: dict[LogLevel, float] = {  # logLevelToOrder, :6594-6613 (Number.MIN/MAX_SAFE_INTEGER)
    "All": -(2**53 - 1),
    "Fatal": 50_000,
    "Error": 40_000,
    "Warn": 30_000,
    "Info": 20_000,
    "Debug": 10_000,
    "Trace": 0,
    "None": 2**53 - 1,
}


def is_greater_than(self: LogLevel, that: LogLevel) -> bool:
    """isLogLevelGreaterThan (:6615-6619)."""
    return _ORDER[self] > _ORDER[that]


class MinimumLogLevel(Reference[LogLevel], key="effect/References/MinimumLogLevel"):
    """Levels below this are skipped. Default "Info" (internal/references.ts:56-58)."""

    @classmethod
    def default(cls) -> LogLevel:
        return "Info"


class LogToStderr(Reference[bool], key="effect/Logger/LogToStderr"):
    """When true the default logger writes with ``console.error``. Default False (:6633)."""

    @classmethod
    def default(cls) -> bool:
        return False


@final
@dataclass(frozen=True, slots=True)
class LogOptions:
    """What a logger gets. ``fiber_id`` stands in for upstream's fiber (INVARIANTS B3);
    ``context`` is that fiber's services, for loggers that read a Reference such as Console."""

    log_level: Severity
    message: tuple[object, ...]
    fiber_id: int
    time_millis: float
    context: ContextMap


@final
class Logger:
    """A logger: ``make(fn)`` wraps a function of LogOptions. Identity is what dedups it."""

    __slots__ = ("_log",)

    def __init__(self, log: Callable[[LogOptions], object]) -> None:
        self._log = log

    def log(self, options: LogOptions) -> None:
        self._log(options)


def make(log: Callable[[LogOptions], object]) -> Logger:
    """Logger.make: a logger that calls ``log`` for every line that passes the minimum."""
    return Logger(log)


def format_time(millis: float) -> str:
    """``HH:MM:SS.mmm`` in local time, as defaultDateFormat (:6810-6813) over ``new Date(millis)``:
    the time is truncated toward zero, and outside Date's range (non-finite, or more than 8.64e15
    from the epoch) every field is NaN."""
    if not math.isfinite(millis) or abs(millis) > 8.64e15:
        return "NaN:NaN:NaN.NaN"
    seconds, ms = divmod(int(millis), 1000)
    t = time.localtime(seconds)
    return f"{t.tm_hour:02}:{t.tm_min:02}:{t.tm_sec:02}.{ms:03}"


def _default_log(options: LogOptions) -> None:
    """defaultLogger (:6984-7003), header only."""
    context = options.context
    console: ConsoleService = context.get(Console.key, Console.default())
    to_stderr: bool = context.get(LogToStderr.key, LogToStderr.default())
    header = (
        f"[{format_time(options.time_millis)}] {options.log_level.upper()} (#{options.fiber_id}):"
    )
    (console.error if to_stderr else console.log)(header, *options.message)


default_logger: Logger = Logger(_default_log)


class CurrentLoggers(Reference[tuple[Logger, ...]], key="effect/Logger/CurrentLoggers"):
    """The loggers each line goes to. A logger listed twice logs once, at its first place, as
    upstream's ``Set`` (:6626-6631). Default: the default logger alone (no tracer logger)."""

    @classmethod
    def default(cls) -> tuple[Logger, ...]:
        return (default_logger,)


def log_with_level(level: Severity) -> Callable[..., Effect[None, Never, Never]]:
    """``log_with_level("Warn")("a", 1)``: log at ``level`` unless the minimum is above it, with
    the fiber Clock's time read once per line (logWithLevel, :6725-6760)."""

    @gen
    def log(*message: object) -> Generator[Effect[Any, Never, Never], Any]:
        if is_greater_than((yield from MinimumLogLevel()), level):
            return
        loggers = yield from CurrentLoggers()
        if not loggers:
            return
        fid = yield from fiber_id
        clock = yield from Clock()
        context = yield from WithFiber(lambda fiber: succeed(fiber.context))
        options = LogOptions(level, message, fid, clock.current_time_millis_unsafe(), context)
        for logger in dict.fromkeys(loggers):  # each once, first registration first, as a Set
            logger.log(options)

    return log


log_warning = log_with_level("Warn")
"""Effect.logWarning."""

__all__ = [
    "CurrentLoggers",
    "LogLevel",
    "LogOptions",
    "LogToStderr",
    "Logger",
    "MinimumLogLevel",
    "Severity",
    "default_logger",
    "format_time",
    "is_greater_than",
    "log_warning",
    "log_with_level",
    "make",
]
