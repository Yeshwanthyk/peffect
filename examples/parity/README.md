# TypeScript parity reference

Source-derived application reference, not an upstream test port. It runs the
`docs/FIRST_USABLE_SLICE.md` scenarios on the real pinned Effect v4. Its output is the baseline
for the Python counterpart. Comparing the two is the parent's harness work; this directory does
not do it.

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

The checker was probed both ways:
- Making block 3 valid turned the check red (exit 1).
- Adding a wrong annotation to `positive.ts` turned it red as well.

## Limits

- This is one application, not schema equivalence. Effect Schema and Pydantic keep their own
  diagnostics.
- The layer-build order and the release-before-`cancel.returned` order come from one runtime
  (bun). They were not re-run under node.
- `negative.ts` is excluded from the `tsc -p` gate on purpose; `check.ts` is its gate.
