"""Done and the genuine Pull completion prefix at Effect v4 pin 289a53f7.

The first six cases port Pull.test.ts:7-41. Source-derived additions pin
Pull.ts:165-177, :195-215, :238-256, :324-329, :354-357 and
internal/core.ts:739-767. No Queue implementation or prototype is imported.

Platform adaptation: peffect has no Result module. filter_done returns the
existing Exit[Done[L], E] as its success/failure carrier; done_exit_from_cause
returns Exit[L, E]. Exits are inspected as data, not executed by these filters.
Python None represents the default void leftover. No distinct fake Pull class
is needed: completion is an ordinary Fail(Done(value)) in the error channel.
"""

from typing import Never

import pytest

from peffect import Cause, Die, Effect, Fail, Failure, Interrupt, Success, exit_, never, run


def test_filter_done_succeeds_when_done_is_the_only_failure():
    """Pull.test.ts:7."""
    from peffect.cause import Done
    from peffect.pull import filter_done

    result = filter_done(Cause.fail(Done("leftover")))
    assert isinstance(result, Success)
    assert isinstance(result.value, Done)
    assert result.value.value == "leftover"


def test_filter_done_strips_done_when_merged_with_a_defect():
    """Pull.test.ts:12. A Done reason must not hide failing finalization."""
    from peffect.cause import Done
    from peffect.pull import filter_done

    result = filter_done(Cause((Fail(Done("leftover")), Die("boom"))))
    assert isinstance(result, Failure)
    assert result.cause.reasons == (Die("boom"),)


def test_filter_done_preserves_the_original_cause_without_done():
    """Pull.test.ts:18."""
    from peffect.pull import filter_done

    cause = Cause.fail("error")
    result = filter_done(cause)
    assert isinstance(result, Failure)
    assert result.cause is cause


def test_filter_done_succeeds_when_merged_only_with_interruptions():
    """Pull.test.ts:23: interrupt reason data is not a live fiber interrupt."""
    from peffect.cause import Done
    from peffect.pull import filter_done

    result = filter_done(Cause((Interrupt(1), Fail(Done("leftover")))))
    assert isinstance(result, Success)
    assert result.value.value == "leftover"


def test_done_exit_from_cause_treats_pure_done_as_success():
    """Pull.test.ts:31."""
    from peffect.cause import Done
    from peffect.pull import done_exit_from_cause

    result = done_exit_from_cause(Cause.fail(Done("leftover")))
    assert isinstance(result, Success)
    assert result.value == "leftover"


def test_done_exit_from_cause_preserves_the_stripped_failure():
    """Pull.test.ts:36."""
    from peffect.cause import Done
    from peffect.pull import done_exit_from_cause

    result = done_exit_from_cause(Cause((Fail(Done("leftover")), Die("boom"))))
    assert isinstance(result, Failure)
    assert result.cause.reasons == (Die("boom"),)


async def test_done_constructor_default_payload_and_effect_are_public():
    """internal/core.ts:746-767; Python None is the void leftover."""
    from peffect import Done, done

    assert Done().value is None
    payload = object()
    assert Done(payload).value is payload
    result = await run(exit_(done(payload)))
    assert isinstance(result, Failure)
    assert len(result.cause.reasons) == 1
    reason = result.cause.reasons[0]
    assert isinstance(reason, Fail)
    assert isinstance(reason.error, Done)
    assert reason.error.value is payload
    default = await run(exit_(done()))
    assert isinstance(default, Failure)
    assert len(default.cause.failures) == 1
    assert default.cause.failures[0].value is None


def test_done_guards_distinguish_values_reasons_and_causes():
    """Pull.ts:195-215; a Done payload in Die is not a completion failure."""
    from peffect.cause import Done, is_done
    from peffect.pull import is_done_cause, is_done_failure

    signal = Done("left")
    assert is_done(signal)
    assert not is_done(None)
    assert not is_done({"_tag": "Done", "value": "left"})
    assert is_done_failure(Fail(signal))
    assert not is_done_failure(Die(signal))
    assert not is_done_failure(Interrupt(1))
    assert not is_done_failure(Fail("error"))
    assert is_done_cause(Cause((Die("boom"), Fail(signal))))
    assert not is_done_cause(Cause((Die(signal), Interrupt(1))))


