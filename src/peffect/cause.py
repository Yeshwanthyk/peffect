"""Cause: everything that went wrong, as data.

A failed effect doesn't carry one exception; it carries a Cause: an ordered tuple of reasons.

    Fail(error)        an expected, typed failure. Its type is the E in Effect[A, E, R].
    Die(defect)        a bug: an exception nobody declared. Not in E.
    Interrupt(by)      the fiber was asked to stop (by fiber id `by`).

One Cause can hold several: a request failed AND its cleanup crashed AND a sibling was interrupted.
Flat like Effect v4 (src/Cause.ts:79-82); `+` keeps order and drops exact duplicates
(src/internal/core.ts:283-323).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Never, TypeVar, final

E_co = TypeVar("E_co", covariant=True)


@final
@dataclass(frozen=True, slots=True)
class Fail(Generic[E_co]):
    error: E_co


@final
@dataclass(frozen=True, slots=True)
class Die:
    defect: BaseException


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

    def without_fails(self) -> Cause[Never]:
        return Cause(tuple(r for r in self.reasons if not isinstance(r, Fail)))

    # -- queries ----------------------------------------------------------------------------------
    @property
    def failures(self) -> list[E_co]:
        return [r.error for r in self.reasons if isinstance(r, Fail)]

    @property
    def defects(self) -> list[BaseException]:
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
                return r.defect
        return Interrupted(self)


class FailureError(Exception):
    """A typed failure that isn't an exception, raised at the run() boundary."""

    def __init__(self, error: object) -> None:
        super().__init__(error)
        self.error = error


class Interrupted(Exception):
    def __init__(self, cause: Cause[object]) -> None:
        super().__init__("fiber interrupted")
        self.cause = cause
