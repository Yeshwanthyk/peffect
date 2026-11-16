"""ManagedRuntime disposal under controlled interleavings. Source-derived, Effect v4 289a53f7.

Every fiber here runs on a manual MixedScheduler (precedent: manual_immediate in
tests/test_scheduler_upstream.py): each armed batch waits in `pending` until the test steps it,
so no asyncio loop and no clock is involved. Disposers run with MaxOpsBeforeYield=1, so every op
boundary of a disposer is a point where the test can interrupt it.

Oracle. The expected outcomes come from the pinned vendor, not from any prototype: the same
World built in TypeScript over vendor/effect-v4 at 289a53f (scratch mr/world.ts, run under bun)
gives:
- interrupting the disposer right after its claim: disposer interrupt, request R interrupt,
  events ["R cleanup", "released"], a later disposer ok;
- interrupting after k batches, for every k: either unclaimed (disposer interrupt, R pending, no
  events), claimed (that same full close, disposer interrupt), or finished (disposer ok);
- a waiting disposer is interrupted at once while the closing one runs to the end of the close;
- probe s08 reaches quiescence with all three fibers pending.
The source behind them, ManagedRuntime.ts: the claim and fiberEnterUninterruptibleUnsafe happen
in one synchronous step (:410-419), the close runs uninterruptibly and always completes the
deferred (:420-423), a later disposer awaits that deferred interruptibly (:407).

"Claimed" is read from the public surface: the claim clears cachedContext (:419), and nothing in
this World sets it again (only a build's tap does, :364-369, and the build finished first).
There is no universal terminal-state invariant: a late request may still succeed when it read
the cached context first, which is why S6 allows ok or the disposal defect.

Quiescence (no pending batch) is exact in this closed world: nothing outside the test can arm
work. A timed wait proves nothing about deadlock and none is used here.
"""

from collections.abc import Sequence
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from peffect import (
    Deferred,
    Die,
    Fiber,
    Interrupt,
    Layer,
    MaxOpsBeforeYield,
    MixedScheduler,
    Scheduler,
    Success,
    add_finalizer,
    ensuring,
    fork_daemon,
    gen,
    never,
    run_fork,
    run_fork_with,
    succeed,
    sync,
    yield_now,
)
from peffect.fiber import Task
from peffect.managed_runtime import ManagedRuntime

DISPOSED = "die:ManagedRuntime disposed"
CLOSED = ["R cleanup", "released"]


class Manual:
    """A scheduler whose armed batches run only when stepped."""

    def __init__(self) -> None:
        self.pending: list[Task] = []
        self.scheduler = MixedScheduler("async", self._arm)

    def _arm(self, batch: Task) -> Task:
        self.pending.append(batch)
        return lambda: self.pending.remove(batch) if batch in self.pending else None

    def step(self, i: int = 0) -> None:
        self.pending.pop(i)()

    def drain(self, limit: int = 20_000) -> bool:
        """Run batches until none is armed; True when the world is quiescent."""
        for _ in range(limit):
            if not self.pending:
                return True
            self.step()
        return not self.pending


def kind(f: Fiber[Any, Any] | None) -> str:
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


class World:
    """The explorer's World: a built runtime whose layer finalizer yields once, one managed
    request R that cleans up after a yield, and disposers that run one op per batch."""

    def __init__(self) -> None:
        self.m = Manual()
        s = self.m.scheduler
        self.events: list[str] = []
        released = yield_now.flat_map(lambda _: sync(lambda: self.events.append("released")))
        self.rt = ManagedRuntime.make(Layer.effect_discard(add_finalizer(lambda _: released)))
        first = self.rt.run_fork(succeed(None), scheduler=s)
        assert self.m.drain() and kind(first) == "ok", "harness: the first request must build"
        assert self.rt.cached_context is not None, "harness: the first request must cache"
        cleanup = yield_now.flat_map(lambda _: sync(lambda: self.events.append("R cleanup")))
        self.R = self.rt.run_fork(never.pipe(ensuring(cleanup)), scheduler=s)
        self._fine = run_fork_with(Scheduler.context(s) | MaxOpsBeforeYield.context(1))

    def disposer(self) -> Fiber[None, Any]:
        return self._fine(self.rt.dispose_effect)

    def late(self) -> Fiber[int, Any]:
        return self.rt.run_fork(sync(lambda: 1), scheduler=self.m.scheduler)

    @property
    def claimed(self) -> bool:
        return self.rt.cached_context is None

    def violations(
        self, disposers: list[Fiber[None, Any]], interrupted: set[int], late: Fiber[int, Any] | None
    ) -> list[str]:
        """The vendor outcome classes as invariants, checked at quiescence."""
        bad: list[str] = []
        if not self.m.drain():
            bad.append("not quiescent")
        if self.claimed:
            if self.events != CLOSED:
                bad.append(f"S1/S3 claimed but events {self.events}")
            if kind(self.R) != "interrupt":
                bad.append(f"S2 claimed but R is {kind(self.R)}")
            probe = run_fork(self.rt.dispose_effect, scheduler=self.m.scheduler)
            self.m.drain()
            if kind(probe) != "ok":
                bad.append(f"L1 later disposer is {kind(probe)}")
        elif self.events or kind(self.R) != "pending":
            bad.append(f"S4 unclaimed but events {self.events}, R {kind(self.R)}")
        for i, d in enumerate(disposers):
            allowed = {"ok", "interrupt"} if i in interrupted else {"ok"}
            if kind(d) not in allowed:
                bad.append(f"L2/S5 disposer {i} is {kind(d)}")
        if late is not None and kind(late) not in ("ok", DISPOSED):
            bad.append(f"S6 late request is {kind(late)}")
        return bad


