"""Batch D: Effect v4's TestClock tests, ported. Upstream is pinned at vendor/effect-v4 commit
289a53f (Effect v4.1.4), packages/effect/test/TestClock.test.ts; the implementation it pins is
src/testing/TestClock.ts. Each test names its upstream line and title. Mapping and deferred
cases: docs/UPSTREAM_TESTS.md.

Adaptations used throughout:
- `TestClock` lives in `peffect.testing`, which doesn't exist yet. Each test looks it up when it
  runs (`load_testing()`), so until it exists every test fails instead of the file failing to
  import. The lookups are temporary: they become imports, with real types, once the modules land.
- Durations are seconds as `float` (D5): `"1 minute"` is `60`, `"999 millis"` is `0.999`.
  `set_time` takes milliseconds, as upstream's `setTime(timestamp: number)` does. Nanosecond
  reads are `int` (upstream `bigint`).
- `it.effect` is @effect/vitest's runner (vendor/effect-v4, sparse checkout; read with `git show
  HEAD:packages/vitest/src/internal/internal.ts`). Line 68: `TestEnv = Layer.mergeAll(
  TestConsole.layer, TestClock.layer())`; line 490: `effect: makeTester(flow(Effect.scoped,
  Effect.provide(TestEnv)))`. So each test body runs with TestEnv provided, inside a Scope.
  `TestClock.layer()` (TestClock.ts:571) builds the clock in that Scope and provides it both as
  the Clock and, through a TrackingScheduler, as the Scheduler.
- `it_effect` below is temporary, incomplete wiring, not an equivalent of `it.effect`: it runs
  `scoped(...)` around `TestClock.make()` (which requires the Scope, TestClock.ts:279) and
  `provide(Clock.context(clock))` around the body. Missing until Layer and scheduler tracking
  exist: the TrackingScheduler as Scheduler, and TestConsole. Every test runs under it,
  including the ones that also make a clock of their own, since that `make()` needs the Scope.
- `scoped` and `fork(..., start_immediately=True)` aren't built yet either, so they are looked
  up when the test runs, like `peffect.testing`.
- `fiber.pollUnsafe()` is `fiber.exit` (D6); `Exit.void` is a `Success` holding `None`.
  `Effect.delay(d)` is `sleep(d).flat_map(...)` and `Effect.forever` a `@gen` `while True` (D7).
- `forkChild({ startImmediately: true })` is `fork(eff, start_immediately=True)` (`fork_now`).
"""

import importlib
from typing import Any, Never

import peffect
from peffect import (
    Clock,
    Effect,
    Exit,
    Fiber,
    Success,
    ensuring,
    fork,
    gen,
    never,
    provide,
    run,
    sleep,
    succeed,
    sync,
    yield_now,
)


def load_testing() -> Any:
    """`peffect.testing`, imported when the test runs: ModuleNotFoundError until it exists."""
    return importlib.import_module("peffect.testing")


def scoped[A, E](eff: Effect[A, E, Any]) -> Effect[A, E, Never]:
    """`Effect.scoped` (internal/effect.ts:4206): AttributeError until Scope exists."""
    upcoming: Any = peffect  # typed lookup: replaced by `from peffect import scoped`
    return upcoming.scoped(eff)


def fork_now[A, E, R](eff: Effect[A, E, R]) -> Effect[Fiber[A, E], Never, R]:
    """`Effect.forkChild(eff, { startImmediately: true })`: TypeError until `fork` takes it."""
    upcoming: Any = fork  # typed lookup: becomes a direct call once the keyword exists
    return upcoming(eff, start_immediately=True)


def make_test_clock() -> Effect[Any, Never, Any]:
    """`TestClock.make()`: a new TestClock, not installed anywhere. Requires a Scope (its
    warning fiber is stopped by a finalizer on it), so R is the Scope service once that exists."""
    return load_testing().TestClock.make()


def ambient_test_clock() -> Effect[Any, Never, Never]:
    """`TestClock.testClockWith(Effect.succeed)`: the fiber's Clock, as a TestClock."""
    return load_testing().test_clock_with(succeed)


async def it_effect(body: Effect[None, Any, Any]) -> None:
    """`it.effect`'s runner for these tests: a Scope, and a fresh TestClock as the fiber's Clock."""

    def install(clock: Any) -> Effect[None, Any, Any]:
        return provide(Clock.context(clock))(body)

    await run(scoped(make_test_clock().flat_map(install)))


def is_void(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Success) and ex.value is None


# -- adjust: cancellation ---------------------------------------------------------------------


async def test_adjust_skips_the_cancelled_deadline_interrupted_sleep():
    """TestClock.test.ts:56 "adjust - skips the cancelled deadline (interrupted sleep)".

    The interrupted sleep's 1-minute deadline must not be visited: an observer records the time
    every time `adjust` yields to other fibers, and 60_000 never shows up."""
    observed: set[float] = set()

    @gen
    def program():
        test_clock = yield from make_test_clock()
        sleeper = yield from fork_now(test_clock.sleep(60))
        yield from yield_now
        yield from sleeper.interrupt()

        @gen
        def observe():
            while True:
                yield from sync(lambda: observed.add(test_clock.current_time_millis_unsafe()))
                yield from yield_now

        yield from fork(observe())
        yield from test_clock.adjust(120)

    await it_effect(program())
    assert 60_000 not in observed


# -- sleep ------------------------------------------------------------------------------------


