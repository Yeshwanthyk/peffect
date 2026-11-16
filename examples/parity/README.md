# EffectJS / Python application parity

Source-derived application reference, not an upstream test port. It runs the
`docs/FIRST_USABLE_SLICE.md` scenarios on the real pinned Effect v4. Its output is the baseline
for the Python counterpart. `compare.py` runs both applications fresh and checks their outcomes,
resource counts and complete ordered event logs. All 20 scenarios currently match.

## Reading and reusing the code

Start with `python_app.py`, `python_composed.py` and `python_batch.py`: these are the
application modules. The TypeScript counterparts are `app.ts`, `composed.ts` and `batch.ts`. They use the existing public
Effect/Context/Layer/resource APIs; no kernel access, type suppressions, casts, or custom
runtime machinery are needed.

| pattern to reuse | concrete example |
|---|---|
| typed service contract and stable tag key | frozen service dataclasses and `Tag` declarations in `python_app.py` |
| validation at the request edge | `handle`: `attempt(..., ValidationError)` before Layer acquisition |
| capture dependencies when building a service | `make_checkout` yields services once and returns typed operations |
| share one dependency across a diamond | `make_checkout_app` and `RiskLive` reuse the same `UsersLive` object |
| resource lifetime | `acquire_release` for service resources; `acquire_use_release` for a reservation |
| selective error recovery | `place_or_pending`, with its remaining error union pinned in typetests |
| per-run state | `EventLog` Reference provided with a fresh sink by the runner; never a cached mutable default |
| replace a service | `make_checkout_app(payments)`; business logic is unchanged |
| build a feature on an existing app | `make_batch_app` provides the unchanged checkout app plus a Ledger to `BatchLive` |
| side effect that keeps the value | `settle_and_record`: `tap(ledger.append)` in a three-step `pipe` |
| turn each error into data | `settle_one` and `settle_and_record` recover by tag until the error channel is `Never` |

`python_scenarios.py` / `composed_scenarios.ts` and `python_batch_scenarios.py` /
`batch_scenarios.ts` are deterministic test fixtures, not application services. The two reference runners own observation and serialization. `compare.py` is a test
tool. Keep that separation when copying the example. Its inventory and payment implementations
are deliberately in-memory; replace them with real adapters for an actual application.

To use this as a source reference, pin the peffect revision as well as the Effect vendor revision,
retain the README's limits, and rerun the comparison plus typing checks after adapting it.
This checkout does not yet contain a project LICENSE; a public redistribution invitation needs
the owner's licensing decision. This example does not supply that permission.

## Pinned source

- Effect: `vendor/effect-v4/packages/effect/src`, version 4.1.4, submodule commit
  `289a53f7b1fc40049219c6e69022db2fb859c356`. Verify the pin with `git ls-tree HEAD vendor/effect-v4`.
- Nothing is installed into vendor. `tsconfig.json` `paths` maps `effect` and `effect/*` to the
  vendored `.ts` source. The package has no runtime dependencies. Bun honors those paths at
  run time, and `bun -e 'console.log(import.meta.resolve("effect"))'` (run here) prints the
  vendored `src/index.ts`.
- The only installed package is `typescript` 6.0.3, exact, in `./node_modules` (gitignored; `bun.lock` pins it).
  TypeScript 5.9.3 reports 2 errors inside vendored `internal/effect.ts` and `Layer.ts`, so it is
  not used. TypeScript 6.0.3 compiles the vendored source and this directory with no errors.
- Runtime: bun 1.3.13 (`/opt/homebrew/bin/bun`).

## Commands

From the repository root, run the complete runtime comparison:

```sh
uv run python -m examples.parity.compare
uv run python -m examples.parity.compare --output-dir /tmp/peffect-parity
uv run python -m examples.parity.python_reference
uv run pytest -q tests/test_application_parity.py tests/test_types.py
```

The comparator checks the vendor SHA, source modifications and actual resolved Effect import.
It never reads captured output as a substitute for running either application. It writes raw
outputs plus `comparison.json` to the printed directory. A mismatch is a nonzero exit with a
diff. Only validation error names (`SchemaError` / `ValidationError`) and diagnostic text are
normalized. Domain errors, JSON value types, counts and event order are compared exactly.

Run each from `examples/parity/`:

```sh
/opt/homebrew/bin/bun install --frozen-lockfile        # typescript only
/opt/homebrew/bin/bun run reference.ts                 # scenarios → JSON (reference.output.json)
/opt/homebrew/bin/bun run fixtures/check.ts            # typing fixtures → JSON (fixtures/check.output.json)
./node_modules/.bin/tsc -p tsconfig.json               # app, runner and positive fixture typecheck
```