def test_interrupt_immediately_after_the_claim_still_closes():
    """Regression for the claim-before-mask defect: with the mask taken in a later op than the
    claim, an interrupt between them leaves `disposed` set and the close never run, so R and
    every later disposer hang. Upstream masks in the claiming step (:410)."""
    w = World()
    d = w.disposer()
    while not w.claimed:
        assert w.m.pending and d.exit is None, "harness: the disposer must reach its claim"
        w.m.step()
    assert (kind(d), kind(w.R), w.events) == ("pending", "pending", [])
    d.interrupt_unsafe()
    assert w.m.drain()
    assert (kind(d), kind(w.R), w.events) == ("interrupt", "interrupt", CLOSED)
    assert w.violations([d], {0}, None) == []


def test_interrupt_after_every_step_lands_in_a_vendor_outcome_class():
    """Exhaustive over k: interrupt the disposer after k batches, then drain.

    The class is fixed by the state at the interrupt, not by the drain: vendor mr/world.ts (b)
    records claimed-before, claimed-right-after-the-interrupt and claimed-at-quiescence, and they
    agree for every k (k=0 unclaimed; 1-32 claimed; 33 finished). The decision, the mask and the
    claim are one synchronous step (:401 withFiber, :410-419), so the vendor has no state that is
    masked but not yet claimed: an interrupt that lands before the claim always wins. A port that
    masks in one op and claims in the next turns such an interrupt into a full close; the step
    counts may differ from the vendor's, the classes may not."""
    classes: dict[str, list[int]] = {}
    k = 0
    while True:
        w = World()
        d = w.disposer()
        for _ in range(k):
            if not w.m.pending or d.exit is not None:
                break
            w.m.step()
        finished = d.exit is not None
        claimed_before = w.claimed
        d.interrupt_unsafe()
        assert w.claimed == claimed_before, f"k={k}: delivering the interrupt changed the claim"
        assert w.m.drain(), f"k={k}: not quiescent"
        outcome = (w.claimed, kind(d), kind(w.R), w.events)
        assert w.claimed == claimed_before, f"k={k}: claimed={claimed_before} at the interrupt"
        if finished:
            assert outcome == (True, "ok", "interrupt", CLOSED), f"k={k}: {outcome}"
            label = "finished"
        elif w.claimed:
            assert outcome == (True, "interrupt", "interrupt", CLOSED), f"k={k}: {outcome}"
            label = "claimed"
        else:
            assert outcome == (False, "interrupt", "pending", []), f"k={k}: {outcome}"
            label = "unclaimed"
        assert w.violations([d], {0}, None) == [], f"k={k}"
        classes.setdefault(label, []).append(k)
        if finished:
            break
        k += 1
    assert {"unclaimed", "claimed", "finished"} <= classes.keys(), classes


def test_an_interrupt_at_any_point_before_the_claim_disposes_nothing():
    """Source-derived (:401, :410-419): every op boundary the disposer passes before its claim is
    one where an interrupt must leave the runtime as it was: unclaimed, R running, no events, and
    a later disposer still the one that closes. Checked at each pre-claim boundary in turn."""
    boundaries = 0
    while True:
        w = World()
        d = w.disposer()
        for _ in range(boundaries):
            w.m.step()
        if w.claimed:
            break
        d.interrupt_unsafe()
        assert w.m.drain()
        assert (w.claimed, kind(d), kind(w.R), w.events) == (False, "interrupt", "pending", []), (
            f"interrupt after {boundaries} batches, before the claim"
        )
        second = w.disposer()
        assert w.m.drain()
        assert (kind(second), kind(w.R), w.events) == ("ok", "interrupt", CLOSED)
        boundaries += 1
        assert boundaries < 50, "harness: the disposer never reached its claim"
    assert boundaries >= 1, "harness: the claim must not precede the disposer's first batch"


