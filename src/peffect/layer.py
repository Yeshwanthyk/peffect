"""Layer: a recipe that builds a Context, shared across one build by identity.

    Users = Layer.effect(UsersTag, acquire_release(open_repo, close_repo))
    App = Users.pipe(Layer.provide(Db)).pipe(Layer.merge(Log))
    yield from provide_layer(App)(program)      # build, run program, release

A ``Layer[ROut, E, RIn]`` builds services for the tags in ROut, may fail with E and needs RIn.
Building one always goes through a MemoMap: requesting a layer the map already holds reuses its
Context, and every requester's scope keeps the shared resources alive until the last one
closes. Scope in an effect's R is the layer's own lifetime, so the constructors remove it.

Effect v4: Layer.ts:240-420 (MemoMap, fromBuild, fromBuildMemo), :600-781 (CurrentMemoMap, build),
mergeAll, provide, and internal/layer.ts:8-22 (Effect.provide with a layer). Not ported:
Layer.fresh, suspend, recovery and tracing; ``merge_all`` is typed for 1 to 4 layers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import reduce
from types import MappingProxyType
from typing import Any, Final, Never, Protocol, final, overload

from .context import Context, Tag, provide
from .core import ContextMap, Effect, Exit, Failure, WithFiber, succeed, suspend
from .deferred import Deferred
from .fiber import for_each
from .scope import Closeable, Scope, ScopeService

type _F[X, Y] = Callable[[X], Y]
type _Build[ROut, E, RIn] = Callable[[MemoMap, ScopeService], Effect[Context[ROut], E, RIn]]

VOID: Effect[None, Never, Never] = succeed(None)


@final
class _Token:
    __slots__ = ()


_TOKEN = _Token()


class _TagClass[S, T](Protocol):
    """A tag class such as ``Users``: ``Users.context(service)`` is a ``Context[Users]``."""

    def context(self, service: S, /) -> Context[T]: ...


@final
class Layer[ROut, E = Never, RIn = Never]:
    """Built only by the constructors below; the builder cannot be replaced after."""

    __slots__ = ("_builder",)

    def __init__(self, builder: _Build[ROut, E, RIn], token: _Token) -> None:
        if token is not _TOKEN:
            raise TypeError("build a Layer with Layer.effect, Layer.succeed, ..., not Layer(...)")
        self._builder: Final = builder

    # pipe(f1, ..., fn): 0-20 steps, left to right, like Effect.pipe (Layer is Pipeable,
    # Pipeable.ts:46-526); nest a second pipe for more.
    @overload
    def pipe(self, /) -> Layer[ROut, E, RIn]: ...
    @overload
    def pipe[T1](self, f1: _F[Layer[ROut, E, RIn], T1], /) -> T1: ...
    @overload
    def pipe[T1, T2](self, f1: _F[Layer[ROut, E, RIn], T1], f2: _F[T1, T2], /) -> T2: ...
    @overload
    def pipe[T1, T2, T3](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        /,
    ) -> T3: ...
    @overload
    def pipe[T1, T2, T3, T4](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        /,
    ) -> T4: ...
    @overload
    def pipe[T1, T2, T3, T4, T5](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        /,
    ) -> T5: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        /,
    ) -> T6: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        /,
    ) -> T7: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        /,
    ) -> T8: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        /,
    ) -> T9: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        /,
    ) -> T10: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        /,
    ) -> T11: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        /,
    ) -> T12: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        /,
    ) -> T13: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        /,
    ) -> T14: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        f15: _F[T14, T15],
        /,
    ) -> T15: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        f15: _F[T14, T15],
        f16: _F[T15, T16],
        /,
    ) -> T16: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        f15: _F[T14, T15],
        f16: _F[T15, T16],
        f17: _F[T16, T17],
        /,
    ) -> T17: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17, T18](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        f15: _F[T14, T15],
        f16: _F[T15, T16],
        f17: _F[T16, T17],
        f18: _F[T17, T18],
        /,
    ) -> T18: ...
    @overload
    def pipe[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17, T18, T19](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        f15: _F[T14, T15],
        f16: _F[T15, T16],
        f17: _F[T16, T17],
        f18: _F[T17, T18],
        f19: _F[T18, T19],
        /,
    ) -> T19: ...
    @overload
    def pipe[
        T1,
        T2,
        T3,
        T4,
        T5,
        T6,
        T7,
        T8,
        T9,
        T10,
        T11,
        T12,
        T13,
        T14,
        T15,
        T16,
        T17,
        T18,
        T19,
        T20,
    ](
        self,
        f1: _F[Layer[ROut, E, RIn], T1],
        f2: _F[T1, T2],
        f3: _F[T2, T3],
        f4: _F[T3, T4],
        f5: _F[T4, T5],
        f6: _F[T5, T6],
        f7: _F[T6, T7],
        f8: _F[T7, T8],
        f9: _F[T8, T9],
        f10: _F[T9, T10],
        f11: _F[T10, T11],
        f12: _F[T11, T12],
        f13: _F[T12, T13],
        f14: _F[T13, T14],
        f15: _F[T14, T15],
        f16: _F[T15, T16],
        f17: _F[T16, T17],
        f18: _F[T17, T18],
        f19: _F[T18, T19],
        f20: _F[T19, T20],
        /,
    ) -> T20: ...
    def pipe(self, /, *fs: _F[Any, Any]) -> Any:
        """Apply each function to the previous result, eagerly (pipeArguments, Pipeable.ts:567)."""
        out: Any = self
        for f in fs:
            out = f(out)
        return out

    # -- constructors (Layer.ts:360-372, :1031-1180) -------------------------------------------

    @staticmethod
    def from_build[O, E2, R](build: _Build[O, E2, R]) -> Layer[O, E2, R]:
        """Not memoized. The build gets a scope forked from the caller's, closed if it fails."""

        def run(memo_map: MemoMap, scope: ScopeService) -> Effect[Context[O], E2, R]:
            layer_scope = Scope.fork_unsafe(scope)

            def close_on_failure(exit_: Exit[Context[O], E2]) -> Effect[None, Never, Never]:
                return Scope.close(layer_scope, exit_) if isinstance(exit_, Failure) else VOID

            return build(memo_map, layer_scope).on_exit(close_on_failure)

        return Layer(run, _TOKEN)

    @staticmethod
    def _from_build_memo[O, E2, R](build: _Build[O, E2, R]) -> Layer[O, E2, R]:
        """fromBuildMemo: the layer itself is the memo key."""

        def memoized(memo_map: MemoMap, scope: ScopeService) -> Effect[Context[O], E2, R]:
            return memo_map.get_or_else_memoize(layer, scope, build)

        layer = Layer.from_build(memoized)
        return layer

    @staticmethod
    def succeed[S, T](tag: _TagClass[S, T], service: S) -> Layer[T]:
        """Not memoized; the Context is made once, here."""
        ctx = tag.context(service)
        return Layer(lambda _m, _s: succeed(ctx), _TOKEN)

    @overload
    @staticmethod
    def effect_context[T, E2](eff: Effect[Context[T], E2, Scope]) -> Layer[T, E2]: ...
    @overload
    @staticmethod
    def effect_context[T, E2, R](
        eff: Effect[Context[T], E2, Scope | R],
    ) -> Layer[T, E2, R]: ...
    @staticmethod
    def effect_context(eff: Effect[Context[Any], Any, Any]) -> Layer[Any, Any, Any]:
        return Layer._from_build_memo(lambda _m, scope: Scope.provide(scope)(eff))

    @overload
    @staticmethod
    def effect[S, T, E2](tag: _TagClass[S, T], eff: Effect[S, E2, Scope]) -> Layer[T, E2]: ...
    @overload
    @staticmethod
    def effect[S, T, E2, R](
        tag: _TagClass[S, T], eff: Effect[S, E2, Scope | R]
    ) -> Layer[T, E2, R]: ...
    @staticmethod
    def effect(tag: _TagClass[Any, Any], eff: Effect[Any, Any, Any]) -> Layer[Any, Any, Any]:
        return Layer.effect_context(eff.map(tag.context))

    @overload
    @staticmethod
    def effect_discard[E2](eff: Effect[object, E2, Scope]) -> Layer[Never, E2]: ...
    @overload
    @staticmethod
    def effect_discard[E2, R](eff: Effect[object, E2, Scope | R]) -> Layer[Never, E2, R]: ...
    @staticmethod
    def effect_discard(eff: Effect[object, Any, Any]) -> Layer[Any, Any, Any]:
        return Layer.effect_context(eff.as_(Context.empty()))

    # -- composition (Layer.ts mergeAll, merge, provide, provideWith) ----------------------------

    # Pyright loses an all-Never union across free type parameters (resources.py:70): for
    # each arity, overloads for all-Never E and R channels come before the general one.
    @overload
    @staticmethod
    def merge_all[O1, E1, R1](a: Layer[O1, E1, R1], /) -> Layer[O1, E1, R1]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2](
        a: Layer[O1, Never, Never], b: Layer[O2, Never, Never], /
    ) -> Layer[O1 | O2, Never, Never]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, R1, R2](
        a: Layer[O1, Never, R1], b: Layer[O2, Never, R2], /
    ) -> Layer[O1 | O2, Never, R1 | R2]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, E1, E2](
        a: Layer[O1, E1, Never], b: Layer[O2, E2, Never], /
    ) -> Layer[O1 | O2, E1 | E2, Never]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, E1, E2, R1, R2](
        a: Layer[O1, E1, R1], b: Layer[O2, E2, R2], /
    ) -> Layer[O1 | O2, E1 | E2, R1 | R2]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3](
        a: Layer[O1, Never, Never], b: Layer[O2, Never, Never], c: Layer[O3, Never, Never], /
    ) -> Layer[O1 | O2 | O3, Never, Never]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, R1, R2, R3](
        a: Layer[O1, Never, R1], b: Layer[O2, Never, R2], c: Layer[O3, Never, R3], /
    ) -> Layer[O1 | O2 | O3, Never, R1 | R2 | R3]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, E1, E2, E3](
        a: Layer[O1, E1, Never], b: Layer[O2, E2, Never], c: Layer[O3, E3, Never], /
    ) -> Layer[O1 | O2 | O3, E1 | E2 | E3, Never]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, E1, E2, E3, R1, R2, R3](
        a: Layer[O1, E1, R1], b: Layer[O2, E2, R2], c: Layer[O3, E3, R3], /
    ) -> Layer[O1 | O2 | O3, E1 | E2 | E3, R1 | R2 | R3]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, O4](
        a: Layer[O1, Never, Never],
        b: Layer[O2, Never, Never],
        c: Layer[O3, Never, Never],
        d: Layer[O4, Never, Never],
        /,
    ) -> Layer[O1 | O2 | O3 | O4, Never, Never]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, O4, R1, R2, R3, R4](
        a: Layer[O1, Never, R1],
        b: Layer[O2, Never, R2],
        c: Layer[O3, Never, R3],
        d: Layer[O4, Never, R4],
        /,
    ) -> Layer[O1 | O2 | O3 | O4, Never, R1 | R2 | R3 | R4]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, O4, E1, E2, E3, E4](
        a: Layer[O1, E1, Never],
        b: Layer[O2, E2, Never],
        c: Layer[O3, E3, Never],
        d: Layer[O4, E4, Never],
        /,
    ) -> Layer[O1 | O2 | O3 | O4, E1 | E2 | E3 | E4, Never]: ...
    @overload
    @staticmethod
    def merge_all[O1, O2, O3, O4, E1, E2, E3, E4, R1, R2, R3, R4](
        a: Layer[O1, E1, R1], b: Layer[O2, E2, R2], c: Layer[O3, E3, R3], d: Layer[O4, E4, R4], /
    ) -> Layer[O1 | O2 | O3 | O4, E1 | E2 | E3 | E4, R1 | R2 | R3 | R4]: ...
    @staticmethod
    def merge_all(*layers: Layer[Any, Any, Any]) -> Layer[Any, Any, Any]:
        """Builds every layer at once, each in a sequential child of one parallel scope."""

        def build(memo_map: MemoMap, scope: ScopeService) -> Effect[Context[Any], Any, Any]:
            parent = Scope.fork_unsafe(scope, "parallel")
            return for_each(
                layers,
                lambda layer, _i: layer._builder(memo_map, Scope.fork_unsafe(parent, "sequential")),
                concurrency=len(layers),
            ).map(lambda ctxs: reduce(lambda a, b: a | b, ctxs, Context.empty()))

        return Layer._from_build_memo(build)

    @staticmethod
    def merge[O2, E2, R2](that: Layer[O2, E2, R2]) -> _Merge[O2, E2, R2]:
        """``self.pipe(Layer.merge(that))`` is ``Layer.merge_all(self, that)``."""
        return _Merge(that)

    @staticmethod
    def provide[O, E2, R2](that: Layer[O, E2, R2]) -> _Provide[O, E2, R2]:
        """``self.pipe(Layer.provide(that))``: build ``that`` first and give its services to
        ``self``; the result provides only what ``self`` does."""

        def apply(self: Layer[Any, Any, Any]) -> Layer[Any, Any, Any]:
            def build(memo_map: MemoMap, scope: ScopeService) -> Effect[Context[Any], Any, Any]:
                return that._builder(memo_map, scope).flat_map(
                    lambda ctx: provide(ctx)(self._builder(memo_map, scope))
                )

            return Layer._from_build_memo(build)

        return _Provide(that, apply)

    # -- building (Layer.ts:670-781) -------------------------------------------------------------

    @staticmethod
    def build_with_memo_map[O, E2, R](
        layer: Layer[O, E2, R], memo_map: MemoMap, scope: ScopeService
    ) -> Effect[Context[O], E2, R]:
        """While building, and in the built Context, CurrentMemoMap is ``memo_map``."""
        current = CurrentMemoMap.context(memo_map)
        built = suspend(lambda: layer._builder(memo_map, scope)).map(lambda ctx: ctx | current)
        return provide(current)(built)

    @staticmethod
    def build_with_scope[O, E2, R](
        layer: Layer[O, E2, R], scope: ScopeService
    ) -> Effect[Context[O], E2, R]:
        return WithFiber(
            lambda fiber: Layer.build_with_memo_map(
                layer, CurrentMemoMap.fork_or_create(fiber.context), scope
            )
        )

    @staticmethod
    def build[O, E2, R](layer: Layer[O, E2, R]) -> Effect[Context[O], E2, R | Scope]:
        """Resources live as long as the caller's Scope."""

        def start(context: ContextMap) -> Effect[Context[O], E2, R]:
            scope: ScopeService = context[Scope.key]
            return Layer.build_with_memo_map(layer, CurrentMemoMap.fork_or_create(context), scope)

        return WithFiber(lambda fiber: start(fiber.context))

    @staticmethod
    def make_memo_map_unsafe() -> MemoMap:
        return MemoMap(None)

    @staticmethod
    def make_memo_map() -> Effect[MemoMap, Never, Never]:
        return suspend(lambda: succeed(MemoMap(None)))

    @staticmethod
    def fork_memo_map_unsafe(parent: MemoMap) -> MemoMap:
        """Reads fall back to ``parent``; entries built here stay here."""
        return MemoMap(parent)

    @staticmethod
    def fork_memo_map(parent: MemoMap) -> Effect[MemoMap, Never, Never]:
        return suspend(lambda: succeed(MemoMap(parent)))


