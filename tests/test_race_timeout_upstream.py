"""race, race_first, race_all, race_all_first, timeout, timeout_option, timeout_or_else. Upstream is
pinned at vendor/effect-v4 commit 289a53f7, packages/effect. Semantics: internal/effect.ts
raceAll :1688, raceAllFirst :1743, interruptRaceLosers :1786, race :1794, raceFirst :1834,
timeoutOrElse :3929, timeout :3963, timeoutOption :3988, TimeoutError :6499, forkUnsafe :5614,
OnExitImpl :4270. Ported from test/Effect.test.ts (each test names its line):
  :1430 raceAll   :1449 raceAll elects   :1455 raceAllFirst   :1475 :1494 :1513 loser interrupts
  :1534 :1552 :1572 race/raceFirst while starting and pending loser
  :2054 :2105 :2119 :2158 :2209 timeoutOrElse   :2244 :2253 :2268 :2281 timeoutOption
  :2289 :2304 :2317 timeout
Source-derived tests are named `test_source_*`; each expected value was measured against the
pinned vendor under bun (scratch probes race-probe/p1.ts and p2.ts, kept with this lane's notes).
The property oracle `race_model` reproduced all 966 vendor runs of p2.ts (n <= 3 racers, every
sync/gated and ok/fail/die mix, every gate order) with no mismatch.

What upstream does, and these tests pin:
- Racers are forked at once (start immediately), as daemons (no parent), always interruptible
  whatever the caller's mask, with the caller's context. A racer that wins while the racers are
  being forked stops the rest from being forked.
- race/race_all: the first success wins; failures are collected in completion order and, when
  every racer failed, returned together with defects elected. race_first/race_all_first: the
  first completion wins, success or failure.
- Losers are interrupted, by the racing fiber's id, as soon as the race settles, before the
  racing fiber resumes. The race then interrupts and awaits every live loser in an OnExit frame,
  masked: the race returns only after loser cleanup, and an interrupt that arrives during that
  wait lands after it.
- timeout_option is race_first(source as Some, sleep then None); timeout_or_else calls `or_else`
  lazily on the caller's fiber after the source has stopped; timeout fails with TimeoutError
  "Operation timed out after '<Duration.format>'" and decodes its Duration when called.

Adaptations used throughout:
- Data-first only: `race(a, b)`, `timeout(eff, d)`, `timeout_or_else(eff, duration=, or_else=)`;
  upstream's pipeable (data-last) forms are not ported.
- The original ports use a provided `GateClock` for explicit sleep gates: a real
  ClockService whose `sleep` records the Duration it was given and parks on a Deferred the test
  opens; as TestClock.sleep (TestClock.ts:329-332), a sleep of at most 0 ms ends at once.
  `TestClock.adjust(t)` is `clock.fire_until(t)`: open, shortest first, each recorded
  sleep of at most t ms (every sleep here starts at time 0). Because opening a gate runs the
  sleeper at once, the race under test is forked with `start_immediately=True` so its sleeps
  exist before the gates open, where TestClock's adjust yields to let a scheduled fork start.
- `Effect.delay(ms)` is `sleep(ms)` then the effect; `forkChild` is `fork`; `Fiber.await` is
  `await_()`; upstream string defects are built as `fail_cause(Cause((Die(...),)))`.
- Upstream's `toString()` of a TimeoutError is "TimeoutError: <message>"; here the class name
  and `str(error)` are checked. Span and log-annotation observations in :2158 are omitted
  (outside these race/timeout assertions).
- `asyncio.wait_for` is a hang guard only; gates and fibers set every order.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Literal, Never

import pytest
from hypothesis import given
from hypothesis import strategies as st

import peffect.duration as D
from peffect import (
    Cause,
    Clock,
    Deferred,
    Die,
    Effect,
    Exit,
    Fail,
    Failure,
    Fiber,
    Interrupt,
    MixedScheduler,
    Option,
    Queue,
    Reference,
    Scheduler,
    SchedulerDispatcher,
    Some,
    Success,
    Tag,
    TimeoutError,
    die,
    ensuring,
    exit_,
    fail,
    fail_cause,
    fiber_id,
    fork,
    gen,
    never,
    on_exit,
    on_interrupt,
    provide,
    provide_layer,
    race,
    race_all,
    race_all_first,
    race_first,
    run,
    run_fork,
    scoped,
    sleep,
    succeed,
    suspend,
    sync,
    tap,
    timeout,
    timeout_option,
    timeout_or_else,
    uninterruptible,
    yield_now,
)
from peffect.core import WithFiber
from peffect.duration import Duration
from peffect.fiber import Task
from peffect.testing import TestClock

WATCHDOG_SECONDS = 2

type Combinator = Callable[[Effect[Any, Any, Any], Effect[Any, Any, Any]], Effect[Any, Any, Any]]
BOTH: list[tuple[str, Combinator]] = [("race", race), ("race_first", race_first)]

# -- helpers ---------------------------------------------------------------------------------------


def reasons(ex: Exit[Any, Any] | None) -> list[tuple[str, object]]:
    """An Exit as comparable data: ("ok", value) or one entry per Cause reason."""
    assert ex is not None, "fiber still running"
    if isinstance(ex, Success):
        return [("ok", ex.value)]
    out: list[tuple[str, object]] = []
    for r in ex.cause.reasons:
        if isinstance(r, Fail):
            out.append(("fail", r.error))
        elif isinstance(r, Die):
            out.append(("die", str(r.defect)))
        else:
            out.append(("interrupt", r.by))
    return out


def has_interrupts(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


async def guarded[A](eff: Effect[A, Never, Never]) -> A:
    return await asyncio.wait_for(run(eff), WATCHDOG_SECONDS)


class GateClock:
    """A provided Clock whose sleeps end only when the test opens them (see the module notes)."""

    def __init__(self) -> None:
        self.asked: list[Duration] = []  # every sleep asked of this clock, in order
        self.sleeps: list[tuple[Duration, Deferred[None]]] = []  # the ones that parked
        self.cancelled: list[Duration] = []

    def current_time_millis_unsafe(self) -> int:
        return 0

    def current_time_nanos_unsafe(self) -> int:
        return 0

    def monotonic_time_nanos_unsafe(self) -> int:
        return 0

    @property
    def current_time_millis(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    def sleep(self, duration: Duration) -> Effect[None, Never, Never]:
        def start() -> Effect[None, Never, Never]:
            self.asked.append(duration)
            if D.to_millis(duration) <= 0:
                return succeed(None)  # TestClock.sleep: a deadline not after now is void
            gate = Deferred[None].make_unsafe()
            self.sleeps.append((duration, gate))
            cancel = on_interrupt(lambda _ids: sync(lambda: self.cancelled.append(duration)))
            return gate.await_().pipe(cancel)

        return suspend(start)

    def fire_until(self, millis: float) -> Effect[None, Never, Never]:
        """TestClock.adjust adaptation: open the shortest unopened sleep of at most ``millis``,
        let it run, and repeat until none is left."""

        def step() -> Effect[None, Never, Never]:
            due = [(D.to_millis(d), i) for i, (d, g) in enumerate(self.sleeps) if g.effect is None]
            due = sorted(x for x in due if x[0] <= millis)
            if not due:
                return succeed(None)
            return self.sleeps[due[0][1]][1].succeed(None).flat_map(lambda _: step())

        return suspend(step)

    def provide[A, E](self, eff: Effect[A, E, Any]) -> Effect[A, E, Never]:
        return provide(Clock.context(self))(eff)


class Manual:
    """A MixedScheduler whose armed batches run only when stepped (precedent:
    tests/test_managed_runtime_interleavings.py)."""

    def __init__(self) -> None:
        self.pending: list[Task] = []
        self.scheduler = MixedScheduler("async", self._arm)

    def _arm(self, batch: Task) -> Task:
        self.pending.append(batch)
        return lambda: self.pending.remove(batch) if batch in self.pending else None

    def drain(self, limit: int = 20_000) -> bool:
        for _ in range(limit):
            if not self.pending:
                return True
            self.pending.pop(0)()
        return not self.pending


def delayed_racer[A, E](
    ms: int, body: Effect[A, E, Never], interrupted: list[int]
) -> Effect[A, E, Never]:
    record = on_interrupt(lambda _ids: sync(lambda: interrupted.append(ms)))
    return sleep(ms).flat_map(lambda _: body).pipe(record)


# == race: upstream ports ==========================================================================


async def test_race_all():
    """Effect.test.ts:1430 "raceAll" (TestClock adaptation: GateClock)."""
    clock = GateClock()
    interrupted: list[int] = []
    racers = [
        delayed_racer(ms, fail("boom") if ms == 0 else succeed(ms), interrupted)
        for ms in [500, 300, 200, 0, 100]
    ]

    @gen
    def program():
        f: Fiber[int, str] = yield from fork(race_all(racers), start_immediately=True)
        yield from clock.fire_until(500)
        return (yield from f.await_())

    assert reasons(await guarded(clock.provide(program()))) == [("ok", 100)]
    assert interrupted == [500, 300, 200]


async def test_race_all_elects_typed_failures_when_all_racers_fail_and_one_dies():
    """Effect.test.ts:1449."""
    exit = await guarded(exit_(race_all([fail("error"), fail_cause(Cause[str]((Die("defect"),)))])))
    assert isinstance(exit, Failure)  # Failure compares by identity; its Cause by value
    assert exit.cause == Cause[str]((Die("error"), Die("defect")))


async def test_race_all_first():
    """Effect.test.ts:1455 "raceAllFirst": 0 fails first and wins; 100 never starts (its sleep
    is never asked of the clock)."""
    clock = GateClock()
    interrupted: list[int] = []
    racers = [
        delayed_racer(ms, fail("boom") if ms == 0 else succeed(ms), interrupted)
        for ms in [500, 300, 200, 0, 100]
    ]

    @gen
    def program():
        f: Fiber[int, str] = yield from fork(race_all_first(racers), start_immediately=True)
        yield from clock.fire_until(500)
        return (yield from f.await_())

    assert reasons(await guarded(clock.provide(program()))) == [("fail", "boom")]
    assert interrupted == [500, 300, 200]
    assert [D.to_millis(d) for d in clock.asked] == [500, 300, 200, 0]


@pytest.mark.parametrize(
    ("name", "fast", "expected"),
    [
        ("race", succeed("fast"), [("ok", "fast")]),  # :1475
        ("race_first", succeed("fast"), [("ok", "fast")]),  # :1494
        ("race_first", fail("boom"), [("fail", "boom")]),  # :1513
    ],
)
async def test_race_interrupts_the_loser_when_the_other_side_settles(
    name: str, fast: Effect[str, str, Never], expected: list[tuple[str, object]]
):
    """Effect.test.ts:1475, :1494, :1513 (TestClock adaptation: GateClock)."""
    combinator = dict(BOTH)[name]
    clock = GateClock()
    interrupted: list[int] = []

    @gen
    def program():
        f: Fiber[str, str] = yield from fork(
            combinator(
                delayed_racer(100, fast, interrupted),
                delayed_racer(500, succeed("slow"), interrupted),
            ),
            start_immediately=True,
        )
        yield from clock.fire_until(500)
        return (yield from f.await_())

    assert reasons(await guarded(clock.provide(program()))) == expected
    assert interrupted == [500]


@pytest.mark.parametrize(("name", "combinator"), BOTH)
async def test_race_interrupts_a_loser_that_settles_the_race_while_starting(
    name: str, combinator: Combinator
):
    """Effect.test.ts:1534."""
    interrupted: list[bool] = []

    @gen
    def program():
        deferred: Deferred[None] = yield from Deferred[None].make()
        loser = (
            deferred.succeed(None)
            .flat_map(lambda _: never)
            .pipe(on_interrupt(lambda _ids: sync(lambda: interrupted.append(True))))
        )
        yield from combinator(deferred.await_(), loser)

    await guarded(program())
    assert interrupted == [True], name


@pytest.mark.parametrize(("name", "combinator"), BOTH)
async def test_race_interrupts_the_loser_when_interrupted_while_starting(
    name: str, combinator: Combinator
):
    """Effect.test.ts:1552."""
    interrupted: list[bool] = []

    def body(parent: Fiber[Any, Any]) -> Effect[Any, Any, Any]:
        first = (
            sync(parent.interrupt_unsafe)
            .flat_map(lambda _: never)
            .pipe(on_interrupt(lambda _ids: sync(lambda: interrupted.append(True))))
        )
        return combinator(first, succeed(None))

    @gen
    def program():
        f: Fiber[Any, Any] = yield from fork(WithFiber(body))
        return (yield from f.await_())

    ex = await guarded(program())
    assert [r for r in reasons(ex) if r[0] == "die"] == [], name
    assert interrupted == [True], name


class YieldParentOnce:
    """Effect.test.ts:1576-1590's scheduler: tasks wait in a list the test drains; the racing
    fiber is told to yield once, when the test asks."""

    def __init__(self) -> None:
        self.tasks: list[Task] = []
        self.parent: Fiber[Any, Any] | None = None
        self.yield_parent = False

    @property
    def execution_mode(self) -> Literal["sync", "async"]:
        return "async"

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        if not self.yield_parent or fiber is not self.parent:
            return False
        self.yield_parent = False
        return True

    def make_dispatcher(self) -> SchedulerDispatcher:
        return _ListDispatcher(self.tasks)


class _ListDispatcher:
    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks

    def schedule_task(self, task: Task, priority: int) -> None:
        self.tasks.append(task)

    def flush(self) -> None:
        pass


@pytest.mark.parametrize(("name", "combinator"), BOTH)
async def test_race_interrupts_a_pending_loser_before_the_parent_resumes(
    name: str, combinator: Combinator
):
    """Effect.test.ts:1572, retained scheduler-only regression. Upstream's Queue offer gives the
    loser a scheduled wake; here the loser opens on a gate and then waits on a queued step
    (`yield_now`), so it holds a pending task when the race settles. Upstream checks the
    interruptor stack frame; here the interruptor id is the racing fiber's."""
    s = YieldParentOnce()
    winner = Deferred[str].make_unsafe()
    wake = Deferred[None].make_unsafe()
    consumed: list[str] = []
    loser_exit: list[Exit[Any, Any]] = []
    loser = (
        wake.await_()
        .flat_map(lambda _: yield_now)
        .flat_map(lambda _: sync(lambda: consumed.append("loser")))
        .pipe(on_exit(lambda ex: sync(lambda: loser_exit.append(ex))))
    )

    def body(parent: Fiber[Any, Any]) -> Effect[Any, Any, Any]:
        s.parent = parent
        return combinator(loser, winner.await_())

    @gen
    def program():
        f: Fiber[Any, Any] = yield from provide(Scheduler.context(s))(
            fork(WithFiber(body), start_immediately=True)
        )
        yield from wake.succeed(None)
        s.yield_parent = True
        winner.done_unsafe(succeed("winner"))
        while s.tasks:
            s.tasks.pop(0)()
        return f, (yield from f.join())

    f, result = await guarded(program())
    assert result == "winner", name
    assert consumed == []
    assert [reasons(ex) for ex in loser_exit] == [[("interrupt", f.id)]]


