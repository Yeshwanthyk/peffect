"""Effect v4's `Effect.forkScoped`, ported test-first (vendor/effect-v4 289a53f).

Upstream: src/internal/effect.ts:5742-5765 (`forkScoped` is `flatMap(scope, (scope) => forkIn(self,
scope, options))`); tests from test/Effect.test.ts:3083-3115. TestClock's "sleep - handles multiple
sleeps" (TestClock.test.ts:133) is its first peffect caller.

Adaptations: `Exit.hasInterrupts(exit)` is `exit.cause.is_interrupted` on a `Failure`;
`Exit.void` is `Success(None)`; options are keyword arguments, as `fork_in` takes them.
"""

from typing import Any

import pytest

from peffect import (
    Failure,
    Scope,
    Success,
    fork_scoped,
    gen,
    never,
    on_interrupt,
    run,
    sync,
    yield_now,
)

CLOSED_SCOPE_OPTIONS: list[dict[str, Any]] = [
    {"start_immediately": True},
    {"uninterruptible": True},
]


@pytest.mark.parametrize("options", CLOSED_SCOPE_OPTIONS, ids=lambda o: str(o))
async def test_does_not_run_work_in_an_already_closed_scope(options: dict[str, Any]) -> None:
    """Effect.test.ts:3084 "does not run work in an already-closed scope with <options>"."""
    ran: list[bool] = []

    @gen
    def body():
        scope = yield from Scope.make()
        yield from Scope.close(scope, Success(None))
        fiber = yield from Scope.provide(scope)(
            fork_scoped(sync(lambda: ran.append(True)), **options)
        )
        exit_ = yield from fiber.await_()
        assert ran == []
        assert isinstance(exit_, Failure) and exit_.cause.is_interrupted

    await run(body())


async def test_is_interrupted_when_scope_is_closed() -> None:
    """Effect.test.ts:3102 "is interrupted when scope is closed"."""
    interrupted: list[bool] = []

    @gen
    def body():
        scope = yield from Scope.make()
        forever = on_interrupt(lambda _ids: sync(lambda: interrupted.append(True)))(never)
        yield from Scope.provide(scope)(fork_scoped(forever, start_immediately=True))
        yield from Scope.close(scope, Success(None))
        assert interrupted == [True]

    await run(body())


async def test_source_outlives_the_forking_fiber_until_the_scope_closes() -> None:
    """Source (internal/effect.ts:5765, forkIn): the fiber belongs to the scope, not to the
    fiber that forked it, so it keeps running after that fiber ends, and stops when the scope
    closes. A `fork` child would be interrupted when its parent ends instead."""

    @gen
    def body():
        scope = yield from Scope.make()
        parent = yield from Scope.provide(scope)(fork_scoped(fork_scoped(never)))
        child = yield from parent.join()
        yield from yield_now
        assert isinstance(parent.exit, Success) and parent.exit.value is child
        assert child.exit is None
        yield from Scope.close(scope, Success(None))
        exit_ = yield from child.await_()
        assert isinstance(exit_, Failure) and exit_.cause.is_interrupted_only

    await run(body())
