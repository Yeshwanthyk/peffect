"""When a woken fiber runs: resume, interrupt and root start are synchronous in Effect v4.
Upstream is pinned at vendor/effect-v4 commit 289a53f (Effect v4.1.4).

Upstream has no test whose subject is resume timing; TestClock depends on it (TestClock.ts
:420-447 wakes sleepers inside `runTracked`, so they must run before it returns). Every test
here is source-derived and labelled with the internal/effect.ts lines it reads. Each expected
value was also measured by running the same program against the pinned source under Node 22
(scratch probes upstream_resume.ts, upstream_deferred.ts and upstream_scheduler.ts, kept with
this lane's notes); the measured upstream output is quoted in each docstring.

Adaptations used throughout:
- `Effect.callback` is `async_`, `Effect.forkChild` is `fork`, `Fiber.await` is `await_()`,
  `fiber.pollUnsafe() !== undefined` is `fiber.exit is not None`, `Effect.interrupt` is
  `fail_cause(Cause.interrupt())` and `Exit.hasInterrupts` is `has_interrupts`.
- Probes call resume/interrupt from a sibling fiber's `sync`, as the upstream probes did.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Never

from peffect import (
    Cause,
    Deferred,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    async_,
    fail_cause,
    fork,
    gen,
    never,
    run,
    run_exit,
    succeed,
    sync,
    yield_now,
)

type Resume = Callable[[Effect[None, Never, Never]], None]


def has_interrupts(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def parked(resumes: list[Resume], then: Effect[None, Never, Never]) -> Effect[None, Never, Never]:
    """Park on an Async whose resume is handed out, then run ``then``."""

    def register(resume: Resume) -> None:
        resumes.append(resume)

    return async_(register).flat_map(lambda _: then)


async def test_s1_resume_runs_the_parked_fiber_before_resume_returns():
    """Source-derived S1: the Async callback's resume calls `fiber.evaluate` directly
    (internal/effect.ts:1220-1226, callbackOptions). Measured upstream:
    ["A continues", "resume returned"]."""
    order: list[str] = []
    resumes: list[Resume] = []

    def resume_then_log() -> None:
        resumes[0](succeed(None))
        order.append("resume returned")

    @gen
    def program():
        a: Fiber[None, Never] = yield from fork(
            parked(resumes, sync(lambda: order.append("A continues"))), start_immediately=True
        )
        yield from sync(resume_then_log)
        yield from a.await_()

    await run(program())
    assert order == ["A continues", "resume returned"]


async def test_s10_interrupting_a_parked_fiber_publishes_its_exit_before_returning():
    """Source-derived S10: interruptUnsafe on a parked, interruptible fiber runs the interrupt
    through `evaluate` at once (internal/effect.ts:605-611). Measured upstream: [true]."""
    out: list[bool] = []

    @gen
    def program():
        f: Fiber[Never, Never] = yield from fork(never, start_immediately=True)

        def interrupt_and_poll() -> None:
            f.interrupt_unsafe()
            out.append(f.exit is not None)

        yield from sync(interrupt_and_poll)
        yield from f.await_()

    await run(program())
    assert out == [True]


async def test_s10_interrupt_then_resume_of_one_park_runs_the_interrupt_only():
    """Source-derived (P4): the first of interrupt and resume claims the park; a late resume is
    ignored (internal/effect.ts:605-611, :1213-1219). Measured upstream: [0, true]."""
    runs: list[int] = []
    resumes: list[Resume] = []

    @gen
    def program():
        f: Fiber[None, Never] = yield from fork(
            parked(resumes, sync(lambda: runs.append(1))), start_immediately=True
        )

        def interrupt_then_resume() -> None:
            f.interrupt_unsafe()
            resumes[0](succeed(None))

        yield from sync(interrupt_then_resume)
        return (yield from f.await_())

    ex = await run(program())
    assert (len(runs), has_interrupts(ex)) == (0, True)


async def test_l1_interrupting_a_yielded_fiber_exits_now_and_drops_its_queued_step():
    """Source-derived L1 (P3): a fiber waiting on its own yieldNow task holds `_yielded`, a
    canceller that marks that task resumed, so interruptUnsafe evaluates the interrupt now
    (internal/effect.ts:605-611, yieldNow :773-781) and the queued task later returns at once.
    Measured upstream: [true, 0].

    The parent's one yield_now is not a timing guess: the child's task was queued first and
    the loop runs callbacks in FIFO order, so by the time the parent continues, the stale task
    has had its turn."""
    runs: list[int] = []
    out: list[object] = []

    @gen
    def program():
        f: Fiber[None, Never] = yield from fork(
            yield_now.flat_map(lambda _: sync(lambda: runs.append(1))), start_immediately=True
        )

        def interrupt_and_poll() -> None:
            f.interrupt_unsafe()
            out.append(f.exit is not None)

        yield from sync(interrupt_and_poll)
        yield from yield_now
        out.append(len(runs))
        out.append(has_interrupts((yield from f.await_())))

    await run(program())
    assert out == [True, 0, True]


async def test_s1_deferred_waiters_resumed_with_an_interrupt_die_inside_done_unsafe():
    """Source-derived S1 through Deferred: doneUnsafe clears `resumes` first because "a waiter
    resumed with an interrupt cause dies synchronously inside `resume`" (Deferred.ts:881-895).
    Measured upstream: [true, true, true, true]."""
    out: list[bool] = []

    @gen
    def program():
        d = Deferred[None].make_unsafe()
        a: Fiber[None, Never] = yield from fork(d.await_(), start_immediately=True)
        b: Fiber[None, Never] = yield from fork(d.await_(), start_immediately=True)

        def complete_and_poll() -> None:
            d.done_unsafe(fail_cause(Cause.interrupt()))
            out.extend([a.exit is not None, b.exit is not None])

        yield from sync(complete_and_poll)
        out.append(has_interrupts((yield from a.await_())))
        out.append(has_interrupts((yield from b.await_())))

    await run(program())
    assert out == [True, True, True, True]


async def test_a7_root_fiber_runs_its_synchronous_prefix_when_run_starts():
    """Source-derived A7: runForkWith evaluates the root fiber before returning
    (internal/effect.ts:5777). Measured upstream, with a setImmediate queued first:
    ["effect prefix", "runPromise returned", "queued callback"].

    Platform adaptation: `run_exit` is a coroutine, so "before runFork returns" becomes "in
    the step that starts run_exit, before callbacks already queued on the loop". Whether the
    awaiting caller also resumes before them is not pinned: asyncio decides that."""
    loop = asyncio.get_running_loop()
    order: list[str] = []
    queued: asyncio.Future[None] = loop.create_future()

    def queued_callback() -> None:
        order.append("queued callback")
        queued.set_result(None)

    loop.call_soon(queued_callback)
    await run_exit(sync(lambda: order.append("effect prefix")))
    await queued
    assert order[0] == "effect prefix"
