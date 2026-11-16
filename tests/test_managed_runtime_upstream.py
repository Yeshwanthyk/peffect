"""ManagedRuntime, pinned Effect v4 289a53f7 (packages/effect/src/ManagedRuntime.ts:325-474).

Three sections, kept apart:

1. upstream: ports of test/ManagedRuntime.test.ts, cited by line. Not ported: the two Pool
   tests at :180-231 (peffect has no Pool).
2. source-derived: scenarios with no upstream test. Each expectation is the pinned vendor's own
   output: probes/scenarios.ts run under bun against vendor/effect-v4 at 289a53f, re-run for this
   unit and identical to probes/vendor.json. The citation names the ManagedRuntime.ts lines that
   produce it. Scenarios s14-s19 of that probe repeat upstream tests (:110, :141, :262, :128,
   :286, :312) and s13 repeats :90; none is repeated here. s08 needs a closed world and lives in
   tests/test_managed_runtime_interleavings.py.
3. seams: what a faithful port needs from the kernel. `await_exit(fiber)` is the runPromiseExit
   bridge (internal/effect.ts:5854-5866): an observer on the run fiber, no second fiber, and on
   cancellation `fiber.interrupt_unsafe()` with no interruptor (signal abort, :5785-5793), as
   kernel `run_exit` already does. Fiber must be weakly referenceable (WeakSet, :338-339).

Names under test (proposed; the coordinator owns exports): `peffect.managed_runtime.ManagedRuntime`
with `make(layer, *, memo_map=)`, `memo_map`, `context_effect`, `cached_context`,
`dispose_effect`, `run_fork(eff, *, scheduler=, uninterruptible=, on_fiber_start=)`, async
`run_exit`, `run`, `context`, `dispose`, `async with`; `peffect.fiber.await_exit`.

Adaptations: `runPromise` is `await rt.run(...)`, `pollUnsafe` is `.exit`, `forkChild` is
`fork(start_immediately=True)`, `forkDetach` is `fork_daemon`, `await using` is `async with`,
`Layer.empty` is `Layer.effect_discard(succeed(None))`. `Promise.race` against a 500ms timer is
`within(...)`: a bound so a hang fails the test, not a proof that no deadlock exists. `tick()` is
the probe's 15ms `setTimeout`. A defect message is compared with `str(defect)` because upstream
dies with the bare string "ManagedRuntime disposed" (:418).
"""

import asyncio
import gc
import weakref
from collections.abc import Coroutine
from typing import Any

import pytest

from peffect import (
    Context,
    Deferred,
    Die,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    Layer,
    Scope,
    Success,
    Tag,
    add_finalizer,
    die,
    ensuring,
    fork,
    fork_daemon,
    gen,
    managed_runtime,
    never,
    provide,
    run,
    run_exit,
    run_fork,
    scope,
    sleep,
    succeed,
    suspend,
    sync,
    uninterruptible,
    yield_now,
)
from peffect.core import WithFiber
from peffect.fiber import await_exit

ManagedRuntime = managed_runtime.ManagedRuntime

VOID = succeed(None)


class Str(Tag[str], key="string"): ...


def kind(f: Fiber[Any, Any] | None) -> str:
    """The probe's tag(): pending, ok, or the sorted set of failure reasons."""
    ex = None if f is None else f.exit
    if ex is None:
        return "pending"
    if isinstance(ex, Success):
        return "ok"
    reasons = {
        "interrupt"
        if isinstance(r, Interrupt)
        else f"die:{r.defect}"
        if isinstance(r, Die)
        else "fail"
        for r in ex.cause.reasons
    }
    return "+".join(sorted(reasons))


def value(f: Fiber[Any, Any]) -> object:
    """The success value, or kind() when the fiber did not succeed (Exit has no __eq__)."""
    ex = f.exit
    return ex.value if isinstance(ex, Success) else kind(f)


