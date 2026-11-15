"""Resources released through a Scope, using only the public API.

uv run python examples/scope.py
"""

import asyncio
from typing import Never

from peffect import (
    Effect,
    Exit,
    Failure,
    Interrupt,
    Scope,
    Success,
    fail,
    fork,
    gen,
    never,
    run_exit,
    scoped,
    sync,
)
from peffect import scope as current_scope


def note(log: list[str], line: str) -> Effect[None, Never, Never]:
    return sync(lambda: log.append(line))


@gen
def use_two(log: list[str]):
    s = yield from current_scope
    yield from Scope.add_finalizer(s, note(log, "release a"))
    yield from Scope.add_finalizer(s, note(log, "release b"))
    log.append("work")


def interrupt_seen(ex: Exit[object, object]) -> bool:
    return isinstance(ex, Failure) and any(isinstance(r, Interrupt) for r in ex.cause.reasons)


@gen
def interrupted(log: list[str]):
    @gen
    def held():
        s = yield from current_scope
        yield from Scope.add_finalizer_exit(
            s, lambda ex: note(log, f"released, interrupted={interrupt_seen(ex)}")
        )
        yield from never

    fiber = yield from fork(scoped(held()), start_immediately=True)
    yield from fiber.interrupt()


@gen
def program():
    lines: list[str] = []

    log: list[str] = []
    yield from scoped(use_two(log))
    assert log == ["work", "release b", "release a"], log
    lines.append("scoped: work, then finalizers newest first")

    log = []
    yield from interrupted(log)
    assert log == ["released, interrupted=True"], log
    lines.append("interrupted scoped: finalizer ran with the interrupt exit")

    log = []
    s = yield from Scope.make()
    yield from Scope.close(s, Success(None))
    yield from Scope.add_finalizer(s, note(log, "late"))
    assert log == ["late"], log
    lines.append("closed scope: a late finalizer runs at once")
    return lines


async def main() -> None:
    ex = await run_exit(program())
    assert isinstance(ex, Success), ex
    for line in ex.value:
        print(line)
    failed = await run_exit(scoped(fail("boom")))  # the body's failure is kept
    assert not isinstance(failed, Success)
    print("scope ok")


if __name__ == "__main__":
    asyncio.run(main())
