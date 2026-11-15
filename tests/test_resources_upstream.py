"""Batch F: Effect v4's resource tests for acquireRelease, acquireUseRelease and addFinalizer,
ported from Effect.test.ts. Upstream is pinned at vendor/effect-v4 commit 289a53f7 (Effect v4.1.4).
Upstream implementation: internal/effect.ts acquireRelease :4240-4256, addFinalizer :4259-4269,
acquireUseRelease :4502-4515.

The three functions are not in peffect yet. Each test looks them up on the package when it runs,
so all tests collect. A missing API raises AttributeError (captured as a Die inside @gen).

Adaptations used throughout:
- `Effect.delay(eff, ms)` is `sleep(ms / 1000)` and then `eff`: real time, as upstream's
  `it.live` (:1389, :2578) and ordinary `it` (:322, :377). Durations are unchanged.
- `Effect.runFork(eff)` + `fiber.interruptUnsafe()` + `Fiber.await` is
  `fork(eff, start_immediately=True)` inside `run`, then `sync(fiber.interrupt_unsafe)`
  and `fiber.await_()`.
  `forkChild({ startImmediately: true })` is the same `fork`.
- The two runFork interruption tests use `interrupt_unsafe()` without an interruptor id,
  preserving the exact `Cause.interrupt()` assertion; no reason is normalized away.
- `Exit.hasInterrupts(e)` is "a Failure with an Interrupt among its reasons".
  `fiber.pollUnsafe()` after `Fiber.interrupt` is the exit `fiber.await_()` gives then.
- peffect's Exit has no `==`; an expected `Exit.failCause(c)` is "a Failure whose cause == c",
  as in tests/test_finalization_upstream.py. Upstream's error strings are exception instances.
- `assertExitDefect(exit, d)` (Effect.test.ts:40) is "a Failure whose cause holds a Die and
  whose first defect is d".
- `Effect.flip` is not ported; its acquisition test checks the failure Exit and exact error
  identity, so an unexpected success cannot satisfy the assertion.
- `Effect.service(ATag)` is `ATag().use()`; `Effect.provideService(eff, ATag, "A")` is
  `provide(ATag.context("A"))(eff)`.

Deferred, not ported here:
- Effect.test.ts:85 "scoped closes its scope when interrupted after installing it" needs a
  custom Scheduler to interrupt between two steps of `scoped`; peffect has no Scheduler hook.
- Effect.test.ts:566 (acquireUseRelease around `tryPromise`) needs tryPromise/AbortController.
- Effect.test.ts:4892 and :4983 use addFinalizer to test AiError reason handling, not resources.
"""

from collections.abc import Callable
from typing import Any, Never, cast

import pytest

import peffect
from peffect import (
    Cause,
    Effect,
    Exit,
    Failure,
    Fiber,
    Interrupt,
    Scope,
    Success,
    Tag,
    die,
    exit_,
    fail,
    fork,
    gen,
    never,
    provide,
    run,
    run_exit,
    scoped,
    sleep,
    succeed,
    sync,
    uninterruptible,
)

type AnyEffect = Effect[Any, Any, Any]
type Release = Callable[[Any, Exit[Any, Any]], AnyEffect]


def _upcoming(name: str) -> Callable[..., AnyEffect]:
    """The peffect function `name`, read when the test runs (AttributeError until it exists)."""
    return cast(Callable[..., AnyEffect], getattr(peffect, name))


def acquire_release(acquire: AnyEffect, release: Release) -> AnyEffect:
    return _upcoming("acquire_release")(acquire, release)


def acquire_use_release(
    acquire: AnyEffect, use: Callable[[Any], AnyEffect], release: Release
) -> AnyEffect:
    return _upcoming("acquire_use_release")(acquire, use, release)


def add_finalizer(finalizer: Callable[[Exit[Any, Any]], AnyEffect]) -> AnyEffect:
    return _upcoming("add_finalizer")(finalizer)


class Boom(Exception):
    pass


EXAMPLE = Boom("Oh noes!")  # upstream ExampleError, Effect.test.ts:3154
VOID: Effect[None, Never, Never] = succeed(None)


class ATag(Tag[str]): ...


def has_interrupts(ex: Exit[Any, Any] | None) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


def assert_exit_defect(ex: Exit[Any, Any] | None, defect: BaseException) -> None:
    assert isinstance(ex, Failure)
    assert ex.cause.defects
    assert ex.cause.defects[0] is defect


def delayed[A](seconds: float, thunk: Callable[[], A]) -> Effect[A, Never, Never]:
    return sleep(seconds).flat_map(lambda _: sync(thunk))


# == acquireRelease (Effect.test.ts:1388) =======================================================


async def test_acquire_release_releases_on_interrupt():
    """Effect.test.ts:1389 "releases on interrupt"."""
    flags = {"release": False}

    @gen
    def program():
        resource = acquire_release(
            delayed(0.1, lambda: "foo"),
            lambda _a, _ex: sync(lambda: flags.__setitem__("release", True)),
        )
        fiber: Fiber[Any, Any] = yield from fork(scoped(resource), start_immediately=True)
        yield from sync(fiber.interrupt_unsafe)
        yield from fiber.await_()

    await run(program())
    assert flags["release"] is True


