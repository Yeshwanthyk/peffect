"""Layer and MemoMap, pinned Effect v4 289a53f7, packages/effect/test/Layer.test.ts ports:
  :15  "layers can be acquired in parallel"        :35  "sharing with merge"
  :512 "memoizes composed layers by identity" (blocks 1-2; block 3 needs Layer.fresh, not asked)
  :557 "interrupting a build does not hang current or later requesters"
  :583 "an interrupted reuse does not keep the shared layer alive"
  :606 "running a get effect twice does not release the shared layer early"
  :624 "a get effect that never runs does not keep the shared layer alive"
  :638 "does not memoize a build before its Effect executes"
  :673 "memoizes layer across builds"              :693 "layers are not released early"
  :712 "forked memo maps reuse parent layers but isolate child layers"
Semantics: MemoMapImpl, memoMapObserve and fromBuildMemo, src/Layer.ts:240-420, plus mergeAll,
provide and buildWithMemoMap. Tests named `test_source_*` are not upstream tests: each pins one
behaviour read from that source (cited in its docstring) that no ported test reaches.

Python names: upstream's names become snake_case on the `Layer` class: `Layer.effect(tag, eff)`,
`Layer.succeed(tag, v)`, `effect_context`, `effect_discard`, `from_build`, `merge_all`, `build`,
`build_with_scope`, `build_with_memo_map`, `make_memo_map(_unsafe)`, `fork_memo_map`. The dual
`merge` and `provide` are data-last only: `self.pipe(Layer.merge(that))`, as upstream's
`self.pipe(Layer.provide(that))`. `MemoMap.get` returns `None` where upstream returns undefined.
Services are the existing Tag classes, and `Context.make(T, v)` is `T.context(v)`.

Harness adaptations, each named again where used:
- `Ref<number>` and `let n` counters become `Counter`, a plain int bumped through `sync`, so the
  bump still runs as an effect step at the point upstream's does.
- A Latch is a Deferred gate: `latch.open` is `succeed(None)`, which also yields a bool.
- :557 and :583 sweep `Scheduler.MaxOpsBeforeYield` from 3 to 64 so that an interrupt lands at
  every step, then `settle` with 200 `yieldNow`s and read `pollUnsafe()`. peffect has no
  per-fiber op budget, and fixed-yield settling is not allowed here. The sweep becomes named,
  deterministic interrupt points (before start, parked mid-build behind a gate, after
  completion). This is partial coverage: an interrupt landing between any other two steps of
  the build is not exercised. `settle` plus `pollUnsafe() !== undefined` becomes awaiting the
  fiber under the watchdog, which only passes when the fiber ended.
- :712 reads the `~effect/Layer/MemoMap` type id; here that is `isinstance(child, MemoMap)`.
Gates are real Deferreds made outside the program, and `ensuring` opens each test-owned gate
even when an assertion fails. `asyncio.wait_for` only guards against hangs; gates set the order.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Never

import pytest

from peffect import (
    CurrentMemoMap,
    Deferred,
    Effect,
    Exit,
    Layer,
    MemoMap,
    Scope,
    Success,
    Tag,
    acquire_release,
    ensuring,
    fork,
    fork_daemon,
    gen,
    never,
    provide_layer,
    run,
    scoped,
    succeed,
    sync,
)
from peffect.core import WithFiber

VOID: Exit[None, Never] = Success(None)
WATCHDOG_SECONDS = 2


# -- fixtures (Layer.test.ts:815-872 and :534-540) ------------------------------------------------

ACQUIRE1 = "Acquiring Module 1"
ACQUIRE2 = "Acquiring Module 2"
RELEASE1 = "Releasing Module 1"
RELEASE2 = "Releasing Module 2"


class Service1:
    def one(self) -> Effect[int, Never, Never]:
        return succeed(1)


class Service2:
    def two(self) -> Effect[int, Never, Never]:
        return succeed(2)


class Service1Tag(Tag[Service1]): ...


class Service2Tag(Tag[Service2]): ...


class BoolTag(Tag[bool]): ...


class Probe(Tag[str]): ...


class Dep(Tag[int]): ...


class Shared(Tag[dict[str, int]]): ...


class Counter:
    """Harness substitution for upstream's `Ref<number>` and `let` counters."""

    def __init__(self) -> None:
        self.n = 0

    def _inc(self) -> None:
        self.n += 1

    def bump(self) -> Effect[None, Never, Never]:
        return sync(self._inc)


def _logged[S](
    array: list[str], acquire: str, release: str, make: Callable[[], S]
) -> Effect[S, Never, Scope]:
    def push() -> S:
        array.append(acquire)
        return make()

    return acquire_release(sync(push), lambda _s, _ex: sync(lambda: array.append(release)))


