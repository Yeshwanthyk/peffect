"""Each block is a mistake pyright must reject. Checked by tests/test_types.py."""

from typing import Any, Never

import peffect
from examples.parity import python_app as parity
from examples.parity import python_composed as composed
from peffect import (
    Context,
    Deferred,
    Effect,
    Exit,
    Fiber,
    Layer,
    ManagedRuntime,
    MemoMap,
    Reference,
    Scope,
    ScopeService,
    Success,
    Tag,
    acquire_release,
    acquire_use_release,
    add_finalizer,
    await_all,
    catch_tag,
    fail,
    for_each,
    fork,
    fork_daemon,
    fork_scoped,
    gen,
    provide,
    provide_layer,
    race,
    race_all,
    race_all_first,
    race_first,
    scoped,
    succeed,
    sync,
    timeout,
    timeout_option,
    timeout_or_else,
)
from peffect.clock import current_time_millis
from peffect.logger import log_with_level
from peffect.option import Option, Some
from peffect.scheduler import MaxOpsBeforeYield, MixedScheduler, SchedulerService, with_dispatcher
from peffect.testing import TestClock, TestClockService


class NotFound(Exception): ...


class Other(Exception): ...


def find(uid: int) -> Effect[str, NotFound, Never]:
    return fail(NotFound())


async def main() -> None:
    # 1. UNHANDLED_ERROR: run needs E = Never
    await peffect.run(find(1))


# 2. WRONG_TAG_LEFT: catching Other leaves NotFound in E
_still: Effect[str, Never, Never] = find(1).pipe(catch_tag(Other, lambda _: succeed("")))


@gen
def prog():
    name = yield from find(1)
    return name


# 3. GEN_ERROR_LOST: @gen kept NotFound in E
_lost: Effect[str, Never, Never] = prog()

# 4. WRONG_VALUE_TYPE: A is str
_n: Effect[int, NotFound, Never] = find(1)


class Repo: ...


class Users(Tag[Repo], key="typetests/Users"): ...


class Log(Tag[str], key="typetests/Log"): ...


class Level(Reference[str], key="typetests/Level"):
    @classmethod
    def default(cls) -> str:
        return "info"


@gen
def needs_two():
    repo = yield from Users()
    log = yield from Log()
    return repo, log


async def missing() -> None:
    # 5. MISSING_AT_RUN: run needs R = Never
    await peffect.run(needs_two())


async def missing_exit() -> None:
    # 6. MISSING_AT_RUN_EXIT: run_exit needs R = Never too
    await peffect.run_exit(needs_two())


async def partial() -> None:
    # 7. PARTIAL_STILL_MISSING: Log was not provided
    await peffect.run(provide(Users.context(Repo()))(needs_two()))


# 8. WRONG_IMPL: Users holds a Repo
Users.context(42)

# 9. WRONG_REFERENCE_OVERRIDE: Level holds a str
Level.context(3)

# 10. CONTEXT_CLAIMS_MORE: a Context[Users] does not hold Log
_claims: Context[Users | Log] = Users.context(Repo())

# 11. FORGED_CONTEXT: no public constructor
_forged: Context[Users] = Context({}, None)


def assign(fiber: Fiber[Any, Any]) -> None:
    # 12. CONTEXT_ASSIGNED: Fiber.context is read-only
    fiber.context = {}


def delete(fiber: Fiber[Any, Any]) -> None:
    # 13. CONTEXT_DELETED: Fiber.context is read-only
    del fiber.context


def write_item(fiber: Fiber[Any, Any]) -> None:
    # 14. CONTEXT_ITEM_WRITTEN: the mapping is read-only
    fiber.context[Users.key] = Repo()


def replace_services(ctx: Context[Users]) -> None:
    # 15. SERVICES_REPLACED: cannot invalidate the promised dependency
    ctx.services = {}


def delete_services(ctx: Context[Users]) -> None:
    # 16. SERVICES_DELETED: cannot remove the promised dependency
    del ctx.services


def overwrite_provider(alias: provide[Never]) -> None:
    # 17. PROVIDER_ALIAS_REPLACED: widened aliases cannot change provision
    alias.ctx = Log.context("wrong tag")


def delete_provider(alias: provide[Users]) -> None:
    # 18. PROVIDER_DELETED: provision is immutable
    del alias.ctx


async def unscoped() -> None:
    # 19. SCOPE_UNPROVIDED: `scope` requires Scope; run needs R = Never
    await peffect.run(peffect.scope)


