"""Layer and MemoMap, pinned Effect v4 289a53f7, packages/effect/test/Layer.test.ts ports:
  :15  "layers can be acquired in parallel"        :35  "sharing with merge"
  :512 "memoizes composed layers by identity" (blocks 1-2; block 3 needs Layer.fresh, not asked)
  :557 "interrupting a build does not hang current or later requesters"
  :583 "an interrupted reuse does not keep the shared layer alive"
  :606 "running a get effect twice does not release the shared layer early"
  :624 "a get effect that never runs does not keep the shared layer alive"
  :638 "does not memoize a build before its Effect executes"
  :673 "memoizes layer across builds"              :693 "layers are not released early"
Semantics: MemoMapImpl, memoMapObserve and fromBuildMemo, src/Layer.ts:240-420, plus mergeAll,
provide and buildWithMemoMap.

Python names: the `Layer` namespace is read from `peffect` at call time (typed dynamic lookup), so
until it exists each test fails on the missing name and nothing else, and no stand-in MemoMap
is built here. Upstream's names become snake_case: `Layer.effect(tag, eff)`, `succeed(tag, v)`,
`effect_context`, `effect_discard`, `from_build`, `merge`, `merge_all`, `provide(self, that)`
(data-first), `build`, `build_with_memo_map`, `make_memo_map_unsafe`. `MemoMap.get` returns
`None` where upstream returns undefined. Services are the existing Tag classes, and
`Context.make(T, v)` is `T.context(v)`.

Harness adaptations, each named again where used:
- `Ref<number>` and `let n` counters become `Counter`, a plain int bumped through `sync`, so the
  bump still runs as an effect step at the point upstream's does.
- A Latch is a Deferred gate: `latch.open` is `succeed(None)`, which also yields a bool.
- :557 and :583 sweep `Scheduler.MaxOpsBeforeYield` from 3 to 64 so that an interrupt lands at
  every step, then `settle` with 200 `yieldNow`s and read `pollUnsafe()`. peffect has no
  per-fiber op budget, and fixed-yield settling is not allowed here. The sweep becomes named,
  deterministic interrupt points (before start, parked mid-build behind a gate, after
  completion). `settle` plus `pollUnsafe() !== undefined` becomes awaiting the fiber under the
  watchdog, which only passes when the fiber ended.
Gates are real Deferreds made outside the program, and `ensuring` opens each test-owned gate
even when an assertion fails. `asyncio.wait_for` only guards against hangs; gates set the order.
"""

import asyncio
from collections.abc import Callable
from typing import Any, Never, cast

import pytest

import peffect
from peffect import (
    Context,
    Deferred,
    Effect,
    Exit,
    Scope,
    ScopeService,
    Success,
    Tag,
    acquire_release,
    ensuring,
    fork,
    fork_daemon,
    gen,
    never,
    run,
    scoped,
    succeed,
    sync,
)

VOID: Exit[None, Never] = Success(None)
WATCHDOG_SECONDS = 2

type AnyLayer = Any
"""`peffect.Layer[ROut, E, RIn]` once it exists; Any until then, so this file type-checks red."""
type MemoMap = Any
"""`peffect.MemoMap` once it exists."""
type Built = Effect[Context[Any], Any, Never]


def _api(name: str) -> Any:
    return getattr(peffect, name)


def _layer(name: str) -> Callable[..., Any]:
    """`peffect.Layer.<name>`. A missing API raises AttributeError in the test that calls it."""
    return cast(Callable[..., Any], getattr(_api("Layer"), name))


def layer_effect[S](tag: type[Tag[S]], eff: Effect[S, Any, Any]) -> AnyLayer:
    return _layer("effect")(tag, eff)


def layer_succeed[S](tag: type[Tag[S]], value: S) -> AnyLayer:
    return _layer("succeed")(tag, value)


def effect_context(eff: Effect[Context[Any], Any, Any]) -> AnyLayer:
    return _layer("effect_context")(eff)


def effect_discard(eff: Effect[Any, Any, Any]) -> AnyLayer:
    return _layer("effect_discard")(eff)


def from_build(build: Callable[[MemoMap, ScopeService], Built]) -> AnyLayer:
    return _layer("from_build")(build)


def merge(self: AnyLayer, that: AnyLayer) -> AnyLayer:
    return _layer("merge")(self, that)


def merge_all(*layers: AnyLayer) -> AnyLayer:
    return _layer("merge_all")(*layers)


def provide_layer(self: AnyLayer, that: AnyLayer) -> AnyLayer:
    return _layer("provide")(self, that)


def build(layer: AnyLayer) -> Effect[Context[Any], Any, Scope]:
    return _layer("build")(layer)


def build_with_memo_map(layer: AnyLayer, memo_map: MemoMap, scope: ScopeService) -> Built:
    return _layer("build_with_memo_map")(layer, memo_map, scope)


def make_memo_map_unsafe() -> MemoMap:
    return _layer("make_memo_map_unsafe")()


def memo_get(memo_map: MemoMap, layer: AnyLayer, scope: ScopeService) -> Built | None:
    return cast(Built | None, memo_map.get(layer, scope))


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


def make_layer1(array: list[str]) -> AnyLayer:
    return layer_effect(Service1Tag, _logged(array, ACQUIRE1, RELEASE1, Service1))


def make_layer2(array: list[str]) -> AnyLayer:
    return layer_effect(Service2Tag, _logged(array, ACQUIRE2, RELEASE2, Service2))


