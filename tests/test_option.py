"""Option at Effect v4 pin 289a53f7: only the constructor and value behaviour peffect uses.

Upstream: test/Option.test.ts; implementation src/Option.ts and internal/option.ts. Option is
`Some[A] | None` (coordinator decision): `Option.some(a)` is `Some(a)`, `Option.none()` is
`None`, `Option.isSome(o)` is `isinstance(o, Some)` and `Option.isNone(o)` is `o is None`.
`Equal.equals` is `==` and `Hash.hash` is `hash`. The rest of Option.ts (map, getOrElse, ...)
is not ported: nothing uses it yet.
"""

from typing import Never

import pytest

from peffect import Effect, succeed
from peffect.option import Option, Some

# == upstream: Option.test.ts ==================================================================


def test_verify_structural_equality():
    """Option.test.ts:107."""
    assert Some(1) == Some(1)
    assert Some(1) != Some(2)
    none: Option[int] = None
    assert none is None  # the single None value: Equal.equals(none(), none())


def test_verify_hash_equality():
    """Option.test.ts:113."""
    assert hash(Some(1)) == hash(Some(1))
    assert (hash(Some(1)) == hash(Some(2))) is False
    assert hash(None) == hash(None)


def test_is_none():
    """Option.test.ts:380."""
    options: list[Option[int]] = [None, Some(1)]
    assert [o is None for o in options] == [True, False]


def test_is_some():
    """Option.test.ts:385."""
    options: list[Option[int]] = [None, Some(1)]
    assert [isinstance(o, Some) for o in options] == [False, True]


# == source-derived ============================================================================


def test_some_of_none_is_present():
    """internal/option.ts:88, :94-100: `some(undefined)` is a Some; presence is the tag, not the
    value. This is why Option is not `A | None`: `Some(None)` must differ from absent."""
    nested: Option[None] = Some(None)
    assert isinstance(nested, Some)
    assert nested is not None
    assert nested.value is None


def test_some_equality_compares_the_value_and_the_tag():
    """internal/option.ts:32-36: equal iff the other is also a Some with an equal value."""
    assert Some((1, "a")) == Some((1, "a"))
    assert Some(1) != 1
    assert Some(Some(1)) != Some(1)


def test_some_value_is_matchable_and_immutable():
    """Some carries one `value` (Option.ts Some interface); peffect values are immutable."""
    match Some(3):
        case Some(value):
            assert value == 3
    some = Some(3)
    for name in ("value", "_value", "other"):
        with pytest.raises(AttributeError):
            setattr(some, name, 4)
        with pytest.raises(AttributeError):
            delattr(some, name)
    assert some == Some(3)
    assert repr(some) == "Some(3)"


def test_some_and_option_widen_covariantly():
    """Option.ts:133 `Some<out A>`: a present int is a present object, with no copy. Checked by
    pyright (this file is type-checked); the narrowing direction is a should_fail block."""

    def widen_some(value: Some[int]) -> Some[object]:
        return value

    def widen_option(value: Option[int]) -> Option[object]:
        return value

    def widen_effect(
        eff: Effect[Option[int], ValueError, Never],
    ) -> Effect[Option[object], Exception, object]:
        return eff

    some = Some(1)
    assert widen_some(some) is some
    assert widen_option(None) is None
    built = succeed(Some(1))
    assert widen_effect(built) is built