def close_service(service: ScopeService) -> None:
    # 20. SERVICE_CLOSED: only the Closeable's maker can close it
    Scope.close(service, Success(None))


def finalizer_requires(service: ScopeService) -> None:
    # 21. FINALIZER_REQUIRES: a finalizer's R is Never
    Scope.add_finalizer(service, Users().use())


def strategy_replaced(service: ScopeService) -> None:
    # 22. STRATEGY_REPLACED: strategy is read-only
    service.strategy = "parallel"


async def resource_without_scope() -> None:
    # 23. RESOURCE_SCOPE_MISSING: only Scope remains after supplying the release dependency
    await peffect.run(provide(Log.context("log"))(
        acquire_release(succeed(1), lambda _a, _ex: Log().use())
    ))


async def resource_without_release_dependency() -> None:
    # 24. RESOURCE_RELEASE_DEPENDENCY_MISSING: scoped removes Scope, not the release's Log
    await peffect.run(scoped(acquire_release(succeed(1), lambda _a, _ex: Log().use())))


async def finalizer_without_scope() -> None:
    # 25. FINALIZER_SCOPE_MISSING: supplying Log leaves Scope outstanding
    await peffect.run(provide(Log.context("log"))(
        add_finalizer(lambda _ex: Log().use().as_(None))
    ))


async def finalizer_without_dependency() -> None:
    # 26. FINALIZER_DEPENDENCY_MISSING: registration requires its finalizer's services
    await peffect.run(scoped(add_finalizer(lambda _ex: Log().use().as_(None))))


# 27. RELEASE_ERROR_LOST: acquire_use_release includes the release's error channel
_release_lost: Effect[int, Never, Never] = acquire_use_release(
    succeed(1), lambda a: succeed(a), lambda _a, _ex: fail(Other())
)


async def use_release_without_dependency() -> None:
    # 28. USE_RELEASE_DEPENDENCY_MISSING: release services are required without a Scope
    await peffect.run(acquire_use_release(
        succeed(1), lambda a: succeed(a), lambda _a, _ex: Log().use().as_(None)
    ))


# 29. RESOURCE_VALUE_LOST: acquire_release keeps the acquired value type
_wrong_resource: Effect[str, Never, Scope] = acquire_release(
    succeed(1), lambda _a, _ex: succeed(None)
)


_deferred = Deferred[int, NotFound].make_unsafe()

# 30. DEFERRED_WRONG_VALUE: completion cannot change the cell's A
_deferred.succeed("wrong")

# 31. DEFERRED_WRONG_ERROR: completion cannot change the cell's E
_deferred.fail(Other())

# 32. DEFERRED_VALUE_INVARIANT: widening A would permit writing the wrong value
_wide_value: Deferred[object, NotFound] = _deferred

# 33. DEFERRED_ERROR_INVARIANT: widening E would permit writing the wrong error
_wide_error: Deferred[int, Exception] = _deferred

# 34. DEFERRED_COMPLETION_REQUIRES_SERVICE: awaiters need no environment
_deferred.done_unsafe(Users().use().as_(1))

# 35. DEFERRED_STORED_EFFECT_REQUIRES_SERVICE: direct storage cannot bypass that promise
_deferred.effect = Users().use().as_(1)

# 36. DEFERRED_COMPLETION_WRONG_ERROR: direct completion retains the declared E
_deferred.done_unsafe(fail(Other()))

# 37. DEFERRED_COMPLETION_WRONG_VALUE: direct completion retains the declared A
_deferred.done_unsafe(succeed("wrong"))

# 38. DEFERRED_DONE_REQUIRES_EXIT: lazy Effects use done_unsafe, not the Exit-only wrapper
_deferred.done(sync(lambda: 1))

# 39. DEFERRED_AWAIT_ERROR_LOST: await preserves the cell's declared E
_deferred_error_lost: Effect[int, Never, Never] = _deferred.await_()



def deferred_value_cannot_narrow(cell: Deferred[object, NotFound]) -> None:
    # 40. DEFERRED_VALUE_NOT_CONTRAVARIANT: a broader cell cannot promise narrower reads
    _narrow: Deferred[int, NotFound] = cell


def deferred_error_cannot_narrow(cell: Deferred[int, Exception]) -> None:
    # 41. DEFERRED_ERROR_NOT_CONTRAVARIANT: broader failures cannot become NotFound-only
    _narrow: Deferred[int, NotFound] = cell



