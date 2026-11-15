"""Kernel behavior: each test is one rule of the run loop."""

import asyncio
from typing import Any, Never, cast

import pytest

from peffect import (
    Cause,
    Effect,
    Failure,
    Success,
    async_,
    catch_all,
    catch_cause,
    die,
    ensuring,
    fail,
    fork,
    fork_daemon,
    gen,
    never,
    on_exit,
    run,
    run_exit,
    sleep,
    succeed,
    sync,
    uninterruptible,
    uninterruptible_mask,
    yield_now,
)
from peffect.cause import Interrupt
from peffect.core import UNIT, Restore, Resume


class Boom(Exception):
    pass


async def test_success_and_map():
    assert await run(succeed(2).map(lambda x: x + 1)) == 3


async def test_failure_short_circuits():
    seen: list[str] = []
    eff = fail(Boom()).flat_map(lambda _: sync(lambda: seen.append("ran")))
    ex = await run_exit(eff)
    assert isinstance(ex, Failure) and isinstance(ex.cause.failures[0], Boom)
    assert seen == []


async def test_stack_safe_100k_flat_maps():
    eff: Effect[int, None, None] = succeed(0)
    for _ in range(100_000):
        eff = eff.flat_map(lambda n: succeed(n + 1))
    assert await run(eff) == 100_000


