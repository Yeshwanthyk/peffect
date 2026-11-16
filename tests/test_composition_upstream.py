"""`tap` and multi-function `pipe` at Effect v4 pin 289a53f7.

Upstream:
- tap: internal/effect.ts:1607-1613 (tapCont, tapEffectCont), :1654-1675 (signatures). It runs
  `f(a)`, or a ready-made effect, after success, then succeeds with the original `a`; result type
  `Effect<A, E | E2, R | R2>`.
- pipe: Pipeable.ts:567-597 (pipeArguments): left-to-right application, eager; no functions
  returns self.

Direct ports carry the upstream test name. Source-derived tests cite the source lines they check.
Adaptation: Function.test.ts tests `F.pipe(value, ...)`; here the same cases go through the
method `Effect.pipe`, the call site of pipeArguments.
"""

from typing import Never, assert_type

from peffect import (
    Cause,
    Effect,
    Failure,
    Layer,
    MissingService,
    Success,
    Tag,
    catch_tag,
    fail,
    provide,
    provide_layer,
    run_exit,
    succeed,
    sync,
    tap,
    update_context,
)


class Boom(Exception):
    pass


class Other(Exception):
    pass


BODY = Boom("body")
TAPPED = Other("tapped")


class ATag(Tag[str], key="test/composition/A"): ...


class BTag(Tag[int], key="test/composition/B"): ...


class CTag(Tag[float], key="test/composition/C"): ...


# == upstream: Effect.test.ts ==================================================================


async def test_context_service():
    """Effect.test.ts:405 "Context.Service": tap then provide, in one pipe."""
    seen: list[str] = []
    program = (
        ATag()
        .use()
        .pipe(
            lambda e: e.tap(lambda a: sync(lambda: seen.append(a))),
            provide(ATag.context("A")),
        )
    )
    assert_type(program, Effect[str, Never, Never])
    result = await run_exit(program)
    assert isinstance(result, Success)
    assert result.value == "A"
    assert seen == ["A"]


# == upstream: Function.test.ts (describe "pipe", through Effect.pipe) ========================


async def test_pipe_returns_value_with_no_functions():
    """Function.test.ts:209 "returns value with no functions"."""
    eff = succeed(5)
    assert eff.pipe() is eff


async def test_pipe_applies_one_function():
    """Function.test.ts:213 "applies one function"."""
    result = await run_exit(succeed(5).pipe(lambda e: e.map(lambda n: n + 1)))
    assert isinstance(result, Success)
    assert result.value == 6


async def test_pipe_applies_two_functions():
    """Function.test.ts:217 "applies two functions"."""
    result = await run_exit(
        succeed(5).pipe(lambda e: e.map(lambda n: n + 1), lambda e: e.map(lambda n: n * 2))
    )
    assert isinstance(result, Success)
    assert result.value == 12


async def test_pipe_applies_a_long_chain():
    """Function.test.ts:221 "applies a long chain": nine functions."""

    def inc(e: Effect[int, Never, Never]) -> Effect[int, Never, Never]:
        return e.map(lambda n: n + 1)

    program = succeed(1).pipe(inc, inc, inc, inc, inc, inc, inc, inc, inc)
    assert_type(program, Effect[int, Never, Never])
    result = await run_exit(program)
    assert isinstance(result, Success)
    assert result.value == 10


async def test_pipe_transforms_types_through_the_chain():
    """Function.test.ts:239 "transforms types through the chain"."""
    program = succeed(5).pipe(lambda e: e.map(str), lambda e: e.map(len))
    assert_type(program, Effect[int, Never, Never])
    result = await run_exit(program)
    assert isinstance(result, Success)
    assert result.value == 1


# == source-derived: pipe (Pipeable.ts:567-597) ================================================


def test_pipe_applies_functions_in_order_at_call_time_without_running_the_effect():
    """pipeArguments calls args[0] first and returns args[n-1]'s result; it runs nothing."""
    calls: list[str] = []
    ran: list[bool] = []
    eff = sync(lambda: ran.append(True))

    def step(name: str):
        def f[A](x: A) -> A:
            calls.append(name)
            return x

        return f

    out = eff.pipe(step("1"), step("2"), step("3"))
    assert calls == ["1", "2", "3"]
    assert ran == []
    assert out is eff


def test_pipe_can_leave_the_effect_type():
    """Each step's output is the next step's input, of any type; the last decides the result."""
    out = succeed(5).pipe(lambda _: "five", len)
    assert_type(out, int)
    assert out == 4