def bulk_channels_cannot_disappear(target: Fiber[int, NotFound]) -> None:
    # 42. BULK_EXIT_ERROR_LOST: target errors remain in each Exit
    _lost: Effect[list[Exit[int, Never]], Never, Never] = await_all([target])
    # 43. BULK_EXIT_VALUE_LOST: ordered results preserve their value channel
    _wrong: Effect[list[Exit[str, NotFound]], Never, Never] = await_all([target])


async def inherited_fork_still_needs_its_environment() -> None:
    # 44. INHERITED_FORK_DEPENDENCY_MISSING: inheriting a mask does not erase R
    await peffect.run(fork(Users().use(), uninterruptible="inherit"))
    # 45. INHERITED_DAEMON_DEPENDENCY_MISSING: detach also preserves R
    await peffect.run(fork_daemon(Users().use(), uninterruptible="inherit"))


# 46. FORK_UNSUPPORTED_BOOLEAN: boolean modes are not yet supported
fork(succeed(1), uninterruptible=True)
# 47. DAEMON_UNSUPPORTED_BOOLEAN: False is not an alias for the supported default None
fork_daemon(succeed(1), uninterruptible=False)
# 48. FORK_UNSUPPORTED_MODE: accept only the tested upstream inherit option
fork(succeed(1), uninterruptible="masked")
# 49. INHERITED_FORK_ERROR_LOST: a fork retains the worker's E
_wrong_worker: Effect[Fiber[str, Never], Never, Never] = fork(find(1), uninterruptible="inherit")


async def traversal_still_needs_its_environment() -> None:
    # 50. TRAVERSAL_DEPENDENCY_MISSING: concurrent traversal keeps each mapper's R
    await peffect.run(for_each([1, 2], lambda _uid, _i: Users().use(), concurrency=2))


# 51. TRAVERSAL_ERROR_LOST: the mapper's E stays in the traversal's error channel
_traversal_error_lost: Effect[list[str], Never, Never] = for_each([1], lambda uid, _i: find(uid))
# 52. TRAVERSAL_VALUE_LOST: results are a list of the mapper's B, not of the items
_traversal_value_lost: Effect[list[int], NotFound, Never] = for_each([1], lambda u, _i: find(u))
# 53. TRAVERSAL_UNSUPPORTED_CONCURRENCY: only an int or "unbounded"
for_each([1], lambda u, _i: find(u), concurrency="inherit")


class Db(Tag[int], key="typetests/Db"): ...


class Timeout(Exception): ...


def slow() -> Effect[int, Timeout, Never]:
    return succeed(1)


async def layer_dependencies(
    users_from_db: Layer[Users, NotFound, Db], db: Layer[Db, Timeout]
) -> None:
    # 54. LAYER_DEPENDENCY_MISSING: the layer's own RIn stays in the provided effect
    await peffect.run(provide_layer(users_from_db)(Users().use()).catch_all(lambda _: succeed(1)))
    # 55. LAYER_EFFECT_MISSING_ITS_LAYER: providing Db does not satisfy Users
    await peffect.run(provide_layer(db)(Users().use()).catch_all(lambda _: succeed(1)))


def layer_provide_keeps_channels(
    users_from_db: Layer[Users, NotFound, Db],
    needs_db_log: Layer[Users, NotFound, Db | Log],
    db: Layer[Db, Timeout],
) -> None:
    # 56. LAYER_PROVIDE_DROPS_UNRELATED: provide removes Db only; Log stays
    _kept: Layer[Users, NotFound | Timeout, Never] = needs_db_log.pipe(Layer.provide(db))
    # 57. LAYER_ERROR_LOST: the provider's Timeout joins E
    _lost: Layer[Users, NotFound, Never] = users_from_db.pipe(Layer.provide(db))
    # 58. LAYER_PROVIDED_EFFECT_ERROR_LOST: provide_layer adds the layer's E
    _effect_lost: Effect[int, Never, Never] = provide_layer(db)(Db().use())