def replay(
    w: World, script: Sequence[str]
) -> tuple[list[Fiber[None, Any]], set[int], Fiber[int, Any] | None]:
    """Run an explorer trace: spawn / late / intN act; an integer steps that pending batch."""
    ds: list[Fiber[None, Any]] = []
    interrupted: set[int] = set()
    late: Fiber[int, Any] | None = None
    for token in script:
        if token == "spawn":
            ds.append(w.disposer())
        elif token == "late":
            late = w.late()
        elif token.startswith("int"):
            ds[int(token[3:])].interrupt_unsafe()
            interrupted.add(int(token[3:]))
        else:
            i = int(token)
            assert i < len(w.m.pending), f"harness: trace step {i} with {len(w.m.pending)} pending"
            w.m.step(i)
    return ds, interrupted, late


def test_named_explorer_trace_late_spawn_0_spawn_1_1_int0_0():
    """probes/explore.py B, seed 20261115, iteration 8, the first schedule that exposed the
    claim-before-mask defect (its leading `skip` was an int1 before any disposer existed).
    The indices name batches of the Python prototype; a faithful port arms the same batches."""
    w = World()
    ds, interrupted, late = replay(w, "late spawn 0 spawn 1 1 int0 0".split())
    assert w.violations(ds, interrupted, late) == []


@given(st.data())
def test_any_schedule_of_two_disposers_a_late_request_and_interrupts_keeps_the_invariants(
    data: st.DataObject,
):
    """Property. Domain: the explorer's alphabet (two disposers, one late request, an interrupt
    of each disposer) in any order, interleaved with any choice of armed batch. Budget: the
    project Hypothesis profile (tests/conftest.py: 600 examples, 20 000 under `deep`)."""
    w = World()
    todo: list[str] = data.draw(st.permutations(["spawn", "spawn", "late", "int0", "int1"]))
    ds: list[Fiber[None, Any]] = []
    interrupted: set[int] = set()
    late: Fiber[int, Any] | None = None
    for _ in range(5_000):
        if not todo and not w.m.pending:
            break
        if todo and (not w.m.pending or data.draw(st.integers(0, 6)) == 0):
            a = todo.pop()
            if a == "spawn":
                ds.append(w.disposer())
            elif a == "late":
                late = w.late()
            elif int(a[3]) < len(ds):
                ds[int(a[3])].interrupt_unsafe()
                interrupted.add(int(a[3]))
        else:
            w.m.step(data.draw(st.integers(0, len(w.m.pending) - 1)))
    assert w.violations(ds, interrupted, late) == []


def test_a_waiting_disposer_is_interruptible_while_the_closer_is_not():
    """Vendor mr/world.ts (c). The closer is masked through the close (:410, :420-423); the waiter
    only awaits the deferred (:407) and ends interrupted at once."""
    m = Manual()
    s = m.scheduler
    events: list[str] = []
    release = Deferred[None, Any].make_unsafe()
    rt = ManagedRuntime.make(
        Layer.effect_discard(
            add_finalizer(
                lambda _: release.await_().flat_map(
                    lambda _: sync(lambda: events.append("layer released"))
                )
            )
        )
    )
    rt.run_fork(succeed(None), scheduler=s)
    assert m.drain()
    first = run_fork(rt.dispose_effect, scheduler=s)
    assert m.drain()
    second = run_fork(rt.dispose_effect, scheduler=s)
    assert m.drain()
    assert (kind(first), kind(second)) == ("pending", "pending")
    second.interrupt_unsafe()
    first.interrupt_unsafe()
    assert m.drain()
    assert (kind(first), kind(second)) == ("pending", "interrupt")
    release.done_unsafe(Success(None))
    assert m.drain()
    assert (kind(first), kind(second), events) == ("interrupt", "interrupt", ["layer released"])


def test_source_a_daemon_disposer_joined_by_managed_cleanup_never_completes():
    """Probe s08, upstream quirk; vendor mr/world.ts (d). R forks a daemon that disposes after a
    gate, and R's cleanup awaits that daemon. An outside disposer X claims and closes fiberScope,
    which interrupts R and awaits its cleanup; the daemon is neither managed nor a descendant of
    one (forkDetach clears the parent link) and lacks DisposingRuntimes (:403-407), so it awaits
    X's deferred. The world goes quiescent with all three pending: no armed work remains."""
    m = Manual()
    s = m.scheduler
    events: list[str] = []
    g = Deferred[None, Any].make_unsafe()
    rt = ManagedRuntime.make(
        Layer.effect_discard(add_finalizer(lambda _: sync(lambda: events.append("layer released"))))
    )
    rt.run_fork(succeed(None), scheduler=s)
    assert m.drain()
    daemon: list[Fiber[Any, Any]] = []

    @gen
    def body():
        d = yield from fork_daemon(g.await_().flat_map(lambda _: rt.dispose_effect))
        daemon.append(d)
        yield from never.pipe(ensuring(d.await_()))

    r = rt.run_fork(body(), scheduler=s)
    assert m.drain()
    x = run_fork(rt.dispose_effect, scheduler=s)
    assert m.drain()
    g.done_unsafe(Success(None))
    assert m.drain()
    assert ([kind(r), kind(daemon[0]), kind(x)], events) == (["pending"] * 3, [])
