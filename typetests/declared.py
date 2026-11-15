"""Declared style: checks under mypy --strict too (mypy can't infer @gen bodies)."""

from typing import Never, assert_type

from peffect import Do, Effect, catch_tag, fail, fork, gen, succeed


class NotFound(Exception): ...


class Timeout(Exception): ...


def find(uid: int) -> Effect[str, NotFound, Never]:
    return succeed("ann") if uid == 1 else fail(NotFound())


def slow() -> Effect[int, Timeout, Never]:
    return succeed(1)


@gen
def both(uid: int) -> Do[str, NotFound | Timeout, Never]:
    name = yield from find(uid)
    n = yield from slow()
    return f"{name}{n}"


assert_type(both(1), Effect[str, NotFound | Timeout, Never])
assert_type(both(1).pipe(catch_tag(NotFound, lambda _: succeed(""))), Effect[str, Timeout, Never])


@gen
def parent() -> Do[str, NotFound, Never]:
    f = yield from fork(find(2))
    return (yield from f.join())


assert_type(parent(), Effect[str, NotFound, Never])
