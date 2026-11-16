"""Bounded child-process probes for nested wakes and stale queued steps."""

import asyncio
import sys
from typing import Never
from unittest.mock import patch

import peffect.fiber as fiber_module
from peffect import Deferred, fork, gen, run_exit, succeed, yield_now
from peffect.core import Effect, Failure, Resume, Success, async_, ensuring, sync, uninterruptible
from peffect.fiber import Fiber, run_fork


async def stale_finalizer() -> None:
    wake: list[Resume[int, Never, Never]] = []
    cleanup: list[Resume[None, Never, Never]] = []
    seen: list[str] = []
    suspended: Effect[int, Never, Never] = async_(lambda resume: wake.append(resume))
    finalizer: Effect[None, Never, Never] = async_(lambda resume: cleanup.append(resume))
    target = run_fork(
        suspended.pipe(ensuring(finalizer))
    )

    def race() -> None:
        wake[0](succeed(7))  # queues a fresh Park at the cutoff
        target.interrupt_unsafe()  # claims that Park and starts the masked finalizer

    run_fork(sync(race))
    await asyncio.sleep(0)
    assert target.exit is None, target.exit
    assert len(cleanup) == 1
    assert seen == []
    cleanup[0](succeed(None))
    ex = await fiber_module.await_exit(target)
    assert isinstance(ex, Failure) and ex.cause.is_interrupted
    print("stale-finalizer: parked until cleanup wake")


async def masked_exit() -> None:
    wake: list[Resume[None, Never, Never]] = []
    seen: list[str] = []
    suspended: Effect[None, Never, Never] = async_(lambda resume: wake.append(resume))
    target = run_fork(
        uninterruptible(
            suspended.flat_map(
                lambda _: sync(lambda: seen.append("critical exit"))
            )
        )
    )

    def race() -> None:
        wake[0](succeed(None))
        target.interrupt_unsafe()

    run_fork(sync(race))
    ex = await fiber_module.await_exit(target)
    assert seen == ["critical exit"], seen
    assert isinstance(ex, Failure) and ex.cause.is_interrupted
    print("masked-exit: critical region finished before interrupt")


@gen
def chain(n: int):
    cells: list[Deferred[int, object]] = []
    for _ in range(n + 1):
        cells.append((yield from Deferred[int, object].make()))

    def link(i: int):
        @gen
        def work():
            value = yield from cells[i].await_()
            yield from cells[i + 1].succeed(value + 1)

        return work()

    for i in range(n):
        yield from fork(link(i))
    yield from yield_now
    yield from cells[0].succeed(0)
    return (yield from cells[n].await_())


async def sweep() -> None:
    sys.setrecursionlimit(350)
    for n in (80, 170, 400, 1000):
        result = await asyncio.wait_for(run_exit(chain(n)), 3)
        assert isinstance(result, Success) and result.value == n
    wake: list[Resume[int, Never, Never]] = []
    suspended: Effect[int, Never, Never] = async_(lambda resume: wake.append(resume))
    fibers: list[Fiber[int, Never]] = [run_fork(suspended)]
    for _ in range(400):
        fibers.append(run_fork(fibers[-1].join().map(lambda x: x + 1)))
    wake[0](succeed(0))
    joined = await fiber_module.await_exit(fibers[-1])
    assert isinstance(joined, Success) and joined.value == 400
    print("recursion-sweep: Deferred 80,170,400,1000 and join 400 at limit 350")


async def main() -> None:
    if sys.argv[1] == "stale":
        with patch.object(fiber_module, "_MAX_WAKE_DEPTH", 1):
            await stale_finalizer()
    elif sys.argv[1] == "masked":
        with patch.object(fiber_module, "_MAX_WAKE_DEPTH", 1):
            await masked_exit()
    else:
        await sweep()


asyncio.run(main())
