"""Services: Tag, Reference, Context, provide. Built on the kernel's update_context.

    class Users(Tag[UserRepo], key="app/Users"): ...  # the key is the identity; R gains Users
    repo = yield from Users()                          # Effect[UserRepo, Never, Users]
    provide(Users.context(impl))(program)              # removes Users from program's R

Effect v4: Context.ts (Service, Reference), internal/effect.ts:2379-2397 (provideContext).
"""

from __future__ import annotations

import math
from collections.abc import Generator, Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Generic, Never, Self, TypeVar, cast, final, overload

from .core import ContextMap, Effect, WithFiber, die, succeed, update_context

T_contra = TypeVar("T_contra", contravariant=True)


class MissingService(Exception):
    """A tag was read with no service provided: a defect, since the types should prevent it."""

    def __init__(self, tag: type[Tag[Any]]) -> None:
        super().__init__("Service not found" + (f": {tag.key}" if tag.key else ""))
        self.tag = tag


class Tag[S]:
    """Subclass once per service: ``class Users(Tag[UserRepo], key="app/Users"): ...``.

    The explicit ``key`` string is the runtime identity, as Effect v4's
    ``Context.Service("app/Users")`` (Context.ts:256-262): two declarations with one key read and
    replace the same service. The class stays the static R. A concrete tag can't be subclassed:
    R is covariant, and a parent tag's context would type-check against a child tag's request
    under another key. ``__init_subclass__`` enforces both rules; there is no default key.
    """

    __slots__ = ()
    key: ClassVar[str]

    def __init_subclass__(cls, *, key: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for base in cls.__bases__:
            if issubclass(base, Tag) and base.__module__ != __name__:  # Tag, Reference are roots
                raise TypeError(f"{cls.__name__}: tag {base.__name__} is concrete; subclass Tag")
        if cls.__module__ == __name__:
            return
        if not isinstance(key, str):  # also catches key=1 from types.new_class
            raise TypeError(f"{cls.__name__}: declare its key, class {cls.__name__}(..., key=...)")
        cls.key = key

    def __iter__(self) -> Generator[Effect[S, Never, Self], Any, S]:
        return (yield from self.use())

    def use(self) -> Effect[S, Never, Self]:
        key = type(self)
        return WithFiber(lambda fiber: key._find(fiber.context))

    @classmethod
    def _find(cls, context: ContextMap) -> Effect[S, Never, Never]:
        key = cls.key
        return succeed(context[key]) if key in context else die(MissingService(cls))

    @classmethod
    def context(cls, service: S) -> Context[Self]:
        """The context holding just this service. Combine with ``|``; the right side wins."""
        if cls.__module__ == __name__:
            raise TypeError(f"subclass {cls.__name__} per service, then call .context on it")
        return Context({cls.key: service}, _TOKEN)


class Reference[S](Tag[S]):
    """A service with a default, so using it never adds to R. Define ``default``.

    The default is built on first use and kept on the class: one value for every fiber and
    every run, until a provide overrides it (Effect v4 Context.ts:1091-1096). A raising default
    is a defect and is not kept, so the next use calls it again.
    """

    __slots__ = ()
    _default_value: ClassVar[Any]

    @classmethod
    def default(cls) -> S:
        raise NotImplementedError(f"{cls.__name__} must define default()")

    def __iter__(self) -> Generator[Effect[S, Never, Never], Any, S]:
        return (yield from self.use())

    def use(self) -> Effect[S, Never, Never]:
        key = type(self)
        return WithFiber(lambda fiber: key._find(fiber.context))

    @classmethod
    def _find(cls, context: ContextMap) -> Effect[S, Never, Never]:
        if cls.key in context:
            return succeed(context[cls.key])
        # per declaration, not per key (Context.ts:1091-1096)
        if "_default_value" not in cls.__dict__:  # own dict: a cache is never inherited
            cls._default_value = cls.default()
        return succeed(cls._default_value)


@final
class _Token:
    __slots__ = ()


_TOKEN = _Token()


def _equal_values(left: Any, right: Any, path: set[tuple[int, int]]) -> bool:
    """Equal.ts:186-238: identity, NaN, and coinductive container comparison.

    Python lists/tuples and dict values recurse; other service objects retain their Python
    equality protocol. Path assumptions are local to this comparison, never cached globally.
    """
    if left is right:
        return True
    if type(left) in (int, float) and type(right) in (int, float):
        return bool(left == right) or (
            isinstance(left, float)
            and isinstance(right, float)
            and math.isnan(left)
            and math.isnan(right)
        )
    if type(left) is not type(right):
        return False
    pair = (id(left), id(right))
    if pair in path:
        return True
    path.add(pair)
    try:
        if isinstance(left, (list, tuple)):
            a, b = cast(list[Any] | tuple[Any, ...], left), cast(list[Any] | tuple[Any, ...], right)
            return len(a) == len(b) and all(
                _equal_values(x, y, path) for x, y in zip(a, b, strict=True)
            )
        if isinstance(left, dict):
            a_map, b_map = cast(dict[Any, Any], left), cast(dict[Any, Any], right)
            return len(a_map) == len(b_map) and all(
                key in b_map and _equal_values(value, b_map[key], path)
                for key, value in a_map.items()
            )
        return bool(left == right)
    finally:
        path.remove(pair)


@final
class Context(Generic[T_contra]):
    """Services for the tags in T, built only by ``Users.context(impl)`` and ``a | b``.

    So a ``Context[Users]`` always holds a Users service, which is what lets ``provide`` take
    Users out of R. Contravariant: one holding more tags stands in for one holding fewer.
    """

    __slots__ = ("_services",)

    def __init__(self, services: dict[str, Any], token: _Token) -> None:
        if token is not _TOKEN:
            raise TypeError("build a Context with Users.context(impl), not Context(...)")
        self._services: Mapping[str, Any] = MappingProxyType(services)

    @property
    def services(self) -> Mapping[str, Any]:
        """Services by key string, in insertion order."""
        return self._services

    @staticmethod
    def empty() -> Context[Never]:
        """The context with no services: one shared, immutable instance (Context.ts:744-745)."""
        return _EMPTY

    def __or__[T, T2](self: Context[T], other: Context[T2]) -> Context[T | T2]:
        # an empty side returns the other one itself (Context.ts:1192-1193). Only empty() is
        # empty (every other context is built from a tag), so the empty side's T is Never.
        if not self.services:
            return cast(Context[T | T2], other)
        if not other.services:
            return cast(Context[T | T2], self)
        return Context({**self.services, **other.services}, _TOKEN)

    def __eq__(self, other: object) -> bool:
        """Same keys, equal values, in any order; only another Context (Context.ts:637-645).
        Values use identity, NaN and recursive container equality, then Python object equality."""
        if not isinstance(other, Context):
            return False
        mine, theirs = self.services, cast(Context[Any], other).services
        return len(mine) == len(theirs) and all(
            k in theirs and _equal_values(v, theirs[k], set()) for k, v in mine.items()
        )

    def __hash__(self) -> int:
        return len(self.services)  # the size, as upstream (Context.ts:646-648)


_EMPTY: Context[Never] = Context({}, _TOKEN)


@final
class provide[T]:
    """``provide(ctx)(program)``: run program with ctx's services added to the current ones.

    Removes ctx's tags from R. Python has no ``Exclude``, so two overloads cover it: R is
    covered by T (result R is Never), or R is a union that names T's tags (the rest stays).
    Anything else, such as a bare type variable, keeps its requirement.
    """

    __slots__ = ("_ctx",)

    def __init__(self, ctx: Context[T]) -> None:
        self._ctx = ctx

    @property
    def ctx(self) -> Context[T]:
        return self._ctx

    # exact match first: matching `T | R` against plain `T` would leave R unsolved
    @overload
    def __call__[A, E](self, eff: Effect[A, E, T]) -> Effect[A, E, Never]: ...
    @overload
    def __call__[A, E, R](self, eff: Effect[A, E, T | R]) -> Effect[A, E, R]: ...
    def __call__(self, eff: Effect[Any, Any, Any]) -> Effect[Any, Any, Any]:
        services = self.ctx.services
        return update_context(eff, lambda old: {**old, **services})
