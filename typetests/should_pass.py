"""Kernel types pyright must infer. Checked by tests/test_types.py."""

from typing import Never, assert_type

from peffect import (
    Effect,
    Exit,
    Fiber,
    catch_all,
    catch_tag,
    ensuring,
    fail,
    fork,
    gen,
    succeed,
    sync,
)


class NotFound(Exception): ...


class Timeout(Exception): ...


def find(uid: int) -> Effect[str, NotFound, Never]:
    return succeed("ann") if uid == 1 else fail(NotFound())


def slow() -> Effect[int, Timeout, Never]:
    return succeed(1)


# @gen unions the error types of every `yield from`
@gen
def both(uid: int):
    name = yield from find(uid)
    n = yield from slow()
    return f"{name}{n}"


assert_type(both(1), Effect[str, NotFound | Timeout, Never])

# catch_tag removes exactly the caught class
assert_type(both(1).pipe(catch_tag(NotFound, lambda _: succeed(""))), Effect[str, Timeout, Never])
assert_type(
    find(1).pipe(catch_tag(NotFound, lambda _: succeed(0))), Effect[str | int, Never, Never]
)

# methods see the precise error type; pipeables clear E entirely
assert_type(find(1).catch_all(lambda e: succeed(type(e).__name__)), Effect[str, Never, Never])
assert_type(both(1).pipe(catch_all(lambda _: succeed(None))), Effect[str | None, Never, Never])

# finalizers don't change A or E
assert_type(find(1).pipe(ensuring(sync(lambda: None))), Effect[str, NotFound, Never])


# fork / join carry A and E through the Fiber
@gen
def parent():
    f = yield from fork(find(2))
    assert_type(f, Fiber[str, NotFound])
    ex = yield from f.await_()
    assert_type(ex, Exit[str, NotFound])
    return (yield from f.join())


assert_type(parent(), Effect[str, NotFound, Never])
