"""A semaphore: a resizable count of permits that fibers take, wait for, and release.

Pinned Effect v4 src/Semaphore.ts:234-387. A request takes at once only when enough permits are
free and no queued request fits (:256); otherwise it queues. A release wakes, on the releasing
fiber's dispatcher, the queued requests the free permits cover; a woken request re-checks and
may queue again. A smaller later request can overtake a larger earlier one (:243-247): the queue
is not FIFO. Upstream's module-level duals only delegate to these methods and are not ported.

Leaving the queue and returning permits on exit happen as the exit frame pops, in one step with
queueing the follow-up wake on the running fiber's dispatcher, like upstream's synchronous onExit
callbacks (:263-268, :365-368, :380-383): core.on_exit_with_dispatcher. No yield can fall between
the state change and its wake.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never, Protocol, final

from .core import (
    Effect,
    async_,
    on_exit_with_dispatcher,
    succeed,
    suspend,
    sync,
    uninterruptible_mask,
)
from .fiber import SchedulerDispatcher, with_dispatcher
from .option import Option, Some
from .scope import Scope, scope

type _Resume = Callable[[Effect[None, Never, Never]], None]

_unit: Effect[None, Never, Never] = succeed(None)


class _Restore(Protocol):
    def __call__[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[A, E, R]: ...


def _unmasked[A, E, R](eff: Effect[A, E, R]) -> Effect[A, E, R]:
    return eff


class WithPermits(Protocol):
    """``sem.with_permits(n)``: run an effect while holding n permits."""

    def __call__[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[A, E, R]: ...


class WithPermitsIfAvailable(Protocol):
    """``sem.with_permits_if_available(n)``: Some(result), or None without running when short."""

    def __call__[A, E, R](self, eff: Effect[A, E, R], /) -> Effect[Option[A], E, R]: ...


@final
class _Waiter:
    """One queued request (:236-239). A wake clears ``resume``; it reserves nothing."""

    __slots__ = ("permits", "resume")

    def __init__(self, permits: int) -> None:
        self.permits = permits
        self.resume: _Resume | None = None


@final
class Semaphore:
    """``waiters`` keeps insertion order, like upstream's Set; ``free`` may go negative after a
    resize, or above ``permits`` after release_all (:177-190)."""

    __slots__ = ("permits", "taken", "waiters")

    def __init__(self, permits: int) -> None:
        self.waiters: dict[_Waiter, None] = {}
        self.taken = 0
        self.permits = permits

    @classmethod
    def make_unsafe(cls, permits: int) -> Semaphore:
        """:234."""
        return cls(permits)

    @classmethod
    def make(cls, permits: int) -> Effect[Semaphore, Never, Never]:
        """A fresh semaphore each time this effect runs (:424)."""
        return sync(lambda: cls(permits))

    @property
    def free(self) -> int:
        return self.permits - self.taken

    def _first_available(self) -> _Waiter | None:
        """firstAvailableWaiter, :241-247."""
        for waiter in self.waiters:
            if waiter.permits <= self.free:
                return waiter
        return None

    def _wake(self) -> None:
        """The task releaseUnsafe queues (:323-336): wake every request the free permits cover,
        counting already-woken ones, over a snapshot because resumed fibers run inline."""
        remaining = self.free
        for waiter in list(self.waiters):
            if waiter not in self.waiters or waiter.permits > remaining:
                continue
            remaining -= waiter.permits
            resume, waiter.resume = waiter.resume, None
            if resume is not None:
                resume(_unit)

    def _release_unsafe(self, dispatcher: SchedulerDispatcher, permits: int) -> int:
        """releaseUnsafe, :320-339."""
        self.taken -= permits
        if self.waiters:
            dispatcher.schedule_task(self._wake, 0)
        return self.free

    def _release_on_exit(self, permits: int) -> Callable[[object, SchedulerDispatcher], None]:
        """The exit callback of withPermits/withPermitsIfAvailable (:365-368, :380-383)."""

        def release(_: object, dispatcher: SchedulerDispatcher) -> None:
            self._release_unsafe(dispatcher, permits)

        return release

    def _leave_unsafe(self, waiter: _Waiter, dispatcher: SchedulerDispatcher) -> None:
        """`remove`, :263-266: leave the queue and let the others re-check, in one step."""
        if waiter in self.waiters:
            del self.waiters[waiter]
            self._release_unsafe(dispatcher, 0)

    def _take_permits[A, E, R](
        self, n: int, on_acquire: Callable[[], Effect[A, E, R]], restore: _Restore = _unmasked
    ) -> Effect[A, E, R]:
        """takePermits, :249-284. Upstream queues the waiter and pushes its exit frame in one
        step (onExitUnsafe puts a continuation frame on the fiber's stack, internal/effect.ts:
        4324-4329). Here the frame is pushed first and the waiter queued inside it, so no
        interrupt can fall between queueing and the frame; see
        test_queued_take_interrupted_at_any_operation_leaves_no_queue_entry."""

        def run() -> Effect[A, E, R]:
            waiter = _Waiter(n)

            def start() -> Effect[A, E, R]:
                if self.free >= n and self._first_available() is None:
                    self.taken += n
                    return on_acquire()
                self.waiters[waiter] = None
                return loop

            def register(resume: _Resume) -> None:
                # The fiber may have yielded since the last check, missing a wake (:270).
                if self._first_available() is waiter:
                    resume(_unit)
                else:
                    waiter.resume = resume

            def after(dispatcher: SchedulerDispatcher) -> Effect[A, E, R]:
                if self._first_available() is not waiter:
                    return loop
                self.taken += n
                self._leave_unsafe(waiter, dispatcher)
                return on_acquire()

            def leave(_: object, dispatcher: SchedulerDispatcher) -> None:
                self._leave_unsafe(waiter, dispatcher)

            loop = restore(async_(register)).flat_map(lambda _: with_dispatcher(after))
            return on_exit_with_dispatcher(suspend(start), leave)

        return suspend(run)

    def take(self, permits: int) -> Effect[int, Never, Never]:
        """Wait for and take permits; not released on interruption after taking (:299-301)."""
        return self._take_permits(permits, lambda: succeed(permits))

    def take_scoped(self, permits: int) -> Effect[None, Never, Scope]:
        """Take permits and release them when the scope closes (:303-310)."""
        return uninterruptible_mask(
            lambda restore: scope.flat_map(
                lambda s: self._take_permits(
                    permits, lambda: Scope.add_finalizer(s, self.release(permits)), restore
                )
            )
        )

    def take_if_available(self, permits: int) -> Effect[bool, Never, Never]:
        """Take only if free now, ignoring queued requests (:312-318)."""

        def go() -> Effect[bool, Never, Never]:
            if self.free < permits:
                return succeed(False)
            self.taken += permits
            return succeed(True)

        return suspend(go)

    def release(self, permits: int) -> Effect[int, Never, Never]:
        """Release permits and return how many are free (:350-352)."""
        return with_dispatcher(lambda d: succeed(self._release_unsafe(d, permits)))

    def release_all(self) -> Effect[int, Never, Never]:
        """Release everything taken, including permits other holders will still release
        (:354-356; documented at :177-190)."""
        return with_dispatcher(lambda d: succeed(self._release_unsafe(d, self.taken)))

    def resize(self, permits: int) -> Effect[None, Never, Never]:
        """Change the total; wakes queued requests only if none is over-taken (:341-348)."""

        def go(dispatcher: SchedulerDispatcher) -> Effect[None, Never, Never]:
            self.permits = permits
            if self.free >= 0:
                self._release_unsafe(dispatcher, 0)
            return _unit

        return with_dispatcher(go)

    def with_permits(self, permits: int) -> WithPermits:
        """Run an effect holding permits, released however it ends (:358-372)."""

        def gate[A, E, R](eff: Effect[A, E, R]) -> Effect[A, E, R]:
            return uninterruptible_mask(
                lambda restore: self._take_permits(
                    permits,
                    lambda: on_exit_with_dispatcher(restore(eff), self._release_on_exit(permits)),
                    restore,
                )
            )

        return gate

    def with_permit[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R]:
        """:374."""
        return self.with_permits(1)(eff)

    def with_permits_if_available(self, permits: int) -> WithPermitsIfAvailable:
        """Some(result) holding permits, or None at once when short (:376-386)."""

        def gate[A, E, R](eff: Effect[A, E, R]) -> Effect[Option[A], E, R]:
            def body(restore: _Restore) -> Effect[Option[A], E, R]:
                def go() -> Effect[Option[A], E, R]:
                    if self.free < permits:
                        return succeed(None)
                    self.taken += permits
                    held = restore(eff.map(Some[A]))
                    return on_exit_with_dispatcher(held, self._release_on_exit(permits))

                return suspend(go)

            return uninterruptible_mask(body)

        return gate