# == race: source-derived ==========================================================================


type Spec = list[tuple[Literal["sync", "gated"], Literal["ok", "fail", "die"]]]


def race_model(mode: str, spec: Spec, order: list[int]) -> dict[str, Any]:
    """The race as upstream's source describes it (raceAll :1688, raceAllFirst :1743), written
    independently of any implementation. Racers start in index order and a sync racer completes
    as it starts; forking stops once the race is settled. Gates then open in ``order``. A
    settled race interrupts every live loser at once, in fork order."""
    n = len(spec)
    started: list[int] = []
    finalized: list[int] = []
    interrupted: list[int] = []
    failures: list[tuple[str, object]] = []
    live: list[int] = []
    result: list[tuple[str, object]] | None = None
    completions = 0

    def complete(i: int) -> None:
        nonlocal result, completions
        if i in live:
            live.remove(i)
        finalized.append(i)
        completions += 1
        o = spec[i][1]
        r = ("ok", f"v{i}") if o == "ok" else ("fail", f"e{i}") if o == "fail" else ("die", f"d{i}")
        if result is not None:
            return
        if mode == "race_all" and o != "ok":
            failures.append(r)
            if completions >= n:
                kinds = {k for k, _ in failures}
                elect = "die" in kinds and "fail" in kinds
                result = [("die", v) for _, v in failures] if elect else list(failures)
            return
        result = [r]
        for j in list(live):
            live.remove(j)
            finalized.append(j)
            interrupted.append(j)
            completions += 1

    for i, (kind, _) in enumerate(spec):
        started.append(i)
        if kind == "sync":
            complete(i)
        else:
            live.append(i)
        if result is not None:
            break
    for i in order:
        if i in live:
            complete(i)
    return {"out": result, "started": started, "finalized": finalized, "interrupted": interrupted}


