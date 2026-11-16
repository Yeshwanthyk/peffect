"""Run the same scenarios as reference.ts; emit outcomes and ordered lifecycle events."""

import asyncio
import json
from typing import Never

from pydantic import ValidationError

from examples.parity.python_app import (
    EventLog,
    Notifier,
    NotifierLive,
    NotifierService,
    User,
    UserNotFound,
    Users,
    UsersLive,
    UsersService,
    exit_kind,
    greet_and_audit,
    greet_or_guest,
    greet_user,
    handle,
    make_app,
    record,
)
from examples.parity.python_batch_scenarios import BATCH_SCENARIOS
from examples.parity.python_composed import OutOfStock, PaymentDeclined
from examples.parity.python_scenarios import COMPOSED_SCENARIOS
from peffect import (
    Deferred,
    Effect,
    Exit,
    Fiber,
    Layer,
    Success,
    fork,
    gen,
    never,
    provide,
    run_exit,
)


def outcome_of(exit_: Exit[object, object]) -> dict[str, object]:
    if isinstance(exit_, Success):
        return {"kind": "success", "value": exit_.value}
    if exit_.cause.is_interrupted_only:
        return {"kind": "interrupted"}
    # Fail closed: an unexpected defect or extra cause must not look like a domain error.
    match exit_.cause.failures:
        case [ValidationError() as error] if len(exit_.cause.reasons) == 1:
            return {
                "kind": "failure",
                "category": "validation",
                "tag": "ValidationError",
                "detail": {"diagnostic_not_compared": str(error)},
            }
        case [UserNotFound(user_id)] if len(exit_.cause.reasons) == 1:
            return {
                "kind": "failure",
                "category": "domain",
                "tag": "UserNotFound",
                "detail": {"userId": user_id},
            }
        case [OutOfStock(sku)] if len(exit_.cause.reasons) == 1:
            return {
                "kind": "failure",
                "category": "domain",
                "tag": "OutOfStock",
                "detail": {"sku": sku},
            }
        case [PaymentDeclined(user_id)] if len(exit_.cause.reasons) == 1:
            return {
                "kind": "failure",
                "category": "domain",
                "tag": "PaymentDeclined",
                "detail": {"userId": user_id},
            }
        case _:
            raise AssertionError(f"unclassified exit: {exit_!r}")


async def scenario(name: str, program: Effect[object, object, Never]) -> dict[str, object]:
    events: list[str] = []
    exit_ = await run_exit(provide(EventLog.context(events.append))(program))
    events.append(f"terminated:{exit_kind(exit_)}")
    return {
        "name": name,
        "outcome": outcome_of(exit_),
        "repo": {
            "acquire": sum(e.startswith("repo.acquire") for e in events),
            "release": sum(e.startswith("repo.release") for e in events),
        },
        "events": list(events),
    }


TestUsers = Layer.succeed(
    Users,
    UsersService(
        lambda user_id: record(f"test-users.get:{user_id}").as_(User(user_id, "test-user"))
    ),
)


@gen
def cancellation():
    started = yield from Deferred[None, Never].make()
    hanging = Layer.succeed(
        Notifier,
        NotifierService(
            lambda message: (
                record(f"notify.started:{message}")
                .flat_map(lambda _: started.succeed(None))
                .flat_map(lambda _: never)
            )
        ),
    )
    fiber: Fiber[str, UserNotFound | ValidationError] = yield from fork(
        handle({"user_id": 1}, greet_user, make_app(UsersLive, hanging))
    )
    yield from started.await_()
    yield from record("cancel.requested")
    yield from fiber.interrupt()
    yield from record("cancel.returned")
    return (yield from fiber.join())


async def run_scenarios() -> list[dict[str, object]]:
    results = [
        await scenario("success", handle({"user_id": 1}, greet_user)),
        await scenario("invalid_input_range", handle({"user_id": 0}, greet_user)),
        await scenario("invalid_input_type", handle({"user_id": "1"}, greet_user)),
        await scenario("domain_failure_after_acquire", handle({"user_id": 99}, greet_user)),
        await scenario("domain_recovery", handle({"user_id": 99}, greet_or_guest)),
        await scenario("shared_layer", handle({"user_id": 2}, greet_and_audit)),
        await scenario(
            "service_replacement",
            handle({"user_id": 1}, greet_user, make_app(TestUsers, NotifierLive)),
        ),
        await scenario("cancellation_after_acquire", cancellation()),
    ]
    for name, program in [*COMPOSED_SCENARIOS, *BATCH_SCENARIOS]:
        results.append(await scenario(name, program))
    return results


async def main() -> None:
    # Watchdog only; all scenario ordering uses actual effects and Deferred, never wall time.
    results = await asyncio.wait_for(run_scenarios(), timeout=10)
    print(
        json.dumps(
            {"implementation": "peffect application counterpart", "scenarios": results}, indent=2
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
