"""Services with Tag, Reference and provide, using only the public API.

uv run python examples/context.py
"""

import asyncio
from dataclasses import dataclass

from peffect import (
    Reference,
    Tag,
    catch_tag,
    fail,
    fork,
    gen,
    provide,
    run,
    succeed,
)


@dataclass
class UserRepo:
    names: dict[int, str]


@dataclass
class NotFound(Exception):
    user_id: int


class Users(Tag[UserRepo]): ...


class Greeting(Reference[str]):
    @classmethod
    def default(cls) -> str:
        return "hello"


@gen
def greet(user_id: int):
    repo = yield from Users()  # R gains Users
    word = yield from Greeting()  # a Reference: R is unchanged
    name = repo.names.get(user_id)
    if name is None:
        return (yield from fail(NotFound(user_id)))
    return f"{word}, {name}"


@gen
def program():
    lines: list[str] = []
    lines.append((yield from greet(1)))
    # an override applies inside provide and is put back after it, even when the inside fails
    shout = provide(Greeting.context("HEY"))
    lines.append((yield from shout(greet(1))))
    lines.append(
        (yield from shout(greet(7)).pipe(catch_tag(NotFound, lambda e: succeed(f"no {e.user_id}"))))
    )
    lines.append((yield from greet(1)))
    # a forked fiber sees the services of the fiber that forked it
    child = yield from fork(greet(2))
    lines.append((yield from child.join()))
    return lines


async def main() -> None:
    repo = UserRepo({1: "ada", 2: "grace"})
    safe = program().pipe(catch_tag(NotFound, lambda e: succeed([f"no {e.user_id}"])))
    lines = await run(provide(Users.context(repo))(safe))
    for line in lines:
        print(line)
    assert lines == ["hello, ada", "HEY, ada", "no 7", "hello, ada", "hello, grace"], lines
    # `await run(program())` is a type error: Users is still in R
    print("context ok")


if __name__ == "__main__":
    asyncio.run(main())
