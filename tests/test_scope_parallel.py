"""Batch G: Effect v4's parallel Scope tests, ported before the implementation. Upstream is pinned
at vendor/effect-v4 commit 289a53f7, packages/effect/test/Scope.test.ts:
  :7   "executes finalizers in parallel"                          (Python harness adaptation)
  :22  "parallel: completes a single finalizer when its callback interrupts close"
  :39  "parallel: completes direct close after interruption"
  :124 "parallel: continues and awaits remaining finalizers after a throw"
The implementation they pin is scopeCloseFinalizers, internal/effect.ts:4077-4101: each finalizer,
newest first, is forked as a daemon child of the closing fiber (startImmediately, mask inherited),
then fiberAwaitAll collects their Exits for exitAsVoidAll.

Names, `VOID`, `reasons` (copied) and the root-fiber interrupt for `controller.abort()` follow
tests/test_scope.py. Gates are real `Deferred`s made with `make_unsafe` outside the program, so
`ensuring` can open every test-owned gate even when an assertion fails: the closing fiber is
masked, and a gate left shut would strand its finalizers. Each run sits under `asyncio.wait_for`
only so a wrong implementation fails instead of hanging; the gates, not the watchdog, set order.
"""

import asyncio
from typing import Any, Never

from peffect import (
    Deferred,
    Die,
    Effect,
    Exit,
    Fail,
    Fiber,
    Scope,
    Success,
    ensuring,
    exit_,
    fork,
    gen,
    run,
    run_exit,
    sync,
    yield_now,
)
from peffect.core import AnyFiber, WithFiber

VOID: Exit[None, Never] = Success(None)
WATCHDOG_SECONDS = 2


def reasons(ex: Exit[Any, Any]) -> list[str]:
    """Upstream `reasons` (Scope.test.ts:115-122), as in tests/test_scope.py."""
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


def open_all(*gates: Deferred[None]) -> Effect[None, Never, Never]:
    """Open every test-owned gate; used in `ensuring`, so a failed assertion cannot strand one."""
    eff: Effect[Any, Never, Never] = sync(lambda: None)
    for gate in gates:
        eff = eff.flat_map(lambda _, g=gate: g.succeed(None))
    return eff.as_(None)


async def test_parallel_executes_finalizers_in_parallel():
    """Scope.test.ts:7 "executes finalizers in parallel".

    Upstream (kept here as the reference):
        three finalizers, each `Effect.sleep(Duration.seconds(1))`;
        fiber = forkChild(Scope.close(scope, Exit.void), { startImmediately: true })
        expect(fiber.pollUnsafe()).toBeUndefined()
        yield* TestClock.adjust(Duration.seconds(1))
        expect(fiber.pollUnsafe()).toBeDefined()
    One second of virtual time ends all three sleeps only if they overlap.

    Python harness adaptation (no TestClock yet): each "sleep" is a finalizer that opens its own
    `started` gate and then waits on one shared `release` gate, standing in for the one clock
    adjustment. Awaiting all three `started` gates while `release` is still shut is what proves
    the overlap: a sequential close would park in the first finalizer and never start the
    others. Opening `release` is the adjustment; the close then completes."""
    scope = Scope.make_unsafe("parallel")
    started = [Deferred[None].make_unsafe() for _ in range(3)]
    release = Deferred[None].make_unsafe()
    slept: list[int] = []

    def sleep(n: int) -> Effect[None, Never, Never]:
        return (
            started[n]
            .succeed(None)
            .flat_map(lambda _: release.await_())
            .flat_map(lambda _: sync(lambda: slept.append(n)))
        )

    @gen
    def program():
        for n in range(3):
            yield from Scope.add_finalizer(scope, sleep(n))
        fiber: Fiber[None, Never] = yield from fork(
            Scope.close(scope, VOID), start_immediately=True
        )
        assert fiber.exit is None  # pollUnsafe() is undefined
        for gate in started:
            yield from gate.await_()
        assert fiber.exit is None and slept == []  # all three in flight, none finished
        yield from release.succeed(None)  # TestClock.adjust(1 second)
        ex = yield from fiber.await_()
        assert isinstance(ex, Success)  # pollUnsafe() is defined
        assert sorted(slept) == [0, 1, 2]

    await asyncio.wait_for(
        run(program().pipe(ensuring(open_all(release, *started)))), WATCHDOG_SECONDS
    )


