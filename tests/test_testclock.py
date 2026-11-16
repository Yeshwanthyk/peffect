"""Batch D: Effect v4's TestClock tests, ported. Upstream is pinned at vendor/effect-v4 commit
289a53f (Effect v4.1.4), packages/effect/test/TestClock.test.ts (all 25 cases); the
implementation it pins is src/testing/TestClock.ts. Each test names its upstream line and title.
Mapping and deferred cases: docs/UPSTREAM_TESTS.md.

"source" tests (S1-S11) are derived from TestClock.ts. S1-S4 each kill a vendor mutant that all
25 upstream cases survive; the rest pin edge values and the warning's lifetime. Every expected
value was measured by running the pinned vendor (and the mutants) under bun (scratch probes
`harness.ts`, `p3.ts`), never computed with the code under test.

Adaptations used throughout:
- Durations are upstream's own `Duration.Input` values: `"1 minute"`, `Duration.seconds(1)` is
  `D.seconds(1)`. `adjust` takes an Input (TestClock.ts:359) and the instance `sleep` a Duration
  (TestClock.ts:329). `set_time` takes milliseconds, as upstream's `setTime(timestamp: number)`
  does; milliseconds are a float, nanosecond reads an `int` (upstream `bigint`).
- `it.effect` is @effect/vitest's runner (vendor/effect-v4, sparse checkout; read with `git show
  HEAD:packages/vitest/src/internal/internal.ts`). Line 68: `TestEnv = Layer.mergeAll(
  TestConsole.layer, TestClock.layer())`; line 490: `effect: makeTester(flow(Effect.scoped,
  Effect.provide(TestEnv)))`. `it_effect` below is that wiring: TestClock.layer() provides the
  clock both as the Clock and, through its tracking scheduler, as the Scheduler.
- `fiber.pollUnsafe()` is `fiber.exit` (D6); `Exit.void` is a `Success` holding `None`.
  `Effect.delay(d)` is `sleep(d).flat_map(...)` and `Effect.forever` a `@gen` `while True` (D7).
- `forkChild({ startImmediately: true })` is `fork(eff, start_immediately=True)`; `forkDetach`
  is `fork_daemon`.
- Upstream spies on `globalThis.console`; the live Console prints to `sys.stdout`/`sys.stderr`,
  so pytest's `capsys` is the spy.
"""

import copy
import math
from typing import Any, Never

import pytest

import peffect.console as Console
import peffect.duration as D
from peffect import (
    Clock,
    Effect,
    Exit,
    Failure,
    Layer,
    MaxOpsBeforeYield,
    Success,
    ensuring,
    fork,
    fork_daemon,
    fork_scoped,
    gen,
    never,
    provide,
    provide_layer,
    run,
    run_exit,
    scoped,
    sleep,
    succeed,
    sync,
    timeout,
    yield_now,
)
from peffect.testing import TestClock, TestClockService, TestConsole

TEST_ENV = Layer.merge_all(TestConsole.layer, TestClock.layer())

WARNING = "not advancing the test clock"
BUSY_WARNING = "still running without suspending"


def make_test_clock() -> Effect[TestClockService, Never, Any]:
    """`TestClock.make()`: a new TestClock, not installed anywhere. Requires a Scope (its
    warning fiber is stopped by a finalizer on it)."""
    return TestClock.make()


def ambient_test_clock() -> Effect[TestClockService, Never, Never]:
    """`TestClock.testClockWith(Effect.succeed)`: the fiber's Clock, as a TestClock."""
    return TestClock.test_clock_with(succeed)


async def it_effect(body: Effect[Any, Any, Any]) -> None:
    """`it.effect`: `flow(Effect.scoped, Effect.provide(TestEnv))`."""
    await run(provide_layer(TEST_ENV)(scoped(body)))


def nanos(clock: TestClockService) -> tuple[int, int]:
    """(wall nanos, monotonic nanos)."""
    return clock.current_time_nanos_unsafe(), clock.monotonic_time_nanos_unsafe()


def is_void(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Success) and ex.value is None


@gen
def spin():
    """`Effect.forever(Effect.yieldNow)`."""
    while True:
        yield from yield_now


# -- warnings ---------------------------------------------------------------------------------


