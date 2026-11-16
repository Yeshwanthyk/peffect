"""SynchronizedRef unit at Effect v4 pin 289a53f7.

Upstream: test/SynchronizedRef.test.ts (all 8 cases); implementation src/SynchronizedRef.ts.
The first section ports upstream cases; the second is source-derived and labelled with the
source it reads. The generated concurrent property is in test_ref_props.py.

Names are snake_case instance methods: `SynchronizedRef[A].make(a)` / `make_unsafe(a)` are
classmethods; `SynchronizedRef.getAndUpdateEffect(ref, f)` is `ref.get_and_update_effect(f)`.
`backing` (the Ref) and `semaphore` (a Semaphore of 1) are public fields, as upstream's
interface; the source-derived tests read `semaphore.taken`/`waiters` to show the permit is back.

Adaptations (as test_semaphore_upstream.py and test_ref_upstream.py):
- `State` objects are `State` enum members; `deepStrictEqual`/`strictEqual` on them is `is`.
- `Effect.succeedSome(a)` is `succeed(Some(a))`, `Effect.succeedNone` is `succeed(None)`.
- `deepStrictEqual(exit, Exit.fail(e))` checks a Failure whose cause is `Cause.fail(e)`.
- `Effect.forkChild(eff)` is `fork(eff)`; `Fiber.interrupt(f)` is `f.interrupt()`.
- "interrupt parent fiber and update" uses `Deferred.complete(deferred, SynchronizedRef.make(..))`,
  which runs the make effect and stores its result; Deferred.complete is not ported
  (deferred.py), so the child runs make itself and stores the ref with `succeed`.
"""

import asyncio
from collections.abc import Callable
from enum import Enum
from typing import Any, Literal, Never

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
    die,
    exit_,
    fail,
    fork,
    gen,
    provide,
    run,
    run_fork,
    succeed,
)
from peffect import yield_now as yield_now_
from peffect.fiber import Task
from peffect.option import Option, Some
from peffect.scheduler import MixedScheduler, Scheduler, SchedulerDispatcher
from peffect.synchronized_ref import SynchronizedRef

current = "value"
update = "new value"
failure = "failure"


class State(Enum):
    ACTIVE = "Active"
    CHANGED = "Changed"
    CLOSED = "Closed"


Active, Changed, Closed = State.ACTIVE, State.CHANGED, State.CLOSED


async def bounded(eff: Effect[None, Any, Never], seconds: float = 5) -> None:
    """Watchdog only (test_semaphore_upstream.py `bounded`): a lost wakeup fails, not hangs."""
    async with asyncio.timeout(seconds):
        await run(eff)


def settle(fibers: list[Fiber[Any, Any]]) -> Effect[None, Never, Never]:
    """Yield until every fiber has ended; a bound, not a timing assumption: the queued wakes
    each need one dispatcher turn."""

    @gen
    def go():
        for _ in range(4 * len(fibers) + 4):
            if all(f.exit is not None for f in fibers):
                return
            yield from yield_now_

    return go()


