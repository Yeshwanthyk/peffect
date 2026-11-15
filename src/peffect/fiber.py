"""Fiber: one running effect. Owns the stack, the interrupt state, and the children it forked.

The run loop is `_drive`: evaluate a node, get the next node, repeat. It stops when a node returns
None, which means one of three things: the fiber parked on an Async, it rescheduled itself
(YieldNow or the 2048-op fairness budget), or it finished. Effect v4: internal/effect.ts:673-770.
"""

from __future__ import annotations

import asyncio
import itertools
import math
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Generic, Literal, Never, TypeVar, final

from peffect.cause import Cause, Interrupt
from peffect.core import (
    ANY,
    EMPTY_CONTEXT,
    ERR,
    OK,
    UNIT,
    AnyEffect,
    Async,
    ContextMap,
    Effect,
    Exit,
    Failure,
    Frame,
    Park,
    Resume,
    Success,
    Suspend,
    Sync,
    Then,
    WithFiber,
    uninterruptible,
)

A_co = TypeVar("A_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)

OPS_BEFORE_YIELD = 2048  # Effect v4 Scheduler.ts:198
_ids = itertools.count(1)

type Observer = Callable[[Exit[Any, Any]], None]


@final
class Fiber(Generic[A_co, E_co]):
    __slots__ = (
        "_context",
        "_deferred",
        "_ending",
        "_exit",
        "_loop",
        "_observers",
        "_parent",
        "_parked",
        "children",
        "id",
        "interrupted",
        "interruptible",
        "running",
        "stack",
    )

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        context: ContextMap,
        parent: Fiber[Any, Any] | None = None,
    ) -> None:
        self.id = next(_ids)
        self.stack: list[Frame] = []
        self._context = context
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

    @property
    def context(self) -> ContextMap:
        """The services this fiber sees. Read-only: change it with update_context."""
        return self._context

    def swap_context(self, context: ContextMap) -> ContextMap:
        """Kernel only (update_context, RestoreContext): install ``context``, return the old one."""
        old, self._context = self._context, context
        return old

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


def await_all[A, E](fibers: Iterable[Fiber[A, E]]) -> Effect[list[Exit[A, E]], Never, Never]:
    """Wait in input order, returning target failures as Exits. Cancellation removes the
    current observer without interrupting targets (Effect v4 internal/effect.ts:876-908)."""

    def register(resume: Resume[list[Exit[A, E]], Never, Never]) -> AnyEffect:
        iterator = iter(fibers)
        exits: list[Exit[A, E]] = []
        cancel: Callable[[], None] | None = None

        def loop() -> None:
            nonlocal cancel
            while True:
                try:
                    fiber = next(iterator, None)
                except Exception as error:
                    resume(Failure(Cause.die(error)))
                    return
                if fiber is None:
                    resume(Success(exits))
                    return
                exit_ = fiber.exit
                if exit_ is not None:
                    exits.append(exit_)
                    continue
                cancel = fiber.add_observer(one_done)
                return

        def one_done(exit_: Exit[A, E]) -> None:
            exits.append(exit_)
            loop()

        def cleanup() -> None:
            if cancel is not None:
                cancel()

        loop()
        return Sync(cleanup)

    return Async(register)


def interrupt_all[A, E](fibers: Iterable[Fiber[A, E]]) -> Effect[None, Never, Never]:
    """Signal every target with the executing caller's id before awaiting any cleanup.
    Consume the iterable once (Effect v4 internal/effect.ts:992-1003)."""

    def go(parent: Fiber[Any, Any]) -> Effect[None, Never, Never]:
        targets: list[Fiber[A, E]] = []
        for fiber in fibers:
            fiber.interrupt_unsafe(parent.id)
            targets.append(fiber)
        return await_all(targets).as_(None)

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
    ctx: ContextMap,
    loop: asyncio.AbstractEventLoop,
    immediate: bool = False,
    interruptible: bool = True,
) -> Fiber[A, E]:
    """Effect v4: forkUnsafe, internal/effect.ts:5614-5637. ``immediate`` runs the child's first
    slice now, inside the caller's drive, until it parks or ends; otherwise it is scheduled."""
    child: Fiber[A, E] = Fiber(loop, ctx, parent)
    child.interruptible = interruptible
    if parent is not None:
        parent.children.add(child)
    drive = child._drive  # pyright: ignore[reportPrivateUsage]
    if immediate:
        drive(eff)
    else:
        loop.call_soon(drive, eff)
    return child