@final
class _Merge[O2, E2, R2]:
    __slots__ = ("_that",)

    def __init__(self, that: Layer[O2, E2, R2]) -> None:
        self._that: Final = that

    def __call__[O1, E1, R1](self, layer: Layer[O1, E1, R1]) -> Layer[O1 | O2, E1 | E2, R1 | R2]:
        return Layer.merge_all(layer, self._that)


@final
class _Provide[O, E2, R2]:
    """Two overloads stand in for ``Exclude<RIn, O>``, as ``provide`` does for effects."""

    __slots__ = ("_apply", "_that")

    def __init__(
        self,
        that: Layer[O, E2, R2],
        apply: Callable[[Layer[Any, Any, Any]], Layer[Any, Any, Any]],
    ) -> None:
        self._that: Final = that
        self._apply: Final = apply

    @overload
    def __call__[O1, E1](self, layer: Layer[O1, E1, O]) -> Layer[O1, E1 | E2, R2]: ...
    @overload
    def __call__[O1, E1, R1](self, layer: Layer[O1, E1, O | R1]) -> Layer[O1, E1 | E2, R1 | R2]: ...
    def __call__(self, layer: Layer[Any, Any, Any]) -> Layer[Any, Any, Any]:
        return self._apply(layer)


# -- MemoMap (Layer.ts:240-420) ------------------------------------------------------------------


