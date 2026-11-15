"""The kernel: Effect nodes, stack frames, and the combinators derived from them.

An Effect is a *description*. Nothing runs until a Fiber (fiber.py) evaluates it. The fiber's run
loop calls ``node._eval(fiber)``, which returns the NEXT node to evaluate (or None: "parked").
Combinators never call each other recursively; they push frames on the fiber's stack. That's what
keeps a million ``flat_map``s from blowing Python's recursion limit.

Seven nodes (Effect v4: internal/core.ts, internal/effect.ts):

    Success(a)        done: hand a to the next on_ok frame
    Failure(cause)    done: hand cause to the next on_err frame
    Sync(thunk)       run a side effect now
    Suspend(thunk)    build the next effect lazily
    Async(register)   park until resume(effect) is called, once
    WithFiber(f)      look at / change the running fiber
    YieldNow          let other asyncio work run

Frames (pushed on the stack; up to three hooks):

    on_ok(value)      success arrives
    on_err(cause)     failure arrives
    on_any(ok)        runs whenever the frame is popped, even if it's being skipped
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Generator, Mapping
from functools import wraps
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Never, TypeVar, cast, final, overload

from peffect.cause import Cause, Interrupt

if TYPE_CHECKING:
    from peffect.fiber import Fiber

A_co = TypeVar("A_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)
R_co = TypeVar("R_co", covariant=True)

type AnyEffect = Effect[Any, Any, Any]
type AnyFiber = Fiber[Any, Any]
type ContextMap = Mapping[object, Any]
"""A fiber's services, keyed by tag class. Read-only: update_context installs a fresh copy."""

EMPTY_CONTEXT: ContextMap = MappingProxyType({})


class Effect(Generic[A_co, E_co, R_co]):
    """A program that succeeds with A, fails with E, and needs services R. Lazy and reusable."""

    __slots__ = ()

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        raise NotImplementedError

    # `x = yield from eff` inside @gen: yield this effect, receive its success value.
    def __iter__(self) -> Generator[Effect[A_co, E_co, R_co], Any, A_co]:
        return cast(A_co, (yield self))

    def pipe[B](self, f: Callable[[Effect[A_co, E_co, R_co]], B], /) -> B:
        return f(self)

    def map[B](self, f: Callable[[A_co], B]) -> Effect[B, E_co, R_co]:
        return OnSuccess(self, lambda a: Success(f(a)))

    def flat_map[B, E2, R2](
        self, f: Callable[[A_co], Effect[B, E2, R2]]
    ) -> Effect[B, E_co | E2, R_co | R2]:
        return OnSuccess(self, f)

    def as_[B](self, value: B) -> Effect[B, E_co, R_co]:
        return OnSuccess(self, lambda _: Success(value))

    def catch_all[B, E2 = Never, R2 = Never](
        self, handler: Callable[[E_co], Effect[B, E2, R2]]
    ) -> Effect[A_co | B, E2, R_co | R2]:
        return _catch_all(self, handler)

    def catch_cause[B, E2 = Never, R2 = Never](
        self, handler: Callable[[Cause[E_co]], Effect[B, E2, R2]]
    ) -> Effect[A_co | B, E2, R_co | R2]:
        return OnFailure(self, handler)

    def on_exit[R2 = Never](
        self, f: Callable[[Exit[A_co, E_co]], Effect[Any, Never, R2]]
    ) -> Effect[A_co, E_co, R_co | R2]:
        return OnExit(self, f)

    def ensuring[R2 = Never](
        self, finalizer: Effect[Any, Never, R2]
    ) -> Effect[A_co, E_co, R_co | R2]:
        return OnExit(self, lambda _exit: finalizer)


# ================================================================================================
# Exit = Success | Failure. An Exit is an effect that is already done.
# ================================================================================================


@final
class Success(Effect[A_co, Never, Never]):
    __slots__ = ("value",)
    __match_args__ = ("value",)

    def __init__(self, value: A_co) -> None:
        self.value = value

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        k = fiber._pop(ok=True)  # pyright: ignore[reportPrivateUsage]
        return k.on_ok(self.value, fiber) if k is not None else fiber._done(self)  # pyright: ignore[reportPrivateUsage]

    def __repr__(self) -> str:
        return f"Success({self.value!r})"