# == source-derived: tap (internal/effect.ts:1607-1613, :1654-1675) ===========================


async def test_tap_preserves_the_value_and_runs_the_function_on_it():
    """tapCont: `ContImpl(f(value), succeedPayload, value)`."""
    seen: list[int] = []
    program = succeed(1).tap(lambda a: sync(lambda: seen.append(a)).as_("ignored"))
    assert_type(program, Effect[int, Never, Never])
    result = await run_exit(program)
    assert isinstance(result, Success)
    assert result.value == 1
    assert seen == [1]


async def test_tap_with_an_effect_runs_it_and_keeps_the_value():
    """tapEffectCont: an Effect argument runs as-is (overloads 2 and 4, :1658-1660)."""
    count: list[int] = []
    program = succeed("a").tap(sync(lambda: count.append(1)))
    assert_type(program, Effect[str, Never, Never])
    for _ in range(2):
        result = await run_exit(program)
        assert isinstance(result, Success)
        assert result.value == "a"
    assert count == [1, 1]


async def test_tap_is_lazy_and_reusable():
    """Building the effect calls nothing; each run calls `f` once."""
    calls: list[int] = []

    def f(a: int) -> Effect[None, Never, Never]:
        calls.append(a)
        return succeed(None)

    program = succeed(7).tap(f)
    assert calls == []
    await run_exit(program)
    await run_exit(program)
    assert calls == [7, 7]


async def test_tap_does_not_run_after_a_failure():
    """tap is a success continuation (ContImpl with onSuccess only)."""
    calls: list[object] = []
    program = fail(BODY).tap(lambda a: sync(lambda: calls.append(a)))
    assert_type(program, Effect[Never, Boom, Never])
    result = await run_exit(program)
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(BODY)
    assert calls == []


async def test_tap_failure_replaces_the_value_and_widens_the_error():
    """E | E2: a failing tap fails the whole effect with its own error."""

    def build(source: Effect[int, Boom, Never]):  # a parameter keeps the declared Boom
        return source.tap(lambda _: fail(TAPPED))

    program = build(succeed(1))
    assert_type(program, Effect[int, Boom | Other, Never])
    result = await run_exit(program)
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(TAPPED)


async def test_tap_adds_the_requirements_of_the_tapped_effect():
    """R | R2: the tapped effect's services are needed; without them the run dies."""
    program = ATag().use().tap(lambda _: BTag().use())
    assert_type(program, Effect[str, Never, ATag | BTag])
    without_b = update_context(program, lambda c: {k: v for k, v in c.items() if k != BTag.key})
    missing = await run_exit(provide(ATag.context("a") | BTag.context(2))(without_b))
    assert isinstance(missing, Failure)
    assert any(isinstance(d, MissingService) for d in missing.cause.defects)
    both = await run_exit(provide(ATag.context("a") | BTag.context(2))(program))
    assert isinstance(both, Success)
    assert both.value == "a"


async def test_tap_raising_is_a_defect():
    """A thrown `f` is an evaluation error, so Die, as for any continuation."""

    def f(_: int) -> Effect[None, Never, Never]:
        raise TAPPED

    result = await run_exit(succeed(1).tap(f))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(TAPPED)


async def test_tap_pipeable_matches_the_method():
    """The data-last form (:1655-1660) gives the same result, inside a multi-step pipe."""
    seen: list[object] = []
    program = succeed(3).pipe(
        tap(lambda a: sync(lambda: seen.append(a))),
        tap(sync(lambda: seen.append("effect"))),
        lambda e: e.map(lambda n: n * 2),
    )
    assert_type(program, Effect[int, Never, Never])
    result = await run_exit(program)
    assert isinstance(result, Success)
    assert result.value == 6
    assert seen == [3, "effect"]


async def test_tap_pipeable_widens_error_and_requirements():
    """E | E2 and R | R2 through the pipeable."""

    def build(source: Effect[int, Boom, ATag]):
        return source.pipe(tap(lambda _: BTag().use().flat_map(lambda _: fail(TAPPED))))

    program = build(ATag().use().as_(1))
    assert_type(program, Effect[int, Boom | Other, ATag | BTag])
    result = await run_exit(provide(ATag.context("a") | BTag.context(2))(program))
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(TAPPED)


# == pinned limitation: catch_tag inside one multi-step pipe ===================================


