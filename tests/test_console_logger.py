"""Console, the header-only Logger subset, and TestConsole, ported test-first (vendor/effect-v4
289a53f). TestClock's warnings are their first peffect caller.

Upstream:
- Console: internal/effect.ts:6584-6587 (`ConsoleRef`, key "effect/Console", default
  `globalThis.console`).
- Log levels: internal/effect.ts:6594-6619 (`logLevelToOrder`, `isLogLevelGreaterThan`);
  internal/references.ts:56-58 (`MinimumLogLevel`, key "effect/References/MinimumLogLevel",
  default "Info").
- Loggers: internal/effect.ts:6626-6634 (`CurrentLoggers`, a `Set`; `LogToStderr`), :6725-6760
  (`logWithLevel`), :6810-6813 (`defaultDateFormat`), :6984-7003 (`defaultLogger`).
- TestConsole: src/testing/TestConsole.ts (entries of method and parameters; `logLines` and
  `errorLines` flatten the "log" and "error" entries).

Every expected header, routing, filtering and dedup result below was measured by running the
pinned vendor under bun (scratch probe `plog.ts`), never computed with the code under test.

Scope and adaptations:
- Header-only subset: the default logger writes `[HH:MM:SS.mmm] LEVEL (#fiber):` and the
  messages. Log spans, annotations, cause rendering, the tracer logger and the pretty/JSON loggers
  are not ported, so `CurrentLoggers` defaults to the default logger alone.
- The five Console methods TestClock needs (log, error, warn, info, debug), not the full
  Console interface.
- The live console prints with `print`: arguments joined by a space. log/info/debug go to
  `sys.stdout` and warn/error to `sys.stderr`, read on every call. This is a platform adaptation
  of Node's console, not a parity claim about its formatting.
- A custom logger gets `fiber_id` (an int) where upstream passes the fiber: outside the kernel
  peffect code reads a fiber's id only through the public `fiber_id` effect (INVARIANTS B3).
- `CurrentLoggers` holds a sequence; logging visits each logger once, in first-registration
  order, as upstream's `Set` does.
- `TestConsole.test_console_with` checks that the console is a TestConsole and dies with a
  TypeError otherwise; upstream casts (TestConsole.ts `testConsoleWith`).
"""

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Never

import pytest

import peffect.console as Console
import peffect.logger as Logger
from peffect import (
    Clock,
    Effect,
    Failure,
    fiber_id,
    gen,
    provide,
    provide_layer,
    run,
    run_exit,
    succeed,
    yield_now,
)
from peffect.duration import Duration
from peffect.testing import TestConsole


def with_test_console[A](body: Effect[A, Any, Any]) -> Effect[A, Any, Never]:
    return provide_layer(TestConsole.layer)(body)


class Recorder:
    """A console that records each call: what a provided Console replaces."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def log(self, *args: object) -> None:
        self.calls.append(("log", args))

    def error(self, *args: object) -> None:
        self.calls.append(("error", args))

    def warn(self, *args: object) -> None:
        self.calls.append(("warn", args))

    def info(self, *args: object) -> None:
        self.calls.append(("info", args))

    def debug(self, *args: object) -> None:
        self.calls.append(("debug", args))


class FixedClock:
    """A Clock stopped at ``millis``: the time the logger's header shows."""

    def __init__(self, millis: float) -> None:
        self.millis = millis

    def current_time_millis_unsafe(self) -> float:
        return self.millis

    def current_time_nanos_unsafe(self) -> int:
        return 0

    def monotonic_time_nanos_unsafe(self) -> int:
        return 0

    @property
    def current_time_millis(self) -> Effect[float, Never, Never]:
        return succeed(self.millis)

    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    def sleep(self, duration: Duration) -> Effect[None, Never, Never]:
        return yield_now


@contextmanager
def local_timezone(name: str) -> Iterator[None]:
    """Run with ``TZ=name``; the header is local time, as upstream's `Date#getHours`."""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = saved
        time.tzset()


# -- Reference keys ---------------------------------------------------------------------------


def test_reference_keys_match_upstream() -> None:
    """The key is a Reference's identity: these are upstream's strings (internal/effect.ts:6585,
    :6628, :6633; internal/references.ts:57)."""
    assert Console.Console.key == "effect/Console"
    assert Logger.CurrentLoggers.key == "effect/Logger/CurrentLoggers"
    assert Logger.LogToStderr.key == "effect/Logger/LogToStderr"
    assert Logger.MinimumLogLevel.key == "effect/References/MinimumLogLevel"
    assert Logger.MinimumLogLevel.default() == "Info"
    assert Logger.LogToStderr.default() is False


