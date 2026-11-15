"""Fiber: one running effect. Owns the stack, the interrupt state, and the children it forked.

The run loop is `_drive`: evaluate a node, get the next node, repeat. It stops when a node returns
None, which means one of three things: the fiber parked on an Async, it rescheduled itself
(YieldNow or the 2048-op fairness budget), or it finished. Effect v4: internal/effect.ts:673-770.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from typing import Any, Generic, Never, TypeVar, final

from peffect.cause import Cause
from peffect.core import (
    ANY,
    ERR,
    OK,
    UNIT,
    AnyEffect,
    Async,
    Effect,
    Exit,
    Failure,
    Frame,
    Park,
    Resume,
    Success,
    Sync,
    Then,
    WithFiber,
)

A_co = TypeVar("A_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)

OPS_BEFORE_YIELD = 2048  # Effect v4 Scheduler.ts:198
_ids = itertools.count(1)

type Observer = Callable[[Exit[Any, Any]], None]


@final
class Fiber(Generic[A_co, E_co]):
    __slots__ = (
        "_deferred",
        "_ending",
        "_exit",
        "_loop",
        "_observers",
        "_parent",
        "_parked",
        "children",
        "context",
        "id",
        "interrupted",
        "interruptible",
        "running",
        "stack",
    )

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
        parent: Fiber[Any, Any] | None = None,
    ) -> None:
        self.id = next(_ids)
        self.stack: list[Frame] = []
        self.context = context
        self.interruptible = True
        self.interrupted: Cause[Never] | None = None
        self.running = False
        self.children: set[Fiber[Any, Any]] = set()
        self._loop = loop
        self._parent = parent
        self._ending: Exit[Any, Any] | None = None  # body exit, kept while children finish
        self._deferred = False  # an interrupt arrived while running; deliver at the next step
        self._parked: Park | None = None
        self._exit: Exit[Any, Any] | None = None
        self._observers: list[Observer] = []

    def __repr__(self) -> str:
        return f"Fiber#{self.id}"

    # -- the run loop ------------------------------------------------------------------------------

    def _drive(self, eff: AnyEffect) -> None:
        self._parked = None
        self.running = True
        cur: AnyEffect | None = eff
        ops = 0
        try:
            while cur is not None:
                if self._deferred and self.interruptible:
                    cur = Failure(self.take_interrupt())
                ops += 1
                if ops > OPS_BEFORE_YIELD:
                    self._reschedule(cur)
                    return
                try:
                    cur = cur._eval(self)  # pyright: ignore[reportPrivateUsage]
                except Exception as e:  # a bug in user code or a combinator: a defect
                    cur = Failure(Cause.die(e))
        finally:
            self.running = False
        # Parked while an interrupt was waiting (it arrived during register): deliver it now.
        if self._deferred and self.interruptible and self._parked is not None:
            self._interrupt_parked(self._parked)

    def _pop(self, ok: bool) -> Frame | None:
        """Pop to the next frame that handles this outcome; on_any runs on every frame popped."""
        want = OK if ok else ERR
        stack = self.stack
        while stack:
            frame = stack.pop()
            if frame.kind & ANY and (eff := frame.on_any(self, ok)) is not None:
                return Then(eff)
            if frame.kind & want:
                return frame
        return None

    def _reschedule(self, eff: AnyEffect) -> None:
        self._loop.call_soon(self._drive, eff)

    def _park(self, park: Park) -> None:
        self._parked = park

    def _wake(self, eff: AnyEffect) -> None:
        self._parked = None
        self._loop.call_soon(self._drive, eff)

    # -- interruption ------------------------------------------------------------------------------

    def interrupt_unsafe(self, by: int | None = None) -> None:
        """Ask the fiber to stop. Returns immediately; use `interrupt()` to also wait."""
        if self._exit is not None:
            return
        cause = Cause.interrupt(by)
        self.interrupted = cause if self.interrupted is None else self.interrupted + cause
        parked = self._parked
        if not self.running and parked is not None and self.interruptible and not parked.done:
            self._interrupt_parked(parked)
        else:
            self._deferred = True  # running, not started yet, or masked: picked up later

    def take_interrupt(self) -> Cause[Never]:
        """Deliver the pending interrupt: the one place `_deferred` is cleared, so an interrupt
        is delivered once however it is picked up (loop top, mask end, unwind, parked wake)."""
        self._deferred = False
        return self.interrupted or Cause.interrupt()

    def _interrupt_parked(self, parked: Park) -> None:
        parked.done = True  # a late resume() is now ignored
        self._wake(Failure(self.take_interrupt()))

    # -- finishing ---------------------------------------------------------------------------------

    def _done(self, exit_: Exit[Any, Any]) -> AnyEffect | None:
        """The stack is empty. Interrupt live children and wait for them, then publish once.

        Like v4 (internal/effect.ts:643-648), the wait runs inside the run loop as an Async, so an
        interrupt sent while waiting is still delivered (K7). v4 then drops the body's exit; here
        the interrupt is added to it instead, so a body's defects survive (K9).
        """
        if self._ending is not None:  # back from waiting: Success, or the interrupt that landed
            ending = self._ending
            if isinstance(exit_, Failure):
                exit_ = Failure(
                    ending.cause + exit_.cause if isinstance(ending, Failure) else exit_.cause
                )
            else:
                exit_ = ending
        live = [c for c in self.children if c._exit is None]
        if not live:
            self._publish(exit_)
            return None
        self._ending = exit_
        for c in live:
            c.interrupt_unsafe(self.id)
        return _await_all(live)

    def _publish(self, exit_: Exit[Any, Any]) -> None:
        self._exit = exit_
        if self._parent is not None:
            self._parent.children.discard(self)
        observers, self._observers = self._observers, []
        for cb in observers:
            cb(exit_)

    @property
    def exit(self) -> Exit[A_co, E_co] | None:
        return self._exit

    def add_observer(self, cb: Observer) -> Callable[[], None]:
        """Call cb with the exit when the fiber is done (now, if it is). Returns remove()."""
        if self._exit is not None:
            cb(self._exit)
            return lambda: None
        self._observers.append(cb)
        return lambda: self._observers.remove(cb) if cb in self._observers else None

    # -- effects on this fiber ---------------------------------------------------------------------

    def await_(self) -> Effect[Exit[A_co, E_co], Never, Never]:
        """Wait for the fiber; succeed with its Exit (never fails)."""

        def register(resume: Resume[Exit[A_co, E_co], Never, Never]) -> AnyEffect:
            remove = self.add_observer(lambda ex: resume(Success(ex)))
            return Sync(remove)

        return Async(register)

    def join(self) -> Effect[A_co, E_co, Never]:
        """Wait for the fiber and continue as it ended: its value, or its failure."""

        def register(resume: Resume[A_co, E_co, Never]) -> AnyEffect:
            remove = self.add_observer(resume)  # an Exit is an effect: resume with it directly
            return Sync(remove)

        return Async(register)

    def interrupt(self) -> Effect[None, Never, Never]:
        """Interrupt and wait until it has finished (its finalizers have run)."""

        def go(me: Fiber[Any, Any]) -> AnyEffect:
            self.interrupt_unsafe(me.id)
            return self.await_().as_(None)

        return WithFiber(go)


def _await_all(fibers: list[Fiber[Any, Any]]) -> AnyEffect:
    """Park until every fiber has published. Interruptible: cancelling removes the observers."""

    def register(resume: Resume[None, Never, Never]) -> AnyEffect:
        remaining = len(fibers)

        def one_done(_: Exit[Any, Any]) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                resume(UNIT)

        removes = [f.add_observer(one_done) for f in fibers]

        def cancel() -> None:
            for r in removes:
                r()

        return Sync(cancel)

    return Async(register)


# ================================================================================================
# Forking and running
# ================================================================================================


def _start[A, E](
    parent: Fiber[Any, Any] | None,
    eff: Effect[A, E, Any],
    ctx: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> Fiber[A, E]:
    child: Fiber[A, E] = Fiber(loop, ctx, parent)
    if parent is not None:
        parent.children.add(child)
    loop.call_soon(child._drive, eff)  # pyright: ignore[reportPrivateUsage]
    return child


def fork[A, E, R](eff: Effect[A, E, R]) -> Effect[Fiber[A, E], Never, R]:
    """Run eff concurrently. The child is supervised: when the parent ends, the child is
    interrupted and the parent waits for it."""
    return WithFiber(lambda p: Success(_start(p, eff, p.context, p._loop)))  # pyright: ignore[reportPrivateUsage]


def fork_daemon[A, E, R](eff: Effect[A, E, R]) -> Effect[Fiber[A, E], Never, R]:
    """Run eff concurrently with no parent: it outlives the fiber that started it."""
    return WithFiber(lambda p: Success(_start(None, eff, p.context, p._loop)))  # pyright: ignore[reportPrivateUsage]


async def run_exit[A, E](eff: Effect[A, E, Never]) -> Exit[A, E]:
    """Run on the current asyncio loop and return the Exit. Cancelling the awaiting task
    interrupts the fiber and waits for its finalizers before re-raising CancelledError."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Exit[A, E]] = loop.create_future()
    fiber = _start(None, eff, {}, loop)
    fiber.add_observer(lambda ex: None if fut.done() else fut.set_result(ex))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        fiber.interrupt_unsafe()
        await fut
        raise


async def run[A](eff: Effect[A, Never, Never]) -> A:
    """Run and return the value; a failure raises cause.squash()."""
    exit_ = await run_exit(eff)
    if isinstance(exit_, Success):
        return exit_.value
    raise exit_.cause.squash()


__all__ = ["UNIT", "Fiber", "fork", "fork_daemon", "run", "run_exit"]