@final
class Failure(Effect[Never, E_co, Never]):
    __slots__ = ("cause",)
    __match_args__ = ("cause",)

    def __init__(self, cause: Cause[E_co]) -> None:
        self.cause = cause

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        cause: Cause[Any] = self.cause
        k = fiber._pop(ok=False)  # pyright: ignore[reportPrivateUsage]
        if fiber.interrupted is not None and fiber.interruptible:
            # Interrupted: skip every handler until something masks the fiber (a finalizer frame
            # does, as it's popped). Recovering from an interrupt must not be possible.
            # Effect v4: internal/core.ts:601-629
            skipped = False
            while k is not None and fiber.interruptible:
                skipped = skipped or not k.restoring
                k = fiber._pop(ok=False)  # pyright: ignore[reportPrivateUsage]
            if skipped:
                cause = cause.without_fails()
            cause = cause + fiber.take_interrupt()
        return k.on_err(cause, fiber) if k is not None else fiber._done(Failure(cause))  # pyright: ignore[reportPrivateUsage]

    def __repr__(self) -> str:
        return f"Failure({self.cause!r})"


type Exit[A, E] = Success[A] | Failure[E]

UNIT: Success[None] = Success(None)


# ================================================================================================
# Other nodes
# ================================================================================================


@final
class Sync(Effect[A_co, Never, Never]):
    __slots__ = ("thunk",)

    def __init__(self, thunk: Callable[[], A_co]) -> None:
        self.thunk = thunk

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        return Success(self.thunk())  # a raise is caught by the run loop -> Die


@final
class Suspend(Effect[A_co, E_co, R_co]):
    __slots__ = ("thunk",)

    def __init__(self, thunk: Callable[[], Effect[A_co, E_co, R_co]]) -> None:
        self.thunk = thunk

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        return self.thunk()


@final
class WithFiber(Effect[A_co, E_co, R_co]):
    __slots__ = ("f",)

    def __init__(self, f: Callable[[AnyFiber], Effect[A_co, E_co, R_co]]) -> None:
        self.f = f

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        return self.f(fiber)


@final
class YieldNow(Effect[None, Never, Never]):
    __slots__ = ()

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        fiber._reschedule(UNIT)  # pyright: ignore[reportPrivateUsage]
        return None


type Resume[A, E, R] = Callable[[Effect[A, E, R]], None]
type Register[A, E, R] = Callable[[Resume[A, E, R]], Effect[Any, Any, Any] | None]


class Park:
    """One parked Async. ``done`` flips once: by resume, or by an interrupt that got there first."""

    __slots__ = ("done", "registering", "sync")

    def __init__(self) -> None:
        self.done = False
        self.registering = True
        self.sync: AnyEffect | None = None


@final
class Async(Effect[A_co, E_co, R_co]):
    """Suspend the fiber. ``register(resume)`` starts the work and may return a cancel effect.

    resume(effect) continues the fiber with that effect. Only the first call counts.
    Effect v4: internal/effect.ts:1206-1273
    """

    __slots__ = ("register",)

    def __init__(self, register: Register[A_co, E_co, R_co]) -> None:
        self.register = register

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        park = Park()

        def resume(eff: Effect[A_co, E_co, R_co]) -> None:
            if park.done:
                return
            park.done = True
            if park.registering:
                park.sync = eff  # resumed before register returned: no need to park
            else:
                fiber._wake(eff)  # pyright: ignore[reportPrivateUsage]

        cancel = self.register(resume)
        park.registering = False
        if park.sync is not None:
            return park.sync
        fiber._park(park)  # pyright: ignore[reportPrivateUsage]
        if cancel is not None:
            fiber.stack.append(_AsyncCancel(cancel))
        return None


# ================================================================================================
# Frames
# ================================================================================================

OK, ERR, ANY = 1, 2, 4


class Frame:
    __slots__ = ()
    kind: ClassVar[int] = 0
    restoring: ClassVar[bool] = False  # True = bookkeeping, not a user handler

    def on_ok(self, value: Any, fiber: AnyFiber) -> AnyEffect | None:
        raise NotImplementedError

    def on_err(self, cause: Cause[Any], fiber: AnyFiber) -> AnyEffect | None:
        raise NotImplementedError

    def on_any(self, fiber: AnyFiber, ok: bool) -> AnyEffect | None:
        return None


