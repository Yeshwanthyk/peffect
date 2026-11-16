# Source complexity gate

Install the locked development dependencies (no global Harrow installation needed):

```sh
uv sync --locked
uv run harrow-code --version  # harrow 0.4.1
uv run python scripts/check_complexity.py --output-dir /tmp/peffect-complexity
```

The gate is a small policy adapter over **native Harrow reports**, not another analyzer.
It runs `harrow-code metrics src/peffect` and `harrow-code check . --select callback-family
--format json --max-findings 10000`, then the source-only pipe signature check. These tools
parse source; they do not import or execute peffect. The gate also runs in the normal pytest
suite (`tests/test_complexity_gate.py`), hence in the full library verifier.

It fails on missing/wrong Harrow, unsupported schema/metric version, diagnostics, omitted or
duplicate source files, omitted/suppressed findings, an incomplete callback detector, renamed
hotspots, changed function inventories, or increased measured burden. Raw stdout/stderr are
saved even on analysis failure. All Python files under `src` must be represented. Native
metrics has no `complete` field: schema/version, diagnostics, exact file coverage and function
inventories are checked instead. Other callback-check detectors are deliberately unselected;
this gate does not replace boundary lint, typing, a full audit or behavioral verification.

## What is ratcheted

`scripts/complexity-policy.json` records each file's sum of cyclomatic decision contributions
(`cyclomatic - 1` per function), cognitive sum, individual maxima and function-name inventory
hash. Lambda coordinates are ignored, but owner and multiplicity remain. Named hotspots have
additional exact identities/ceilings. Every reported lexical callback family has scope/depth,
aggregate cognitive and directly captured-writer ceilings. Findings are **not suppressed** or
hidden in a Harrow findings baseline.

A helper extraction changes the inventory even if individual scores fall. Moving branches
into an existing helper still counts against file totals. Moving work across files requires
review of both files' owners and budgets. There is no automatic policy-refresh command.
An intentional change of inventory, family identity or ceiling requires an explicit policy
edit and whole-family review in the accompanying log: authoritative state, every writer
(including aliases/method calls), phases, synchronous reentry, cleanup, extracted helpers and
a concrete competing design. Static lexical analysis cannot prove ownership or detect every
coordination transfer; the policy is a review tripwire, not a universal maintainability proof.

## Current deliberate complexity

| Operation | Cyclomatic / cognitive | Reason / disposition |
|---|---:|---|
| Context `_equal_values` | 15 / 16 | Identity, numeric NaN, exact type, cycles and recursive containers are different contracts. Removed two repeated float decisions, retained container protocol. |
| `Fiber.evaluate` | 15 / 15 | Retained interpreter boundary. Resumption allowance expires after one operation; failed-dispatch yield suppression lasts the slice. Deferred interrupt delivery, exception conversion, running restoration and parked-tail delivery cannot share one flag or disappear into a helper. |
| Queue `offer_all_unsafe` | 11 / 15 | Retained distinct bulk extension and finite admission. `islice/ceil` changes NaN/fractional semantics; repeated single offers change scheduling and remaining suffix. |
| Queue `offer_unsafe` | 10 / 10 | Normal admission is now a guard return, not nested under overflow policy. Kept literal `>=` (its inverse is **not** `<` for NaN). |
| Duration `from_input_unsafe` | 9 / 10 | Nano/micro scales share the same exact parser/constructor. Millisecond representation, bool rejection and invalid-input dispatch remain distinct. |

Traversal deliberately trades two additional measured decisions and one predicate scope for
independent, authoritative cancellation and failure facts. Exhaustion is derived from the
index, rather than synchronized in `done`; cancellation no longer writes a success sentinel
into a failure accumulator. Lexical family cognitive is **51 → 52**, scopes **11 → 12**, depth
**2 → 2**, direct captured bindings **5 → 5**. This is an ownership improvement, **not** a
claimed complexity-score reduction. A state class would still need per-pump observer closures
and add fields for immutable invocation inputs; it does not remove the protocol.

Semaphore retains the existing request family (7 scopes, cognitive 5). A request object would
need the same start/register/recheck/leave phases plus a semaphore back-reference and captured
continuation/restore fields. No coordination disappears. Semaphore remains the permit and
queue-membership authority; `_Waiter` owns only requested permits and optional resume. Wake
clears resume without reserving permits. Exit-frame-before-queue and atomic remove/release
scheduling are intentional, tested contracts, not complexity debt declared fixed.

## Behavior evidence is separate

Every small refactor requires focused Python tests **and fresh real EffectJS differential
execution before the next refactor**. `tests/test_cleanup_parity.py` verifies vendor pin
`289a53f7b1fc40049219c6e69022db2fb859c356`, clean vendor source and direct vendored import
resolution. `uv run pytest -q -s tests/test_cleanup_parity.py` prints paired results/events.
It never falls back to stored JSON. `uv run python -m examples.parity.compare` additionally
runs the real composed applications.

Compared contracts include numeric Context equality, selected Duration inputs (including
invalid runtime inputs), Queue strategies/raw capacities and generator order, traversal result
order and cancellation/failure cleanup order, semaphore overtaking and wake-then-cancel with
permit stealing, pipe order, and a capacity-one producer/worker drain. Fiber identity counters
are process-local: only reason kinds/payloads and their order are compared, not numeric IDs.
Python structural container/custom equality and float-subclass handling are explicitly separate
adaptation tests, **not** assertions of JavaScript parity. These are finite observed probes,
not universal equivalence, arbitrary-interleaving proofs or performance measurements.
