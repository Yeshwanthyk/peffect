"""Prerequisites for Scope and TestClock: `forkChild({ startImmediately: true })` and
`Effect.exit`. Upstream is pinned at vendor/effect-v4 commit 289a53f (Effect v4.1.4).

Upstream has no test whose subject is either one; both appear inside tests of other things.
The first section ports the pinned tests that depend on them and use only what peffect already
has. The second section is source-derived and labelled that way: assertions read off
internal/effect.ts (forkUnsafe :5614-5637, exit :3873-3889) where no upstream test exists.

Adaptations used throughout:
- `forkChild({ startImmediately: true })` is `fork(eff, start_immediately=True)`,
  `Effect.forkDetach(..., { startImmediately: true })` is `fork_daemon(..., start_immediately=True)`
  and `Effect.exit` is `exit_`.
- `Fiber.getCurrent()` is the fiber `WithFiber` hands over; `Fiber.interrupt(f)` is
  `f.interrupt()`, `Fiber.await(f)` is `f.await_()`, `Effect.forkDetach` is `fork_daemon`.
- `Exit.hasInterrupts(exit)` is "a Failure with an Interrupt among its reasons". peffect's Exit
  has no `==`, so an expected `Exit.failCause(c)` is "a Failure whose cause == c", as in
  tests/test_finalization_upstream.py; upstream's thrown strings are exception instances.
"""

from typing import Any, Never

from peffect import (
    Cause,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    Success,
    exit_,
    fail,
    fork,
    fork_daemon,
    gen,
    never,
    on_exit,
    on_interrupt,
    run,
    succeed,
    suspend,
    sync,
    yield_now,
)
from peffect.core import AnyFiber, WithFiber


def has_interrupts(ex: Exit[Any, Any]) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


class Boom(Exception):
    pass


# == upstream ==================================================================================


async def test_fork_is_interrupted_with_parent():
    """Effect.test.ts:2987 describe "fork", "is interrupted with parent"."""
    flags: dict[str, bool] = {"child": False, "parent": False}

    def mark(name: str) -> Effect[None, Never, Never]:
        return sync(lambda: flags.__setitem__(name, True))

    @gen
    def program():
        inner = fork(never.pipe(on_interrupt(lambda _: mark("child"))), start_immediately=True)
        outer = inner.flat_map(lambda _: never).pipe(on_interrupt(lambda _: mark("parent")))
        fiber: Fiber[Never, Never] = yield from fork(outer, start_immediately=True)
        yield from fiber.interrupt()

    await run(program())
    assert flags["child"] is True
    assert flags["parent"] is True


async def test_fork_daemon_is_not_interrupted_with_parent():
    """Effect.test.ts:3013 describe "forkDaemon", "is not interrupted with parent"."""
    flags: dict[str, bool] = {"child": False, "parent": False}

    def mark(name: str) -> Effect[None, Never, Never]:
        return sync(lambda: flags.__setitem__(name, True))

    @gen
    def program():
        inner = fork_daemon(never.pipe(on_interrupt(lambda _: mark("child"))))
        outer = inner.flat_map(lambda _: never).pipe(on_interrupt(lambda _: mark("parent")))
        handle: Fiber[Never, Never] = yield from fork(outer, start_immediately=True)
        yield from handle.interrupt()

    await run(program())
    assert flags["child"] is False
    assert flags["parent"] is True


async def test_delivers_a_synchronous_self_interrupt_instead_of_completing_to_success():
    """Fiber.test.ts:137 "delivers a synchronous self-interrupt instead of completing to
    success"."""

    def body(current: AnyFiber) -> Effect[int, Never, Never]:
        current.interrupt_unsafe()
        return succeed(42)

    @gen
    def program():
        child: Fiber[int, Never] = yield from fork(WithFiber(body), start_immediately=True)
        ex = yield from child.await_()
        assert has_interrupts(ex)

    await run(program())


async def test_runs_an_async_interrupt_finalizer_exactly_once_in_order():
    """Fiber.test.ts:225 "runs an async interrupt finalizer exactly once, in order"."""
    events: list[str] = []

    @gen
    def finalizer():
        events.append("finalizer-start")
        yield from yield_now
        events.append("finalizer-end")

    def body(current: AnyFiber) -> Effect[None, Never, Never]:
        def acquire() -> Effect[None, Never, Never]:
            current.interrupt_unsafe()
            events.append("acquired")
            return succeed(None)

        guarded = suspend(acquire).pipe(on_interrupt(lambda _: finalizer()))
        return guarded.flat_map(lambda _: sync(lambda: events.append("unreachable")))

    @gen
    def program():
        child: Fiber[None, Never] = yield from fork(WithFiber(body), start_immediately=True)
        ex = yield from child.await_()
        events.append("awaited")
        assert has_interrupts(ex)

    await run(program())
    assert events == ["acquired", "finalizer-start", "finalizer-end", "awaited"]


