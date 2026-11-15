"""Concurrent forEach, pinned Effect v4 289a53f7, packages/effect/test/Effect.test.ts ports:
  :783 "bounded"                                                  :819 "bounded interrupt"
  :834 "preserves interruption when a child cleanup dies"
  :852 "preserves a recorded sibling failure on external interruption"
  :882 "elects typed failures when a concurrent sibling finalizer dies"
  :907 "interrupts started workers when the mapper throws during initial pumping"
  :933 "preserves a sibling cleanup defect when the mapper throws"
  :960 "interrupts started workers when observer-driven refill throws"
Semantics: forEach and iterateConcurrentImpl, internal/effect.ts:5030-5072, :5160-5302.

Python names: `Effect.forEach(items, f, { concurrency })` is the planned public
`for_each(items, f, *, concurrency=...)`; `f` takes `(item, index)` as upstream's does. Until
it exists, the typed `for_each` below looks it up on `peffect`, so each test is red on the
missing API (the precedent is the `_api` lookup of the bulk-fiber red commit).

Harness adaptations, each named again where used:
- Exit helpers are upstream's: `has_interrupts` is Exit.hasInterrupts; `assert_exit_defect` is
  this file's assertExitDefect (:40-47: hasDies, then findDefect, the first Die, is the defect);
  `find_error` is Cause.findError (the first Fail's error). `forkChild` is `fork`,
  `Fiber.interrupt` is `fiber.interrupt()`, `Effect.callback` is `async_`, whose register
  returns the canceller effect.
- Upstream's `interruptUnsafe` and Deferred completion evaluate the woken fiber at once; peffect
  wakes a parked fiber on the next loop turn (`Fiber._wake`, call_soon). So where upstream reads
  `Deferred.isDone(gate)` or `pollUnsafe()` straight after the step that causes it, the port
  awaits that gate or the fiber itself (as tests/test_fiber_bulk_upstream.py does), and keeps
  each `pollUnsafe()` undefined check that holds while a gate is shut.
- :819 needs TestClock (not built). Each `Effect.delay(200)` becomes a per-item gate pair: the
  item opens `started[i]`, then waits on `tick[i]` standing for its 200ms. `adjust(350)` is: open
  the ticks of items 1 and 2 (their sleeps end at 200), then await `started` of items 3 and 4
  (the refills begin sleeping until 400, so at 350 they are still parked). No fake clock.
- :882 uses `Effect.all([...], { concurrency })`, which is `forEach(arr, identity, options)`
  (internal/effect.ts:4770-4786); it is ported through `for_each` with an identity mapper.
  Upstream dies with strings; `die()` takes an exception, so the string defect is built as
  `fail_cause(Cause((Die("cleanup defect"),)))`, keeping upstream's payload.
Gates are real Deferreds made outside the program, and `ensuring` opens each test-owned gate
even when an assertion fails, so masked worker cleanup never strands. `asyncio.wait_for` is a
hang guard only; gates set the order.
"""

import asyncio
from collections.abc import Callable, Iterable
from typing import Any, Literal, Never, cast

import peffect
from peffect import (
    Cause,
    Deferred,
    Die,
    Effect,
    Exit,
    Fail,
    Failure,
    Fiber,
    Interrupt,
    async_,
    die,
    ensuring,
    exit_,
    fail,
    fail_cause,
    fork,
    gen,
    never,
    on_interrupt,
    run,
    succeed,
    sync,
)

WATCHDOG_SECONDS = 2


def for_each[A, B, E, R](
    items: Iterable[A],
    f: Callable[[A, int], Effect[B, E, R]],
    *,
    concurrency: int | Literal["unbounded"] = 1,
) -> Effect[list[B], E, R]:
    """The planned `peffect.for_each`, looked up so a missing API is a red test, not an import
    error for the whole file."""
    return _api("for_each")(items, f, concurrency=concurrency)


def _api(name: str) -> Callable[..., Any]:
    return cast(Callable[..., Any], getattr(peffect, name))


def has_interrupts(ex: Exit[Any, Any]) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def assert_exit_defect(ex: Exit[Any, Any] | None, defect: object) -> None:
    """Effect.test.ts:40-47 assertExitDefect."""
    assert isinstance(ex, Failure)
    defects = [r.defect for r in ex.cause.reasons if isinstance(r, Die)]
    assert defects, ex
    assert defects[0] is defect