@st.composite
def race_cases(draw: st.DrawFn) -> tuple[str, Spec, list[int]]:
    mode = draw(st.sampled_from(["race_all", "race_all_first"]))
    spec: Spec = draw(
        st.lists(
            st.tuples(st.sampled_from(["sync", "gated"]), st.sampled_from(["ok", "fail", "die"])),
            min_size=1,
            max_size=5,
        )
    )
    order = draw(st.permutations([i for i, (k, _) in enumerate(spec) if k == "gated"]))
    return mode, spec, order


@given(race_cases())
def test_source_race_matches_the_model_for_any_mix_and_completion_order(
    case: tuple[str, Spec, list[int]],
):
    """Source-derived property: result, which racers start, which are interrupted (by the racing
    fiber), and that every started racer has finished before the race returns. Runs on a manual
    scheduler, so the whole run is synchronous and replayable."""
    mode, spec, order = case
    started: list[int] = []
    finalized: list[int] = []
    interrupted: list[int] = []
    interruptors: set[int | None] = set()
    at_return: list[list[int]] = []

    def record(i: int, ex: Exit[Any, Any]) -> None:
        finalized.append(i)
        if isinstance(ex, Failure):
            by = {r.by for r in ex.cause.reasons if isinstance(r, Interrupt)}
            if by:
                interrupted.append(i)
                interruptors.update(by)

    @gen
    def program():
        gates: list[Deferred[None]] = [Deferred.make_unsafe() for _ in spec]
        racers: list[Effect[str, str, Never]] = []
        for i, (kind, outcome) in enumerate(spec):
            body: Effect[str, str, Never] = (
                succeed(f"v{i}")
                if outcome == "ok"
                else fail(f"e{i}")
                if outcome == "fail"
                else die(Exception(f"d{i}"))
            )
            if kind == "gated":
                body = gates[i].await_().flat_map(lambda _, b=body: b)
            racers.append(
                sync(lambda i=i: started.append(i))
                .flat_map(lambda _, b=body: b)
                .pipe(on_exit(lambda ex, i=i: sync(lambda: record(i, ex))))
            )
        combinator = race_all if mode == "race_all" else race_all_first
        snapshot = on_exit(lambda _ex: sync(lambda: at_return.append(list(finalized))))
        f: Fiber[str, str] = yield from fork(
            combinator(racers).pipe(snapshot), start_immediately=True
        )
        for i in order:
            yield from gates[i].succeed(None)
        return f, (yield from f.await_())

    m = Manual()
    root = run_fork(program(), scheduler=m.scheduler)
    assert m.drain(), "not quiescent"
    assert isinstance(root.exit, Success)
    f, ex = root.exit.value
    expected = race_model(mode, spec, order)
    assert {
        "out": reasons(ex),
        "started": started,
        "finalized": finalized,
        "interrupted": interrupted,
    } == {**expected, "out": list(expected["out"])}
    assert at_return == [expected["finalized"]]
    assert interruptors <= {f.id}


