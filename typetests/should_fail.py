"""Each block is a mistake pyright must reject. Checked by tests/test_types.py."""

from typing import Any, Never

import peffect
from peffect import Context, Effect, Fiber, Reference, Tag, catch_tag, fail, gen, provide, succeed


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