def make_layer1(array: list[str]) -> Layer[Service1Tag]:
    return Layer.effect(Service1Tag, _logged(array, ACQUIRE1, RELEASE1, Service1))


def make_layer2(array: list[str]) -> Layer[Service2Tag]:
    return Layer.effect(Service2Tag, _logged(array, ACQUIRE2, RELEASE2, Service2))


def release_counted(released: Counter) -> Layer[Shared]:
    """:536 releaseCounted, with a Counter for the Ref."""
    return Layer.effect(Shared, acquire_release(succeed({"n": 1}), lambda _s, _ex: released.bump()))


def open_all(*gates: Deferred[None]) -> Effect[None, Never, Never]:
    eff: Effect[Any, Never, Never] = sync(lambda: None)
    for gate in gates:
        eff = eff.flat_map(lambda _, g=gate: g.succeed(None))
    return eff.as_(None)


async def run_guarded[A](eff: Effect[A, Any, Never], *gates: Deferred[None]) -> A:
    return await asyncio.wait_for(run(eff.pipe(ensuring(open_all(*gates)))), WATCHDOG_SECONDS)


# -- Layer -----------------------------------------------------------------------------------------


async def test_layers_can_be_acquired_in_parallel():
    """:15. The Latch is a Deferred gate. layer2 opens it only if it is built while layer1 is
    still parked in `never`, that is, if merge acquires in parallel."""
    latch = Deferred[None].make_unsafe()
    layer1: Layer[Never] = Layer.effect_context(never)  # upstream infers Layer<never>
    layer2 = Layer.effect_context(
        acquire_release(latch.succeed(None).map(BoolTag.context), lambda _c, _ex: succeed(None))
    )
    env = Layer.build(layer1.pipe(Layer.merge(layer2)))

    @gen
    def program():
        fiber = yield from fork_daemon(scoped(env))
        yield from latch.await_()
        result = yield from fiber.interrupt()
        assert result is None

    await run_guarded(program(), latch)


async def test_sharing_with_merge():
    """:35"""
    array: list[str] = []
    layer = make_layer1(array)
    await run_guarded(scoped(Layer.build(layer.pipe(Layer.merge(layer)))))
    assert array == [ACQUIRE1, RELEASE1]


# -- MemoMap ---------------------------------------------------------------------------------------


async def test_memoizes_composed_layers_by_identity():
    """:512, blocks 1-2. from_build is not memoized itself, so `builds` counts how many times the
    composed parent was walked. Block 3 (`mergeAll(provided, Layer.fresh(provided))` builds 2)
    is not ported: Layer.fresh is outside this unit."""
    builds = Counter()
    probe = Layer.from_build(lambda _m, _s: builds.bump().map(lambda _: Probe.context("x")))
    dep = Layer.succeed(Dep, 1)

    provided = probe.pipe(Layer.provide(dep))
    await run_guarded(scoped(Layer.build(Layer.merge_all(provided, provided))))
    assert builds.n == 1

    builds.n = 0
    merged = Layer.merge_all(probe, dep)
    await run_guarded(scoped(Layer.build(Layer.merge_all(merged, merged))))
    assert builds.n == 1


@pytest.mark.parametrize("interrupt_point", ["before-start", "parked-in-build", "after-build"])
async def test_interrupting_a_build_does_not_hang_current_or_later_requesters(interrupt_point: str):
    """:557. The op-budget sweep becomes three named interrupt points (module docstring). For
    "parked-in-build", the first build attempt opens `entered` and then parks on `release`, which
    only `ensuring` opens, standing for the sweep's mid-build budgets. Every other attempt, and
    every attempt at the other points, is upstream's `Effect.succeed({ n: 1 })`: once the first
    requester and the waiter fail, closing their layer scopes drops the entry (Layer.ts:360-372,
    :245-266), so `later` builds again, and upstream's rebuild does not park. `settle` plus
    `pollUnsafe()` defined becomes awaiting `waiter` and `later` under the watchdog."""
    entered = Deferred[None].make_unsafe()
    release = Deferred[None].make_unsafe()
    attempts = Counter()

    def parks_first_attempt(_: None) -> Effect[None, Never, Never]:
        if interrupt_point == "parked-in-build" and attempts.n == 1:
            return entered.succeed(None).flat_map(lambda _: release.await_())
        return succeed(None)

    value = attempts.bump().flat_map(parks_first_attempt).as_({"n": 1})
    memo_map = Layer.make_memo_map_unsafe()
    layer = Layer.effect(Shared, value)

    @gen
    def program():
        first = yield from fork(
            Layer.build_with_memo_map(layer, memo_map, (yield from Scope.make())),
            start_immediately=interrupt_point != "before-start",
        )
        if interrupt_point == "parked-in-build":
            yield from entered.await_()
            assert first.exit is None  # harness guard: the interrupt must land mid-build
        waiter = yield from fork(
            Layer.build_with_memo_map(layer, memo_map, (yield from Scope.make())),
            start_immediately=True,
        )
        yield from first.interrupt()
        later = yield from fork(
            Layer.build_with_memo_map(layer, memo_map, (yield from Scope.make())),
            start_immediately=True,
        )
        yield from waiter.await_()  # waiter hung
        yield from later.await_()  # later requester hung

    await run_guarded(program(), entered, release)