## The application (`app.ts`)

- **Validation.** `user_id` must be an integer, with `1 <= user_id <= 1_000_000`. It uses
  `Schema.Int` with range checks, so the string `"1"` is rejected, not coerced. The Pydantic
  counterpart is `StrictInt, Field(ge=1, le=1_000_000)`. Diagnostic texts differ by library;
  they are reported as `diagnostic_not_compared`.
- **Services and layers.** The services are `Repo`, `Users`, `Notifier`, `Greeter` and `Auditor`.
  - `RepoLive` is `Layer.effect` over `Effect.acquireRelease`, so its release gets the scope's Exit.
  - `UsersLive` needs Repo.
  - `makeApp(users, notifier)` gives one `users` layer to both consumers (`Greeter`, `Auditor`)
    through `Layer.mergeAll(...).pipe(Layer.provide(users), Layer.provide(notifier))`.
- **The request edge.** `handle` decodes first, then calls `Effect.provide(useCase, app)`, so
  invalid input never builds a layer.
- **Cancellation.** It waits on a `Deferred` that the hanging Notifier completes once the Repo
  is acquired, then calls `Fiber.interrupt`. No wall-clock sleeps.

## Captured results (`reference.output.json`, two runs byte-identical)

| scenario | outcome | repo acquire/release | lifecycle after `request.valid` |
|---|---|---|---|
| success | `"hello ann"` | 1/1 | acquire, builds, `notify:hello ann`, `release:success` |
| invalid_input_range (`0`) | validation, `SchemaError` | 0/0 | none |
| invalid_input_type (`"1"`) | validation, `SchemaError` | 0/0 | none |
| domain_failure_after_acquire (`99`) | domain, `UserNotFound{userId:99}` | 1/1 | acquire, builds, `release:failure`, then `terminated:failure` |
| domain_recovery (`99`) | `"hello guest"` | 1/1 | `recovered:UserNotFound:99`, `release:success` |
| shared_layer (`2`, both consumers) | `{greeting:"hello bob", audit:"audited 2"}` | 1/1 | one acquire, one `users.build`, both consumer builds |
| service_replacement | `"hello test-user"` | 0/0 | `test-users.get:1`, no repo events |
| cancellation_after_acquire | interrupted | 1/1 | `notify.started`, `cancel.requested`, `release:interrupt`, `cancel.returned` |

Build order in every app run: `repo.acquire`, `users.build`, `greeter.build`, `auditor.build`.
That order is what the pinned `Layer.mergeAll` produced here. A port must reproduce it or record
the difference; it must not be normalized away. The output contains no fiber ids or stack traces.

## Composed checkout (`composed.ts`, `python_composed.py`)

The second application adds Inventory, Payments, Risk and Checkout service tags. Checkout needs
all four dependencies: Users, Inventory, Payments and Risk. Risk itself is built with the same
UsersLive layer, so the graph contains a real diamond. MemoMap must share Users and its Repo
across both paths: every checkout scenario acquires and releases the Repo exactly once.

Repo, Inventory and Payments have Layer-scoped lifetimes. Each order also uses
`acquireUseRelease` / `acquire_use_release` for a reservation. The error channel is the union
`UserNotFound | OutOfStock | PaymentDeclined`. Recovery catches only PaymentDeclined and the
typing fixtures require both other errors to remain.

| scenario | result and lifecycle assertion |
|---|---|
| composed_success | paid receipt; reservation closes before the three service resources |
| composed_user_failure | UserNotFound; no reservation or payment attempted |
| composed_stock_failure | OutOfStock; no payment or reservation release after failed acquisition |
| composed_payment_failure | PaymentDeclined; reservation and all resources close with failure |
| composed_payment_recovery | pending receipt; reservation sees failure, outer resources see success |
| composed_payment_replacement | sandbox Payments implementation; unchanged checkout logic |
| composed_parallel_batch | two workers reach Deferred gates before release; results retain input order |
| composed_batch_cancellation | both reservations and shared resources release before cancellation returns |

The batches use concurrency 2. Deferred gates establish overlap and cancellation timing; there
are no sleeps or fixed-yield guesses. A wall-clock watchdog only prevents a broken run hanging.
These are deterministic in-memory application services, not real inventory/payment clients.
No stock-accounting, database transaction, network, or external payment guarantee is claimed.

## Batch settlement (`batch.ts`, `python_batch.py`)