def has_interrupts[A, E](ex: Exit[A, E] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def succeeded[A, E](ex: Exit[A, E] | None, value: A) -> bool:
    return isinstance(ex, Success) and ex.value == value


def released(sref: SynchronizedRef[Any]) -> bool:
    return sref.semaphore.taken == 0 and not sref.semaphore.waiters


# == upstream: SynchronizedRef.test.ts =========================================================


async def test_get():
    """SynchronizedRef.test.ts:32."""
    eff = SynchronizedRef[str].make(current).flat_map(lambda r: r.get())
    assert await run(eff) == current


async def test_get_and_update_effect_happy_path():
    """SynchronizedRef.test.ts:37."""

    @gen
    def program():
        ref = yield from SynchronizedRef[str].make(current)
        result1 = yield from ref.get_and_update_effect(lambda _: succeed(update))
        result2 = yield from ref.get()
        assert result1 == current
        assert result2 == update

    await run(program())


async def test_get_and_update_effect_with_failure():
    """SynchronizedRef.test.ts:45."""

    @gen
    def program():
        ref = yield from SynchronizedRef[str].make(current)
        result = yield from exit_(ref.get_and_update_effect(lambda _: fail(failure)))
        assert isinstance(result, Failure)
        assert result.cause == Cause.fail(failure)

    await run(program())


def some_effect(
    f: Callable[[State], Option[State]],
) -> Callable[[State], Effect[Option[State], Never, Never]]:
    return lambda s: succeed(f(s))


async def test_get_and_update_some_effect_happy_path():
    """SynchronizedRef.test.ts:51."""

    @gen
    def program():
        ref = yield from SynchronizedRef[State].make(Active)
        result1 = yield from ref.get_and_update_some_effect(
            some_effect(lambda s: Some(Changed) if s is Closed else None)
        )
        result2 = yield from ref.get()
        assert result1 is Active
        assert result2 is Active

    await run(program())


async def test_get_and_update_some_effect_twice():
    """SynchronizedRef.test.ts:62."""

    def second(s: State) -> Option[State]:
        return Some(Active) if s is Closed else Some(Closed) if s is Changed else None

    @gen
    def program():
        ref = yield from SynchronizedRef[State].make(Active)
        result1 = yield from ref.get_and_update_some_effect(
            some_effect(lambda s: Some(Changed) if s is Active else None)
        )
        result2 = yield from ref.get_and_update_some_effect(some_effect(second))
        result3 = yield from ref.get()
        assert result1 is Active
        assert result2 is Changed
        assert result3 is Closed

    await run(program())


async def test_get_and_update_some_effect_with_failure():
    """SynchronizedRef.test.ts:80."""

    def pf(s: State) -> Effect[Option[State], str, Never]:
        return fail(failure) if s is Active else succeed(None)

    @gen
    def program():
        ref = yield from SynchronizedRef[State].make(Active)
        result = yield from exit_(ref.get_and_update_some_effect(pf))
        assert isinstance(result, Failure)
        assert result.cause == Cause.fail(failure)

    await run(program())


async def test_get_and_update_some_effect_interrupt_parent_fiber_and_update():
    """SynchronizedRef.test.ts:93 (Deferred.complete adapted, see the module docstring)."""

    @gen
    def program():
        deferred = yield from Deferred[SynchronizedRef[State], Never].make()
        latch = yield from Deferred[None, Never].make()

        @gen
        def make_and_wait():
            ref = yield from SynchronizedRef[State].make(Active)
            yield from deferred.succeed(ref)
            yield from latch.await_()

        fiber = yield from fork(make_and_wait())
        ref = yield from deferred.await_()
        yield from fiber.interrupt()
        result = yield from ref.update_and_get_effect(lambda _: succeed(Closed))
        assert result is Closed

    await bounded(program())


async def test_get_and_update_some_updates_the_backing_ref():
    """SynchronizedRef.test.ts:106."""

    @gen
    def program():
        ref = yield from SynchronizedRef[int].make(1)
        previous = yield from ref.get_and_update_some(lambda n: Some(n + 1))
        assert previous == 1
        assert (yield from ref.get()) == 2

    await run(program())


# == source-derived: src/SynchronizedRef.ts =====================================================


class Boom(Exception):
    pass


type Bad = Callable[[int], Effect[Never, str, Never]]
type EffectOp = Callable[[SynchronizedRef[int], Bad], Effect[object, str, Never]]

EFFECT_OPS: list[tuple[str, EffectOp]] = [
    ("get_and_update_effect", lambda r, f: r.get_and_update_effect(f)),
    ("get_and_update_some_effect", lambda r, f: r.get_and_update_some_effect(f)),
    ("modify_effect", lambda r, f: r.modify_effect(f)),
    ("modify_some_effect", lambda r, f: r.modify_some_effect(f)),
    ("update_effect", lambda r, f: r.update_effect(f)),
    ("update_and_get_effect", lambda r, f: r.update_and_get_effect(f)),
    ("update_some_effect", lambda r, f: r.update_some_effect(f)),
    ("update_some_and_get_effect", lambda r, f: r.update_some_and_get_effect(f)),
]


def raising(_: int) -> Never:
    raise Boom


BAD: dict[str, tuple[Bad, type]] = {
    "fail": (lambda _: fail(failure), Fail),
    "die": (lambda _: die(Boom()), Die),
    "raise": (raising, Die),
}


@pytest.mark.parametrize("bad", sorted(BAD))
@pytest.mark.parametrize("op", [op for _, op in EFFECT_OPS], ids=[n for n, _ in EFFECT_OPS])
async def test_an_effectful_update_that_fails_writes_nothing_and_returns_the_permit(
    op: EffectOp, bad: str
):
    """Every *Effect body writes only in the map/flatMap after `f(value)` succeeds, inside
    `semaphore.withPermit(Effect.suspend(...))`: a failure, a defect, or a throw from `f`
    itself (a defect of the suspend) leaves the value and releases the permit."""
    f, reason = BAD[bad]
    sref = SynchronizedRef[int].make_unsafe(7)
    ex = await run(exit_(op(sref, f)))
    assert isinstance(ex, Failure)
    assert [type(r) for r in ex.cause.reasons] == [reason]
    assert sref.get_unsafe() == 7
    assert released(sref)
    assert await run(sref.update_and_get(lambda n: n + 1)) == 8


type PureOp = Callable[[SynchronizedRef[int]], Effect[object, Never, Never]]

PURE_RAISING: list[tuple[str, PureOp]] = [
    ("get_and_update", lambda r: r.get_and_update(raising)),
    ("get_and_update_some", lambda r: r.get_and_update_some(raising)),
    ("modify", lambda r: r.modify(raising)),
    ("modify_some", lambda r: r.modify_some(raising)),
    ("update", lambda r: r.update(raising)),
    ("update_and_get", lambda r: r.update_and_get(raising)),
    ("update_some", lambda r: r.update_some(raising)),
    ("update_some_and_get", lambda r: r.update_some_and_get(raising)),
]


@pytest.mark.parametrize("op", [op for _, op in PURE_RAISING], ids=[n for n, _ in PURE_RAISING])
async def test_a_raising_pure_update_is_a_defect_and_returns_the_permit(op: PureOp):
    """The pure operations are `withPermit(Ref.op)`; Ref.op computes before it assigns."""
    sref = SynchronizedRef[int].make_unsafe(7)
    ex = await run(exit_(op(sref)))
    assert isinstance(ex, Failure)
    assert [type(r) for r in ex.cause.reasons] == [Die]
    assert sref.get_unsafe() == 7
    assert released(sref)


async def test_interrupting_a_parked_update_writes_nothing_and_admits_the_next_caller():
    """withPermit masks acquisition and restores the body (Semaphore.ts:358-372): an update
    parked inside `f` is interruptible, writes nothing, and its exit releases the permit to a
    caller queued behind it."""

    @gen
    def program():
        sref = yield from SynchronizedRef[int].make(0)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(
            sref.update_effect(lambda n: gate.await_().map(lambda _: n + 1)),
            start_immediately=True,
        )
        queued = yield from fork(sref.update(lambda n: n + 10), start_immediately=True)
        assert holder.exit is None and queued.exit is None
        yield from holder.interrupt()
        yield from settle([queued])
        assert has_interrupts(holder.exit)
        assert succeeded(queued.exit, None)
        assert sref.get_unsafe() == 10
        assert released(sref)

    await bounded(program())


def produce(ran: list[bool], n: int) -> int:
    ran[0] = True
    return n + 1


type Swept = Callable[[SynchronizedRef[int], list[bool]], Effect[object, Never, Never]]
type Outcome = tuple[str, bool, int]  # (exit, the function produced or ran, final value)

# One write after a map, one after a flatMap, one Ref op under the permit, and one with no
# function. Each set is every outcome the source allows for 0 -> 1, so equality says both that
# nothing else happens and that the sweep reaches each allowed one.
SWEPT: list[tuple[str, Swept, set[Outcome]]] = [
    (
        "update_and_get_effect",
        lambda r, ran: r.update_and_get_effect(lambda n: yield_now_.map(lambda _: produce(ran, n))),
        {("interrupt", False, 0), ("interrupt", True, 0), ("interrupt", True, 1), ("ok", True, 1)},
    ),
    (
        "update_some_and_get_effect",
        lambda r, ran: r.update_some_and_get_effect(
            lambda n: yield_now_.map(lambda _: Some(produce(ran, n)))
        ),
        {("interrupt", False, 0), ("interrupt", True, 0), ("interrupt", True, 1), ("ok", True, 1)},
    ),
    (
        "update",
        lambda r, ran: r.update(lambda n: produce(ran, n)),
        {("interrupt", False, 0), ("interrupt", True, 1), ("ok", True, 1)},
    ),
    (
        "set",
        lambda r, _: r.set(1),
        {("interrupt", False, 0), ("interrupt", False, 1), ("ok", False, 1)},
    ),
]


@pytest.mark.parametrize(
    ("op", "allowed"), [(op, allowed) for _, op, allowed in SWEPT], ids=[n for n, _, _ in SWEPT]
)
async def test_an_interrupt_at_any_operation_leaves_the_old_or_the_new_value(
    op: Swept, allowed: set[Outcome]
):
    """The write is the continuation of `f`'s success inside withPermit (pure updates: Ref.ts
    computes and assigns in one sync step); interrupting the updating fiber at each scheduler
    operation (Semaphore.test.ts:382's technique), and once past the last, leaves the old or the
    new value and always returns the permit. Measured consequences the oracles elsewhere rely
    on: an effectful update interrupted after its function produced a value may or may not have
    written it, and a pure update or a set may exit interrupted after its write."""

    @gen
    def program():
        operations = [0]
        counted = CountingScheduler(lambda _: operations.__setitem__(0, operations[0] + 1))
        baseline = SynchronizedRef[int].make_unsafe(0)
        fiber = yield from fork(on(counted, op(baseline, [False])))
        yield from fiber.await_()
        assert operations[0] > 0 and baseline.get_unsafe() == 1

        outcomes: set[Outcome] = set()
        for at in range(1, operations[0] + 2):
            sref = SynchronizedRef[int].make_unsafe(0)
            ran, seen = [False], [0]

            def interrupt_at(fiber: Any, at: int = at, seen: list[int] = seen) -> None:
                seen[0] += 1
                if seen[0] == at:
                    fiber.interrupt_unsafe()

            target = yield from fork(on(CountingScheduler(interrupt_at), op(sref, ran)))
            yield from target.await_()
            ex = target.exit
            exit_kind = (
                "ok" if isinstance(ex, Success) else "interrupt" if has_interrupts(ex) else repr(ex)
            )
            value = sref.get_unsafe()
            outcomes.add((exit_kind, ran[0], value))
            assert released(sref)
            assert (yield from sref.update_and_get(lambda n: n + 1)) == value + 1
        assert outcomes == allowed

    await bounded(program())


async def test_a_set_waits_for_an_in_flight_effectful_update():
    """`set` is `semaphore.withPermit(Ref.set(...))`: issued while an effectful update holds the
    permit, it applies after that update's write, so neither write is lost."""

    @gen
    def program():
        sref = yield from SynchronizedRef[int].make(0)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(
            sref.update_effect(lambda n: gate.await_().map(lambda _: n + 1)),
            start_immediately=True,
        )
        setter = yield from fork(sref.set(5), start_immediately=True)
        assert setter.exit is None
        assert sref.get_unsafe() == 0
        yield from gate.succeed(None)
        yield from settle([holder, setter])
        assert succeeded(holder.exit, None) and succeeded(setter.exit, None)
        assert sref.get_unsafe() == 5

    await bounded(program())


async def test_get_is_not_synchronized():
    """`get` is `Effect.sync(() => getUnsafe(self))`, outside the semaphore: during an
    effectful update it returns the old value at once."""

    @gen
    def program():
        sref = yield from SynchronizedRef[int].make(0)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(
            sref.update_effect(lambda n: gate.await_().map(lambda _: n + 1)),
            start_immediately=True,
        )
        reader = yield from fork(sref.get(), start_immediately=True)
        assert succeeded(reader.exit, 0)
        assert sref.semaphore.taken == 1
        yield from gate.succeed(None)
        yield from settle([holder])
        assert (yield from sref.get()) == 1

    await bounded(program())


async def test_an_effectful_update_reads_the_value_when_it_holds_the_permit():
    """Each *Effect body reads `getUnsafe(self)` inside `Effect.suspend` under the permit: an
    update queued behind another sees that one's write, and one effect value run twice applies
    twice."""

    @gen
    def program():
        sref = yield from SynchronizedRef[int].make(0)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(
            sref.update_effect(lambda _: gate.await_().map(lambda _: 10)),
            start_immediately=True,
        )
        inc = sref.update_and_get_effect(lambda n: succeed(n + 1))
        queued = yield from fork(inc, start_immediately=True)
        yield from gate.succeed(None)
        yield from settle([holder, queued])
        assert succeeded(queued.exit, 11)
        assert (yield from inc) == 12

    await bounded(program())


async def test_queued_updates_run_in_arrival_order():
    """Semaphore.ts:241-256 with one permit and requests of one: the first queued waiter is the
    only one that fits, and a newcomer queues behind it, so updates apply first in, first out."""

    @gen
    def program():
        sref = yield from SynchronizedRef[int].make(0)
        gate = yield from Deferred[None, Never].make()
        holder = yield from fork(
            sref.update_effect(lambda _: gate.await_().map(lambda _: 1)),
            start_immediately=True,
        )
        queued: list[Fiber[None, Never]] = [holder]
        for j in (1, 2, 3):
            fiber = yield from fork(sref.update(lambda s, j=j: s * 10 + j), start_immediately=True)
            queued.append(fiber)
        yield from gate.succeed(None)
        yield from settle(queued)
        assert sref.get_unsafe() == 1123

    await bounded(program())


def test_an_update_that_calls_its_own_ref_parks_until_interrupted():
    """Regression for a documented hazard: the semaphore is not reentrant, so a function that
    calls a synchronized operation on its own ref waits for the permit its caller holds. On a
    manual scheduler the world reaches quiescence (no armed work anywhere) with the update
    still pending, holding the permit and queued behind itself: parked, not slow. Interrupting
    it writes nothing and frees the permit; cleanup is bounded by drain's step limit."""
    m = Manual()
    sref = SynchronizedRef[int].make_unsafe(0)
    fiber = run_fork(
        sref.update_effect(lambda n: sref.set(n + 5).map(lambda _: n + 1)), scheduler=m.scheduler
    )
    assert m.drain()
    assert fiber.exit is None
    assert sref.semaphore.taken == 1 and len(sref.semaphore.waiters) == 1
    stopper = run_fork(fiber.interrupt(), scheduler=m.scheduler)
    assert m.drain()
    assert has_interrupts(fiber.exit) and isinstance(stopper.exit, Success)
    assert sref.get_unsafe() == 0
    assert released(sref)


async def test_partial_effectful_updates_return_their_source_defined_values():
    """Return values that differ across the *SomeEffect bodies: getAndUpdateSomeEffect returns
    the old value; updateSomeAndGetEffect `option.value` when Some, else the old value;
    modifySomeEffect `b` either way. Some(None) is present and is stored (`Option.isNone`
    tests the tag, not the value)."""

    def to(v: int | None) -> Callable[[int | None], Effect[Option[int | None], Never, Never]]:
        return lambda _: succeed(Some(v))

    def none(_: int | None) -> Effect[Option[int | None], Never, Never]:
        return succeed(None)

    @gen
    def program():
        sref = yield from SynchronizedRef[int | None].make(1)
        assert (yield from sref.get_and_update_some_effect(to(2))) == 1
        assert (yield from sref.get_and_update_some_effect(none)) == 2
        assert (yield from sref.update_some_and_get_effect(to(3))) == 3
        assert (yield from sref.update_some_and_get_effect(none)) == 3
        assert (yield from sref.modify_some_effect(lambda _: succeed(("b", Some(4))))) == "b"
        assert (yield from sref.modify_some_effect(lambda _: succeed(("c", None)))) == "c"
        assert sref.get_unsafe() == 4
        yield from sref.update_some_effect(none)
        assert sref.get_unsafe() == 4

        assert (yield from sref.get_and_update_some_effect(to(None))) == 4
        assert sref.get_unsafe() is None
        yield from sref.set(1)
        assert (yield from sref.update_some_and_get_effect(to(None))) is None
        yield from sref.set(1)
        yield from sref.update_some_effect(to(None))
        assert sref.get_unsafe() is None
        yield from sref.set(1)
        assert (yield from sref.modify_some_effect(lambda _: succeed(("b", Some(None))))) == "b"
        assert sref.get_unsafe() is None

    await run(program())


# -- helpers (test_semaphore_upstream.py) -------------------------------------------------------


def on[A, E, R](scheduler: Any, eff: Effect[A, E, R]) -> Effect[A, E, R]:
    return provide(Scheduler.context(scheduler))(eff)


class Manual:
    """test_managed_runtime_interleavings.py: a scheduler whose armed batches run only when
    stepped, so "nothing armed" is exact quiescence in this closed world."""

    def __init__(self) -> None:
        self.pending: list[Task] = []
        self.scheduler = MixedScheduler("async", self._arm)

    def _arm(self, batch: Task) -> Task:
        self.pending.append(batch)
        return lambda: self.pending.remove(batch) if batch in self.pending else None

    def drain(self, limit: int = 20_000) -> bool:
        """Run batches until none is armed; True when the world is quiescent."""
        for _ in range(limit):
            if not self.pending:
                return True
            self.pending.pop(0)()
        return not self.pending


class CountingScheduler:
    """Semaphore.test.ts:385-393: a MixedScheduler that observes each should_yield."""

    execution_mode: Literal["async"] = "async"

    def __init__(self, on_operation: Callable[[Any], None]) -> None:
        self.base = MixedScheduler()
        self.on_operation = on_operation

    def should_yield(self, fiber: Any) -> bool:
        self.on_operation(fiber)
        return self.base.should_yield(fiber)

    def make_dispatcher(self) -> SchedulerDispatcher:
        return self.base.make_dispatcher()
