from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Never, Protocol

import pytest

import pfx
from pfx import Effect, Tag, TaggedError, catch_tag, gen, provide


class Boom(TaggedError):
    n: int


class Counter(Protocol):
    def next(self) -> int: ...


class Counters(Tag[Counter]): ...


class Res(Tag[str]): ...


class Res2(Tag[str]): ...


class _C:
    def __init__(self) -> None:
        self.i = 0

    def next(self) -> int:
        self.i += 1
        return self.i


async def test_typed_failure_short_circuits_and_is_caught() -> None:
    seen: list[str] = []

    @gen
    def prog():
        seen.append("before")
        yield from pfx.fail(Boom(n=1))
        seen.append("after")  # never runs
        return 0

    out = await pfx.run(prog().pipe(catch_tag(Boom, lambda e: pfx.succeed(e.n * 10))))
    assert out == 10
    assert seen == ["before"]


async def test_layer_builds_once_per_run_even_if_used_twice() -> None:
    builds: list[int] = []

    def make() -> Effect[Counter, Never, Never]:
        return pfx.sync(lambda: builds.append(1) or _C())

    live = Counters.effect(make())

    @gen
    def prog():
        a = yield from Counters()
        b = yield from Counters()
        return (a.next(), b.next())

    both = live.merge(live)  # same Layer object twice
    assert await pfx.run(prog().pipe(provide(both))) == (1, 2)
    assert builds == [1]


async def test_scoped_resources_release_in_reverse_order() -> None:
    log: list[str] = []

    def resource(name: str):
        @asynccontextmanager
        async def cm() -> AsyncGenerator[str]:
            log.append(f"open {name}")
            yield name
            log.append(f"close {name}")

        return cm

    @gen
    def prog():
        a = yield from Res()
        b = yield from Res2()
        log.append(f"use {a}+{b}")
        return a + b

    layer = Res.scoped(resource("a")).merge(Res2.scoped(resource("b")))
    assert await pfx.run(prog().pipe(provide(layer))) == "ab"
    assert log == ["open a", "open b", "use a+b", "close b", "close a"]


async def test_defects_are_exceptions_not_typed_errors() -> None:
    @gen
    def prog():
        yield from pfx.sync(lambda: 1 / 0)
        return 1

    with pytest.raises(ZeroDivisionError):
        await pfx.run(prog())


async def test_attempt_lifts_named_exception_into_E() -> None:
    def parse() -> int:
        return int("nope")

    eff = pfx.attempt(parse, ValueError).pipe(catch_tag(ValueError, lambda e: pfx.succeed(-1)))
    assert await pfx.run(eff) == -1


async def test_decode_turns_validation_error_into_typed_failure() -> None:
    from pydantic import BaseModel

    class M(BaseModel):
        x: int

    eff = pfx.decode(M, {"x": "nope"}).pipe(
        catch_tag(pfx.DecodeError, lambda e: pfx.succeed(M(x=len(e.errors))))
    )
    assert (await pfx.run(eff)).x == 1


def test_tagged_error_serializes_with_tag() -> None:
    assert Boom(n=2).to_json_dict() == {"_tag": "Boom", "n": 2}
