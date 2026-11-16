"""Ref unit at Effect v4 pin 289a53f7.

Upstream: test/Ref.test.ts (all 15 cases); implementation src/Ref.ts. The first section ports
upstream cases; the second is source-derived and labelled with the source lines it reads. The
generated sequential property is in test_ref_props.py.

Names are snake_case instance methods of Ref.ts: `Ref.make(a)` / `make_unsafe(a)` are
classmethods (Deferred precedent), so a widened cell is `Ref[State].make(...)`; `Ref.get(ref)`
is `ref.get()`, `Ref.getAndUpdateSome(ref, pf)` is `ref.get_and_update_some(pf)`, and so on.
Upstream's data-first/data-last duals only select this receiver and are not separate API.

Adaptations:
- The tagged `State` objects (`{ _tag: "Active" }`) are members of the `State` enum; the
  `isActive`/`isChanged`/`isClosed` guards are identity checks. `assert.strictEqual` and
  `assert.deepEqual` on them are both `is` here.
- Option is `Some[A] | None` (peffect.option): `Option.some(a)` is `Some(a)`, `Option.none()`
  is `None`.
- `modify`'s `readonly [B, A]` is a `tuple[B, A]`.
"""

from collections.abc import Callable
from enum import Enum
from typing import Never

import pytest
from peffect.ref import Ref

from peffect import Die, Effect, Failure, Success, exit_, gen, run
from peffect.option import Option, Some

current = "value"
update = "new value"


class State(Enum):
    ACTIVE = "Active"
    CHANGED = "Changed"
    CLOSED = "Closed"


Active, Changed, Closed = State.ACTIVE, State.CHANGED, State.CLOSED


def active_to_changed(s: State) -> Option[State]:
    return Some(Changed) if s is Active else None


def closed_to_changed(s: State) -> Option[State]:
    return Some(Changed) if s is Closed else None


def active_then_closed(s: State) -> Option[State]:
    """`isActive ? some(Changed) : isChanged ? some(Closed) : none()`."""
    return Some(Changed) if s is Active else Some(Closed) if s is Changed else None


# == upstream: Ref.test.ts =====================================================================


async def test_get_returns_the_current_value():
    """Ref.test.ts:30."""
    assert await run(Ref[str].make_unsafe(current).get()) == current


async def test_get_and_set_returns_the_previous_value_and_stores_the_replacement():
    """Ref.test.ts:36. Upstream asserts only the result; the stored value is checked below."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        result1 = yield from ref.get_and_set(update)
        assert result1 == current

    await run(program())


async def test_get_and_update_returns_the_previous_value_and_stores_the_computed_value():
    """Ref.test.ts:43."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        result1 = yield from ref.get_and_update(lambda _: update)
        assert result1 == current

    await run(program())


async def test_get_and_update_some_leaves_state_unchanged_when_no_transition_matches():
    """Ref.test.ts:50."""

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        result1 = yield from ref.get_and_update_some(closed_to_changed)
        result2 = yield from ref.get()
        assert result1 is Active
        assert result2 is Active

    await run(program())


async def test_get_and_update_some_returns_each_previous_state_while_applying_transitions():
    """Ref.test.ts:63."""

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        result1 = yield from ref.get_and_update_some(active_to_changed)
        result2 = yield from ref.get_and_update_some(active_then_closed)
        result3 = yield from ref.get()
        assert result1 is Active
        assert result2 is Changed
        assert result3 is Closed

    await run(program())


async def test_set_stores_the_provided_value():
    """Ref.test.ts:84."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        yield from ref.set(update)
        assert (yield from ref.get()) == update

    await run(program())


async def test_update_stores_the_computed_value():
    """Ref.test.ts:92."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        yield from ref.update(lambda _: update)
        assert (yield from ref.get()) == update

    await run(program())


async def test_update_and_get_stores_and_returns_the_computed_value():
    """Ref.test.ts:100."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        assert (yield from ref.update_and_get(lambda _: update)) == update

    await run(program())


async def test_update_some_leaves_state_unchanged_when_no_transition_matches():
    """Ref.test.ts:107."""

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        yield from ref.update_some(closed_to_changed)
        assert (yield from ref.get()) is Active

    await run(program())


async def test_update_some_applies_matching_transitions_to_the_stored_state():
    """Ref.test.ts:115."""

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        yield from ref.update_some(active_to_changed)
        result1 = yield from ref.get()
        yield from ref.update_some(active_then_closed)
        result2 = yield from ref.get()
        assert result1 is Changed
        assert result2 is Closed

    await run(program())


async def test_update_some_and_get_returns_the_current_state_when_no_transition_matches():
    """Ref.test.ts:132."""

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        assert (yield from ref.update_some_and_get(closed_to_changed)) is Active

    await run(program())


async def test_update_some_and_get_twice():
    """Ref.test.ts:138."""

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        result1 = yield from ref.update_some_and_get(active_to_changed)
        result2 = yield from ref.update_some_and_get(active_then_closed)
        assert result1 is Changed
        assert result2 is Closed

    await run(program())


async def test_modify_returns_a_result_while_storing_the_new_value():
    """Ref.test.ts:156."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        result1 = yield from ref.modify(lambda _: ("hello", update))
        result2 = yield from ref.get()
        assert result1 == "hello"
        assert result2 == update

    await run(program())