def interrupters(ex: Exit[Any, Any] | None) -> list[int | None]:
    assert isinstance(ex, Failure)
    return [r.by for r in ex.cause.reasons if isinstance(r, Interrupt)]


def gate() -> Deferred[None, Any]:
    return Deferred[None, Any].make_unsafe()


def open_(d: Deferred[None, Any]) -> None:
    d.done_unsafe(Success(None))


def ev(events: list[str], s: str):
    return sync(lambda: events.append(s))


def fin(events: list[str], s: str):
    return add_finalizer(lambda _: ev(events, s))


async def tick(ms: int = 15) -> None:
    await asyncio.sleep(ms / 1000)


async def within[T](coro: Coroutine[Any, Any, T]) -> T:
    return await asyncio.wait_for(coro, 0.5)


DISPOSED = "die:ManagedRuntime disposed"


# == upstream ==================================================================================


async def test_memoizes_the_layer_build():
    """ManagedRuntime.test.ts:6."""
    count = [0]
    rt = ManagedRuntime.make(Layer.effect_discard(sync(lambda: count.__setitem__(0, count[0] + 1))))
    await rt.run(VOID)
    await rt.run(VOID)
    await rt.dispose()
    assert count[0] == 1


async def test_provides_context():
    """ManagedRuntime.test.ts:18."""
    rt = ManagedRuntime.make(Layer.succeed(Str, "test"))
    result = await rt.run(Str().use())
    await rt.dispose()
    assert result == "test"


async def test_allows_sharing_a_memo_map():
    """ManagedRuntime.test.ts:27."""
    count = [0]
    layer = Layer.effect_discard(sync(lambda: count.__setitem__(0, count[0] + 1)))
    rt_a = ManagedRuntime.make(layer)
    rt_b = ManagedRuntime.make(layer, memo_map=rt_a.memo_map)
    await rt_a.run(VOID)
    await rt_b.run(VOID)
    await rt_a.dispose()
    await rt_b.dispose()
    assert count[0] == 1


def test_can_be_built_synchronously():
    """ManagedRuntime.test.ts:41. Harness adaptation: peffect has no runSync, so the root fiber's
    synchronous first slice stands in; no event loop is running, so nothing async could finish."""
    rt = ManagedRuntime.make(Layer.succeed(Str, "test"))
    services = run_fork(rt.context_effect).exit
    assert isinstance(services, Success)
    read = run_fork(provide(services.value)(Str().use())).exit
    assert isinstance(read, Success) and read.value == "test"


async def test_supports_async_with():
    """ManagedRuntime.test.ts:50 (`await using`, Symbol.asyncDispose :398-400)."""
    count = [0]
    layer = Layer.effect_discard(
        add_finalizer(lambda _: sync(lambda: count.__setitem__(0, count[0] + 1)))
    )
    async with ManagedRuntime.make(layer) as rt:
        await rt.run(VOID)
        assert count[0] == 0
    assert count[0] == 1


async def test_fibers_are_interrupted_on_dispose():
    """ManagedRuntime.test.ts:65."""
    rt = ManagedRuntime.make(Layer.effect_discard(VOID))
    fiber = rt.run_fork(never)
    await rt.dispose()
    assert kind(fiber) == "interrupt"


async def test_dispose_effect_inside_run_after_an_async_boundary_releases_the_layer():
    """ManagedRuntime.test.ts:74."""
    released = [False]
    rt = ManagedRuntime.make(
        Layer.effect_discard(add_finalizer(lambda _: sync(lambda: released.__setitem__(0, True))))
    )
    await within(rt.run(sleep(1).pipe(ensuring(rt.dispose_effect))))
    assert released == [True]


async def test_dispose_effect_inside_an_immediately_started_request_child_releases_the_layer():
    """ManagedRuntime.test.ts:90."""
    released = [False]
    rt = ManagedRuntime.make(
        Layer.effect_discard(add_finalizer(lambda _: sync(lambda: released.__setitem__(0, True))))
    )

    @gen
    def program():
        yield from sleep(1)
        child = yield from fork(rt.dispose_effect, start_immediately=True)
        yield from child.join()

    await within(rt.run(program()))
    assert released == [True]