# -- Console ----------------------------------------------------------------------------------


async def test_live_console_routes_log_info_debug_to_stdout_and_warn_error_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Platform adaptation of the default `globalThis.console` (internal/effect.ts:6586)."""

    @gen
    def body():
        yield from Console.log("log", 1)
        yield from Console.info("info")
        yield from Console.debug("debug")
        yield from Console.warn("warn", 2)
        yield from Console.error("error")

    await run(body())
    captured = capsys.readouterr()
    assert captured.out == "log 1\ninfo\ndebug\n"
    assert captured.err == "warn 2\nerror\n"


async def test_a_provided_console_replaces_the_live_one(capsys: pytest.CaptureFixture[str]) -> None:
    recorder = Recorder()

    @gen
    def body():
        yield from Console.log("a", 1)
        yield from Console.error("b")
        yield from Console.warn("c")
        yield from Console.info("d")
        yield from Console.debug("e")
        service = yield from Console.Console()
        assert service is recorder

    await run(provide(Console.Console.context(recorder))(body()))
    assert recorder.calls == [
        ("log", ("a", 1)),
        ("error", ("b",)),
        ("warn", ("c",)),
        ("info", ("d",)),
        ("debug", ("e",)),
    ]
    assert capsys.readouterr() == ("", "")


# -- TestConsole ------------------------------------------------------------------------------


async def test_test_console_captures_log_and_error_lines_flattened(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """TestConsole.ts: every call is an entry; `logLines` and `errorLines` flatten the
    parameters of the "log" and "error" entries, in call order. Nothing reaches the terminal."""

    @gen
    def body():
        yield from Console.log("a", 1)
        yield from Console.error("e1")
        yield from Console.warn("w")
        yield from Console.info("i")
        yield from Console.debug("d")
        yield from Console.log("b")
        yield from Console.error("e2", 2)
        assert list((yield from TestConsole.log_lines)) == ["a", 1, "b"]
        assert list((yield from TestConsole.error_lines)) == ["e1", "e2", 2]

    await run(with_test_console(body()))
    assert capsys.readouterr() == ("", "")


async def test_each_test_console_layer_build_starts_empty() -> None:
    """`layer` is `Layer.effect(Console, make)`: every build makes new entries."""
    for _ in range(2):

        @gen
        def body():
            assert list((yield from TestConsole.log_lines)) == []
            yield from Console.log("once")
            assert list((yield from TestConsole.log_lines)) == ["once"]

        await run(with_test_console(body()))


async def test_test_console_with_on_the_live_console_is_a_type_error_defect() -> None:
    """Adaptation: upstream casts; here a console that isn't a TestConsole is a defect."""
    exit_ = await run_exit(TestConsole.test_console_with(succeed))
    assert isinstance(exit_, Failure)
    assert isinstance(exit_.cause.squash(), TypeError)


# -- default logger header --------------------------------------------------------------------

# (TZ, clock millis, header time) -- measured: Effect.logWarning("m", 1) under bun with a Clock
# fixed at the millis. Fractions truncate toward zero (Date's TimeClip); non-finite is NaN.
HEADER_CASES: list[tuple[str, float, str]] = [
    ("UTC", 1_700_000_000_123, "22:13:20.123"),
    ("UTC", 0, "00:00:00.000"),
    ("UTC", 1.5, "00:00:00.001"),
    ("UTC", 1.9999, "00:00:00.001"),
    ("UTC", -1, "23:59:59.999"),
    ("UTC", -1.5, "23:59:59.999"),
    ("UTC", float("inf"), "NaN:NaN:NaN.NaN"),
    ("UTC", float("nan"), "NaN:NaN:NaN.NaN"),
    ("Asia/Kolkata", 1_700_000_000_123, "03:43:20.123"),
    ("Asia/Kolkata", 0, "05:30:00.000"),
    ("Asia/Kolkata", 1.5, "05:30:00.001"),
    ("Asia/Kolkata", -1, "05:29:59.999"),
]


@pytest.mark.parametrize(("tz", "millis", "clock_text"), HEADER_CASES, ids=lambda x: str(x))
async def test_default_logger_header_matches_upstream(
    tz: str, millis: float, clock_text: str
) -> None:
    """defaultLogger (internal/effect.ts:7002): the time is the fiber Clock's, in local time;
    the messages follow the header as separate console arguments."""

    @gen
    def body():
        fid = yield from fiber_id
        yield from Logger.log_warning("m", 1)
        lines = list((yield from TestConsole.log_lines))
        assert lines == [f"[{clock_text}] WARN (#{fid}):", "m", 1]

    with local_timezone(tz):
        await run(with_test_console(provide(Clock.context(FixedClock(millis)))(body())))


