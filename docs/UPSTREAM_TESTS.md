# Upstream tests ported from Effect v4

Upstream is pinned at `vendor/effect-v4`, commit `289a53f7b1fc40049219c6e69022db2fb859c356` (Effect v4.1.4). Paths below are under `vendor/effect-v4/packages/effect/test/`. Each Python test's docstring names its upstream `file:line` and title.

Rule: upstream semantics are the contract. A test keeps upstream's scenario and assertions; only syntax is translated, and each translation is written down in the test file's docstring. No skip or xfail. A name peffect doesn't have yet is looked up inside the test (`upcoming(...)`), so that test fails with `AttributeError` while the rest of its file still runs.

Status values:
- **green**: ported, passes against current code.
- **red: missing API**: ported, fails with `AttributeError` on a name that doesn't exist yet.
- **red: behavioral**: ported, an assertion fails against an API that exists.
- **planned**: batch not ported yet.
- **deferred**: not ported, with the reason.

## Departures from upstream

| # | Upstream | peffect | Effect on the tests |
|---|---|---|---|
| D1 | A service key is a string (`Context.Service("ContextTest/A")`) | The key is the tag class | Order assertions compare classes |
| D2 | `Scope` strategy `"sequential"` (default) or `"parallel"` | Both, sequential first | Parallel cases are batch G, tier 3. Deferred, not dropped |
| D3 | `Context.makeUnsafe(map)` keeps the caller's map | The map is copied on install | That case is not ported |
| D4 | Interrupt-at-install forced by a custom `Scheduler.shouldYield` | No scheduler hook: the interrupt is requested inside the `update_context` merge, at the same point | Same scenario and assertion (`observed is original`) |
| D5 | Duration strings (`"1 minute"`) | Seconds as `float`, as `sleep` takes today | Only cases that need the unported Duration API are deferred (see below) |
| D6 | `fiber.pollUnsafe()` | `fiber.exit`, read in tests | none |
| D7 | `Effect.forever`, `Effect.delay` | `@gen` `while True`, `sleep(...).flat_map(...)` | No new API |

## Batch A: Context gaps — `tests/test_context_upstream.py`

| Upstream | Python test | Status |
|---|---|---|
| Context.test.ts:17 keeps the source immutable across additions | `test_keeps_the_source_immutable_across_additions` | green |
| Context.test.ts:31 preserves Map insertion order for replacements and appends | `test_preserves_insertion_order_for_replacements_and_appends` | red: missing API (`Context.empty`) |
| Context.test.ts:104 distinguishes an undefined service from an absent service | `test_distinguishes_an_undefined_service_from_an_absent_service` | green |
| Context.test.ts:278 supports the ReadonlyMap surface through mapUnsafe | `test_supports_the_readonly_map_surface_through_services` | green |
| Context.test.ts:292 supports equality and JSON materialization | `test_supports_equality` | red: behavioral (no `Context.__eq__`, so `==` is identity) |
| Context.test.ts:304 merges with right bias and preserves empty operand identity | `test_merges_with_right_bias_and_preserves_empty_operand_identity` | red: missing API (`Context.empty`) |
| Context.test.ts:332 resolves reference defaults lazily and caches them | `test_resolves_reference_defaults_lazily_and_caches_them` | green |
| Effect.test.ts:405 Context.Service | `test_context_service` | green |
| Effect.test.ts:131 provideService restores the original context if interrupted before its body | `test_provide_service_restores_the_original_context_if_interrupted_before_its_body` | green (D4) |

Equality follows `Context.ts:637-649`, not dict equality: both sides must be Contexts, same size, same keys, `Equal.equals` values, any order; the hash is the size. A plain mapping with the same items is not equal.

Deferred from `Context.test.ts`: `toJSON`, Redactable, `pick` / `omit` / `addOrOmit` (no such API yet), the `makeUnsafe`-retains-map case (D3), and tests of upstream internals. The pure getters (`getOption`, `getUnsafe`, `get`) have no peffect counterpart; membership in `services` and a lookup inside `provide` assert the same facts.

