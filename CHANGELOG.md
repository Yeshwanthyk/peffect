# Changelog

## 0.1.1

- Add TestClock with tracked wake settling, exact nanosecond time, cancellation and live-time warnings.
- Add Console, header-only Logger and TestConsole, plus scope-owned `fork_scoped`.
- Widen Clock millisecond types to float for fractional and non-finite virtual time.
- Preserve upstream NaN deadline behavior; skip time reads when logging is disabled; distinguish emitted Severity from filter LogLevel.

Validation: all 12 verifier steps pass, including 639 tests, strict typing, boundaries and
21 real Python/EffectJS application comparisons. Independent review findings are corrected
and retained as runtime/type regressions. The kernel is unchanged.

Limits: Console has five methods and Logger is a header-only subset. One trailing-yield
mutation remains indistinguishable under the current suite; upstream behavior is retained,
with no equivalence claim. Warning checks observe bounded live-time windows.

## 0.1.0

Initial private release of the implemented Effect v4 subset for Python 3.13+.

- Typed effects, errors, services and context; fibers, cancellation and scoped resources.
- Layer composition and ManagedRuntime; Deferred, Latch, Semaphore and Option.
- Duration and Clock; concurrent traversal, races and timeouts.
- Composed Python and EffectJS examples with 21 matching application scenarios.

Validation: 558 implemented-surface tests passed on the release source before packaging;
independent reviews, generated tests and bounded kernel verification are recorded in docs.
The 16 TestClock draft tests are for an unimplemented feature and fail on missing peffect.testing.
TestClock and its Console/Logger support remain on build/primitives for the next release.
This is an initial subset, not full EffectJS parity. See docs/UPSTREAM_TESTS.md for limits.
