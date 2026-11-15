"""Scope: a lifetime that runs its finalizers, newest first, when it closes.

    s = yield from Scope.make()
    yield from Scope.add_finalizer(s, release)       # runs when s closes
    yield from Scope.close(s, Success(None))         # finalizers run masked, get the Exit

    scoped(body)                                     # body reads its Scope with `scope`

A finalizer added to a closed scope runs at once with the exit it closed with. A child made by
``Scope.fork`` closes with its parent and leaves the parent when it closes first.

Effect v4: Scope.ts, internal/effect.ts:4024-4215 (scopeClose ... scoped). Upstream's
``Scope.use`` (scopeUse) is not ported: on this Tag ``Scope().use()`` is the service lookup
every Tag has, the same effect as ``scope``.

A ``"parallel"`` scope with several finalizers starts each one, newest first, in its own daemon
fiber that inherits the closing fiber's mask, then waits for all of them; empty and
one-finalizer closes take the common path, as upstream's do.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any, Final, Literal, Never, Protocol, final, overload

from .cause import Cause, Die, Fail, Interrupt
from .context import Tag, provide
from .core import (
    Effect,
    Exit,
    Failure,
    Success,
    exit_,
    gen,
    on_exit,
    succeed,
    suspend,
    sync,
    uninterruptible,
    update_context,
)
from .fiber import Fiber, await_all, fork_daemon

type Strategy = Literal["sequential", "parallel"]
type Finalizer = Callable[[Exit[Any, Any]], Effect[Any, Never, Never]]

VOID: Effect[None, Never, Never] = succeed(None)


# -- state (Scope.ts:103-195) ---------------------------------------------------------------------


@final
class Empty:
    """Open, no finalizers. Removing the last finalizer comes back here."""

    __slots__ = ()
    tag: Final = "Empty"


@final
class Open:
    """Open with finalizers: the first is kept inline, a second moves both into the dict."""

    __slots__ = ("finalizer", "finalizer_key", "finalizers")
    tag: Final = "Open"

    def __init__(
        self,
        finalizer_key: object | None,
        finalizer: Finalizer | None,
        finalizers: dict[object, Finalizer] | None,
    ) -> None:
        self.finalizer_key = finalizer_key
        self.finalizer = finalizer
        self.finalizers = finalizers


@final
class Closed:
    """Closing has started (finalizers may still be running); ``exit`` is what it closed with."""

    __slots__ = ("exit",)
    tag: Final = "Closed"

    def __init__(self, exit_: Exit[Any, Any]) -> None:
        self.exit: Final = exit_


type State = Empty | Open | Closed

_EMPTY: Final = Empty()  # constScopeEmpty


class ScopeService(Protocol):
    """What ``scope`` hands out (Scope.ts:47-52): no ``close``, that needs a Closeable."""

    @property
    def strategy(self) -> Strategy: ...
    @property
    def parent(self) -> ScopeService | None: ...

    state: State


@final
class Closeable:
    """A scope its maker can close (Scope.ts:77-79). Made by ``Scope.make``/``make_unsafe``/
    ``fork``/``fork_unsafe``."""

    __slots__ = ("_parent", "_strategy", "state")

    def __init__(self, strategy: Strategy, parent: ScopeService | None) -> None:
        self._strategy: Final = strategy
        self._parent: Final = parent
        self.state: State = _EMPTY

    @property
    def strategy(self) -> Strategy:
        return self._strategy

    @property
    def parent(self) -> ScopeService | None:
        return self._parent


# -- internals (internal/effect.ts) ---------------------------------------------------------------


def _add_finalizer_unsafe(scope: ScopeService, key: object, finalizer: Finalizer) -> None:
    """scopeAddFinalizerUnsafe, :4143-4159. A Closed scope is left alone."""
    state = scope.state
    if isinstance(state, Empty):
        scope.state = Open(key, finalizer, None)
    elif isinstance(state, Open):
        if state.finalizer is not None:
            state.finalizers = {state.finalizer_key: state.finalizer, key: finalizer}
            state.finalizer_key = None
            state.finalizer = None
        elif state.finalizers is not None:
            state.finalizers[key] = finalizer


def _remove_finalizer_unsafe(scope: ScopeService, key: object) -> None:
    """scopeRemoveFinalizerUnsafe, :4162-4176."""
    state = scope.state
    if not isinstance(state, Open):
        return
    if state.finalizer_key is key:
        scope.state = _EMPTY
    elif state.finalizers is not None:
        state.finalizers.pop(key, None)
        if not state.finalizers:
            scope.state = _EMPTY


def _run_finalizer(finalizer: Finalizer, exit_: Exit[Any, Any]) -> Effect[Any, Never, Never]:
    """runFinalizer, :4058-4067: a finalizer that raises becomes its defect."""
    try:
        return finalizer(exit_)
    except Exception as e:
        return Failure(Cause.die(e))


def _as_void_all(exits: list[Exit[Any, Never]]) -> Exit[None, Never]:
    """exitAsVoidAll, :2221-2234: every failure's reasons, in order, kept as they are (no
    merging of equal reasons, no defect election)."""
    reasons: list[Fail[Any] | Die | Interrupt] = []
    for ex in exits:
        if isinstance(ex, Failure):
            reasons.extend(ex.cause.reasons)
    return Failure(Cause(tuple(reasons))) if reasons else Success(None)


@gen
def _close_finalizers(
    strategy: Strategy, finalizers: dict[object, Finalizer], closed_with: Exit[Any, Any]
) -> Generator[Effect[Any, Never, Never], Any]:
    """scopeCloseFinalizers, :4077-4101: newest first. Sequential runs each to its Exit in turn;
    parallel starts each in a daemon fiber with the closing fiber's mask, then awaits them all.
    Upstream also skips a finalizer keyed by the closing fiber (forkIn); none exists here yet."""
    exits: list[Exit[Any, Never]] = []
    fibers: list[Fiber[Any, Never]] = []
    for finalizer in reversed(list(finalizers.values())):
        if strategy == "sequential":
            ex: Exit[Any, Never] = yield from exit_(_run_finalizer(finalizer, closed_with))
            exits.append(ex)
        else:
            fiber: Fiber[Any, Never] = yield from fork_daemon(
                _run_finalizer(finalizer, closed_with),
                start_immediately=True,
                uninterruptible="inherit",
            )
            fibers.append(fiber)
    if fibers:
        exits = yield from await_all(fibers)
    yield from _as_void_all(exits)


def _void_if_none(eff: Effect[Any, Never, Never] | None) -> Effect[Any, Never, Never]:
    return VOID if eff is None else eff


# -- the API (Scope.ts) ---------------------------------------------------------------------------


class Scope(Tag[ScopeService]):
    """The Scope service (scopeTag), and the Scope module's functions."""

    __slots__ = ()

    @staticmethod
    def make_unsafe(strategy: Strategy = "sequential") -> Closeable:
        """scopeMakeUnsafe, :4180-4192."""
        return Closeable(strategy, None)

    @staticmethod
    def make(strategy: Strategy = "sequential") -> Effect[Closeable, Never, Never]:
        return sync(lambda: Scope.make_unsafe(strategy))

    @staticmethod
    def add_finalizer_exit(scope: ScopeService, finalizer: Finalizer) -> Effect[None, Never, Never]:
        """scopeAddFinalizerExit, :4123-4134: on a closed scope the finalizer runs now, with the
        exit the scope closed with."""

        def add() -> Effect[None, Never, Never]:
            state = scope.state
            if isinstance(state, Closed):
                return finalizer(state.exit)
            _add_finalizer_unsafe(scope, object(), finalizer)
            return VOID

        return suspend(add)

    @staticmethod
    def add_finalizer(
        scope: ScopeService, finalizer: Effect[Any, Never, Never]
    ) -> Effect[None, Never, Never]:
        return Scope.add_finalizer_exit(scope, lambda _: finalizer)

    @staticmethod
    def close_unsafe(scope: Closeable, exit_: Exit[Any, Any]) -> Effect[None, Never, Never] | None:
        """scopeCloseUnsafe, :4040-4056. Marks the scope Closed and leaves its parent, then gives
        the finalizers' effect (None when there is nothing to run). A second close gives None."""
        state = scope.state
        if isinstance(state, Closed):
            return None
        scope.state = Closed(exit_)
        if scope.parent is not None:
            _remove_finalizer_unsafe(scope.parent, scope)
        if isinstance(state, Empty):
            return None
        if state.finalizer is not None:
            return _run_finalizer(state.finalizer, exit_)
        finalizers = state.finalizers
        assert finalizers is not None  # Open holds the inline finalizer or the dict
        if len(finalizers) == 1:
            return _run_finalizer(next(iter(finalizers.values())), exit_)
        return _close_finalizers(scope.strategy, finalizers, exit_)

    @staticmethod
    def close(scope: Closeable, exit_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        """scopeClose, :4033-4038: masked first, so the finalizers run uninterruptibly."""
        return uninterruptible(suspend(lambda: _void_if_none(Scope.close_unsafe(scope, exit_))))

    @staticmethod
    def fork_unsafe(scope: ScopeService, strategy: Strategy = "sequential") -> Closeable:
        """scopeForkUnsafe, :4110-4121: a child of a closed scope starts closed, with its exit."""
        child = Closeable(strategy, scope)
        if isinstance(scope.state, Closed):
            child.state = scope.state
            return child
        _add_finalizer_unsafe(scope, child, lambda ex: Scope.close(child, ex))
        return child

    @staticmethod
    def fork(
        scope: ScopeService, strategy: Strategy = "sequential"
    ) -> Effect[Closeable, Never, Never]:
        return sync(lambda: Scope.fork_unsafe(scope, strategy))

    @staticmethod
    def provide(scope: ScopeService) -> provide[Scope]:
        """provideScope: run an effect with this scope as its Scope service."""
        return provide(Scope.context(scope))


scope: Effect[ScopeService, Never, Scope] = Scope().use()
"""Effect.scope: the Scope service in context (internal/effect.ts:4192)."""


# exact match first, as context.provide: matching `Scope | R` against `Scope` leaves R unsolved
@overload
def scoped[A, E](eff: Effect[A, E, Scope]) -> Effect[A, E, Never]: ...
@overload
def scoped[A, E, R](eff: Effect[A, E, Scope | R]) -> Effect[A, E, R]: ...
def scoped(eff: Effect[Any, Any, Any]) -> Effect[Any, Any, Any]:
    """Run ``eff`` with a new Scope, closed with ``eff``'s exit when it ends.

    Effect v4 scoped (:4204-4215) installs the Scope and its exit handler in one step; here the
    handler (``on_exit``) is in place before the context changes, and the context goes back
    (update_context's frame) before the scope closes, so finalizers run without the Scope.
    """

    def install() -> Effect[Any, Any, Any]:
        s = Scope.make_unsafe()
        return on_exit(lambda ex: _void_if_none(Scope.close_unsafe(s, ex)))(
            update_context(eff, lambda old: {**old, Scope: s})
        )

    return suspend(install)