async def test_a_request_that_disposes_the_runtime_before_its_first_async_step_keeps_running():
    """ManagedRuntime.test.ts:110."""
    released = [False]
    rt = ManagedRuntime.make(
        Layer.effect_discard(
            add_finalizer(
                lambda _: yield_now.flat_map(lambda _: sync(lambda: released.__setitem__(0, True)))
            )
        )
    )
    await rt.run(VOID)

    @gen
    def program():
        yield from rt.dispose_effect
        yield from yield_now
        return "finished"

    assert (await rt.run(program()), released) == ("finished", [True])


async def test_a_layer_build_that_disposes_the_runtime_is_interrupted():
    """ManagedRuntime.test.ts:128."""
    box: list[ManagedRuntime[Any, Any]] = []
    rt = ManagedRuntime.make(
        Layer.effect_discard(suspend(lambda: box[0].dispose_effect).flat_map(lambda _: never))
    )
    box.append(rt)
    assert isinstance(await within(run_exit(rt.context_effect)), Failure)


async def test_detached_fiber_disposal_still_interrupts_and_awaits_its_former_request():
    """ManagedRuntime.test.ts:141."""
    events: list[str] = []
    rt = ManagedRuntime.make(Layer.effect_discard(fin(events, "layer released")))

    @gen
    def cleanup():
        yield from sleep(1)
        events.append("former request cleaned up")

    @gen
    def program():
        g = yield from Deferred[None].make()
        started = yield from Deferred[Fiber[None, Any]].make()

        @gen
        def request():
            detached = yield from fork_daemon(g.await_().pipe(ensuring(rt.dispose_effect)))
            yield from started.succeed(detached)
            return (yield from never)

        rt.run_fork(request().pipe(ensuring(cleanup())))
        detached = yield from started.await_()
        yield from g.succeed(None)
        yield from detached.join()

    await within(run(program()))
    assert events == ["former request cleaned up", "layer released"]


async def test_overlapping_disposal_with_the_exported_context_waits_for_layer_release():
    """ManagedRuntime.test.ts:233."""
    events: list[str] = []

    @gen
    def finalizer(started: Deferred[None, Any], release: Deferred[None, Any]):
        yield from started.succeed(None)
        yield from release.await_()
        events.append("layer released")

    @gen
    def program():
        started = yield from Deferred[None].make()
        release = yield from Deferred[None].make()
        rt = ManagedRuntime.make(
            Layer.effect_discard(add_finalizer(lambda _: finalizer(started, release)))
        )
        context = yield from rt.context_effect
        first = yield from fork(rt.dispose_effect, start_immediately=True)
        yield from started.await_()
        second = yield from fork(
            provide(context)(rt.dispose_effect).flat_map(
                lambda _: ev(events, "second disposeEffect done")
            ),
            start_immediately=True,
        )
        yield from release.succeed(None)
        yield from first.join()
        yield from second.join()

    await within(run(program()))
    assert events == ["layer released", "second disposeEffect done"]


async def test_a_layer_build_interrupted_by_dispose_can_dispose_the_runtime():
    """ManagedRuntime.test.ts:262."""
    cleaned = [False]
    started = asyncio.Event()
    box: list[ManagedRuntime[Any, Any]] = []

    def start():
        started.set()
        return never

    rt = ManagedRuntime.make(
        Layer.effect_discard(
            suspend(start).pipe(
                ensuring(
                    suspend(
                        lambda: box[0].dispose_effect.flat_map(
                            lambda _: sync(lambda: cleaned.__setitem__(0, True))
                        )
                    )
                )
            )
        )
    )
    box.append(rt)
    rt.run_fork(VOID)
    await started.wait()
    await within(rt.dispose())
    assert cleaned == [True]


