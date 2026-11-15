"""Context: tags, references, provide, and the kernel's update_context. Rows C1-C8 in
docs/INVARIANTS.md, "Context". White-box where a test needs the raw fiber context."""

import types
from collections.abc import Callable
from typing import Any, Never, cast

import pytest

from peffect import (
    Context,
    ContextMap,
    Failure,
    Fiber,
    MissingService,
    Reference,
    Tag,
    catch_all,
    catch_cause,
    die,
    ensuring,
    fail,
    fork,
    gen,
    never,
    provide,
    run,
    run_exit,
    succeed,
    sync,
    update_context,
    yield_now,
)
from peffect.core import EMPTY_CONTEXT, AnyFiber, WithFiber


class Users(Tag[str], key="test_context/Users"): ...


class Log(Tag[str], key="test_context/Log"): ...


class Boom(Exception):
    pass


def raw() -> WithFiber[ContextMap, Never, Never]:
    """The fiber's context as it is right now."""
    return WithFiber(lambda f: succeed(f.context))


def users_now() -> WithFiber[object, Never, Never]:
    return WithFiber(lambda f: succeed(f.context.get(Users.key)))


# -- C1: restored on every exit ---------------------------------------------------------------


async def test_restored_after_success_failure_and_defect():
    seen: list[object] = []
    record = users_now().flat_map(lambda u: sync(lambda: seen.append(u)))
    inner = provide(Users.context("inner"))
    body = Users().use().flat_map(lambda u: sync(lambda: seen.append(u)))

    @gen
    def prog():
        yield from record
        yield from inner(body)
        yield from record
        yield from inner(body.flat_map(lambda _: fail(Boom()))).pipe(
            catch_all(lambda _: succeed(0))
        )
        yield from record
        yield from inner(die(Boom())).pipe(catch_cause(lambda _: succeed(0)))
        yield from record

    await run(provide(Users.context("outer"))(prog()))
    assert seen == ["outer", "inner", "outer", "inner", "outer", "outer"]


async def test_nested_provide_layers_and_unwinds():
    seen: list[tuple[object, object]] = []

    def record(_: object = None):
        return raw().flat_map(
            lambda c: sync(lambda: seen.append((c.get(Users.key), c.get(Log.key))))
        )

    @gen
    def prog():
        yield from record()
        yield from provide(Users.context("b") | Log.context("x"))(record())
        yield from record()

    await run(provide(Users.context("a"))(prog()).flat_map(record))
    assert seen == [("a", None), ("b", "x"), ("a", None), (None, None)]


async def test_restored_when_interrupted_while_parked_inside():
    seen: list[object] = []
    body = users_now().flat_map(lambda u: sync(lambda: seen.append(u))).flat_map(lambda _: never)
    after = users_now().flat_map(lambda u: sync(lambda: seen.append(("finalizer", u))))

    @gen
    def prog():
        child = yield from fork(provide(Users.context("inner"))(body).pipe(ensuring(after)))
        yield from yield_now  # let the child install its context and park
        yield from child.interrupt()

    await run(provide(Users.context("outer"))(prog()))
    # the body ran with the new context installed; the finalizer outside saw it put back
    assert seen == ["inner", ("finalizer", "outer")]


# -- C2: no interrupt window between install and restore frame ---------------------------------


async def test_interrupt_at_install_still_restores(monkeypatch: pytest.MonkeyPatch):
    """The interrupt is requested inside the install step itself, so it lands at the very next
    loop turn, before the body. Without the restore frame the installed context would leak to the
    finalizer (drop the frame push in update_context and this test goes red)."""
    me: list[AnyFiber] = []
    installed: list[object] = []
    swap = Fiber[Any, Any].swap_context

    def observe_swap(fiber: AnyFiber, context: ContextMap) -> ContextMap:
        old = swap(fiber, context)
        if fiber.interrupted:
            installed.append(fiber.context.get(Users.key))
        return old

    monkeypatch.setattr(Fiber, "swap_context", observe_swap)
    ran: list[str] = []
    after: list[object] = []

    def install(old: ContextMap) -> ContextMap:
        new = {**old, Users.key: "inner"}
        me[0].interrupt_unsafe()  # running, so this is deferred to the next loop top
        return new

    grab = WithFiber(lambda f: sync(lambda: me.append(f)))
    body = update_context(sync(lambda: ran.append("body")), install)
    final = users_now().flat_map(lambda u: sync(lambda: after.append(u)))
    prog = grab.flat_map(lambda _: body).pipe(ensuring(final))
    ex = await run_exit(provide(Users.context("outer"))(prog))

    assert isinstance(ex, Failure) and ex.cause.is_interrupted
    assert installed[0] == "inner" and ran == []
    assert after == ["outer"]


async def test_raising_update_changes_nothing():
    def bad(_: ContextMap) -> ContextMap:
        raise Boom()

    ran: list[str] = []
    seen: list[object] = []
    attempt = update_context(sync(lambda: ran.append("body")), bad)

    @gen
    def prog():
        ex = yield from attempt.pipe(catch_cause(lambda c: succeed(c)))
        seen.append(ex)
        seen.append((yield from users_now()))

    await run(provide(Users.context("outer"))(prog()))
    assert ran == []
    assert isinstance(cast(Any, seen[0]).defects[0], Boom) and seen[1] == "outer"


