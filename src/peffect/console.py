"""Console: printing as a service, so a test can capture it.

    yield from Console.log("hello", 1)               # through the fiber's Console
    provide(Console.Console.context(recorder))(p)    # replace it

`Console` is a Reference: using it never adds to R. The default prints with `print`, log/info/debug
to `sys.stdout` and warn/error to `sys.stderr`, each stream read on every call (a platform
adaptation of Node's console, not a parity claim about its formatting). Five methods only, the
ones TestClock and the default logger need. Effect v4: internal/effect.ts:6584-6587 (ConsoleRef),
Console.ts.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Never, Protocol, final

from .context import Reference
from .core import Effect, sync


class ConsoleService(Protocol):
    """What a Console provides: the five methods TestClock and the default logger use."""

    def log(self, *args: object) -> None: ...
    def error(self, *args: object) -> None: ...
    def warn(self, *args: object) -> None: ...
    def info(self, *args: object) -> None: ...
    def debug(self, *args: object) -> None: ...


@final
class _LiveConsole:
    __slots__ = ()

    def log(self, *args: object) -> None:
        print(*args, file=sys.stdout)

    def error(self, *args: object) -> None:
        print(*args, file=sys.stderr)

    def warn(self, *args: object) -> None:
        print(*args, file=sys.stderr)

    def info(self, *args: object) -> None:
        print(*args, file=sys.stdout)

    def debug(self, *args: object) -> None:
        print(*args, file=sys.stdout)


LIVE_CONSOLE: ConsoleService = _LiveConsole()


class Console(Reference[ConsoleService], key="effect/Console"):  # internal/effect.ts:6585
    """The fiber's Console. ``yield from Console()`` gives the service; the default is live."""

    @classmethod
    def default(cls) -> ConsoleService:
        return LIVE_CONSOLE


def console_with[A, E, R](f: Callable[[ConsoleService], Effect[A, E, R]]) -> Effect[A, E, R]:
    """Run ``f`` with the fiber's Console (Console.ts consoleWith)."""
    return Console().use().flat_map(f)


def log(*args: object) -> Effect[None, Never, Never]:
    return console_with(lambda c: sync(lambda: c.log(*args)))


def error(*args: object) -> Effect[None, Never, Never]:
    return console_with(lambda c: sync(lambda: c.error(*args)))


def warn(*args: object) -> Effect[None, Never, Never]:
    return console_with(lambda c: sync(lambda: c.warn(*args)))


def info(*args: object) -> Effect[None, Never, Never]:
    return console_with(lambda c: sync(lambda: c.info(*args)))


def debug(*args: object) -> Effect[None, Never, Never]:
    return console_with(lambda c: sync(lambda: c.debug(*args)))


__all__ = [
    "LIVE_CONSOLE",
    "Console",
    "ConsoleService",
    "console_with",
    "debug",
    "error",
    "info",
    "log",
    "warn",
]
