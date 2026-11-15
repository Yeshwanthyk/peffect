"""Kernel types pyright must infer. Checked by tests/test_types.py."""

from typing import Any, Never, assert_type

from peffect import (
    Cause,
    Clock,
    ClockService,
    Closeable,
    Context,
    Deferred,
    Effect,
    Exit,
    Failure,
    Fiber,
    Reference,
    Scope,
    ScopeService,
    Tag,
    acquire_release,
    acquire_use_release,
    add_finalizer,
    catch_all,
    catch_tag,
    clock_with,
    current_time_millis,
    ensuring,
    exit_,
    fail,
    fiber_id,
    fork,
    fork_daemon,
    gen,
    on_error,
    on_exit,
    on_interrupt,
    provide,
    scoped,
    sleep,
    succeed,
    sync,
)
from peffect import scope as current_scope  # Effect.scope


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


def on_cause(cause: Cause[Any]) -> Effect[None, Never, Never]:
    return sync(lambda: None)


def on_ids(ids: frozenset[int]) -> Effect[None, Never, Never]:
    return sync(lambda: None)


assert_type(find(1).pipe(on_error(on_cause)), Effect[str, NotFound, Never])
assert_type(find(1).pipe(on_interrupt(on_ids)), Effect[str, NotFound, Never])


# fork / join carry A and E through the Fiber
@gen
def parent():
    f = yield from fork(find(2))
    assert_type(f, Fiber[str, NotFound])
    ex = yield from f.await_()
    assert_type(ex, Exit[str, NotFound])
    return (yield from f.join())


assert_type(parent(), Effect[str, NotFound, Never])
assert_type(fork(find(2), start_immediately=True), Effect[Fiber[str, NotFound], Never, Never])
assert_type(
    fork_daemon(find(2), start_immediately=True), Effect[Fiber[str, NotFound], Never, Never]
)

# exit_ turns both outcomes into the value; the error channel is empty
assert_type(exit_(find(2)), Effect[Exit[str, NotFound], Never, Never])


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
assert_type(Context.empty() | full, Context[Users | Log])  # empty adds no tag
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


# Clock: a Reference, so reading it adds nothing to R; clock_with keeps f's E and R
assert_type(Clock().use(), Effect[ClockService, Never, Never])
assert_type(clock_with(lambda _: find(1)), Effect[str, NotFound, Never])
assert_type(clock_with(lambda _: needs_two()), Effect[Pair, Never, Users | Log])
assert_type(current_time_millis, Effect[int, Never, Never])
assert_type(sleep(0.5), Effect[None, Never, Never])


class FixedClock:
    def current_time_millis_unsafe(self) -> int:
        return 0

    def current_time_nanos_unsafe(self) -> int:
        return 0

    def monotonic_time_nanos_unsafe(self) -> int:
        return 0

    @property
    def current_time_millis(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def current_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    @property
    def monotonic_time_nanos(self) -> Effect[int, Never, Never]:
        return succeed(0)

    def sleep(self, seconds: float) -> Effect[None, Never, Never]:
        return succeed(None)


fixed: ClockService = FixedClock()  # structural: any class with the members is a ClockService
assert_type(provide(Clock.context(fixed))(sleep(1)), Effect[None, Never, Never])


# Scope: `scope` requires Scope, `scoped` discharges exactly it, finalizers take any Exit
assert_type(Scope.make(), Effect[Closeable, Never, Never])
assert_type(current_scope, Effect[ScopeService, Never, Scope])
assert_type(scoped(current_scope), Effect[ScopeService, Never, Never])
assert_type(scoped(current_scope.flat_map(lambda _: Users().use())), Effect[Repo, Never, Users])
made: ScopeService = Scope.make_unsafe()  # a Closeable is a ScopeService
assert_type(Scope.add_finalizer_exit(made, lambda ex: ex), Effect[None, Never, Never])
assert_type(Scope.fork(made), Effect[Closeable, Never, Never])
assert_type(Scope.provide(made)(current_scope), Effect[ScopeService, Never, Never])


# Resources: acquisition returns its value, registration captures release requirements.
resource = acquire_release(Users().use(), lambda _repo, _ex: Log().use())
assert_type(resource, Effect[Repo, Never, Users | Log | Scope])
assert_type(scoped(resource), Effect[Repo, Never, Users | Log])
assert_type(provide(full)(scoped(resource)), Effect[Repo, Never, Never])
registered = add_finalizer(lambda _ex: Log().use().as_(None))
assert_type(registered, Effect[None, Never, Log | Scope])
assert_type(provide(Log.context(Logger()))(scoped(registered)), Effect[None, Never, Never])
assert_type(
    acquire_release(find(1), lambda _name, _ex: succeed(None)),
    Effect[str, NotFound, Scope],
)


class ReleaseError(Exception): ...


def acquire_resource() -> Effect[int, NotFound, Users]:
    return Users().use().as_(123)


def use_resource(resource: int) -> Effect[str, Timeout, Log]:
    return Log().use().as_(str(resource))


def release_resource(resource: int, ex: Exit[str, Timeout]) -> Effect[None, ReleaseError, Db]:
    return Db().use().as_(None)


assert_type(
    acquire_use_release(acquire_resource(), use_resource, release_resource),
    Effect[str, NotFound | Timeout | ReleaseError, Users | Log | Db],
)
# Pin each empty-channel overload, too: no Unknown may hide a requirement or error.
assert_type(
    acquire_use_release(succeed(1), lambda _: succeed("used"), lambda _a, _ex: succeed(None)),
    Effect[str, Never, Never],
)
assert_type(
    acquire_use_release(find(1), lambda _: slow(), lambda _a, _ex: fail(ReleaseError())),
    Effect[int, NotFound | Timeout | ReleaseError, Never],
)
assert_type(
    acquire_use_release(Users().use(), lambda _: Log().use(), lambda _a, _ex: Db().use().as_(None)),
    Effect[Logger, Never, Users | Log | Db],
)
# Generalized on_exit exposes a failing finalizer in either public spelling.
assert_type(find(1).on_exit(lambda _: fail(Timeout())), Effect[str, NotFound | Timeout, Never])
assert_type(
    find(1).pipe(on_exit(lambda _: fail(Timeout()))), Effect[str, NotFound | Timeout, Never]
)


# Deferred: completion keeps the cell's A/E; stored Effects require no environment.
deferred = Deferred[int, NotFound].make_unsafe()
assert_type(deferred, Deferred[int, NotFound])
assert_type(Deferred[int, NotFound].make(), Effect[Deferred[int, NotFound], Never, Never])
assert_type(deferred.effect, Effect[int, NotFound, Never] | None)
assert_type(deferred.await_(), Effect[int, NotFound, Never])
assert_type(deferred.succeed(1), Effect[bool, Never, Never])
assert_type(deferred.fail(NotFound()), Effect[bool, Never, Never])
assert_type(deferred.done_unsafe(fail(NotFound())), bool)
assert_type(deferred.done(Failure(Cause.fail(NotFound()))), Effect[bool, Never, Never])
assert_type(deferred.interrupt_with(-1), Effect[bool, Never, Never])
assert_type(deferred.interrupt(), Effect[bool, Never, Never])
assert_type(Deferred[int].make_unsafe(), Deferred[int, Never])
assert_type(fiber_id, Effect[int, Never, Never])