@pytest.mark.parametrize("start_immediately", [False, True], ids=["before-start", "immediate"])
@pytest.mark.parametrize("closed", [False, True], ids=["open", "closed"])
async def test_an_interrupted_reuse_does_not_keep_the_shared_layer_alive(
    closed: bool, start_immediately: bool
):
    """:583. The reuse path never parks once the layer is built, so in peffect an immediate
    start runs it to the end before `fork` returns. The sweep becomes two points: interrupted
    before it starts, or after it ran to completion (module docstring)."""
    released = Counter()
    memo_map = Layer.make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer, memo_map, scope1)
        if closed:
            yield from Scope.close(scope2, VOID)
        reuse = yield from fork(
            Layer.build_with_memo_map(layer, memo_map, scope2), start_immediately=start_immediately
        )
        yield from reuse.interrupt()
        if not closed:
            yield from Scope.close(scope2, VOID)
        yield from Scope.close(scope1, VOID)
        assert released.n == 1, f"closed scope: {closed}, start_immediately: {start_immediately}"

    await run_guarded(program())


async def test_running_a_get_effect_twice_does_not_release_the_shared_layer_early():
    """:606"""
    released = Counter()
    memo_map = Layer.make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer, memo_map, scope1)
        reuse = memo_map.get(layer, scope2)
        assert reuse is not None
        yield from reuse
        yield from reuse
        yield from Scope.close(scope2, VOID)
        assert released.n == 0
        yield from Scope.close(scope1, VOID)
        assert released.n == 1

    await run_guarded(program())


async def test_a_get_effect_that_never_runs_does_not_keep_the_shared_layer_alive():
    """:624"""
    released = Counter()
    memo_map = Layer.make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer, memo_map, scope1)
        assert memo_map.get(layer, scope2) is not None
        yield from Scope.close(scope2, VOID)
        yield from Scope.close(scope1, VOID)
        assert released.n == 1

    await run_guarded(program())


def test_does_not_memoize_a_build_before_its_effect_executes():
    """:638. A plain `it`: the build effect is made and never run. `memo_map.map` is the same
    white-box read as upstream's `(memoMap as any).map.size`."""
    memo_map = Layer.make_memo_map_unsafe()
    scope = Scope.make_unsafe()
    layer = Layer.effect_discard(succeed(None))

    _unrun = Layer.build_with_memo_map(layer, memo_map, scope)

    assert len(memo_map.map) == 0


async def test_memoizes_layer_across_builds():
    """:673"""
    arr: list[str] = []
    layer1 = make_layer1(arr)
    layer2 = make_layer2(arr).pipe(Layer.provide(layer1))
    memo_map = Layer.make_memo_map_unsafe()

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer1, memo_map, scope1)
        yield from Layer.build_with_memo_map(layer2, memo_map, scope2)
        yield from Scope.close(scope2, VOID)
        yield from Layer.build_with_memo_map(layer2, memo_map, scope1)
        yield from Scope.close(scope1, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, ACQUIRE2, RELEASE2, ACQUIRE2, RELEASE2, RELEASE1]


async def test_layers_are_not_released_early():
    """:693"""
    arr: list[str] = []
    layer1 = make_layer1(arr)
    layer2 = make_layer2(arr).pipe(Layer.provide(layer1))
    memo_map = Layer.make_memo_map_unsafe()

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer1, memo_map, scope1)
        yield from Layer.build_with_memo_map(layer2, memo_map, scope2)
        yield from Scope.close(scope1, VOID)
        yield from Scope.close(scope2, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, ACQUIRE2, RELEASE2, RELEASE1]


async def test_forked_memo_maps_reuse_parent_layers_but_isolate_child_layers():
    """:712. The type-id read becomes `isinstance` (module docstring)."""
    arr: list[str] = []
    parent_layer = make_layer1(arr)
    child_layer = make_layer2(arr)

    @gen
    def program():
        parent = yield from Layer.make_memo_map()
        child = yield from Layer.fork_memo_map(parent)
        assert isinstance(child, MemoMap)
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(parent_layer, parent, scope1)
        yield from Layer.build_with_memo_map(parent_layer, child, scope2)
        yield from Layer.build_with_memo_map(child_layer, child, scope2)
        yield from Layer.build_with_memo_map(child_layer, parent, scope1)
        yield from Scope.close(scope2, VOID)
        yield from Scope.close(scope1, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, ACQUIRE2, ACQUIRE2, RELEASE2, RELEASE2, RELEASE1]