@final
class Then(Frame):
    """Whatever arrives, continue with ``eff``. Produced when an on_any hook takes over."""

    __slots__ = ("eff",)
    kind = OK | ERR
    restoring = True

    def __init__(self, eff: AnyEffect) -> None:
        self.eff = eff

    def on_ok(self, value: Any, fiber: AnyFiber) -> AnyEffect | None:
        return self.eff

    def on_err(self, cause: Cause[Any], fiber: AnyFiber) -> AnyEffect | None:
        return self.eff


@final
class OnSuccess(Effect[A_co, E_co, R_co], Frame):
    __slots__ = ("eff", "f")
    kind = OK

    def __init__(self, eff: AnyEffect, f: Callable[[Any], AnyEffect]) -> None:
        self.eff = eff
        self.f = f

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        fiber.stack.append(self)
        return self.eff

    def on_ok(self, value: Any, fiber: AnyFiber) -> AnyEffect | None:
        return self.f(value)


@final
class OnFailure(Effect[A_co, E_co, R_co], Frame):
    __slots__ = ("eff", "f")
    kind = ERR

    def __init__(self, eff: AnyEffect, f: Callable[[Cause[Any]], AnyEffect]) -> None:
        self.eff = eff
        self.f = f

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        fiber.stack.append(self)
        return self.eff

    def on_err(self, cause: Cause[Any], fiber: AnyFiber) -> AnyEffect | None:
        return self.f(cause)


def _mask_while_popping(fiber: AnyFiber) -> None:
    """Make the fiber uninterruptible for the handler about to run; restore it afterwards."""
    if fiber.interruptible:
        fiber.interruptible = False
        fiber.stack.append(RESTORE_INTERRUPTIBLE)


@final
class OnExit(Effect[A_co, E_co, R_co], Frame):
    """Run ``f(exit)`` after ``eff`` however it ends, uninterruptibly, then re-emit the exit.

    on_any masks the fiber as the frame is popped. That is what stops Failure's skip-loop from
    skipping this frame when the fiber is being interrupted. Effect v4: internal/effect.ts:4270-4302
    """

    __slots__ = ("eff", "f")
    kind = OK | ERR | ANY

    def __init__(self, eff: AnyEffect, f: Callable[[Exit[Any, Any]], AnyEffect]) -> None:
        self.eff = eff
        self.f = f

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        fiber.stack.append(self)
        return self.eff

    def on_any(self, fiber: AnyFiber, ok: bool) -> AnyEffect | None:
        _mask_while_popping(fiber)
        return None

    def _finalizer(self, exit_: Exit[Any, Any]) -> AnyEffect:
        try:
            return self.f(exit_)
        except Exception as e:  # f raised instead of returning an effect: a defect of the finalizer
            return Failure(Cause.die(e))

    def on_ok(self, value: Any, fiber: AnyFiber) -> AnyEffect | None:
        exit_ = Success(value)
        return OnSuccess(self._finalizer(exit_), lambda _: exit_)

    def on_err(self, cause: Cause[Any], fiber: AnyFiber) -> AnyEffect | None:
        exit_: Failure[Any] = Failure(cause)
        # a failing finalizer adds its cause to the original, then defects are elected
        # (combineFinalizerCause, internal/effect.ts:4069-4075)
        fin: AnyEffect = OnFailure(
            self._finalizer(exit_), lambda c2: Failure((cause + c2).elect_defects())
        )
        return OnSuccess(fin, lambda _: exit_)


@final
class SetInterruptible(Frame):
    """Restore interruptibility when popped; deliver an interrupt that waited for it."""

    __slots__ = ("value",)
    kind = ANY
    restoring = True

    def __init__(self, value: bool) -> None:
        self.value = value

    def on_any(self, fiber: AnyFiber, ok: bool) -> AnyEffect | None:
        fiber.interruptible = self.value
        if self.value and fiber.interrupted is not None:
            return Failure(fiber.take_interrupt())
        return None


RESTORE_INTERRUPTIBLE = SetInterruptible(True)
RESTORE_UNINTERRUPTIBLE = SetInterruptible(False)


@final
class RestoreContext(Frame):
    """Put back the context from before update_context, however the effect ends (on_any runs
    on success, failure, and when an interrupt skips the frame)."""

    __slots__ = ("context",)
    kind = ANY
    restoring = True

    def __init__(self, context: ContextMap) -> None:
        self.context = context

    def on_any(self, fiber: AnyFiber, ok: bool) -> AnyEffect | None:
        fiber.swap_context(self.context)
        return None