@pytest.mark.parametrize("kind", ["typed", "defect", "both"])
def test_filter_strips_all_done_reasons_and_preserves_other_reasons_in_order(kind: str):
    """Pull.ts:243-255: strip Done, preserve other failures and interruptions."""
    from peffect.cause import Done
    from peffect.pull import done_exit_from_cause, filter_done

    ordinary = (
        (Fail("error"),)
        if kind == "typed"
        else (Die("boom"),)
        if kind == "defect"
        else (Fail("error"), Die("boom"))
    )
    cause = Cause((Fail(Done("first")), Interrupt(1), *ordinary, Fail(Done("second"))))
    for result in (filter_done(cause), done_exit_from_cause(cause)):
        assert isinstance(result, Failure)
        assert result.cause.reasons == (Interrupt(1), *ordinary)


def test_filter_uses_the_first_done_payload_even_when_it_is_none():
    """Pull.ts:248 uses the first Done object, not the first truthy payload."""
    from peffect.cause import Done
    from peffect.pull import done_exit_from_cause, filter_done

    cause = Cause((Fail(Done[None](None)), Interrupt(1), Fail(Done("second"))))
    filtered = filter_done(cause)
    assert isinstance(filtered, Success)
    assert filtered.value.value is None
    result = done_exit_from_cause(cause)
    assert isinstance(result, Success)
    assert result.value is None


def test_empty_and_interrupt_only_causes_are_not_normal_completion():
    """Pull.ts:252: without Done, retain even an empty or interruption-only Cause."""
    from peffect.pull import done_exit_from_cause, filter_done

    for cause in (Cause[Never](), Cause.interrupt(1)):
        for result in (filter_done(cause), done_exit_from_cause(cause)):
            assert isinstance(result, Failure)
            assert result.cause is cause


async def test_catch_done_passes_first_leftover_to_handler_once():
    """Pull.ts:165-177 and :324-329."""
    from peffect.cause import Done
    from peffect.pull import catch_done

    seen: list[str] = []

    def handler(leftover: str) -> Effect[int, Never, Never]:
        seen.append(leftover)
        return Success(len(leftover))

    effect = Failure(Cause((Fail(Done("first")), Fail(Done("second")), Interrupt(1))))
    assert await run(catch_done(effect, handler)) == 5
    assert seen == ["first"]


@pytest.mark.parametrize("kind", ["typed", "defect", "both"])
async def test_catch_done_never_recovers_a_real_failure_merged_with_done(kind: str):
    """Pull.ts:238-256: unlike catch_tag, do not discard a second typed failure."""
    from peffect.cause import Done
    from peffect.pull import catch_done

    seen: list[str] = []

    def handler(leftover: str) -> Effect[int, Never, Never]:
        seen.append(leftover)
        return Success(0)

    ordinary = (
        (Fail("error"),)
        if kind == "typed"
        else (Die("boom"),)
        if kind == "defect"
        else (Fail("error"), Die("boom"))
    )
    cause = Cause((Interrupt(7), Fail(Done("leftover")), *ordinary))
    result = await run(exit_(catch_done(Failure(cause), handler)))
    assert isinstance(result, Failure)
    assert result.cause.reasons == (Interrupt(7), *ordinary)
    assert seen == []


async def test_catch_done_passes_success_and_non_done_failure_through():
    from peffect.pull import catch_done

    seen: list[str] = []

    def handler(leftover: str) -> Effect[int, Never, Never]:
        seen.append(leftover)
        return Success(0)

    assert await run(catch_done(Success(23), handler)) == 23
    cause = Cause.fail("ordinary")
    result = await run(exit_(catch_done(Failure(cause), handler)))
    assert isinstance(result, Failure)
    assert result.cause is cause
    assert seen == []


async def test_catch_done_preserves_handler_failure_and_raising_handler_defect():
    from peffect import fail
    from peffect.cause import Done
    from peffect.pull import catch_done

    result = await run(exit_(catch_done(Failure(Cause.fail(Done("left"))), lambda _: fail(17))))
    assert isinstance(result, Failure)
    assert result.cause.reasons == (Fail(17),)
    boom = ValueError("handler boom")

    def raises(_: str) -> Effect[int, Never, Never]:
        raise boom

    result = await run(exit_(catch_done(Failure(Cause.fail(Done("left"))), raises)))
    assert isinstance(result, Failure)
    assert result.cause.reasons == (Die(boom),)


def test_catch_done_does_not_catch_an_actual_interrupt_of_its_waiting_fiber():
    from peffect import run_fork
    from peffect.pull import catch_done

    seen: list[str] = []

    def handler(leftover: str) -> Effect[int, Never, Never]:
        seen.append(leftover)
        return Success(0)

    fiber = run_fork(catch_done(never, handler))
    assert fiber.exit is None
    fiber.interrupt_unsafe(42)
    assert isinstance(fiber.exit, Failure)
    assert fiber.exit.cause.reasons == (Interrupt(42),)
    assert seen == []
