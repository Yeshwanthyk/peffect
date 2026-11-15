"""Effect v4's Duration, the subset Clock and TestClock need, ported test-first. Upstream is pinned
at vendor/effect-v4 commit 289a53f (Effect v4.1.4): tests from
packages/effect/test/Duration.test.ts, behavior from src/Duration.ts. Consumers that pick the
subset: TestClock.ts:329-363 (`sleep` reads `toMillis`; `adjust` reads `fromInputUnsafe`,
`toMillis`, `toNanosUnsafe`) and TestClock.test.ts (string inputs such as "1 minute",
`Duration.nanos(999_999_999_999_999_000n)`, the infinities).

Labels: "port <line>" is a direct port of the upstream test at that line (the subset of its
assertions this foundation covers); "source <line>" is derived from src/Duration.ts at that line,
with the expected value read from running the pinned upstream module under node, not computed
with the code under test.

Representation (source-grounded, Duration.ts:139 `DurationValue`): a Duration is one of
Millis(JS number), Nanos(bigint), Infinity, NegativeInfinity. Python keeps the same four cases:
Millis holds a `float` (IEEE double, as a JS number), Nanos holds an `int` (unbounded, as a
bigint). Exact nanoseconds therefore never pass through a float. Tests read the case through the
public `match` (Duration.ts:1140), as upstream's `deepStrictEqual` reads `.value`.

Platform adaptations:
- Names are snake_case: `from_input_unsafe`, `to_millis`, `to_nanos_unsafe`, `negative_infinity`;
  upstream's `Order` is `order`, `Equivalence`/`equals` is `equals` and `==`.
- `Input` (Duration.ts:203): JS splits number (millis) from bigint (nanos) by type. Python has one
  `int`, and a bare `100` is a JS number literal, so `int` and `float` inputs are both millis;
  nanoseconds come only through `nanos(int)` or a Duration. `bool` is rejected, as JS rejects
  `true` (typeof "boolean"). Tuple and DurationObject inputs are deferred: no consumer yet.
- Upstream `throw new Error(...)` is `ValueError` with the same message.
- `toNanos` returns `Option`; peffect has no Option yet, so only `toNanosUnsafe` is ported.
- JS `Number(bigint)` (Infinity past the double range) is how a Python `int` becomes a JS number:
  an `int` too large for a float is +-Infinity, never an OverflowError.
- Boundary tests at the end ("boundary") are source-derived cases where Python and JS arithmetic
  differ (ties, truncating vs flooring division, float overflow,
  the regex's `$`, digit and whitespace classes).
"""

import math
from typing import Any

import pytest

import peffect.duration as D
from peffect.duration import Duration

SAFE = 9_007_199_254_740_993  # 2**53 + 1: the first integer a JS number (or a float) can't hold


def rep(d: Duration) -> tuple[str, Any]:
    """The DurationValue case and payload, via the public `match`."""

    def on_millis(m: float) -> tuple[str, Any]:
        return ("Millis", m)

    def on_nanos(n: int) -> tuple[str, Any]:
        return ("Nanos", n)

    return D.match(
        d,
        on_millis=on_millis,
        on_nanos=on_nanos,
        on_infinity=lambda: ("Infinity", None),
        on_negative_infinity=lambda: ("NegativeInfinity", None),
    )


# --- constructors and `make` normalization (Duration.ts:462) ---


def test_make_normalizes_nan_zero_and_negative_zero_to_millis_zero() -> None:
    # source 462-466: isNaN / 0 / -0 all become the shared zero value; nanos(0n) too (476)
    for d in (D.millis(math.nan), D.millis(0), D.millis(-0.0), D.nanos(0), D.zero):
        assert rep(d) == ("Millis", 0)
        assert D.is_zero(d)


def test_make_keeps_negative_values() -> None:
    # port 699 "negative values": v4 does not clamp negatives to zero
    assert D.is_negative(D.millis(-1))
    assert D.is_negative(D.nanos(-1))
    assert D.to_millis(D.millis(-1)) == -1
    assert D.to_nanos_unsafe(D.nanos(-1)) == -1
    assert rep(D.millis(-5)) == ("Millis", -5)
    assert not D.is_negative(D.zero)
    assert not D.is_negative(D.infinity)
    assert D.is_negative(D.negative_infinity)


def test_make_maps_non_finite_millis_to_the_infinities() -> None:
    # source 467-468
    assert rep(D.millis(math.inf)) == ("Infinity", None)
    assert rep(D.millis(-math.inf)) == ("NegativeInfinity", None)
    assert rep(D.infinity) == ("Infinity", None)
    assert rep(D.negative_infinity) == ("NegativeInfinity", None)
    assert D.is_finite(D.millis(100))  # port 799 "isFinite"
    assert D.is_finite(D.nanos(100))
    assert not D.is_finite(D.infinity)


