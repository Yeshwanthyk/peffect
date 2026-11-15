"""Batch E: Effect v4's Scope tests, sequential strategy, ported. Upstream is pinned at
vendor/effect-v4 commit 289a53f (Effect v4.1.4), packages/effect/test/Scope.test.ts; the
implementation it pins is src/Scope.ts over internal/effect.ts:4033-4240 (scopeClose,
scopeCloseUnsafe, scopeForkUnsafe, scopeAddFinalizerExit, scopeMakeUnsafe, scoped). The
parallel variants and "executes finalizers in parallel" (:7, needs TestClock) are batch G.

Below the upstream tests: the Scope.ts doc examples that add coverage the tests above don't
have (the rest repeat it), then source-derived cases where neither has a test, each labelled.

Adaptations used throughout:
- Scope isn't built yet. Every Scope name is looked up when the test runs (`upcoming`), so until
  it exists each test fails with AttributeError instead of the file failing to import. The
  typed wrappers below (`close`, `add_finalizer`, ...) are where the real imports go once it
  lands. Names are upstream's in snake_case: `Scope.make_unsafe`, `Scope.add_finalizer`,
  `Scope.add_finalizer_exit`, `Scope.close`, `Scope.close_unsafe`, `Scope.fork_unsafe`,
  `scoped`, `scope` (Effect.scope) and `exit_` (Effect.exit).
- `scope.state._tag` is `scope.state.tag`, with upstream's values "Empty", "Open", "Closed".
- `Deferred` is tier 3. The tests use it only to signal between the test and a finalizer, so
  `Signal` below stands in: a one-shot latch over the public `async_`, nothing else.
- `forkChild({ startImmediately: true })` is `fork(eff, start_immediately=True)`.
- Upstream throws strings (`throw "release"`). Python raises exceptions, so a throwing
  finalizer raises `RuntimeError("release")`; `reasons` renders a defect with `str`, as
  upstream's `String(reason.defect)`, giving the same "die:release".
- `Scope.Scope` (the tag, yielded) is `scope`: upstream defines `Effect.scope` as that tag
  (internal/effect.ts:4192).
- `Exit.void` is `Success(None)`; `Exit.fail(e)` is `Failure(Cause.fail(e))`. `it.effect`'s
  ambient Scope and TestClock are not used by these cases, so they run under plain `run`.
"""

from collections.abc import Callable
from typing import Any, Never

import peffect
from peffect import (
    Cause,
    Die,
    Effect,
    Exit,
    Fail,
    Failure,
    Fiber,
    Success,
    async_,
    fail,
    fork,
    gen,
    run,
    run_exit,
    succeed,
    suspend,
    sync,
    uninterruptible,
    yield_now,
)
from peffect.context import MissingService
from peffect.core import AnyFiber, Resume, WithFiber

VOID: Exit[None, Never] = Success(None)


def upcoming(name: str) -> Any:
    """A name upstream has and peffect doesn't yet: AttributeError until it does."""
    return getattr(peffect, name)


# -- Scope API, typed; the bodies become imports once Scope exists ------------------------------


def make_unsafe(strategy: str = "sequential") -> Any:
    return upcoming("Scope").make_unsafe(strategy)


def make(strategy: str = "sequential") -> Effect[Any, Never, Never]:
    return upcoming("Scope").make(strategy)


def scope_fork(parent: Any, strategy: str = "sequential") -> Effect[Any, Never, Never]:
    return upcoming("Scope").fork(parent, strategy)


def provide_scope(scope: Any) -> Callable[[Effect[Any, Any, Any]], Effect[Any, Any, Never]]:
    return upcoming("Scope").provide(scope)


def fork_unsafe(parent: Any) -> Any:
    return upcoming("Scope").fork_unsafe(parent)


def add_finalizer(scope: Any, finalizer: Effect[Any, Any, Any]) -> Effect[None, Never, Never]:
    return upcoming("Scope").add_finalizer(scope, finalizer)


