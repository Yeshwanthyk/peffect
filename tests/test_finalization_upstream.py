"""Batch B: Effect v4's finalization tests (Effect.test.ts:3153-3270, describe "finalization"),
ported. Upstream is pinned at vendor/effect-v4 commit 289a53f (Effect v4.1.4). Mapping and
status: docs/UPSTREAM_TESTS.md.

Adaptations used throughout:
- Upstream fails and dies with strings. peffect's `Die` holds an exception, so each value is an
  exception instance, compared by identity; a thrown finalizer raises the instance it names.
- Upstream's `Effect.exit` / `Effect.sandbox` + `flip` read the Exit or Cause inside the
  program; here `run_exit` reads it at the edge. peffect's Exit has no `==`, so an expected
  `Exit.failCause(c)` is checked as "is a Failure, and its cause == c".
- `Cause.combine(x, y)` is `x + y`, and `Cause.die(d)` is `Cause.die(d)`.
- Upstream's `Effect.interrupt` (the fiber fails with its own interruption) is
  `fail_cause(Cause.interrupt())`.
- `Effect.onInterrupt` / `Effect.onError` are `on_interrupt` / `on_error`.
"""

from typing import Any

from peffect import (
    Cause,
    Die,
    Failure,
    Interrupt,
    Success,
    catch_cause,
    die,
    ensuring,
    fail,
    fail_cause,
    fork,
    on_error,
    on_exit,
    on_interrupt,
    run_exit,
    succeed,
    sync,
)


class Boom(Exception):
    pass


BODY = Boom("body failure")
FIN = Boom("finalizer defect")
EXAMPLE = Boom("Oh noes!")  # upstream ExampleError


def throwing(_: object) -> Any:
    """Upstream `throwing`: a finalizer callback that throws instead of returning an effect."""
    raise FIN


async def test_on_exit_preserves_the_original_failure_when_the_finalizer_throws():
    """Effect.test.ts:3160 "onExit preserves the original failure when the finalizer throws"."""
    result = await run_exit(fail(BODY).pipe(on_exit(throwing)))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(BODY) + Cause.die(FIN)


async def test_on_exit_reports_a_thrown_finalizer_on_success():
    """Effect.test.ts:3169 "onExit reports a thrown finalizer on success"."""
    result = await run_exit(succeed(1).pipe(on_exit(throwing)))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(FIN)


async def test_an_outer_on_interrupt_still_sees_the_interruption_after_an_inner_finalizer_throws():
    """Effect.test.ts:3175 "an outer onInterrupt still sees the interruption after an inner
    finalizer throws"."""
    finalized: list[bool] = []

    def mark(_: object) -> Any:
        return sync(lambda: finalized.append(True))

    result = await run_exit(
        fail_cause(Cause.interrupt()).pipe(on_exit(throwing)).pipe(on_interrupt(mark))
    )
    assert finalized == [True]
    assert isinstance(result, Failure)
    assert [type(r) for r in result.cause.reasons] == [Interrupt, Die]
    assert result.cause.reasons[1] == Cause.die(FIN).reasons[0]


async def test_nested_throwing_finalizers_do_not_overflow_the_stack():
    """Effect.test.ts:3195 "nested throwing finalizers do not overflow the stack".

    Upstream wraps 20,000 times in `Effect.onExit(Effect.exit(program), throwing)`; `Effect.exit`
    turns any ending into success, here `catch_cause(succeed)`. `runSyncExit` is `run_exit`."""
    program: Any = succeed(1)
    for _ in range(20_000):
        program = program.pipe(catch_cause(lambda c: succeed(c))).pipe(on_exit(throwing))
    result = await run_exit(program)
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(FIN)


async def test_fail_ensuring():
    """Effect.test.ts:3203 "fail ensuring"."""
    finalized: list[bool] = []
    result = await run_exit(fail(EXAMPLE).pipe(ensuring(sync(lambda: finalized.append(True)))))
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(EXAMPLE)
    assert finalized == [True]


async def test_fail_on_error():
    """Effect.test.ts:3216 "fail on error"."""
    finalized: list[bool] = []

    def mark(_: object) -> Any:
        return sync(lambda: finalized.append(True))

    result = await run_exit(fail(EXAMPLE).pipe(on_error(mark)))
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(EXAMPLE)
    assert finalized == [True]


async def test_finalizer_errors_merged():
    """Effect.test.ts:3231 "finalizer errors merged"."""
    e2, e3 = Boom("e2"), Boom("e3")
    result = await run_exit(fail(EXAMPLE).pipe(ensuring(die(e2))).pipe(ensuring(die(e3))))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(EXAMPLE) + Cause.die(e2) + Cause.die(e3)


async def test_finalizer_errors_reported():
    """Effect.test.ts:3249 "finalizer errors reported". `Effect.forkChild` is `fork`,
    `Fiber.await` is `fiber.await_()`."""
    reported: list[object] = []
    program = (
        fork(succeed(42).pipe(ensuring(die(EXAMPLE))))
        .flat_map(lambda fiber: fiber.await_())
        .flat_map(lambda e: sync(lambda: reported.append(e)))
    )
    result = await run_exit(program)
    assert isinstance(result, Success) and result.value is None
    assert len(reported) == 1 and not isinstance(reported[0], Success)