def test_fractional_millis_become_nanos_rounded_ties_away_from_zero() -> None:
    # source 43-46, 469-470: a non-integer millis is stored as Nanos, never as a float
    assert rep(D.millis(1.0005)) == ("Nanos", 1_000_500)
    # 0.0000025 * 1e6 is 2.5000000000000004 in doubles; upstream gives 3n / -3n
    assert rep(D.millis(0.0000025)) == ("Nanos", 3)
    assert rep(D.millis(-0.0000025)) == ("Nanos", -3)
    assert rep(D.seconds(1.5)) == ("Millis", 1500)  # 1.5 * 1000 is integral: stays Millis


def test_unit_constructors_scale_to_millis() -> None:
    # source 781-815; port 262 "equals"
    assert rep(D.seconds(1)) == ("Millis", 1000)
    assert rep(D.minutes(1)) == ("Millis", 60_000)
    assert rep(D.hours(11)) == ("Millis", 39_600_000)
    assert D.equals(D.hours(1), D.minutes(60))


# --- conversions ---


def test_to_millis() -> None:
    # port 660 "toMillis"
    assert D.to_millis(D.millis(1)) == 1
    assert D.to_millis(D.nanos(1)) == 0.000001
    assert D.to_millis(D.infinity) == math.inf
    # source 891-897
    assert D.to_millis(D.negative_infinity) == -math.inf
    assert D.to_millis(D.nanos(1_500_000)) == 1.5
    assert D.to_millis(D.nanos(SAFE)) == 9007199254.740992  # Number(nanos) / 1e6, as upstream


def test_to_nanos_unsafe() -> None:
    # port 680 "toNanosUnsafe"
    assert D.to_nanos_unsafe(D.nanos(1)) == 1
    with pytest.raises(ValueError, match="Cannot convert infinite duration to nanos"):
        D.to_nanos_unsafe(D.infinity)
    assert D.to_nanos_unsafe(D.millis(1.0005)) == 1_000_500
    assert D.to_nanos_unsafe(D.millis(0.0000015)) == 2
    assert D.to_nanos_unsafe(D.millis(-0.0000015)) == -2
    assert D.to_nanos_unsafe(D.millis(100)) == 100_000_000
    assert D.to_nanos_unsafe(D.millis(2.0**1023)) == 1_000_000 << 1023
    assert D.to_nanos_unsafe(D.millis(1_760_450_000_123)) == 1_760_450_000_123_000_000
    # source 1051-1062: bigint out, exactly; negative infinity throws too
    assert type(D.to_nanos_unsafe(D.millis(100))) is int
    with pytest.raises(ValueError, match="Cannot convert infinite duration to nanos"):
        D.to_nanos_unsafe(D.negative_infinity)


def test_nanos_beyond_js_safe_integer_stay_exact() -> None:
    # source 730; the value is TestClock.test.ts:205's
    big = 999_999_999_999_999_000
    assert D.to_nanos_unsafe(D.nanos(big)) == big
    assert D.to_nanos_unsafe(D.from_input_unsafe(D.nanos(big))) == big
    assert rep(D.nanos(SAFE)) == ("Nanos", SAFE)


# --- from_input_unsafe (Duration.ts:275) ---


def test_from_input_unsafe_numbers_and_durations() -> None:
    # port 14 "fromInputUnsafe": a Duration passes through as the same object; a number is millis
    millis100 = D.millis(100)
    assert D.from_input_unsafe(millis100) is millis100
    assert rep(D.from_input_unsafe(100)) == ("Millis", 100)
    assert rep(D.from_input_unsafe(0.5)) == ("Nanos", 500_000)
    assert rep(D.from_input_unsafe(math.nan)) == ("Millis", 0)


