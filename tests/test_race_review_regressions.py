"""Expected values measured from pinned vendor 289a53f7 by parity.ts."""

import pytest

import peffect.duration as D
from peffect import Failure, race_all_first, run_exit, succeed


@pytest.mark.asyncio
async def test_early_winner_closes_retained_generator():
    log: list[str] = []

    def racers():
        try:
            yield succeed(1)
            yield succeed(2)
        finally:
            log.append("closed")

    retained = racers()
    effect = race_all_first(retained)
    try:
        await run_exit(effect)
        assert log == ["closed"]
    finally:
        retained.close()


@pytest.mark.asyncio
async def test_iterator_close_failure_replaces_synchronous_winner():
    def racers():
        try:
            yield succeed(1)
            yield succeed(2)
        finally:
            raise RuntimeError("close failed")

    retained = racers()
    effect = race_all_first(retained)
    try:
        ex = await run_exit(effect)
        assert isinstance(ex, Failure)
        assert [str(d) for d in ex.cause.defects] == ["close failed"]
    finally:
        try:
            retained.close()
        except RuntimeError:
            pass


def test_day_count_uses_javascript_shortest_decimal():
    assert D.format(D.nanos(1000000000000000128 * 86400000000000)) == "1000000000000000100d"
