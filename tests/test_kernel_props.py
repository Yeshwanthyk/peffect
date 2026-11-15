"""Generated programs against the real kernel. See docs/INVARIANTS.md (K1-K9).

A program is a small tree (Prog). `compile_` turns it into a peffect Effect that logs what it does;
`reference` interprets the same tree with plain recursion and no fibers. The two are written
independently: if they disagree, the kernel (or the oracle) is wrong.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from hypothesis import event, example, given
from hypothesis import strategies as st

from peffect import (
    Cause,
    Effect,
    Failure,
    Fiber,
    Success,
    fork,
    run_exit,
    uninterruptible,
    yield_now,
)
from peffect.cause import Die, Fail, Interrupt
from peffect.core import Async, Resume, Sync, WithFiber, die, fail, fail_cause, succeed, sync
from peffect.fiber import _start  # pyright: ignore[reportPrivateUsage]

# -- programs ---


@dataclass(frozen=True)
class Pure:
    v: int


@dataclass(frozen=True)
class Raise:
    tag: str


@dataclass(frozen=True)
class Defect:
    id: int


@dataclass(frozen=True)
class Map:
    a: Prog


@dataclass(frozen=True)
class Bind:
    a: Prog
    b: Prog


@dataclass(frozen=True)
class Catch:
    id: int
    body: Prog
    handler: Prog


@dataclass(frozen=True)
class Ensure:
    id: int
    body: Prog
    fin_dies: bool


@dataclass(frozen=True)
class Mask:
    id: int
    body: Prog


@dataclass(frozen=True)
class Park:
    id: int


@dataclass(frozen=True)
class Pause:
    pass


@dataclass(frozen=True)
class Fork:
    body: Prog


type Prog = Pure | Raise | Defect | Map | Bind | Catch | Ensure | Mask | Park | Pause | Fork

_ids = iter(range(10**9))


def _progs(with_fork: bool) -> st.SearchStrategy[Prog]:
    nid = st.builds(lambda: next(_ids))
    leaves: st.SearchStrategy[Prog] = st.one_of(
        st.builds(Pure, st.integers(0, 9)),
        st.builds(Raise, st.sampled_from(["a", "b"])),
        st.builds(Defect, nid),
        st.builds(Park, nid),
        st.just(Pause()),
    )

    def extend(kids: st.SearchStrategy[Prog]) -> st.SearchStrategy[Prog]:
        options: list[st.SearchStrategy[Prog]] = [
            st.builds(Map, kids),
            st.builds(Bind, kids, kids),
            st.builds(Catch, nid, kids, kids),
            st.builds(Ensure, nid, kids, st.booleans()),
            st.builds(Mask, nid, kids),
        ]
        if with_fork:
            options.append(st.builds(Fork, kids))
        return st.one_of(options)

    return st.recursive(leaves, extend, max_leaves=12)


# -- compile to peffect ---

type Reason = tuple[str, Any]


@dataclass
class Run:
    log: list[str] = field(default_factory=list[str])
    violations: list[str] = field(default_factory=list[str])
    forked: list[Fiber[Any, Any]] = field(default_factory=list[Fiber[Any, Any]])
    dies: list[str] = field(default_factory=list[str])  # defects raised by Defect leaves

    def note(self, s: str) -> Effect[None, Any, Any]:
        return sync(lambda: self.log.append(s))


def compile_(p: Prog, r: Run) -> Effect[int, Any, Any]:
    match p:
        case Pure(v):
            return succeed(v)
        case Raise(tag):
            return fail(tag)
        case Defect(i):
            return sync(lambda: r.dies.append(f"d{i}")).flat_map(
                lambda _: die(RuntimeError(f"d{i}"))
            )
        case Map(a):
            return compile_(a, r).map(lambda x: x + 1)
        case Bind(a, b):
            return compile_(a, r).flat_map(lambda _: compile_(b, r))
        case Catch(i, body, handler):

            def on_err(_e: Any) -> Effect[int, Any, Any]:
                def check(f: Fiber[Any, Any]) -> Effect[int, Any, Any]:
                    if f.interrupted is not None and f.interruptible:  # K5
                        r.violations.append(f"K5: catch {i} ran during an interrupt")
                    r.log.append(f"catch:{i}")
                    return compile_(handler, r)

                return WithFiber(check)

            return compile_(body, r).catch_all(on_err)
        case Ensure(i, body, fin_dies):
            fin = r.note(f"fin:{i}")
            if fin_dies:
                fin = fin.flat_map(lambda _: die(RuntimeError(f"f{i}")))
            return r.note(f"enter:{i}").flat_map(lambda _: compile_(body, r)).ensuring(fin)
        case Mask(i, body):

            def saw(c: Cause[Any]) -> Effect[int, Any, Any]:
                if c.is_interrupted:  # K6: nothing interrupts a masked region
                    r.violations.append(f"K6: mask {i} saw an interrupt")
                return fail_cause(c)

            return uninterruptible(compile_(body, r).catch_cause(saw))
        case Park(i):

            def register(resume: Resume[int, Any, Any]) -> Effect[None, Any, Any]:
                h = asyncio.get_running_loop().call_soon(resume, Success(0))

                def cancel() -> None:
                    h.cancel()
                    r.log.append(f"cancel:{i}")

                return Sync(cancel)

            return Async(register)
        case Pause():
            return yield_now.as_(0)
        case Fork(body):
            child = Run()  # its log isn't compared: its timing is the scheduler's business
            return fork(compile_(body, child)).map(lambda f: (r.forked.append(f), 0)[1])


def reasons(c: Cause[Any]) -> list[Reason]:
    out: list[Reason] = []
    for x in c.reasons:
        match x:
            case Fail(error):
                out.append(("fail", error))
            case Die(defect):
                out.append(("die", str(defect)))
            case Interrupt():
                out.append(("interrupt", None))
    return out


# -- the reference interpreter (no fibers, no interrupts) ---

type RExit = tuple[bool, Any]  # (True, value) | (False, [reasons])


def _add(a: list[Reason], b: list[Reason]) -> list[Reason]:
    return a + [x for x in b if x not in a]


def _elect(v: list[Reason]) -> list[Reason]:
    """causeElectDefects (Effect v4 internal/effect.ts:184-191): with a die present, every fail
    becomes a die holding the same value."""
    if not any(k == "die" for k, _ in v):
        return v
    return [("die", x) if k == "fail" else (k, x) for k, x in v]


def reference(p: Prog, log: list[str]) -> RExit:
    match p:
        case Pure(v):
            return True, v
        case Raise(tag):
            return False, [("fail", tag)]
        case Defect(i):
            return False, [("die", f"d{i}")]
        case Map(a):
            ok, v = reference(a, log)
            return (True, v + 1) if ok else (False, v)
        case Bind(a, b):
            ok, v = reference(a, log)
            return reference(b, log) if ok else (False, v)
        case Catch(i, body, handler):
            ok, v = reference(body, log)
            if ok:
                return True, v
            recoverable = not any(k == "die" for k, _ in v) and any(k == "fail" for k, _ in v)
            if not recoverable:
                return False, v
            log.append(f"catch:{i}")
            return reference(handler, log)
        case Ensure(i, body, fin_dies):
            log.append(f"enter:{i}")
            ok, v = reference(body, log)
            log.append(f"fin:{i}")
            fin: list[Reason] = [("die", f"f{i}")] if fin_dies else []
            if not fin:
                return ok, v
            return False, (fin if ok else _elect(_add(v, fin)))  # combineFinalizerCause
        case Mask(_, body):
            return reference(body, log)
        case Park():
            return True, 0
        case Pause():
            return True, 0
        case Fork():
            return True, 0


# -- properties ---


@given(_progs(with_fork=False))
async def test_kernel_matches_reference(p: Prog):
    """No interrupts: the kernel computes what plain recursion computes, with the same log."""
    r = Run()
    ex = await run_exit(compile_(p, r))
    want_log: list[str] = []
    ok, v = reference(p, want_log)
    got = (True, ex.value) if isinstance(ex, Success) else (False, reasons(ex.cause))
    assert got == (ok, v)
    assert r.log == want_log
    assert r.violations == []


@given(_progs(with_fork=True), st.integers(0, 8))
# found by --hypothesis-profile=deep when the skip loop stopped adding the interrupt (K7):
# an interrupt that waited out a mask, arriving on a failure that is already unwinding
@example(p=Mask(-1, Ensure(-2, Park(-3), fin_dies=True)), ticks=1)
@example(p=Mask(-1, Bind(Park(-2), Raise("a"))), ticks=1)
async def test_interrupt_at_any_tick(p: Prog, ticks: int):
    """Interrupt the program after `ticks` scheduler turns; the kernel invariants must hold."""
    r = Run()
    published: list[Any] = []

    async def scenario() -> None:
        # started directly, as a root: it runs its first slice now (internal/effect.ts:5777), so
        # tick 0 means "after the first slice, before any wake".
        target: Fiber[Any, Any] = _start(None, compile_(p, r), {})

        def on_publish(ex: Any) -> None:
            published.append(ex)
            # K8: every child it forked has already published
            for c in r.forked:
                if c.exit is None:
                    r.violations.append("K8: published before a forked child")

        target.add_observer(on_publish)
        for _ in range(ticks):
            await asyncio.sleep(0)
        finished_before = target.exit is not None
        event("finished before interrupt" if finished_before else "interrupted mid-run")
        target.interrupt_unsafe(by=0)
        while target.exit is None:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        ex = target.exit
        # K1: exactly one publish, and late observers still hear it
        late: list[Any] = []
        target.add_observer(late.append)
        assert len(published) == 1 and late == [ex]
        # K7: an interrupt sent before the end lands in the cause
        if not finished_before:
            assert isinstance(ex, Failure) and ex.cause.is_interrupted, ex
        # K4: each finalizer whose region was entered ran exactly once; none ran twice
        for line in r.log:
            if line.startswith("enter:"):
                i = line.split(":")[1]
                assert r.log.count(f"fin:{i}") == 1, (i, r.log)
        assert all(r.log.count(x) == 1 for x in r.log if x.startswith("fin:"))
        # K9: no defect that happened is lost (a defect is never caught, only combined)
        if isinstance(ex, Failure):
            got = reasons(ex.cause)
            for x in [*_fin_defects(p, r.log), *r.dies]:
                assert ("die", x) in got, (x, got)
        assert r.violations == []

    await asyncio.wait_for(scenario(), timeout=5)


def _fin_defects(p: Prog, log: list[str]) -> list[str]:
    """Tags of finalizer defects that actually happened (the finalizer ran and was set to die)."""
    out: list[str] = []

    def walk(q: Prog) -> None:
        match q:
            case Ensure(i, body, fin_dies):
                if fin_dies and f"fin:{i}" in log:
                    out.append(f"f{i}")
                walk(body)
            case Map(a) | Mask(_, a):
                walk(a)
            case Bind(a, b) | Catch(_, a, b):
                walk(a)
                walk(b)
            case _:
                pass

    walk(p)
    return out