def test_from_input_unsafe_strings() -> None:
    # port 14: the nano rows; source 279-326 for the units TestClock.test.ts uses
    assert rep(D.from_input_unsafe("1 nano")) == ("Nanos", 1)
    assert rep(D.from_input_unsafe("10 nanos")) == ("Nanos", 10)
    assert rep(D.from_input_unsafe("1.5 nanos")) == ("Nanos", 2)
    assert rep(D.from_input_unsafe("-1.5 nanos")) == ("Nanos", -2)
    assert rep(D.from_input_unsafe("9007199254740993.1 nanos")) == ("Nanos", SAFE)
    assert rep(D.from_input_unsafe("-9007199254740993.5 nanos")) == (
        "Nanos",
        -9_007_199_254_740_994,
    )
    assert rep(D.from_input_unsafe("1 second")) == ("Millis", 1000)
    assert rep(D.from_input_unsafe("999 millis")) == ("Millis", 999)
    assert rep(D.from_input_unsafe("1 millis")) == ("Millis", 1)
    assert rep(D.from_input_unsafe("1 minute")) == ("Millis", 60_000)
    assert rep(D.from_input_unsafe("2 minutes")) == ("Millis", 120_000)
    assert rep(D.from_input_unsafe("11 hours")) == ("Millis", 39_600_000)
    assert rep(D.from_input_unsafe("0.5 millis")) == ("Nanos", 500_000)
    assert rep(D.from_input_unsafe("Infinity")) == ("Infinity", None)
    assert rep(D.from_input_unsafe("-Infinity")) == ("NegativeInfinity", None)


def test_from_input_unsafe_keeps_nanoseconds_in_large_decimal_seconds() -> None:
    # port 116
    assert (
        D.to_nanos_unsafe(D.from_input_unsafe("17280000.000000001 seconds"))
        == 17_280_000_000_000_001
    )
    assert (
        D.to_nanos_unsafe(D.from_input_unsafe("-17280000.000000001 seconds"))
        == -17_280_000_000_000_001
    )


@pytest.mark.parametrize("bad", ["abc", "1 fortnight", "1second", True, None])
def test_from_input_unsafe_rejects_invalid_input(bad: Any) -> None:
    # source 384-386: `throw new Error(`Invalid Input: ${input}`)`
    with pytest.raises(ValueError, match="Invalid Input"):
        D.from_input_unsafe(bad)


# --- comparison ---


def test_order() -> None:
    # port 193 "Order"
    assert D.order(D.millis(1), D.millis(2)) == -1
    assert D.order(D.millis(2), D.millis(1)) == 1
    assert D.order(D.millis(2), D.millis(2)) == 0
    assert D.order(D.millis(1), D.nanos(2_000_000)) == -1
    assert D.order(D.nanos(1), D.nanos(2)) == -1
    assert D.order(D.nanos(2), D.nanos(2)) == 0
    assert D.order(D.nanos(2_000_000), D.millis(1)) == 1
    assert D.order(D.infinity, D.infinity) == 0
    assert D.order(D.infinity, D.millis(1)) == 1
    assert D.order(D.nanos(1), D.infinity) == -1
    # source 1262-1275: negative infinity sits below everything finite; exact past 2**53
    assert D.order(D.negative_infinity, D.nanos(-SAFE)) == -1
    assert D.order(D.nanos(SAFE), D.nanos(SAFE - 1)) == 1


def test_equality_compares_across_representations() -> None:
    # port 262 "equals", port 266 "Hash.symbol agrees with equals": Equal.symbol is `equals`,
    # so Python `==` and `hash` follow it
    assert D.equals(D.nanos(1_000_000), D.millis(1))
    assert D.nanos(1_000_000) == D.millis(1)
    assert hash(D.nanos(1_000_000)) == hash(D.millis(1))
    assert D.nanos(SAFE) != D.nanos(SAFE - 1)
    assert D.infinity == D.infinity
    assert D.infinity != D.negative_infinity


# --- arithmetic ---


def test_sum() -> None:
    # port 399 "sum"
    assert rep(D.sum(D.seconds(30), D.seconds(30))) == rep(D.minutes(1))
    assert rep(D.sum(D.nanos(30), D.nanos(30))) == ("Nanos", 60)
    assert rep(D.sum(D.infinity, D.seconds(30))) == ("Infinity", None)
    assert rep(D.sum(D.nanos(1), D.infinity)) == ("Infinity", None)
    assert rep(D.sum(D.seconds(1), D.zero)) == rep(D.seconds(1))  # port 645 "zero"
    # source 1629-1645: exact past 2**53, mixed cases go through nanos, +inf + -inf is zero
    assert rep(D.sum(D.nanos(SAFE), D.nanos(1))) == ("Nanos", 9_007_199_254_740_994)
    assert rep(D.sum(D.millis(1), D.nanos(SAFE))) == ("Nanos", 9_007_199_255_740_993)
    assert rep(D.sum(D.infinity, D.negative_infinity)) == ("Millis", 0)


