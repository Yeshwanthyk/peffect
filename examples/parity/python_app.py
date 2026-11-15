"""Python counterpart of app.ts, composed entirely from public peffect APIs.

Pydantic is an application boundary, not a port of Effect Schema. Its ValidationError
is the declared error channel; unexpected exceptions remain defects.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never

from pydantic import BaseModel, Field, StrictInt, ValidationError

from peffect import (
    Effect,
    Exit,
    Layer,
    Reference,
    Success,
    Tag,
    acquire_release,
    attempt,
    catch_tag,
    fail,
    gen,
    provide_layer,
    succeed,
    sync,
)


class EventLog(Reference[Callable[[str], None]], key="parity/EventLog"):
    """A caller supplies its own sink; the default has no mutable shared state."""

    @classmethod
    def default(cls) -> Callable[[str], None]:
        return lambda _: None


def record(event: str) -> Effect[None, Never, Never]:
    return EventLog().use().flat_map(lambda sink: sync(lambda: sink(event)))


def exit_kind(exit_: Exit[object, object]) -> str:
    if isinstance(exit_, Success):
        return "success"
    return "interrupt" if exit_.cause.is_interrupted_only else "failure"


class UserRequest(BaseModel):
    user_id: StrictInt = Field(ge=1, le=1_000_000)


@dataclass(frozen=True)
class UserNotFound:
    user_id: int


@dataclass(frozen=True)
class User:
    id: int
    name: str


@dataclass(frozen=True)
class RepoService:
    lookup: Callable[[int], User | None]


@dataclass(frozen=True)
class UsersService:
    get: Callable[[int], Effect[User, UserNotFound, Never]]


@dataclass(frozen=True)
class NotifierService:
    send: Callable[[str], Effect[None, Never, Never]]


@dataclass(frozen=True)
class GreeterService:
    greet: Callable[[int], Effect[str, UserNotFound, Never]]


@dataclass(frozen=True)
class AuditorService:
    audit: Callable[[int], Effect[str, UserNotFound, Never]]


class Repo(Tag[RepoService], key="parity/Repo"): ...


class Users(Tag[UsersService], key="parity/Users"): ...


class Notifier(Tag[NotifierService], key="parity/Notifier"): ...


class Greeter(Tag[GreeterService], key="parity/Greeter"): ...


class Auditor(Tag[AuditorService], key="parity/Auditor"): ...


TABLE = {1: "ann", 2: "bob"}


def lookup(user_id: int) -> User | None:
    name = TABLE.get(user_id)
    return None if name is None else User(user_id, name)


RepoLive = Layer.effect(
    Repo,
    acquire_release(
        record("repo.acquire").as_(RepoService(lookup)),
        lambda _, ex: record(f"repo.release:{exit_kind(ex)}"),
    ),
)


@gen
def make_users():
    repo = yield from Repo()
    yield from record("users.build")

    def get(user_id: int) -> Effect[User, UserNotFound, Never]:
        user = repo.lookup(user_id)
        return fail(UserNotFound(user_id)) if user is None else succeed(user)

    return UsersService(get)


UsersLive = Layer.effect(Users, make_users()).pipe(Layer.provide(RepoLive))
NotifierLive = Layer.succeed(Notifier, NotifierService(lambda message: record(f"notify:{message}")))


@gen
def make_greeter():
    users = yield from Users()
    notifier = yield from Notifier()
    yield from record("greeter.build")

    def greet(user_id: int) -> Effect[str, UserNotFound, Never]:
        return (
            users.get(user_id)
            .map(lambda u: f"hello {u.name}")
            .flat_map(lambda message: notifier.send(message).as_(message))
        )

    return GreeterService(greet)


@gen
def make_auditor():
    users = yield from Users()
    yield from record("auditor.build")
    return AuditorService(lambda user_id: users.get(user_id).map(lambda u: f"audited {u.id}"))


GreeterLive = Layer.effect(Greeter, make_greeter())
AuditorLive = Layer.effect(Auditor, make_auditor())


def make_app(users: Layer[Users], notifier: Layer[Notifier]) -> Layer[Greeter | Auditor]:
    return (
        Layer.merge_all(GreeterLive, AuditorLive)
        .pipe(Layer.provide(users))
        .pipe(Layer.provide(notifier))
    )


AppLive = make_app(UsersLive, NotifierLive)


@gen
def greet_user(req: UserRequest):
    greeter = yield from Greeter()
    return (yield from greeter.greet(req.user_id))


def greet_or_guest(req: UserRequest) -> Effect[str, Never, Greeter]:
    return greet_user(req).pipe(
        catch_tag(
            UserNotFound,
            lambda e: record(f"recovered:UserNotFound:{e.user_id}").as_("hello guest"),
        )
    )


@gen
def greet_and_audit(req: UserRequest):
    greeting = yield from (yield from Greeter()).greet(req.user_id)
    audit = yield from (yield from Auditor()).audit(req.user_id)
    return {"greeting": greeting, "audit": audit}


def handle[A, E](
    raw: object,
    use_case: Callable[[UserRequest], Effect[A, E, Greeter | Auditor]],
    app: Layer[Greeter | Auditor] = AppLive,
) -> Effect[A, E | ValidationError, Never]:
    return attempt(lambda: UserRequest.model_validate(raw), ValidationError).flat_map(
        lambda req: record("request.valid").flat_map(lambda _: provide_layer(app)(use_case(req)))
    )
