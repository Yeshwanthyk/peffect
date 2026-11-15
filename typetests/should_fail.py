"""Each block is a mistake pyright must reject. Checked by tests/test_types.py."""

from typing import Any, Never

import peffect
from peffect import (
    Context,
    Deferred,
    Effect,
    Exit,
    Fiber,
    Layer,
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
    gen,
    provide,
    provide_layer,
    scoped,
    succeed,
    sync,
)


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


class Users(Tag[Repo]): ...


class Log(Tag[str]): ...


class Level(Reference[str]):
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
    fiber.context[Users] = Repo()


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


class Db(Tag[int]): ...


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
