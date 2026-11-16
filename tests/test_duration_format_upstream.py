"""Effect v4's `Duration.format`, ported test-first (vendor/effect-v4 289a53f).

Upstream: src/Duration.ts:1854-1895 (`format`), :1789-1836 (`parts`), :623 (`abs`); tests from
test/Duration.test.ts:497-507 and :740-742. `Effect.timeout`'s message embeds it
(internal/effect.ts:3954-3956).

Labels: "port <line>" is a direct port of an upstream assertion; "source" cases are derived from
the source, and every expected string was read by running the pinned upstream module under bun
(`Duration.format` on the same input), never computed with the code under test.

Adaptations:
- peffect has no `days`/`weeks` constructors; those inputs go through `from_input_unsafe("2 days")`
  and similar. `format` reads only the value's nanoseconds, so the representation (Millis or
  Nanos) a constructor picks does not change the result.
- A bigint input is a Python `int` passed to `nanos`.
- JS prints a whole-day count as a JS number: integer digits below 1e21, then exponent notation
  (`1e+21`, `1.1574074074074075e+26`), and `Infinity` past the double range.
"""

import pytest

import peffect.duration as D
from peffect.duration import Duration


def test_format_port_497() -> None:
    """Duration.test.ts:497-507."""
    assert D.format(D.infinity) == "Infinity"
    assert D.format(D.minutes(5)) == "5m"
    assert D.format(D.minutes(5.325)) == "5m 19s 500ms"
    assert D.format(D.hours(3)) == "3h"
    assert D.format(D.hours(3.11125)) == "3h 6m 40s 500ms"
    assert D.format(D.from_input_unsafe("2 days")) == "2d"
    assert D.format(D.from_input_unsafe("2.25 days")) == "2d 6h"
    assert D.format(D.from_input_unsafe("1 week")) == "7d"
    assert D.format(D.zero) == "0"


def test_format_port_740_negatives() -> None:
    """Duration.test.ts:740-742."""
    assert D.format(D.seconds(-5)) == "-5s"
    assert D.format(D.negative_infinity) == "-Infinity"


# (input, upstream Duration.format) -- each expected string measured from the vendor under bun.
SOURCE_CASES: list[tuple[str, Duration, str]] = [
    # zero in every representation
    ("millis(-0)", D.millis(-0.0), "0"),
    ("nanos(0)", D.nanos(0), "0"),
    ("millis(1e-7) rounds to 0ns", D.millis(0.0000001), "0"),
    ("millis(-4e-7) rounds to 0ns", D.millis(-0.0000004), "0"),
    ("input 1e-7", D.from_input_unsafe(1e-7), "0"),
    # fractional millis and sub-millisecond nanos
    ("millis(1.5)", D.millis(1.5), "1ms 500000ns"),
    ("millis(-1.5)", D.millis(-1.5), "-1ms 500000ns"),
    ("millis(5e-7) rounds ties away", D.millis(0.0000005), "1ns"),
    ("input 0.1", D.from_input_unsafe(0.1), "100000ns"),
    ("millis(3_599_999.5)", D.millis(3_599_999.5), "59m 59s 999ms 500000ns"),
    ("millis(59_999)", D.millis(59_999), "59s 999ms"),
    ("millis(86_400_001)", D.millis(86_400_001), "1d 1ms"),
    ("millis(-93_784_500)", D.millis(-93_784_500), "-1d 2h 3m 4s 500ms"),
    # exact nanos
    ("nanos(1)", D.nanos(1), "1ns"),
    ("nanos(-1)", D.nanos(-1), "-1ns"),
    ("nanos(1_000_001)", D.nanos(1_000_001), "1ms 1ns"),
    ("nanos(93_784_005_000_006)", D.nanos(93_784_005_000_006), "1d 2h 3m 4s 5ms 6ns"),
    ("nanos(-93_784_005_000_006)", D.nanos(-93_784_005_000_006), "-1d 2h 3m 4s 5ms 6ns"),
    (
        "nanos(999_999_999_999_999_000)",
        D.nanos(999_999_999_999_999_000),
        "11574d 1h 46m 39s 999ms 999000ns",
    ),
    # large inputs: the day count is a JS number
    ("nanos(10**30)", D.nanos(10**30), "11574074074074074d 1h 46m 40s"),
    ("nanos(86_400 * 10**20)", D.nanos(86_400 * 10**20), "100000000000d"),
    ("nanos(86_400 * 10**21)", D.nanos(86_400 * 10**21), "1000000000000d"),
    (
        "nanos(86_400 * (10**21 - 1))",
        D.nanos(86_400 * (10**21 - 1)),
        "999999999999d 23h 59m 59s 999ms 913600ns",
    ),
    ("nanos(86_400 * 10**30)", D.nanos(86_400 * 10**30), "1e+21d"),
    (
        "nanos((2**53 + 1) days)",
        D.nanos(9_007_199_254_740_993 * 86_400_000_000_000),
        "9007199254740992d",
    ),
    ("nanos(10**40)", D.nanos(10**40), "1.1574074074074075e+26d 17h 46m 40s"),
    ("nanos(-(10**40))", D.nanos(-(10**40)), "-1.1574074074074075e+26d 17h 46m 40s"),
    ("nanos(10**330)", D.nanos(10**330), "Infinityd 1h 46m 40s"),
    ("nanos(-(10**330))", D.nanos(-(10**330)), "-Infinityd 1h 46m 40s"),
    ("millis(1e21)", D.millis(1e21), "11574074074074d 1h 46m 40s"),
    ("millis(2**53 - 1)", D.millis(9_007_199_254_740_991), "104249991d 8h 59m 991ms"),
    ("millis(1e300)", D.millis(1e300), "1.1574074074074075e+292d 23h 42m 20s 160ms"),
    (
        "millis(max double)",
        D.millis(1.7976931348623157e308),
        "2.0806633505350875e+300d 22h 14m 18s 368ms",
    ),
    # infinities through other constructors
    ("millis(inf)", D.millis(float("inf")), "Infinity"),
    ("millis(-inf)", D.millis(float("-inf")), "-Infinity"),
    ("input 'Infinity'", D.from_input_unsafe("Infinity"), "Infinity"),
    # the inputs timeout messages use
    ("input '1.5 seconds'", D.from_input_unsafe("1.5 seconds"), "1s 500ms"),
    ("input 10", D.from_input_unsafe(10), "10ms"),
    ("input '1 minute'", D.from_input_unsafe("1 minute"), "1m"),
    ("input '10 millis'", D.from_input_unsafe("10 millis"), "10ms"),
    ("input '1500 micros'", D.from_input_unsafe("1500 micros"), "1ms 500000ns"),
    ("input '1 nano'", D.from_input_unsafe("1 nano"), "1ns"),
]


@pytest.mark.parametrize(("label", "duration", "expected"), SOURCE_CASES, ids=lambda x: str(x))
def test_format_source_matches_the_pinned_upstream(
    label: str, duration: Duration, expected: str
) -> None:
    """Source-derived (Duration.ts:1789-1895); expected strings measured from the vendor."""
    assert D.format(duration) == expected, label