def find_error(ex: Exit[Any, Any]) -> object:
    """Cause.findError: the first Fail's error."""
    assert isinstance(ex, Failure)
    errors = [r.error for r in ex.cause.reasons if isinstance(r, Fail)]
    assert errors, ex
    return errors[0]


def open_all(*gates: Deferred[None]) -> Effect[None, Never, Never]:
    eff: Effect[Any, Never, Never] = succeed(None)
    for gate in gates:
        eff = eff.flat_map(lambda _, g=gate: g.succeed(None))
    return eff.as_(None)


async def run_guarded[A](eff: Effect[A, Never, Never], *gates: Deferred[None]) -> A:
    return await asyncio.wait_for(run(eff.pipe(ensuring(open_all(*gates)))), WATCHDOG_SECONDS)


async def test_bounded():
    """Effect.test.ts:783 "bounded"."""
    results = await run_guarded(for_each([1, 2, 3, 4, 5], lambda x, _i: succeed(x), concurrency=2))
    assert results == [1, 2, 3, 4, 5]


async def test_bounded_interrupt():
    """Effect.test.ts:819 "bounded interrupt" (TestClock adaptation: per-item gates, above)."""
    started = {i: Deferred[None].make_unsafe() for i in range(1, 7)}
    tick = {i: Deferred[None].make_unsafe() for i in range(1, 7)}
    done: list[int] = []

    def delayed(i: int, _index: int) -> Effect[int, Never, Never]:
        def record() -> int:
            done.append(i)
            return i

        return (
            started[i]
            .succeed(None)
            .flat_map(lambda _: tick[i].await_())
            .flat_map(lambda _: sync(record))
        )

    @gen
    def program():
        fiber: Fiber[list[int], Never] = yield from fork(
            for_each([1, 2, 3, 4, 5, 6], delayed, concurrency=2)
        )
        yield from tick[1].succeed(None)  # TestClock.adjust(350): the first two sleeps end
        yield from tick[2].succeed(None)
        yield from started[3].await_()  # ... and the two refills are parked mid-sleep
        yield from started[4].await_()
        yield from fiber.interrupt()
        result = yield from fiber.await_()
        assert has_interrupts(result)
        assert done == [1, 2]

    await run_guarded(program(), *started.values(), *tick.values())


async def test_preserves_interruption_when_a_child_cleanup_dies():
    """Effect.test.ts:834 "preserves interruption when a child cleanup dies"."""
    started = Deferred[None].make_unsafe()
    defect = RuntimeError("cleanup defect")

    def item(i: int, _index: int) -> Effect[None, Never, Never]:
        if i == 0:
            return started.succeed(None).flat_map(lambda _: async_(lambda _resume: die(defect)))
        return never

    @gen
    def program():
        fiber: Fiber[list[None], Never] = yield from fork(
            for_each([0, 1], item, concurrency=2), start_immediately=True
        )
        yield from started.await_()
        yield from fiber.interrupt()
        ex = yield from fiber.await_()
        assert has_interrupts(ex)
        assert_exit_defect(ex, defect)

    await run_guarded(program(), started)


async def test_preserves_a_recorded_sibling_failure_on_external_interruption():
    """Effect.test.ts:852 "preserves a recorded sibling failure on external interruption"."""
    started = Deferred[None].make_unsafe()
    cleanup_started = Deferred[None].make_unsafe()
    release_cleanup = Deferred[None].make_unsafe()

    def item(i: int, _index: int) -> Effect[None, str, Never]:
        if i == 0:
            cleanup = cleanup_started.succeed(None).flat_map(lambda _: release_cleanup.await_())
            return (
                started.succeed(None)
                .flat_map(lambda _: never)
                .pipe(on_interrupt(lambda _: cleanup))
            )
        return started.await_().flat_map(lambda _: fail("sibling failure"))

    @gen
    def program():
        fiber: Fiber[list[None], str] = yield from fork(
            for_each([0, 1], item, concurrency=2), start_immediately=True
        )
        yield from cleanup_started.await_()
        fiber.interrupt_unsafe()
        yield from release_cleanup.succeed(None)
        ex = yield from fiber.await_()
        assert has_interrupts(ex)
        assert isinstance(ex, Failure)
        assert find_error(ex) == "sibling failure"

    await run_guarded(program(), started, cleanup_started, release_cleanup)