async def test_a_layer_finalizer_shared_through_a_memo_map_can_dispose_the_runtime_releasing_it():
    """ManagedRuntime.test.ts:286."""
    released = [False]
    box: list[ManagedRuntime[Any, Any]] = []
    layer = Layer.effect_discard(
        add_finalizer(
            lambda _: box[0].dispose_effect.flat_map(
                lambda _: sync(lambda: released.__setitem__(0, True))
            )
        )
    )
    rt_a = ManagedRuntime.make(layer)
    rt_b = ManagedRuntime.make(layer, memo_map=rt_a.memo_map)
    box.append(rt_b)
    await rt_a.run(VOID)
    await rt_b.run(VOID)
    await rt_a.dispose()
    await within(rt_b.dispose())
    assert released == [True]


async def test_runtimes_whose_layer_finalizers_dispose_each_other_can_be_disposed():
    """ManagedRuntime.test.ts:312."""
    a: list[ManagedRuntime[Any, Any]] = []
    b: list[ManagedRuntime[Any, Any]] = []
    rt_a = ManagedRuntime.make(Layer.effect_discard(add_finalizer(lambda _: b[0].dispose_effect)))
    rt_b = ManagedRuntime.make(Layer.effect_discard(add_finalizer(lambda _: a[0].dispose_effect)))
    a.append(rt_a)
    b.append(rt_b)
    await rt_a.run(VOID)
    await rt_b.run(VOID)
    await within(rt_a.dispose())


async def test_completes_sequential_layer_finalizers_when_dispose_effect_is_interrupted():
    """ManagedRuntime.test.ts:330."""

    @gen
    def program():
        started = yield from Deferred[None].make()
        release = yield from Deferred[None].make()
        ran: list[str] = []

        @gen
        def flush_logs():
            yield from started.succeed(None)
            yield from release.await_()
            ran.append("flush logs")

        @gen
        def build():
            s = yield from scope
            yield from Scope.add_finalizer(s, sync(lambda: ran.append("close database")))
            yield from Scope.add_finalizer(s, flush_logs())

        rt = ManagedRuntime.make(Layer.effect_discard(build()))
        yield from rt.context_effect
        closing = yield from fork(rt.dispose_effect, start_immediately=True)
        yield from started.await_()
        interrupting = yield from fork(closing.interrupt(), start_immediately=True)
        yield from release.succeed(None)
        yield from interrupting.join()
        yield from closing.await_()
        yield from rt.dispose_effect
        return ran

    assert await within(run(program())) == ["flush logs", "close database"]


# == source-derived (vendor output of probes/scenarios.ts) =====================================


async def test_source_concurrent_first_requests_share_one_build():
    """s01. contextEffect :361-383: the first request starts the one build fiber; later ones
    await it. Requests run in start order once the build finishes; release is last."""
    events: list[str] = []
    builds = [0]
    g = gate()

    @gen
    def build():
        builds[0] += 1
        events.append("build start")
        yield from fin(events, "released")
        yield from g.await_()
        events.append("build end")

    rt = ManagedRuntime.make(Layer.effect_discard(build()))
    fs = [rt.run_fork(sync(lambda i=i: (events.append(f"req {i}"), i)[1])) for i in (1, 2, 3)]
    assert [kind(f) for f in fs] == ["pending"] * 3
    open_(g)
    assert [value(f) for f in fs] == [1, 2, 3]
    await rt.dispose()
    assert builds[0] == 1
    assert events == ["build start", "build end", "req 1", "req 2", "req 3", "released"]


async def test_source_dispose_during_build_interrupts_waiters_then_dies_later_requests():
    """s02. disposeEffect :401-424: the claim swaps contextEffect for die (:418) and closes the
    scope, which interrupts the build fiber (runIn fiberScope) and its waiters synchronously."""
    events: list[str] = []
    g = gate()

    @gen
    def build():
        events.append("build start")
        yield from fin(events, "released")
        yield from g.await_()
        events.append("build end")

    rt = ManagedRuntime.make(Layer.effect_discard(build()))
    f1, f2 = rt.run_fork(succeed(1)), rt.run_fork(succeed(2))
    w = run_fork(rt.context_effect)
    d = run_fork(rt.dispose_effect)
    assert [kind(f1), kind(f2), kind(w), kind(d)] == ["interrupt", "interrupt", "interrupt", "ok"]
    await tick()
    assert kind(rt.run_fork(succeed(1))) == DISPOSED
    assert kind(run_fork(rt.dispose_effect)) == "ok"
    assert events == ["build start", "released"]


