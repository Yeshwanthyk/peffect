"""Kernel types pyright must infer. Checked by tests/test_types.py."""

from typing import Never, assert_type

from peffect import (
    Context,
    Effect,
    Exit,
    Fiber,
    Reference,
    Tag,
    catch_all,
    catch_tag,
    ensuring,
    fail,
    fork,
    gen,
    provide,
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


# -- Context: a tag adds itself to R; provide removes exactly the tags its Context holds ---------


class Repo: ...


class Logger: ...


class Users(Tag[Repo]): ...


class Log(Tag[Logger]): ...


class Db(Tag[int]): ...


class Level(Reference[str]):
    @classmethod
    def default(cls) -> str:
        return "info"


@gen
def needs_two():
    repo = yield from Users()
    log = yield from Log()
    level = yield from Level()  # a Reference never adds to R
    assert_type(level, str)
    return repo, log


Pair = tuple[Repo, Logger]
assert_type(needs_two(), Effect[Pair, Never, Users | Log])
one_left = provide(Users.context(Repo()))(needs_two())
assert_type(one_left, Effect[Pair, Never, Log])  # partial
assert_type(provide(Log.context(Logger()))(one_left), Effect[Pair, Never, Never])  # 2 -> 1 -> 0
full = Users.context(Repo()) | Log.context(Logger())
assert_type(full, Context[Users | Log])
assert_type(provide(full)(needs_two()), Effect[Pair, Never, Never])  # full
assert_type(provide(full | Db.context(1))(needs_two()), Effect[Pair, Never, Never])  # more
overlap = Users.context(Repo()) | Db.context(1)
assert_type(provide(overlap)(needs_two()), Effect[Pair, Never, Log])  # overlap keeps Log
assert_type(provide(Level.context("debug"))(needs_two()), Effect[Pair, Never, Users | Log])
assert_type(Level().use(), Effect[str, Never, Never])


# a requirement Python can't subtract from (a bare type variable) is kept, not dropped
def keeps[R](eff: Effect[int, Never, R]) -> Effect[int, Never, R]:
    return provide(Users.context(Repo()))(eff)


def subtracts[R](eff: Effect[int, Never, Users | R]) -> Effect[int, Never, R]:
    return provide(Users.context(Repo()))(eff)