async def test_source_the_race_returns_after_loser_cleanup_and_masks_that_wait():
    """Source-derived: the race's OnExit frame awaits live losers masked (:1704, :4270-4300).
    Measured upstream (p1.ts C/D): the loser is interrupted by the racing fiber; the race is still
    running after the winner and after an external interrupt; the external interrupt lands once
    the cleanup ends, so the winner is discarded and the exit is that interrupt alone."""
    log: list[str] = []
    cleanup_gate = Deferred[None].make_unsafe()
    win = Deferred[None].make_unsafe()

    def loser_exit(ex: Exit[Any, Any]) -> Effect[None, Never, Never]:
        return (
            sync(lambda: log.append(f"loser exit {reasons(ex)}"))
            .flat_map(lambda _: cleanup_gate.await_())
            .flat_map(lambda _: sync(lambda: log.append("loser cleanup done")))
        )

    @gen
    def program():
        me: int = yield from fiber_id
        f: Fiber[str, Never] = yield from fork(
            race(never.pipe(on_exit(loser_exit)), win.await_().as_("winner")).pipe(
                tap(lambda _: sync(lambda: log.append("race returned")))
            ),
            start_immediately=True,
        )
        yield from win.succeed(None)
        log.append(f"after win done={f.exit is not None}")
        f.interrupt_unsafe(me)
        log.append(f"after interrupt done={f.exit is not None}")
        yield from cleanup_gate.succeed(None)
        return me, f.id, (yield from f.await_())

    me, racing, ex = await guarded(program())
    assert log == [
        f"loser exit {[('interrupt', racing)]}",
        "after win done=False",
        "after interrupt done=False",
        "loser cleanup done",
    ]
    assert reasons(ex) == [("interrupt", me)]


