"""Kernel types pyright must infer. Checked by tests/test_types.py."""

from collections.abc import Mapping
from typing import Any, Literal, Never, assert_type

from pydantic import ValidationError

import peffect.console as Console
import peffect.logger as EffectLogger
from examples.parity import python_app as parity
from examples.parity import python_composed as composed
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
    Layer,
    ManagedRuntime,
    MemoMap,
    Reference,
    Scope,
    ScopeService,
    Tag,
    TimeoutError,
    acquire_release,
    acquire_use_release,
    add_finalizer,
    await_all,
    catch_all,
    catch_tag,
    clock_with,
    current_time_millis,
    ensuring,
    exit_,
    fail,
    fiber_id,
    for_each,
    fork,
    fork_daemon,
    fork_scoped,
    gen,
    interrupt_all,
    never,
    on_error,
    on_exit,
    on_interrupt,
    provide,
    provide_layer,
    race,
    race_all,
    race_all_first,
    race_first,
    scoped,
    sleep,
    succeed,
    sync,
    tap,
    timeout,
    timeout_option,
    timeout_or_else,
)
from peffect import scope as current_scope  # Effect.scope
from peffect.duration import Duration, seconds
from peffect.option import Option, Some
from peffect.scheduler import (
    MaxOpsBeforeYield,
    MixedScheduler,
    PreventSchedulerYield,
    Scheduler,
    SchedulerDispatcher,
    SchedulerService,
    with_dispatcher,
)
from peffect.testing import TestClock, TestClockService, TestConsole


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


class Users(Tag[Repo], key="typetests/Users"): ...


class Log(Tag[Logger], key="typetests/Log"): ...


class Db(Tag[int], key="typetests/Db"): ...


class Level(Reference[str], key="typetests/Level"):
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
assert_type(Users.key, str)  # the key string is the runtime identity
assert_type(full.services, Mapping[str, Any])
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
assert_type(current_time_millis, Effect[float, Never, Never])  # a TestClock can stand at 0.0015
assert_type(sleep(500), Effect[None, Never, Never])  # a bare number is milliseconds
assert_type(sleep("1 second"), Effect[None, Never, Never])
assert_type(sleep(seconds(1)), Effect[None, Never, Never])


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

    def sleep(self, duration: Duration) -> Effect[None, Never, Never]:
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


# Fiber bulk operations preserve ordered Exit channels, never the target's error as failure.
def fiber_bulk_types(first: Fiber[int, NotFound], second: Fiber[str, Timeout]) -> None:
    assert_type(await_all([first]), Effect[list[Exit[int, NotFound]], Never, Never])
    targets: list[Fiber[int | str, NotFound | Timeout]] = [first, second]
    assert_type(
        await_all(iter(targets)), Effect[list[Exit[int | str, NotFound | Timeout]], Never, Never]
    )
    assert_type(interrupt_all(iter(targets)), Effect[None, Never, Never])


assert_type(
    fork(Users().use(), uninterruptible="inherit"), Effect[Fiber[Repo, Never], Never, Users]
)
assert_type(
    fork_daemon(find(1), uninterruptible="inherit"), Effect[Fiber[str, NotFound], Never, Never]
)
assert_type(fork(succeed(1)), Effect[Fiber[int, Never], Never, Never])


# for_each keeps the mapper's B, E and R; the index is an int, the result a list in input order.
def traversal_types(ids: list[int], names: str) -> None:
    assert_type(for_each(ids, lambda uid, _i: find(uid)), Effect[list[str], NotFound, Never])
    assert_type(
        for_each(ids, lambda _uid, _i: Users().use(), concurrency="unbounded"),
        Effect[list[Repo], Never, Users],
    )
    assert_type(
        for_each(names, lambda c, i: succeed((c, i)), concurrency=2),
        Effect[list[tuple[str, int]], Never, Never],
    )