def add_finalizer_exit(
    scope: Any, finalizer: Callable[[Exit[Any, Any]], Effect[Any, Any, Any]]
) -> Effect[None, Never, Never]:
    return upcoming("Scope").add_finalizer_exit(scope, finalizer)


def close(scope: Any, exit_: Exit[Any, Any]) -> Effect[None, Never, Never]:
    return upcoming("Scope").close(scope, exit_)


def close_unsafe(scope: Any, exit_: Exit[Any, Any]) -> Effect[None, Never, Never] | None:
    return upcoming("Scope").close_unsafe(scope, exit_)


def exit_of[A, E](eff: Effect[A, E, Any]) -> Effect[Exit[A, E], Never, Never]:
    return upcoming("exit_")(eff)


def upcoming_scope() -> Effect[Any, Never, Any]:
    """`Effect.scope`: the Scope service of the enclosing `scoped`."""
    return upcoming("scope")


def state_tag(scope: Any) -> str:
    return scope.state.tag


def finalizer_log(log: list[str], entry: str) -> Effect[None, Never, Never]:
    return sync(lambda: log.append(entry))


# -- test-only helpers -------------------------------------------------------------------------


class Signal:
    """Deferred<void> reduced to what these tests use: `succeed` opens it once, `await_` parks
    until it is open. Built on the public `async_`; interrupting a waiter removes it."""

    def __init__(self) -> None:
        self.open = False
        self.waiters: list[Resume[None, Never, Never]] = []

    def _open(self) -> None:
        if not self.open:
            self.open = True
            waiters, self.waiters = self.waiters, []
            for resume in waiters:
                resume(succeed(None))

    def succeed(self) -> Effect[None, Never, Never]:
        return sync(self._open)

    def await_(self) -> Effect[None, Never, Never]:
        def register(resume: Resume[None, Never, Never]) -> Effect[None, Never, Never]:
            self.waiters.append(resume)
            return sync(lambda: self.waiters.remove(resume) if resume in self.waiters else None)

        return suspend(lambda: succeed(None) if self.open else async_(register))


def reasons(ex: Exit[Any, Any]) -> list[str]:
    """Upstream `reasons` (Scope.test.ts:115-122)."""
    if isinstance(ex, Success):
        return []
    out: list[str] = []
    for reason in ex.cause.reasons:
        if isinstance(reason, Fail):
            out.append(f"fail:{reason.error}")
        elif isinstance(reason, Die):
            out.append(f"die:{reason.defect}")
        else:
            out.append("interrupt")
    return out


# -- interrupted close --------------------------------------------------------------------------


