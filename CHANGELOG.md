# Changelog

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
