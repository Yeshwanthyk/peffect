"""Each block is a mistake pyright must reject. Checked by tests/test_types.py."""

from typing import Never

import pfx
from examples.app import (
    AppSettings,
    Config,
    ConfigLive,
    DbLive,
    MemoryConn,
    SqlUsers,
    UserRepo,
    Users,
    UsersLive,
    signup,
    signup_handler,
)
from pfx import Effect, catch_tag, provide

# 1. MISSING_LAYER: Config never provided
pfx.run(signup_handler({}).pipe(provide(UsersLive.provide_merge(DbLive))))

# 2. UNHANDLED_ERROR: EmailTaken / DecodeError not caught
pfx.run(signup({}).pipe(provide(UsersLive.provide_merge(DbLive).merge(ConfigLive))))

# 3. LAYER_NEEDS_UNMET: UsersLive needs Db, nobody provides it
pfx.run(
    signup_handler({})
    .pipe(provide(UsersLive.merge(ConfigLive)))
    .pipe(catch_tag(pfx.ConfigError, lambda e: pfx.succeed("x")))
)

# 4. WRONG_SERVICE_SHAPE: a Conn is not a UserRepo
Users.succeed(MemoryConn("x"))

# 5. WRONG_SETTINGS_CLASS: Config's shape is AppSettings
Config.succeed({"db_url": "x"})

# 6. WRONG_ERROR_TYPE: repo.get can't fail with EmailTaken per its Protocol
bad_repo: UserRepo = SqlUsers  # class, not instance

# 7. NOT_HANDLED_BY_CATCH: catching an error that isn't in E still leaves the real ones
x: Effect[str, Never, Never] = signup({}).pipe(
    catch_tag(pfx.ConfigError, lambda e: pfx.succeed("x"))
)

_ = AppSettings