@final
class _AsyncCancel(Frame):
    """Sits under a parked Async. If the fiber is interrupted while parked, run the cancel effect
    (masked) before the interrupt continues upward. Passes successes straight through."""

    __slots__ = ("cancel",)
    kind = ERR | ANY
    restoring = True

    def __init__(self, cancel: AnyEffect) -> None:
        self.cancel = cancel

    def on_any(self, fiber: AnyFiber, ok: bool) -> AnyEffect | None:
        _mask_while_popping(fiber)
        return None

    def on_err(self, cause: Cause[Any], fiber: AnyFiber) -> AnyEffect | None:
        if not cause.is_interrupted:
            return Failure(cause)
        return OnSuccess(self.cancel, lambda _: Failure(cause))


@final
class GenFrame(Effect[Any, Any, Any], Frame):
    """Drives a generator: send each success in, run whatever it yields next.

    A failure never re-enters the generator (no .throw(), like Effect v4 internal/effect.ts:1487).
    We .close() it so its ``finally`` blocks run now rather than at garbage collection.
    """

    __slots__ = ("gen",)
    kind = OK | ANY

    def __init__(self, gen: Generator[AnyEffect, Any, Any]) -> None:
        self.gen = gen

    def _eval(self, fiber: AnyFiber) -> AnyEffect | None:
        return self.on_ok(None, fiber)

    def on_ok(self, value: Any, fiber: AnyFiber) -> AnyEffect | None:
        while True:
            try:
                nxt = self.gen.send(value)
            except StopIteration as stop:
                return Success(stop.value)
            if isinstance(nxt, Success):  # already done: skip the push/pop round trip
                value = nxt.value
                continue
            if not isinstance(nxt, Effect):  # pyright: ignore[reportUnnecessaryIsInstance]
                self.gen.close()
                raise TypeError(f"@gen body yielded {nxt!r}; use `yield from` on an Effect")
            fiber.stack.append(self)
            return nxt

    def on_any(self, fiber: AnyFiber, ok: bool) -> AnyEffect | None:
        if not ok:
            self.gen.close()
        return None


# ================================================================================================
# Constructors
# ================================================================================================


def succeed[A](value: A) -> Effect[A, Never, Never]:
    return Success(value)


def fail[E](error: E) -> Effect[Never, E, Never]:
    return Failure(Cause.fail(error))


def fail_cause[E](cause: Cause[E]) -> Effect[Never, E, Never]:
    return Failure(cause)


def die(defect: BaseException) -> Effect[Never, Never, Never]:
    return Failure(Cause.die(defect))


def sync[A](thunk: Callable[[], A]) -> Effect[A, Never, Never]:
    """A side effect. Exceptions it raises are defects (Die), not typed errors."""
    return Sync(thunk)


def suspend[A, E, R](thunk: Callable[[], Effect[A, E, R]]) -> Effect[A, E, R]:
    return Suspend(thunk)


def async_[A, E = Never, R = Never](register: Register[A, E, R]) -> Effect[A, E, R]:
    return Async(register)


yield_now: Effect[None, Never, Never] = YieldNow()
never: Effect[Never, Never, Never] = Async(lambda _resume: None)


def sleep(seconds: float) -> Effect[None, Never, Never]:
    def register(resume: Resume[None, Never, Never]) -> AnyEffect:
        handle = asyncio.get_running_loop().call_later(seconds, resume, UNIT)
        return Sync(handle.cancel)

    return Async(register)


def attempt[A, X: BaseException](
    thunk: Callable[[], A] | Callable[[], Awaitable[A]],
    catch: type[X] | tuple[type[X], ...] = (),
) -> Effect[A, X, Never]:
    """Call sync or async code. Exceptions of type ``catch`` become typed failures; others, defects.

    An awaitable result runs as an asyncio task; interrupting the fiber cancels the task and waits
    for it to finish.
    """

    def start() -> AnyEffect:
        try:
            out = thunk()
        except catch as e:
            return Failure(Cause.fail(e))
        if inspect.isawaitable(out):
            return _await(cast(Awaitable[A], out), catch)
        return Success(out)

    return Suspend(start)


def from_async[A](thunk: Callable[[], Awaitable[A]]) -> Effect[A, Never, Never]:
    return attempt(thunk)  # no exception is expected: all of them are defects