async def test_source_dispose_during_uninterruptible_build_waits_for_it():
    """s03. Closing fiberScope awaits the build fiber (runIn, :375-378). Its uninterruptible
    region finishes, then the pending interrupt stops it before the tap (:364-369), so the
    cachedContext the claim cleared (:419) stays cleared."""
    events: list[str] = []
    g = gate()

    @gen
    def build():
        yield from fin(events, "released")
        events.append("acquiring")
        yield from g.await_()
        events.append("acquired")

    rt = ManagedRuntime.make(Layer.effect_discard(uninterruptible(build())))
    f1 = rt.run_fork(succeed(1))
    d = run_fork(rt.dispose_effect)
    assert [kind(f1), kind(d)] == ["interrupt", "pending"]
    open_(g)
    assert [kind(f1), kind(d)] == ["interrupt", "ok"]
    await tick()
    assert rt.cached_context is None
    assert kind(rt.run_fork(ev(events, "late ran"))) == DISPOSED
    assert events == ["acquiring", "acquired", "released"]


async def test_source_waiting_disposers_stay_interruptible_the_closing_one_does_not():
    """s04. The claiming disposer runs the close under fiberEnterUninterruptibleUnsafe (:410);
    a later disposer only awaits the deferred (:407), interruptibly unless the caller masks."""
    events: list[str] = []
    release = gate()

    @gen
    def finalizer():
        events.append("fin start")
        yield from release.await_()
        events.append("layer released")

    rt = ManagedRuntime.make(Layer.effect_discard(add_finalizer(lambda _: finalizer())))
    await rt.run_exit(VOID)
    first = run_fork(rt.dispose_effect)
    second = run_fork(rt.dispose_effect)
    third = run_fork(uninterruptible(rt.dispose_effect))
    assert [kind(first), kind(second), kind(third)] == ["pending"] * 3
    second.interrupt_unsafe()
    third.interrupt_unsafe()
    first.interrupt_unsafe()
    assert [kind(first), kind(second), kind(third)] == ["pending", "interrupt", "pending"]
    open_(release)
    assert [kind(first), kind(second), kind(third)] == ["interrupt"] * 3
    await tick()
    assert kind(run_fork(rt.dispose_effect)) == "ok"
    assert events == ["fin start", "layer released"]


async def test_source_a_finalizer_defect_fails_the_closer_but_completes_the_waiters():
    """s05. The closer gets the close's Exit; `ensuring(Deferred.done(done, Exit.void))` (:422)
    completes the deferred with void regardless, so waiters and later disposers succeed."""
    events: list[str] = []
    g = gate()

    @gen
    def b_fin():
        yield from g.await_()
        events.append("B dying")
        yield from die(Exception("boom"))

    @gen
    def build():
        yield from fin(events, "A released")
        yield from add_finalizer(lambda _: b_fin())

    rt = ManagedRuntime.make(Layer.effect_discard(build()))
    await rt.run_exit(VOID)
    r = rt.run_fork(never.pipe(ensuring(ev(events, "R cleanup"))))
    first = run_fork(rt.dispose_effect)
    waiter = run_fork(rt.dispose_effect)
    assert [kind(r), kind(first), kind(waiter)] == ["interrupt", "pending", "pending"]
    open_(g)
    assert [kind(r), kind(first), kind(waiter)] == ["interrupt", "die:boom", "ok"]
    await tick()
    assert kind(run_fork(rt.dispose_effect)) == "ok"
    assert events == ["R cleanup", "B dying", "A released"]