def layer_services(repo: Repo, users: Layer[Users]) -> None:
    # 59. LAYER_WRONG_SERVICE: Users holds a Repo
    Layer.succeed(Users, 1)
    # 60. LAYER_WRONG_EFFECT_SERVICE: the effect must produce the tag's service
    Layer.effect(Users, succeed(1))
    # 61. LAYER_WRONG_OUTPUT: users provides Users, not Log
    _log: Layer[Log] = users
    # 62. LAYER_SCOPE_REMOVES_ONLY_SCOPE: the effect's other R survives Scope's removal
    _db: Layer[Db, Timeout, Never] = Layer.effect(
        Db,
        acquire_release(slow(), lambda _n, _ex: succeed(None)).flat_map(
            lambda n: Users().use().as_(n)
        ),
    )
    # 63. LAYER_BUILD_NEEDS_SCOPE: build ties the layer's resources to the caller's Scope
    _built: Effect[Context[Users], Never, Never] = Layer.build(users)


def layer_aliases(
    users: Layer[Users], failing: Layer[Users, NotFound], needy: Layer[Users, Never, Db]
) -> None:
    # 64. LAYER_OUTPUT_WIDENED_BY_ALIAS: a Users layer cannot stand for one that also provides Db
    _more: Layer[Users | Db] = users
    # 65. LAYER_ERROR_NARROWED_BY_ALIAS: E is not erased by assignment
    _safe: Layer[Users] = failing
    # 66. LAYER_DEPENDENCY_NARROWED_BY_ALIAS: RIn is not erased by assignment
    _free: Layer[Users] = needy


def layer_construction(repo: Repo, users: Layer[Users], mm: MemoMap, scope: ScopeService) -> None:
    # 67. LAYER_CONSTRUCTOR_FORGED: only Layer's own constructors can make one
    Layer(lambda _m, _s: succeed(Users.context(repo)), object())
    # 68. LAYER_BUILDER_REPLACED: a built layer's builder is fixed
    users._builder = lambda _m, _s: succeed(Users.context(repo))
    # 69. MERGE_ALL_EMPTY: upstream mergeAll takes at least one layer
    Layer.merge_all()
    # 70. MEMO_GET_WRONG_OUTPUT: get keeps the layer's ROut
    _got: Effect[Context[Log], Never, Never] | None = mm.get(users, scope)


class NoDispatcher:
    execution_mode = "async"

    def should_yield(self, fiber: Fiber[Any, Any]) -> bool:
        return False


def scheduler_mistakes() -> None:
    # 71. SCHEDULER_WITHOUT_DISPATCHER: a scheduler must make the dispatcher a fiber queues on
    _s: SchedulerService = NoDispatcher()
    # 72. SCHEDULER_MODE_UNKNOWN: execution_mode is "sync" or "async"
    MixedScheduler("eager")
    # 73. MAX_OPS_NOT_INT: the op budget is an int
    MaxOpsBeforeYield.context("16")
    # 74. WITH_DISPATCHER_NOT_EFFECT: f returns the effect to run
    with_dispatcher(lambda d: d.flush())


async def parity_application_mistakes(
    partial: Layer[parity.Users, Never, parity.Repo],
) -> None:
    req = parity.UserRequest(user_id=1)
    # 75. PARITY_MISSING_DEPENDENCY: Greeter must be provided before running
    await peffect.run_exit(parity.greet_user(req))
    # 76. PARITY_LAYER_MISSING_DEPENDENCY: this Users layer still needs Repo
    parity.make_app(partial, parity.NotifierLive)
    # 77. PARITY_WRONG_IMPLEMENTATION: get returns User, not str
    Layer.succeed(parity.Users, parity.UsersService(lambda _: succeed("ann")))
    # 78. PARITY_UNDECLARED_ERROR: get can fail only with UserNotFound
    Layer.succeed(parity.Users, parity.UsersService(lambda _: fail(ValueError("db down"))))
    # 79. PARITY_WRONG_CATCH_LEAVES_ERROR: a nonmatching catch cannot erase UserNotFound
    _safe: Effect[str, Never, parity.Greeter] = parity.greet_user(req).pipe(
        catch_tag(ValueError, lambda _: succeed("guest"))
    )
    # 80. PARITY_FORGED_SERVICE: RepoService cannot stand in for UsersService
    parity.Users.context(parity.RepoService(lambda _: None))
    # 81. COMPOSED_REMAINING_ERRORS: recovering payment failure keeps user and stock failures
    _no_errors: Effect[composed.Receipt, Never, composed.Checkout] = composed.place_or_pending(
        composed.Order(1)
    )
    # 82. COMPOSED_MISSING_SERVICE: Checkout must still be provided
    await peffect.run_exit(composed.place_or_pending(composed.Order(1)))
    # 83. COMPOSED_WRONG_PAYMENT_ERROR: stock errors do not belong to Payments
    Layer.succeed(composed.Payments, composed.PaymentsService(
        lambda _: fail(composed.OutOfStock("book"))
    ))