def _await(
    aw: Awaitable[Any], catch: type[BaseException] | tuple[type[BaseException], ...]
) -> AnyEffect:
    def register(resume: Resume[Any, Any, Any]) -> AnyEffect:
        task = asyncio.ensure_future(aw)

        def done(t: asyncio.Future[Any]) -> None:
            if t.cancelled():
                resume(Failure(Cause.interrupt()))
            elif (e := t.exception()) is not None:
                resume(Failure(Cause.fail(e) if isinstance(e, catch) else Cause.die(e)))
            else:
                resume(Success(t.result()))

        task.add_done_callback(done)

        def cancel_and_wait(resume_cancel: Resume[None, Never, Never]) -> None:
            task.cancel()
            task.add_done_callback(lambda _: resume_cancel(UNIT))

        return Async(cancel_and_wait)

    return Async(register)


# ================================================================================================
# Interruptibility
# ================================================================================================


def uninterruptible[A, E, R](eff: Effect[A, E, R]) -> Effect[A, E, R]:
    def go(fiber: AnyFiber) -> AnyEffect:
        if fiber.interruptible:
            fiber.interruptible = False
            fiber.stack.append(RESTORE_INTERRUPTIBLE)
        return eff

    return WithFiber(go)


def interruptible[A, E, R](eff: Effect[A, E, R]) -> Effect[A, E, R]:
    def go(fiber: AnyFiber) -> AnyEffect:
        if fiber.interruptible:
            return eff
        fiber.interruptible = True
        fiber.stack.append(RESTORE_UNINTERRUPTIBLE)
        if fiber.interrupted is not None:
            return Failure(fiber.take_interrupt())
        return eff

    return WithFiber(go)


@final
class Restore:
    """Passed to uninterruptible_mask's body: puts back the interruptibility from outside."""

    __slots__ = ("was",)

    def __init__(self, was: bool) -> None:
        self.was = was

    def __call__[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R]:
        return interruptible(eff) if self.was else eff


def uninterruptible_mask[A, E, R](body: Callable[[Restore], Effect[A, E, R]]) -> Effect[A, E, R]:
    """Run ``body`` uninterruptibly, except the parts it wraps in ``restore(...)``.

    The acquire/use/release shape: acquire and release can't be interrupted halfway; use can.
    """

    def go(fiber: AnyFiber) -> AnyEffect:
        was = fiber.interruptible
        if was:
            fiber.interruptible = False
            fiber.stack.append(RESTORE_INTERRUPTIBLE)
        return body(Restore(was))

    return WithFiber(go)


# ================================================================================================
# Context
# ================================================================================================


def update_context[A, E, R](
    eff: Effect[A, E, R], f: Callable[[ContextMap], ContextMap]
) -> Effect[A, E, R]:
    """Run ``eff`` with the context ``f(current)``; the current one comes back on every exit.

    The new context is a read-only copy of what f returns, so changing that mapping later can't
    reach the fiber. Swap and restore frame happen in one step, like ``uninterruptible``: no
    interrupt can land between them. If f raises, nothing changed and the raise is a defect.
    R is unchanged; context.provide is what removes services from R.
    Effect v4: internal/effect.ts:2269-2293 (updateContext).
    """

    def go(fiber: AnyFiber) -> AnyEffect:
        old = fiber.swap_context(MappingProxyType(dict(f(fiber.context))))
        fiber.stack.append(RestoreContext(old))
        return eff

    return WithFiber(go)


# ================================================================================================
# Error handling and finalization
#
# Two spellings. Methods (`eff.catch_all(h)`) give the handler the precise error type. Pipeables
# (`eff.pipe(catch_all(h))`) build the handler before they see `eff`, and Python can't infer a
# lambda's parameter from a later call, so their handler parameter is Any. catch_tag pipes cleanly
# because the class names the type.
# ================================================================================================


def _first_recoverable(cause: Cause[Any]) -> tuple[bool, Any]:
    """A typed failure is recoverable unless a defect is also present (v4 effect.ts:184-200)."""
    if cause.defects:
        return False, None
    for e in cause.failures:
        return True, e
    return False, None


def _catch_all(eff: AnyEffect, handler: Callable[[Any], AnyEffect]) -> AnyEffect:
    def on_err(cause: Cause[Any]) -> AnyEffect:
        ok, err = _first_recoverable(cause)
        return handler(err) if ok else Failure(cause)

    return OnFailure(eff, on_err)