# Layer[ROut, E, RIn]: built from effects, Scope in an effect's R is the layer's own lifetime and
# is removed; every other service, and every error, stays. provide removes only what it supplies.
def layer_types(
    repo: Repo,
    users: Layer[Users],
    db: Layer[Db, Timeout],
    users_from_db: Layer[Users, NotFound, Db],
    needs_db_log: Layer[Users, NotFound, Db | Log],
    mm: MemoMap,
    scope: ScopeService,
) -> None:
    assert_type(Layer.succeed(Users, repo), Layer[Users, Never, Never])
    assert_type(Layer.effect(Db, slow()), Layer[Db, Timeout, Never])
    assert_type(
        Layer.effect(Db, acquire_release(slow(), lambda _n, _ex: sync(lambda: None))),
        Layer[Db, Timeout, Never],
    )
    assert_type(Layer.effect(Users, Db().use().as_(repo)), Layer[Users, Never, Db])
    assert_type(Layer.effect_context(sync(lambda: Users.context(repo))), Layer[Users, Never, Never])
    assert_type(Layer.effect_discard(slow()), Layer[Never, Timeout, Never])
    assert_type(users_from_db.pipe(Layer.provide(db)), Layer[Users, NotFound | Timeout, Never])
    assert_type(needs_db_log.pipe(Layer.provide(db)), Layer[Users, NotFound | Timeout, Log])
    assert_type(users.pipe(Layer.merge(db)), Layer[Users | Db, Timeout, Never])
    assert_type(Layer.merge_all(users), Layer[Users, Never, Never])
    assert_type(
        Layer.merge_all(users, db, users_from_db, needs_db_log),
        Layer[Users | Db, Timeout | NotFound, Db | Log],
    )
    assert_type(Layer.build(users_from_db), Effect[Context[Users], NotFound, Db | Scope])
    assert_type(Layer.build_with_scope(users_from_db, scope), Effect[Context[Users], NotFound, Db])
    assert_type(
        Layer.build_with_memo_map(users_from_db, mm, scope), Effect[Context[Users], NotFound, Db]
    )
    assert_type(mm.get(users_from_db, scope), Effect[Context[Users], NotFound, Db] | None)
    assert_type(Layer.make_memo_map(), Effect[MemoMap, Never, Never])
    assert_type(Layer.fork_memo_map(mm), Effect[MemoMap, Never, Never])
    assert_type(provide_layer(db)(Db().use()), Effect[int, Timeout, Never])
    assert_type(
        provide_layer(users)(Users().use().flat_map(lambda _: Db().use())),
        Effect[int, Never, Db],
    )


# A layer that provides more stands for one that provides less; E and RIn only widen.
def needs_users(layer: Layer[Users]) -> Layer[Users]:
    return layer


def tolerates_db(layer: Layer[Users, NotFound, Db]) -> Layer[Users, NotFound, Db]:
    return layer


def layer_variance(users_and_db: Layer[Users | Db], users: Layer[Users]) -> None:
    assert_type(needs_users(users_and_db), Layer[Users])
    assert_type(tolerates_db(users), Layer[Users, NotFound, Db])


# Scheduler: References add nothing to R; any class with the members is a SchedulerService;
# with_dispatcher keeps f's A, E and R and hands it the fiber's dispatcher.
assert_type(Scheduler().use(), Effect[SchedulerService, Never, Never])
assert_type(MaxOpsBeforeYield().use(), Effect[int, Never, Never])
assert_type(PreventSchedulerYield().use(), Effect[bool, Never, Never])


class EagerScheduler:
    execution_mode: Literal["sync"] = "sync"

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        return fiber.current_op_count > 64

    def make_dispatcher(self) -> SchedulerDispatcher:
        return MixedScheduler("sync").make_dispatcher()


eager: SchedulerService = EagerScheduler()
budget = Scheduler.context(eager) | MaxOpsBeforeYield.context(64)
assert_type(provide(budget)(find(1)), Effect[str, NotFound, Never])
assert_type(
    with_dispatcher(lambda d: sync(d.flush).flat_map(lambda _: find(1))),
    Effect[str, NotFound, Never],
)