async def test_source_a_masked_race_still_interrupts_its_loser_and_finishes_first():
    """Source-derived: racers are forked interruptible whatever the caller's mask (forkUnsafe
    :5614, uninterruptible=false). Measured upstream (p1.ts E): an external interrupt of the masked
    racing fiber touches no racer; the loser is interrupted when the winner settles; the race
    returns the winner inside the mask; the exit is the external interrupt."""
    log: list[str] = []
    win = Deferred[None].make_unsafe()
    loser = never.pipe(on_interrupt(lambda _ids: sync(lambda: log.append("loser interrupted"))))

    @gen
    def program():
        me: int = yield from fiber_id
        f: Fiber[str, Never] = yield from fork(
            uninterruptible(
                race(loser, win.await_().as_("winner")).pipe(
                    tap(lambda v: sync(lambda: log.append(f"race returned {v}")))
                )
            ),
            start_immediately=True,
        )
        f.interrupt_unsafe(me)
        log.append(f"interrupted; loser interrupted={'loser interrupted' in log}")
        yield from win.succeed(None)
        return me, (yield from f.await_())

    me, ex = await guarded(program())
    assert log == [
        "interrupted; loser interrupted=False",
        "loser interrupted",
        "race returned winner",
    ]
    assert reasons(ex) == [("interrupt", me)]


async def test_source_interrupting_the_racing_fiber_interrupts_every_racer():
    """Source-derived (p1.ts G): both racers end interrupted by the racing fiber, in fork order,
    before the racing fiber's exit, which is the external interrupt."""
    log: list[tuple[str, object]] = []

    def racer(name: str) -> Effect[Never, Never, Never]:
        return never.pipe(on_exit(lambda ex: sync(lambda: log.append((name, reasons(ex))))))

    @gen
    def program():
        me: int = yield from fiber_id
        f: Fiber[Never, Never] = yield from fork(
            race(racer("a"), racer("b")), start_immediately=True
        )
        f.interrupt_unsafe(me)
        return me, f.id, (yield from f.await_())

    me, racing, ex = await guarded(program())
    assert log == [("a", [("interrupt", racing)]), ("b", [("interrupt", racing)])]
    assert reasons(ex) == [("interrupt", me)]


class Region(Tag[str], key="race-test/Region"): ...


async def test_source_racers_see_the_callers_context_and_run_as_daemons():
    """Source-derived: forkUnsafe(parent, effect, true, true) (:1734) copies the parent's context
    and adds no parent link; a racer's own `provide` does not leak to the caller."""

    @gen
    def racer():
        region: str = yield from Region()
        parent: Fiber[Any, Any] | None = yield from WithFiber(lambda f: succeed(f.parent))
        inner: str = yield from provide(Region.context("inner"))(Region().use())
        return region, parent is None, inner

    @gen
    def program():
        got = yield from race(racer(), never)
        after: str = yield from Region()
        return got, after

    result = await guarded(provide(Region.context("outer"))(program()))
    assert result == (("outer", True, "inner"), "outer")


