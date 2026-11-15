"""Duration: a span of time, exact to the nanosecond. A port of the subset of Effect v4's
packages/effect/src/Duration.ts (vendor/effect-v4, commit 289a53f) that Clock and TestClock use.

A Duration holds one of four cases (Duration.ts:139 `DurationValue`): `Millis` (a JS number, here
a `float`), `Nanos` (a JS bigint, here an unbounded `int`), `Infinity` and `NegativeInfinity`.
Exact nanoseconds are only ever `int`, so they never lose precision through a float.

A Python `int` passed where upstream takes a JS number (`millis`, `seconds`, an `Input`) becomes
one the way JS `Number(bigint)` does: rounded to the nearest double, +-Infinity past its range.
Mapping, adaptations and the boundary cases: tests/test_duration_upstream.py.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Never, final, overload


@final
@dataclass(frozen=True, slots=True)
class Millis:
    millis: float


@final
@dataclass(frozen=True, slots=True)
class Nanos:
    nanos: int


@final
@dataclass(frozen=True, slots=True)
class Infinity:
    pass


@final
@dataclass(frozen=True, slots=True)
class NegativeInfinity:
    pass


type DurationValue = Millis | Nanos | Infinity | NegativeInfinity

_TOKEN: Final = object()


@final
class Duration:
    """Built only by the constructors below, which normalize like upstream's private `make`."""

    __slots__ = ("_value",)
    _value: DurationValue

    def __init__(self, value: DurationValue, token: object) -> None:
        if token is not _TOKEN:
            raise TypeError("build a Duration with millis, nanos or from_input_unsafe")
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> DurationValue:
        return self._value

    def __setattr__(self, name: str, value: object) -> Never:
        raise AttributeError("Duration is immutable")

    def __delattr__(self, name: str) -> Never:
        raise AttributeError("Duration is immutable")

    def __eq__(self, that: object) -> bool:  # Duration.ts:425, Equal.symbol
        return isinstance(that, Duration) and equals(self, that)

    def __hash__(self) -> int:  # Duration.ts:415-424: canonical nanoseconds for finite values
        match self._value:
            case Millis(m):
                return hash(_round_millis_to_nanos(m))
            case Nanos(n):
                return hash(n)
            case v:  # Hash.structure of the tag: the two infinities hash apart
                return hash(type(v).__name__)

    def __repr__(self) -> str:
        return f"Duration({self._value!r})"


type Input = Duration | int | float | str
"""Duration.ts:203. A number is millis; tuple and DurationObject inputs are not ported yet."""


# --- JS number arithmetic -------------------------------------------------------------------


def _number(x: float) -> float:
    """JS `Number(x)`: an int too large for a double is +-Infinity, not an OverflowError."""
    if isinstance(x, float):
        return x
    try:
        return float(x)
    except OverflowError:
        return math.inf if x > 0 else -math.inf


def _round_ties_away_from_zero(x: float) -> int:  # Duration.ts:43
    a = abs(x)
    whole = math.floor(a)
    rounded = whole + 1 if a - whole >= 0.5 else whole
    return -rounded if x < 0 else rounded


def _round_millis_to_nanos(millis: float) -> int:  # Duration.ts:45
    if millis.is_integer():
        return int(millis) * 1_000_000
    return _round_ties_away_from_zero(millis * 1_000_000)


def _trunc_divmod(n: int, d: int) -> tuple[int, int]:
    """JS bigint `/` and `%`: the quotient truncates toward zero, the remainder takes n's sign."""
    q = abs(n) // d
    q = -q if n < 0 else q
    return q, n - q * d


def _nanos_to_unit(nanos: int, divisor: int) -> float:  # Duration.ts:50
    value = _number(nanos)
    if math.isfinite(value):
        return value / divisor
    q, r = _trunc_divmod(nanos, divisor)
    return _number(q) + _number(r) / divisor


# --- make (Duration.ts:462) -----------------------------------------------------------------


def _make(value: DurationValue) -> Duration:
    return Duration(value, _TOKEN)


def _from_number(x: float) -> Duration:
    if math.isnan(x) or x == 0:
        return zero
    if math.isinf(x):
        return infinity if x > 0 else negative_infinity
    if not x.is_integer():
        return _make(Nanos(_round_millis_to_nanos(x)))
    return _make(Millis(x))


def _from_bigint(n: int) -> Duration:
    return zero if n == 0 else _make(Nanos(n))


