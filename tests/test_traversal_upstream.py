"""Concurrent forEach, pinned Effect v4 289a53f7, packages/effect/test/Effect.test.ts ports:
  :783 "bounded"                                                  :819 "bounded interrupt"
  :834 "preserves interruption when a child cleanup dies"
  :852 "preserves a recorded sibling failure on external interruption"
  :882 "elects typed failures when a concurrent sibling finalizer dies"
  :907 "interrupts started workers when the mapper throws during initial pumping"
  :933 "preserves a sibling cleanup defect when the mapper throws"
  :960 "interrupts started workers when observer-driven refill throws"
  :771 "sequential"   :777 "unbounded"   :990 "length = 0"   :996 "string"
Source-derived, labelled `test_source_*` (no upstream test): ordered results when workers end
out of order, a fresh run per execution, and the concurrency floor of 1; added after the
implementation, because scratch mutations survived the ports: a mapper defect leaves only
after started workers are interrupted, and workers inherit the caller's mask.
Not ported: "sequential interrupt", "unbounded interrupt" and "unbounded fail" need TestClock.
Semantics: forEach and iterateConcurrentImpl, internal/effect.ts:5030-5072, :5160-5302.

Python names: `Effect.forEach(items, f, { concurrency })` is
`for_each(items, f, *, concurrency=...)`; `f` takes `(item, index)` as upstream's does.

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
from typing import Any, Never

import pytest

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
    Success,
    async_,
    die,
    ensuring,
    exit_,
    fail,
    fail_cause,
    for_each,
    fork,
    gen,
    never,
    on_interrupt,
    run,
    run_exit,
    succeed,
    sync,
    uninterruptible,
)
from peffect.core import AnyFiber, WithFiber

WATCHDOG_SECONDS = 2


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


# -- upstream forEach cases added with the implementation --------------------------------


async def test_sequential():
    """Effect.test.ts:771 "sequential"."""
    assert await run_guarded(for_each([1, 2, 3], lambda x, _i: succeed(x))) == [1, 2, 3]


async def test_unbounded():
    """Effect.test.ts:777 "unbounded"."""
    results = await run_guarded(
        for_each([1, 2, 3], lambda x, _i: succeed(x), concurrency="unbounded")
    )
    assert results == [1, 2, 3]


async def test_length_0():
    """Effect.test.ts:990 "length = 0"."""
    empty: list[int] = []
    assert await run_guarded(for_each(empty, lambda x, _i: succeed(x))) == []


async def test_string():
    """Effect.test.ts:996 "string"."""
    assert await run_guarded(for_each("abc", lambda x, _i: succeed(x))) == ["a", "b", "c"]


# -- source-derived (no upstream test), each labelled ---------------------------------------------


async def test_source_concurrent_results_keep_input_order_when_workers_finish_out_of_order():
    """forEachConcurrent.step, internal/effect.ts:5307-5312: each Exit is written to `out` at its
    own index, so results follow the input, not completion order."""
    gate = [Deferred[None].make_unsafe() for _ in range(3)]
    finished = [Deferred[None].make_unsafe() for _ in range(3)]
    order: list[int] = []

    def item(i: int, index: int) -> Effect[str, Never, Never]:
        def record() -> str:
            order.append(i)
            return f"{i}@{index}"

        return (
            gate[i]
            .await_()
            .flat_map(lambda _: sync(record))
            .pipe(ensuring(finished[i].succeed(None)))
        )

    @gen
    def program():
        fiber: Fiber[list[str], Never] = yield from fork(
            for_each([0, 1, 2], item, concurrency="unbounded"), start_immediately=True
        )
        for i in (2, 1, 0):
            yield from gate[i].succeed(None)
            yield from finished[i].await_()
        results = yield from fiber.join()
        assert order == [2, 1, 0]
        assert results == ["0@0", "1@1", "2@2"]

    await run_guarded(program(), *gate, *finished)


async def test_source_each_run_maps_afresh_into_its_own_list():
    """forEach, internal/effect.ts:5055-5071: everything is built inside `suspend`, so running
    one for_each effect twice calls the mapper again and returns a new list each time."""
    calls: list[int] = []

    def item(x: int, _index: int) -> Effect[int, Never, Never]:
        calls.append(x)
        return succeed(x * 10)

    for concurrency in (1, 2):
        calls.clear()
        eff = for_each([1, 2, 3], item, concurrency=concurrency)
        first = await run_guarded(eff)
        second = await run_guarded(eff)
        assert first == second == [10, 20, 30]
        assert first is not second
        assert calls == [1, 2, 3, 1, 2, 3]


@pytest.mark.parametrize("concurrency", [None, 1, 0, -3])
async def test_source_concurrency_below_one_and_the_default_run_one_at_a_time(
    concurrency: int | None,
):
    """resolveConcurrency, internal/effect.ts:5111-5112: `max(1, n)`, so 0 and negatives, like
    the default, take forEachSequential (:5085-5108): the next item's mapper is not called
    until the current item's effect has finished."""
    gate = Deferred[None].make_unsafe()
    mapped: list[int] = []

    def item(i: int, _index: int) -> Effect[int, Never, Never]:
        mapped.append(i)
        return gate.await_().as_(i) if i == 0 else succeed(i)

    @gen
    def program():
        eff = (
            for_each([0, 1, 2], item)
            if concurrency is None
            else for_each([0, 1, 2], item, concurrency=concurrency)
        )
        fiber: Fiber[list[int], Never] = yield from fork(eff, start_immediately=True)
        assert mapped == [0]
        yield from gate.succeed(None)
        assert (yield from fiber.join()) == [0, 1, 2]
        assert mapped == [0, 1, 2]

    await run_guarded(program(), gate)