async def test_source_a_build_that_disposes_and_continues_caches_its_context():
    """s06, upstream quirk: the build fiber disposes inside its first slice (exempt as the
    disposing fiber, :414-417) and keeps going; its tap (:364-369) then sets cachedContext after
    the claim cleared it (:419). A later synchronous request runs on the disposed runtime; an
    async one is interrupted when its start hook runs it in the closed fiberScope (:341-345)."""
    events: list[str] = []
    box: list[ManagedRuntime[Any, Any]] = []

    @gen
    def build():
        yield from fin(events, "released")
        yield from suspend(lambda: box[0].dispose_effect)
        events.append("build continues")

    rt = ManagedRuntime.make(Layer.effect_discard(build()))
    box.append(rt)
    c = run_fork(rt.context_effect)
    assert rt.cached_context is not None
    late_sync = rt.run_fork(sync(lambda: (events.append("late ran"), 1)[1]))
    late_async = rt.run_fork(never)
    assert isinstance(c.exit, Success) and isinstance(c.exit.value, Context)
    assert (value(late_sync), kind(late_async)) == (1, "interrupt")
    await tick()
    assert events == ["released", "build continues", "late ran"]


async def test_source_a_reentrant_first_build_acquires_the_layer_once():
    """s07. A request forked synchronously from inside the first build sees no buildFiber yet
    (assigned after runFork returns, :362-381), so it starts its own build fiber; the shared
    MemoMap still acquires the layer once. The inner request finishes before the outer."""
    events: list[str] = []
    builds = [0]
    inner: list[Fiber[Any, Any]] = []
    box: list[ManagedRuntime[Any, Any]] = []

    def body() -> None:
        builds[0] += 1
        inner.append(box[0].run_fork(sync(lambda: (events.append("inner ran"), "inner")[1])))
        events.append("inner after fork: " + kind(inner[0]))

    rt = ManagedRuntime.make(Layer.effect_discard(sync(body)))
    box.append(rt)
    outer = rt.run_fork(sync(lambda: (events.append("outer ran"), "outer")[1]))
    assert (value(outer), value(inner[0])) == ("outer", "inner")
    await tick()
    assert value(rt.run_fork(succeed("again"))) == "again"
    await rt.dispose()
    assert builds[0] == 1
    assert events == ["inner after fork: pending", "inner ran", "outer ran"]


async def test_source_managed_and_descendant_disposers_return_without_waiting():
    """s09. A disposer that is a managed fiber, or descends from one through parent links
    (:404-406), returns at once instead of waiting on the close that is interrupting it."""
    events: list[str] = []
    release = gate()
    rt = ManagedRuntime.make(
        Layer.effect_discard(
            add_finalizer(
                lambda _: release.await_().flat_map(lambda _: ev(events, "layer released"))
            )
        )
    )
    await rt.run_exit(VOID)
    r = rt.run_fork(
        never.pipe(ensuring(rt.dispose_effect.flat_map(lambda _: ev(events, "R dispose returned"))))
    )

    @gen
    def cleanup():
        child = yield from fork(
            rt.dispose_effect.flat_map(lambda _: ev(events, "child dispose returned")),
            start_immediately=True,
        )
        yield from child.await_()

    r2 = rt.run_fork(never.pipe(ensuring(cleanup())))
    x = run_fork(rt.dispose_effect)
    assert [kind(r), kind(r2), kind(x)] == ["interrupt", "interrupt", "pending"]
    assert sorted(events) == ["R dispose returned", "child dispose returned"]
    open_(release)
    await tick()
    assert kind(x) == "ok" and events[-1] == "layer released"