async def test_sleep_does_not_require_passage_of_wall_time():
    """TestClock.test.ts:83 "sleep - does not require passage of wall time"."""
    t = load_testing()
    elapsed: list[bool] = []

    @gen
    def body():
        yield from fork(sleep(10 * 3600).flat_map(lambda _: sync(lambda: elapsed.append(True))))
        yield from t.adjust(11 * 3600)

    await it_effect(body())
    assert elapsed == [True]


async def test_sleep_delays_effects_until_time_is_adjusted():
    """TestClock.test.ts:93 "sleep - delays effects until time is adjusted"."""
    t = load_testing()
    elapsed: list[bool] = []

    @gen
    def body():
        fiber = yield from fork(
            sleep(10 * 3600).flat_map(lambda _: sync(lambda: elapsed.append(True)))
        )
        yield from t.adjust(9 * 3600)
        assert fiber.exit is None
        yield from t.adjust(11 * 3600)
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
        yield from sleep(1)
        yield from sync(lambda: events.append("cleanup finished"))

    @gen
    def program():
        clock = yield from make_test_clock()
        fiber = yield from fork_now(provide(Clock.context(clock))(never.pipe(ensuring(cleanup()))))
        fiber.interrupt_unsafe()
        yield from yield_now
        assert events == ["cleanup started"]
        assert fiber.exit is None
        yield from clock.adjust(1)
        yield from fiber.await_()

    await it_effect(program())
    assert events == ["cleanup started", "cleanup finished"]


async def test_sleep_waits_the_full_duration_each_time_a_sleep_effect_runs():
    """TestClock.test.ts:143 "sleep - waits the full duration each time a sleep effect runs".

    One sleep *effect*, run twice: each run measures from its own start, not from when the
    effect was built."""
    t = load_testing()

    @gen
    def body():
        clock = yield from ambient_test_clock()
        one_second = clock.sleep(1)
        yield from t.adjust(5)
        for _ in range(2):
            fiber = yield from fork(one_second)
            yield from t.adjust(0.999)
            assert fiber.exit is None
            yield from t.adjust(0.001)
            assert is_void(fiber.exit)

    await it_effect(body())


# -- setTime ----------------------------------------------------------------------------------


async def test_set_time_sleep_correctly_handles_new_set_time():
    """TestClock.test.ts:157 "setTime - sleep correctly handles new set time"."""
    t = load_testing()
    elapsed: list[bool] = []

    @gen
    def body():
        yield from fork(sleep(10 * 3600).flat_map(lambda _: sync(lambda: elapsed.append(True))))
        assert elapsed == []
        yield from t.set_time(11 * 3600 * 1000)  # Duration.toMillis(Duration.hours(11))
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
    t = load_testing()
    ticks: list[int] = []

    @gen
    def tick_forever():
        while True:
            yield from sleep(1)
            yield from sync(lambda: ticks.append(1))
            yield from fork(yield_now).flat_map(lambda f: f.join())

    @gen
    def body():
        fiber = yield from fork(tick_forever())
        yield from t.set_time(5_000)
        yield from fiber.interrupt()

    await it_effect(body())
    assert len(ticks) == 5


# -- adjust -----------------------------------------------------------------------------------


async def test_adjust_advances_the_clock_when_detached():
    """TestClock.test.ts:183 "adjust - advances the clock when detached, copied or inherited".

    Only the detached part is ported: a bound `adjust` taken off the clock still advances it.
    The copied (`{ ...clock }`) and inherited (`Object.create(clock)`) parts depend on JS own
    fields and prototype delegation, which Python objects don't have; they are not ported, so
    the expected time is one adjustment, 1000, instead of upstream's three, 3000."""

    @gen
    def body():
        clock = yield from ambient_test_clock()
        adjust = clock.adjust
        yield from adjust(1)
        assert clock.current_time_millis_unsafe() == 1000

    await it_effect(body())


async def test_adjust_advances_wall_and_monotonic_time():
    """TestClock.test.ts:194 "adjust - advances wall and monotonic time"."""

    @gen
    def program():
        test_clock = yield from make_test_clock()
        yield from test_clock.adjust(1)
        assert test_clock.current_time_millis_unsafe() == 1_000
        assert test_clock.monotonic_time_nanos_unsafe() == 1_000_000_000
        assert (yield from test_clock.monotonic_time_nanos) == 1_000_000_000

    await it_effect(program())


async def test_adjust_keeps_nanosecond_access_total_after_infinite_durations():
    """TestClock.test.ts:212 "adjust - keeps nanosecond access total after infinite durations".
    `typeof x === "bigint"` is `type(x) is int`."""

    @gen
    def program():
        for duration in (float("inf"), float("-inf")):
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
            yield from test_clock.sleep(1)
            monotonic: Effect[int, Never, Never] = test_clock.monotonic_time_nanos
            observed.append((yield from monotonic))

        yield from fork(sleeper())
        yield from test_clock.adjust(2)
        assert test_clock.monotonic_time_nanos_unsafe() == 2_000_000_000

    await it_effect(program())
    assert observed == [1_000_000_000]


async def test_adjust_runs_sleeps_registered_after_a_yield():
    """TestClock.test.ts:256 "adjust - runs sleeps registered after a yield"."""
    t = load_testing()
    ticks: list[int] = []

    @gen
    def tick_forever():
        while True:
            yield from sleep(1)
            yield from sync(lambda: ticks.append(1))
            yield from yield_now

    @gen
    def body():
        fiber = yield from fork(tick_forever())
        yield from t.adjust(5)
        yield from fiber.interrupt()

    await it_effect(body())
    assert len(ticks) == 5