@final
class _Entry:
    """One shared build: how many open scopes observe it, its result, and its own scope."""

    __slots__ = ("deferred", "finalizer", "observers", "scope")

    def __init__(self, forget: Callable[[], object]) -> None:
        self.observers = 0
        self.deferred: Final = Deferred[Context[Any], Any].make_unsafe()
        self.scope: Final[Closeable] = Scope.make_unsafe()

        # Holds the entry and `forget` only, never the requester that made it (Layer.ts:250).
        def finalizer(exit_: Exit[Any, Any]) -> Effect[None, Never, Never]:
            def release() -> Effect[None, Never, Never]:
                self.observers -= 1
                if self.observers > 0:
                    return VOID
                forget()
                return Scope.close(self.scope, exit_)

            return suspend(release)

        self.finalizer: Final = finalizer


def _observe(entry: _Entry, scope: ScopeService) -> bool:
    """Count and register at once, so no interrupt can land between the two (Layer.ts:268-275).
    A closed scope cannot own anything: the caller builds unshared instead."""
    if scope.state.tag == "Closed":
        return False
    entry.observers += 1
    Scope.add_finalizer_unsafe(scope, object(), entry.finalizer)
    return True


@final
class MemoMap:
    """Built layers by identity. A forked map reads its parent's entries but never writes them."""

    __slots__ = ("_map", "_parent")

    def __init__(self, parent: MemoMap | None) -> None:
        self._parent: Final = parent
        self._map: Final[dict[Layer[Any, Any, Any], _Entry]] = {}

    @property
    def map(self) -> Mapping[Layer[Any, Any, Any], object]:
        """This map's own entries, read-only."""
        return MappingProxyType(self._map)

    def get[O, E, R](
        self, layer: Layer[O, E, R], scope: ScopeService
    ) -> Effect[Context[O], E, R] | None:
        """The shared build of ``layer``, or None. ``scope`` owns a share from this call on,
        whether or not the returned effect ever runs."""
        entry = self._map.get(layer)
        if entry is None:
            return None if self._parent is None else self._parent.get(layer, scope)
        _observe(entry, scope)
        done = entry.deferred.effect
        return done if done is not None else entry.deferred.await_()

    def get_or_else_memoize[O, E, R](
        self, layer: Layer[O, E, R], scope: ScopeService, build: _Build[O, E, R]
    ) -> Effect[Context[O], E, R]:
        """The first requester builds in the entry's own scope and publishes its Exit, whatever
        it is, the moment the build ends; later requesters wait for that Exit."""

        def start() -> Effect[Context[O], E, R]:
            published: list[Deferred[Context[Any], Any]] = []

            def request() -> Effect[Context[O], E, R]:
                existing = self.get(layer, scope)
                if existing is not None:
                    return existing
                entry = _Entry(lambda: self._map.pop(layer, None))
                if not _observe(entry, scope):
                    return build(self, scope)
                published.append(entry.deferred)
                self._map[layer] = entry
                return build(self, entry.scope)

            def publish(exit_: Exit[Context[O], E]) -> Effect[None, Never, Never]:
                for deferred in published:
                    deferred.done_unsafe(exit_)
                return VOID

            return suspend(request).on_exit(publish)

        return suspend(start)


