"""Typed Context boundary for runForkWith, internal/effect.ts:5773-5777."""

from typing import Any

from peffect import Failure, Fiber, Scope, Success, Tag, never, run, run_fork_with, run_in


class Value(Tag[int], key="RootContext/Value"): ...


async def test_typed_root_uses_supplied_context():
    fiber = run_fork_with(Value.context(42))(Value().use())
    assert isinstance(fiber.exit, Success)
    assert fiber.exit.value == 42


async def test_typed_root_forwards_start_hook():
    owner = Scope.make_unsafe()
    seen: list[Fiber[Any, Any]] = []

    def attach(fiber: Fiber[Any, Any]):
        seen.append(fiber)
        run_in(fiber, owner)

    fiber = run_fork_with(Value.context(42))(never, on_fiber_start=attach)
    assert seen == [fiber]
    await run(Scope.close(owner, Success(None)))
    assert isinstance(fiber.exit, Failure)
