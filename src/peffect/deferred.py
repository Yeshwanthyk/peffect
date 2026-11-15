"""One-time coordination with a stored completion Effect, not a memoized result.

Pinned Effect v4 Deferred.ts:151-193, :363-409, :647-695, :806-810, :881-894.
Only the constructors, await and completion operations covered by the Deferred unit are
ported. complete/completeWith, polling and the remaining convenience APIs are not ported.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never, final

from .cause import Cause
from .core import Effect, Exit, Failure, Success, async_, fiber_id, sync


@final
class Deferred[A, E = Never]:
    """An invariant cell completed once; each waiter runs the stored Effect itself.

    A and E are invariant because the same cell is both completed and awaited. Stored
    effects require no environment: an awaiter need not supply the completing fiber's services.
    Like upstream, effect/resumes are visible for low-level consumers and white-box tests.
    """

    __slots__ = ("effect", "resumes")

    def __init__(self) -> None:
        self.effect: Effect[A, E, Never] | None = None
        self.resumes: list[Callable[[Effect[A, E, Never]], None]] | None = None

    @classmethod
    def make_unsafe(cls) -> Deferred[A, E]:
        """Allocate an empty cell synchronously (Deferred.ts:151)."""
        return cls()

    @classmethod
    def make(cls) -> Effect[Deferred[A, E], Never, Never]:
        """Allocate a fresh empty cell each time this effect runs (:178)."""
        return sync(cls.make_unsafe)

    def await_(self) -> Effect[A, E, Never]:
        """Run the stored completion, or register until completed (:180-193)."""

        def register(
            resume: Callable[[Effect[A, E, Never]], None],
        ) -> Effect[None, Never, Never] | None:
            if self.effect is not None:
                resume(self.effect)
                return None
            if self.resumes is None:
                self.resumes = []
            self.resumes.append(resume)

            def unregister() -> None:
                resumes = self.resumes
                if resumes is None:
                    return  # completion already detached the callbacks
                try:
                    index = resumes.index(resume)
                except ValueError:
                    return
                del resumes[index]

            return sync(unregister)

        return async_(register)

    def done_unsafe(self, effect: Effect[A, E, Never]) -> bool:
        """Store once, detach all callbacks, then notify them synchronously (:881-894)."""
        if self.effect is not None:
            return False
        self.effect = effect
        if self.resumes is not None:
            resumes = self.resumes
            self.resumes = None
            for resume in resumes:
                resume(effect)
        return True

    def done(self, exit_: Exit[A, E]) -> Effect[bool, Never, Never]:
        """Attempt completion with an Exit (:363-366, completeWith :323-326)."""
        return sync(lambda: self.done_unsafe(exit_))

    def succeed(self, value: A) -> Effect[bool, Never, Never]:
        """Attempt successful completion (:806-810)."""
        return self.done(Success(value))

    def fail(self, error: E) -> Effect[bool, Never, Never]:
        """Attempt typed failure completion (:400-404)."""
        return self.done(Failure(Cause.fail(error)))

    def interrupt_with(self, fiber_id_: int) -> Effect[bool, Never, Never]:
        """Attempt interruption by the specified fiber (:683-695)."""
        return self.done(Failure(Cause.interrupt(fiber_id_)))

    def interrupt(self) -> Effect[bool, Never, Never]:
        """Attempt interruption by the fiber executing this effect (:647-648)."""
        return fiber_id.flat_map(self.interrupt_with)