class CurrentMemoMap(Tag[MemoMap], key="effect/Layer/CurrentMemoMap"):
    """The MemoMap of the build in progress; nested builds fork it (Layer.ts:600-604)."""

    @staticmethod
    def fork_or_create(context: ContextMap) -> MemoMap:
        current = context.get(CurrentMemoMap.key)
        return MemoMap(current if isinstance(current, MemoMap) else None)


# -- Effect.provide(layer) (internal/layer.ts:8-22) ----------------------------------------------


def _close_with(scope: Closeable, exit_: Exit[Any, Any]) -> Effect[None, Never, Never]:
    closing = Scope.close_unsafe(scope, exit_)
    return VOID if closing is None else closing


@final
class provide_layer[O, E2 = Never, R2 = Never]:
    """``provide_layer(layer)(program)``: build ``layer`` in a fresh scope, run ``program`` with
    its services, and close the scope with ``program``'s Exit. The scope is not provided."""

    __slots__ = ("_layer",)

    def __init__(self, layer: Layer[O, E2, R2]) -> None:
        self._layer: Final = layer

    @overload
    def __call__[A, E](self, eff: Effect[A, E, O]) -> Effect[A, E | E2, R2]: ...
    @overload
    def __call__[A, E, R](self, eff: Effect[A, E, O | R]) -> Effect[A, E | E2, R | R2]: ...
    def __call__(self, eff: Effect[Any, Any, Any]) -> Effect[Any, Any, Any]:
        layer = self._layer

        def start() -> Effect[Any, Any, Any]:
            scope = Scope.make_unsafe()
            run = Layer.build_with_scope(layer, scope).flat_map(lambda ctx: provide(ctx)(eff))
            return run.on_exit(lambda exit_: _close_with(scope, exit_))

        return suspend(start)
