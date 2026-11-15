"""Tags (service keys), Layers (service constructors), and Scope (resource lifetimes).

class UserStore(Protocol):            # the shape
    def find(self, uid: str) -> Effect[User, UserNotFound, Never]: ...

class Users(Tag[UserStore]): ...       # the key; nominal, so two tags can share a shape

users = yield from Users()             # inside @gen: Effect[UserStore, Never, Users]

UsersLive = Users.effect(make_users())        # Layer[Users, E, <make_users's R>]
app = UsersLive.provide_merge(DbLive)         # feed one layer's needs from another
program.pipe(provide(app))                    # removes Users | Db from R
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, Generic, Never, Self, TypeVar, cast, final, overload

from pfx.core import Effect, _Async, _interpret, _Need, _Provide  # pyright: ignore[reportPrivateUsage]


class Tag[S]:
    """Subclass to declare a service key: ``class Db(Tag[DbShape]): ...``.

    Instances are cheap and stateless; ``yield from Db()`` asks the runtime for the service.
    """

    def __iter__(self) -> Generator[Effect[S, Never, Self], Any, S]:
        return cast(S, (yield self.use()))

    def use(self) -> Effect[S, Never, Self]:
        return Effect(_Need(type(self)))

    # Layer constructors live on the tag so S is already bound: Users.succeed(impl) checks impl.

    @classmethod
    def succeed(cls, service: S) -> Layer[Self, Never, Never]:
        return Layer(lambda _ctx, _scope: _const({cls: service}))

    @classmethod
    def effect[E, R](cls, make: Effect[S, E, R]) -> Layer[Self, E, R]:
        """Service built by an effect; ``make`` may ``yield from`` other tags (they become RIn)."""
        return Layer(lambda _ctx, _scope: make.map(lambda s: {cls: s}))

    @classmethod
    def scoped(
        cls, resource: Callable[[], AbstractAsyncContextManager[S]]
    ) -> Layer[Self, Never, Never]:
        """Acquire on build, release when the run's Scope closes (Effect's Layer.scoped)."""

        def build(
            _ctx: dict[type, object], scope: Scope
        ) -> Effect[dict[type, object], Never, Never]:
            async def acquire() -> dict[type, object]:
                return {cls: await scope.enter(resource())}

            return Effect(_Async(acquire))

        return Layer(build)


class Scope:
    """Owns finalizers for scoped layers; closes them in reverse acquisition order."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self.memo: dict[int, dict[type, object]] = {}

    async def __aenter__(self) -> Self:
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def enter[T](self, cm: AbstractAsyncContextManager[T]) -> T:
        return await self._stack.enter_async_context(cm)


ROut = TypeVar("ROut")  # invariant: Layer[Users] must not pass where Layer[Users | Db] is needed
E = TypeVar("E", covariant=True)
RIn = TypeVar("RIn", covariant=True)


class Layer(Generic[ROut, E, RIn]):
    """Builds services ROut, may fail with E, needs RIn. Memoized per run (Effect's default)."""

    __slots__ = ("_build",)

    def __init__(
        self, build: Callable[[dict[type, object], Scope], Effect[dict[type, object], E, RIn]]
    ) -> None:
        self._build = build

    # -- composition ----------------------------------------------------------------------------

    def merge[RO2, E2, RI2](
        self, other: Layer[RO2, E2, RI2]
    ) -> Layer[ROut | RO2, E | E2, RIn | RI2]:
        """Both layers, side by side (Layer.merge)."""

        def build(ctx: dict[type, object], scope: Scope) -> Effect[dict[type, object], Any, Any]:
            return self._built(ctx, scope).and_then(
                lambda a: other._built(ctx, scope).map(lambda b: {**a, **b})
            )

        return Layer(build)

    @overload
    def provide[RO2, E2, RI2](
        self: Layer[ROut, E, RO2], dep: Layer[RO2, E2, RI2]
    ) -> Layer[ROut, E | E2, RI2]: ...
    @overload
    def provide[RO2, E2, RI2, Rest](
        self: Layer[ROut, E, RO2 | Rest], dep: Layer[RO2, E2, RI2]
    ) -> Layer[ROut, E | E2, Rest | RI2]: ...
    def provide(self, dep: Layer[Any, Any, Any]) -> Layer[Any, Any, Any]:
        """Feed ``dep`` into this layer's requirements; output only this layer (Layer.provide)."""

        def build(ctx: dict[type, object], scope: Scope) -> Effect[dict[type, object], Any, Any]:
            return dep._built(ctx, scope).and_then(lambda d: self._built({**ctx, **d}, scope))

        return Layer(build)

    @overload
    def provide_merge[RO2, E2, RI2](
        self: Layer[ROut, E, RO2], dep: Layer[RO2, E2, RI2]
    ) -> Layer[ROut | RO2, E | E2, RI2]: ...
    @overload
    def provide_merge[RO2, E2, RI2, Rest](
        self: Layer[ROut, E, RO2 | Rest], dep: Layer[RO2, E2, RI2]
    ) -> Layer[ROut | RO2, E | E2, Rest | RI2]: ...
    def provide_merge(self, dep: Layer[Any, Any, Any]) -> Layer[Any, Any, Any]:
        """Like provide, but also expose ``dep``'s services (Layer.provideMerge)."""

        def build(ctx: dict[type, object], scope: Scope) -> Effect[dict[type, object], Any, Any]:
            return dep._built(ctx, scope).and_then(
                lambda d: self._built({**ctx, **d}, scope).map(lambda s: {**d, **s})
            )

        return Layer(build)

    # -- runtime --------------------------------------------------------------------------------

    def _built(self, ctx: dict[type, object], scope: Scope) -> Effect[dict[type, object], E, RIn]:
        """Memoized: the same Layer object builds once per run, however many times it's used."""
        key = id(self)

        async def go() -> dict[type, object]:
            if key not in scope.memo:
                scope.memo[key] = await _run_inner(self._build(ctx, scope), ctx, scope)
            return scope.memo[key]

        return Effect(_Async(go))

    async def build(self, ctx: dict[type, object], scope: Scope) -> dict[type, object]:
        return cast(dict[type, object], await _run_inner(self._built(ctx, scope), ctx, scope))


async def _run_inner(eff: Effect[Any, Any, Any], ctx: dict[type, object], scope: Scope) -> Any:
    return await _interpret(eff, ctx, scope)


def _const(v: dict[type, object]) -> Effect[dict[type, object], Never, Never]:
    from pfx.core import succeed

    return succeed(v)


# -- providing ----------------------------------------------------------------------------------


@final
class provide[RO, LE, LI]:
    """eff.pipe(provide(layer)): matching ``RO | R`` against the effect's R leaves R = the rest."""

    __slots__ = ("layer",)

    def __init__(self, layer: Layer[RO, LE, LI]) -> None:
        self.layer = layer

    @overload  # program needs a subset of what the layer provides (covariant R) -> R = LI
    def __call__[A, X](self, eff: Effect[A, X, RO]) -> Effect[A, X | LE, LI]: ...
    @overload  # partial: whatever the layer doesn't cover stays in R
    def __call__[A, X, R](self, eff: Effect[A, X, RO | R]) -> Effect[A, X | LE, R | LI]: ...
    def __call__(self, eff: Effect[Any, Any, Any]) -> Effect[Any, Any, Any]:
        return Effect(_Provide(eff, self.layer))