def test_subtract() -> None:
    # port 414 "subtract"
    assert rep(D.subtract(D.seconds(30), D.seconds(10))) == rep(D.seconds(20))
    assert rep(D.subtract(D.seconds(30), D.seconds(30))) == rep(D.zero)
    assert rep(D.subtract(D.seconds(30), D.seconds(40))) == rep(D.seconds(-10))
    assert rep(D.subtract(D.nanos(30), D.nanos(40))) == ("Nanos", -10)
    assert rep(D.subtract(D.nanos(30), D.nanos(30))) == rep(D.zero)
    assert rep(D.subtract(D.infinity, D.nanos(30))) == ("Infinity", None)
    assert rep(D.subtract(D.seconds(30), D.infinity)) == ("NegativeInfinity", None)
    assert rep(D.subtract(D.infinity, D.infinity)) == rep(D.zero)
    assert rep(D.subtract(D.negative_infinity, D.negative_infinity)) == rep(D.zero)
    assert rep(D.subtract(D.seconds(5), D.negative_infinity)) == ("Infinity", None)
    # source 1585-1600: exact past 2**53 with a mixed Millis operand
    assert rep(D.subtract(D.nanos(SAFE), D.millis(1))) == ("Nanos", 9_007_199_253_740_993)


# --- boundary: Python/JS arithmetic differences. Every expected value was read from running the
# pinned upstream Duration.ts under node 22. ---


def test_boundary_exact_ties_round_away_from_zero_not_to_even() -> None:
    # source 43: Math.sign(x) * Math.round(|x|); 0.0000025 * 1e6 is exactly 2.5 in doubles
    assert rep(D.millis(0.0000005)) == ("Nanos", 1)
    assert rep(D.millis(0.0000025)) == ("Nanos", 3)
    assert rep(D.millis(-0.0000045)) == ("Nanos", -5)
    assert rep(D.millis(4.999999999999999e-7)) == ("Nanos", 0)  # 0.4999999999999999 rounds down
    # source 469-470: a tiny negative millis rounds to Nanos 0n (not normalized to Millis 0)
    assert rep(D.millis(-1e-7)) == ("Nanos", 0)
    assert D.is_zero(D.millis(-1e-7))
    assert D.millis(-1e-7) == D.zero
    assert rep(D.millis(-6e-7)) == ("Nanos", -1)
    assert D.to_nanos_unsafe(D.millis(-1.0005)) == -1_000_500


def test_boundary_parser_divides_negative_decimals_away_from_zero() -> None:
    # source 58-75: bigint division truncates; ties go away from zero on |numerator|
    for text, expected in [
        ("0.5 nanos", 1),
        ("-0.5 nanos", -1),
        ("2.5 nanos", 3),
        ("-2.5 nanos", -3),
        ("-2.5 micros", -2500),
        ("0.0000005 millis", 1),
        ("-0.0000005 millis", -1),
        ("-0.0000025 millis", -3),
        ("2.0000005 millis", 2_000_001),
        ("0.0000000005 seconds", 1),
    ]:
        assert rep(D.from_input_unsafe(text)) == ("Nanos", expected), text
    assert rep(D.from_input_unsafe("-0 nanos")) == ("Millis", 0)
    assert rep(D.from_input_unsafe("-0 millis")) == ("Millis", 0)
    assert rep(D.from_input_unsafe("007 millis")) == ("Millis", 7)


def test_boundary_parser_matches_the_js_regexp_exactly() -> None:
    # source 246: JS `\s` includes NBSP and BOM, `\d` is ASCII only, `$` (no m flag) is end only
    assert rep(D.from_input_unsafe("1\u00a0second")) == ("Millis", 1000)
    assert rep(D.from_input_unsafe("1\ufeffsecond")) == ("Millis", 1000)
    assert rep(D.from_input_unsafe("1\tseconds")) == ("Millis", 1000)
    for bad in ["1 second\n", "\u0661 second", "1 second ", " 1 second", "+1 second"]:
        with pytest.raises(ValueError, match="Invalid Input"):
            D.from_input_unsafe(bad)
    for bad in ["1. second", ".5 second", "1e3 millis", "Infinity millis"]:
        with pytest.raises(ValueError, match="Invalid Input"):
            D.from_input_unsafe(bad)


@pytest.mark.parametrize("sign", [1, -1])
def test_boundary_large_finite_millisecond_strings_do_not_overflow(sign: int) -> None:
    # port 127 (toMillis half; toSeconds is not in this subset): nanosToUnit, Duration.ts:50-56
    text = f"{'-' if sign < 0 else ''}1{'0' * 302}1 millis"
    assert D.is_finite(D.from_input_unsafe(text))
    assert D.to_millis(text) == sign * 1e303


