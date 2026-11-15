"""Four ways to write the same body. What does pyright infer for each?"""

from collections.abc import Generator
from typing import Any, reveal_type

import pfx
from pfx import Effect, Tag, TaggedError, gen


class Boom(TaggedError):
    n: int


class Store(Tag[dict[str, int]]): ...


def lookup(k: str) -> Effect[int, Boom, Store]: ...


# 1. yield from (what pfx does)
@gen
def with_yield_from():
    store = yield from Store()
    n = yield from lookup("a")
    reveal_type(store)
    reveal_type(n)
    return n + len(store)


reveal_type(with_yield_from)


# 2. plain yield: the value you get back is the generator's ONE send type
def with_plain_yield():
    store = yield Store().use()
    n = yield lookup("a")
    reveal_type(store)
    reveal_type(n)
    return n


reveal_type(with_plain_yield)


# 2b. plain yield, send type annotated: still one type for every yield
def with_plain_yield_annotated() -> Generator[Effect[Any, Boom, Store], int, int]:
    n = yield lookup("a")
    s = yield Store().use()  # want dict[str, int], get int
    reveal_type(s)
    return n


# 3. async/await: per-site types are perfect, but E and R vanish
class AwaitableEffect[A, E, R]:
    def __await__(self) -> Generator[Effect[Any, E, R], Any, A]: ...


def lookup_aw(k: str) -> AwaitableEffect[int, Boom, Store]: ...


async def with_await():
    n = await lookup_aw("a")
    reveal_type(n)
    return n


reveal_type(with_await)
_ = pfx
