"""Service identity is an explicit string key, as in Effect v4 (`Context.Service<A>("app/A")`).

Upstream is pinned at vendor/effect-v4 commit 289a53f. A tag declares its key in the class
statement, `class Users(Tag[Repo], key="app/Users")`; there is no generated default. Contexts and
the fiber's context map are keyed by that string (Context.ts:546 `lookup(self, key: string)`).

Labels: "Direct port" names the Context.test.ts test; "Source-derived" names the Context.ts lines
that fix the behavior. Every tag is declared inside its test so one red declaration can't stop
collection of the others.
"""

import types
from typing import Any

import pytest

from peffect import (
    Clock,
    Context,
    ContextMap,
    CurrentMemoMap,
    Failure,
    Layer,
    MissingService,
    Reference,
    Scope,
    Tag,
    provide,
    run,
    run_exit,
    scoped,
    succeed,
    update_context,
)
from peffect.core import EMPTY_CONTEXT, AnyFiber, WithFiber


def _abc() -> tuple[type[Tag[int]], type[Tag[int]], type[Tag[int]]]:
    class A(Tag[int], key="ContextTest/A"): ...

    class B(Tag[int], key="ContextTest/B"): ...

    class C(Tag[int], key="ContextTest/C"): ...

    return A, B, C


# -- Direct ports: Context.test.ts, keys now strings --------------------------------------------


def test_keeps_the_source_immutable_across_additions():
    """Direct port, Context.test.ts:17."""
    A, B, _ = _abc()
    source = A.context(1)
    result = source | B.context(2)
    assert list(source.services.items()) == [("ContextTest/A", 1)]
    assert list(result.services.items()) == [("ContextTest/A", 1), ("ContextTest/B", 2)]


def test_preserves_insertion_order_for_replacements_and_appends():
    """Direct port, Context.test.ts:31."""
    A, B, C = _abc()

    class Ref(Reference[int], key="ContextTest/OrderRef"):
        @classmethod
        def default(cls) -> int:
            return 0

    ctx = (
        Context.empty() | A.context(1) | Ref.context(2) | B.context(3) | A.context(4) | C.context(5)
    )
    assert list(ctx.services.items()) == [
        ("ContextTest/A", 4),
        ("ContextTest/OrderRef", 2),
        ("ContextTest/B", 3),
        ("ContextTest/C", 5),
    ]


def test_distinguishes_an_undefined_service_from_an_absent_service():
    """Direct port, Context.test.ts:104; pure getters become `services` membership."""

    class Undefined(Tag[int | None], key="ContextTest/Undefined"): ...

    ctx = Undefined.context(1) | Undefined.context(None)
    assert list(ctx.services.items()) == [("ContextTest/Undefined", None)]
    assert "ContextTest/Missing" not in ctx.services


def test_supports_the_readonly_map_surface_through_services():
    """Direct port, Context.test.ts:278."""
    A, B, _ = _abc()
    ctx = A.context(1) | B.context(2)
    assert "ContextTest/A" in ctx.services and A not in ctx.services
    assert list(ctx.services.values()) == [1, 2]
    assert list(ctx.services.keys()) == ["ContextTest/A", "ContextTest/B"]


def test_supports_equality():
    """Direct port, Context.test.ts:292 (equality only; no toJSON)."""
    A, B, _ = _abc()
    left = A.context(1) | B.context(2)
    assert left == B.context(2) | A.context(1)
    assert left != A.context(2)


def test_merges_with_right_bias_and_preserves_empty_operand_identity():
    """Direct port, Context.test.ts:304."""
    A, B, C = _abc()
    left = A.context(1) | B.context(2)
    merged = left | (A.context(3) | C.context(4))
    assert (Context.empty() | left) is left
    assert (left | Context.empty()) is left
    assert list(merged.services.items()) == [
        ("ContextTest/A", 3),
        ("ContextTest/B", 2),
        ("ContextTest/C", 4),
    ]


async def test_resolves_reference_defaults_lazily_and_caches_them():
    """Direct port, Context.test.ts:332."""
    calls: list[int] = []

    class Ref(Reference[object], key="ContextTest/LazyRef"):
        @classmethod
        def default(cls) -> object:
            calls.append(1)
            return {}

    assert calls == []
    first = await run(Ref().use())
    assert await run(Ref().use()) is first
    assert calls == [1]


