# Effect v4 Config and Data: implementation notes

Scope: the checked-out `packages/effect/src` tree supplied for this scout. The implementation identifies these APIs as Effect 4 (`since 4.0.0` in Config/ConfigProvider); I did not inspect a package version or pin a commit. Citations are paths relative to that `src` directory. Findings are from source reading, not execution.

## 1. Config representation, execution, errors

- `Config<A>` is a typed recipe and an `Effect.Effect<A, ConfigError>` at the same time; it is yieldable and pipeable. Its internal representation stores an evaluator `(provider, pathPrefix) -> Effect<Resolution<A>, ConfigError>` and exposes `parse(provider)` to run it with an explicit provider. (`Config.ts:92-127,146-160`)
- The Effectable prototype evaluates a Config by reading the current `ConfigProvider` fiber reference and calling `parse`; therefore `yield* Config.String("NAME")` inside `Effect.gen` asks the active provider without explicit `parse`. (`Config.ts:131-144`; `ConfigProvider.ts:351-354`)
- `Config.schema(codec, path?)` is Schema-backed: it obtains the codec's encoded shape, builds a cursor-aware decoding schema, loads provider nodes at the requested path, and runs `SchemaParser.decodeUnknownEffect`. The `String`, `Int`, `Boolean`, etc. helpers are shortcuts over this schema route. (`Config.ts:854-886,961-1062`)
- The provider node algebra is deliberately smaller than the decoded domain: `Value` is a string leaf; `Record` has child keys and optional co-located scalar value; `Array` has length and optional scalar value. Schema interprets these nodes into typed values. (`ConfigProvider.ts:58-75`; `Config.ts:854-866`)
- `ConfigError` has `_tag = "ConfigError"`, `name`, a `cause` that is either `SourceError` or `SchemaError`, and a `message` derived from `cause.toString()`. It is not a union of arbitrary strings. (`Config.ts:76-90`)
- Internally, parsing distinguishes recoverable “no usable input” from effect failure using `Result`: missing input is represented as a failed resolution until a combinator handles it; invalid supplied input becomes `Effect.fail(ConfigError)`. Source errors are converted back to typed ConfigError failures at the schema boundary. (`Config.ts:120-126,854-884`; `Config.ts:639-653`)

## 2. ConfigProvider

- `ConfigProvider` is a service interface with `load(path) -> Effect<Node | undefined, SourceError>` and `mapInput(transform) -> ConfigProvider`. `undefined` means path absent; a found container may still have `value: undefined` to mean “container, no scalar at this node.” (`ConfigProvider.ts:278-313,58-75`)
- The exported `ConfigProvider` value is a `Context.Reference`, whose default factory is `fromEnv()`. It can be yielded as a service, explicitly replaced with `provideService`, or installed as a `Layer`; `layerAdd` composes a primary and fallback provider. (`ConfigProvider.ts:318-354,682-744`)
- `fromUnknown(root)` adapts an in-memory JavaScript value, commonly an already-parsed JSON object; traversal uses string keys and numeric array indices, stringifies primitive numbers/booleans/bigints, treats empty strings as missing by default, and does not fail with `SourceError`. This source does not expose a `fromJson(text)` constructor: for JSON text, the Config docs direct callers to Schema's `fromJsonString` codec. (`ConfigProvider.ts:744-810`; `Config.ts:825-840`)
- `fromEnv` reads the runtime environment (or a supplied record), joining path segments with `_`. It also builds a trie from underscore-separated names so `DATABASE_HOST` is discoverable both as a flat key and under `DATABASE`; all-numeric child names form an array node, other children a record node. Empty strings default to missing. (`ConfigProvider.ts:892-955,958-1004`)
- `nested(prefix)` prepends one or more path segments through `mapInput`; it composes with provider transformations, so transformation order is observable. (`ConfigProvider.ts:600-650`)

## 3. TaggedError, yieldability, and equality

- `Data.TaggedError(tag)` constructs a subclass of `Data.Error`, whose underlying base is `Cause.YieldableError`, itself a subclass of JavaScript `Error`. So it is an Error subclass and can be yielded directly: `yield* new MyErr(fields)` fails the enclosing Effect with that error as the typed failure value. (`Data.ts:727-781`; `internal/core.ts:667-683,689-718`; `Cause.ts:1158-1187`)
- The tag is an instance `readonly _tag` field initialized from the constructor's tag argument; `_tag` is omitted from the accepted field argument type. The internal tagged-error base sets the class prototype's `name` to the tag. (`Data.ts:777-781`; `internal/core.ts:714-718`)
- Structural equality is not special to `TaggedError`: `Equal.equals` compares ordinary objects structurally and custom `Equal` implementations must pair equality with a compatible hash. `Data.Class` documents equal same-field instances; the generic structural hash/equality uses enumerable object keys. `Hash.hash` caches object hashes, so mutating after hashing is unsafe. (`Data.ts:35-57`; `Equal.ts:72-80,177-180,239-260`; `internal/core.ts:82-94`; `Hash.ts:108-168,429-468`)