async def test_second_catch_tag_in_one_pipe_over_approximates_the_error():
    """Pyright solves a pipe's steps together. After a first catch_tag leaves two or more errors,
    a second catch_tag in the same pipe keeps its own tag in E: sound (wider than needed), never
    narrower. Chained `.pipe(a).pipe(b)` or nested calls narrow exactly. Runtime is unaffected."""

    def one_pipe(src: Effect[int, Boom | Other | KeyError, Never]):
        return src.pipe(
            catch_tag(Boom, lambda _: succeed(0)), catch_tag(Other, lambda _: succeed(1))
        )

    def chained(src: Effect[int, Boom | Other | KeyError, Never]):
        return src.pipe(catch_tag(Boom, lambda _: succeed(0))).pipe(
            catch_tag(Other, lambda _: succeed(1))
        )

    assert_type(one_pipe(fail(TAPPED)), Effect[int, KeyError | Other, Never])
    assert_type(chained(fail(TAPPED)), Effect[int, KeyError, Never])
    for build in (one_pipe, chained):
        result = await run_exit(build(fail(TAPPED)))
        assert isinstance(result, Success)
        assert result.value == 1


# == source-derived: Layer.pipe (Layer is Pipeable: Layer.ts:126-141, Pipeable.ts:567-597) =====


def test_layer_pipe_with_no_steps_returns_the_layer():
    """pipeArguments with no functions returns self."""
    live = Layer.succeed(BTag, 2)
    assert live.pipe() is live


def test_layer_pipe_applies_steps_left_to_right_and_may_leave_the_layer_type():
    """Each step gets the previous result, of any type; nothing is built."""
    calls: list[str] = []
    live = Layer.succeed(BTag, 2)

    def step(name: str):
        def f[X](x: X) -> X:
            calls.append(name)
            return x

        return f

    out = live.pipe(step("1"), step("2"), lambda _: "built?", len)
    assert_type(out, int)
    assert out == 6
    assert calls == ["1", "2"]


async def test_layer_pipe_infers_provide_then_merge_in_one_call():
    """`ALive.pipe(Layer.provide(BLive), Layer.merge(CLive))`: the same type as the chained form,
    the requirement removed, and the built context serves both tags."""
    a_live = Layer.effect(ATag, BTag().use().map(lambda n: f"a{n}"))
    b_live = Layer.succeed(BTag, 2)
    c_live = Layer.succeed(CTag, 0.5)
    one_call = a_live.pipe(Layer.provide(b_live), Layer.merge(c_live))
    chained = a_live.pipe(Layer.provide(b_live)).pipe(Layer.merge(c_live))
    assert_type(one_call, Layer[ATag | CTag, Never, Never])
    assert_type(chained, Layer[ATag | CTag, Never, Never])
    program = ATag().use().flat_map(lambda a: CTag().use().map(lambda c: (a, c)))
    result = await run_exit(provide_layer(one_call)(program))
    assert isinstance(result, Success)
    assert result.value == ("a2", 0.5)


async def test_second_layer_provide_in_one_pipe_over_approximates_the_requirement():
    """As for catch_tag: after a first Layer.provide leaves two or more requirements, a second
    provide in the same pipe keeps its own in RIn (wider than needed, never narrower). Chained
    pipes narrow exactly. The built layer is the same either way."""

    class DTag(Tag[bytes], key="test/composition/D"): ...

    def a_needs(b: int, c: float, d: bytes) -> str:
        return f"{b}{c}{d.decode()}"

    a_live = Layer.effect(
        ATag,
        BTag()
        .use()
        .flat_map(
            lambda b: CTag().use().flat_map(lambda c: DTag().use().map(lambda d: a_needs(b, c, d)))
        ),
    )
    b_live = Layer.succeed(BTag, 1)
    c_live = Layer.succeed(CTag, 0.5)
    one_pipe = a_live.pipe(Layer.provide(b_live), Layer.provide(c_live))
    chained = a_live.pipe(Layer.provide(b_live)).pipe(Layer.provide(c_live))
    assert_type(one_pipe, Layer[ATag, Never, CTag | DTag])
    assert_type(chained, Layer[ATag, Never, DTag])
    # the over-wide type asks the caller for CTag too; the layer's own 0.5 still wins inside it
    outer = DTag.context(b"x") | CTag.context(9.0)
    for live in (one_pipe, chained):
        result = await run_exit(provide(outer)(provide_layer(live)(ATag().use())))
        assert isinstance(result, Success)
        assert result.value == "10.5x"
