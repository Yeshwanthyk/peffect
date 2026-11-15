"""Batch A: Effect v4's Context tests, ported. Upstream is pinned at vendor/effect-v4 commit
289a53f (Effect v4.1.4). Each test names its upstream file:line and test title; the mapping,
the departures and the tests not ported are in docs/UPSTREAM_TESTS.md.

Adaptations used throughout:
- A service is a tag class with upstream's key string (`Context.Service<number>("ContextTest/A")`
  becomes `class A(Tag[int], key="ContextTest/A")`). Upstream's `[...context.mapUnsafe]`
  (key, value) pairs become `list(ctx.services.items())`, keyed by `A.key`.
- `Context.make(A, 1)` is `A.context(1)`; `Context.add(ctx, B, 2)` and `Context.merge` are
  `ctx | B.context(2)`.
- `Context.empty()` is `Context.empty()`.
"""

from typing import Any

from peffect import (
    Context,
    ContextMap,
    Failure,
    MissingService,
    Reference,
    Tag,
    on_exit,
    provide,
    run,
    run_exit,
    sync,
    update_context,
)
from peffect.core import AnyFiber, WithFiber


class A(Tag[int], key="ContextTest/A"): ...


class B(Tag[int], key="ContextTest/B"): ...


class C(Tag[int], key="ContextTest/C"): ...


# -- Context.test.ts ------------------------------------------------------------------------------


def test_keeps_the_source_immutable_across_additions():
    """Context.test.ts:17 "keeps the source immutable across additions"."""
    source = A.context(1)
    result = source | B.context(2)

    assert list(source.services.items()) == [(A.key, 1)]
    assert list(result.services.items()) == [(A.key, 1), (B.key, 2)]


def test_preserves_insertion_order_for_replacements_and_appends():
    """Context.test.ts:31 "preserves Map insertion order for replacements and appends"."""

    class Ref(Reference[int], key="ContextTest/OrderRef"):
        @classmethod
        def default(cls) -> int:
            return 0

    context = (
        Context.empty() | A.context(1) | Ref.context(2) | B.context(3) | A.context(4) | C.context(5)
    )

    assert list(context.services.items()) == [(A.key, 4), (Ref.key, 2), (B.key, 3), (C.key, 5)]


async def test_distinguishes_an_undefined_service_from_an_absent_service():
    """Context.test.ts:104 "distinguishes an undefined service from an absent service".

    JS `undefined` is Python `None`. Upstream's pure getters (getOption, getUnsafe, getOrElse,
    get) have no peffect equivalent; membership in `services` and a lookup inside `provide`
    say the same things. The `Context.pick` lines are not ported (no pick API)."""

    class Undefined(Tag[int | None], key="ContextTest/Undefined"): ...

    class Missing(Tag[None], key="ContextTest/Missing"): ...

    class Ref(Reference[str | None], key="ContextTest/UndefinedRef"):
        @classmethod
        def default(cls) -> str | None:
            return "default"

    context = Undefined.context(1) | Undefined.context(None) | Ref.context(None)

    assert Undefined.key in context.services  # getOption(Undefined) is Some
    assert Missing.key not in context.services  # getOption(Missing) is None
    assert context.services[Undefined.key] is None  # getUnsafe / getOrElse give undefined
    assert Ref.key in context.services and context.services[Ref.key] is None  # Some(undefined)
    assert await run(provide(context)(Undefined().use())) is None
    assert await run(provide(context)(Ref().use())) is None  # get(Ref): not the default
    missing = await run_exit(
        provide(Missing.context(None))(update_context(Missing().use(), lambda _: {}))
    )
    assert isinstance(missing, Failure)
    assert isinstance(missing.cause.defects[0], MissingService)


def test_supports_the_readonly_map_surface_through_services():
    """Context.test.ts:278 "supports the ReadonlyMap surface through mapUnsafe"."""
    context = A.context(1) | B.context(2)
    visited = [(key, value) for key, value in context.services.items()]  # forEach

    assert len(context.services) == 2
    assert A.key in context.services
    assert context.services.get(B.key) == 2
    assert list(context.services.keys()) == [A.key, B.key]
    assert list(context.services.values()) == [1, 2]
    assert list(context.services.items()) == [(A.key, 1), (B.key, 2)]
    assert visited == [(A.key, 1), (B.key, 2)]


