"""peffect quickstart, using only `from peffect import ...`.

    uv run python examples/quickstart.py

Three things the kernel promises, each checked here so a wrong result exits non-zero:
typed failures are values you can catch by class, finalizers run however a program ends, and
interrupting a fiber waits for its cleanup.
"""

import asyncio
from dataclasses import dataclass

from peffect import (
    Failure,
    catch_tag,
    ensuring,
    fail,
    fork,
    gen,
    never,
    run,
    run_exit,
    sleep,
    succeed,
    sync,
)


@dataclass(frozen=True)
class NotFound:
    user_id: int


USERS = {1: "ada", 2: "grace"}


@gen
def find_user(user_id: int):
    yield from sleep(0)
    if user_id not in USERS:
        yield from fail(NotFound(user_id))
    return USERS[user_id]


@gen
def greet(user_id: int):
    name = yield from find_user(user_id).pipe(catch_tag(NotFound, lambda e: succeed("stranger")))
    return f"hello, {name}"


async def main() -> None:
    log: list[str] = []

    # 1. A typed failure is caught by class; an uncaught one ends the program as a Failure.
    print(await run(greet(1)))
    print(await run(greet(7)))
    lost = await run_exit(find_user(7))
    assert isinstance(lost, Failure) and lost.cause.failures == [NotFound(7)]
    print(f"uncaught: {lost.cause.failures}")

    # 2. A finalizer runs on failure too.
    guarded = find_user(9).pipe(ensuring(sync(lambda: log.append("released"))))
    assert isinstance(await run_exit(guarded), Failure)
    print(f"finalizer after failure: {log}")

    # 3. Interrupting a forked fiber waits until its finalizer has run.
    @gen
    def supervisor():
        worker = yield from fork(never.pipe(ensuring(sync(lambda: log.append("worker cleaned")))))
        yield from sleep(0.01)
        yield from worker.interrupt()
        log.append("after interrupt")
        ended = yield from worker.await_()
        return ended

    ended = await run(supervisor())
    assert isinstance(ended, Failure) and ended.cause.is_interrupted_only
    assert log == ["released", "worker cleaned", "after interrupt"], log
    print(f"interrupt waits for cleanup: {log[1:]}")
    print("quickstart ok")


if __name__ == "__main__":
    asyncio.run(main())
