"""assert_type pins what pyright infers; a regression in the type encoding fails pyright here."""

from typing import Never, assert_type

import pfx
from examples.app import (
    AppLive,
    Config,
    ConfigLive,
    Db,
    DbLive,
    EmailTaken,
    Users,
    UsersLive,
    signup,
    signup_handler,
)
from pfx import ConfigError, DecodeError, Effect, Layer, catch_tag, provide

# inference from @gen bodies
assert_type(signup({}), Effect[str, DecodeError | EmailTaken, Config | Users])
assert_type(signup_handler({}), Effect[str, Never, Config | Users])

# catch_tag: remove one of two, then the last one (Fable's 1 -> 0 case)
one = signup({}).pipe(catch_tag(EmailTaken, lambda e: pfx.succeed(0)))
assert_type(one, Effect[str | int, DecodeError, Config | Users])
zero = one.pipe(catch_tag(DecodeError, lambda e: pfx.succeed(0)))
assert_type(zero, Effect[str | int, Never, Config | Users])

# partial provide: 2 -> 1 -> 0
p1 = signup_handler({}).pipe(provide(ConfigLive))
assert_type(p1, Effect[str, ConfigError, Users])
p0 = p1.pipe(provide(UsersLive.provide(DbLive)))
assert_type(p0, Effect[str, ConfigError, Never])

# layer algebra
assert_type(UsersLive, Layer[Users, Never, Db])
assert_type(UsersLive.provide(DbLive), Layer[Users, Never, Never])
assert_type(AppLive, Layer[Users | Db | Config, ConfigError, Never])
