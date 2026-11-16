"""A mutable cell read and written by effects; each operation is one synchronous step.

Pinned Effect v4 src/Ref.ts. Every operation is `Effect.sync` of a body that reads the cell,
calls the user function, and assigns, so no other fiber can run between the read and the write
of one operation. A user function that raises is a defect of that sync step and assigns nothing
(each body computes before it assigns). Upstream's data-first/data-last duals only select the
receiver and are not separate API; the Pipeable/Inspectable protocol and toJSON are not ported.

Upstream stores a MutableRef in `self.ref` and reads `self.ref.current`; MutableRef is not
ported, so the cell is the `current` slot itself. Option is `Some[A] | None`: an update applies
when the function returns a `Some`, including `Some(None)` (Ref.ts tests `_tag === "Some"`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never, final

from .core import Effect, sync
from .option import Option


@final
class Ref[A]:
    """A cell of A. Invariant in A: it is both read and written (Ref.ts `Ref<in out A>`)."""

    __slots__ = ("current",)

    def __init__(self, value: A) -> None:
        self.current: A = value

    @classmethod
    def make_unsafe(cls, value: A) -> Ref[A]:
        """Allocate a cell synchronously (Ref.ts makeUnsafe)."""
        return cls(value)

    @classmethod
    def make(cls, value: A) -> Effect[Ref[A], Never, Never]:
        """A fresh cell each time this effect runs (Ref.ts make)."""
        return sync(lambda: cls(value))

    def get_unsafe(self) -> A:
        return self.current

    def get(self) -> Effect[A, Never, Never]:
        return sync(self.get_unsafe)

    def set(self, value: A) -> Effect[None, Never, Never]:
        def go() -> None:
            self.current = value

        return sync(go)

    def get_and_set(self, value: A) -> Effect[A, Never, Never]:
        def go() -> A:
            current = self.current
            self.current = value
            return current

        return sync(go)

    def set_and_get(self, value: A) -> Effect[A, Never, Never]:
        def go() -> A:
            self.current = value
            return value

        return sync(go)

    def get_and_update(self, f: Callable[[A], A]) -> Effect[A, Never, Never]:
        def go() -> A:
            current = self.current
            self.current = f(current)
            return current

        return sync(go)

    def get_and_update_some(self, pf: Callable[[A], Option[A]]) -> Effect[A, Never, Never]:
        def go() -> A:
            current = self.current
            option = pf(current)
            if option is not None:
                self.current = option.value
            return current

        return sync(go)

    def modify[B](self, f: Callable[[A], tuple[B, A]]) -> Effect[B, Never, Never]:
        def go() -> B:
            b, a = f(self.current)
            self.current = a
            return b

        return sync(go)

    def modify_some[B](self, pf: Callable[[A], tuple[B, Option[A]]]) -> Effect[B, Never, Never]:
        """Ref.ts modifySome: modify, keeping the value when the option is None."""

        def f(value: A) -> tuple[B, A]:
            b, option = pf(value)
            return b, (value if option is None else option.value)

        return self.modify(f)

    def update(self, f: Callable[[A], A]) -> Effect[None, Never, Never]:
        def go() -> None:
            self.current = f(self.current)

        return sync(go)

    def update_and_get(self, f: Callable[[A], A]) -> Effect[A, Never, Never]:
        def go() -> A:
            value = f(self.current)
            self.current = value
            return value

        return sync(go)

    def update_some(self, f: Callable[[A], Option[A]]) -> Effect[None, Never, Never]:
        def go() -> None:
            option = f(self.current)
            if option is not None:
                self.current = option.value

        return sync(go)

    def update_some_and_get(self, pf: Callable[[A], Option[A]]) -> Effect[A, Never, Never]:
        def go() -> A:
            option = pf(self.current)
            if option is not None:
                self.current = option.value
            return self.current

        return sync(go)