async def test_elects_typed_failures_when_a_concurrent_sibling_finalizer_dies():
    """Effect.test.ts:882 "elects typed failures when a concurrent sibling finalizer dies"
    (Effect.all as for_each with identity; string defect kept, above)."""
    cleanup_defect: Effect[Never, Never, Never] = fail_cause(Cause((Die("cleanup defect"),)))
    effects: list[Effect[Never, str, Never]] = [
        never.pipe(ensuring(cleanup_defect)),
        fail("error"),
    ]
    ex = await run_guarded(exit_(for_each(effects, lambda e, _i: e, concurrency="unbounded")))
    assert isinstance(ex, Failure)
    assert ex.cause == Cause((Die("error"), Die("cleanup defect")))


async def test_interrupts_started_workers_when_the_mapper_throws_during_initial_pumping():
    """Effect.test.ts:907 "interrupts started workers when the mapper throws during initial
    pumping". Upstream reads `Deferred.isDone(interruptStarted)` and the defined `pollUnsafe()`
    synchronously; the port awaits the gate and the fiber (scheduled wake, above)."""
    interrupt_started = Deferred[None].make_unsafe()
    interrupt_finished = Deferred[None].make_unsafe()
    defect = RuntimeError("mapper defect")

    def item(i: int, _index: int) -> Effect[None, Never, Never]:
        if i == 0:
            cleanup = interrupt_started.succeed(None).flat_map(
                lambda _: interrupt_finished.await_()
            )
            return never.pipe(on_interrupt(lambda _: cleanup))
        raise defect

    @gen
    def program():
        fiber: Fiber[list[None], Never] = yield from fork(
            for_each([0, 1], item, concurrency=2), start_immediately=True
        )
        yield from interrupt_started.await_()
        assert fiber.exit is None
        yield from interrupt_finished.succeed(None)
        ex = yield from fiber.await_()
        assert_exit_defect(ex, defect)

    await run_guarded(program(), interrupt_started, interrupt_finished)


async def test_preserves_a_sibling_cleanup_defect_when_the_mapper_throws():
    """Effect.test.ts:933 "preserves a sibling cleanup defect when the mapper throws"."""
    mapper_defect = RuntimeError("mapper defect")
    cleanup_defect = RuntimeError("cleanup defect")
    cleaned_up: list[bool] = []

    def cancel() -> None:
        cleaned_up.append(True)
        raise cleanup_defect

    def item(i: int, _index: int) -> Effect[None, Never, Never]:
        if i == 0:
            return async_(lambda _resume: sync(cancel))
        raise mapper_defect

    ex = await run_guarded(exit_(for_each([0, 1], item, concurrency=2)))
    assert cleaned_up == [True]
    assert isinstance(ex, Failure)
    assert [r.defect for r in ex.cause.reasons if isinstance(r, Die)] == [
        mapper_defect,
        cleanup_defect,
    ]


async def test_interrupts_started_workers_when_observer_driven_refill_throws():
    """Effect.test.ts:960 "interrupts started workers when observer-driven refill throws".
    The `isDone` and second defined `pollUnsafe()` reads await, as in :907."""
    release = Deferred[None].make_unsafe()
    interrupt_started = Deferred[None].make_unsafe()
    interrupt_finished = Deferred[None].make_unsafe()
    defect = RuntimeError("refill defect")

    def item(i: int, _index: int) -> Effect[None, Never, Never]:
        if i == 0:
            return release.await_()
        if i == 1:
            cleanup = interrupt_started.succeed(None).flat_map(
                lambda _: interrupt_finished.await_()
            )
            return never.pipe(on_interrupt(lambda _: cleanup))
        raise defect

    @gen
    def program():
        fiber: Fiber[list[None], Never] = yield from fork(
            for_each([0, 1, 2], item, concurrency=2), start_immediately=True
        )
        assert fiber.exit is None
        yield from release.succeed(None)
        yield from interrupt_started.await_()
        assert fiber.exit is None
        yield from interrupt_finished.succeed(None)
        ex = yield from fiber.await_()
        assert_exit_defect(ex, defect)

    await run_guarded(program(), release, interrupt_started, interrupt_finished)
