"""Generated operation sequences for Ref and SynchronizedRef at Effect v4 pin 289a53f7.

Shared value domain: ints 0..7 and a parameter k in 0..7. Every user function is the same
transition t(s, k) = (s + k) % 8; a partial one is Some(t) when s is even, else None; a modify
result is b(s, k) = s * 10 + k, so a wrong argument shows in the result. The models below are
transcribed from Ref.ts and SynchronizedRef.ts operation by operation, not from the port.

1. Ref, sequential. Entry point: the real Ref methods, run one after another. Oracle: `REF_MODEL`
   gives each operation's result and the new value; every result and the final value must
   match. Sequential evidence only.

2. SynchronizedRef, concurrent. Entry point: the real SynchronizedRef, operations forked as
   fibers (start_immediately) on the default scheduler; the driver interleaves by spawning,
   opening gates, interrupting, and single yield_now turns: no clocks, sleeps or timers.
   Domain: all 11 pure updates and all 8 effectful ones; an effectful function may wait on its
   own gate and then succeeds, fails, or dies. Every user function logs `enter` with the value
   it was given and `exit` (an effectful one through `ensuring`, so interruption logs it too).
   Oracle, from SynchronizedRef.ts (each update holds `semaphore.withPermit`, reads the value
   inside it, and writes only from the function's success):
   - exclusion: the log alternates enter/exit of one call at a time;
   - serial commit: replaying calls in enter order through `SYNC_MODEL`, each call is given the
     value the previous ones committed; a pure call that ran committed (its function and write
     are one step); an effectful call whose function and fiber succeeded committed; one that
     failed, died, or never entered did not; one interrupted after its function produced a
     value is the one ambiguous case (accepted either way and counted with `event`);
   - results: every successful fiber returns the model's result for the value it was given;
   - liveness and release: once every gate is open, every fiber ends, the semaphore holds no
     permit and no waiter, and a further update completes.
   `get` is unsynchronized upstream and is pinned by test_synchronized_ref_upstream.py instead.
   The functions never mutate their argument in place; no rollback of in-place mutation exists
   or is claimed.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Never

from hypothesis import event, given, settings
from hypothesis import strategies as st
from peffect.ref import Ref
from peffect.synchronized_ref import SynchronizedRef

from peffect import (
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
    fail,
    fork,
    gen,
    run,
    succeed,
    sync,
)
from peffect import yield_now as yield_now_
from peffect.option import Option, Some


def t(s: int, k: int) -> int:
    return (s + k) % 8


def pt(s: int, k: int) -> Option[int]:
    return Some(t(s, k)) if s % 2 == 0 else None


def b(s: int, k: int) -> int:
    return s * 10 + k


def some_or(s: int, k: int) -> int:
    """The value a partial update leaves: t when it applies, else the old value."""
    return t(s, k) if s % 2 == 0 else s


# name -> (s, k) -> (result, new value), one line per Ref.ts export.
REF_MODEL: dict[str, Callable[[int, int], tuple[object, int]]] = {
    "get": lambda s, k: (s, s),
    "set": lambda s, k: (None, k),
    "get_and_set": lambda s, k: (s, k),
    "set_and_get": lambda s, k: (k, k),
    "get_and_update": lambda s, k: (s, t(s, k)),
    "update": lambda s, k: (None, t(s, k)),
    "update_and_get": lambda s, k: (t(s, k), t(s, k)),
    "get_and_update_some": lambda s, k: (s, some_or(s, k)),
    "update_some": lambda s, k: (None, some_or(s, k)),
    "update_some_and_get": lambda s, k: (some_or(s, k), some_or(s, k)),
    "modify": lambda s, k: (b(s, k), t(s, k)),
    "modify_some": lambda s, k: (b(s, k), some_or(s, k)),
}

# SynchronizedRef.ts: the pure operations delegate to Ref under the permit (same model, minus
# the unsynchronized get); the effectful ones are transcribed from their own bodies. Note
# getAndUpdateSomeEffect returns the old value, updateSomeAndGetEffect `option.value` or the
# old value, and modifySomeEffect `b` either way.
SYNC_MODEL: dict[str, Callable[[int, int], tuple[object, int]]] = {
    **{name: f for name, f in REF_MODEL.items() if name != "get"},
    "get_and_update_effect": lambda s, k: (s, t(s, k)),
    "get_and_update_some_effect": lambda s, k: (s, some_or(s, k)),
    "modify_effect": lambda s, k: (b(s, k), t(s, k)),
    "modify_some_effect": lambda s, k: (b(s, k), some_or(s, k)),
    "update_effect": lambda s, k: (None, t(s, k)),
    "update_and_get_effect": lambda s, k: (t(s, k), t(s, k)),
    "update_some_effect": lambda s, k: (None, some_or(s, k)),
    "update_some_and_get_effect": lambda s, k: (some_or(s, k), some_or(s, k)),
}
PURE = sorted(name for name in SYNC_MODEL if not name.endswith("_effect"))
EFFECTFUL = sorted(name for name in SYNC_MODEL if name.endswith("_effect"))


def ref_op(ref: Ref[int], name: str, k: int) -> Effect[object, Never, Never]:
    match name:
        case "get":
            return ref.get()
        case "set":
            return ref.set(k)
        case "get_and_set":
            return ref.get_and_set(k)
        case "set_and_get":
            return ref.set_and_get(k)
        case "get_and_update":
            return ref.get_and_update(lambda s: t(s, k))
        case "update":
            return ref.update(lambda s: t(s, k))
        case "update_and_get":
            return ref.update_and_get(lambda s: t(s, k))
        case "get_and_update_some":
            return ref.get_and_update_some(lambda s: pt(s, k))
        case "update_some":
            return ref.update_some(lambda s: pt(s, k))
        case "update_some_and_get":
            return ref.update_some_and_get(lambda s: pt(s, k))
        case "modify":
            return ref.modify(lambda s: (b(s, k), t(s, k)))
        case "modify_some":
            return ref.modify_some(lambda s: (b(s, k), pt(s, k)))
        case _:
            raise AssertionError(name)


@settings(max_examples=200, deadline=None)
@given(
    st.integers(0, 7),
    st.lists(st.tuples(st.sampled_from(sorted(REF_MODEL)), st.integers(0, 7)), max_size=30),
)
async def test_ref_results_and_value_follow_the_model_over_operation_sequences(
    initial: int, ops: list[tuple[str, int]]
):
    ref = Ref[int].make_unsafe(initial)
    state = initial
    for name, k in ops:
        expected, state = REF_MODEL[name](state, k)
        assert await run(ref_op(ref, name, k)) == expected, (name, k)
        assert ref.get_unsafe() == state, (name, k)


# == SynchronizedRef ============================================================================

Outcome = Literal["ok", "fail", "die"]
MARK = "failure"


class Boom(Exception):
    pass


@dataclass
class Call:
    name: str
    k: int
    outcome: Outcome
    gate: Deferred[None, Never]
    fiber: Fiber[object, str] | None = None
    entered: bool = False  # its function ran, or (set family) it completed
    seen: int | None = None  # the value its function was given (get_and_set: the old value)
    produced: bool = False  # the effectful function reached its success value


@dataclass
class Log:
    events: list[tuple[Literal["enter", "exit"], int]] = field(default_factory=list[Any])


def sync_op(sref: SynchronizedRef[int], call: Call, i: int, log: Log) -> Effect[object, str, Never]:
    k = call.k

    def enter(s: int) -> None:
        log.events.append(("enter", i))
        call.entered = True
        call.seen = s

    def pure[T](f: Callable[[int], T]) -> Callable[[int], T]:
        def run_f(s: int) -> T:
            enter(s)
            out = f(s)
            log.events.append(("exit", i))
            return out

        return run_f

    def effectful[T](f: Callable[[int], T]) -> Callable[[int], Effect[T, str, Never]]:
        def run_f(s: int) -> Effect[T, str, Never]:
            enter(s)

            def finish(_: None) -> Effect[T, str, Never]:
                if call.outcome == "fail":
                    return fail(MARK)
                if call.outcome == "die":
                    return die(Boom())
                call.produced = True
                return succeed(f(s))

            exit_log = sync(lambda: log.events.append(("exit", i)))
            return call.gate.await_().flat_map(finish).ensuring(exit_log)

        return run_f

    def logged[T](eff: Effect[T, Never, Never]) -> Effect[T, Never, Never]:
        """set, get_and_set and set_and_get take no function: log them as they complete. The
        permit's release only queues the next holder's wake on the dispatcher (Semaphore.ts:
        323-336), so no other holder runs between the write and this log."""

        def mark(result: T) -> T:
            log.events.extend([("enter", i), ("exit", i)])
            call.entered = True
            if call.name == "get_and_set":
                call.seen = result if isinstance(result, int) else None
            return result

        return eff.map(mark)

    match call.name:
        case "set":
            return logged(sref.set(k))
        case "get_and_set":
            return logged(sref.get_and_set(k))
        case "set_and_get":
            return logged(sref.set_and_get(k))
        case "get_and_update":
            return sref.get_and_update(pure(lambda s: t(s, k)))
        case "update":
            return sref.update(pure(lambda s: t(s, k)))
        case "update_and_get":
            return sref.update_and_get(pure(lambda s: t(s, k)))
        case "get_and_update_some":
            return sref.get_and_update_some(pure(lambda s: pt(s, k)))
        case "update_some":
            return sref.update_some(pure(lambda s: pt(s, k)))
        case "update_some_and_get":
            return sref.update_some_and_get(pure(lambda s: pt(s, k)))
        case "modify":
            return sref.modify(pure(lambda s: (b(s, k), t(s, k))))
        case "modify_some":
            return sref.modify_some(pure(lambda s: (b(s, k), pt(s, k))))
        case "get_and_update_effect":
            return sref.get_and_update_effect(effectful(lambda s: t(s, k)))
        case "get_and_update_some_effect":
            return sref.get_and_update_some_effect(effectful(lambda s: pt(s, k)))
        case "modify_effect":
            return sref.modify_effect(effectful(lambda s: (b(s, k), t(s, k))))
        case "modify_some_effect":
            return sref.modify_some_effect(effectful(lambda s: (b(s, k), pt(s, k))))
        case "update_effect":
            return sref.update_effect(effectful(lambda s: t(s, k)))
        case "update_and_get_effect":
            return sref.update_and_get_effect(effectful(lambda s: t(s, k)))
        case "update_some_effect":
            return sref.update_some_effect(effectful(lambda s: pt(s, k)))
        case "update_some_and_get_effect":
            return sref.update_some_and_get_effect(effectful(lambda s: pt(s, k)))
        case _:
            raise AssertionError(call.name)


Step = (
    tuple[Literal["call"], str, int, Outcome, bool]
    | tuple[Literal["open", "interrupt"], int]
    | tuple[Literal["turn"]]
)

OUTCOMES: list[Outcome] = ["ok", "fail", "die"]
DRIVER: list[Literal["open", "interrupt"]] = ["open", "interrupt"]


def call_step(name: str, k: int, outcome: Outcome, gated: bool) -> Step:
    return ("call", name, k, outcome, gated)


def driver_step(kind: Literal["open", "interrupt"], i: int) -> Step:
    return (kind, i)


TURN: Step = ("turn",)

steps: st.SearchStrategy[list[Step]] = st.lists(
    st.one_of(
        st.builds(
            call_step, st.sampled_from(PURE), st.integers(0, 7), st.just("ok"), st.just(False)
        ),
        st.builds(
            call_step,
            st.sampled_from(EFFECTFUL),
            st.integers(0, 7),
            st.sampled_from(OUTCOMES),
            st.booleans(),
        ),
        st.builds(driver_step, st.sampled_from(DRIVER), st.integers(-3, 7)),  # <0: recent calls
        st.just(TURN),
    ),
    max_size=24,
)


def reasons(ex: Exit[object, str] | None) -> list[type]:
    assert isinstance(ex, Failure)
    return [type(r) for r in ex.cause.reasons]


@settings(max_examples=200, deadline=None)
@given(st.integers(0, 7), steps)
async def test_synchronized_ref_serializes_and_commits_only_successful_updates(
    initial: int, sequence: list[Step]
):
    log = Log()
    calls: list[Call] = []

    @gen
    def program():
        sref = yield from SynchronizedRef[int].make(initial)
        for step in sequence:
            match step:
                case ("call", name, k, outcome, gated):
                    gate = yield from Deferred[None, Never].make()
                    if not gated:
                        yield from gate.succeed(None)
                    call = Call(name, k, outcome, gate)
                    calls.append(call)
                    op = sync_op(sref, call, len(calls) - 1, log)
                    call.fiber = yield from fork(op, start_immediately=True)
                    if call.fiber.exit is None and name in PURE:
                        event("a pure call queued behind the permit")
                case ("open", i):  # constructive: the index always names an existing call
                    if calls:
                        yield from calls[i % len(calls)].gate.succeed(None)
                case ("interrupt", i):
                    if calls and (fiber := calls[i % len(calls)].fiber) is not None:
                        yield from fiber.interrupt()
                case ("turn",):
                    yield from yield_now_
                case _:
                    raise AssertionError(step)

        for call in calls:
            yield from call.gate.succeed(None)
        for _ in range(4 * len(calls) + 4):
            if all(c.fiber is not None and c.fiber.exit is not None for c in calls):
                break
            yield from yield_now_
        final = sref.get_unsafe()
        assert sref.semaphore.taken == 0
        assert not sref.semaphore.waiters
        yield from sref.update(lambda s: s)
        return final

    final = await run(program())

    # exclusion
    events = log.events
    assert len(events) % 2 == 0, events
    for at in range(0, len(events), 2):
        assert events[at][0] == "enter" and events[at + 1] == ("exit", events[at][1]), events

    # serial commit and results
    possible = {initial}
    for _, i in events[0::2]:
        call = calls[i]
        ex = call.fiber.exit if call.fiber is not None else None
        if call.name in ("set", "set_and_get"):
            s = min(possible)  # nothing observes the old value; the model ignores it
        else:
            s = call.seen
            assert s is not None and s in possible, (call, possible)
        result, new = SYNC_MODEL[call.name](s, call.k)
        if call.name in PURE:  # the function and the write are one step: entered is committed
            assert isinstance(ex, Success) and ex.value == result, (call.name, s, call.k, ex)
            possible = {new}
        elif isinstance(ex, Success):
            assert call.outcome == "ok"
            assert ex.value == result, (call.name, s, call.k)
            possible = {new}
        elif call.outcome == "fail" and reasons(ex) == [Fail]:
            assert isinstance(ex, Failure) and ex.cause.reasons[0] == Fail(MARK)
            event("a failed update committed nothing")
            possible = {s}
        elif call.outcome == "die" and reasons(ex) == [Die]:
            event("a dying update committed nothing")
            possible = {s}
        else:
            assert Interrupt in reasons(ex), (call, ex)
            if call.produced:
                event("interrupted after the function produced its value")
                possible = {s, new}
            else:
                event("interrupted inside the function")
                possible = {s}
    assert final in possible
    for call in calls:
        assert call.fiber is not None and call.fiber.exit is not None, "a call never finished"
        if not call.entered:
            event("interrupted before taking the permit")
            assert Interrupt in reasons(call.fiber.exit)
