"""Option: a value that may be absent, with presence carried by a wrapper, not by the value.

Pinned Effect v4 Option.ts:266-385 and internal/option.ts:28-100: `some(a)` is `Some(a)`,
`none()` is `None`. `Some(None)` is present, so a computation returning None stays
distinguishable from no result. Only construction, the value and structural equality are ported.
"""

from __future__ import annotations

from typing import final


@final
class Some[A]:
    """A present value. Equal and hashed by value, like internal/option.ts:32-39. Covariant like
    upstream's `Some<out A>` (Option.ts:133): the value is a read-only property and the instance
    rejects every write, so a Some[int] is a Some[object]. A frozen dataclass would type `value`
    as a mutable field and make A invariant."""

    __slots__ = ("_value",)
    __match_args__ = ("value",)

    def __init__(self, value: A) -> None:
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> A:
        return self._value

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"Some is immutable: cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Some is immutable: cannot delete {name!r}")

    def _key(self) -> object:
        """The value as `object`: comparable across differently parameterized Somes."""
        return self._value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Some) and bool(self._key() == other._key())

    def __hash__(self) -> int:
        return hash((Some, self._value))

    def __repr__(self) -> str:
        return f"Some({self._value!r})"


type Option[A] = Some[A] | None
