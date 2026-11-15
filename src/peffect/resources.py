"""Scoped resources, composed from the public kernel and Scope APIs.

Effect v4 internal/effect.ts:4240-4269 (acquireRelease, addFinalizer) and :4502-4515
(acquireUseRelease), pinned at 289a53f7. Only acquireRelease's default, masked acquisition
mode is supported; its optional interruptible acquisition mode is not ported.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Never, cast, overload

from .core import (
    ContextMap,
    Effect,
    Exit,
    WithFiber,
    on_exit,
    suspend,
    uninterruptible,
    uninterruptible_mask,
    update_context,
)
from .scope import Scope, ScopeService, scope


def _provide_captured[A, E, R](
    eff: Effect[A, E, R], context: ContextMap
) -> Effect[A, E, Never]:
    # The enclosing resource API requires R at capture time. All its services are supplied
    # here, so the registered finalizer has no outstanding requirement. Like provide's
    # overloads, this discharges R only after installing its services; update_context alone
    # deliberately preserves R. Captured services win, as upstream provideContext does.
    return cast(Effect[A, E, Never], update_context(eff, lambda old: {**old, **context}))


def acquire_release[A, E = Never, R = Never, R2 = Never](
    acquire: Effect[A, E, R],
    release: Callable[[A, Exit[Any, Any]], Effect[Any, Never, R2]],
) -> Effect[A, E, R | R2 | Scope]:
    """Acquire masked and register release before interruption can resume.

    Release receives the scope's closing Exit and runs with the services captured here.
    A failed acquisition registers nothing. Requires Scope and both callbacks' services.
    """

    def captured(context: ContextMap) -> Effect[A, E, R | R2 | Scope]:
        def in_scope(s: ScopeService) -> Effect[A, E, R]:
            return acquire.flat_map(
                lambda a: Scope.add_finalizer_exit(
                    s, lambda ex: _provide_captured(release(a, ex), context)
                ).as_(a)
            )

        return uninterruptible(scope.flat_map(in_scope))

    return WithFiber(lambda fiber: captured(fiber.context))


def add_finalizer[R = Never](
    finalizer: Callable[[Exit[Any, Any]], Effect[None, Never, R]],
) -> Effect[None, Never, R | Scope]:
    """Register a finalizer in the current Scope, capturing its required services now."""

    def register(s: ScopeService, context: ContextMap) -> Effect[None, Never, R]:
        return Scope.add_finalizer_exit(s, lambda ex: _provide_captured(finalizer(ex), context))

    return scope.flat_map(lambda s: WithFiber(lambda fiber: register(s, fiber.context)))


# Pyright loses an all-Never union across free function type parameters. As in
# provide/scoped, cover the exact empty channels before the general union signature.
@overload
def acquire_use_release[Resource, A](
    acquire: Effect[Resource, Never, Never],
    use: Callable[[Resource], Effect[A, Never, Never]],
    release: Callable[[Resource, Exit[A, Never]], Effect[None, Never, Never]],
) -> Effect[A, Never, Never]: ...
@overload
def acquire_use_release[Resource, A, E, E2, E3](
    acquire: Effect[Resource, E, Never],
    use: Callable[[Resource], Effect[A, E2, Never]],
    release: Callable[[Resource, Exit[A, E2]], Effect[None, E3, Never]],
) -> Effect[A, E | E2 | E3, Never]: ...
@overload
def acquire_use_release[Resource, A, R, R2, R3](
    acquire: Effect[Resource, Never, R],
    use: Callable[[Resource], Effect[A, Never, R2]],
    release: Callable[[Resource, Exit[A, Never]], Effect[None, Never, R3]],
) -> Effect[A, Never, R | R2 | R3]: ...
@overload
def acquire_use_release[Resource, A, E, R, E2, R2, E3, R3](
    acquire: Effect[Resource, E, R],
    use: Callable[[Resource], Effect[A, E2, R2]],
    release: Callable[[Resource, Exit[A, E2]], Effect[None, E3, R3]],
) -> Effect[A, E | E2 | E3, R | R2 | R3]: ...
def acquire_use_release[
    Resource, A, E = Never, R = Never, E2 = Never, R2 = Never, E3 = Never, R3 = Never
](
    acquire: Effect[Resource, E, R],
    use: Callable[[Resource], Effect[A, E2, R2]],
    release: Callable[[Resource, Exit[A, E2]], Effect[None, E3, R3]],
) -> Effect[A, E | E2 | E3, R | R2 | R3]:
    """Acquire and release masked; use inherits the caller's interruption status.

    The use callback is suspended beneath the exit handler, so a thrown callback still
    releases the acquired resource. Release errors join the use failure through on_exit.
    """
    return uninterruptible_mask(
        lambda restore: acquire.flat_map(
            lambda a: suspend(lambda: restore(use(a))).pipe(on_exit(lambda ex: release(a, ex)))
        )
    )
