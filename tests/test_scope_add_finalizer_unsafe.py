"""Source-derived Scope plumbing for MemoMap: internal/effect.ts:4143-4159.

Python exposes the synchronous internal operation through Scope so Layer can register
ownership atomically without importing private implementation names.
"""
from typing import Any, Never

from peffect import Effect, Exit, Scope, Success, run, sync
from peffect.scope import Closed, Open


async def test_registration_is_synchronous_and_preserves_the_key():
    scope = Scope.make_unsafe()
    key = object()
    seen: list[Exit[Any, Any]] = []

    def finalizer(exit_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        return sync(lambda: seen.append(exit_))

    getattr(Scope, "add_finalizer_unsafe")(scope, key, finalizer)
    assert isinstance(scope.state, Open)
    assert scope.state.finalizer_key is key
    assert seen == []
    exit_ = Success(None)
    await run(Scope.close(scope, exit_))
    assert seen == [exit_]


async def test_registration_on_a_closed_scope_does_not_run_or_register():
    scope = Scope.make_unsafe()
    exit_ = Success(None)
    await run(Scope.close(scope, exit_))
    state = scope.state
    seen: list[bool] = []
    getattr(Scope, "add_finalizer_unsafe")(scope, object(), lambda _: sync(lambda: seen.append(True)))
    assert isinstance(scope.state, Closed)
    assert scope.state is state
    assert scope.state.exit is exit_
    assert seen == []
    await run(Scope.close(scope, exit_))
    assert seen == []