def fork[A, E, R](
    eff: Effect[A, E, R],
    *,
    start_immediately: bool = False,
    uninterruptible: Literal["inherit"] | None = None,
) -> Effect[Fiber[A, E], Never, R]:
    """Run eff concurrently. The child is supervised: when the parent ends, the child is
    interrupted and the parent waits for it. ``start_immediately`` runs the child until it first
    parks or ends before ``fork`` returns (Effect v4: forkChild's ``startImmediately``).
    Only ``uninterruptible="inherit"`` copies the caller's mask; the default is interruptible."""
    if uninterruptible is not None and uninterruptible != "inherit":
        raise ValueError('uninterruptible must be None or "inherit"')
    return WithFiber(
        lambda p: Success(
            _start(
                p,
                eff,
                p.context,
                p._loop,  # pyright: ignore[reportPrivateUsage]
                start_immediately,
                p.interruptible if uninterruptible == "inherit" else True,
            )
        )
    )


def fork_daemon[A, E, R](
    eff: Effect[A, E, R],
    *,
    start_immediately: bool = False,
    uninterruptible: Literal["inherit"] | None = None,
) -> Effect[Fiber[A, E], Never, R]:
    """Run eff concurrently with no parent: it outlives the fiber that started it.
    ``start_immediately`` and ``uninterruptible="inherit"`` work as for ``fork``
    (Effect v4: forkDetach, internal/effect.ts:5728)."""
    if uninterruptible is not None and uninterruptible != "inherit":
        raise ValueError('uninterruptible must be None or "inherit"')
    return WithFiber(
        lambda p: Success(
            _start(
                None,
                eff,
                p.context,
                p._loop,  # pyright: ignore[reportPrivateUsage]
                start_immediately,
                p.interruptible if uninterruptible == "inherit" else True,
            )
        )
    )


# ================================================================================================
# Traversal
# ================================================================================================


def for_each[A, B, E, R](
    items: Iterable[A],
    f: Callable[[A, int], Effect[B, E, R]],
    *,
    concurrency: int | Literal["unbounded"] = 1,
) -> Effect[list[B], E, R]:
    """Map each item and its index to an effect; succeed with the values in input order.

    Concurrency 1 (the default; below 1 also means 1) runs one item at a time, reading the
    iterable lazily. Higher runs up to that many items at once as traversal-owned daemon workers
    that inherit the caller's mask. The first failure interrupts the rest; cleanup defects are kept.
    Each run starts afresh. Effect v4: forEach, internal/effect.ts:5030-5112."""
    limit = math.inf if concurrency == "unbounded" else max(1, concurrency)

    def start() -> Effect[list[B], E, R]:
        if limit == 1:
            return _for_each_sequential(iter(items), f)
        array = list(items)
        if not array:
            return Success([])
        out: list[Any] = [None] * len(array)
        eff = _for_each_concurrent(array, f, out, limit)
        return Success(out) if eff is None else eff.as_(out)

    return Suspend(start)


def _for_each_sequential[A, B, E, R](
    iterator: Iterator[A], f: Callable[[A, int], Effect[B, E, R]]
) -> Effect[list[B], E, R]:
    """forEachSequential, internal/effect.ts:5085-5108: the next item is read after a step."""
    out: list[B] = []

    def next_(index: int) -> Effect[list[B], E, R]:
        for item in iterator:

            def keep(b: B) -> Effect[list[B], E, R]:
                out.append(b)
                return next_(index + 1)

            return f(item, index).flat_map(keep)
        return Success(out)

    return next_(0)