@pytest.mark.parametrize("warning", [WARNING, BUSY_WARNING])
async def test_warnings_bypass_test_console(
    warning: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """TestClock.test.ts:16 "warnings bypass TestConsole - <warning>".

    The warning goes to the live console even though the test provides TestConsole; the poll
    stops when it reaches either destination, rather than timing out on the regression."""
    output: list[str] = []

    def drain() -> None:
        captured = capsys.readouterr()
        output.extend((captured.out, captured.err))

    def contains_warning(lines: Any) -> bool:
        return any(warning in str(line) for line in lines)

    @gen
    def poll():
        while True:
            lines = yield from TestConsole.log_lines
            yield from sync(drain)
            if contains_warning(output) or contains_warning(lines):
                return lines
            yield from sleep("10 millis")

    @gen
    def body():
        yield from Console.log("ordinary output")
        after = spin() if warning == BUSY_WARNING else succeed(None)
        yield from fork(sleep("1 minute").flat_map(lambda _: after))
        if warning == BUSY_WARNING:
            yield from TestClock.adjust("1 minute")
        captured = yield from TestClock.with_live(timeout(poll(), "3 seconds"))
        assert list(captured) == ["ordinary output"]
        assert contains_warning(output)

    await it_effect(body())


# -- adjust: cancellation ---------------------------------------------------------------------


async def test_adjust_skips_the_cancelled_deadline_interrupted_sleep():
    """TestClock.test.ts:56 "adjust - skips the cancelled deadline (interrupted sleep)".

    The interrupted sleep's 1-minute deadline must not be visited: an observer records the time
    every time `adjust` yields to other fibers, and 60_000 never shows up."""
    observed: set[float] = set()

    @gen
    def program():
        test_clock = yield from make_test_clock()
        sleeper = yield from fork(test_clock.sleep(D.minutes(1)), start_immediately=True)
        yield from yield_now
        yield from sleeper.interrupt()

        @gen
        def observe():
            while True:
                yield from sync(lambda: observed.add(test_clock.current_time_millis_unsafe()))
                yield from yield_now

        yield from fork(observe())
        yield from test_clock.adjust("2 minutes")

    await it_effect(program())
    assert 60_000 not in observed


async def test_adjust_skips_the_cancelled_deadline_successful_timeout():
    """TestClock.test.ts:56 "adjust - skips the cancelled deadline (successful timeout)".

    The timeout's 1-minute sleep is cancelled when `yield_now` wins; its deadline must be gone."""
    observed: set[float] = set()

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from provide(Clock.context(test_clock))(timeout(yield_now, "1 minute"))

        @gen
        def observe():
            while True:
                yield from sync(lambda: observed.add(test_clock.current_time_millis_unsafe()))
                yield from yield_now

        yield from fork(observe())
        yield from test_clock.adjust("2 minutes")

    await it_effect(program())
    assert 60_000 not in observed


# -- sleep ------------------------------------------------------------------------------------


async def test_sleep_does_not_require_passage_of_wall_time():
    """TestClock.test.ts:83 "sleep - does not require passage of wall time"."""
    elapsed: list[bool] = []

    @gen
    def body():
        yield from fork(sleep("10 hours").flat_map(lambda _: sync(lambda: elapsed.append(True))))
        yield from TestClock.adjust("11 hours")

    await it_effect(body())
    assert elapsed == [True]


async def test_sleep_delays_effects_until_time_is_adjusted():
    """TestClock.test.ts:93 "sleep - delays effects until time is adjusted"."""
    elapsed: list[bool] = []

    @gen
    def body():
        fiber = yield from fork(
            sleep("10 hours").flat_map(lambda _: sync(lambda: elapsed.append(True)))
        )
        yield from TestClock.adjust("9 hours")
        assert fiber.exit is None
        yield from TestClock.adjust("11 hours")
        assert is_void(fiber.exit)

    await it_effect(body())
    assert elapsed == [True]


async def test_sleep_preserves_interruption_masking_in_a_finalizer_on_first_use():
    """TestClock.test.ts:106 "sleep - preserves interruption masking in a finalizer on first use".

    The finalizer's first sleep on this clock happens while the fiber is being interrupted: it
    must still wait for the clock, not be cut short."""
    events: list[str] = []

    @gen
    def cleanup():
        yield from sync(lambda: events.append("cleanup started"))
        yield from sleep("1 second")
        yield from sync(lambda: events.append("cleanup finished"))

    @gen
    def program():
        clock = yield from make_test_clock()
        fiber = yield from fork(
            provide(Clock.context(clock))(never.pipe(ensuring(cleanup()))), start_immediately=True
        )
        fiber.interrupt_unsafe()
        yield from yield_now
        assert events == ["cleanup started"]
        assert fiber.exit is None
        yield from clock.adjust("1 second")
        yield from fiber.await_()

    await it_effect(program())
    assert events == ["cleanup started", "cleanup finished"]


async def test_sleep_handles_multiple_sleeps():
    """TestClock.test.ts:128 "sleep - handles multiple sleeps"."""
    message: list[str] = []

    @gen
    def body():
        yield from fork_scoped(
            sleep("3 hours").flat_map(lambda _: sync(lambda: message.append("World!")))
        )
        yield from fork_scoped(
            sleep("1 hour").flat_map(lambda _: sync(lambda: message.append("Hello, ")))
        )
        yield from TestClock.adjust("1 hour")
        assert "".join(message) == "Hello, "
        yield from TestClock.adjust("4 hours")
        assert "".join(message) == "Hello, World!"

    await it_effect(body())


async def test_sleep_waits_the_full_duration_each_time_a_sleep_effect_runs():
    """TestClock.test.ts:143 "sleep - waits the full duration each time a sleep effect runs".

    One sleep *effect*, run twice: each run measures from its own start, not from when the
    effect was built."""

    @gen
    def body():
        clock = yield from ambient_test_clock()
        one_second = clock.sleep(D.seconds(1))
        yield from TestClock.adjust("5 seconds")
        for _ in range(2):
            fiber = yield from fork(one_second)
            yield from TestClock.adjust("999 millis")
            assert fiber.exit is None
            yield from TestClock.adjust("1 millis")
            assert is_void(fiber.exit)

    await it_effect(body())


# -- setTime ----------------------------------------------------------------------------------


async def test_set_time_sleep_correctly_handles_new_set_time():
    """TestClock.test.ts:157 "setTime - sleep correctly handles new set time"."""
    elapsed: list[bool] = []

    @gen
    def body():
        yield from fork(sleep("10 hours").flat_map(lambda _: sync(lambda: elapsed.append(True))))
        assert elapsed == []
        yield from TestClock.set_time(D.to_millis(D.hours(11)))
        assert elapsed == [True]

    await it_effect(body())


async def test_set_time_floors_nanoseconds_for_fractional_millisecond_instants():
    """TestClock.test.ts:168 "setTime - floors nanoseconds for fractional millisecond instants"."""

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.set_time(199023438.0000004)
        assert test_clock.current_time_nanos_unsafe() == 199023438000000

    await it_effect(program())


async def test_set_time_preserves_wall_clock_nanoseconds_for_large_timestamps():
    """TestClock.test.ts:175 "setTime - preserves wall-clock nanoseconds for large timestamps"."""
    timestamp = 1_000_000_000_001

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.set_time(timestamp)
        assert test_clock.current_time_nanos_unsafe() == timestamp * 1_000_000

    await it_effect(program())


async def test_set_time_advances_monotonic_time_only_when_moving_forward():
    """TestClock.test.ts:221 "setTime - advances monotonic time only when moving forward"."""

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.set_time(2_000)
        assert test_clock.monotonic_time_nanos_unsafe() == 2_000_000_000
        yield from test_clock.set_time(500)
        assert test_clock.current_time_millis_unsafe() == 500
        assert test_clock.monotonic_time_nanos_unsafe() == 2_000_000_000
        yield from test_clock.set_time(1_000)
        assert test_clock.monotonic_time_nanos_unsafe() == 2_500_000_000

    await it_effect(program())


async def test_set_time_preserves_nanosecond_precision_for_far_future_timestamps():
    """TestClock.test.ts:233 "setTime - preserves nanosecond precision for far-future
    timestamps"."""
    far_future = 1_000_000_000_000

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.set_time(far_future)
        before = test_clock.monotonic_time_nanos_unsafe()
        yield from test_clock.set_time(far_future + 1)
        assert test_clock.monotonic_time_nanos_unsafe() - before == 1_000_000

    await it_effect(program())


async def test_set_time_runs_sleeps_registered_after_a_fork_and_join():
    """TestClock.test.ts:272 "setTime - runs sleeps registered after a fork and join"."""
    ticks: list[int] = []

    @gen
    def tick_forever():
        while True:
            yield from sleep("1 second")
            yield from sync(lambda: ticks.append(1))
            yield from fork(yield_now).flat_map(lambda f: f.join())

    @gen
    def body():
        fiber = yield from fork(tick_forever())
        yield from TestClock.set_time(5_000)
        yield from fiber.interrupt()

    await it_effect(body())
    assert len(ticks) == 5


# -- adjust -----------------------------------------------------------------------------------


async def test_adjust_advances_the_clock_when_detached_or_copied():
    """TestClock.test.ts:183 "adjust - advances the clock when detached, copied or inherited".

    Detached and copied are ported: a bound `adjust` taken off the clock, and a `copy.copy` of
    it (upstream `{ ...clock }`), both advance the one clock. The inherited part
    (`Object.create(clock)`) is JS prototype delegation, which Python objects don't have; it is
    not ported, so the expected time is two adjustments, 2000, instead of upstream's 3000."""

    @gen
    def body():
        clock = yield from ambient_test_clock()
        adjust = clock.adjust
        yield from adjust("1 second")
        yield from copy.copy(clock).adjust("1 second")
        assert clock.current_time_millis_unsafe() == 2000

    await it_effect(body())


async def test_adjust_advances_wall_and_monotonic_time():
    """TestClock.test.ts:194 "adjust - advances wall and monotonic time"."""

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.adjust("1 second")
        assert test_clock.current_time_millis_unsafe() == 1_000
        assert test_clock.monotonic_time_nanos_unsafe() == 1_000_000_000
        assert (yield from test_clock.monotonic_time_nanos) == 1_000_000_000

    await it_effect(program())


async def test_adjust_preserves_precision_beyond_max_safe_integer_nanos():
    """TestClock.test.ts:203 "adjust - preserves precision for nanosecond durations beyond
    Number.MAX_SAFE_INTEGER"."""
    nanos = 999_999_999_999_999_000

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.adjust(D.nanos(nanos))
        assert test_clock.monotonic_time_nanos_unsafe() == nanos
        assert test_clock.current_time_nanos_unsafe() == nanos

    await it_effect(program())


async def test_adjust_keeps_nanosecond_access_total_after_infinite_durations():
    """TestClock.test.ts:212 "adjust - keeps nanosecond access total after infinite durations".
    `typeof x === "bigint"` is `type(x) is int`."""

    @gen
    def program():
        for duration in (D.infinity, D.negative_infinity):
            test_clock = yield from make_test_clock()
            yield from test_clock.adjust(duration)
            nanos: Effect[int, Never, Never] = test_clock.current_time_nanos
            assert type((yield from nanos)) is int

    await it_effect(program())


async def test_adjust_advances_monotonic_time_to_intermediate_sleep_deadlines():
    """TestClock.test.ts:243 "adjust - advances monotonic time to intermediate sleep deadlines"."""
    observed: list[int] = []

    @gen
    def program():
        test_clock = yield from make_test_clock()

        @gen
        def sleeper():
            yield from test_clock.sleep(D.seconds(1))
            monotonic: Effect[int, Never, Never] = test_clock.monotonic_time_nanos
            observed.append((yield from monotonic))

        yield from fork(sleeper())
        yield from test_clock.adjust("2 seconds")
        assert test_clock.monotonic_time_nanos_unsafe() == 2_000_000_000

    await it_effect(program())
    assert observed == [1_000_000_000]


async def test_adjust_runs_sleeps_registered_after_a_yield():
    """TestClock.test.ts:256 "adjust - runs sleeps registered after a yield"."""
    ticks: list[int] = []

    @gen
    def tick_forever():
        while True:
            yield from sleep("1 second")
            yield from sync(lambda: ticks.append(1))
            yield from yield_now

    @gen
    def body():
        fiber = yield from fork(tick_forever())
        yield from TestClock.adjust("5 seconds")
        yield from fiber.interrupt()

    await it_effect(body())
    assert len(ticks) == 5


async def test_adjust_completes_when_a_nested_clock_wakes_a_busy_fiber():
    """TestClock.test.ts:288 "adjust - completes when a nested clock wakes a busy fiber".

    The busy fiber never suspends, so `adjust` waits for it only `warning_delay`, then goes on
    and wakes the later sleeper."""
    later: list[bool] = []

    @gen
    def body():
        yield from fork(sleep("1 second").flat_map(lambda _: spin()))
        yield from fork(sleep("2 seconds").flat_map(lambda _: sync(lambda: later.append(True))))
        yield from TestClock.adjust("5 seconds")
        assert later == [True]

    await it_effect(provide_layer(TestClock.layer(warning_delay="10 millis"))(body()))


async def test_serializes_a_context_containing_a_test_clock():
    """TestClock.test.ts:305 "serializes a context containing a TestClock".

    Python adaptation, not a toJSON parity claim: `JSON.stringify` is `repr`, of the clock and of
    a context map holding a copy of it. The copy shares the clock's time, as upstream's
    `{ ...clock }` keeps reading the same state."""

    @gen
    def body():
        clock = yield from ambient_test_clock()
        clock_copy = copy.copy(clock)
        repr({Clock.key: clock})
        repr({Clock.key: clock_copy})
        yield from clock_copy.adjust("1 second")
        assert "current_time_millis=1000" in repr(clock_copy)

    await it_effect(body())


async def test_adjust_waits_for_woken_fibers_when_the_advancing_fiber_yields_on_its_budget():
    """TestClock.test.ts:317 "adjust - waits for woken fibers when the advancing fiber yields on
    its operation budget"."""
    done: list[bool] = []

    @gen
    def body():
        woken = sleep("1 second").flat_map(lambda _: yield_now)
        yield from fork(woken.flat_map(lambda _: sync(lambda: done.append(True))))
        yield from TestClock.adjust("1 second")
        assert done == [True]

    layer = TestClock.layer(warning_delay=D.infinity)
    await it_effect(provide(MaxOpsBeforeYield.context(4))(provide_layer(layer)(body())))


async def test_layer_can_adjust_when_provided_without_an_ambient_scope():
    """TestClock.test.ts:335 "layer - can adjust when provided without an ambient Scope".
    Not under `it_effect`: its Scope would hide the regression (upstream #2244)."""
    await run(provide_layer(TestClock.layer())(TestClock.adjust("1 second")))


# -- source-derived (TestClock.ts) ------------------------------------------------------------


async def test_source_s1_negative_adjust_moves_wall_time_back_but_not_monotonic_time():
    """S1, `run`'s `if (adj > 0)` (mutant `mono_backwards`)."""

    @gen
    def program():
        c = yield from make_test_clock()
        yield from c.adjust(D.seconds(-1))
        assert c.current_time_millis_unsafe() == -1000
        assert nanos(c) == (-(10**9), 0)
        yield from c.adjust("2 seconds")
        assert c.current_time_millis_unsafe() == 1000
        assert nanos(c) == (10**9, 2 * 10**9)
        yield from c.set_time(500)
        assert nanos(c) == (5 * 10**8, 2 * 10**9)

    await it_effect(program())


async def test_source_s2_equal_deadlines_wake_in_sleep_order():
    """S2, `sleep`'s insertion before equal deadlines (mutant `equal_after`: c,b,a)."""
    order: list[str] = []

    @gen
    def program():
        c = yield from make_test_clock()
        for name in "abc":
            woken = c.sleep(D.seconds(1)).flat_map(lambda _, n=name: sync(lambda: order.append(n)))
            yield from fork(woken, start_immediately=True)
        yield from c.adjust("1 second")
        assert order == ["a", "b", "c"]

    await it_effect(program())


async def test_source_s3_wake_yields_once_on_a_clock_without_its_scheduler():
    """S3, `wake`'s `pending === 0` yield (mutant `idle_no_yield`). A `make()` clock is not the
    Scheduler, so nothing is tracked; the yield still lets a woken fiber that yields once finish."""
    done: list[bool] = []

    @gen
    def program():
        c = yield from make_test_clock()
        woken = c.sleep(D.seconds(1)).flat_map(lambda _: yield_now)
        yield from fork(
            woken.flat_map(lambda _: sync(lambda: done.append(True))), start_immediately=True
        )
        yield from c.adjust("1 second")
        assert done == [True]

    await it_effect(program())


async def count_warnings(capsys: pytest.CaptureFixture[str], body: Effect[Any, Never, Any]) -> int:
    """Run ``body`` on the live clock with a 50ms-warning clock in its Scope, then wait 150ms
    of live time (three warning delays) and count the warnings printed. A count over a window:
    it shows what was printed by then, not that nothing prints later."""
    await run(scoped(body).flat_map(lambda _: sleep("150 millis")))
    return capsys.readouterr().out.count(WARNING)


async def test_source_s4_closing_the_scope_stops_the_pending_warning(
    capsys: pytest.CaptureFixture[str],
):
    """S4, `make`'s `addFinalizer(warningDone)` (mutant `no_finalizer`)."""

    @gen
    def body():
        c = yield from TestClock.make(warning_delay="50 millis")
        yield from fork_daemon(c.sleep(D.seconds(1)))
        yield from yield_now

    assert await count_warnings(capsys, body()) == 0


async def test_source_s10_one_warning_per_clock(capsys: pytest.CaptureFixture[str]):
    """S10, `warningStart` forks only from `Start` (measured: two sleeps, one warning)."""

    @gen
    def body():
        c = yield from TestClock.make(warning_delay="50 millis")
        yield from fork_daemon(c.sleep(D.seconds(1)))
        yield from fork_daemon(c.sleep(D.seconds(2)))
        yield from sleep("150 millis")

    assert await count_warnings(capsys, body()) == 1


async def test_source_s11_the_warning_belongs_to_the_first_sleeping_fiber(
    capsys: pytest.CaptureFixture[str],
):
    """S11, `warningStart` uses `forkChild`: interrupting the sleeper before the delay stops the
    warning (measured: 0)."""

    @gen
    def body():
        c = yield from TestClock.make(warning_delay="50 millis")
        sleeper = yield from fork_daemon(c.sleep(D.seconds(1)))
        yield from yield_now
        yield from sleeper.interrupt()
        yield from sleep("150 millis")

    assert await count_warnings(capsys, body()) == 0


# (set_time on a fresh clock, wall nanos, monotonic nanos): measured under bun
ROUNDING_CASES: list[tuple[float, int, int]] = [
    (0.0000025, 2, 3),
    (0.0000005, 0, 1),
    (1.5, 1_500_000, 1_500_000),
]


@pytest.mark.parametrize(("millis", "wall", "monotonic"), ROUNDING_CASES, ids=lambda x: str(x))
async def test_source_s5_monotonic_rounds_half_up_and_wall_floors(
    millis: float, wall: int, monotonic: int
):
    """S5, `advanceTo`: monotonic is `Math.round(delta * 1e6)` (half up; Python's `round` is
    half even and gives 2), wall is `millisToNanos` (floors)."""

    @gen
    def program():
        c = yield from make_test_clock()
        yield from c.set_time(millis)
        assert nanos(c) == (wall, monotonic)

    await it_effect(program())


async def test_source_s6_non_finite_times():
    """S6, `advanceTo` and `run` skip non-finite steps; `sleep` past an infinite now returns."""

    @gen
    def program():
        c = yield from make_test_clock()
        yield from c.adjust(D.infinity)
        assert c.current_time_millis_unsafe() == math.inf
        assert nanos(c) == (0, 0)
        sleeper = yield from fork(c.sleep(D.seconds(1)), start_immediately=True)
        assert is_void(sleeper.exit)

        d = yield from make_test_clock()
        yield from d.adjust(D.negative_infinity)
        assert d.current_time_millis_unsafe() == -math.inf
        assert nanos(d) == (0, 0)

        e = yield from make_test_clock()
        yield from e.adjust("1 second")
        yield from e.set_time(math.nan)
        assert math.isnan(e.current_time_millis_unsafe())
        assert nanos(e) == (10**9, 10**9)

    await it_effect(program())


async def test_source_s7_sub_millisecond_adjust_keeps_exact_nanos():
    """S7, `adjust` passes `toNanosUnsafe` for a finite duration: wall and monotonic time move by
    the exact nanos, not by rounded milliseconds."""

    @gen
    def program():
        c = yield from make_test_clock()
        yield from c.adjust(D.nanos(1500))
        assert c.current_time_millis_unsafe() == 0.0015
        assert nanos(c) == (1500, 1500)

    await it_effect(program())


async def test_source_s8_test_clock_with_on_the_live_clock_is_a_type_error_defect():
    """S8, adaptation: upstream casts (`testClockWith`); here a Clock that isn't a TestClock is
    a defect."""
    exit_ = await run_exit(TestClock.test_clock_with(succeed))
    assert isinstance(exit_, Failure)
    assert isinstance(exit_.cause.squash(), TypeError)


async def test_source_s9_each_run_gets_a_fresh_clock():
    """S9, `layer` builds a clock per provide: a second run starts at 0."""
    seen: list[float] = []

    @gen
    def body():
        yield from TestClock.adjust("1 second")
        clock = yield from ambient_test_clock()
        seen.append(clock.current_time_millis_unsafe())

    await it_effect(body())
    await it_effect(body())
    assert seen == [1000, 1000]