The third module is a feature added on top of the checkout application without editing it.
`Batch.settle` places each order (concurrency 2) through the existing `place_or_pending` use case
and appends one line per order to a `Ledger`. Settlement never fails: `PaymentDeclined` is already
a pending receipt, `UserNotFound` and `OutOfStock` become `rejected` lines, and a Ledger that
refuses a line (`LedgerFull`) leaves that order `unrecorded`.

- **Shared resource.** The Ledger is Layer-scoped (`ledger.open`, `ledger.close:<exit>:<lines>`).
  `make_audited_batch_app(ledger)` / `makeAuditedBatchApp(ledger)` supplies the chosen Ledger
  to both the audited Payments replacement and Batch. They share one layer identity, including
  when a custom capacity is selected. Capacity checks and appends run atomically at execution
  time, so preconstructing or replaying an append effect cannot bypass the limit.
- **Tap.** `ledger.append` runs as a tap: the settled line stays the value, and the tap's
  `LedgerFull` enters the error channel, where the next pipe step recovers it by tag.
- **Types.** Both sides declare `settle_one` as `Effect<Line, never, Checkout>` and the composed
  per-order effect as `Effect<Line>`; pyright and tsc check those annotations against the pipes.

| scenario | result and lifecycle assertion |
|---|---|
| batch_settle_mixed | four orders give paid, pending, rejected, rejected lines in input order; Ledger closes with 4 lines before Payments, Inventory and Repo |
| batch_settle_audited_payments | one `ledger.open`; charge and settlement lines interleave in one Ledger, which closes with 4 lines |
| batch_settle_custom_audit | a custom capacity-1 Ledger opens once and closes with one combined line |
| batch_settle_cancellation | order 1 is recorded, order 2 hangs in its charge; the interrupt releases the reservation, Ledger (1 line), Inventory and Repo before it returns |
| batch_settle_ledger_full | capacity 1: order 2 is charged, `ledger.full:2` fails the tap, recovery yields an `unrecorded` line and every resource closes with success |

In these scenarios each order runs to completion before the next starts, because no step
suspends; only the cancellation scenario has a suspended worker. The traces therefore pin
result order, resource sharing and release order, not interleaving of concurrent settlements.
The Ledger is an in-memory list. An `unrecorded` line means the order was charged and not
audited; a real system needs its own compensation there, and none is claimed.

## Typing fixtures (`fixtures/`, `fixtures/check.output.json`)

`check.ts` compiles both files with the TypeScript compiler API and this tsconfig.
- `positive.ts` must produce 0 diagnostics.
- Each `// N. NAME` block in `negative.ts` must produce at least one diagnostic of its own, with
  none outside the blocks.
- No TypeScript diagnostic suppressions or untyped escape hatches are used.

The negative blocks and the diagnostic each produced:
1. MISSING_DEPENDENCY: TS2379.
2. LAYER_MISSING_DEPENDENCY: TS2379. A Users layer that still needs Repo is passed to `makeApp`.
3. WRONG_IMPLEMENTATION: TS2322.
4. UNDECLARED_ERROR: TS2322.
5. UNKNOWN_ERROR_TAG: TS2345.
6. FORGED_SERVICE: TS2353.
7. COMPOSED_REMAINING_ERRORS: recovering payment failure cannot erase user/stock errors.
8. COMPOSED_MISSING_SERVICE: Checkout still needs to be provided.
9. COMPOSED_WRONG_PAYMENT_ERROR: Payments cannot declare an inventory failure.

Python counterparts live in `typetests/should_pass.py` and blocks 75–83 of `should_fail.py`.
The existing per-block harness checks each rejection. One difference is explicit: Python
`catch_tag` accepts a nonmatching error class but preserves the original error channel; TS
rejects an unknown tag at the call. Python block 79 checks that it cannot falsely erase the error,
and the positive fixture pins the retained type. This is sound but not identical API typing.

The checker was probed both ways:
- Making block 3 valid turned the check red (exit 1).
- Adding a wrong annotation to `positive.ts` turned it red as well.

## Limits

- This is one application, not schema equivalence. Effect Schema and Pydantic keep their own
  diagnostics. Python's StrictInt also rejects an integral Python float such as `1.0`; JS has
  a single number type. The acceptance scenarios use JSON integer values, not that distinction.
- The layer-build order and the release-before-`cancel.returned` order come from one runtime
  (bun). They were not re-run under node.
- `negative.ts` is excluded from the `tsc -p` gate on purpose; `check.ts` is its gate.
- This does not implement ManagedRuntime, TestClock, or any new kernel primitive. The comparison
  establishes these 20 application traces, not arbitrary program equivalence.
- The batch module has no typing fixtures of its own: no negative block proves that a missing
  Ledger or an unrecovered `LedgerFull` is rejected. Its annotations are checked by pyright and tsc.
