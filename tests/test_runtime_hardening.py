"""Public outcomes at the kernel's wake, publication and Python context boundaries."""

import asyncio
import contextvars
import gc
import pickle
import subprocess
import sys
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Never

import pytest

from peffect import Queue, Semaphore, fork, gen, run, run_exit, succeed, yield_now
from peffect.cause import Cause, Die, Done, Fail
from peffect.core import (
    Effect,
    Exit,
    Failure,
    Resume,
    Success,
    async_,
    catch_tag,
    ensuring,
    from_async,
    sync,
    uninterruptible,
)
from peffect.fiber import MixedScheduler, run_fork, run_fork_with


@pytest.mark.asyncio
async def test_observer_error_keeps_exit_and_notifies_later_observers():
    loop = asyncio.get_running_loop()
    errors: list[BaseException] = []
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, info: errors.append(info["exception"]))
    try:
        f = run_fork(yield_now.as_(7))
        seen: list[Exit[int, Never]] = []
        f.add_observer(lambda _ex: (_ for _ in ()).throw(ValueError("observer")))
        f.add_observer(seen.append)
        assert await run(f.join()) == 7
        assert len(seen) == 1 and isinstance(seen[0], Success) and seen[0].value == 7
        assert isinstance(errors[0], ValueError)
    finally:
        loop.set_exception_handler(old)


@pytest.mark.asyncio
async def test_reporting_failure_cannot_replace_first_exit_or_skip_observers():
    class ReportingStopped(BaseException):
        pass

    loop = asyncio.get_running_loop()
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, _info: (_ for _ in ()).throw(ReportingStopped()))
    try:
        wake: list[Resume[int, Never, Never]] = []
        suspended: Effect[int, Never, Never] = async_(lambda resume: wake.append(resume))
        f = run_fork(suspended)
        seen: list[Exit[int, Never]] = []
        f.add_observer(lambda _ex: (_ for _ in ()).throw(ValueError("observer")))
        f.add_observer(seen.append)
        wake[0](succeed(7))  # asyncio logs the handler's exception itself
        assert isinstance(f.exit, Success) and f.exit.value == 7
        assert len(seen) == 1 and seen[0] is f.exit
        late: list[Exit[int, Never]] = []
        f.add_observer(late.append)
        assert late == [f.exit]
    finally:
        loop.set_exception_handler(old)


@pytest.mark.asyncio
async def test_reporting_api_throw_cannot_replace_published_exit(monkeypatch: pytest.MonkeyPatch):
    class ReportingStopped(BaseException):
        pass

    loop = asyncio.get_running_loop()

    def stop_reporting(_info: object) -> None:
        raise ReportingStopped()

    monkeypatch.setattr(loop, "call_exception_handler", stop_reporting)
    wake: list[Resume[int, Never, Never]] = []
    suspended: Effect[int, Never, Never] = async_(lambda resume: wake.append(resume))
    f = run_fork(suspended)
    seen: list[Exit[int, Never]] = []
    f.add_observer(lambda _ex: (_ for _ in ()).throw(ValueError("observer")))
    f.add_observer(seen.append)
    with pytest.raises(ReportingStopped):
        wake[0](succeed(7))
    assert isinstance(f.exit, Success) and f.exit.value == 7
    assert seen == [f.exit]


@pytest.mark.asyncio
async def test_dispatcher_runs_rest_of_detached_batch_after_base_exception():
    class BatchStopped(BaseException):
        pass

    pending: list[Callable[[], None]] = []

    def arm(task: Callable[[], None]) -> Callable[[], None]:
        pending.append(task)
        return lambda: None

    scheduler = MixedScheduler(set_immediate=arm)
    dispatcher = scheduler.make_dispatcher()
    seen: list[str] = []

    def stop() -> None:
        raise BatchStopped()

    dispatcher.schedule_task(stop, 0)
    dispatcher.schedule_task(lambda: seen.append("sibling"), 0)
    with pytest.raises(BatchStopped):
        pending[0]()
    assert seen == ["sibling"]


@pytest.mark.asyncio
async def test_cancelled_error_in_effect_is_interrupt_and_runs_finalizer():
    seen: list[str] = []

    def cancel() -> None:
        raise asyncio.CancelledError()

    ex = await run_exit(sync(cancel).pipe(ensuring(sync(lambda: seen.append("closed")))))
    assert isinstance(ex, Failure) and ex.cause.is_interrupted
    assert seen == ["closed"]