## 4. Data constructors

- `Data.Class` supplies a Pipeable base and copies constructor properties; its public type marks fields `Readonly`. It does not add a tag or make the object an Effect failure. (`Data.ts:35-58`)
- `Data.TaggedClass(tag)` is the same value-class pattern plus a readonly `_tag`; `_tag` is excluded from user constructor fields. (`Data.ts:62-101`)
- `Data.TaggedEnum<Definition>` describes a discriminated union where each variant key supplies the `_tag`; `Data.taggedEnum()` returns a constructor per variant, `$is(tag)` tag guards, and `$match` dispatch. Runtime constructors produce plain objects; `$is` checks only the tag, so it is not validation for untrusted values. (`Data.ts:108-160,571-645`; `Data.ts:581-589,641-685`)
- `Data.Error` is the untagged yieldable-error base; `Data.TaggedError` adds tag-based recovery compatibility such as `Effect.catchTag`. (`Data.ts:710-781`)

## 5. Invariants to preserve

1. Keep absence distinct from bad input: fallback/default/optional behavior applies to absence, while a present value that fails decoding remains a typed configuration failure. (`Config.ts:120-126,486-497,854-884`)
2. Keep source acquisition distinct from decoding: providers return a small node shape or source error; Schema owns validation and conversion into domain values. (`ConfigProvider.ts:58-75,278-313`; `Config.ts:854-884`)
3. A config recipe is not a settings object captured at construction: it resolves its provider when evaluated, so provider replacement and layer composition govern the read. (`Config.ts:131-144`; `ConfigProvider.ts:351-354,682-744`)
4. Treat structural hash inputs as immutable after hashing; equality implies equal hashes for custom `Equal` values. (`Hash.ts:89-90,108-168`; `Equal.ts:72-80`)
5. Tags are useful discriminators, not complete validation: tagged enum `$is` only checks `_tag`. (`Data.ts:581-589`)

## 6. Python implications for pydantic-settings and pfx TaggedError

- `pydantic-settings` as a Layer is a reasonable Python boundary for loading a complete settings model. Compared with Effect Config, it collapses provider path traversal, path remapping, composition/fallback, per-field decoding recipes, and evaluation-time provider lookup into the settings loader unless pfx deliberately models those capabilities separately. That comparison is an inference from the two APIs: Effect's provider is a path-based service and Config is a deferred schema recipe. (`Config.ts:92-127,854-886`; `ConfigProvider.ts:278-313,351-354,682-744`)
- For closer Effect parity, keep the settings loader but model provider/source separately from decoding if callers need nested paths, overlays, or test-time provider substitution. Distinguish missing keys from invalid values so defaults do not hide validation failures. (`ConfigProvider.ts:278-313,492-500,797-810`; `Config.ts:486-497,854-884`)
- The supplied pfx description says `TaggedError` is a frozen Pydantic model, not an Exception. Effect v4's `TaggedError` is explicitly an `Error` subclass and directly yieldable. If parity is the goal, make a tagged domain error usable as an exception and preserve `_tag`; keep it as a value in the typed `Effect[E]` failure channel rather than relying on Python `raise` for ordinary Effect failure. (`Data.ts:727-781`; `internal/core.ts:667-718`; `Cause.ts:1158-1187`)
- Yieldability is a separate protocol from subclassing: Effect's yieldable error supplies an iterator whose yielded Effect has `never` success and the error as its failure; `Effect.gen` turns that into a typed failure. For pfx, prefer explicit `fail(err)` as the semantic core, then add `yield from err` only if its generator DSL can express the same typed channel unambiguously. (`Cause.ts:1158-1187`; `internal/core.ts:667-683`)
- Structural equality/hash is also a separate contract from Pydantic frozen-model equality: if pfx needs hash collections or cross-type structural comparison, define explicit `Equal`/`Hash` semantics and require equal values to hash equally; otherwise rely on Python value-model equality and avoid claiming Effect compatibility. (`Equal.ts:72-80,112-113`; `Hash.ts:108-168`)

## Gaps

- I did not read tests, run code, inspect package metadata, or pin the vendored source to a commit; behavior claims above are source-read findings, not runtime-checked. The exact compatibility of Python `Exception` + Pydantic model inheritance and a `yield from` error protocol remains a pfx design/implementation question.