async def test_log_to_stderr_routes_the_default_logger_to_console_error() -> None:
    """defaultLogger: `LogToStderr ? console.error : console.log` (measured: "error")."""

    @gen
    def body():
        fid = yield from fiber_id
        yield from provide(Logger.LogToStderr.context(True))(Logger.log_warning("e"))
        yield from Logger.log_with_level("Info")("i")
        assert list((yield from TestConsole.error_lines)) == [f"[00:00:00.000] WARN (#{fid}):", "e"]
        assert list((yield from TestConsole.log_lines)) == [f"[00:00:00.000] INFO (#{fid}):", "i"]

    with local_timezone("UTC"):
        await run(with_test_console(provide(Clock.context(FixedClock(0)))(body())))


# -- minimum level ----------------------------------------------------------------------------

LEVELS = ["Trace", "Debug", "Info", "Warn", "Error", "Fatal"]

# minimum -> levels that log; measured under bun with each MinimumLogLevel provided
MINIMUM_CASES: list[tuple[str, list[str]]] = [
    ("All", LEVELS),
    ("Trace", LEVELS),
    ("Debug", ["Debug", "Info", "Warn", "Error", "Fatal"]),
    ("Info", ["Info", "Warn", "Error", "Fatal"]),
    ("Warn", ["Warn", "Error", "Fatal"]),
    ("Error", ["Error", "Fatal"]),
    ("Fatal", ["Fatal"]),
    ("None", []),
]


@pytest.mark.parametrize(("minimum", "logged"), MINIMUM_CASES, ids=lambda x: str(x))
async def test_minimum_log_level_filters(minimum: Logger.LogLevel, logged: list[str]) -> None:
    """logWithLevel skips when `isLogLevelGreaterThan(minimumLogLevel, level)`."""
    seen: list[str] = []
    recorder = Logger.make(lambda options: seen.append(options.log_level))

    @gen
    def body():
        for level in LEVELS:
            yield from Logger.log_with_level(level)("z")

    env = Logger.CurrentLoggers.context((recorder,)) | Logger.MinimumLogLevel.context(minimum)
    await run(provide(env)(body()))
    assert seen == logged


async def test_default_minimum_is_info() -> None:
    seen: list[str] = []
    recorder = Logger.make(lambda options: seen.append(options.log_level))

    @gen
    def body():
        yield from Logger.log_with_level("Debug")("hidden")
        yield from Logger.log_with_level("Info")("shown")

    await run(provide(Logger.CurrentLoggers.context((recorder,)))(body()))
    assert seen == ["Info"]


# -- CurrentLoggers ---------------------------------------------------------------------------


async def test_custom_loggers_get_level_message_and_fiber_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Providing CurrentLoggers replaces the default logger: nothing is printed."""
    seen: list[tuple[str, tuple[object, ...], int]] = []
    recorder = Logger.make(
        lambda options: seen.append((options.log_level, tuple(options.message), options.fiber_id))
    )

    @gen
    def body():
        fid = yield from fiber_id
        yield from Logger.log_warning("x", 2)
        return fid

    fid = await run(provide(Logger.CurrentLoggers.context((recorder,)))(body()))
    assert seen == [("Warn", ("x", 2), fid)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("order", "expected"),
    [("aba", ["a:x", "b:x"]), ("bab", ["b:x", "a:x"])],
)
async def test_a_logger_registered_twice_logs_once_in_first_registration_order(
    order: str, expected: list[str]
) -> None:
    """Upstream holds a `Set` (internal/effect.ts:6626-6631). Measured: `new Set([a, b, a])`
    logs a then b, `new Set([b, a, b])` b then a, each once."""
    lines: list[str] = []
    loggers = {
        name: Logger.make(lambda options, name=name: lines.append(f"{name}:{options.message[0]}"))
        for name in "ab"
    }
    registered = tuple(loggers[name] for name in order)
    await run(provide(Logger.CurrentLoggers.context(registered))(Logger.log_warning("x")))
    assert lines == expected


async def test_no_loggers_logs_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    await run(provide(Logger.CurrentLoggers.context(()))(Logger.log_warning("none")))
    assert capsys.readouterr() == ("", "")


async def test_the_default_loggers_are_the_default_logger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Header-only subset: upstream's default set also holds the tracer logger, not ported."""
    assert tuple(Logger.CurrentLoggers.default()) == (Logger.default_logger,)
    await run(Logger.log_warning("live"))
    out = capsys.readouterr().out
    assert " WARN (#" in out and out.endswith(": live\n")
