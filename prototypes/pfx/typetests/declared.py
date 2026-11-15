"""Declared style: works under mypy --strict too, and pyright checks the body against it."""

from typing import Never, Protocol

import pfx
from pfx import Do, Tag, TaggedError, catch_tag, gen, provide


class NotFound(TaggedError):
    key: str


class Kv(Protocol):
    def get(self, key: str) -> str | None: ...


class Store(Tag[Kv]): ...


class Mem:
    def get(self, key: str) -> str | None:
        return {"a": "1"}.get(key)


@gen
def lookup(key: str) -> Do[str, NotFound, Store]:
    kv = yield from Store()
    v = kv.get(key)
    if v is None:
        return (yield from pfx.fail(NotFound(key=key)))
    return v


main = (
    lookup("a")
    .pipe(provide(Store.succeed(Mem())))
    .pipe(catch_tag(NotFound, lambda e: pfx.succeed("?")))
)
ok: pfx.Effect[str, Never, Never] = main