def release_counted(released: Counter) -> AnyLayer:
    """:536 releaseCounted, with a Counter for the Ref."""
    return layer_effect(Shared, acquire_release(succeed({"n": 1}), lambda _s, _ex: released.bump()))


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
    layer1 = effect_context(never)
    layer2 = effect_context(
        acquire_release(latch.succeed(None).map(BoolTag.context), lambda _c, _ex: succeed(None))
    )
    env = build(merge(layer1, layer2))

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
    await run_guarded(scoped(build(merge(layer, layer))))
    assert array == [ACQUIRE1, RELEASE1]


# -- MemoMap ---------------------------------------------------------------------------------------


async def test_memoizes_composed_layers_by_identity():
    """:512, blocks 1-2. from_build is not memoized itself, so `builds` counts how many times the
    composed parent was walked. Block 3 (`mergeAll(provided, Layer.fresh(provided))` builds 2)
    is not ported: Layer.fresh is outside this unit."""
    builds = Counter()
    probe = from_build(lambda _m, _s: builds.bump().map(lambda _: Probe.context("x")))
    dep = layer_succeed(Dep, 1)

    provided = provide_layer(probe, dep)
    await run_guarded(scoped(build(merge_all(provided, provided))))
    assert builds.n == 1

    builds.n = 0
    merged = merge_all(probe, dep)
    await run_guarded(scoped(build(merge_all(merged, merged))))
    assert builds.n == 1


@pytest.mark.parametrize("interrupt_point", ["before-start", "parked-in-build", "after-build"])
async def test_interrupting_a_build_does_not_hang_current_or_later_requesters(interrupt_point: str):
    """:557. The op-budget sweep becomes three named interrupt points (module docstring). For
    "parked-in-build", the layer's effect opens `entered` and then parks on `release`, which only
    `ensuring` opens, standing for the sweep's mid-build budgets; the other points keep
    upstream's `Effect.succeed({ n: 1 })`. `settle` plus `pollUnsafe()` defined becomes awaiting
    `waiter` and `later` under the watchdog."""
    entered = Deferred[None].make_unsafe()
    release = Deferred[None].make_unsafe()
    value: Effect[dict[str, int], Never, Never] = (
        entered.succeed(None).flat_map(lambda _: release.await_()).map(lambda _: {"n": 1})
        if interrupt_point == "parked-in-build"
        else succeed({"n": 1})
    )
    memo_map = make_memo_map_unsafe()
    layer = layer_effect(Shared, value)

    @gen
    def program():
        first = yield from fork(
            build_with_memo_map(layer, memo_map, (yield from Scope.make())),
            start_immediately=interrupt_point != "before-start",
        )
        if interrupt_point == "parked-in-build":
            yield from entered.await_()
            assert first.exit is None  # harness guard: the interrupt must land mid-build
        waiter = yield from fork(
            build_with_memo_map(layer, memo_map, (yield from Scope.make())), start_immediately=True
        )
        yield from first.interrupt()
        later = yield from fork(
            build_with_memo_map(layer, memo_map, (yield from Scope.make())), start_immediately=True
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
    memo_map = make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from build_with_memo_map(layer, memo_map, scope1)
        if closed:
            yield from Scope.close(scope2, VOID)
        reuse = yield from fork(
            build_with_memo_map(layer, memo_map, scope2), start_immediately=start_immediately
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
    memo_map = make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from build_with_memo_map(layer, memo_map, scope1)
        reuse = memo_get(memo_map, layer, scope2)
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
    memo_map = make_memo_map_unsafe()
    layer = release_counted(released)

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from build_with_memo_map(layer, memo_map, scope1)
        assert memo_get(memo_map, layer, scope2) is not None
        yield from Scope.close(scope2, VOID)
        yield from Scope.close(scope1, VOID)
        assert released.n == 1

    await run_guarded(program())


def test_does_not_memoize_a_build_before_its_effect_executes():
    """:638. A plain `it`: the build effect is made and never run. `memo_map.map` is the same
    white-box read as upstream's `(memoMap as any).map.size`."""
    memo_map = make_memo_map_unsafe()
    scope = Scope.make_unsafe()
    layer = effect_discard(succeed(None))

    _unrun = build_with_memo_map(layer, memo_map, scope)

    assert len(memo_map.map) == 0


async def test_memoizes_layer_across_builds():
    """:673"""
    arr: list[str] = []
    layer1 = make_layer1(arr)
    layer2 = provide_layer(make_layer2(arr), layer1)
    memo_map = make_memo_map_unsafe()

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from build_with_memo_map(layer1, memo_map, scope1)
        yield from build_with_memo_map(layer2, memo_map, scope2)
        yield from Scope.close(scope2, VOID)
        yield from build_with_memo_map(layer2, memo_map, scope1)
        yield from Scope.close(scope1, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, ACQUIRE2, RELEASE2, ACQUIRE2, RELEASE2, RELEASE1]


async def test_layers_are_not_released_early():
    """:693"""
    arr: list[str] = []
    layer1 = make_layer1(arr)
    layer2 = provide_layer(make_layer2(arr), layer1)
    memo_map = make_memo_map_unsafe()

    @gen
    def program():
        scope1 = yield from Scope.make()
        scope2 = yield from Scope.make()
        yield from build_with_memo_map(layer1, memo_map, scope1)
        yield from build_with_memo_map(layer2, memo_map, scope2)
        yield from Scope.close(scope1, VOID)
        yield from Scope.close(scope2, VOID)

    await run_guarded(program())
    assert arr == [ACQUIRE1, ACQUIRE2, RELEASE2, RELEASE1]
