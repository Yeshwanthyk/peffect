"""Effect[A, E, R]: a lazy program that succeeds with A, fails with E, and needs services R.

The type trick (same as Effect-TS, minus TS's Exclude):
  * every yielded instruction is an ``Effect[Any, E_i, R_i]``; pyright infers the generator's
    yield type as their union, and solving it against ``Effect[Any, E, R]`` (covariant E, R)
    gives ``E = E_1 | E_2 ...`` and ``R = R_1 | R_2 ...``.
  * removal (``catch_tag``, ``provide``) is solved by matching ``X | Rest`` against the union,
    which pyright does: it binds ``Rest`` to what is left.
  * ``run`` only accepts ``Effect[A, Never, Never]`` -> missing layer / unhandled error is a
    type error at the call site.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from functools import wraps
from typing import Any, Generic, Never, TypeVar, cast, final, overload

A_co = TypeVar("A_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)
R_co = TypeVar("R_co", covariant=True)


class Effect(Generic[A_co, E_co, R_co]):
    """Covariant in all three. Explicit TypeVars, not PEP 695: mypy doesn't infer the variance."""

    __slots__ = ("_node",)

    def __init__(self, node: _Node) -> None:
        self._node = node

    # `x = yield from eff` inside @gen: yields the effect, receives its success value.
    def __iter__(self) -> Generator[Effect[A_co, E_co, R_co], Any, A_co]:
        return cast(A_co, (yield self))  # the runtime sends back this effect's success value

    def pipe[B](self, f: Callable[[Effect[A_co, E_co, R_co]], B], /) -> B:
        return f(self)

    def map[B](self, f: Callable[[A_co], B]) -> Effect[B, E_co, R_co]:
        return Effect(_FlatMap(self, lambda a: succeed(f(a))))

    def and_then[B, E2, R2](
        self, f: Callable[[A_co], Effect[B, E2, R2]]
    ) -> Effect[B, E_co | E2, R_co | R2]:
        return Effect(_FlatMap(self, f))


# ---- interpreter nodes (private, untyped on purpose: the public API carries the types) ----


class _Node: ...


@final
@dataclass(frozen=True, slots=True)
class _Succeed(_Node):
    value: object


@final
@dataclass(frozen=True, slots=True)
class _Fail(_Node):
    error: object


@final
@dataclass(frozen=True, slots=True)
class _Sync(_Node):
    thunk: Callable[[], object]


@final
@dataclass(frozen=True, slots=True)
class _Async(_Node):
    thunk: Callable[[], Awaitable[object]]


@final
@dataclass(frozen=True, slots=True)
class _Gen(_Node):
    start: Callable[[], Generator[Effect[Any, Any, Any], Any, Any]]


@final
@dataclass(frozen=True, slots=True)
class _FlatMap(_Node):
    eff: Effect[Any, Any, Any]
    f: Callable[[Any], Effect[Any, Any, Any]]


@final
@dataclass(frozen=True, slots=True)
class _Catch(_Node):
    eff: Effect[Any, Any, Any]
    cls: type | tuple[type, ...]
    handler: Callable[[Any], Effect[Any, Any, Any]]


@final
@dataclass(frozen=True, slots=True)
class _Need(_Node):
    key: type


@final
@dataclass(frozen=True, slots=True)
class _Provide(_Node):
    eff: Effect[Any, Any, Any]
    layer: Any  # pfx.layer.Layer; typed at the public provide()


# ---- constructors ----


def succeed[A](value: A) -> Effect[A, Never, Never]:
    return Effect(_Succeed(value))


def fail[E](error: E) -> Effect[Never, E, Never]:
    return Effect(_Fail(error))


def sync[A](thunk: Callable[[], A]) -> Effect[A, Never, Never]:
    """Wrap a side effect. Exceptions it raises are defects, not typed errors."""
    return Effect(_Sync(thunk))


def from_async[A](thunk: Callable[[], Awaitable[A]]) -> Effect[A, Never, Never]:
    return Effect(_Async(thunk))


def attempt[A, X: Exception](
    thunk: Callable[[], A] | Callable[[], Awaitable[A]],
    catch: type[X],
) -> Effect[A, X, Never]:
    """Lift a raising (sync or async) call into a typed failure for exceptions of type ``catch``."""

    async def run() -> object:
        try:
            out = thunk()
            return _Ok(await out if inspect.isawaitable(out) else out)
        except catch as e:
            return _Err(e)

    def unwrap(r: object) -> Effect[A, X, Never]:
        if isinstance(r, _Err):
            return fail(cast(X, r.error))
        return succeed(cast(A, cast(_Ok, r).value))

    return Effect(_FlatMap(Effect(_Async(run)), unwrap))


@final
@dataclass(frozen=True, slots=True)
class _Ok:
    value: object


@final
@dataclass(frozen=True, slots=True)
class _Err:
    error: object