@pytest.mark.parametrize("refill", [False, True], ids=["initial", "refill"])
async def test_source_a_mapper_defect_leaves_only_after_started_workers_are_interrupted(
    refill: bool,
):
    """failDefect, internal/effect.ts:5179-5186: a mapper raising, while pumping the first window
    or refilling from an observer, interrupts and awaits started workers before the defect
    leaves for_each, so their cleanup has run when the caller sees the Exit, before the
    caller's own supervision could stop them. Added after the implementation: a mutation that
    skipped this wait survived the ports, whose fibers end (and supervise) right after."""
    release = Deferred[None].make_unsafe()
    defect = RuntimeError("mapper defect")
    cleaned: list[int] = []

    def worker(i: int) -> Effect[None, Never, Never]:
        return never.pipe(on_interrupt(lambda _: sync(lambda: cleaned.append(i))))

    def item(i: int, _index: int) -> Effect[None, Never, Never]:
        if not refill and i == 0:
            return worker(0)
        if refill and i == 0:
            return release.await_()
        if refill and i == 1:
            return release.succeed(None).flat_map(lambda _: worker(1))
        raise defect

    @gen
    def program():
        ex = yield from exit_(for_each(range(3 if refill else 2), item, concurrency=2))
        assert cleaned == [1 if refill else 0]
        assert_exit_defect(ex, defect)

    await run_guarded(program(), release)


async def test_source_workers_inherit_the_callers_mask():
    """forkUnsafe(parentFiber, eff, true, true, "inherit"), internal/effect.ts:5232: a worker
    runs masked when for_each does, so interrupting its own fiber does not cut it short. As
    test_source_parallel_finalizers_inherit_the_close_mask (tests/test_scope_parallel.py)."""
    done: list[int] = []

    def item(i: int, _index: int) -> Effect[None, Never, Never]:
        def self_interrupt(fiber: AnyFiber) -> Effect[None, Never, Never]:
            fiber.interrupt_unsafe()
            return sync(lambda: done.append(i))

        return WithFiber(self_interrupt)

    ex = await run_guarded(exit_(uninterruptible(for_each([0, 1], item, concurrency=2))))
    assert isinstance(ex, Success), ex
    assert sorted(done) == [0, 1]


async def test_source_cleanup_defects_keep_worker_insertion_order():
    """JS Set iteration in iterateConcurrentImpl is insertion ordered (:5279-5283).

    Repeated allocations expose Python set's varying order; every run must preserve A then B.
    """
    for _ in range(100):
        effects = [
            never.pipe(ensuring(fail_cause(Cause((Die("cleanup A"),))))),
            never.pipe(ensuring(fail_cause(Cause((Die("cleanup B"),))))),
            fail("error"),
        ]
        ex = await run_exit(for_each(effects, lambda eff, _: eff, concurrency="unbounded"))
        assert isinstance(ex, Failure)
        assert ex.cause.reasons == (Die("error"), Die("cleanup A"), Die("cleanup B"))


async def test_source_traversal_cancellation_combines_interrupt_before_cleanup_defects():
    """AsyncFinalizer :1268-1271 combines/elects the cancellation cause, including traversal."""

    @gen
    def program():
        worker = async_(lambda _: fail_cause(Cause((Fail("typed"), Die("defect")))))
        fiber = yield from fork(
            for_each([worker, never], lambda eff, _: eff, concurrency=2), start_immediately=True
        )
        yield from fiber.interrupt()
        return (yield from fiber.await_())

    ex = await run(program())
    assert isinstance(ex, Failure)
    assert isinstance(ex.cause.reasons[0], Interrupt)
    assert ex.cause.failures == []
    assert ex.cause.defects == ["typed", "defect"]