async def test_sequential_completes_a_single_finalizer_when_its_callback_interrupts_close():
    """Scope.test.ts:22 "sequential: completes a single finalizer when its callback interrupts
    close".

    Upstream aborts the `runPromiseExit` signal from inside the finalizer callback, which
    interrupts the fiber running `close`. peffect's runner takes no signal, so the callback
    interrupts that same fiber directly (as D4): the root fiber is captured when it starts."""
    scope = make_unsafe("sequential")
    root: list[AnyFiber] = []
    cleaned: list[bool] = []

    def finalizer(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        root[0].interrupt_unsafe()  # controller.abort()
        return sync(lambda: cleaned.append(True))

    await run(add_finalizer_exit(scope, finalizer))

    def start(fiber: AnyFiber) -> Effect[None, Never, Never]:
        root.append(fiber)
        return yield_now.flat_map(lambda _: close(scope, VOID))

    await run_exit(WithFiber(start))
    assert cleaned == [True]


async def test_sequential_completes_direct_close_after_interruption():
    """Scope.test.ts:39 "sequential: completes direct close after interruption"."""
    scope = make_unsafe("sequential")
    started, release = Signal(), Signal()
    ran: list[str] = []

    @gen
    def program():
        yield from add_finalizer(scope, sync(lambda: ran.append("second")))

        @gen
        def first():
            yield from started.succeed()
            yield from release.await_()
            ran.append("first")

        yield from add_finalizer(scope, first())
        closing: Fiber[None, Never] = yield from fork(close(scope, VOID), start_immediately=True)
        yield from started.await_()
        interrupting: Fiber[None, Never] = yield from fork(
            closing.interrupt(), start_immediately=True
        )
        yield from yield_now
        assert interrupting.exit is None
        yield from release.succeed()
        yield from interrupting.join()
        yield from closing.await_()
        yield from close(scope, VOID)

    await run(program())
    assert ran == ["first", "second"]


async def test_close_unsafe_runs_finalizers_when_its_returned_effect_is_masked_by_the_caller():
    """Scope.test.ts:81 "closeUnsafe runs finalizers when its returned effect is masked by the
    caller"."""
    scope = make_unsafe()
    started, release = Signal(), Signal()
    ran: list[str] = []

    @gen
    def program():
        yield from add_finalizer(scope, sync(lambda: ran.append("second")))

        @gen
        def first():
            yield from started.succeed()
            yield from release.await_()
            ran.append("first")

        yield from add_finalizer(scope, first())
        finalizers = close_unsafe(scope, VOID)
        assert finalizers is not None
        closing: Fiber[None, Never] = yield from fork(
            uninterruptible(finalizers), start_immediately=True
        )
        yield from started.await_()
        interrupting: Fiber[None, Never] = yield from fork(
            closing.interrupt(), start_immediately=True
        )
        yield from release.succeed()
        yield from interrupting.join()

    await run(program())
    assert ran == ["first", "second"]


# -- finalizers that throw ----------------------------------------------------------------------


async def test_sequential_continues_and_awaits_remaining_finalizers_after_a_throw():
    """Scope.test.ts:124 "sequential: continues and awaits remaining finalizers after a throw"."""
    scope = make_unsafe("sequential")
    log: list[str] = []

    @gen
    def remaining():
        yield from yield_now
        log.append("remaining")

    def throwing(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        log.append("throwing")
        raise RuntimeError("release")

    @gen
    def program():
        yield from add_finalizer(scope, remaining())
        yield from add_finalizer_exit(scope, throwing)
        return (yield from exit_of(close(scope, VOID)))

    ex = await run(program())
    assert log == ["throwing", "remaining"]
    assert reasons(ex) == ["die:release"]


async def test_a_sole_throwing_finalizer_keeps_the_scoped_effects_failure():
    """Scope.test.ts:145 "a sole throwing finalizer keeps the scoped effect's failure"."""

    def throwing(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        raise RuntimeError("release")

    @gen
    def body():
        scope = yield from upcoming_scope()
        yield from add_finalizer_exit(scope, throwing)
        return (yield from fail("error"))

    ex = await run(exit_of(upcoming("scoped")(body())))
    assert reasons(ex) == ["die:error", "die:release"]


# -- fork ---------------------------------------------------------------------------------------


async def test_closing_a_child_removes_it_from_the_parent():
    """Scope.test.ts:159 "closing a child removes it from the parent"."""
    parent = make_unsafe()
    child = fork_unsafe(parent)
    assert state_tag(child) == "Empty"
    assert state_tag(parent) == "Open"
    await run(close(child, VOID))
    assert state_tag(parent) == "Empty"


async def test_a_throwing_child_finalizer_does_not_leave_the_child_in_its_parent():
    """Scope.test.ts:169 "a throwing child finalizer does not leave the child in its parent"."""
    parent = make_unsafe()
    child = fork_unsafe(parent)

    def throwing(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        raise RuntimeError("boom")

    await run(add_finalizer_exit(child, throwing))
    result = await run(exit_of(close(child, VOID)))
    assert isinstance(result, Failure)
    assert state_tag(child) == "Closed"
    assert state_tag(parent) == "Empty"


async def test_an_interrupted_close_does_not_leave_the_child_in_its_parent():
    """Scope.test.ts:182 "an interrupted close does not leave the child in its parent"."""
    parent = make_unsafe()
    child = fork_unsafe(parent)
    entered, release = Signal(), Signal()

    @gen
    def program():
        yield from add_finalizer(child, entered.succeed().flat_map(lambda _: release.await_()))
        fiber: Fiber[None, Never] = yield from fork(close(child, VOID), start_immediately=True)
        yield from entered.await_()
        interrupting: Fiber[None, Never] = yield from fork(
            fiber.interrupt(), start_immediately=True
        )
        yield from release.succeed()
        yield from interrupting.join()

    await run(program())
    assert state_tag(child) == "Closed"
    assert state_tag(parent) == "Empty"


async def test_closing_the_parent_closes_the_child_with_the_parents_exit_only_once():
    """Scope.test.ts:201 "closing the parent closes the child with the parent's exit only
    once"."""
    parent = make_unsafe()
    child = fork_unsafe(parent)
    exits: list[Exit[Any, Any]] = []

    def record(ex: Exit[Any, Any]) -> Effect[None, Never, Never]:
        return sync(lambda: exits.append(ex))

    failure: Exit[Never, str] = Failure(Cause.fail("parent failed"))
    await run(add_finalizer_exit(child, record))
    await run(close(parent, failure))
    await run(close(child, VOID))
    assert exits == [failure]


# -- Scope.ts doc examples (`ts import.meta.vitest` blocks; only those adding coverage) ----------


async def test_doc_scope_make_starts_empty_with_its_strategy_and_closes():
    """Scope.ts:29, `Scope` interface example: `Scope.make("sequential")`."""

    @gen
    def program():
        scope = yield from make("sequential")
        initial = (scope.strategy, state_tag(scope))
        yield from close(scope, VOID)
        return initial, state_tag(scope)

    assert await run(program()) == (("sequential", "Empty"), "Closed")


async def test_doc_open_state_holds_its_first_finalizer_inline():
    """Scope.ts:139, `State.Open` example: one finalizer is stored inline, not in the map."""
    scope = make_unsafe()
    await run(add_finalizer(scope, succeed(None)))
    assert state_tag(scope) == "Open"
    assert scope.state.finalizer is not None


async def test_doc_closed_state_keeps_the_exit_it_was_closed_with():
    """Scope.ts:172, `State.Closed` example."""
    done: Exit[str, Never] = Success("Done")

    @gen
    def program():
        scope = yield from make()
        yield from close(scope, done)
        return scope.state.exit if state_tag(scope) == "Closed" else None

    assert await run(program()) is done


async def test_doc_scoped_closes_its_scope_on_success():
    """Scope.ts:207, `Scope` service example: the finalizer runs when `scoped` succeeds."""
    cleanups: list[str] = []

    @gen
    def program():
        scope = yield from upcoming_scope()
        yield from add_finalizer(scope, finalizer_log(cleanups, "Cleanup"))

    await run(upcoming("scoped")(program()))
    assert cleanups == ["Cleanup"]


async def test_doc_provide_hands_a_caller_managed_scope_to_the_effect():
    """Scope.ts:298, `provide` example: the finalizer waits for the caller's close."""
    events: list[str] = []

    @gen
    def program():
        scope = yield from upcoming_scope()
        yield from add_finalizer(scope, finalizer_log(events, "cleanup"))
        events.append("working")

    @gen
    def with_scope():
        scope = yield from make()
        yield from provide_scope(scope)(program())
        yield from close(scope, VOID)

    await run(with_scope())
    assert events == ["working", "cleanup"]


async def test_doc_close_runs_three_finalizers_newest_first_after_the_work():
    """Scope.ts:375, `addFinalizer` example: three finalizers, so close takes the many-finalizer
    path (the upstream tests above only reach it with a throwing finalizer)."""
    events: list[str] = []

    @gen
    def program():
        scope = yield from make()
        for n in (1, 2, 3):
            yield from add_finalizer(scope, finalizer_log(events, f"cleanup {n}"))
        events.append("work")
        yield from close(scope, VOID)

    await run(program())
    assert events == ["work", "cleanup 3", "cleanup 2", "cleanup 1"]


async def test_doc_fork_effect_child_closes_before_its_parent():
    """Scope.ts:409, `fork` example. The child is "parallel" upstream too; with one finalizer
    its close takes the single-finalizer path, so no parallel finalization is exercised."""
    cleanups: list[str] = []

    @gen
    def program():
        parent = yield from make("sequential")
        yield from add_finalizer(parent, finalizer_log(cleanups, "parent"))
        child = yield from scope_fork(parent, "parallel")
        yield from add_finalizer(child, finalizer_log(cleanups, "child"))
        yield from close(child, VOID)
        yield from close(parent, VOID)

    await run(program())
    assert cleanups == ["child", "parent"]


# -- source-derived (no upstream test or example) -----------------------------------------------


async def test_source_a_finalizer_added_after_close_runs_at_once_with_the_closed_exit():
    """scopeAddFinalizerExit, internal/effect.ts:4123-4130: on a Closed scope the finalizer is
    called immediately with `scope.state.exit`, and nothing is registered."""
    scope = make_unsafe()
    failure: Exit[Never, str] = Failure(Cause.fail("closed"))
    exits: list[Exit[Any, Any]] = []
    await run(close(scope, failure))
    await run(add_finalizer_exit(scope, lambda ex: sync(lambda: exits.append(ex))))
    assert len(exits) == 1 and exits[0] is failure
    assert state_tag(scope) == "Closed"


async def test_source_forking_a_closed_parent_gives_a_child_closed_with_the_parents_exit():
    """scopeForkUnsafe, internal/effect.ts:4113-4116: the child takes the parent's Closed state
    and is not registered with it; a finalizer added to the child runs at once with that exit."""
    parent = make_unsafe()
    failure: Exit[Never, str] = Failure(Cause.fail("parent closed"))
    await run(close(parent, failure))
    child = fork_unsafe(parent)
    assert state_tag(child) == "Closed"
    assert child.state.exit is failure
    exits: list[Exit[Any, Any]] = []
    await run(add_finalizer_exit(child, lambda ex: sync(lambda: exits.append(ex))))
    assert len(exits) == 1 and exits[0] is failure
    assert state_tag(parent) == "Closed"


async def test_source_direct_close_keeps_every_failure_reason_and_elects_no_defect():
    """scopeCloseFinalizers + exitAsVoidAll, internal/effect.ts:4077-4101 and :2221-2234: each
    finalizer's Exit is collected newest first and their reasons are concatenated as they are.
    Equal reasons are not merged, and a Fail next to a Die stays a Fail: defect election
    (combineFinalizerCause, :4069-4075) belongs to a body's exit, not to a direct close."""

    def throwing(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        raise RuntimeError("release")

    scope = make_unsafe()

    @gen
    def program():
        yield from add_finalizer(scope, fail("boom"))
        yield from add_finalizer(scope, fail("boom"))
        yield from add_finalizer_exit(scope, throwing)
        return (yield from exit_of(close(scope, VOID)))

    ex = await run(program())
    assert reasons(ex) == ["die:release", "fail:boom", "fail:boom"]


async def test_source_scoped_restores_the_context_before_running_finalizers():
    """scoped, internal/effect.ts:4204-4215: the exit handler sets the context back to the one
    from before `scoped`, then closes the scope. So a finalizer no longer sees the Scope service:
    reading it there dies with the missing service."""
    seen: list[Exit[Any, Any]] = []

    def record(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        return exit_of(upcoming_scope()).flat_map(lambda ex: sync(lambda: seen.append(ex)))

    @gen
    def body():
        scope = yield from upcoming_scope()
        yield from add_finalizer_exit(scope, record)

    await run(upcoming("scoped")(body()))
    assert len(seen) == 1
    ex = seen[0]
    assert isinstance(ex, Failure)
    (reason,) = ex.cause.reasons
    assert isinstance(reason, Die) and isinstance(reason.defect, MissingService)
