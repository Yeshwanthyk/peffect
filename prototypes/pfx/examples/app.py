"""A slice of a pydantic app written with pfx. No type annotations on the @gen functions:
pyright infers Effect[A, E, R] for each, and `run` rejects anything unprovided/unhandled."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Never, Protocol

from pydantic import BaseModel, EmailStr
from pydantic_settings import BaseSettings

import pfx
from pfx import Effect, SettingsTag, Tag, TaggedError, catch_tag, gen, provide


# ---- domain (plain pydantic) -------------------------------------------------------------------
class User(BaseModel):
    id: int
    email: EmailStr


class SignupRequest(BaseModel):
    email: EmailStr


class UserNotFound(TaggedError):
    user_id: int


class EmailTaken(TaggedError):
    email: str


# ---- services: shape (Protocol) + key (Tag) ----------------------------------------------------
class AppSettings(BaseSettings):
    db_url: str = "memory://"
    greeting: str = "hello"


class Config(SettingsTag[AppSettings]): ...


class Conn(Protocol):
    async def fetch(self, key: str) -> dict[str, object] | None: ...
    async def put(self, key: str, value: dict[str, object]) -> None: ...


class Db(Tag[Conn]): ...


class UserRepo(Protocol):
    def get(self, user_id: int) -> Effect[User, UserNotFound, Never]: ...
    def create(self, email: str) -> Effect[User, EmailTaken, Never]: ...


class Users(Tag[UserRepo]): ...


# ---- live implementations ----------------------------------------------------------------------
class MemoryConn:
    def __init__(self, url: str) -> None:
        self.url = url
        self.rows: dict[str, dict[str, object]] = {}
        self.closed = False

    async def fetch(self, key: str) -> dict[str, object] | None:
        return self.rows.get(key)

    async def put(self, key: str, value: dict[str, object]) -> None:
        self.rows[key] = value


EVENTS: list[str] = []


@asynccontextmanager
async def open_conn() -> AsyncGenerator[Conn]:
    conn = MemoryConn("memory://")
    EVENTS.append("db open")
    try:
        yield conn
    finally:
        conn.closed = True
        EVENTS.append("db close")


DbLive = Db.scoped(open_conn)  # Layer[Db, Never, Never]; closes when run() ends


class SqlUsers:
    def __init__(self, conn: Conn) -> None:
        self.conn = conn

    @gen
    def get(self, user_id: int):
        row = yield from pfx.from_async(lambda: self.conn.fetch(f"user:{user_id}"))
        if row is None:
            return (yield from pfx.fail(UserNotFound(user_id=user_id)))
        return (yield from pfx.decode(User, row).pipe(_defect_on_decode))

    @gen
    def create(self, email: str):
        if (yield from pfx.from_async(lambda: self.conn.fetch(f"email:{email}"))) is not None:
            return (yield from pfx.fail(EmailTaken(email=email)))
        user = User(id=len(email), email=email)
        yield from pfx.from_async(lambda: self.conn.put(f"user:{user.id}", user.model_dump()))
        yield from pfx.from_async(lambda: self.conn.put(f"email:{email}", {"id": user.id}))
        return user


def _defect_on_decode(eff: Effect[User, pfx.DecodeError, Never]) -> Effect[User, Never, Never]:
    """Our own rows failing validation is a bug, not a domain error: turn it into a defect."""

    def die(e: pfx.DecodeError) -> Effect[User, Never, Never]:
        raise RuntimeError(f"corrupt row: {e}")

    return eff.pipe(catch_tag(pfx.DecodeError, die))


@gen
def make_users():
    conn = yield from Db()
    return SqlUsers(conn)  # SqlUsers structurally satisfies UserRepo


UsersLive = Users.effect(make_users())  # Layer[Users, Never, Db]
ConfigLive = Config.from_env(AppSettings)  # Layer[Config, ConfigError, Never]
AppLive = UsersLive.provide_merge(DbLive).merge(ConfigLive)  # Layer[Users|Db|Config, ...]


# ---- programs ----------------------------------------------------------------------------------
@gen
def signup(payload: object):
    req = yield from pfx.decode(SignupRequest, payload)
    users = yield from Users()
    cfg = yield from Config()
    user = yield from users.create(req.email)
    return f"{cfg.greeting} {user.email}"


@gen
def signup_handler(payload: object):
    """What a FastAPI route would run: every error becomes a response, nothing escapes."""
    return (
        yield from signup(payload)
        .pipe(catch_tag(EmailTaken, lambda e: pfx.succeed(f"409 {e.to_json_dict()}")))
        .pipe(catch_tag(pfx.DecodeError, lambda e: pfx.succeed(f"422 {e.errors[0]['msg']}")))
    )


main = (
    signup_handler({"email": "ada@example.com"})
    .pipe(provide(AppLive))
    .pipe(catch_tag(pfx.ConfigError, lambda e: pfx.succeed(f"500 {e.message}")))
)


if __name__ == "__main__":
    print(asyncio.run(pfx.run(main)))
    print(EVENTS)