async def test_sync_raise_is_a_defect_not_a_failure():
    ex = await run_exit(sync(lambda: 1 // 0))
    assert isinstance(ex, Failure)
    assert ex.cause.failures == [] and isinstance(ex.cause.defects[0], ZeroDivisionError)


async def test_catch_all_recovers_typed_failure_but_not_defect():
    ok = fail("nope").catch_all(lambda e: succeed(f"got {e}"))
    assert await run(ok) == "got nope"
    ex = await run_exit(die(Boom()).pipe(catch_all(lambda _: succeed("no"))))
    assert isinstance(ex, Failure) and ex.cause.defects
    piped = fail("nope").pipe(catch_all(lambda e: succeed(f"piped {e}")))
    assert await run(piped) == "piped nope"
    assert isinstance(ex, Failure) and ex.cause.defects


async def test_gen_threads_values_and_finally_runs_on_failure():
    log: list[str] = []

    @gen
    def prog():
        try:
            a = yield from succeed(1)
            b = yield from succeed(2)
            log.append(f"sum {a + b}")
            yield from fail("stop")
            log.append("unreachable")
        finally:
            log.append("finally")
        return a + b

    ex = await run_exit(prog())
    assert isinstance(ex, Failure) and ex.cause.failures == ["stop"]
    assert log == ["sum 3", "finally"]


async def test_finalizer_runs_after_success_and_failure():
    log: list[str] = []
    fin = sync(lambda: log.append("fin"))
    assert await run(succeed(1).pipe(ensuring(fin))) == 1
    await run_exit(fail("x").pipe(ensuring(fin)))
    assert log == ["fin", "fin"]


async def test_finalizer_failure_combines_causes():
    """Both reasons are kept; the finalizer's defect elects the body's Fail to a Die holding the
    same value (Effect v4 combineFinalizerCause + causeElectDefects, internal/effect.ts:4069-4075,
    184-191; Effect.test.ts:3231 "finalizer errors merged")."""
    eff = fail("first").pipe(on_exit(lambda _: die(Boom())))
    ex = await run_exit(eff)
    assert isinstance(ex, Failure)
    assert ex.cause.failures == []
    first, second = ex.cause.defects
    assert first == "first" and isinstance(second, Boom)


# -- interruption ---------------------------------------------------------------------------------


async def test_interrupt_runs_finalizers_and_skips_catch():
    log: list[str] = []

    @gen
    def parent():
        child = yield from fork(
            never.pipe(catch_cause(lambda _: sync(lambda: log.append("caught")))).pipe(
                ensuring(sync(lambda: log.append("finalized")))
            )
        )
        yield from sleep(0.01)
        yield from child.interrupt()  # waits for the finalizer
        log.append("after interrupt")
        return child.exit

    ex = await run(parent())
    assert log == ["finalized", "after interrupt"]
    assert isinstance(ex, Failure) and ex.cause.is_interrupted_only


async def test_uninterruptible_region_finishes_before_interrupt_lands():
    log: list[str] = []

    @gen
    def parent():
        child = yield from fork(
            uninterruptible(
                sleep(0.02).flat_map(lambda _: sync(lambda: log.append("done")))
            ).flat_map(lambda _: sync(lambda: log.append("after region")))
        )
        yield from sleep(0.005)
        yield from child.interrupt()
        return child.exit

    ex = await run(parent())
    assert log == ["done"]  # region completed; the next step saw the interrupt
    assert isinstance(ex, Failure) and ex.cause.is_interrupted


async def test_mask_restore_makes_only_the_use_part_interruptible():
    log: list[str] = []

    def body(restore: Restore) -> Effect[None, Never, Never]:  # acquire / use / release
        return sync(lambda: log.append("acquire")).flat_map(
            lambda _: restore(never).pipe(ensuring(sync(lambda: log.append("release"))))
        )

    @gen
    def parent():
        child = yield from fork(uninterruptible_mask(body))
        yield from sleep(0.005)
        yield from child.interrupt()

    await run(parent())
    assert log == ["acquire", "release"]


async def test_sleep_cancelled_on_interrupt():
    loop = asyncio.get_running_loop()
    before = len(cast(list[Any], loop._scheduled))  # type: ignore[attr-defined]  # pyright: ignore

    @gen
    def parent():
        child = yield from fork(sleep(60))
        yield from sleep(0.001)
        yield from child.interrupt()

    await run(parent())
    scheduled = cast(list[asyncio.TimerHandle], loop._scheduled)  # type: ignore[attr-defined]  # pyright: ignore
    pending = [h for h in scheduled if not h.cancelled()]
    assert len(pending) <= before


async def test_children_interrupted_when_parent_ends():
    log: list[str] = []

    @gen
    def parent():
        yield from fork(never.pipe(ensuring(sync(lambda: log.append("child cleaned")))))
        yield from sleep(0.001)
        return "parent done"

    assert await run(parent()) == "parent done"
    assert log == ["child cleaned"]  # published only after the child finished


async def test_join_propagates_child_failure():
    @gen
    def parent():
        child = yield from fork(fail("child failed"))
        return (yield from child.join())

    ex = await run_exit(parent())
    assert isinstance(ex, Failure) and ex.cause.failures == ["child failed"]


async def test_async_resume_counts_once():
    from peffect import async_

    def register(resume: Resume[int, Never, Never]) -> None:
        resume(Success(1))
        resume(Success(2))

    assert await run(async_(register)) == 1


async def test_cancelling_the_task_interrupts_and_waits():
    log: list[str] = []
    eff = never.pipe(ensuring(sleep(0.01).flat_map(lambda _: sync(lambda: log.append("fin")))))
    task = asyncio.ensure_future(run(eff))
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == ["fin"]


async def test_interrupt_drops_typed_failure_from_skipped_handler():
    c = Cause.fail("x") + Cause.interrupt(1)
    assert c.without_fails().reasons == (Interrupt(1),)


async def test_fairness_other_tasks_progress_during_long_loop():
    ticks: list[int] = []

    async def ticker():
        for i in range(3):
            ticks.append(i)
            await asyncio.sleep(0)

    t = asyncio.ensure_future(ticker())
    eff: Effect[int, None, None] = succeed(0)
    for _ in range(20_000):
        eff = eff.flat_map(lambda n: succeed(n + 1))
    assert await run(eff) == 20_000
    await t
    assert ticks == [0, 1, 2]


async def test_attempt_wraps_coroutines_and_exceptions():
    from peffect import attempt

    async def ok():
        await asyncio.sleep(0)
        return 5

    async def bad():
        raise Boom()

    assert await run(attempt(ok)) == 5
    ex = await run_exit(attempt(bad, Boom))
    assert isinstance(ex, Failure) and isinstance(ex.cause.failures[0], Boom)


async def test_interrupt_while_waiting_for_children_lands():
    """Regression (Hypothesis, K7): the body had finished but a child was still running. An
    interrupt sent during that wait was parked in `_deferred` and never delivered, so the fiber
    published Success. Now the wait is an Async inside the run loop."""
    gate: list[Resume[None, Never, Never]] = []
    slow_cleanup: Effect[None, Never, Never] = async_(lambda resume: gate.append(resume))
    # the child starts (yield_now), then its slow cleanup holds the wait open
    body = fork(never.ensuring(slow_cleanup)).flat_map(lambda _: yield_now).as_("done")

    async def scenario() -> None:
        ex = await run_exit(fork_daemon(body))
        assert isinstance(ex, Success)
        target = ex.value
        for _ in range(3):  # let the body finish and start waiting on its child
            await asyncio.sleep(0)
        assert target.exit is None and target.children
        target.interrupt_unsafe(by=0)
        await asyncio.sleep(0)
        gate[0](UNIT)  # now let the child finish
        while target.exit is None:
            await asyncio.sleep(0)
        assert isinstance(target.exit, Failure) and target.exit.cause.is_interrupted

    await asyncio.wait_for(scenario(), timeout=2)