@final
class catch_all[B, E2, R2]:
    """eff.pipe(catch_all(lambda e: ...)): recover from any typed failure. Defects pass through."""

    __slots__ = ("handler",)

    def __init__(self, handler: Callable[[Any], Effect[B, E2, R2]]) -> None:
        self.handler = handler

    def __call__[A, R](self, eff: Effect[A, Any, R]) -> Effect[A | B, E2, R | R2]:
        return _catch_all(eff, self.handler)


@final
class catch_cause[B, E2, R2]:
    """See the whole Cause (failures, defects). Interrupts can't be caught."""

    __slots__ = ("handler",)

    def __init__(self, handler: Callable[[Cause[Any]], Effect[B, E2, R2]]) -> None:
        self.handler = handler

    def __call__[A, R](self, eff: Effect[A, Any, R]) -> Effect[A | B, E2, R | R2]:
        return OnFailure(eff, self.handler)


@final
class catch_tag[X, B, E2, R2]:
    """eff.pipe(catch_tag(NotFound, lambda e: succeed(None))) removes NotFound from E."""

    __slots__ = ("cls", "handler")

    def __init__(self, cls: type[X], handler: Callable[[X], Effect[B, E2, R2]]) -> None:
        self.cls = cls
        self.handler = handler

    # exact match first: matching `X | E` against plain `X` would leave E unsolved
    @overload
    def __call__[A, R](self, eff: Effect[A, X, R]) -> Effect[A | B, E2, R | R2]: ...
    @overload
    def __call__[A, E, R](self, eff: Effect[A, X | E, R]) -> Effect[A | B, E | E2, R | R2]: ...
    def __call__(self, eff: AnyEffect) -> AnyEffect:
        def on_err(cause: Cause[Any]) -> AnyEffect:
            if not cause.defects:
                for e in cause.failures:
                    if isinstance(e, self.cls):
                        return self.handler(e)
            return Failure(cause)

        return OnFailure(eff, on_err)


@final
class on_exit[R2]:
    """Run ``f(exit)`` after the effect however it ends (success, failure, interrupt), masked."""

    __slots__ = ("f",)

    def __init__(self, f: Callable[[Exit[Any, Any]], Effect[Any, Never, R2]]) -> None:
        self.f = f

    def __call__[A, E, R](self, eff: Effect[A, E, R]) -> Effect[A, E, R | R2]:
        return OnExit(eff, self.f)


def ensuring[R2](finalizer: Effect[Any, Never, R2]) -> on_exit[R2]:
    return on_exit(lambda _exit: finalizer)


def on_error[R2](f: Callable[[Cause[Any]], Effect[Any, Never, R2]]) -> on_exit[R2]:
    """Run ``f(cause)`` if the effect fails (interruption included), masked. Effect v4:
    onError, internal/effect.ts:4411-4425."""

    def finalizer(exit_: Exit[Any, Any]) -> Effect[Any, Never, R2]:
        return f(exit_.cause) if isinstance(exit_, Failure) else succeed(None)

    return on_exit(finalizer)


def on_interrupt[R2](f: Callable[[frozenset[int]], Effect[Any, Never, R2]]) -> on_exit[R2]:
    """Run ``f(interruptors)`` if the effect's cause holds an interruption, masked. The set holds
    the interrupting fiber ids that are known. Effect v4: onInterrupt, internal/effect.ts:4485-4499
    (causeFilterInterruptors, :224)."""

    def on_cause(cause: Cause[Any]) -> Effect[Any, Never, R2]:
        if not cause.is_interrupted:
            return succeed(None)
        ids = frozenset(
            r.by for r in cause.reasons if isinstance(r, Interrupt) and r.by is not None
        )
        return f(ids)

    return on_error(on_cause)


# ================================================================================================
# Generators: Effect.gen
# ================================================================================================

type Do[A, E, R] = Generator[Effect[Any, E, R], Any, A]
"""Declared @gen body type (mypy needs it): ``def get(uid: int) -> Do[User, NotFound, Db]``"""


def gen[**P, A, E = Never, R = Never](
    f: Callable[P, Generator[Effect[Any, E, R], Any, A]],
) -> Callable[P, Effect[A, E, R]]:
    """Write effects as generator functions; pyright infers Effect[A, E, R] from the yields.

    @gen
    def greet(uid: str):
        users = yield from Users()
        user = yield from users.find(uid)
        return user.name
    """

    @wraps(f)
    def build(*args: P.args, **kwargs: P.kwargs) -> Effect[A, E, R]:
        return Suspend(lambda: GenFrame(f(*args, **kwargs)))

    return build