zero: Final[Duration] = _make(Millis(0.0))
infinity: Final[Duration] = _make(Infinity())
negative_infinity: Final[Duration] = _make(NegativeInfinity())


def nanos(nanos: int) -> Duration:
    return _from_bigint(nanos)


def millis(millis: float) -> Duration:
    return _from_number(_number(millis))


def seconds(seconds: float) -> Duration:
    return _from_number(_number(seconds) * 1000)


def minutes(minutes: float) -> Duration:
    return _from_number(_number(minutes) * 60_000)


def hours(hours: float) -> Duration:
    return _from_number(_number(hours) * 3_600_000)


# --- from_input_unsafe (Duration.ts:246-330) ------------------------------------------------

# JS `\s` (WhiteSpace and LineTerminator) and ASCII-only `\d`; used with fullmatch, as JS `^...$`.
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_DURATION_REGEXP: Final = re.compile(
    rf"(-?[0-9]+(?:\.[0-9]+)?)[{_JS_SPACE}]+"
    r"(nanos?|micros?|millis?|seconds?|minutes?|hours?|days?|weeks?)"
)
_MILLIS_PER_UNIT: Final = {
    "milli": 1,
    "second": 1_000,
    "minute": 60_000,
    "hour": 3_600_000,
    "day": 86_400_000,
    "week": 604_800_000,
}


def _divide_ties_away_from_zero(numerator: int, denominator: int) -> int:  # Duration.ts:71
    a = abs(numerator)
    rounded = a // denominator + (1 if a % denominator * 2 >= denominator else 0)
    return -rounded if numerator < 0 else rounded


def _decimal_integer(text: str) -> int:
    """Parse validated ASCII digits without Python's process-wide decimal limit."""
    negative = text.startswith("-")
    digits = text[1:] if negative else text
    value = 0
    for start in range(0, len(digits), 600):
        chunk = digits[start : start + 600]
        value = value * 10 ** len(chunk) + int(chunk)
    return -value if negative else value


def _parse_nanos(text: str, scale: int) -> int:  # Duration.ts:58
    if "." not in text:
        return _decimal_integer(text) * scale
    negative = text[0] == "-"
    whole, fractional = text[1 if negative else 0 :].split(".")
    fractional_scale = 10 ** len(fractional)
    scaled = (_decimal_integer(whole) * fractional_scale + _decimal_integer(fractional)) * scale
    return _divide_ties_away_from_zero(-scaled if negative else scaled, fractional_scale)


def _from_millis_string(text: str, scale: int) -> Duration:  # Duration.ts:77
    millis = float(text) * scale
    if not math.isfinite(millis):
        return _from_number(millis)
    nanos = _parse_nanos(text, scale * 1_000_000)
    # Keep the millisecond representation only when it preserves the exact value.
    if millis.is_integer() and int(millis) * 1_000_000 == nanos:
        return _from_number(millis)
    return _from_bigint(nanos)


def _js_string(value: object) -> str:
    match value:
        case None:
            return "null"
        case bool():
            return "true" if value else "false"
        case _:
            return str(value)


def _invalid(value: object) -> Never:  # Duration.ts:384
    raise ValueError(f"Invalid Input: {_js_string(value)}")


def from_input_unsafe(input: Input) -> Duration:
    match input:
        case Duration():
            return input
        case bool():  # JS rejects booleans (typeof "boolean"); Python bool is an int
            return _invalid(input)
        case int() | float():
            return millis(input)
        case "Infinity":
            return infinity
        case "-Infinity":
            return negative_infinity
        case str():
            found = _DURATION_REGEXP.fullmatch(input)
            if found is None:
                return _invalid(input)
            text, unit = found.group(1), found.group(2).removesuffix("s")
            if unit == "nano":
                return nanos(_parse_nanos(text, 1))
            if unit == "micro":
                return nanos(_parse_nanos(text, 1_000))
            return _from_millis_string(text, _MILLIS_PER_UNIT[unit])
        case _:  # the Input union is closed, but an untyped caller can pass anything
            return _invalid(input)


# --- getters (Duration.ts:891, 1051) --------------------------------------------------------


def to_millis(input: Input) -> float:
    match from_input_unsafe(input).value:
        case Millis(m):
            return m
        case Nanos(n):
            return _nanos_to_unit(n, 1_000_000)
        case Infinity():
            return math.inf
        case NegativeInfinity():
            return -math.inf


def to_nanos_unsafe(input: Input) -> int:
    match from_input_unsafe(input).value:
        case Millis(m):
            return _round_millis_to_nanos(m)
        case Nanos(n):
            return n
        case Infinity() | NegativeInfinity():
            raise ValueError("Cannot convert infinite duration to nanos")


