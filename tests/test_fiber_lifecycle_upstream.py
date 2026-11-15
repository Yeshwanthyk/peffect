"""Fiber lifecycle prerequisites for ManagedRuntime: root `runFork`/`runForkWith` with their
`RunOptions` (scheduler, uninterruptible, onFiberStart), `Fiber.runIn`, `Effect.forkIn`, and the
parallel Scope close that skips the closing fiber's own key. Pinned Effect v4 289a53f7.

The first section ports pinned upstream tests. The second is source-derived and labelled that
way: assertions read off packages/effect/src/internal/effect.ts (fiberScopeFinalizer :5690-5693,
forkIn :5717-5737, runForkWith :5773-5799, fiberRunIn :5801-5822, scopeCloseFinalizers
:4077-4101, FiberImpl._parent :652-654) and ManagedRuntime.ts:341-347, :402-407, where no
upstream test pins the behavior. Nothing here is ManagedRuntime itself.

Names under test (proposed; the coordinator owns exports): `run_fork(eff, *, scheduler=,
uninterruptible=, on_fiber_start=)` and `run_fork_with(context_map)` in peffect.fiber;
`run_in(fiber, scope)` and `fork_in(eff, scope, *, start_immediately=, uninterruptible=)` in
peffect.scope; `Scope.remove_finalizer_unsafe`; read-only `Fiber.parent`.

Adaptations, as in tests/test_fiber_start_upstream.py: `Effect.forkDetach` is `fork_daemon`,
`Fiber.await(f)` is `f.await_()`, `Exit.hasInterrupts` is `has_interrupts`, `Exit.void` is
`Success(None)`, `Effect.runSync`/`runPromise` of a closed program is `await run(...)`.
`asyncio.wait_for` is only a watchdog so a missing key skip fails instead of hanging.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, Literal

from peffect import (
    Exit,
    Failure,
    Fiber,
    Interrupt,
    Scope,
    Success,
    ensuring,
    fiber_id,
    fork,
    fork_daemon,
    gen,
    never,
    on_interrupt,
    run,
    succeed,
    sync,
    yield_now,
)
from peffect.core import WithFiber
from peffect.fiber import Task, run_fork, run_fork_with
from peffect.scheduler import Scheduler
from peffect.scope import Closed, Empty, Open, fork_in, run_in


def has_interrupts(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def interrupters(ex: Exit[Any, Any] | None) -> list[int | None]:
    assert isinstance(ex, Failure)
    return [r.by for r in ex.cause.reasons if isinstance(r, Interrupt)]


async def within[T](coro: Coroutine[Any, Any, T]) -> T:
    return await asyncio.wait_for(coro, 1.0)


# == upstream ==================================================================================


async def test_run_in_closes_the_latest_registration_first_when_registered_twice():
    """Fiber.test.ts:10."""
    events: list[str] = []
    scope = Scope.make_unsafe("sequential")
    fiber = run_fork(never.pipe(ensuring(sync(lambda: events.append("interrupted")))))
    run_in(fiber, scope)
    await run(Scope.add_finalizer(scope, sync(lambda: events.append("finalizer"))))
    run_in(fiber, scope)

    await within(run(Scope.close(scope, Success(None))))
    assert events == ["interrupted", "finalizer"]


async def test_run_in_does_not_wait_for_the_fiber_that_closes_the_scope():
    """Fiber.test.ts:22."""

    @gen
    def program():
        scope = yield from Scope.make()
        fiber = yield from fork_daemon(Scope.close(scope, Success(None)).as_("closed"))
        run_in(fiber, scope)
        return (yield from fiber.await_())

    ex = await within(run(program()))
    assert isinstance(ex, Success) and ex.value == "closed"


async def _fork_in_closed_scope(start_immediately: bool, uninterruptible: bool) -> None:
    ran: list[bool] = []

    @gen
    def program():
        scope = yield from Scope.make()
        yield from Scope.close(scope, Success(None))
        fiber = yield from fork_in(
            sync(lambda: ran.append(True)),
            scope,
            start_immediately=start_immediately,
            uninterruptible=uninterruptible,
        )
        return (yield from fiber.await_())

    ex = await within(run(program()))
    assert (ran, has_interrupts(ex)) == ([], True)


async def test_fork_in_does_not_run_work_in_a_closed_scope_with_start_immediately():
    """Effect.test.ts:3039, options {startImmediately: true}."""
    await _fork_in_closed_scope(start_immediately=True, uninterruptible=False)


async def test_fork_in_does_not_run_work_in_a_closed_scope_with_uninterruptible():
    """Effect.test.ts:3039, options {uninterruptible: true}."""
    await _fork_in_closed_scope(start_immediately=False, uninterruptible=True)


async def test_fork_in_is_interrupted_when_scope_is_closed():
    """Effect.test.ts:3056."""
    interrupted: list[bool] = []

    @gen
    def program():
        scope = yield from Scope.make()
        yield from fork_in(
            never.pipe(on_interrupt(lambda _: sync(lambda: interrupted.append(True)))),
            scope,
            start_immediately=True,
        )
        yield from Scope.close(scope, Success(None))

    await within(run(program()))
    assert interrupted == [True]


async def test_fork_in_does_not_wait_for_the_fiber_that_closes_a_parallel_scope():
    """Effect.test.ts:3073."""

    @gen
    def program():
        scope = yield from Scope.make("parallel")
        sibling = yield from fork_in(never, scope)
        closer = yield from fork_in(Scope.close(scope, Success(None)).as_("closed"), scope)
        closed = yield from closer.await_()
        sibling_exit = yield from sibling.await_()
        return closed, sibling_exit

    closed, sibling_exit = await within(run(program()))
    assert isinstance(closed, Success) and closed.value == "closed"
    assert has_interrupts(sibling_exit)


# == source-derived ============================================================================


class RecordingScheduler:
    """Scheduler shape from Effect.test.ts:67: tasks are kept, never run on their own."""

    execution_mode: Literal["sync", "async"] = "async"

    def __init__(self) -> None:
        self.tasks: list[Task] = []

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        return False

    def make_dispatcher(self) -> "RecordingScheduler":
        return self

    def schedule_task(self, task: Task, priority: int) -> None:
        self.tasks.append(task)

    def flush(self) -> None:
        pass


async def test_source_run_fork_runs_the_synchronous_prefix_and_skips_the_hook_once_exited():
    """runForkWith :5781-5782: evaluate first, and a fiber already exited returns before
    onFiberStart."""
    started: list[Fiber[Any, Any]] = []
    fiber = run_fork(succeed(1), on_fiber_start=started.append)
    ex = fiber.exit
    assert isinstance(ex, Success) and ex.value == 1
    assert started == []


async def test_source_run_fork_calls_the_hook_once_after_the_prefix_with_the_live_fiber():
    """runForkWith :5794-5796: onFiberStart(fiber) after the first evaluate."""
    events: list[object] = []
    fiber = run_fork(
        sync(lambda: events.append("prefix")).flat_map(lambda _: never),
        on_fiber_start=lambda f: events.append((f, f.exit)),
    )
    assert events == ["prefix", (fiber, None)]


async def test_source_run_fork_scheduler_option_overrides_the_context_scheduler():
    """runForkWith :5777-5780: options.scheduler is Context.add-ed over the given context, so it
    replaces one already there; the fiber's yields go to its dispatcher."""
    given, chosen = RecordingScheduler(), RecordingScheduler()
    fiber = run_fork_with(Scheduler.context(given).services)(
        yield_now.as_("after"), scheduler=chosen
    )
    assert fiber.context[Scheduler.key] is chosen
    assert (len(given.tasks), len(chosen.tasks), fiber.exit) == (0, 1, None)
    chosen.tasks.pop()()
    ex = fiber.exit
    assert isinstance(ex, Success) and ex.value == "after"