type Do[A, E, R] = Generator[Effect[Any, E, R], Any, A]
"""Declared style for @gen bodies: ``def get(uid: int) -> Do[User, NotFound, Db]``. Needed for mypy;
optional under pyright, where it also catches a body that yields more than it declares."""


def gen[**P, A, E = Never, R = Never](
    f: Callable[P, Generator[Effect[Any, E, R], Any, A]],
) -> Callable[P, Effect[A, E, R]]:
    """Effect.gen / Effect.fn: write effects as plain generator functions.

    @gen
    def greet(uid: str):
        users = yield from Users()
        user = yield from users.find(uid)
        return user.name
    # pyright: (uid: str) -> Effect[str, UserNotFound, Users]
    """

    @wraps(f)
    def build(*args: P.args, **kwargs: P.kwargs) -> Effect[A, E, R]:
        return Effect(_Gen(lambda: f(*args, **kwargs)))

    return build


# ---- error handling (pipeable) ----


def catch_tag[X, B, E2 = Never, R2 = Never](
    cls: type[X],
    handler: Callable[[X], Effect[B, E2, R2]],
) -> _CatchTag[X, B, E2, R2]:
    """eff.pipe(catch_tag(UserNotFound, lambda e: succeed(None))) removes UserNotFound from E."""
    return _CatchTag(cls, handler)


@final
class _CatchTag[X, B, E2, R2]:
    __slots__ = ("cls", "handler")

    def __init__(self, cls: type[X], handler: Callable[[X], Effect[B, E2, R2]]) -> None:
        self.cls = cls
        self.handler = handler

    # Exact match first: matching `X | E` against plain `X` leaves E unsolved instead of Never.
    @overload
    def __call__[A, R](self, eff: Effect[A, X, R]) -> Effect[A | B, E2, R | R2]: ...
    # Otherwise matching `X | E` against the effect's error union binds E to "the rest".
    @overload
    def __call__[A, E, R](self, eff: Effect[A, X | E, R]) -> Effect[A | B, E | E2, R | R2]: ...
    def __call__(self, eff: Effect[Any, Any, Any]) -> Effect[Any, Any, Any]:
        return Effect(_Catch(eff, self.cls, self.handler))


def catch_all[E, B, E2 = Never, R2 = Never](
    handler: Callable[[E], Effect[B, E2, R2]],
) -> _CatchAll[E, B, E2, R2]:
    return _CatchAll(handler)


@final
class _CatchAll[E, B, E2, R2]:
    __slots__ = ("handler",)

    def __init__(self, handler: Callable[[E], Effect[B, E2, R2]]) -> None:
        self.handler = handler

    def __call__[A, R](self, eff: Effect[A, E, R]) -> Effect[A | B, E2, R | R2]:
        return Effect(_Catch(eff, object, self.handler))


# ---- running ----


class Failure(Exception):
    """Raised by run() only if a typed failure escapes (impossible when types check)."""

    def __init__(self, error: object) -> None:
        super().__init__(error)
        self.error = error


class _Escape(Exception):  # internal carrier for typed failures through the interpreter
    def __init__(self, error: object) -> None:
        self.error = error


async def run[A](eff: Effect[A, Never, Never]) -> A:
    """Only accepts fully-provided, fully-handled programs."""
    from pfx.layer import Scope  # local: layer imports core

    async with Scope() as scope:
        try:
            return cast(A, await _interpret(eff, {}, scope))
        except _Escape as esc:
            raise Failure(esc.error) from None


async def _interpret(eff: Effect[Any, Any, Any], ctx: dict[type, object], scope: Any) -> object:
    node = eff._node  # pyright: ignore[reportPrivateUsage]
    match node:
        case _Succeed(value):
            return value
        case _Fail(error):
            raise _Escape(error)
        case _Sync(thunk):
            return thunk()
        case _Async(thunk):
            return await thunk()
        case _Need(key):
            if key not in ctx:
                raise LookupError(f"service {key.__name__} not provided")  # only via casts
            return ctx[key]
        case _FlatMap(inner, f):
            return await _interpret(f(await _interpret(inner, ctx, scope)), ctx, scope)
        case _Catch(inner, cls, handler):
            try:
                return await _interpret(inner, ctx, scope)
            except _Escape as esc:
                if isinstance(esc.error, cls):
                    return await _interpret(handler(esc.error), ctx, scope)
                raise
        case _Provide(inner, layer):
            built = await layer.build(ctx, scope)
            return await _interpret(inner, {**ctx, **built}, scope)
        case _Gen(start):
            g = start()
            send: object = None
            while True:
                try:
                    step = g.send(send)
                except StopIteration as stop:
                    return stop.value
                try:
                    send = await _interpret(step, ctx, scope)
                except _Escape as esc:
                    # let the generator see nothing: typed failures short-circuit like Effect-TS
                    g.close()
                    raise esc from None
        case _:
            raise TypeError(f"unknown node {node!r}")