@pytest.mark.parametrize(
    ("name", "combinator", "first", "pulled", "expected"),
    [
        ("race_all", race_all, succeed(1), [0, 1, 2], [("ok", 1)]),
        ("race_all_first", race_all_first, succeed(1), [0, 1], [("ok", 1)]),
        ("race_all_first", race_all_first, fail("e"), [0, 1], [("fail", "e")]),
    ],
)
async def test_source_how_far_each_race_reads_a_lazy_iterable(
    name: str,
    combinator: Callable[[Any], Effect[int, str, Never]],
    first: Effect[int, str, Never],
    pulled: list[int],
    expected: list[tuple[str, object]],
) -> None:
    """Source-derived: raceAll reads every racer first (Arr.fromIterable, :1707); raceAllFirst
    reads lazily and checks `done` before each fork (:1763-1764), so when the first racer settles
    synchronously the generator is resumed once more but nothing after it is forked. Measured
    under bun (scratch probe race-probe/p3.ts): pulled [0, 1, 2] / [0, 1] / [0, 1], ran []."""
    log: list[int] = []
    ran: list[int] = []

    def racers():
        log.append(0)
        yield first
        log.append(1)
        yield sync(lambda: ran.append(1) or 2)
        log.append(2)
        yield succeed(3)

    ex = await guarded(exit_(combinator(racers())))
    assert (log, ran, reasons(ex)) == (pulled, [], expected), name


# == timeout_or_else: upstream ports ===============================================================


async def test_timeout_or_else_factory_and_body_follow_cleanup_and_replay():
    """Effect.test.ts:2054 (data-first form; the pipe form is not proposed). Live clock."""
    stopped = False
    factories = 0
    bodies = 0
    order: list[str] = []
    value = {"fallback": True}

    def stop() -> None:
        nonlocal stopped
        stopped = True
        order.append("stopped")

    def or_else() -> Effect[dict[str, bool], Never, Never]:
        nonlocal factories
        factories += 1
        order.append(f"factory:{stopped}")

        def body() -> dict[str, bool]:
            nonlocal bodies
            bodies += 1
            order.append(f"body:{stopped}")
            return value

        return sync(body)

    source: Effect[Never, Never, Never] = never.pipe(ensuring(sync(stop)))
    program = timeout_or_else(source, duration=0, or_else=or_else)
    before = [factories, bodies]
    first = await guarded(program)
    first_stopped = stopped
    stopped = False
    second = await guarded(program)
    assert {
        "before": before,
        "factories": factories,
        "bodies": bodies,
        "first_stopped": first_stopped,
        "stopped": stopped,
        "identity": [first is value, second is value],
        "order": order,
    } == {
        "before": [0, 0],
        "factories": 2,
        "bodies": 2,
        "first_stopped": True,
        "stopped": True,
        "identity": [True, True],
        "order": ["stopped", "factory:True", "body:True", "stopped", "factory:True", "body:True"],
    }


async def test_timeout_or_else_a_ready_source_value_is_not_confused_with_a_zero_duration():
    """Effect.test.ts:2105, plus (source-derived, p1.ts H) the clock is never asked to sleep:
    the source wins while the racers are forked, so the sleep racer is never forked."""
    clock = GateClock()
    calls = 0

    def or_else() -> Effect[Option[str], Never, Never]:
        nonlocal calls
        calls += 1
        return succeed(Some("unexpected fallback"))

    value: Option[str] = None
    result = await guarded(
        clock.provide(timeout_or_else(succeed(value), duration=0, or_else=or_else))
    )
    assert (result, calls, clock.asked) == (value, 0, [])


async def test_timeout_or_else_slow_fallback_cannot_let_the_original_source_win_after_timeout():
    """Effect.test.ts:2119. Live clock."""
    source_gate = Deferred[None].make_unsafe()
    source_stopped = Deferred[None].make_unsafe()
    fallback_started = Deferred[None].make_unsafe()
    fallback_release = Deferred[None].make_unsafe()
    source_completed: list[bool] = []
    fallback_interrupted: list[bool] = []
    value = {"fallback": True}
    source = (
        source_gate.await_()
        .flat_map(lambda _: sync(lambda: source_completed.append(True)))
        .as_({"source": True})
        .pipe(ensuring(source_stopped.succeed(None)))
    )

    def or_else() -> Effect[dict[str, bool], Never, Never]:
        return (
            fallback_started.succeed(None)
            .flat_map(lambda _: fallback_release.await_())
            .as_(value)
            .pipe(on_interrupt(lambda _ids: sync(lambda: fallback_interrupted.append(True))))
        )

    @gen
    def program():
        f: Fiber[dict[str, bool], Never] = yield from fork(
            timeout_or_else(source, duration=0, or_else=or_else)
        )
        yield from fallback_started.await_()
        yield from source_gate.succeed(None)
        yield from source_stopped.await_()
        yield from fallback_release.succeed(None)
        return (yield from f.join())

    result = await guarded(program())
    assert [result is value, source_completed, fallback_interrupted] == [True, [], []]


class SourceService(Tag[dict[str, str]], key="timeout-test/SourceService"): ...


class FallbackService(Tag[dict[str, str]], key="timeout-test/FallbackService"): ...


class Local(Reference[str], key="timeout-test/Local"):
    @classmethod
    def default(cls) -> str:
        return "default"


