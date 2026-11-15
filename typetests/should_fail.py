"""Each block is a mistake pyright must reject. Checked by tests/test_types.py."""

from typing import Never

import peffect
from peffect import Effect, catch_tag, fail, gen, succeed


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