def lifecycle_mistakes() -> None:
    owner = Scope.make_unsafe()
    # 84. ROOT_MISSING_SERVICE: roots must have every requirement supplied
    peffect.run_fork(Users().use())
    # 85. ROOT_CONTEXT_MISSING_SERVICE: supplied Users cannot satisfy Log
    peffect.run_fork_with(Users.context(Repo()))(Log().use())
    # 86. FORK_SCOPE_MASK: only bool or inherit is supported
    peffect.fork_in(succeed(1), owner, uninterruptible="masked")
    # 87. FORK_SCOPE_RETAINS_R: attaching a scope does not provide services
    _lost: Effect[Fiber[Repo, Never], Never, Never] = peffect.fork_in(Users().use(), owner)
    # 88. ROOT_HOOK_SIGNATURE: the hook receives the started fiber
    peffect.run_fork(succeed(1), on_fiber_start=lambda: None)


def managed_runtime_mistakes(
    users_rt: ManagedRuntime[Users, Never], wide: ManagedRuntime[Users, LookupError]
) -> None:
    # 89. MANAGED_RUNTIME_MISSING_SERVICE: the runtime provides Users, not Log
    users_rt.run_fork(Log().use())
    # 90. MANAGED_RUNTIME_R_NOT_COVARIANT: a runtime without Log cannot stand in for one with it
    _more: ManagedRuntime[Users | Log, Never] = users_rt
    # 91. MANAGED_RUNTIME_ER_NOT_CONTRAVARIANT: a build error cannot narrow
    _narrow: ManagedRuntime[Users, KeyError] = wide
    # 92. MANAGED_RUNTIME_LAYER_INPUT: the layer's own requirements must already be provided
    ManagedRuntime.make(Layer.effect(Users, Log().use().as_(Repo())))
    # 93. MANAGED_RUNTIME_READ_ONLY_CACHE: only the runtime writes its cached context
    users_rt.cached_context = None


class ComposeBoom(Exception): ...


class ComposeOther(Exception): ...


class ComposeA(Tag[str], key="snip/A"): ...


class ComposeB(Tag[int], key="snip/B"): ...


def composition_mistakes(p: Effect[int, ComposeBoom, ComposeA]) -> None:
    # 94. TAP_NOT_AN_EFFECT: tap's function must return an Effect
    p.tap(lambda a: a + 1)
    # 95. TAP_WRONG_VALUE: tap's function receives the success value, here an int
    p.tap(lambda a: a.upper())
    # 96. TAP_NOT_CALLABLE_OR_EFFECT: a plain value is neither form of tap
    p.tap(1)
    # 97. TAP_ERROR_LOST: a failing tap widens E
    _e: Effect[int, ComposeBoom, ComposeA] = p.tap(lambda _: fail(ComposeOther()))
    # 98. TAP_REQUIREMENT_LOST: a tapped effect's services widen R
    _r: Effect[int, ComposeBoom, ComposeA] = p.tap(lambda _: ComposeB().use())
    # 99. PIPE_STEP_MISMATCH: each step receives the previous step's output (str, not int)
    p.pipe(lambda e: e.map(str), lambda e: e.map(lambda s: s + 1))
    # 100. PIPE_TOO_LONG: 21 steps exceed the 20 overloads (upstream's cap); nest a second pipe
    p.pipe(
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
        lambda e: e,
    )


def option_mistakes(s: Some[object], o: Option[object], i: Some[int]) -> None:
    # 101. SOME_UNSAFE_NARROWING
    _s: Some[int] = s
    # 102. OPTION_UNSAFE_NARROWING
    _o: Option[int] = o
    # 103. SOME_IMMUTABLE
    i.value = 2


class PipeLayerA(Tag[str], key="snip/PipeLayerA"): ...


class PipeLayerC(Tag[float], key="snip/PipeLayerC"): ...


type PipeLayerType = Layer[PipeLayerA, Never, Never]


def layer_identity(layer: PipeLayerType) -> PipeLayerType:
    return layer