def parity_application_types() -> None:
    req = parity.UserRequest(user_id=1)
    assert_type(parity.AppLive, Layer[parity.Greeter | parity.Auditor])
    assert_type(
        parity.handle({"user_id": 1}, parity.greet_user),
        Effect[str, parity.UserNotFound | ValidationError, Never],
    )
    assert_type(parity.greet_or_guest(req), Effect[str, Never, parity.Greeter])
    replacement = Layer.succeed(
        parity.Users, parity.UsersService(lambda uid: succeed(parity.User(uid, "test-user")))
    )
    assert_type(
        parity.handle(
            {"user_id": 1}, parity.greet_user, parity.make_app(replacement, parity.NotifierLive)
        ),
        Effect[str, parity.UserNotFound | ValidationError, Never],
    )
    # Unlike TS catchTag, a nonmatching class is allowed, but it cannot erase the real error.
    assert_type(
        parity.greet_user(req).pipe(catch_tag(ValueError, lambda _: succeed("guest"))),
        Effect[str, parity.UserNotFound, parity.Greeter],
    )
    assert_type(
        composed.place_or_pending(composed.Order(1)),
        Effect[composed.Receipt, parity.UserNotFound | composed.OutOfStock, composed.Checkout],
    )
    assert_type(
        provide_layer(composed.make_checkout_app())(composed.place_or_pending(composed.Order(1))),
        Effect[composed.Receipt, parity.UserNotFound | composed.OutOfStock, Never],
    )


def lifecycle_types() -> None:
    from peffect import fork_in, run_fork, run_fork_with, run_in

    owner = Scope.make_unsafe()
    root = run_fork(succeed(1))
    assert_type(root, Fiber[int, Never])
    assert_type(run_in(root, owner), Fiber[int, Never])
    assert_type(fork_in(Users().use(), owner), Effect[Fiber[Repo, Never], Never, Users])
    runner = run_fork_with(Users.context(Repo()))
    assert_type(runner(Users().use()), Fiber[Repo, Never])
    assert_type(runner(succeed(1)), Fiber[int, Never])


async def managed_runtime_types(
    both: ManagedRuntime[Users | Log, KeyError], narrow_error: ManagedRuntime[Users, KeyError]
) -> None:
    rt = ManagedRuntime.make(Layer.succeed(Users, Repo()))
    assert_type(rt, ManagedRuntime[Users, Never])
    assert_type(rt.run_fork(Users().use()), Fiber[Repo, Never])
    assert_type(rt.run_fork(succeed(1)), Fiber[int, Never])
    assert_type(await rt.run_exit(Users().use()), Exit[Repo, Never])
    assert_type(await rt.run(Users().use()), Repo)
    assert_type(await rt.context(), Context[Users])
    assert_type(rt.context_effect, Effect[Context[Users], Never, Never])
    assert_type(rt.dispose_effect, Effect[None, Never, Never])
    assert_type(both.run_fork(Users().use()), Fiber[Repo, KeyError])
    # R is contravariant: a runtime with more services serves an effect needing fewer.
    _fewer: ManagedRuntime[Users, KeyError] = both
    # ER is covariant: a build error widens.
    _wider: ManagedRuntime[Users, LookupError] = narrow_error
    async with rt as entered:
        assert_type(entered, ManagedRuntime[Users, Never])


class ComposeBoom(Exception): ...


class ComposeOther(Exception): ...


class ComposeA(Tag[str], key="snip/A"): ...


class ComposeB(Tag[int], key="snip/B"): ...


def composition_ok(p: Effect[int, ComposeBoom, ComposeA]) -> None:
    assert_type(p.pipe(), Effect[int, ComposeBoom, ComposeA])
    assert_type(
        p.pipe(lambda e: e.map(str), lambda e: e.map(len)), Effect[int, ComposeBoom, ComposeA]
    )
    assert_type(p.pipe(lambda _: "x", len), int)
    assert_type(
        p.tap(lambda _: ComposeB().use().flat_map(lambda _: fail(ComposeOther()))),
        Effect[int, ComposeBoom | ComposeOther, ComposeA | ComposeB],
    )
    assert_type(p.tap(sync(lambda: 1)), Effect[int, ComposeBoom, ComposeA])
    assert_type(
        p.pipe(tap(lambda a: succeed(a)), tap(ComposeB().use())),
        Effect[int, ComposeBoom, ComposeA | ComposeB],
    )
    twenty = p.pipe(
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
        lambda e: e,
    )
    assert_type(twenty, Effect[int, ComposeBoom, ComposeA])