async def test_parallel_completes_a_single_finalizer_when_its_callback_interrupts_close():
    """Scope.test.ts:22 "parallel: completes a single finalizer when its callback interrupts
    close". As the sequential port: the callback interrupts the root fiber running `close`
    directly, since peffect's runner takes no abort signal."""
    scope = Scope.make_unsafe("parallel")
    root: list[AnyFiber] = []
    cleaned: list[bool] = []

    def finalizer(_: Exit[Any, Any]) -> Effect[None, Never, Never]:
        root[0].interrupt_unsafe()  # controller.abort()
        return sync(lambda: cleaned.append(True))

    await run(Scope.add_finalizer_exit(scope, finalizer))

    def start(fiber: AnyFiber) -> Effect[None, Never, Never]:
        root.append(fiber)
        return yield_now.flat_map(lambda _: Scope.close(scope, VOID))

    await asyncio.wait_for(run_exit(WithFiber(start)), WATCHDOG_SECONDS)
    assert cleaned == [True]


async def test_parallel_completes_direct_close_after_interruption():
    """Scope.test.ts:39 "parallel: completes direct close after interruption"."""
    scope = Scope.make_unsafe("parallel")
    started = Deferred[None].make_unsafe()
    release = Deferred[None].make_unsafe()
    other_started = Deferred[None].make_unsafe()
    ran: list[str] = []

    @gen
    def second():
        yield from other_started.succeed(None)
        yield from release.await_()
        ran.append("second")

    @gen
    def first():
        yield from started.succeed(None)
        yield from release.await_()
        ran.append("first")

    @gen
    def program():
        yield from Scope.add_finalizer(scope, second())
        yield from Scope.add_finalizer(scope, first())
        closing: Fiber[None, Never] = yield from fork(
            Scope.close(scope, VOID), start_immediately=True
        )
        # Python harness guard, not upstream: a close that ended here could never open the gates
        # awaited next, so fail with the reason instead of parking until the watchdog.
        assert closing.exit is None, closing.exit
        yield from started.await_()
        yield from other_started.await_()
        interrupting: Fiber[None, Never] = yield from fork(
            closing.interrupt(), start_immediately=True
        )
        yield from yield_now
        assert interrupting.exit is None
        yield from release.succeed(None)
        yield from interrupting.join()
        yield from closing.await_()
        yield from Scope.close(scope, VOID)
        ran.sort()
        assert ran == ["first", "second"]

    await asyncio.wait_for(
        run(program().pipe(ensuring(open_all(started, other_started, release)))),
        WATCHDOG_SECONDS,
    )


async def test_parallel_continues_and_awaits_remaining_finalizers_after_a_throw():
    """Scope.test.ts:124 "parallel: continues and awaits remaining finalizers after a throw"."""
    scope = Scope.make_unsafe("parallel")
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
        yield from Scope.add_finalizer(scope, remaining())
        yield from Scope.add_finalizer_exit(scope, throwing)
        return (yield from exit_(Scope.close(scope, VOID)))

    ex = await asyncio.wait_for(run(program()), WATCHDOG_SECONDS)
    assert log == ["throwing", "remaining"]
    assert reasons(ex) == ["die:release"]


async def test_source_parallel_finalizers_inherit_the_close_mask():
    """internal/effect.ts:4094 forkUnsafe(..., "inherit"): a parallel finalizer runs masked like
    the masked close that starts it, so interrupting its own fiber does not cut it short."""
    scope = Scope.make_unsafe("parallel")
    done: list[str] = []

    def self_interrupt(fiber: AnyFiber) -> Effect[None, Never, Never]:
        fiber.interrupt_unsafe()
        return yield_now.flat_map(lambda _: sync(lambda: done.append("interrupted-self")))

    @gen
    def program():
        yield from Scope.add_finalizer(scope, sync(lambda: done.append("other")))
        yield from Scope.add_finalizer(scope, WithFiber(self_interrupt))
        return (yield from exit_(Scope.close(scope, Success(None))))

    ex = await run(program())
    assert isinstance(ex, Success)
    assert sorted(done) == ["interrupted-self", "other"]