def test_supports_equality():
    """Context.test.ts:292 "supports equality and JSON materialization". Equality only: JSON
    (`toJSON`) has no peffect counterpart.

    Upstream's `makeUnsafe(new Map(...))` builds `right` from a raw map; peffect has no raw
    constructor, so `right` is built separately in the opposite order. The last three asserts
    pin upstream's definition (Context.ts:637-649): equal when both are Contexts with the same
    size, keys and equal values, regardless of order, and the hash is the size. A plain
    mapping with the same items is not a Context, so it is not equal."""
    left = A.context(1) | B.context(2)
    right = B.context(2) | A.context(1)

    assert left == right
    assert left != A.context(2)
    assert hash(left) == hash(right)
    assert left != {A.key: 1, B.key: 2}
    assert left != left.services


def test_merges_with_right_bias_and_preserves_empty_operand_identity():
    """Context.test.ts:304 "merges with right bias and preserves empty operand identity"."""
    left = A.context(1) | B.context(2)
    right = A.context(3) | C.context(4)
    merged = left | right

    assert (Context.empty() | left) is left
    assert (left | Context.empty()) is left
    assert list(merged.services.items()) == [(A.key, 3), (B.key, 2), (C.key, 4)]


async def test_resolves_reference_defaults_lazily_and_caches_them():
    """Context.test.ts:332 "resolves reference defaults lazily and caches them".

    Upstream reads the default with the pure `Context.get(Context.empty(), Ref)`; here the
    lookup is `Ref().use()` with nothing provided. The `pick` line is not ported."""
    calls: list[int] = []

    class Ref(Reference[object], key="ContextTest/LazyRef"):
        @classmethod
        def default(cls) -> object:
            calls.append(1)
            return {}

    assert calls == []
    first = await run(Ref().use())
    assert calls == [1]
    assert await run(Ref().use()) is first
    assert calls == [1]


# -- Effect.test.ts -------------------------------------------------------------------------------


async def test_context_service():
    """Effect.test.ts:405 "Context.Service"."""

    class ATag(Tag[str], key="ATag"): ...

    seen: list[str] = []
    eff = ATag().use().flat_map(lambda a: sync(lambda: seen.append(a)))
    await run(provide(ATag.context("A"))(eff))
    assert seen == ["A"]


async def test_provide_service_restores_the_original_context_if_interrupted_before_its_body():
    """Effect.test.ts:131 "provideService restores the original context if interrupted before
    its body".

    Upstream forces the interrupt with a custom Scheduler whose `shouldYield` fires once the
    fiber's context holds Value = 2. peffect has no scheduler hook, and `provide` is
    `update_context` with a merge, so the interrupt is requested inside that merge, at the same
    point: the new context is built and about to be installed, the body hasn't run. The
    assertion is upstream's: the finalizer outside sees the *same* context object as before."""

    class Value(Tag[int], key="InterruptedValue"): ...

    me: list[AnyFiber] = []
    original: list[ContextMap] = []
    observed: list[ContextMap] = []
    ran: list[str] = []

    def merge_then_interrupt(old: ContextMap) -> ContextMap:
        new = {**old, Value.key: 2}
        me[0].interrupt_unsafe()
        return new

    def grab(f: AnyFiber) -> Any:
        me.append(f)
        original.append(f.context)
        return update_context(sync(lambda: ran.append("body")), merge_then_interrupt)

    def observe(_: object) -> Any:
        return WithFiber(lambda f: sync(lambda: observed.append(f.context)))

    child = WithFiber(grab).pipe(on_exit(observe))
    ex = await run_exit(provide(Value.context(1))(child))

    assert isinstance(ex, Failure) and ex.cause.is_interrupted  # the interrupt landed
    assert ran == []
    assert observed[0] is original[0]


def test_equality_handles_nan_and_cycles():
    """Equal.ts compareBoth/compareObjects: NaN equality and repeated pairs close cycles."""

    class Value(Tag[object], key="ContextTest/Value"): ...

    assert Value.context(float("nan")) == Value.context(float("nan"))
    left: list[object] = []
    right: list[object] = []
    left.append(left)
    right.append(right)
    assert Value.context(left) == Value.context(right)
    right.append(1)
    assert Value.context(left) != Value.context(right)


def test_equality_checks_data_beyond_a_cycle():
    """Equal.ts compareOnPath: a repeated pair only discharges that recursive comparison."""

    class Value(Tag[object], key="ContextTest/Value"): ...

    left: dict[str, object] = {}
    right: dict[str, object] = {}
    left.update(loop=left, value=[float("nan"), 1])
    right.update(loop=right, value=[float("nan"), 1])
    assert Value.context(left) == Value.context(right)
    right["value"] = [float("nan"), 2]
    assert Value.context(left) != Value.context(right)