BODY = Boom("body failure")
FIN = Boom("finalizer defect")


def throwing(_: object) -> Any:
    raise FIN


async def test_on_exit_preserves_the_original_failure_when_the_finalizer_throws_read_by_exit():
    """Effect.test.ts:3160 "onExit preserves the original failure when the finalizer throws", in
    its upstream form: the Exit is read inside the program by `Effect.exit`.
    tests/test_finalization_upstream.py reads the same case at the edge with `run_exit`."""
    result = await run(exit_(fail(BODY).pipe(on_exit(throwing))))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(BODY) + Cause.die(FIN)


async def test_on_exit_reports_a_thrown_finalizer_on_success_read_by_exit():
    """Effect.test.ts:3169 "onExit reports a thrown finalizer on success", read by `Effect.exit`."""
    result = await run(exit_(succeed(1).pipe(on_exit(throwing))))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(FIN)


# == source-derived (no upstream test) =========================================================


async def test_source_start_immediately_runs_the_child_before_fork_returns():
    """forkUnsafe, internal/effect.ts:5631-5632: `if (immediate) child.evaluate(effect)`, so a
    child that never suspends has finished by the time the parent gets its Fiber back."""
    order: list[str] = []

    @gen
    def program():
        child: Fiber[None, Never] = yield from fork(
            sync(lambda: order.append("child")), start_immediately=True
        )
        order.append("parent")
        assert isinstance(child.exit, Success)

    await run(program())
    assert order == ["child", "parent"]


async def test_source_default_fork_schedules_the_child_after_the_parent_continues():
    """forkUnsafe, internal/effect.ts:5633-5636: without `immediate` the child's first step is a
    scheduled task, so the parent runs on first. Current behavior, kept by the new keyword."""
    order: list[str] = []

    @gen
    def program():
        child: Fiber[None, Never] = yield from fork(sync(lambda: order.append("child")))
        order.append("parent")
        assert child.exit is None
        yield from child.join()

    await run(program())
    assert order == ["parent", "child"]


async def test_source_exit_turns_success_and_failure_into_values():
    """exit, internal/effect.ts:3873-3889: the Exit frame succeeds with `exitSucceed(value)` on
    success and `exitFailCause(cause)` on failure, so the program goes on either way."""

    @gen
    def program():
        ok = yield from exit_(succeed(1))
        failed = yield from exit_(fail(BODY))
        return ok, failed

    ok, failed = await run(program())
    assert isinstance(ok, Success) and ok.value == 1
    assert isinstance(failed, Failure) and failed.cause == Cause.fail(BODY)


async def test_source_exit_of_an_exit_succeeds_with_that_same_exit():
    """exit, internal/effect.ts:3874: `effectIsExit(self) ? exitSucceed(self)`: an Exit passed
    straight in comes back as the value, the same object."""
    done: Exit[int, Never] = Success(1)
    failed: Exit[Never, Boom] = Failure(Cause.fail(BODY))
    assert (await run(exit_(done))) is done
    assert (await run(exit_(failed))) is failed


async def test_source_fork_daemon_start_immediately_runs_now_and_stays_unsupervised():
    """forkDetach, internal/effect.ts:5728: `forkUnsafe(parent, self, startImmediately, true)`.
    The child runs before `fork_daemon` returns, and as a daemon (:5625-5628 skip the parent's
    children) it is still running after its parent has ended."""
    order: list[str] = []

    @gen
    def program():
        @gen
        def daemon():
            order.append("daemon")
            yield from never

        handle: Fiber[None, Never] = yield from fork_daemon(daemon(), start_immediately=True)
        order.append("parent")
        return handle

    handle = await run(program())
    assert order == ["daemon", "parent"]
    assert handle.exit is None
    await run(handle.interrupt())


async def test_source_exit_does_not_catch_the_fibers_own_interruption():
    """exitFailCause, internal/core.ts:611-624: while the fiber is interrupted and interruptible,
    the unwind skips every handler, the Exit frame included (it has an identifier). So `exit_`
    can't turn its own fiber's interruption into a value: the fiber ends interrupted and the
    code after `exit_` never runs."""
    after: list[object] = []

    @gen
    def body():
        ex = yield from exit_(never)
        after.append(ex)

    @gen
    def program():
        fiber: Fiber[None, Never] = yield from fork(body(), start_immediately=True)
        yield from fiber.interrupt()
        return (yield from fiber.await_())

    ex = await run(program())
    assert has_interrupts(ex)
    assert after == []