def layer_pipe_mistakes(base: PipeLayerType) -> None:
    # 104. LAYER_PIPE_STEP_MISMATCH: each step receives the previous step's output
    base.pipe(lambda layer: len(str(layer)), lambda n: n.upper())
    # 105. LAYER_PIPE_TOO_LONG: 21 steps exceed the 20 overloads, as for Effect.pipe
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
        layer_identity,
    )


class RaceA(Tag[str], key="snip/RaceA"): ...


class RaceB(Tag[int], key="snip/RaceB"): ...


class RaceErrA(Exception): ...


class RaceErrB(Exception): ...


def race_left() -> Effect[int, RaceErrA, RaceA]:
    return fail(RaceErrA())


def race_right() -> Effect[str, RaceErrB, RaceB]:
    return fail(RaceErrB())


async def race_and_timeout_mistakes() -> None:
    # 106. RACE_VALUE_LOST: the joined value keeps the right side's str
    _value: Effect[int, RaceErrA | RaceErrB, RaceA | RaceB] = race(race_left(), race_right())
    # 107. RACE_ERROR_LOST: the joined error keeps the right side's RaceErrB
    _error: Effect[int | str, RaceErrA, RaceA | RaceB] = race_first(race_left(), race_right())
    # 108. RACE_REQUIREMENT_LOST: the joined requirements keep RaceB
    _needs: Effect[int | str, RaceErrA | RaceErrB, RaceA] = race_all([race_left(), race_right()])
    # 109. RACE_ALL_FIRST_UNHANDLED: run needs every racer's error handled and service provided
    await peffect.run(race_all_first([race_left(), race_right()]))
    # 110. TIMEOUT_ERROR_LOST: timeout adds TimeoutError to the error channel
    _timed: Effect[int, RaceErrA, RaceA] = timeout(race_left(), 10)
    # 111. TIMEOUT_OPTION_UNWRAPPED: timeout_option yields an Option, not the bare value
    _bare: Effect[int, RaceErrA, RaceA] = timeout_option(race_left(), 10)
    # 112. TIMEOUT_OR_ELSE_FALLBACK_LOST: the fallback's value, error and service join the result
    _fallback: Effect[int, RaceErrA, RaceA] = timeout_or_else(
        race_left(), duration=10, or_else=race_right
    )
    # 113. TIMEOUT_OR_ELSE_EAGER: or_else is a thunk, not an effect
    timeout_or_else(race_left(), duration=10, or_else=race_right())


def scoped_fork_and_test_clock_mistakes() -> None:
    # 114. FORK_SCOPED_SCOPE_DROPPED: fork_scoped needs the Scope that will interrupt the fiber
    _unscoped: Effect[Fiber[int, RaceErrA], Never, RaceA] = fork_scoped(race_left())
    # 115. TEST_CLOCK_MAKE_SCOPE_DROPPED: make registers a finalizer, so it needs a Scope
    _clock: Effect[TestClockService, Never, Never] = TestClock.make()
    # 116. LOG_LEVEL_UNKNOWN: levels are the upstream names, "Warn" not "Warning"
    log_with_level("Warning")
    # 117. TEST_CLOCK_WITH_LIVE_ERROR_LOST: with_live keeps the wrapped effect's error
    _live: Effect[int, Never, RaceA] = TestClock.with_live(race_left())
    # 118. CLOCK_MILLIS_NOT_INT: current_time_millis is a float; a TestClock stands between millis
    _millis: Effect[int, Never, Never] = current_time_millis


def emission_rejects_filter_sentinels() -> None:
    # 119. LOG_ALL_NOT_SEVERITY: All is a minimum filter, not an emitted severity
    log_with_level("All")
    # 120. LOG_NONE_NOT_SEVERITY: None is a minimum filter, not an emitted severity
    log_with_level("None")


def ref_type_mistakes() -> None:
    from peffect import Ref, SynchronizedRef

    # 121. REF_INVARIANCE
    _ref_widened: Ref[object] = Ref[int].make_unsafe(1)
    # 122. SYNC_REF_INVARIANCE
    _sync_widened: SynchronizedRef[object] = SynchronizedRef[int].make_unsafe(1)
    # 123. REF_BARE_UNSAFE
    _bare_ref = Ref.make_unsafe(1)
    # 124. REF_BARE_MAKE
    _bare_ref_effect = Ref.make(1)
    # 125. SYNC_REF_BARE_UNSAFE
    _bare_sync = SynchronizedRef.make_unsafe(1)
    # 126. SYNC_REF_BARE_MAKE
    _bare_sync_effect = SynchronizedRef.make(1)
