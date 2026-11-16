"""A Ref whose updates may run effects, serialized by a one-permit semaphore.

Pinned Effect v4 src/SynchronizedRef.ts. Every update holds `semaphore.with_permit`: the pure
ones run the Ref operation on `backing` under it, and each effectful one reads the value inside
`suspend` once it holds the permit, runs the user effect, and writes only in the continuation of
that effect's success. A failure, defect, or interruption before that continuation writes
nothing; the permit is released however the update ends (Semaphore.ts:358-372). `get` and
`get_unsafe` read without the permit.

The semaphore is not reentrant: an update whose function runs an update on the same ref waits
for the permit its own caller holds, and parks until interrupted.

Upstream's data-first/data-last duals only select the receiver and are not separate API; the
Pipeable/Inspectable protocol and toJSON are not ported. Option is `Some[A] | None`, and
`Some(None)` is a present value (`Option.isNone` tests the tag).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never, final

from .core import Effect, succeed, suspend, sync
from .option import Option
from .ref import Ref
from .semaphore import Semaphore


@final
class SynchronizedRef[A]:
    """`backing` and `semaphore` are public, as in upstream's interface. Invariant in A."""

    __slots__ = ("backing", "semaphore")

    def __init__(self, value: A) -> None:
        self.semaphore: Semaphore = Semaphore.make_unsafe(1)
        self.backing: Ref[A] = Ref[A].make_unsafe(value)

    @classmethod
    def make_unsafe(cls, value: A) -> SynchronizedRef[A]:
        """SynchronizedRef.ts makeUnsafe."""
        return cls(value)

    @classmethod
    def make(cls, value: A) -> Effect[SynchronizedRef[A], Never, Never]:
        """A fresh ref and semaphore each time this effect runs (SynchronizedRef.ts make)."""
        return sync(lambda: cls(value))

    def get_unsafe(self) -> A:
        return self.backing.current

    def get(self) -> Effect[A, Never, Never]:
        """Not synchronized: reads without the permit (SynchronizedRef.ts get)."""
        return sync(self.get_unsafe)

    # -- pure updates: withPermit(Ref.op(backing, ...)) --------------------------------------------

    def set(self, value: A) -> Effect[None, Never, Never]:
        return self.semaphore.with_permit(self.backing.set(value))

    def get_and_set(self, value: A) -> Effect[A, Never, Never]:
        return self.semaphore.with_permit(self.backing.get_and_set(value))

    def set_and_get(self, value: A) -> Effect[A, Never, Never]:
        return self.semaphore.with_permit(self.backing.set_and_get(value))

    def get_and_update(self, f: Callable[[A], A]) -> Effect[A, Never, Never]:
        return self.semaphore.with_permit(self.backing.get_and_update(f))

    def get_and_update_some(self, pf: Callable[[A], Option[A]]) -> Effect[A, Never, Never]:
        return self.semaphore.with_permit(self.backing.get_and_update_some(pf))

    def modify[B](self, f: Callable[[A], tuple[B, A]]) -> Effect[B, Never, Never]:
        return self.semaphore.with_permit(self.backing.modify(f))

    def modify_some[B](self, pf: Callable[[A], tuple[B, Option[A]]]) -> Effect[B, Never, Never]:
        return self.semaphore.with_permit(self.backing.modify_some(pf))

    def update(self, f: Callable[[A], A]) -> Effect[None, Never, Never]:
        return self.semaphore.with_permit(self.backing.update(f))

    def update_and_get(self, f: Callable[[A], A]) -> Effect[A, Never, Never]:
        return self.semaphore.with_permit(self.backing.update_and_get(f))

    def update_some(self, f: Callable[[A], Option[A]]) -> Effect[None, Never, Never]:
        return self.semaphore.with_permit(self.backing.update_some(f))

    def update_some_and_get(self, pf: Callable[[A], Option[A]]) -> Effect[A, Never, Never]:
        return self.semaphore.with_permit(self.backing.update_some_and_get(pf))

    # -- effectful updates: withPermit(suspend(read, run f, write on its success)) ---------------

    def _locked[T, E, R](self, body: Callable[[A], Effect[T, E, R]]) -> Effect[T, E, R]:
        """`semaphore.withPermit(Effect.suspend(() => body(getUnsafe(self))))`."""
        return self.semaphore.with_permit(suspend(lambda: body(self.get_unsafe())))

    def _write(self, value: A) -> None:
        self.backing.current = value

    def get_and_update_effect[E, R](self, f: Callable[[A], Effect[A, E, R]]) -> Effect[A, E, R]:
        def body(value: A) -> Effect[A, E, R]:
            def write(new_value: A) -> A:
                self._write(new_value)
                return value

            return f(value).map(write)

        return self._locked(body)

    def get_and_update_some_effect[E, R](
        self, pf: Callable[[A], Effect[Option[A], E, R]]
    ) -> Effect[A, E, R]:
        def body(value: A) -> Effect[A, E, R]:
            def write(option: Option[A]) -> Effect[A, Never, Never]:
                if option is None:
                    return succeed(value)
                self._write(option.value)
                return succeed(value)

            return pf(value).flat_map(write)

        return self._locked(body)

    def modify_effect[B, E, R](
        self, f: Callable[[A], Effect[tuple[B, A], E, R]]
    ) -> Effect[B, E, R]:
        def body(value: A) -> Effect[B, E, R]:
            def write(result: tuple[B, A]) -> B:
                b, a = result
                self._write(a)
                return b

            return f(value).map(write)

        return self._locked(body)

    def modify_some_effect[B, E, R](
        self, pf: Callable[[A], Effect[tuple[B, Option[A]], E, R]]
    ) -> Effect[B, E, R]:
        def body(value: A) -> Effect[B, E, R]:
            def write(result: tuple[B, Option[A]]) -> Effect[B, Never, Never]:
                b, maybe_a = result
                if maybe_a is None:
                    return succeed(b)
                self._write(maybe_a.value)
                return succeed(b)

            return pf(value).flat_map(write)

        return self._locked(body)

    def update_effect[E, R](self, f: Callable[[A], Effect[A, E, R]]) -> Effect[None, E, R]:
        return self._locked(lambda value: f(value).map(self._write))

    def update_and_get_effect[E, R](self, f: Callable[[A], Effect[A, E, R]]) -> Effect[A, E, R]:
        def write(new_value: A) -> A:
            self._write(new_value)
            return new_value

        return self._locked(lambda value: f(value).map(write))

    def update_some_effect[E, R](
        self, pf: Callable[[A], Effect[Option[A], E, R]]
    ) -> Effect[None, E, R]:
        def write(option: Option[A]) -> None:
            if option is not None:
                self._write(option.value)

        return self._locked(lambda value: pf(value).map(write))

    def update_some_and_get_effect[E, R](
        self, pf: Callable[[A], Effect[Option[A], E, R]]
    ) -> Effect[A, E, R]:
        def body(value: A) -> Effect[A, E, R]:
            def write(option: Option[A]) -> Effect[A, Never, Never]:
                if option is None:
                    return succeed(value)
                self._write(option.value)
                return succeed(option.value)

            return pf(value).flat_map(write)

        return self._locked(body)