def _for_each_concurrent[A, B, E, R](
    items: list[A], f: Callable[[A, int], Effect[B, E, R]], out: list[Any], concurrency: float
) -> Effect[None, E, R] | None:
    """iterateConcurrentImpl with forEachConcurrent's step, internal/effect.ts:5160-5320.

    `go` pumps items until the window is full or a terminal Exit is recorded. Ready Exits are
    stepped inline; the first effect that is not one becomes an Async on the caller, and each
    later one a child forked with the caller's mask. A child's observer steps its Exit, refills
    a paused `go`, or after a terminal folds the child's non-interrupt reasons into it. None
    means every item finished inline with no failure."""
    index = 0
    end = len(items)
    done = False
    parent: Fiber[Any, Any] | None = None
    fibers: dict[Fiber[Any, Any], None] = {}
    resume: Resume[None, E, R] | None = None
    terminal: Exit[None, E] | None = None
    effect: Effect[B, E, R] | None = None  # the item effect pending while the Async starts

    def step(exit_: Exit[B, E], i: int) -> Failure[E] | None:
        if isinstance(exit_, Failure):
            return exit_
        out[i] = exit_.value
        return None

    def fail_defect(error: Exception) -> Effect[None, E, R]:
        nonlocal terminal, done
        defect: Failure[E] = Failure(Cause.die(error))
        terminal = defect
        done = True
        if not fibers:
            return defect
        return uninterruptible(interrupt_all(list(fibers))).flat_map(
            lambda _: defect if terminal is None else terminal
        )

    def on_cancel() -> Effect[None, E, R]:
        nonlocal terminal
        if terminal is None:
            terminal = UNIT
        return interrupt_all(list(fibers)).flat_map(
            lambda _: terminal if isinstance(terminal, Failure) else UNIT
        )

    def observe(fiber: Fiber[B, E], i: int, paused: list[bool]) -> Observer:
        def on_exit(exit_: Exit[B, E]) -> None:
            nonlocal terminal
            assert resume is not None
            fibers.pop(fiber, None)
            try:
                if terminal is not None:
                    if isinstance(exit_, Failure):
                        kept = tuple(r for r in exit_.cause.reasons if not isinstance(r, Interrupt))
                        if kept:
                            cause = Cause(kept)
                            terminal = Failure(
                                (terminal.cause + cause).elect_defects()
                                if isinstance(terminal, Failure)
                                else cause
                            )
                elif (result := step(exit_, i)) is not None:
                    terminal = result
                    go()
                if paused[0]:
                    if (eff := go()) is not None:
                        resume(eff)
                elif done and not fibers:
                    resume(UNIT if terminal is None else terminal)
            except Exception as error:
                resume(fail_defect(error))

        return on_exit

    def go() -> Effect[None, E, R] | None:
        nonlocal index, done, terminal, effect
        paused = [False]  # this call's: its observers refill only once it has paused
        while terminal is None and index < end:
            eff = effect if effect is not None else f(items[index], index)
            if isinstance(eff, (Success, Failure)):
                terminal = step(eff, index)
                if terminal is not None:
                    break
            elif parent is None:
                return WithFiber(lambda p, first=eff: Async(lambda cb: register(p, first, cb)))
            else:
                effect = None
                # Every go() runs on the caller's loop: in its drive, or in a child's observer.
                loop = asyncio.get_running_loop()
                fiber = _start(None, eff, parent.context, loop, True, parent.interruptible)
                if fiber.exit is not None:
                    terminal = step(fiber.exit, index)
                    if terminal is not None:
                        break
                else:
                    fibers[fiber] = None
                    fiber.add_observer(observe(fiber, index, paused))
                    if len(fibers) >= concurrency:
                        paused[0] = True
                        index += 1
                        return None
            index += 1
        done = True
        if terminal is not None:
            if fibers:
                assert parent is not None
                for fiber in list(fibers):
                    fiber.interrupt_unsafe(parent.id)
                return None
            if resume is not None or isinstance(terminal, Failure):
                return terminal
        elif resume is not None and not fibers:
            resume(UNIT)
        return None

    def register(
        p: Fiber[Any, Any], first: Effect[B, E, R], cb: Resume[None, E, R]
    ) -> Effect[None, E, R] | None:
        nonlocal parent, effect, resume
        parent, effect, resume = p, first, cb
        try:
            result = go()
        except Exception as error:
            cb(fail_defect(error))
            return None
        if result is not None:
            cb(result)
            return None
        return Suspend(on_cancel)

    return go()


async def run_exit[A, E](eff: Effect[A, E, Never]) -> Exit[A, E]:
    """Run on the current asyncio loop and return the Exit. Cancelling the awaiting task
    interrupts the fiber and waits for its finalizers before re-raising CancelledError."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Exit[A, E]] = loop.create_future()
    fiber = _start(None, eff, EMPTY_CONTEXT, loop)
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


__all__ = [
    "UNIT",
    "Fiber",
    "await_all",
    "for_each",
    "fork",
    "fork_daemon",
    "interrupt_all",
    "run",
    "run_exit",
]