async def test_acquire_release_supports_release_dependencies():
    """Effect.test.ts:1407 "supports release dependencies": the release reads ATag, which was
    provided around acquireRelease but not around Scope.close."""
    flags = {"release": False}

    @gen
    def program():
        s = yield from Scope.make()
        resource = acquire_release(
            succeed("foo"),
            lambda _a, _ex: (
                ATag().use().flat_map(lambda _: sync(lambda: flags.__setitem__("release", True)))
            ),
        )
        yield from Scope.provide(s)(provide(ATag.context("A"))(resource))
        yield from Scope.close(s, Success(None))

    await run(program())
    assert flags["release"] is True


# == acquireUseRelease (Effect.test.ts:322-400, :2578-2621) =====================================


def _flags_resource(flags: dict[str, bool]) -> tuple[AnyEffect, Release]:
    """The shared acquire/release pair of :322 and :377."""

    def acquired() -> int:
        flags["acquire"] = True
        return 123

    def release(a: Any, _ex: Exit[Any, Any]) -> AnyEffect:
        def mark() -> None:
            assert a == 123
            flags["release"] = True

        return sync(mark)

    return delayed(0.1, acquired), release


async def _fork_interrupt_await(eff: AnyEffect, *, unsafe: bool = False) -> Exit[Any, Any]:
    @gen
    def program():
        fiber: Fiber[Any, Any] = yield from fork(eff, start_immediately=True)
        yield from sync(fiber.interrupt_unsafe) if unsafe else fiber.interrupt()
        return (yield from fiber.await_())

    return await run(program())


async def test_acquire_use_release_interrupt():
    """Effect.test.ts:322 "acquireUseRelease interrupt"."""
    flags = {"acquire": False, "use": False, "release": False}
    acquire, release = _flags_resource(flags)
    eff = acquire_use_release(
        acquire, lambda _: sync(lambda: flags.__setitem__("use", True)), release
    )
    result = await _fork_interrupt_await(eff, unsafe=True)
    assert isinstance(result, Failure)
    assert result.cause == Cause.interrupt()
    assert flags == {"acquire": True, "use": False, "release": True}


async def test_acquire_use_release_runs_release_when_use_throws():
    """Effect.test.ts:349 "acquireUseRelease runs release when use throws"."""
    resource = {"id": 1}
    defect = Boom("boom")
    seen: dict[str, Any] = {"releases": 0}

    def use(_: Any) -> AnyEffect:
        raise defect

    def release(r: Any, ex: Exit[Any, Any]) -> AnyEffect:
        def record() -> None:
            seen["releases"] += 1
            seen["resource"] = r
            seen["exit"] = ex

        return sync(record)

    result = await run(exit_(acquire_use_release(succeed(resource), use, release)))
    assert_exit_defect(result, defect)
    assert seen["releases"] == 1
    assert seen["resource"] is resource
    assert seen.get("exit") is not None
    assert_exit_defect(seen["exit"], defect)


async def test_acquire_use_release_uninterruptible():
    """Effect.test.ts:377 "acquireUseRelease uninterruptible"."""
    flags = {"acquire": False, "use": False, "release": False}
    acquire, release = _flags_resource(flags)
    eff = acquire_use_release(
        acquire, lambda a: sync(lambda: (flags.__setitem__("use", True), a)[1]), release
    )
    result = await _fork_interrupt_await(uninterruptible(eff), unsafe=True)
    assert isinstance(result, Failure)
    assert result.cause == Cause.interrupt()
    assert flags == {"acquire": True, "use": True, "release": True}


async def test_acquire_use_release_use_inherits_interrupt_status():
    """Effect.test.ts:2578 "acquireUseRelease use inherits interrupt status"."""
    flags = {"ref": False}
    eff = acquire_use_release(
        succeed(123),
        lambda _: delayed(0.01, lambda: flags.__setitem__("ref", True)),
        lambda _a, _ex: VOID,
    )
    await _fork_interrupt_await(uninterruptible(eff))
    assert flags["ref"] is True


async def test_acquire_use_release_release_runs_when_use_is_interrupted():
    """Effect.test.ts:2598 "acquireUseRelease release runs when use is interrupted"."""
    seen: dict[str, Any] = {"acquired": False}

    def acquired() -> int:
        seen["acquired"] = True
        return 123

    def release(r: Any, ex: Exit[Any, Any]) -> AnyEffect:
        def record() -> None:
            assert r == 123
            seen["exit"] = ex

        return sync(record)

    result = await _fork_interrupt_await(
        acquire_use_release(sync(acquired), lambda _: never, release)
    )
    assert seen["acquired"] is True
    assert seen.get("exit") is not None
    assert has_interrupts(seen["exit"])
    assert has_interrupts(result)


# == finalization (Effect.test.ts:3271-3381); :3153-3270 is tests/test_finalization_upstream.py ==