# -- C3: children inherit a snapshot ------------------------------------------------------------


async def test_child_inherits_context_at_fork_and_cannot_leak_back():
    seen: list[tuple[str, object]] = []

    def note(who: str):
        return Users().use().flat_map(lambda u: sync(lambda: seen.append((who, u))))

    child_body = note("child").flat_map(lambda _: provide(Users.context("child's"))(note("child")))

    @gen
    def prog():
        fib = yield from provide(Users.context("forked under"))(fork(child_body))
        yield from fib.join()
        yield from note("parent")

    await run(provide(Users.context("parent's"))(prog()))
    # the child started after the parent left the inner provide, and still saw its snapshot
    assert seen == [("child", "forked under"), ("child", "child's"), ("parent", "parent's")]


async def test_fiber_context_is_read_only_and_owned():
    source: dict[str, Any] = {Users.key: "a"}
    mutate = sync(lambda: source.__setitem__(Users.key, "changed"))
    seen: list[object] = []
    refused: list[str] = []

    def attempt(name: str, write: Callable[[], object]) -> None:
        try:
            write()
        except (TypeError, AttributeError):
            refused.append(name)

    def poke(f: AnyFiber):
        attempt("item", lambda: cast(Any, f.context).__setitem__(Users.key, "x"))
        attempt("assign", lambda: setattr(f, "context", {}))
        attempt("delete", lambda: delattr(f, "context"))
        return Users().use()

    prog = update_context(
        mutate.flat_map(lambda _: WithFiber(poke)).flat_map(lambda u: sync(lambda: seen.append(u))),
        lambda _: source,
    )
    await run(provide(Users.context("outer"))(prog))
    # the caller's mapping was copied on install, and the fiber's copy can't be written
    assert seen == ["a"]
    assert refused == ["item", "assign", "delete"]


async def test_root_starts_empty():
    assert await run(raw()) == {}


# -- C4, C5: Reference defaults -------------------------------------------------------------------


async def test_reference_default_built_once_across_roots_and_override_restores():
    calls: list[int] = []

    class Level(Reference[str], key="test_context/Level"):
        @classmethod
        def default(cls) -> str:
            calls.append(1)
            return "info"

    # an override never builds the default
    assert await run(provide(Level.context("debug"))(Level().use())) == "debug"
    assert calls == []

    @gen
    def prog():
        a = yield from Level()
        b = yield from provide(Level.context("debug"))(Level().use())
        c = yield from Level()
        return a, b, c

    assert await run(prog()) == ("info", "debug", "info")
    assert await run(Level().use()) == "info"  # an independent root
    assert calls == [1]


async def test_failed_default_is_a_defect_and_not_kept():
    calls: list[int] = []

    class Flaky(Reference[int], key="test_context/Flaky"):
        @classmethod
        def default(cls) -> int:
            calls.append(1)
            if len(calls) == 1:
                raise Boom()
            return 7

    ex = await run_exit(Flaky().use())
    assert isinstance(ex, Failure) and isinstance(ex.cause.defects[0], Boom)
    assert await run(Flaky().use()) == 7
    assert await run(Flaky().use()) == 7
    assert calls == [1, 1]


# -- C6: exact keys, so concrete tags are final ---------------------------------------------------


def test_concrete_tags_cannot_be_subclassed():
    class Level(Reference[str], key="test_context/Level"):
        @classmethod
        def default(cls) -> str:
            return "info"

    with pytest.raises(TypeError, match="concrete"):
        types.new_class("Admins", (Users,))
    with pytest.raises(TypeError, match="concrete"):
        types.new_class("Loud", (Level,))


def test_base_tags_build_no_context():
    with pytest.raises(TypeError):
        Tag[str].context("x")
    with pytest.raises(TypeError):
        Reference[str].context("x")


# -- C7: Context is only built from tags ---------------------------------------------------------


def test_context_has_no_public_constructor_and_merges_right_wins():
    with pytest.raises(TypeError):
        cast(Any, Context)({Users.key: "forged"}, object())
    merged = Users.context("a") | Log.context("x") | Users.context("b")
    assert dict(merged.services) == {Users.key: "b", Log.key: "x"}
    with pytest.raises(TypeError):
        cast(Any, merged.services)[Users.key] = "c"


async def test_merged_context_right_side_wins_at_runtime():
    ctx = Users.context("a") | Users.context("b")
    assert await run(provide(ctx)(Users().use())) == "b"


# -- C8: a missed lookup is a defect ---------------------------------------------------------------


async def test_missing_service_is_a_defect():
    stripped = update_context(Users().use(), lambda _: EMPTY_CONTEXT)
    ex = await run_exit(provide(Users.context("a"))(stripped))
    assert isinstance(ex, Failure)
    (defect,) = ex.cause.defects
    assert isinstance(defect, MissingService) and defect.tag is Users


@pytest.mark.parametrize("member", ["services", "ctx"])
@pytest.mark.parametrize("operation", ["replace", "delete"])
def test_dependency_evidence_cannot_be_mutated(member: str, operation: str):
    ctx = Users.context("present")
    target = ctx if member == "services" else provide(ctx)
    with pytest.raises(AttributeError):
        if operation == "replace":
            setattr(target, member, {})
        else:
            delattr(target, member)