async def test_source_run_fork_uninterruptible_starts_the_root_masked():
    """runForkWith :5778-5779: `options?.uninterruptible !== true` is the root's mask."""
    read = WithFiber(lambda f: succeed(f.interruptible))
    masked, default = run_fork(read, uninterruptible=True), run_fork(read)
    assert isinstance(masked.exit, Success) and masked.exit.value is False
    assert isinstance(default.exit, Success) and default.exit.value is True


async def test_source_run_in_leaves_an_exited_fiber_and_the_scope_alone():
    """fiberRunIn :5810-5811."""
    scope = Scope.make_unsafe()
    fiber = run_fork(succeed(1))
    assert run_in(fiber, scope) is fiber
    assert isinstance(scope.state, Empty)


async def test_source_run_in_a_closed_scope_interrupts_with_the_fibers_own_id():
    """fiberRunIn :5812-5814: `self.interruptUnsafe(self.id)` (forkIn uses the parent's id)."""
    scope = Scope.make_unsafe()
    await run(Scope.close(scope, Success(None)))
    fiber = run_fork(never)
    assert run_in(fiber, scope) is fiber
    assert interrupters(fiber.exit) == [fiber.id]


async def test_source_run_in_detaches_when_the_fiber_exits_first():
    """fiberRunIn :5818: the observer removes the fiber's key, so the scope is Empty again."""
    scope = Scope.make_unsafe()
    fiber = run_fork(yield_now)
    run_in(fiber, scope)
    assert isinstance(scope.state, Open)
    await within(run(fiber.await_()))
    assert isinstance(scope.state, Empty)


async def test_source_closing_the_scope_waits_for_the_attached_fibers_cleanup():
    """fiberScopeFinalizer :5690-5693 uses fiberInterrupt, which awaits the target."""
    events: list[str] = []
    cleanup = yield_now.flat_map(lambda _: sync(lambda: events.append("cleaned")))
    fiber = run_fork(never.pipe(ensuring(cleanup)))
    scope = Scope.make_unsafe()
    run_in(fiber, scope)
    await within(run(Scope.close(scope, Success(None))))
    assert events == ["cleaned"]
    assert has_interrupts(fiber.exit)