async def test_scoped_combines_usage_and_finalizer_failures():
    """Effect.test.ts:3271 "scoped combines usage and finalizer failures": the finalizer's
    defect elects the usage failure into a defect too."""
    usage, finalizer_failure = Boom("usage failure"), Boom("finalizer failure")

    @gen
    def body():
        yield from add_finalizer(lambda _ex: die(finalizer_failure))
        return (yield from fail(usage))

    result = await run_exit(scoped(body()))
    assert isinstance(result, Failure)
    assert result.cause == Cause.die(usage) + Cause.die(finalizer_failure)


async def test_acquire_use_release_usage_result():
    """Effect.test.ts:3283 "acquireUseRelease usage result"."""
    result = await run(acquire_use_release(VOID, lambda _: succeed(42), lambda _a, _ex: VOID))
    assert result == 42


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        pytest.param("acquire", Cause.fail(EXAMPLE), id="3292-error-in-just-acquisition"),
        pytest.param("release", Cause.die(EXAMPLE), id="3305-error-in-just-release"),
        pytest.param("use", Cause.fail(EXAMPLE), id="3318-error-in-just-usage"),
        pytest.param("release", Cause.die(EXAMPLE), id="3354-rethrown-caught-error-in-release"),
        pytest.param("use", Cause.fail(EXAMPLE), id="3367-rethrown-caught-error-in-usage"),
    ],
)
async def test_acquire_use_release_single_stage_error(stage: str, expected: Cause[Any]):
    """Effect.test.ts ports at the exact lines in the parameter ids; shared setup/assertions."""
    eff = acquire_use_release(
        fail(EXAMPLE) if stage == "acquire" else VOID,
        lambda _: fail(EXAMPLE) if stage == "use" else VOID,
        lambda _a, _ex: die(EXAMPLE) if stage == "release" else VOID,
    )
    result = await run_exit(eff)
    assert isinstance(result, Failure)
    assert result.cause == expected


async def test_combines_usage_and_release_failures():
    """Effect.test.ts:3331 "combines usage and release failures"."""
    usage, release_failure = Boom("usage failure"), Boom("release failure")
    eff = acquire_use_release(VOID, lambda _: fail(usage), lambda _a, _e: fail(release_failure))
    result = await run_exit(eff)
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(usage) + Cause.fail(release_failure)


async def test_rethrown_caught_error_in_acquisition():
    """Effect.test.ts:3344 "rethrown caught error in acquisition" (flip: see the docstring)."""
    eff = acquire_use_release(fail(EXAMPLE), lambda _: VOID, lambda _a, _e: VOID)
    result = await run_exit(eff)
    assert isinstance(result, Failure)
    assert result.cause == Cause.fail(EXAMPLE)
    assert result.cause.failures[0] is EXAMPLE


# Source-derived additions: these are not claimed as Effect.test.ts ports.


@pytest.mark.parametrize("api", ["acquire_release", "add_finalizer"])
async def test_source_finalizer_captures_registration_context_and_close_exit(api: str):
    """internal/effect.ts:4240-4269: capture context now; supply the closing Exit later."""
    seen: list[tuple[str, Exit[Any, Any]]] = []
    closing = Failure(Cause.fail(EXAMPLE))

    @gen
    def program():
        s = yield from Scope.make()
        def finalizer(ex: Exit[Any, Any]) -> AnyEffect:
            return ATag().use().flat_map(lambda a: sync(lambda: seen.append((a, ex))))

        registration = (
            add_finalizer(finalizer)
            if api == "add_finalizer"
            else acquire_release(succeed("resource"), lambda _a, ex: finalizer(ex))
        )
        yield from Scope.provide(s)(provide(ATag.context("acquired"))(registration))
        yield from provide(ATag.context("closing"))(Scope.close(s, closing))

    await run(program())
    assert len(seen) == 1
    assert seen[0][0] == "acquired"
    assert seen[0][1] is closing


@pytest.mark.parametrize("api", ["acquire_release", "acquire_use_release"])
@pytest.mark.parametrize("defect", [False, True], ids=["failure", "defect"])
async def test_source_acquisition_failure_never_uses_or_releases(api: str, defect: bool):
    """internal/effect.ts:4246-4254, :4509-4514: flatMap registers cleanup only on success.
    Effect.test.ts:3292 checks the acquisition Exit, but not the absence of use/release."""
    events: list[str] = []
    acquire = die(EXAMPLE) if defect else fail(EXAMPLE)
    def release(_a: Any, _ex: Exit[Any, Any]) -> AnyEffect:
        return sync(lambda: events.append("release"))

    eff = (
        scoped(acquire_release(acquire, release))
        if api == "acquire_release"
        else acquire_use_release(acquire, lambda _: sync(lambda: events.append("use")), release)
    )
    result = await run_exit(eff)
    assert isinstance(result, Failure)
    assert result.cause == (Cause.die(EXAMPLE) if defect else Cause.fail(EXAMPLE))
    assert events == []