async def test_source_a_captured_context_effect_still_builds_after_dispose():
    """s10, upstream quirk: disposal replaces the field (:418), not an effect already read from
    it. The captured effect builds into the closed layer scope (its finalizer runs at once) and
    its tap (:364-369) re-sets cachedContext, after which rt.run_fork runs again. So there is
    no universal terminal state after disposal."""
    events: list[str] = []
    builds = [0]

    @gen
    def build():
        builds[0] += 1
        yield from fin(events, "released")
        events.append("built")

    rt = ManagedRuntime.make(Layer.effect_discard(build()))
    captured = rt.context_effect
    await rt.dispose()
    assert kind(rt.run_fork(succeed(1))) == DISPOSED
    late = run_fork(captured)
    assert isinstance(late.exit, Success) and isinstance(late.exit.value, Context)
    assert rt.cached_context is not None
    assert value(rt.run_fork(succeed(1))) == 1
    await tick()
    assert builds[0] == 1
    assert events == ["released", "built"]


async def test_source_a_layer_finalizer_that_redisposes_returns():
    """s11. The close runs with DisposingRuntimes holding this runtime (:421, :403), so a
    finalizer's own disposeEffect returns instead of awaiting the close it is part of."""
    events: list[str] = []
    box: list[ManagedRuntime[Any, Any]] = []
    rt = ManagedRuntime.make(
        Layer.effect_discard(
            add_finalizer(
                lambda _: suspend(lambda: box[0].dispose_effect).flat_map(
                    lambda _: ev(events, "finalizer redispose returned")
                )
            )
        )
    )
    box.append(rt)
    await rt.run_exit(VOID)
    assert kind(run_fork(rt.dispose_effect)) == "ok"
    assert events == ["finalizer redispose returned"]


async def test_source_a_request_disposing_in_its_finalizer_completes():
    """s12. The disposing request is itself managed: its key is removed from fiberScope (:414-417)
    before the close, so the close interrupts only the other request and does not await self."""
    events: list[str] = []
    rt = ManagedRuntime.make(Layer.effect_discard(fin(events, "layer released")))
    other = rt.run_fork(never.pipe(ensuring(ev(events, "other cleanup"))))
    me = rt.run_fork(yield_now.pipe(ensuring(rt.dispose_effect)).as_("self done"))
    await tick()
    assert (kind(other), value(me)) == ("interrupt", "self done")
    assert events == ["other cleanup", "layer released"]


async def test_source_a_request_started_while_closing_dies_and_the_close_completes():
    """s20. After the claim, a new request reads the die contextEffect (:418)."""
    events: list[str] = []
    release = gate()
    rt = ManagedRuntime.make(
        Layer.effect_discard(
            add_finalizer(
                lambda _: release.await_().flat_map(lambda _: ev(events, "layer released"))
            )
        )
    )
    await rt.run_exit(VOID)
    x = run_fork(rt.dispose_effect)
    late = rt.run_fork(ev(events, "late ran"))
    assert [kind(x), kind(late)] == ["pending", DISPOSED]
    open_(release)
    await tick()
    assert [kind(x), kind(late)] == ["ok", DISPOSED]
    assert events == ["layer released"]


# == seams =====================================================================================


async def test_seam_await_exit_returns_the_run_fibers_exit_without_another_fiber():
    """runPromiseExitWith :5854-5866: `runFork` then `fiber.addObserver(resolve)`."""
    started: list[Fiber[Any, Any]] = []
    fiber = run_fork(yield_now.as_(7), on_fiber_start=started.append)
    ex = await await_exit(fiber)
    assert ex is fiber.exit and value(fiber) == 7
    assert started == [fiber]


async def _cancel_while_running(start: Any) -> tuple[list[Fiber[Any, Any]], list[str]]:
    """Start a request through `start(eff, hook)`, cancel the awaiting task, and record what the
    hook saw and the order of cleanup against the CancelledError."""
    events: list[str] = []
    seen: list[Fiber[Any, Any]] = []
    eff = never.pipe(ensuring(sleep(1).flat_map(lambda _: ev(events, "cleanup"))))
    task = asyncio.create_task(start(eff, seen.append))
    await tick()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        events.append("cancelled")
    return seen, events