# --- pattern matching (Duration.ts:1140, 1198) ----------------------------------------------


@overload
def match[A, B, C](
    self: Duration,
    *,
    on_millis: Callable[[float], A],
    on_nanos: Callable[[int], B],
    on_infinity: Callable[[], C],
) -> A | B | C: ...
@overload
def match[A, B, C, D](
    self: Duration,
    *,
    on_millis: Callable[[float], A],
    on_nanos: Callable[[int], B],
    on_infinity: Callable[[], C],
    on_negative_infinity: Callable[[], D],
) -> A | B | C | D: ...
def match[A, B, C, D](
    self: Duration,
    *,
    on_millis: Callable[[float], A],
    on_nanos: Callable[[int], B],
    on_infinity: Callable[[], C],
    on_negative_infinity: Callable[[], D] | None = None,
) -> A | B | C | D:
    match self.value:
        case Millis(m):
            return on_millis(m)
        case Nanos(n):
            return on_nanos(n)
        case Infinity():
            return on_infinity()
        case NegativeInfinity():
            return on_infinity() if on_negative_infinity is None else on_negative_infinity()


def _match_pair[A](
    self: Duration,
    that: Duration,
    on_millis: Callable[[float, float], A],
    on_nanos: Callable[[int, int], A],
    on_infinity: Callable[[DurationValue, DurationValue], A],
) -> A:
    s, t = self.value, that.value
    if isinstance(s, Infinity | NegativeInfinity) or isinstance(t, Infinity | NegativeInfinity):
        return on_infinity(s, t)
    if isinstance(s, Millis) and isinstance(t, Millis):
        return on_millis(s.millis, t.millis)
    return on_nanos(to_nanos_unsafe(self), to_nanos_unsafe(that))


# --- predicates (Duration.ts:516-594) -------------------------------------------------------


def is_finite(self: Duration) -> bool:
    return isinstance(self.value, Millis | Nanos)


def is_zero(self: Duration) -> bool:
    match self.value:
        case Millis(m):
            return m == 0
        case Nanos(n):
            return n == 0
        case _:
            return False


def is_negative(self: Duration) -> bool:
    match self.value:
        case Millis(m):
            return m < 0
        case Nanos(n):
            return n < 0
        case Infinity():
            return False
        case NegativeInfinity():
            return True


# --- comparison and arithmetic (Duration.ts:1262-1645) --------------------------------------


def _compare(a: float, b: float) -> Literal[-1, 0, 1]:
    return -1 if a < b else 1 if a > b else 0


def _compare_infinities(s: DurationValue, t: DurationValue) -> Literal[-1, 0, 1]:
    if type(s) is type(t):
        return 0
    if isinstance(s, Infinity):
        return 1
    if isinstance(s, NegativeInfinity):
        return -1
    return -1 if isinstance(t, Infinity) else 1


def order(self: Duration, that: Duration) -> Literal[-1, 0, 1]:
    return _match_pair(self, that, _compare, _compare, _compare_infinities)


def equals(self: Duration, that: Duration) -> bool:
    return _match_pair(
        self,
        that,
        lambda s, t: s == t,
        lambda s, t: s == t,
        lambda s, t: type(s) is type(t),
    )


def _sum_infinities(s: DurationValue, t: DurationValue) -> Duration:
    if isinstance(s, Infinity) and isinstance(t, NegativeInfinity):
        return zero
    if isinstance(s, NegativeInfinity) and isinstance(t, Infinity):
        return zero
    if isinstance(s, Infinity) or isinstance(t, Infinity):
        return infinity
    return negative_infinity


def sum(self: Duration, that: Duration) -> Duration:
    return _match_pair(
        self,
        that,
        lambda s, t: _from_number(s + t),
        lambda s, t: _from_bigint(s + t),
        _sum_infinities,
    )


def _subtract_infinities(s: DurationValue, t: DurationValue) -> Duration:
    if isinstance(s, Infinity):
        return zero if isinstance(t, Infinity) else infinity
    if isinstance(s, NegativeInfinity):
        return zero if isinstance(t, NegativeInfinity) else negative_infinity
    return negative_infinity if isinstance(t, Infinity) else infinity


def subtract(self: Duration, that: Duration) -> Duration:
    return _match_pair(
        self,
        that,
        lambda s, t: _from_number(s - t),
        lambda s, t: _from_bigint(s - t),
        _subtract_infinities,
    )