## Batch B: kernel finalization — `tests/test_finalization_upstream.py`

All of `Effect.test.ts:3153-3270` (describe "finalization").

| Upstream | Python test | Status |
|---|---|---|
| :3160 onExit preserves the original failure when the finalizer throws | `test_on_exit_preserves_the_original_failure_when_the_finalizer_throws` | red: behavioral (got `Die(FIN)` only; the body failure is lost) |
| :3169 onExit reports a thrown finalizer on success | `test_on_exit_reports_a_thrown_finalizer_on_success` | green |
| :3175 an outer onInterrupt still sees the interruption after an inner finalizer throws | `test_an_outer_on_interrupt_still_sees_the_interruption_after_an_inner_finalizer_throws` | red: missing API (`on_interrupt`) |
| :3195 nested throwing finalizers do not overflow the stack | `test_nested_throwing_finalizers_do_not_overflow_the_stack` | green (20,000 deep) |
| :3203 fail ensuring | `test_fail_ensuring` | green |
| :3216 fail on error | `test_fail_on_error` | red: missing API (`on_error`) |
| :3231 finalizer errors merged | `test_finalizer_errors_merged` | red: behavioral (`Fail(EXAMPLE)` is not elected to `Die`) |
| :3249 finalizer errors reported | `test_finalizer_errors_reported` | green |

Approved fix, after review of this red evidence: repair it in `on_exit`, the primitive that owns it, not in Scope. Keep every reason, and elect defects as upstream's `causeElectDefects` does (`internal/effect.ts:184-191`): when a cause holds a Die, each Fail becomes a Die.

## Batch C: Clock — `tests/test_clock.py`

| Upstream | Python test | Status |
|---|---|---|
| Clock.test.ts:5 keeps wall time aligned while exposing the monotonic source | `test_keeps_wall_time_aligned_while_exposing_the_monotonic_source` | red: missing API (`Clock`) |

`time.time_ns` and `time.monotonic_ns` are monkeypatched in place of `Date.now` and `process.hrtime.bigint`.

## Batches D–G: planned

| Batch | File | Ports | Needs | Status |
|---|---|---|---|---|
| D: TestClock | `tests/test_testclock.py` | `TestClock.test.ts` sleep, setTime and adjust groups | C | planned |
| E: Scope, sequential | `tests/test_scope.py` | `Scope.test.ts` sequential variants: interrupted close, direct close after interrupt, masked `closeUnsafe`, throw-then-continue, sole throwing finalizer keeps the failure, the 4 fork tests | A, B | planned |
| F: Scope with effects | `tests/test_scope_effects.py` | `Effect.test.ts` scoped `:86`, acquireRelease `:1389` `:1407`, scoped failures `:3271`, forkIn `:3038-3071`, forkScoped `:3083-3116`, updateServiceScoped `:5334` `:5345` `:5359`, acquireUseRelease `:322` `:349` `:377` `:2578` `:2598` | E | planned |
| G: parallel Scope | `tests/test_scope_parallel.py` | `Scope.test.ts` parallel variants (4), `Effect.test.ts:3073`, TestClock "handles multiple sleeps" (needs `forkScoped`) | D, E, F | deferred to tier 3 (D2) |

Decisions already made for these batches:
- **E and F** may use a small test-only latch built on the public `async_`. No fake Scope or Clock behavior.
- **Nanoseconds.** Python ints are exact, so nanosecond precision is not unavailable in general. Only a case that needs the unported Duration API is deferred, and the reason is recorded when it is.
- **`copy.copy`.** TestClock's "detached, copied or inherited" case uses JS object spread. Before using `copy.copy` in its place, check that it gives the same observable result the test asserts; don't assume the two are equivalent.

Deferred elsewhere in these upstream files:
- TestClock warning tests: they need TestConsole.
- The timeout variant of the cancelled-deadline test: tier 3.
- Layer tests: tier 4.
- `MaxOpsBeforeYield`: no scheduler hook.
- The `updateServiceScoped` reset option: no such option yet.
