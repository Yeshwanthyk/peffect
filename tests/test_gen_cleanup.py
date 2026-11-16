"""What a @gen body's own `finally` does to the cause when the body fails or is interrupted.

Deliberate Python adaptation. Upstream's generator frame (Effect v4 pin 289a53f7,
internal/effect.ts:1487-1519, fromIteratorUnsafe) has only a success continuation: on failure it
never calls `iterator.return()`, so a JS `finally` in an Effect.gen body does not run. peffect
closes the generator as its frame pops (core.py GenFrame), so a Python `finally` runs
deterministically. That cleanup is new code that can itself go wrong, and upstream has no rule
for it. The contract pinned here borrows the nearest upstream rule, a finalizer that fails
(combineFinalizerCause, internal/effect.ts:4069-4075; Effect.test.ts:3160):

1. A plain `finally` leaves the cause untouched and runs before any outer handler.
2. A `finally` that raises X keeps the original cause and adds Die(X), then promotes typed
   failures to defects (`elect_defects`), exactly like `fail(e).pipe(on_exit(throwing))`.
3. A `finally` that yields makes Python's `close()` raise RuntimeError("generator ignored
   GeneratorExit"). That is case 2 with X = the RuntimeError, never swallowed.
4. Under interruption the Interrupt survives, and Die(X) is added after it.
"""

import gc
import warnings
from typing import Any

from peffect import (
    Cause,
    Die,
    Failure,
    Interrupt,
    Success,
    catch_all,
    fail,
    fork,
    gen,
    never,
    on_exit,
    run_exit,
    succeed,
    sync,
)


class Boom(Exception):
    pass


BODY = Boom("body failure")
CLEANUP = Boom("cleanup defect")


def throwing(_: object) -> Any:
    raise CLEANUP


async def test_plain_finally_keeps_the_typed_failure_and_runs_before_outer_handlers():
    """Contract 1 (pins current behaviour)."""
    events: list[str] = []

    @gen
    def body():
        try:
            yield from fail(BODY)
        finally:
            events.append("finally")

    caught = await run_exit(
        body().pipe(catch_all(lambda e: sync(lambda: events.append(f"caught:{e}"))))
    )
    assert isinstance(caught, Success)
    assert events == ["finally", "caught:body failure"]

    events.clear()
    result = await run_exit(body())
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(BODY)
    assert events == ["finally"]


async def test_finally_raising_keeps_the_original_failure_as_on_exit_does():
    """Contract 2. The cause must equal the one an explicit throwing finalizer gives
    (Effect.test.ts:3160): Die(BODY) + Die(CLEANUP), not Die(CLEANUP) alone."""

    @gen
    def body():
        try:
            yield from fail(BODY)
        finally:
            raise CLEANUP

    explicit = await run_exit(fail(BODY).pipe(on_exit(throwing)))
    result = await run_exit(body())
    assert isinstance(explicit, Failure)
    assert isinstance(result, Failure)
    assert explicit.cause == Cause.die(BODY) + Cause.die(CLEANUP)
    assert result.cause == explicit.cause


async def test_finally_yielding_keeps_the_original_failure_and_reports_the_runtime_error():
    """Contract 3: Python refuses a yield during close(). Reported as a defect after the original
    failure; the original typed error is still present (as Die, after elect_defects)."""

    @gen
    def body():
        try:
            yield from fail(BODY)
        finally:
            yield from sync(lambda: None)

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = await run_exit(body())
        gc.collect()  # the generator stays suspended; GC's own close() may warn
    assert isinstance(result, Failure)
    reasons = result.cause.reasons
    assert len(reasons) == 2
    assert reasons[0] == Cause.die(BODY).reasons[0]
    second = reasons[1]
    assert isinstance(second, Die)
    assert isinstance(second.defect, RuntimeError)
    assert str(second.defect) == "generator ignored GeneratorExit"


async def test_a_typed_handler_cannot_recover_after_cleanup_died():
    """Contract 2: a defect is present, so catch_all must not run (v4 effect.ts:184-200)."""

    @gen
    def body():
        try:
            yield from fail(BODY)
        finally:
            raise CLEANUP

    result = await run_exit(body().pipe(catch_all(lambda _: succeed("recovered"))))
    assert isinstance(result, Failure)
    assert Cause.die(CLEANUP).reasons[0] in result.cause.reasons


async def test_interrupted_plain_finally_keeps_the_interrupt():
    """Contract 1 under interruption (pins current behaviour)."""
    events: list[str] = []

    @gen
    def body():
        try:
            yield from never
        finally:
            events.append("finally")

    @gen
    def main():
        child = yield from fork(body(), start_immediately=True)
        yield from child.interrupt()
        return (yield from child.await_())

    outer = await run_exit(main())
    assert isinstance(outer, Success)
    assert isinstance(outer.value, Failure)
    assert [type(r) for r in outer.value.cause.reasons] == [Interrupt]
    assert events == ["finally"]


async def test_interrupted_finally_raising_keeps_the_interrupt_and_adds_the_defect():
    """Contract 4, matching Effect.test.ts:3175 for an explicit finalizer: [Interrupt, Die]."""

    @gen
    def body():
        try:
            yield from never
        finally:
            raise CLEANUP

    @gen
    def main():
        child = yield from fork(body(), start_immediately=True)
        yield from child.interrupt()
        return (yield from child.await_())

    outer = await run_exit(main())
    assert isinstance(outer, Success)
    exit_ = outer.value
    assert isinstance(exit_, Failure)
    assert [type(r) for r in exit_.cause.reasons] == [Interrupt, Die]
    assert exit_.cause.reasons[1] == Cause.die(CLEANUP).reasons[0]


async def test_finally_raising_after_success_is_a_defect():
    """Pins current behaviour: on success, cleanup runs inside the generator's own `send`, so an
    exception there is an evaluation error and becomes Die (internal/effect.ts:1482-1484)."""

    @gen
    def body():
        try:
            yield from succeed(1)
        finally:
            raise CLEANUP

    result = await run_exit(body())
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(CLEANUP)
