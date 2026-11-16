"""The completion prefix of Effect v4 Pull, pinned at 289a53f7.

Done is an ordinary typed failure, so a pull is already an Effect. This module
ports only the predicates, filter, terminal conversion and handler needed by
Queue. It does not claim the remaining Pull surface or introduce a wrapper type.

Python adaptation: filter_done uses Exit[Done[L], E] where upstream uses
Result<Done<L>, Cause<E>>. Its Failure contains the remaining Cause unchanged.
Exact-Done-first overloads express ExcludeDone at the public boundary; the
filter implementation works on object-typed reasons and narrows actual values.
"""

from collections.abc import Callable
from typing import Never, TypeIs, overload

from .cause import Cause, Die, Done, Fail, Interrupt, is_done
from .core import Effect, Exit, Failure, Success, fail


@overload
def done() -> Effect[Never, Done[None], Never]: ...
@overload
def done[L](value: L) -> Effect[Never, Done[L], Never]: ...
def done(value: object = None) -> Effect[Never, Done[object], Never]:
    """Fail with normal completion (internal/core.ts:765-767)."""
    return fail(Done(value))


def is_done_failure(reason: Fail[object] | Die | Interrupt) -> TypeIs[Fail[Done[object]]]:
    """Pull.ts:213-215: a Done value in Die is not a completion failure."""
    return isinstance(reason, Fail) and is_done(reason.error)


def is_done_cause(cause: Cause[object]) -> bool:
    """Pull.ts:195: true even when Done was merged with another failure."""
    return any(is_done_failure(reason) for reason in cause.reasons)


@overload
def filter_done(cause: Cause[Never]) -> Exit[Never, Never]: ...
@overload
def filter_done[L = Never](cause: Cause[Done[L]]) -> Exit[Done[L], Never]: ...
@overload
def filter_done[E, L = Never](cause: Cause[E | Done[L]]) -> Exit[Done[L], E]: ...
def filter_done(cause: Cause[object]) -> Exit[Done[object], object]:
    """Pull.ts:238-256: recover Done only without other non-interrupt reasons."""
    found: Done[object] | None = None
    remaining: list[Fail[object] | Die | Interrupt] = []
    has_failure = False
    for reason in cause.reasons:
        if is_done_failure(reason):
            if found is None:
                found = reason.error
        else:
            remaining.append(reason)
            if not isinstance(reason, Interrupt):
                has_failure = True
    if found is None:
        return Failure(cause)
    if has_failure:
        return Failure(Cause(tuple(remaining)))
    return Success(found)


@overload
def done_exit_from_cause(cause: Cause[Never]) -> Exit[Never, Never]: ...
@overload
def done_exit_from_cause[L = Never](cause: Cause[Done[L]]) -> Exit[L, Never]: ...
@overload
def done_exit_from_cause[E, L = Never](cause: Cause[E | Done[L]]) -> Exit[L, E]: ...
def done_exit_from_cause(cause: Cause[object]) -> Exit[object, object]:
    """Pull.ts:354-357: expose the leftover or the filtered failure cause."""
    result = filter_done(cause)
    if isinstance(result, Failure):
        return result
    return Success(result.value.value)


@overload
def catch_done[L, E2, R](
    effect: Effect[Never, Done[L], R], handler: Callable[[L], Effect[Never, E2, R]]
) -> Effect[Never, E2, R]: ...
@overload
def catch_done[A, L, R, B, E2](
    effect: Effect[A, Done[L], R], handler: Callable[[L], Effect[B, E2, R]]
) -> Effect[A | B, E2, R]: ...
@overload
def catch_done[E, L, R, E2](
    effect: Effect[Never, E | Done[L], R], handler: Callable[[L], Effect[Never, E2, R]]
) -> Effect[Never, E | E2, R]: ...
@overload
def catch_done[A, E, L, R, B, E2](
    effect: Effect[A, E | Done[L], R], handler: Callable[[L], Effect[B, E2, R]]
) -> Effect[A | B, E | E2, R]: ...
def catch_done[A, E, L, R, B, E2](
    effect: Effect[A, E | Done[L], R], handler: Callable[[L], Effect[B, E2, R]]
) -> Effect[A | B, E | E2, R]:
    """Pull.ts:165-177: handle leftover completion, preserving real failures."""

    def recover(cause: Cause[E | Done[L]]) -> Effect[B, E | E2, R]:
        result = filter_done(cause)
        if isinstance(result, Failure):
            return result
        return handler(result.value.value)

    return effect.catch_cause(recover)


__all__ = [
    "catch_done",
    "done",
    "done_exit_from_cause",
    "filter_done",
    "is_done_cause",
    "is_done_failure",
]