# -- source behaviour no upstream test reaches -----------------------------------------------------


def counted_resource(acquired: Counter, released: Counter) -> Layer[Shared]:
    return Layer.effect(
        Shared,
        acquire_release(acquired.bump().as_({"n": 1}), lambda _s, _ex: released.bump()),
    )


async def test_source_a_build_on_a_closed_scope_is_unshared_and_released_at_once():
    """Layer.ts:268-275 memoMapObserve returns false for a closed scope, so getOrElseMemoize
    (:404-418) builds with no entry: nothing is stored for later requesters and no observer is
    counted. fromBuild (:360-372) forks the layer scope from the closed scope, so it starts
    closed and the resource's finalizer runs as soon as it is added."""
    acquired = Counter()
    released = Counter()
    memo_map = Layer.make_memo_map_unsafe()
    layer = counted_resource(acquired, released)

    @gen
    def program():
        closed = yield from Scope.make()
        yield from Scope.close(closed, VOID)
        ctx = yield from Layer.build_with_memo_map(layer, memo_map, closed)
        assert ctx.services[Shared] == {"n": 1}
        assert (acquired.n, released.n, len(memo_map.map)) == (1, 1, 0)

        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer, memo_map, scope1)
        yield from Layer.build_with_memo_map(layer, memo_map, scope2)
        assert (acquired.n, released.n, len(memo_map.map)) == (2, 1, 1)
        yield from Scope.close(scope1, VOID)
        assert released.n == 1  # scope2 still observes the entry the closed build never made
        yield from Scope.close(scope2, VOID)
        assert (released.n, len(memo_map.map)) == (2, 0)

    await run_guarded(program())


async def test_source_get_takes_ownership_when_called_not_when_run():
    """Layer.ts:404-409 MemoMapImpl.get calls memoMapObserve (:268-275) before it returns the
    effect: the caller's scope holds the entry from the call, even if the effect never runs.
    Closing the building scope first therefore releases nothing."""
    released = Counter()
    memo_map = Layer.make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from Layer.build_with_memo_map(layer, memo_map, scope1)
        assert memo_map.get(layer, scope2) is not None  # never run
        yield from Scope.close(scope1, VOID)
        assert released.n == 0
        yield from Scope.close(scope2, VOID)
        assert released.n == 1

    await run_guarded(program())


async def test_source_a_nested_build_forks_the_current_memo_map():
    """Layer.ts:670-680 buildWithMemoMap provides CurrentMemoMap while it builds and adds it to
    the built context; :781 buildWithScope forks the current one (:600-604 forkOrCreate). A layer
    that builds another inside its own build therefore reuses the outer map's entry."""
    arr: list[str] = []
    shared = make_layer1(arr)
    nested = Layer.from_build(lambda _m, s: Layer.build_with_scope(shared, s))
    memo_map = Layer.make_memo_map_unsafe()

    @gen
    def program():
        scope = yield from Scope.make()
        yield from Layer.build_with_memo_map(shared, memo_map, scope)
        ctx = yield from Layer.build_with_memo_map(nested, memo_map, scope)
        assert ctx.services[CurrentMemoMap] is memo_map
        yield from Scope.close(scope, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, RELEASE1]


async def test_source_without_a_current_memo_map_each_build_gets_a_fresh_one():
    """Layer.ts:600-604 forkOrCreate makes a new map when the context has none: two top-level
    builds share nothing."""
    arr: list[str] = []
    layer = make_layer1(arr)

    @gen
    def program():
        scope = yield from Scope.make()
        yield from Layer.build_with_scope(layer, scope)
        yield from Layer.build_with_scope(layer, scope)
        yield from Scope.close(scope, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, ACQUIRE1, RELEASE1, RELEASE1]


async def test_source_provide_layer_scopes_the_layer_to_the_effect():
    """internal/layer.ts:8-22 provideLayer with effect.ts:4231 scopedWith: a fresh scope that
    is not put in the context, closed with the effect's exit when it ends, and the built
    context (with CurrentMemoMap, so nested builds reuse the map) provided to the effect."""
    arr: list[str] = []
    layer = make_layer1(arr)
    seen: list[bool] = []

    def inside(_s: Service1) -> Effect[None, Never, Never]:
        def look(fiber: Any) -> Effect[None, Never, Never]:
            seen.append(Scope in fiber.context)
            return succeed(None)

        return WithFiber(look).flat_map(
            lambda _: scoped(Layer.build(layer)).as_(None)  # reuses through CurrentMemoMap
        )

    @gen
    def program():
        yield from provide_layer(layer)(Service1Tag().use().flat_map(inside))
        arr.append("after")

    await run_guarded(program())
    assert seen == [False]
    assert arr == [ACQUIRE1, RELEASE1, "after"]
