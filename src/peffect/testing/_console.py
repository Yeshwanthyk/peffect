"""TestConsole: a Console that records every call instead of printing.

    provide_layer(TestConsole.layer)(program)
    lines = yield from TestConsole.log_lines        # every Console.log argument, flattened

Effect v4: src/testing/TestConsole.ts. ``test_console_with`` checks that the fiber's Console is a
TestConsole and dies with a TypeError otherwise, where upstream casts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, Literal, Never, Protocol, final

from ..console import Console, ConsoleService
from ..core import Effect, die, sync
from ..layer import Layer

type Method = Literal["log", "error", "warn", "info", "debug"]


class TestConsoleService(ConsoleService, Protocol):
    """A Console whose ``log`` and ``error`` arguments can be read back."""

    @property
    def log_lines(self) -> Effect[tuple[object, ...], Never, Never]: ...
    @property
    def error_lines(self) -> Effect[tuple[object, ...], Never, Never]: ...


@final
class _TestConsole:
    __slots__ = ("entries",)

    def __init__(self) -> None:
        self.entries: list[tuple[Method, tuple[object, ...]]] = []

    def log(self, *args: object) -> None:
        self.entries.append(("log", args))

    def error(self, *args: object) -> None:
        self.entries.append(("error", args))

    def warn(self, *args: object) -> None:
        self.entries.append(("warn", args))

    def info(self, *args: object) -> None:
        self.entries.append(("info", args))

    def debug(self, *args: object) -> None:
        self.entries.append(("debug", args))

    def _lines(self, method: Method) -> tuple[object, ...]:
        return tuple(p for m, params in self.entries if m == method for p in params)

    @property
    def log_lines(self) -> Effect[tuple[object, ...], Never, Never]:
        return sync(lambda: self._lines("log"))

    @property
    def error_lines(self) -> Effect[tuple[object, ...], Never, Never]:
        return sync(lambda: self._lines("error"))


def _test_console_with[A, E, R](
    f: Callable[[TestConsoleService], Effect[A, E, R]],
) -> Effect[A, E, R]:
    def check(console: ConsoleService) -> Effect[A, E, R]:
        if isinstance(console, _TestConsole):
            return f(console)
        return die(TypeError(f"the fiber's Console is not a TestConsole: {console!r}"))

    return Console().use().flat_map(check)


@final
class _TestConsoleModule:
    """The TestConsole module's exports (TestConsole.ts). An instance, not a class, so a test
    runner never takes it for a test class."""

    __slots__ = ()

    make: ClassVar[Effect[TestConsoleService, Never, Never]] = sync(_TestConsole)
    """A fresh recording console each time it runs."""
    layer: ClassVar[Layer[Console]] = Layer.effect(Console, make)
    """Console = a fresh TestConsole, per build."""
    log_lines: ClassVar[Effect[tuple[object, ...], Never, Never]] = _test_console_with(
        lambda c: c.log_lines
    )
    error_lines: ClassVar[Effect[tuple[object, ...], Never, Never]] = _test_console_with(
        lambda c: c.error_lines
    )

    @staticmethod
    def test_console_with[A, E, R](
        f: Callable[[TestConsoleService], Effect[A, E, R]],
    ) -> Effect[A, E, R]:
        """Run ``f`` with the fiber's Console, which must be a TestConsole."""
        return _test_console_with(f)


TestConsole = _TestConsoleModule()


__all__ = ["TestConsole", "TestConsoleService"]