# -- Source-derived: the key string is the identity ---------------------------------------------


async def test_distinct_declarations_with_one_key_are_one_service():
    """Source-derived, Context.ts:546-552 lookup and 1192-1200 merge: a second declaration with
    the same key reads, replaces in place, and compares equal to the first."""

    class First(Tag[str], key="ContextTest/Same"): ...

    class Second(Tag[str], key="ContextTest/Same"): ...

    class Other(Tag[str], key="ContextTest/Other"): ...

    ctx = First.context("first") | Other.context("other") | Second.context("second")
    assert list(ctx.services.items()) == [
        ("ContextTest/Same", "second"),
        ("ContextTest/Other", "other"),
    ]
    assert await run(provide(First.context("f") | Second.context("s"))(First().use())) == "s"
    assert await run(provide(ctx)(First().use())) == "second"
    assert First.context("x") == Second.context("x")


async def test_reference_defaults_are_per_declaration_despite_one_key():
    """Source-derived, Context.ts:1089-1096: the default cache lives on the Reference object, so
    two declarations sharing a key keep their own defaults until one is provided."""

    class One(Reference[str], key="ContextTest/SharedRef"):
        @classmethod
        def default(cls) -> str:
            return "one"

    class Two(Reference[str], key="ContextTest/SharedRef"):
        @classmethod
        def default(cls) -> str:
            return "two"

    assert await run(One().use()) == "one"
    assert await run(Two().use()) == "two"
    assert await run(provide(One.context("given"))(Two().use())) == "given"


async def test_missing_service_reports_its_key():
    """Source-derived, Context.ts:1098-1101 serviceNotFoundError: "Service not found: <key>"."""

    class Missing(Tag[int], key="ContextTest/MissingKey"): ...

    def drop(_: ContextMap) -> ContextMap:
        return EMPTY_CONTEXT

    ex = await run_exit(provide(Missing.context(0))(update_context(Missing().use(), drop)))
    assert isinstance(ex, Failure)
    (defect,) = ex.cause.defects
    assert isinstance(defect, MissingService) and defect.tag is Missing
    assert str(defect) == "Service not found: ContextTest/MissingKey"


def test_a_tag_must_declare_a_string_key():
    """Platform adaptation: TS requires the key argument; here the class statement must give a
    str. Built with types.new_class so the check is the runtime one, not pyright's."""
    for base in (Tag[int], Reference[int]):
        with pytest.raises(TypeError):
            types.new_class("NoKey", (base,))
        with pytest.raises(TypeError):
            types.new_class("IntKey", (base,), {"key": 1})


def test_static_tag_class_rules_hold_with_keys():
    """Platform adaptation, kept from tests/test_context.py: a concrete tag can't be subclassed,
    even under a new key, and the roots can't build a Context."""

    class Users(Tag[str], key="ContextTest/Users"): ...

    with pytest.raises(TypeError):
        types.new_class("Admins", (Users,), {"key": "ContextTest/Admins"})

    with pytest.raises(TypeError):
        Tag[int].context(1)


# -- Source-derived: internal keys are upstream's strings ---------------------------------------


async def test_internal_services_use_upstream_keys():
    """Source-derived: internal/effect.ts:4030 "effect/Scope", :6395 "effect/Clock", Layer.ts:600
    "effect/Layer/CurrentMemoMap". The fiber's raw context is keyed by those strings."""
    clock: Any = object()
    memo_map = Layer.make_memo_map_unsafe()
    assert list(Clock.context(clock).services) == ["effect/Clock"]
    assert list(CurrentMemoMap.context(memo_map).services) == ["effect/Layer/CurrentMemoMap"]

    def raw(fiber: AnyFiber) -> Any:
        return succeed((fiber.context.get("effect/Scope"), Scope in fiber.context))

    found, by_class = await run(scoped(WithFiber(raw)))
    assert found is not None and not by_class
    assert list(Scope.context(found).services) == ["effect/Scope"]


def test_missing_service_with_empty_key_has_no_suffix():
    """Source-derived: Context.ts serviceNotFoundError omits an empty key."""

    class Empty(Tag[int], key=""): ...

    defect = MissingService(Empty)
    assert str(defect) == "Service not found"
    assert defect.tag is Empty