async def test_timeout_or_else_source_and_fallback_inherit_caller_context():
    """Effect.test.ts:2158 without its span and log-annotation observations (not built)."""
    source_service = {"source": "source service"}
    fallback_service = {"fallback": "fallback service"}
    observed: list[object] = []

    @gen
    def source():
        observed.extend([(yield from SourceService()) is source_service, (yield from Local())])
        return (yield from never)

    @gen
    def fallback(caller: int):
        observed.extend([(yield from fiber_id) == caller, (yield from Local())])
        return (yield from FallbackService())

    @gen
    def program():
        caller: int = yield from fiber_id
        value = yield from timeout_or_else(
            provide(Local.context("source-local"))(source()),
            duration=0,
            or_else=lambda: fallback(caller),
        )
        observed.append((yield from Local()))
        return value

    ctx = (
        SourceService.context(source_service)
        | FallbackService.context(fallback_service)
        | Local.context("parent-local")
    )
    result = await guarded(provide(ctx)(program()))
    assert [result is fallback_service, observed] == [
        True,
        [True, "source-local", True, "parent-local", "parent-local"],
    ]


async def test_timeout_or_else_external_interruption_stops_a_pending_fallback():
    """Effect.test.ts:2209. Live clock."""
    started = Deferred[None].make_unsafe()
    flags = {"source_stopped": False, "fallback_stopped": False}
    calls = 0

    def mark(key: str) -> Effect[None, Never, Never]:
        return sync(lambda: flags.__setitem__(key, True))

    def or_else() -> Effect[Never, Never, Never]:
        nonlocal calls
        calls += 1
        return (
            started.succeed(None).flat_map(lambda _: never).pipe(ensuring(mark("fallback_stopped")))
        )

    @gen
    def program():
        f: Fiber[Never, Never] = yield from fork(
            timeout_or_else(
                never.pipe(ensuring(mark("source_stopped"))), duration=0, or_else=or_else
            )
        )
        yield from started.await_()
        yield from f.interrupt()
        return (yield from f.await_())

    ex = await guarded(program())
    assert [has_interrupts(ex), flags["source_stopped"], flags["fallback_stopped"], calls] == [
        True,
        True,
        True,
        1,
    ]


# == timeout_option and timeout: upstream ports ====================================================


async def test_timeout_option_a_long_computation():
    """Effect.test.ts:2244. Live clock: 10ms real."""
    result = await guarded(timeout_option(sleep(60_000).as_(True), 10))
    assert result is None


async def test_timeout_option_a_long_computation_with_a_failure():
    """Effect.test.ts:2253 (timeoutOrElse with a dying fallback). Live clock."""
    error = Exception("boom")
    ex = await guarded(
        exit_(timeout_or_else(sleep(5000).as_(True), duration=10, or_else=lambda: die(error)))
    )
    assert isinstance(ex, Failure) and ex.cause == Cause.die(error)


async def test_timeout_option_repetition_of_an_uninterruptible_effect():
    """Effect.test.ts:2268 (TestClock adaptation: GateClock). The source never suspends; it is
    stopped between masked steps once the sleep wins."""
    clock = GateClock()

    @gen
    def forever():
        while True:
            yield from uninterruptible(succeed(None))

    @gen
    def program():
        f: Fiber[Option[Never], Never] = yield from fork(
            timeout_option(forever(), 10), start_immediately=True
        )
        yield from clock.fire_until(10)
        return (yield from f.join())

    assert await guarded(clock.provide(program())) is None


async def test_timeout_option_in_an_uninterruptible_region():
    """Effect.test.ts:2281, plus (source-derived) a source that suspends, so the sleep racer is
    forked and must be stopped although the caller is masked."""
    assert await guarded(uninterruptible(timeout_option(succeed(None), 20_000))) == Some(None)
    clock = GateClock()
    result = await guarded(clock.provide(uninterruptible(timeout_option(yield_now.as_(1), 20_000))))
    assert (result, clock.cancelled) == (Some(1), [D.seconds(20)])


def timeout_error(ex: Exit[Any, Any]) -> Exception:
    assert isinstance(ex, Failure), ex
    errors = ex.cause.failures
    assert len(errors) == 1 and len(ex.cause.reasons) == 1, ex
    error = errors[0]
    assert isinstance(error, TimeoutError), error
    assert type(error).__name__ == "TimeoutError"
    return error


async def test_timeout_produces_a_useful_error_message():
    """Effect.test.ts:2289 (TestClock adaptation: GateClock)."""
    clock = GateClock()

    @gen
    def program():
        f: Fiber[Never, Exception] = yield from fork(
            timeout(never, D.millis(1500)), start_immediately=True
        )
        yield from clock.fire_until(2000)
        return (yield from f.await_())

    error = timeout_error(await guarded(clock.provide(program())))
    assert str(error) == "Operation timed out after '1s 500ms'"


async def test_timeout_a_long_computation():
    """Effect.test.ts:2304. Live clock: 10ms real."""
    error = timeout_error(await guarded(exit_(timeout(sleep(60_000).as_(True), 10))))
    assert str(error) == "Operation timed out after '10ms'"


async def test_timeout_interrupts_the_effect():
    """Effect.test.ts:2317 (TestClock adaptation: GateClock)."""
    clock = GateClock()
    interrupted: list[bool] = []
    source = never.pipe(on_interrupt(lambda _ids: sync(lambda: interrupted.append(True))))

    @gen
    def program():
        f: Fiber[Never, Exception] = yield from fork(timeout(source, 10), start_immediately=True)
        yield from clock.fire_until(10)
        return (yield from f.await_())

    error = timeout_error(await guarded(clock.provide(program())))
    assert str(error) == "Operation timed out after '10ms'"
    assert interrupted == [True]