def option_ok(s: Some[int], o: Option[int], e: Effect[Option[int], ValueError, Never]) -> None:
    _s: Some[object] = s
    _o: Option[object] = o
    _e: Effect[Option[object], Exception, object] = e


class PipeLayerA(Tag[str], key="snip/PipeLayerA"): ...


class PipeLayerC(Tag[float], key="snip/PipeLayerC"): ...


type PipeLayerType = Layer[PipeLayerA, Never, Never]


def layer_identity(layer: PipeLayerType) -> PipeLayerType:
    return layer


def layer_pipe_types(base: PipeLayerType, c: Layer[PipeLayerC]) -> None:
    assert_type(base.pipe(), PipeLayerType)
    assert_type(base.pipe(Layer.merge(c)), Layer[PipeLayerA | PipeLayerC, Never, Never])
    assert_type(base.pipe(lambda layer: (layer, 1), lambda t: t[1]), int)
    assert_type(
        base.pipe(
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
            layer_identity,
        ),
        PipeLayerType,
    )


# --- racing and timeouts: one type variable per channel joins both sides ---


class RaceA(Tag[str], key="typetests/RaceA"): ...


class RaceB(Tag[int], key="typetests/RaceB"): ...


class RaceErrA(Exception): ...


class RaceErrB(Exception): ...


def race_left() -> Effect[int, RaceErrA, RaceA]:
    return fail(RaceErrA())


def race_right() -> Effect[str, RaceErrB, RaceB]:
    return fail(RaceErrB())


def race_pure() -> Effect[bytes, Never, Never]:
    return succeed(b"")


type RaceJoined = Effect[int | str, RaceErrA | RaceErrB, RaceA | RaceB]


def race_types() -> None:
    assert_type(race(race_left(), race_right()), RaceJoined)
    assert_type(race_first(race_left(), race_right()), RaceJoined)
    assert_type(race_all([race_left(), race_right()]), RaceJoined)
    assert_type(race_all_first((race_left(), race_right())), RaceJoined)
    assert_type(race(race_pure(), race_pure()), Effect[bytes, Never, Never])
    assert_type(race(race_pure(), race_left()), Effect[bytes | int, RaceErrA, RaceA])
    assert_type(race(never, never), Effect[Never, Never, Never])


def timeout_types() -> None:
    assert_type(timeout_option(race_left(), "1 second"), Effect[Option[int], RaceErrA, RaceA])
    assert_type(timeout(race_left(), 10), Effect[int, RaceErrA | TimeoutError, RaceA])
    assert_type(timeout(race_pure(), 10), Effect[bytes, TimeoutError, Never])
    assert_type(
        timeout_or_else(race_left(), duration=10, or_else=race_right),
        Effect[int | str, RaceErrA | RaceErrB, RaceA | RaceB],
    )
    assert_type(
        timeout_or_else(race_pure(), duration=10, or_else=lambda: succeed(1)),
        Effect[bytes | int, Never, Never],
    )


def scoped_fork_types() -> None:
    # fork_scoped adds Scope to R; scoped discharges it
    assert_type(fork_scoped(race_left()), Effect[Fiber[int, RaceErrA], Never, RaceA | Scope])
    assert_type(scoped(fork_scoped(race_pure())), Effect[Fiber[bytes, Never], Never, Never])


def console_logger_types() -> None:
    # Console and the logger References never add to R
    assert_type(Console.log("a", 1), Effect[None, Never, Never])
    assert_type(Console.Console().use(), Effect[Console.ConsoleService, Never, Never])
    assert_type(EffectLogger.log_warning("a", 1), Effect[None, Never, Never])
    assert_type(EffectLogger.log_with_level("Debug")("a"), Effect[None, Never, Never])
    assert_type(
        EffectLogger.CurrentLoggers().use(), Effect[tuple[EffectLogger.Logger, ...], Never, Never]
    )
    assert_type(TestConsole.log_lines, Effect[tuple[object, ...], Never, Never])
    assert_type(TestConsole.test_console_with(lambda _: race_left()), Effect[int, RaceErrA, RaceA])