def test_boundary_python_ints_become_js_numbers() -> None:
    # adaptation of Number(bigint): round to the nearest double, Infinity past the range
    assert rep(D.millis(2**53 + 1)) == ("Millis", 9_007_199_254_740_992)
    assert rep(D.millis(10**400)) == ("Infinity", None)
    assert rep(D.millis(-(10**400))) == ("NegativeInfinity", None)
    assert rep(D.from_input_unsafe(-(10**400))) == ("NegativeInfinity", None)
    assert rep(D.seconds(1e306)) == ("Infinity", None)  # source 781: seconds * 1000 overflows
    assert rep(D.seconds(10**400)) == ("Infinity", None)


def test_boundary_huge_nanos_convert_to_millis_without_overflow_error() -> None:
    # source 50-56: Number(nanos) is Infinity, so it scales first with truncating bigint division
    assert D.to_millis(D.nanos(10**400)) == math.inf
    assert D.to_millis(D.nanos(-(10**400))) == -math.inf
    assert D.to_millis(D.nanos(-(10**310) - 1_500_000)) == -1e304
    assert D.to_millis(D.nanos(-1_500_000)) == -1.5
    # A quotient exactly between two doubles: JS truncates to -midpoint (ties to the even
    # -2**1005); a flooring quotient, one further from zero, would round to the other neighbor.
    midpoint = (1 << 1005) + (1 << 952)
    assert D.to_millis(D.nanos(-(midpoint * 1_000_000 + 1))) == -(2.0**1005)
    assert D.to_millis(D.nanos(midpoint * 1_000_000 + 1)) == 2.0**1005


def test_boundary_finite_arithmetic_overflow_is_infinity() -> None:
    # source 1629, 1585: make(self + that) on doubles
    assert rep(D.sum(D.millis(1.7e308), D.millis(1.7e308))) == ("Infinity", None)
    assert rep(D.subtract(D.millis(-1.7e308), D.millis(1.7e308))) == ("NegativeInfinity", None)


def test_boundary_order_negatives() -> None:
    # port 699 "negative values" (Order rows)
    assert D.order(D.negative_infinity, D.negative_infinity) == 0
    assert D.order(D.negative_infinity, D.seconds(-5)) == -1
    assert D.order(D.seconds(-5), D.negative_infinity) == 1
    assert D.order(D.negative_infinity, D.infinity) == -1
    assert D.order(D.infinity, D.negative_infinity) == 1
    assert D.order(D.seconds(-5), D.seconds(-3)) == -1
    assert D.order(D.seconds(-3), D.seconds(-5)) == 1


def test_boundary_hash_and_equality_on_large_millis() -> None:
    # port 266 and port 281: Hash.hash(roundMillisToNanos(millis)) for Millis, exact bigint
    assert hash(D.seconds(5)) == hash(D.nanos(5_000_000_000))
    assert hash(D.millis(-5000)) == hash(D.nanos(-5_000_000_000))
    assert hash(D.infinity) == hash(D.infinity)
    assert D.nanos(5_000_000_000) in {D.seconds(5)}
    assert D.millis(2.0**1023) == D.nanos(1_000_000 << 1023)
    assert D.millis(1e303) == D.millis(1e303)
    assert D.millis(1e303) in {D.millis(1e303)}
    # source 1262: a mixed pair compares BigInt(1e303) * 1e6, the double's exact value
    exact = int(1e303) * 1_000_000
    assert D.order(D.millis(1e303), D.nanos(exact)) == 0
    assert D.order(D.millis(1e303), D.nanos(10**309)) == 1
    assert D.millis(1e303) == D.nanos(exact)
    # source 425: Equal.symbol is `isDuration(that) && equals(...)`
    assert D.millis(5) != 5


def test_boundary_duration_is_immutable_and_built_only_by_constructors() -> None:
    # source 462: `make` is private; every Duration is normalized by it
    d = D.millis(1)
    for attribute in ("value", "_value"):
        with pytest.raises(AttributeError):
            setattr(d, attribute, D.zero.value)
    with pytest.raises(TypeError):
        Duration(D.zero.value, object())


def test_duration_rejects_state_deletion_and_remains_in_set():
    """Regression: immutable Duration must retain its hash after rejected deletion."""
    value = D.millis(1)
    held = {value}
    with pytest.raises(AttributeError):
        delattr(value, "_value")
    assert value in held
    assert D.to_nanos_unsafe(value) == 1_000_000


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0" * 4300 + "1 nanos", 1),
        ("0." + "0" * 4300 + "5 nanos", 0),
        ("0." + "0" * 4300 + "5 seconds", 0),
    ],
)
def test_long_decimal_strings_match_upstream(text: str, expected: int):
    """Source-derived: Duration.ts parseNanos accepts digits beyond Python's int limit."""
    assert D.to_nanos_unsafe(D.from_input_unsafe(text)) == expected
