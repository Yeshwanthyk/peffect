"""A reusable gate: waiters park while it is closed and resume once it opens or is released.

Pinned Effect v4 Latch.ts and the Latch class in internal/effect.ts:5928-6017. `open()` and
`release()` resume waiters on the calling fiber's dispatcher; `open_unsafe()` resumes them
before it returns. Upstream's Effect-valued fields (`open`, `await`, ...) are methods here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never, final

from .core import Effect, async_, succeed, sync
from .fiber import SchedulerDispatcher, with_dispatcher

type _Resume = Callable[[Effect[None, Never, Never]], None]


@final
class Latch:
    """Waiters still parked when the latch opens sit in ``waiters``; once a wake-up is queued on
    a dispatcher they move to ``scheduled`` until it runs. Both are visible, like upstream."""

    __slots__ = ("_is_open", "scheduled", "waiters")

    def __init__(self, is_open: bool) -> None:
        self.waiters: list[_Resume] = []
        self.scheduled: list[_Resume] | None = None
        self._is_open = is_open

    @classmethod
    def make_unsafe(cls, open: bool = False) -> Latch:
        """A latch, closed unless ``open`` (internal/effect.ts:6014)."""
        return cls(open)

    @classmethod
    def make(cls, open: bool = False) -> Effect[Latch, Never, Never]:
        """A fresh latch each time this effect runs (:6017)."""
        return sync(lambda: cls(open))

    def _schedule(self, dispatcher: SchedulerDispatcher) -> Effect[bool, Never, Never]:
        """scheduleUnsafe, :5937-5951: one flush per batch; later wakes join the pending one."""
        if self.waiters:
            if self.scheduled is None:
                self.scheduled = self.waiters
                dispatcher.schedule_task(self._flush_scheduled, 0)
            else:
                self.scheduled.extend(self.waiters)
            self.waiters = []
        return succeed(True)

    def _flush_scheduled(self) -> None:
        waiters, self.scheduled = self.scheduled, None
        for resume in waiters or ():
            resume(succeed(None))

    def open(self) -> Effect[bool, Never, Never]:
        """Open and wake every waiter on the dispatcher; False if already open (:5972-5976)."""

        def go(dispatcher: SchedulerDispatcher) -> Effect[bool, Never, Never]:
            if self._is_open:
                return succeed(False)
            self._is_open = True
            return self._schedule(dispatcher)

        return with_dispatcher(go)

    def release(self) -> Effect[bool, Never, Never]:
        """Wake current waiters and stay closed; False if open (:5977)."""
        return with_dispatcher(lambda d: succeed(False) if self._is_open else self._schedule(d))

    def open_unsafe(self) -> bool:
        """Open and resume every waiter before returning (:5978-5983, :5960-5971). Both lists are
        swapped out first: a resumed waiter that closes the latch and waits again stays parked."""
        if self._is_open:
            return False
        self._is_open = True
        waiters, self.waiters = self.waiters, []
        self._flush_scheduled()
        for resume in waiters:
            resume(succeed(None))
        return True

    def await_(self) -> Effect[None, Never, Never]:
        """Wait until open; an interrupted waiter leaves either list (:5984-6000)."""

        def register(resume: _Resume) -> Effect[None, Never, Never] | None:
            if self._is_open:
                resume(succeed(None))
                return None
            self.waiters.append(resume)

            def cancel() -> None:
                if resume in self.waiters:
                    self.waiters.remove(resume)
                elif self.scheduled is not None and resume in self.scheduled:
                    self.scheduled.remove(resume)

            return sync(cancel)

        return async_(register)

    def close_unsafe(self) -> bool:
        """Close; False if already closed (:6001-6005)."""
        if not self._is_open:
            return False
        self._is_open = False
        return True

    def close(self) -> Effect[bool, Never, Never]:
        return sync(self.close_unsafe)

    def when_open[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R]:
        """Run ``eff`` once the latch is open (:6007)."""
        return self.await_().flat_map(lambda _: eff)

    def is_open(self) -> bool:
        return self._is_open