async def _cancel_twice(start: Any) -> tuple[list[Fiber[Any, Any]], list[str], bool]:
    """Cancel the awaiting task, then cancel it again while it waits for the fiber's cleanup.
    Returns what the hook saw, the event order, and whether the fiber was still running when
    the task ended."""
    events: list[str] = []
    seen: list[Fiber[Any, Any]] = []
    g = gate()
    eff = never.pipe(ensuring(g.await_().flat_map(lambda _: ev(events, "cleanup"))))
    task = asyncio.create_task(start(eff, seen.append))
    await tick()
    task.cancel()
    await tick()
    assert not task.done(), "harness: the first cancel must wait for the gated cleanup"
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        events.append("cancelled")
    running = seen[0].exit is None
    open_(g)
    await tick()
    return seen, events, running


def _kernel(eff: Any, hook: Any) -> Coroutine[Any, Any, Exit[Any, Any]]:
    """Kernel run_exit with the hook fed from inside the run fiber (it takes no hook)."""
    return run_exit(WithFiber(lambda fiber: sync(lambda: hook(fiber)).flat_map(lambda _: eff)))


def _await_exit(eff: Any, hook: Any) -> Coroutine[Any, Any, Exit[Any, Any]]:
    return await_exit(run_fork(eff, on_fiber_start=hook))


BRIDGES = ["kernel run_exit", "await_exit(run_fork)", "ManagedRuntime.run_exit"]


async def _bridge(name: str) -> tuple[Any, ManagedRuntime[Any, Any] | None]:
    if name == "kernel run_exit":
        return _kernel, None
    if name == "await_exit(run_fork)":
        return _await_exit, None
    rt = ManagedRuntime.make(Layer.effect_discard(VOID))
    await rt.run(VOID)
    return (lambda eff, hook: rt.run_exit(eff, on_fiber_start=hook)), rt


@pytest.mark.parametrize("name", BRIDGES)
async def test_seam_one_cancel_interrupts_the_run_fiber_and_waits_for_its_cleanup(name: str):
    """Signal abort :5785-5793 calls `fiber.interruptUnsafe()` on the run fiber itself, no
    interruptor. runPromiseExitWith (:5854) and ManagedRuntime.runPromiseExit (:450-454) await
    that fiber through an observer, so all three bridges agree: one fiber seen by the hook,
    interrupted with no interruptor, and the task re-raises only after cleanup. A bridge that
    awaits through a second fiber would record that fiber's id instead."""
    start, rt = await _bridge(name)
    seen, events = await _cancel_while_running(start)
    assert len(seen) == 1 and interrupters(seen[0].exit) == [None]
    assert events == ["cleanup", "cancelled"]
    if rt is not None:
        await rt.dispose()


@pytest.mark.parametrize("name", BRIDGES)
async def test_seam_a_second_cancel_stops_waiting_but_not_the_cleanup(name: str):
    """Pinned to kernel run_exit (fiber.py run_exit), which the bridges must match; upstream
    has no second abort to port (an AbortSignal fires once). The first cancel interrupts once;
    a second cancel during the cleanup wait ends the task at once, and the fiber, still
    running, finishes its cleanup afterwards with that single interrupt."""
    start, rt = await _bridge(name)
    seen, events, running = await _cancel_twice(start)
    assert running, "the second cancel must not wait for the cleanup"
    assert events == ["cancelled", "cleanup"]
    assert len(seen) == 1 and interrupters(seen[0].exit) == [None]
    if rt is not None:
        await rt.dispose()


async def test_seam_a_finished_request_is_not_retained_by_the_runtime():
    """managedFibers / disposingFibers are WeakSets (:338-339) and runIn detaches an exited fiber
    from fiberScope, so a finished request is collectable while the runtime lives."""
    rt = ManagedRuntime.make(Layer.effect_discard(VOID))
    fiber = rt.run_fork(yield_now)
    await await_exit(fiber)
    ref = weakref.ref(fiber)
    del fiber
    await tick()
    gc.collect()
    assert ref() is None
    await rt.dispose()
