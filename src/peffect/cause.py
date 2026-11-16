"""Cause: everything that went wrong, as data.

A failed effect doesn't carry one exception; it carries a Cause: an ordered tuple of reasons.

    Fail(error)        an expected, typed failure. Its type is the E in Effect[A, E, R].
    Die(defect)        a bug: an exception nobody declared. Not in E.
    Interrupt(by)      the fiber was asked to stop (by fiber id `by`).

One Cause can hold several: a request failed AND its cleanup crashed AND a sibling was interrupted.
Flat like Effect v4 (src/Cause.ts:79-82); `+` keeps order and drops exact duplicates
(src/internal/core.ts:283-323).

A Die's defect is any value, as in v4 (`Die.defect: unknown`): when a defect is present, a
finalizer's combine elects non-completion Fails to Dies (`elect_defects`). A Fail's error need
not be an exception. `die()` still takes an exception; election can make another value a defect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Generic, Never, Self, TypeIs, TypeVar, final, overload

E_co = TypeVar("E_co", covariant=True)


@final
class Done[A = None]:
    """Normal input completion, carried as Fail(Done(value)), not as a new Reason.

    Effect v4 internal/core.ts:739-767. Python None represents the void leftover.
    The read-only payload follows Some's immutable, covariant representation.
    """

    __slots__ = ("_value",)
    __match_args__ = ("value",)
    _value: A
    _void: ClassVar[Self | None] = None

    def __new__(cls, value: A | None = None) -> Self:
        if value is None:
            if cls._void is None:
                cls._void = super().__new__(cls)
            return cls._void
        return super().__new__(cls)

    @overload
    def __init__(self: Done[None]) -> None: ...
    @overload
    def __init__(self, value: A) -> None: ...
    def __init__(self, value: object = None) -> None:
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> A:
        return self._value

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"Done is immutable: cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Done is immutable: cannot delete {name!r}")

    def __repr__(self) -> str:
        return f"Done({self._value!r})"

    def __reduce__(self) -> tuple[type[Done[A]], tuple[A]]:
        return type(self), (self._value,)


def is_done(value: object) -> TypeIs[Done[object]]:
    """Recognize the nominal Python completion value (Cause.ts:1286)."""
    return isinstance(value, Done)


@final
@dataclass(frozen=True, slots=True)
class Fail(Generic[E_co]):
    error: E_co


@final
@dataclass(frozen=True, slots=True)
class Die:
    defect: object


@final
@dataclass(frozen=True, slots=True)
class Interrupt:
    by: int | None = None


type Reason[E] = Fail[E] | Die | Interrupt


@final
@dataclass(frozen=True, slots=True)
class Cause(Generic[E_co]):
    reasons: tuple[Fail[E_co] | Die | Interrupt, ...] = ()

    # -- constructors -----------------------------------------------------------------------------
    @staticmethod
    def fail[E](error: E) -> Cause[E]:
        return Cause((Fail(error),))

    @staticmethod
    def die(defect: BaseException) -> Cause[Never]:
        return Cause((Die(defect),))

    @staticmethod
    def interrupt(by: int | None = None) -> Cause[Never]:
        return Cause((Interrupt(by),))

    # -- combine ----------------------------------------------------------------------------------
    def __add__[E2](self, other: Cause[E2]) -> Cause[E_co | E2]:
        out: list[Fail[E_co | E2] | Die | Interrupt] = list(self.reasons)
        out.extend(r for r in other.reasons if r not in out)
        return Cause(tuple(out))

    def elect_defects(self) -> Cause[E_co]:
        """If a Die is present, each non-Done Fail becomes a Die holding its error, in place.
        Effect v4: causeElectDefects, internal/effect.ts:184-191."""
        if not (self.defects and self.failures):
            return self
        return Cause(
            tuple(
                Die(r.error) if isinstance(r, Fail) and not is_done(r.error) else r
                for r in self.reasons
            )
        )

    def without_fails(self) -> Cause[Never]:
        return Cause(tuple(r for r in self.reasons if not isinstance(r, Fail)))

    # -- queries ----------------------------------------------------------------------------------
    @property
    def failures(self) -> list[E_co]:
        return [r.error for r in self.reasons if isinstance(r, Fail)]

    @property
    def defects(self) -> list[object]:
        return [r.defect for r in self.reasons if isinstance(r, Die)]

    @property
    def is_interrupted(self) -> bool:
        return any(isinstance(r, Interrupt) for r in self.reasons)

    @property
    def is_interrupted_only(self) -> bool:
        return bool(self.reasons) and all(isinstance(r, Interrupt) for r in self.reasons)

    def squash(self) -> BaseException:
        """One exception for a Python boundary. Lossy, like v4's squash
        (internal/effect.ts:289-299): first typed failure, else first defect, else interruption."""
        for r in self.reasons:
            if isinstance(r, Fail):
                err = r.error
                return err if isinstance(err, BaseException) else FailureError(err)
        for r in self.reasons:
            if isinstance(r, Die):
                d = r.defect
                return d if isinstance(d, BaseException) else DefectError(d)
        return Interrupted(self)


class FailureError(Exception):
    """A typed failure that isn't an exception, raised at the run() boundary."""

    def __init__(self, error: object) -> None:
        super().__init__(error)
        self.error = error


class DefectError(Exception):
    """A defect that isn't an exception (an elected Fail), raised at the run() boundary."""

    def __init__(self, defect: object) -> None:
        super().__init__(defect)
        self.defect = defect


class Interrupted(Exception):
    def __init__(self, cause: Cause[object]) -> None:
        super().__init__("fiber interrupted")
        self.cause = cause