async def test_source_on_fiber_start_composes_with_run_in():
    """ManagedRuntime.ts:341-347: onFiberStart attaches each started root to a scope; a root
    that exits synchronously never reaches the hook, so nothing is attached."""
    scope = Scope.make_unsafe("parallel")
    run_fork(succeed(1), on_fiber_start=lambda f: run_in(f, scope))
    assert isinstance(scope.state, Empty)
    a = run_fork(never, on_fiber_start=lambda f: run_in(f, scope))
    b = run_fork(never, on_fiber_start=lambda f: run_in(f, scope))
    await within(run(Scope.close(scope, Success(None))))
    assert has_interrupts(a.exit) and has_interrupts(b.exit)


async def test_source_fork_in_does_not_register_a_child_that_exits_at_start():
    """forkIn :5728: `if (!fiber._exit)` guards the registration."""

    @gen
    def program():
        scope = yield from Scope.make()
        fiber = yield from fork_in(succeed(1), scope, start_immediately=True)
        return scope, fiber

    scope, fiber = await run(program())
    assert isinstance(fiber.exit, Success)
    assert isinstance(scope.state, Empty)


async def test_source_fork_in_a_closed_scope_interrupts_with_the_parents_id():
    """forkIn :5723-5725, :5731-5733: an unstarted fiber, `interruptUnsafe(parent.id, ...)`."""

    @gen
    def program():
        parent = yield from fiber_id
        scope = yield from Scope.make()
        yield from Scope.close(scope, Success(None))
        fiber = yield from fork_in(succeed(1), scope)
        return parent, fiber

    parent, fiber = await run(program())
    assert interrupters(fiber.exit) == [parent]


async def test_source_fork_in_child_is_a_daemon_owned_by_the_scope():
    """forkIn :5724 forks with daemon=true: the parent's end does not interrupt the child."""
    interrupted: list[bool] = []
    scope = Scope.make_unsafe()
    child = never.pipe(on_interrupt(lambda _: sync(lambda: interrupted.append(True))))
    fiber = await run(fork_in(child, scope, start_immediately=True))
    assert (fiber.exit, interrupted) == (None, [])
    await within(run(Scope.close(scope, Success(None))))
    assert interrupted == [True]


async def test_source_fork_in_registers_atomically_with_the_start():
    """forkIn :5720-5736 forks and registers in one withFiber body. An interrupt the child sends
    its running parent is deferred to the parent's next op, after the registration."""
    interrupted: list[bool] = []
    scope = Scope.make_unsafe()

    def body(parent: Fiber[Any, Any]) -> Any:
        child = sync(parent.interrupt_unsafe).flat_map(lambda _: never)
        return fork_in(
            child.pipe(on_interrupt(lambda _: sync(lambda: interrupted.append(True)))),
            scope,
            start_immediately=True,
        ).flat_map(lambda _: never)

    parent = run_fork(WithFiber(body))
    assert has_interrupts(parent.exit)
    assert isinstance(scope.state, Open)
    await within(run(Scope.close(scope, Success(None))))
    assert interrupted == [True]


async def test_source_fork_in_uninterruptible_starts_the_child_masked():
    """forkUnsafe :5622-5623 via forkIn's options: `uninterruptible: true` masks the child."""
    scope = Scope.make_unsafe()
    read = WithFiber(lambda f: succeed(f.interruptible))
    fiber = await run(fork_in(read, scope, start_immediately=True, uninterruptible=True))
    assert isinstance(fiber.exit, Success) and fiber.exit.value is False


# -- seams ManagedRuntime.disposeEffect needs (ManagedRuntime.ts:402-407) ----------------------


async def test_source_remove_finalizer_unsafe_detaches_a_run_in_registration():
    """scopeRemoveFinalizerUnsafe :4162-4176, as disposeEffect exempts the disposing fiber."""
    scope = Scope.make_unsafe("parallel")
    fiber = run_fork(never)
    run_in(fiber, scope)
    Scope.remove_finalizer_unsafe(scope, fiber)
    await within(run(Scope.close(scope, Success(None))))
    assert isinstance(scope.state, Closed)
    assert fiber.exit is None
    fiber.interrupt_unsafe()


async def test_source_parent_is_the_live_supervisor_and_clears_on_exit():
    """FiberImpl._parent: set for a supervised child (forkUnsafe :5624-5627), absent for a daemon
    or root, cleared when the child exits (:652-654). disposeEffect walks it."""
    seen: dict[str, Fiber[Any, Any] | None] = {}

    @gen
    def program():
        me = yield from WithFiber(lambda f: succeed(f))
        child = yield from fork(never, start_immediately=True)
        daemon = yield from fork_daemon(never, start_immediately=True)
        seen["child"], seen["daemon"], seen["me"] = child.parent, daemon.parent, me
        yield from child.interrupt()
        seen["after"] = child.parent
        yield from daemon.interrupt()

    root = run_fork(program())
    await within(run(root.await_()))
    assert seen["child"] is seen["me"] and seen["daemon"] is None
    assert seen["after"] is None and root.parent is None
