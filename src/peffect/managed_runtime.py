"""ManagedRuntime: build a Layer once, run effects with its services, dispose it once.

Effect v4 289a53f7, packages/effect/src/ManagedRuntime.ts:325-474 (``make``). Line references
below are to that file unless they name another one.

    rt = ManagedRuntime.make(Users.layer)
    users = await rt.run(Users().use())      # the first request builds the layer
    await rt.dispose()                       # closes the layer and interrupts managed fibers

State, all owned by the runtime:

- ``scope`` (sequential) with two children forked at construction: ``_layer_scope`` holds the
  layer's resources, ``_fiber_scope`` (parallel) holds one finalizer per running managed fiber,
  so closing ``scope`` interrupts and awaits those fibers, then releases the layer (:332-334).
- ``_managed`` / ``_disposing``: WeakSets of fibers (:338-339). A managed fiber (or one of its
  descendants) that disposes the runtime returns at once instead of awaiting its own close;
  a disposing fiber that starts a request is not added to ``_fiber_scope`` (:340-346).
- ``_build_fiber``: the one layer build, started by the first request (:361-383).
- ``_disposed``: set by the one disposer that claims disposal; every other disposer awaits it.

Adaptations: Promise runners are async methods over ``await_exit`` (runPromiseExitWith,
internal/effect.ts:5854-5866); there is no sync or callback runner and no AbortSignal, so
``run_sync*``, ``run_callback`` and ``signal`` are not ported. ``die`` takes an exception, so the
disposal defect is ``RuntimeError("ManagedRuntime disposed")`` where upstream dies with the bare
string (:418). ``Symbol.asyncDispose`` is ``async with``.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Any, Never, Self, final

from .context import Context, Reference, provide, run_fork_with
from .core import (
    Effect,
    Exit,
    Success,
    WithFiber,
    die,
    ensuring,
    succeed,
    sync,
    uninterruptible_mask,
)
from .deferred import Deferred
from .fiber import (
    DEFAULT_SCHEDULER,
    Fiber,
    SchedulerService,
    await_exit,
    run,
    run_exit,
    run_fork,
)
from .layer import Layer, MemoMap
from .scheduler import Scheduler
from .scope import Closeable, Scope, run_in

_VOID: Effect[None, Never, Never] = succeed(None)
_DISPOSED = "ManagedRuntime disposed"

type _Hook = Callable[[Fiber[Any, Any]], object]  # RunOptions.onFiberStart
type _Restore = Callable[[Effect[None, Never, Never]], Effect[None, Never, Never]]


class _DisposingRuntimes(  # :250-262, not exported upstream either
    Reference[frozenset["ManagedRuntime[Any, Any]"]],
    key="effect/ManagedRuntime/DisposingRuntimes",
):
    """The runtimes whose disposal the current fiber is running. A disposal adds its runtime to
    the set it inherits, so a finalizer that disposes another runtime still belongs to the outer
    disposals. Not provided to the layer build: a layer shared through a MemoMap keeps the build
    context of the runtime that built it, which may not be the one disposing it."""

    __slots__ = ()

    @classmethod
    def default(cls) -> frozenset[ManagedRuntime[Any, Any]]:
        return frozenset()


@final
class ManagedRuntime[R, ER]:
    """A Layer built on first use; runs effects needing R, and fails them with ER if the build
    fails. Contravariant in R and covariant in ER, as upstream ``ManagedRuntime<in R, out ER>``."""

    __slots__ = (
        "_build_fiber",
        "_cached_context",
        "_context_effect",
        "_disposed",
        "_disposing",
        "_fiber_scope",
        "_layer",
        "_layer_scope",
        "_managed",
        "dispose_effect",
        "memo_map",
        "scope",
    )

    def __init__(self, layer: Layer[R, ER, Never], memo_map: MemoMap | None) -> None:
        self.memo_map: MemoMap = memo_map if memo_map is not None else Layer.make_memo_map_unsafe()
        self.scope: Closeable = Scope.make_unsafe("sequential")
        self._layer_scope = Scope.fork_unsafe(self.scope, "sequential")
        self._fiber_scope = Scope.fork_unsafe(self.scope, "parallel")
        # Fibers started by the runtime, the build included, are interrupted and awaited by its
        # disposal, so they and their children must not wait for it (:335-339).
        self._managed: weakref.WeakSet[Fiber[Any, Any]] = weakref.WeakSet()
        self._disposing: weakref.WeakSet[Fiber[Any, Any]] = weakref.WeakSet()
        self._layer = layer
        self._build_fiber: Fiber[Context[R], ER] | None = None
        self._disposed: Deferred[None, Never] | None = None
        self._cached_context: Context[R] | None = None
        self._context_effect: Effect[Context[R], ER, Never] = WithFiber(
            lambda fiber: self._request_context(fiber)
        )
        # The decision, the mask and the claim are one step: uninterruptible_mask calls its body
        # inside the op that masks (core.py uninterruptible_mask), as withFiber +
        # fiberEnterUninterruptibleUnsafe do upstream (:401, :410-411). An interrupt that lands
        # before that op leaves the runtime unclaimed.
        self.dispose_effect: Effect[None, Never, Never] = WithFiber(
            lambda fiber: uninterruptible_mask(lambda restore: self._dispose(fiber, restore))
        )

    @staticmethod
    def make[R2, ER2](
        layer: Layer[R2, ER2, Never], *, memo_map: MemoMap | None = None
    ) -> ManagedRuntime[R2, ER2]:
        """make, :325-331. Nothing is built until the first request."""
        return ManagedRuntime(layer, memo_map)

    # -- read-only views (upstream fields; read-only here so R stays contravariant) -------------

    @property
    def cached_context(self) -> Context[R] | None:
        """The built context once the build succeeded, until disposal claims (:364-369, :419)."""
        return self._cached_context

    @property
    def context_effect(self) -> Effect[Context[R], ER, Never]:
        """Join (or start) the build; after disposal, a defect (:361-383, :418)."""
        return self._context_effect

    # -- start hooks (:340-358, :372-378) ---------------------------------------------------------

    def _on_fiber_start(self, fiber: Fiber[Any, Any]) -> None:
        self._managed.add(fiber)
        if fiber in self._disposing:
            return
        run_in(fiber, self._fiber_scope)

    def _on_build_start(self, fiber: Fiber[Any, Any]) -> None:
        # Unlike requests, the build joins fiberScope even if it disposed the runtime, so
        # disposal still interrupts it (:373-377).
        self._managed.add(fiber)
        run_in(fiber, self._fiber_scope)

    def _merge(self, hook: _Hook | None) -> _Hook:
        """mergeRunOptions, :347-358: the runtime's hook first, then the caller's."""
        if hook is None:
            return self._on_fiber_start
        user = hook

        def both(fiber: Fiber[Any, Any]) -> None:
            self._on_fiber_start(fiber)
            user(fiber)

        return both

    # -- contextEffect (:361-383) -----------------------------------------------------------------

    def _request_context(self, fiber: Fiber[Any, Any]) -> Effect[Context[R], ER, Never]:
        build = self._build_fiber
        if build is None:
            # A local function, not a method: a method taking Context[R] would make pyright
            # infer R invariant (private attributes are left out of variance inference).
            def cache(context: Context[R]) -> Context[R]:  # :364-369
                self._cached_context = context
                return context

            scheduler: SchedulerService = fiber.context.get(Scheduler.key, DEFAULT_SCHEDULER)
            built = Layer.build_with_memo_map(self._layer, self.memo_map, self._layer_scope)
            build = self._build_fiber = run_fork(
                built.flat_map(lambda context: sync(lambda: cache(context))),
                scheduler=scheduler,  # fiber.cache.scheduler, :372
                on_fiber_start=self._on_build_start,
            )
        return build.await_().flat_map(_flatten)

    # -- disposeEffect (:401-424) -----------------------------------------------------------------

    def _dispose(self, fiber: Fiber[Any, Any], restore: _Restore) -> Effect[None, Never, Never]:
        inherited: frozenset[ManagedRuntime[Any, Any]] = fiber.context.get(
            _DisposingRuntimes.key, _DisposingRuntimes.default()
        )
        done = self._disposed
        if done is not None:
            if self in inherited:  # a finalizer of this disposal (:403)
                return _VOID
            current: Fiber[Any, Any] | None = fiber
            while current is not None:  # a managed fiber, which the close awaits (:404-406)
                if current in self._managed:
                    return _VOID
                current = current.parent
            return restore(done.await_())  # waiters stay interruptible (:407)
        # Once `disposed` is set the close must run, or later calls would wait forever (:409).
        done = self._disposed = Deferred[None, Never].make_unsafe()
        # Closing fiberScope interrupts and awaits every managed fiber. Exempt the disposing
        # fiber and its ancestors, which would otherwise await themselves (:412-417).
        current = fiber
        while current is not None:
            self._disposing.add(current)
            Scope.remove_finalizer_unsafe(self._fiber_scope, current)
            current = current.parent
        self._context_effect = die(RuntimeError(_DISPOSED))  # :418
        self._cached_context = None  # :419
        disposing = _DisposingRuntimes.context(inherited | {self})
        return provide(disposing)(Scope.close(self.scope, _EXIT_VOID)).pipe(
            ensuring(done.done(_EXIT_VOID))  # :420-423
        )

    # -- runners (:390-400, :425-474) -------------------------------------------------------------

    def _provided[A, E](self, effect: Effect[A, E, R]) -> Effect[A, E | ER, Never]:
        """provide, :466-474. Reads ``context_effect`` now, as upstream reads it at call time."""
        return self._context_effect.flat_map(lambda context: provide(context)(effect))

    def run_fork[A, E](
        self,
        effect: Effect[A, E, R],
        *,
        scheduler: SchedulerService | None = None,
        uninterruptible: bool = False,
        on_fiber_start: _Hook | None = None,
    ) -> Fiber[A, E | ER]:
        """runFork, :425-429: over the cached context once built, else through the build."""
        hook = self._merge(on_fiber_start)
        cached = self._cached_context
        if cached is None:
            return run_fork(
                self._provided(effect),
                scheduler=scheduler,
                uninterruptible=uninterruptible,
                on_fiber_start=hook,
            )
        return run_fork_with(cached)(
            effect, scheduler=scheduler, uninterruptible=uninterruptible, on_fiber_start=hook
        )

    async def run_exit[A, E](
        self,
        effect: Effect[A, E, R],
        *,
        scheduler: SchedulerService | None = None,
        uninterruptible: bool = False,
        on_fiber_start: _Hook | None = None,
    ) -> Exit[A, E | ER]:
        """runPromiseExit, :450-454: the request fiber's own Exit, through an observer.
        Cancelling the awaiting task interrupts that fiber (``await_exit``)."""
        fiber = self.run_fork(
            effect,
            scheduler=scheduler,
            uninterruptible=uninterruptible,
            on_fiber_start=on_fiber_start,
        )
        return await await_exit(fiber)

    async def run[A, E](
        self,
        effect: Effect[A, E, R],
        *,
        scheduler: SchedulerService | None = None,
        uninterruptible: bool = False,
        on_fiber_start: _Hook | None = None,
    ) -> A:
        """runPromise, :455-461: the value, or the squashed cause raised."""
        exit_ = await self.run_exit(
            effect,
            scheduler=scheduler,
            uninterruptible=uninterruptible,
            on_fiber_start=on_fiber_start,
        )
        if isinstance(exit_, Success):
            return exit_.value
        raise exit_.cause.squash()

    async def context(self) -> Context[R]:
        """context, :390-394: the cached context, or ``context_effect`` run as an unmanaged
        root (no runtime hook)."""
        cached = self._cached_context
        if cached is not None:
            return cached
        exit_ = await run_exit(self._context_effect)
        if isinstance(exit_, Success):
            return exit_.value
        raise exit_.cause.squash()

    async def dispose(self) -> None:
        """dispose, :395-397: ``dispose_effect`` as an unmanaged root."""
        await run(self.dispose_effect)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        """Symbol.asyncDispose, :398-400."""
        await self.dispose()


_EXIT_VOID: Exit[None, Never] = Success(None)


def _flatten[A, E](exit_: Exit[A, E]) -> Effect[A, E, Never]:
    """Effect.flatten over Fiber.await: continue as the build ended (:382)."""
    return exit_


def is_managed_runtime(value: object) -> bool:
    """isManagedRuntime, :51."""
    return isinstance(value, ManagedRuntime)


__all__ = ["ManagedRuntime", "is_managed_runtime"]
