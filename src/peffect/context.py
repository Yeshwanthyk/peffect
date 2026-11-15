"""Services: Tag, Reference, Context, provide. Built on the kernel's update_context.

    class Users(Tag[UserRepo]): ...          # the class is the key; R gains Users when used
    repo = yield from Users()                # Effect[UserRepo, Never, Users]
    provide(Users.context(impl))(program)    # removes Users from program's R

Effect v4: Context.ts (Service, Reference), internal/effect.ts:2379-2397 (provideContext).
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Generic, Never, Self, TypeVar, final, overload

from .core import ContextMap, Effect, WithFiber, die, succeed, update_context

T_contra = TypeVar("T_contra", contravariant=True)


class MissingService(Exception):
    """A tag was read with no service provided: a defect, since the types should prevent it."""

    def __init__(self, tag: type[Tag[Any]]) -> None:
        super().__init__(f"no service for {tag.__qualname__}; provide({tag.__name__}.context(...))")
        self.tag = tag


class Tag[S]:
    """Subclass once per service: ``class Users(Tag[UserRepo]): ...``.

    The subclass itself is the runtime key, looked up exactly. So a concrete tag can't be
    subclassed: R is covariant, and a parent tag's context would type-check against a child
    tag's request that the exact lookup can't answer. ``__init_subclass__`` enforces it.
    """

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for base in cls.__bases__:
            if issubclass(base, Tag) and base.__module__ != __name__:  # Tag, Reference are roots
                raise TypeError(f"{cls.__name__}: tag {base.__name__} is concrete; subclass Tag")

    def __iter__(self) -> Generator[Effect[S, Never, Self], Any, S]:
        return (yield from self.use())

    def use(self) -> Effect[S, Never, Self]:
        key = type(self)
        return WithFiber(lambda fiber: key._find(fiber.context))

    @classmethod
    def _find(cls, context: ContextMap) -> Effect[S, Never, Never]:
        return succeed(context[cls]) if cls in context else die(MissingService(cls))

    @classmethod
    def context(cls, service: S) -> Context[Self]:
        """The context holding just this service. Combine with ``|``; the right side wins."""
        if cls.__module__ == __name__:
            raise TypeError(f"subclass {cls.__name__} per service, then call .context on it")
        return Context({cls: service}, _TOKEN)


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
        if cls in context:
            return succeed(context[cls])
        if "_default_value" not in cls.__dict__:  # own dict: a cache is never inherited
            cls._default_value = cls.default()
        return succeed(cls._default_value)


@final
class _Token:
    __slots__ = ()


_TOKEN = _Token()


@final
class Context(Generic[T_contra]):
    """Services for the tags in T, built only by ``Users.context(impl)`` and ``a | b``.

    So a ``Context[Users]`` always holds a Users service, which is what lets ``provide`` take
    Users out of R. Contravariant: one holding more tags stands in for one holding fewer.
    """

    __slots__ = ("_services",)

    def __init__(self, services: dict[type, Any], token: _Token) -> None:
        if token is not _TOKEN:
            raise TypeError("build a Context with Users.context(impl), not Context(...)")
        self._services: Mapping[type, Any] = MappingProxyType(services)

    @property
    def services(self) -> Mapping[type, Any]:
        return self._services

    def __or__[T, T2](self: Context[T], other: Context[T2]) -> Context[T | T2]:
        return Context({**self.services, **other.services}, _TOKEN)


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
