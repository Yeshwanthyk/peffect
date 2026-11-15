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
- `peffect.duration` doesn't exist yet. `load_duration()` looks it up when each test runs, so
  every test fails rather than the file failing to import. The lookup is temporary: it becomes a
  typed import once the module lands.
"""

import importlib
import math
from typing import Any

import pytest

SAFE = 9_007_199_254_740_993  # 2**53 + 1: the first integer a JS number (or a float) can't hold


def load_duration() -> Any:
    return importlib.import_module("peffect.duration")


def rep(d: Any) -> tuple[str, Any]:
    """The DurationValue case and payload, via the public `match`."""

    def on_millis(m: float) -> tuple[str, Any]:
        return ("Millis", m)

    def on_nanos(n: int) -> tuple[str, Any]:
        return ("Nanos", n)

    return load_duration().match(
        d,
        on_millis=on_millis,
        on_nanos=on_nanos,
        on_infinity=lambda: ("Infinity", None),
        on_negative_infinity=lambda: ("NegativeInfinity", None),
    )


# --- constructors and `make` normalization (Duration.ts:462) ---


def test_make_normalizes_nan_zero_and_negative_zero_to_millis_zero() -> None:
    # source 462-466: isNaN / 0 / -0 all become the shared zero value; nanos(0n) too (476)
    D = load_duration()
    for d in (D.millis(math.nan), D.millis(0), D.millis(-0.0), D.nanos(0), D.zero):
        assert rep(d) == ("Millis", 0)
        assert D.is_zero(d)


def test_make_keeps_negative_values() -> None:
    # port 699 "negative values": v4 does not clamp negatives to zero
    D = load_duration()
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
    D = load_duration()
    assert rep(D.millis(math.inf)) == ("Infinity", None)
    assert rep(D.millis(-math.inf)) == ("NegativeInfinity", None)
    assert rep(D.infinity) == ("Infinity", None)
    assert rep(D.negative_infinity) == ("NegativeInfinity", None)
    assert D.is_finite(D.millis(100))  # port 799 "isFinite"
    assert D.is_finite(D.nanos(100))
    assert not D.is_finite(D.infinity)


def test_fractional_millis_become_nanos_rounded_ties_away_from_zero() -> None:
    # source 43-46, 469-470: a non-integer millis is stored as Nanos, never as a float
    D = load_duration()
    assert rep(D.millis(1.0005)) == ("Nanos", 1_000_500)
    # 0.0000025 * 1e6 is 2.5000000000000004 in doubles; upstream gives 3n / -3n
    assert rep(D.millis(0.0000025)) == ("Nanos", 3)
    assert rep(D.millis(-0.0000025)) == ("Nanos", -3)
    assert rep(D.seconds(1.5)) == ("Millis", 1500)  # 1.5 * 1000 is integral: stays Millis


def test_unit_constructors_scale_to_millis() -> None:
    # source 781-815; port 262 "equals"
    D = load_duration()
    assert rep(D.seconds(1)) == ("Millis", 1000)
    assert rep(D.minutes(1)) == ("Millis", 60_000)
    assert rep(D.hours(11)) == ("Millis", 39_600_000)
    assert D.equals(D.hours(1), D.minutes(60))


# --- conversions ---


def test_to_millis() -> None:
    # port 660 "toMillis"
    D = load_duration()
    assert D.to_millis(D.millis(1)) == 1
    assert D.to_millis(D.nanos(1)) == 0.000001
    assert D.to_millis(D.infinity) == math.inf
    # source 891-897
    assert D.to_millis(D.negative_infinity) == -math.inf
    assert D.to_millis(D.nanos(1_500_000)) == 1.5
    assert D.to_millis(D.nanos(SAFE)) == 9007199254.740992  # Number(nanos) / 1e6, as upstream


def test_to_nanos_unsafe() -> None:
    # port 680 "toNanosUnsafe"
    D = load_duration()
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
    D = load_duration()
    big = 999_999_999_999_999_000
    assert D.to_nanos_unsafe(D.nanos(big)) == big
    assert D.to_nanos_unsafe(D.from_input_unsafe(D.nanos(big))) == big
    assert rep(D.nanos(SAFE)) == ("Nanos", SAFE)


# --- from_input_unsafe (Duration.ts:275) ---


def test_from_input_unsafe_numbers_and_durations() -> None:
    # port 14 "fromInputUnsafe": a Duration passes through as the same object; a number is millis
    D = load_duration()
    millis100 = D.millis(100)
    assert D.from_input_unsafe(millis100) is millis100
    assert rep(D.from_input_unsafe(100)) == ("Millis", 100)
    assert rep(D.from_input_unsafe(0.5)) == ("Nanos", 500_000)
    assert rep(D.from_input_unsafe(math.nan)) == ("Millis", 0)


def test_from_input_unsafe_strings() -> None:
    # port 14: the nano rows; source 279-326 for the units TestClock.test.ts uses
    D = load_duration()
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
    D = load_duration()
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
    D = load_duration()
    with pytest.raises(ValueError, match="Invalid Input"):
        D.from_input_unsafe(bad)


# --- comparison ---


def test_order() -> None:
    # port 193 "Order"
    D = load_duration()
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
    D = load_duration()
    assert D.equals(D.nanos(1_000_000), D.millis(1))
    assert D.nanos(1_000_000) == D.millis(1)
    assert hash(D.nanos(1_000_000)) == hash(D.millis(1))
    assert D.nanos(SAFE) != D.nanos(SAFE - 1)
    assert D.infinity == D.infinity
    assert D.infinity != D.negative_infinity


# --- arithmetic ---


def test_sum() -> None:
    # port 399 "sum"
    D = load_duration()
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
    D = load_duration()
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