# == timeouts: source-derived ======================================================================


async def test_source_timeouts_sleep_on_the_fibers_clock_with_the_decoded_duration():
    """Source-derived (p1.ts H): the sleep racer asks the caller's Clock for a Duration decoded
    from the input; it is interrupted when the source wins; a number is milliseconds."""
    clock = GateClock()
    assert await guarded(clock.provide(timeout_option(yield_now.as_("y"), "1 minute"))) == Some("y")
    assert (clock.asked, clock.cancelled) == ([D.minutes(1)], [D.minutes(1)])

    clock = GateClock()

    @gen
    def program():
        f: Fiber[Option[Never], Never] = yield from fork(
            timeout_option(never, 10), start_immediately=True
        )
        yield from clock.fire_until(10)
        return (yield from f.join())

    assert await guarded(clock.provide(program())) is None
    assert (clock.asked, clock.cancelled) == ([D.millis(10)], [])


async def test_source_invalid_durations_fail_where_upstream_decodes_them():
    """Source-derived (p1.ts H): timeout_option hands the input to `sleep`, which decodes it when
    the sleep racer runs, so it dies there; timeout decodes when called (:3976), so it raises."""
    clock = GateClock()
    bad: Any = "bogus"
    ex = await guarded(clock.provide(exit_(timeout_option(never, bad))))
    assert (reasons(ex), clock.asked) == ([("die", "Invalid Input: bogus")], [])
    with pytest.raises(ValueError, match="Invalid Input: bogus"):
        timeout(never, bad)


async def test_source_timeouts_keep_typed_channels():
    """Source-derived (p1.ts I2): a source failure before the deadline is the result, typed, for
    all three; a fallback's failure is the result of timeout_or_else; Some(None) is a present
    value, not a timeout."""
    clock = GateClock()

    @gen
    def program():
        a = yield from exit_(timeout(fail("src-err"), "1 minute"))
        b = yield from exit_(timeout_option(fail("src-err"), "1 minute"))
        c = yield from exit_(
            timeout_or_else(fail("src-err"), duration="1 minute", or_else=lambda: succeed(0))
        )
        f: Fiber[Never, str] = yield from fork(
            timeout_or_else(never, duration=5, or_else=lambda: fail("fb-err")),
            start_immediately=True,
        )
        yield from clock.fire_until(5)
        d = yield from f.await_()
        e = yield from timeout_option(succeed(None), "1 minute")
        return [reasons(x) for x in (a, b, c, d)], e

    exits, present = await guarded(clock.provide(program()))
    assert exits == [[("fail", "src-err")]] * 3 + [[("fail", "fb-err")]]
    assert present == Some(None)


@pytest.mark.parametrize(("name", "combinator"), BOTH)
async def test_queue_message_survives_race_loser_interruption(name: str, combinator: Combinator):
    """Effect.test.ts:1572 with real Queue: interrupt before its scheduled wake consumes."""
    scheduler = YieldParentOnce()
    winner = Deferred[str].make_unsafe()
    accepted: list[str] = []
    loser_exits: list[Exit[Any, Any]] = []

    @gen
    def program():
        queue = yield from Queue[str].unbounded()

        def body(parent: Fiber[Any, Any]) -> Effect[Any, Any, Any]:
            scheduler.parent = parent
            loser = queue.take().tap(lambda value: sync(lambda: accepted.append(value)))
            return combinator(
                loser.pipe(on_exit(lambda ex: sync(lambda: loser_exits.append(ex)))),
                winner.await_(),
            )

        fiber: Fiber[Any, Any] = yield from fork(WithFiber(body), start_immediately=True)
        assert queue.offer_unsafe("loser")
        scheduler.yield_parent = True
        winner.done_unsafe(succeed("winner"))
        for _ in range(100):
            if not scheduler.tasks:
                break
            scheduler.tasks.pop(0)()
        assert not scheduler.tasks, "dispatcher did not settle"
        assert (yield from fiber.join()) == "winner", name
        assert accepted == []
        assert [reasons(ex) for ex in loser_exits] == [[("interrupt", fiber.id)]]
        assert queue.size_unsafe() == 1
        assert (yield from queue.take()) == "loser"
        assert queue.size_unsafe() == 0

    await guarded(provide(Scheduler.context(scheduler))(program()))


@pytest.mark.parametrize("capacity", [0, 1])
async def test_timeout_removes_queue_taker_before_next_message(capacity: int):
    """Real Queue/TestClock composition: timeout cleanup cannot steal the next message."""

    @gen
    def program():
        queue = yield from Queue[int].bounded(capacity)
        timed = yield from fork(timeout_option(queue.take(), 1), start_immediately=True)
        yield from TestClock.adjust(1)
        assert (yield from timed.join()) is None
        offered = yield from fork(queue.offer(7), start_immediately=True)
        assert (yield from queue.take()) == 7
        assert (yield from offered.join()) is True
        assert queue.size_unsafe() == 0

    await guarded(scoped(provide_layer(TestClock.layer())(program())))