async def test_modify_some_once():
    """Ref.test.ts:164."""

    def pf(s: State) -> tuple[str, Option[State]]:
        return ("active", Some(Active)) if s is Closed else ("state does not change", None)

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        assert (yield from ref.modify_some(pf)) == "state does not change"

    await run(program())


async def test_modify_some_returns_a_result_while_applying_matching_transitions():
    """Ref.test.ts:174."""

    def first(s: State) -> tuple[str, Option[State]]:
        return ("changed", Some(Changed)) if s is Active else ("state does not change", None)

    def second(s: State) -> tuple[str, Option[State]]:
        if s is Active:
            return ("changed", Some(Changed))
        if s is Changed:
            return ("closed", Some(Closed))
        return ("state does not change", None)

    @gen
    def program():
        ref = yield from Ref[State].make(Active)
        result1 = yield from ref.modify_some(first)
        result2 = yield from ref.modify_some(second)
        assert result1 == "changed"
        assert result2 == "closed"

    await run(program())


# == source-derived: src/Ref.ts ================================================================


async def test_writes_are_visible_and_set_and_get_returns_the_stored_value():
    """Ref.ts setAndGet (`self.ref.current = value`, its value is the result) has no upstream
    test; get_and_set's stored value is not asserted upstream; get_unsafe reads the cell."""

    @gen
    def program():
        ref = yield from Ref[str].make(current)
        assert (yield from ref.set_and_get(update)) == update
        assert ref.get_unsafe() == update
        assert (yield from ref.get_and_set(current)) == update
        assert ref.get_unsafe() == current

    await run(program())


async def test_some_none_is_a_present_value_and_is_stored():
    """Ref.ts getAndUpdateSome/updateSome/updateSomeAndGet/modifySome test `_tag === "Some"`,
    not the inner value: on a cell of `int | None`, Some(None) stores None. A port that tests
    the inner value for None (or unwraps truthily) leaves 1 in place."""

    @gen
    def program():
        ref = yield from Ref[int | None].make(1)
        assert (yield from ref.get_and_update_some(lambda _: Some(None))) == 1
        assert ref.get_unsafe() is None
        yield from ref.set(1)
        yield from ref.update_some(lambda _: Some(None))
        assert ref.get_unsafe() is None
        yield from ref.set(1)
        assert (yield from ref.update_some_and_get(lambda _: Some(None))) is None
        yield from ref.set(1)
        assert (yield from ref.modify_some(lambda _: ("b", Some(None)))) == "b"
        assert ref.get_unsafe() is None

    await run(program())


class Boom(Exception):
    pass


def raising(_: int) -> Never:
    raise Boom


type RefOp = Callable[[Ref[int]], Effect[object, Never, Never]]

RAISING_OPS: list[tuple[str, RefOp]] = [
    ("get_and_update", lambda r: r.get_and_update(raising)),
    ("update", lambda r: r.update(raising)),
    ("update_and_get", lambda r: r.update_and_get(raising)),
    ("get_and_update_some", lambda r: r.get_and_update_some(raising)),
    ("update_some", lambda r: r.update_some(raising)),
    ("update_some_and_get", lambda r: r.update_some_and_get(raising)),
    ("modify", lambda r: r.modify(raising)),
    ("modify_some", lambda r: r.modify_some(raising)),
]


@pytest.mark.parametrize("op", [op for _, op in RAISING_OPS], ids=[n for n, _ in RAISING_OPS])
async def test_a_raising_function_is_a_defect_and_leaves_the_value(op: RefOp):
    """Every Ref.ts update computes `f(current)` before assigning, inside Effect.sync, whose
    throw is a defect: the cell keeps its value."""
    ref = Ref[int].make_unsafe(7)
    ex = await run(exit_(op(ref)))
    assert isinstance(ex, Failure)
    assert [type(r) for r in ex.cause.reasons] == [Die]
    assert ref.get_unsafe() == 7


async def test_operations_are_lazy_and_rerun_against_the_current_value():
    """Ref.ts wraps every operation in Effect.sync, and make in sync(makeUnsafe): building an
    effect reads nothing, and running one twice applies twice; make gives a fresh cell per run."""
    ref = Ref[int].make_unsafe(0)
    inc = ref.update_and_get(lambda n: n + 1)
    assert ref.get_unsafe() == 0
    assert await run(inc) == 1
    assert await run(inc) == 2
    make = Ref[int].make(0)
    a = await run(make)
    b = await run(make)
    assert a is not b
    assert isinstance(await run(exit_(a.set(5))), Success)
    assert b.get_unsafe() == 0