def testclock_module_types() -> None:
    # make needs the Scope its warning is stopped by; the layer provides Clock and Scheduler
    assert_type(TestClock.make(), Effect[TestClockService, Never, Scope])
    assert_type(TestClock.layer(warning_delay="10 millis"), Layer[Clock | Scheduler])
    assert_type(provide_layer(TestClock.layer())(sleep(1)), Effect[None, Never, Never])
    assert_type(TestClock.adjust("1 second"), Effect[None, Never, Never])
    assert_type(TestClock.set_time(1.5), Effect[None, Never, Never])
    assert_type(TestClock.with_live(race_left()), Effect[int, RaceErrA, RaceA])
    assert_type(TestClock.test_clock_with(lambda _: race_left()), Effect[int, RaceErrA, RaceA])


def ref_type_contracts() -> None:
    from peffect import Ref, SynchronizedRef
    from peffect.option import Option, Some

    ref = Ref[int | None].make_unsafe(1)
    assert_type(ref, Ref[int | None])
    assert_type(Ref[int | None].make(None), Effect[Ref[int | None], Never, Never])
    ref.set(None)
    ref.update_some(lambda _: Some(None))
    sref = SynchronizedRef[int | None].make_unsafe(None)
    assert_type(sref, SynchronizedRef[int | None])
    assert_type(
        SynchronizedRef[int | None].make(1), Effect[SynchronizedRef[int | None], Never, Never]
    )

    # Declared error and environment channels must survive each effectful method.
    def checked(
        eff: Effect[int, str, Users],
        opt: Effect[Option[int], str, Users],
        pair: Effect[tuple[str, int], str, Users],
        partial: Effect[tuple[str, Option[int]], str, Users],
    ) -> None:
        r = SynchronizedRef[int].make_unsafe(1)
        assert_type(r.get_and_update_effect(lambda _: eff), Effect[int, str, Users])
        assert_type(r.get_and_update_some_effect(lambda _: opt), Effect[int, str, Users])
        assert_type(r.modify_effect(lambda _: pair), Effect[str, str, Users])
        assert_type(r.modify_some_effect(lambda _: partial), Effect[str, str, Users])
        assert_type(r.update_effect(lambda _: eff), Effect[None, str, Users])
        assert_type(r.update_and_get_effect(lambda _: eff), Effect[int, str, Users])
        assert_type(r.update_some_effect(lambda _: opt), Effect[None, str, Users])
        assert_type(r.update_some_and_get_effect(lambda _: opt), Effect[int, str, Users])

    assert callable(checked)


def queue_prerequisite_type_contracts() -> None:
    from peffect.pull import catch_done, done_exit_from_cause, filter_done

    from peffect import Done, done, suspend_exit

    assert_type(Done(), Done[None])
    assert_type(Done("leftover"), Done[str])
    assert_type(done(), Effect[Never, Done[None], Never])
    assert_type(done("leftover"), Effect[Never, Done[str], Never])

    def checked(
        pure_done: Effect[int, Done[str], RaceA],
        mixed: Effect[int, NotFound | Done[str], RaceA],
        ordinary: Effect[int, NotFound, RaceA],
        success: Effect[int, Never, RaceA],
        cause: Cause[NotFound | Done[str]],
    ) -> None:
        def recover(leftover: str) -> Effect[float, Timeout, Users]:
            return fail(Timeout(leftover))

        assert_type(suspend_exit(lambda: mixed), Effect[int, NotFound | Done[str], RaceA])
        assert_type(catch_done(pure_done, recover), Effect[int | float, Timeout, RaceA | Users])
        assert_type(
            catch_done(mixed, recover), Effect[int | float, NotFound | Timeout, RaceA | Users]
        )
        assert_type(
            catch_done(ordinary, recover), Effect[int | float, NotFound | Timeout, RaceA | Users]
        )
        assert_type(catch_done(success, recover), Effect[int | float, Timeout, RaceA | Users])
        assert_type(filter_done(cause), Exit[Done[str], NotFound])
        assert_type(done_exit_from_cause(cause), Exit[str, NotFound])
        assert_type(done_exit_from_cause(Cause.fail(Done("left"))), Exit[str, Never])

    assert callable(checked)