@pytest.mark.parametrize("case", ["stale", "masked", "sweep"])
def test_bounded_cutoff_and_join_probes(case: str):
    script = Path(__file__).with_name("runtime_hardening_wake_probe.py")
    result = subprocess.run(
        [sys.executable, str(script), case], capture_output=True, text=True, timeout=8
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_queue_and_semaphore_wake_chains_complete():
    @gen
    def queue_chain():
        queues: list[Queue[int, Never]] = []
        for _ in range(220):
            queues.append((yield from Queue[int].bounded(1)))

        def link(i: int):
            @gen
            def work():
                n = yield from queues[i].take()
                yield from queues[i + 1].offer(n + 1)

            return work()

        for i in range(219):
            yield from fork(link(i))
        yield from yield_now
        yield from queues[0].offer(0)
        return (yield from queues[-1].take())

    @gen
    def semaphore_chain():
        semaphores: list[Semaphore] = []
        for _ in range(220):
            semaphores.append((yield from Semaphore.make(0)))

        def link(i: int):
            @gen
            def work():
                yield from semaphores[i].take(1)
                yield from semaphores[i + 1].release(1)

            return work()

        for i in range(219):
            yield from fork(link(i))
        yield from yield_now
        yield from semaphores[0].release(1)
        return (yield from semaphores[-1].take(1))

    for effect, expected in ((queue_chain(), 219), (semaphore_chain(), 1)):
        result = await asyncio.wait_for(run_exit(effect), 4)
        assert isinstance(result, Success) and result.value == expected


@pytest.mark.asyncio
async def test_python_context_is_fiber_local_across_wake_and_fork():
    var = contextvars.ContextVar("test-peffect-fiber-context", default="default")
    wake: list[Resume[None, Never, Never]] = []

    @gen
    def child():
        assert var.get() == "parent"
        var.set("child")
        yield from async_(lambda resume: wake.append(resume))
        return var.get()

    @gen
    def parent():
        var.set("parent")
        f = yield from fork(child(), start_immediately=True)
        assert var.get() == "parent"
        yield from yield_now
        wake[0](succeed(None))
        return (yield from f.join()), var.get()

    assert await run(parent()) == ("child", "parent")
    assert var.get() == "default"


@pytest.mark.asyncio
async def test_from_async_sees_fiber_context_and_resume_retains_it():
    var = contextvars.ContextVar("test-peffect-from-async", default="default")

    async def work() -> str:
        before = var.get()
        var.set("async task")
        await asyncio.sleep(0)
        return before

    @gen
    def program():
        var.set("fiber")
        observed = yield from from_async(work)
        return observed, var.get()

    assert await run(program()) == ("fiber", "fiber")
    assert var.get() == "default"


@pytest.mark.asyncio
async def test_completed_fiber_context_retained_until_fiber_is_released():
    class Payload:
        pass

    var: contextvars.ContextVar[Payload | None] = contextvars.ContextVar(
        "test-peffect-retained-context"
    )
    payload: Payload | None = Payload()
    assert payload is not None
    ref = weakref.ref(payload)
    f = run_fork(sync(lambda: var.set(payload)))
    payload = None
    gc.collect()
    assert ref() is not None and f.exit is not None
    del f
    gc.collect()
    assert ref() is None


@pytest.mark.asyncio
async def test_completed_fiber_service_context_retained_until_release():
    class Service:
        pass

    service: Service | None = Service()
    assert service is not None
    ref = weakref.ref(service)
    f = run_fork_with({"app/service": service})(succeed(None))
    service = None
    gc.collect()
    assert ref() is not None and f.context["app/service"] is ref()
    del f
    gc.collect()
    assert ref() is None


@pytest.mark.asyncio
async def test_thread_resume_is_marshaled_before_park_claim():
    wake: list[Resume[int, Never, Never]] = []
    suspended: Effect[int, Never, Never] = async_(lambda resume: wake.append(resume))
    f = run_fork(suspended)
    await asyncio.to_thread(lambda: wake[0](succeed(4)))
    assert await run(f.join()) == 4


@pytest.mark.asyncio
async def test_thread_resume_loses_to_interrupt_before_loop_handoff():
    wake: list[Resume[int, Never, Never]] = []
    suspended: Effect[int, Never, Never] = async_(lambda resume: wake.append(resume))
    f = run_fork(suspended)
    thread = threading.Thread(target=lambda: wake[0](succeed(4)))
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    f.interrupt_unsafe()
    ex = await run(f.await_())
    assert isinstance(ex, Failure) and ex.cause.is_interrupted


@pytest.mark.asyncio
async def test_masked_park_finishes_critical_region_before_deferred_interrupt():
    wake: list[Resume[None, Never, Never]] = []
    seen: list[str] = []
    suspended: Effect[None, Never, Never] = async_(lambda resume: wake.append(resume))
    effect = uninterruptible(
        suspended.flat_map(lambda _: sync(lambda: seen.append("critical exit")))
    )
    f = run_fork(effect)
    f.interrupt_unsafe()
    wake[0](succeed(None))
    ex = await run(f.await_())
    assert seen == ["critical exit"]
    assert isinstance(ex, Failure) and ex.cause.is_interrupted


@pytest.mark.asyncio
async def test_first_failure_only_is_recoverable_for_catch_tag():
    class Wanted(Exception):
        pass

    cause = Cause.fail(ValueError("first")) + Cause.fail(Wanted("second"))
    ex = await run_exit(Failure(cause).pipe(catch_tag(Wanted, lambda _e: succeed(1))))
    assert isinstance(ex, Failure) and ex.cause == cause


def test_done_is_not_elected_to_defect():
    done = Done(3)
    cause = (Cause.fail(done) + Cause.die(ValueError("bad"))).elect_defects()
    assert isinstance(cause.reasons[0], Fail)
    assert isinstance(cause.reasons[1], Die)


def test_done_pickle_round_trip_preserves_value_and_void_singleton():
    loaded: Done[int] = pickle.loads(pickle.dumps(Done(3)))
    assert isinstance(loaded, Done) and loaded.value == 3
    assert pickle.loads(pickle.dumps(Done())) is Done()


@pytest.mark.parametrize("kind", ["KeyboardInterrupt", "SystemExit", "Custom"])
def test_base_exception_publishes_and_cleans_up_before_propagation(kind: str):
    code = '''
import asyncio
import sys
from peffect.cause import Die
from peffect.core import EMPTY_CONTEXT, Failure, ensuring, sync
from peffect.fiber import Fiber, MixedScheduler
events = []
class Custom(BaseException): pass
kind = sys.argv[1]
error = {"KeyboardInterrupt": KeyboardInterrupt, "SystemExit": SystemExit, "Custom": Custom}[kind]
def stop(): raise error()
async def main():
    fiber = Fiber(EMPTY_CONTEXT)
    try:
        fiber.evaluate(sync(stop).pipe(ensuring(sync(lambda: events.append("cleanup")))))
        assert kind == "Custom"
    except (KeyboardInterrupt, SystemExit):
        assert kind != "Custom"
    assert events == ["cleanup"]
    assert isinstance(fiber.exit, Failure)
    assert any(isinstance(reason, Die) and isinstance(reason.defect, error)
               for reason in fiber.exit.cause.reasons)
    pending = []
    def arm(task):
        pending.append(task)
        return lambda: None
    dispatcher = MixedScheduler(set_immediate=arm).make_dispatcher()
    dispatcher.schedule_task(stop, 0)
    dispatcher.schedule_task(lambda: events.append("sibling"), 0)
    try:
        pending[0]()
    except error:
        pass
    else:
        raise AssertionError("dispatcher fatal was not propagated")
    assert events == ["cleanup", "sibling"]
    print("published-after-cleanup", kind)
asyncio.run(main())
'''
    result = subprocess.run(
        [sys.executable, "-c", code, kind], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
    assert "published-after-cleanup" in result.stdout


@pytest.mark.asyncio
async def test_cancelled_error_in_finalizer_callback_is_interrupt():
    def cancel(_exit: Exit[int, Never]) -> Effect[None, Never, Never]:
        raise asyncio.CancelledError()

    ex = await run_exit(succeed(1).on_exit(cancel))
    assert isinstance(ex, Failure) and ex.cause.is_interrupted
