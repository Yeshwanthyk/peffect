# Effect v4 Context, services, and references

Scope: vendored Effect v4 source under `packages/effect/src` (Context.ts:11-13). This slice uses v4's `Context.Service` API; `Context.ts` defines Service and Reference types and constructors (Context.ts:103-108, 344-349, 209-275).

## Structure and types

`Context` is a typed immutable view over a runtime map keyed by **string**, specifically each key's `key` field—not the key object's identity. `Context.make` writes `key.key`; `add` routes to `addUnsafe(self, key.key, value)`; lookup compares/gets that string. Storage is `ReadonlyMap<string, any>` plus persistent overlay nodes, with flattening/caching to bound read and update costs. (Context.ts:478-499, 523-561, 766-770, 808-822)

`Key<Identifier, Shape>` extends `Effect<Shape, never, Identifier>` and carries the runtime service marker, phantom `Service: Shape`, phantom `Identifier`, and runtime `key: string`. Thus the Effect environment requirement `R` is `Identifier`, while the returned implementation is `Shape`. (Context.ts:68-73)

`Service<Identifier, Shape>` adds helpers `of`, `context`, `use`, and `useSync`. A normal required service key carries its identifier in the Effect environment. `Context.Service<Shape>("id")` is the function-style constructor; the two-stage `Context.Service<Self, Shape>()("id")` produces a class-style key. (Context.ts:103-108, 168-171, 209-251)

## Class-based service syntax and identity

```ts
class Config extends Context.Service<Config, {
  readonly greeting: string
}>()("Config") {}
const program = Effect.gen(function*() {
  const config = yield* Config
  return config.greeting
})
```

This is the documented class form and class-as-key usage. The resulting class value carries the service methods and key metadata; its `Self` type is the Identifier, and its shape is the declared implementation type. `ServiceClass` exposes the string literal `key`; generated `KeyClass` is configured with the shared service prototype. (Context.ts:110-134, 143-155, 217-251, 253-305; Effect.ts:6269-6276)

Crucial runtime distinction: the class value is the ergonomic key, but it is **not** the map identity; its string `key` is. The source explicitly warns that reusing a string key for unrelated services makes them share a Context slot. So nominal Python classes are a natural ergonomic/type-level match, but class-object dictionary keys are not a literal runtime match. They avoid string collisions; if matching v4, give each class/tag an explicit stable key and key storage by it. (Context.ts:120-121, 173-177, 546-561, 766-770)

## References: defaulted keys

`Reference<Shape>` extends `Service<never, Shape>`: it remains a service key and Effect value, but its Identifier is `never`, so reading it adds no requirement to `R`. It has a `defaultValue: () => Shape`, a reference marker, iterator support, and a class constructor signature. `Reference(key, { defaultValue })` is implemented as the same `Service` constructor with reference metadata attached. (Context.ts:344-349, 1390-1397, 252-275)

A missing ordinary key makes `getUnsafe` throw; a missing Reference instead resolves its default. The default is lazy and memoized on the reference object itself, so later Contexts/fibers reading that key reuse that single value; an explicit Context value overrides it. `getOption` also resolves defaults, while raw `getOrUndefined` does not. (Context.ts:1037-1049, 1089-1096, 987-999)

Built-in defaults cover runtime policy and ambient metadata: e.g. CurrentLogLevel defaults to `Info`, Scheduler is exposed as a Reference, and tracer/scheduler settings are read from References. These are dynamically overridable via the same service provisioning mechanism. (internal/references.ts:49-64; References.ts:230-234, 658-695; References.ts:419-437)

## Fiber lookup, updates, and provision

Each `FiberImpl` stores `context: Context<never>` and initializes/updates it through `setContext`; the latter also refreshes its fiber cache when relevant cached keys differ. A service key is itself an Effect: the shared prototype evaluator succeeds with `get(fiber.context, this)`. Therefore yielding a service key in `Effect.gen` reads the current fiber's Context; `Effect.service(key)` returns that key. (internal/effect.ts:521-526, 563-565, 790-805; Context.ts:277-284; internal/effect.ts:2254-2256; Effect.ts:6305-6326)

`provideService` builds `Context.add(current, key, implementation)` and uses `updateContext` to install it around the target effect. `updateContext` saves the previous Context and registers an `onExit` restoration, so the replacement is scoped to that dynamic region, including interruption/exit. `updateService` reads the current value, transforms it, adds a replacement, and uses the same scoped update/restore mechanism. (internal/effect.ts:2277-2293, 2297-2320, 2401-2432)

`provideContext` merges the supplied Context over the current Context for the region, so provided keys override collisions while unrelated outer services remain; its type removes the supplied requirements from `R`. `setContext` instead replaces the current Context completely. (internal/effect.ts:2378-2397; Context.ts:1188-1195; Effect.ts:6258-6263)

## Invariants and design implications for pfx

- Key types keep `Identifier` and `Shape` coupled statically; runtime Context values are unvalidated `any`, so the declared type does not check an implementation at lookup. (Context.ts:68-73, 478-483, 766-770)
- One string key means one slot, even if two key objects/classes were independently declared; unique explicit IDs are required to prevent accidental replacement. Missing required keys fail at runtime. (Context.ts:173-177, 1042-1048)
- Context additions return derived Context views; callers can keep using the prior Context value after an addition. (Context.ts:808-850)
- Reference defaults are lazy but cached once per Reference object, rather than once per Context or fiber. Mutable defaults therefore become shared mutable objects unless the default factory returns an immutable value or callers override it. (Context.ts:1091-1096)
- Provision is dynamically scoped and must restore the prior Context on all exits, including interruption; v4 tests this restoration case. (internal/effect.ts:2283-2292)

## Python translation guidance

peffect now follows v4: `class Users(Tag[UserRepo], key="app/Users")` keeps the nominal class for R and the protocol shape, and a Context is keyed by the explicit `key` string, so same-key declarations share a slot as in v4 (INVARIANTS C10). (Context.ts:129-155, 546-561)

Represent Context as an immutable/persistent mapping from the explicit key string to implementation; updates should derive a new mapping. Keep the runtime Fiber's current Context explicit and have tag access read that Fiber; use an async context manager/try-finally around `provide` to swap then restore context on success, failure, and cancellation. For `update_service`, derive the modified Context and reuse that same scoped swap. (internal/effect.ts:2283-2293, 2314-2319, 2427-2432)

Add `Reference[T]` as a separate Tag subtype/value that holds a lazy default factory and contributes no required environment type. `get` should use the provided override if present, otherwise the default; decide deliberately whether defaults cache per Reference (v4 behavior) or per fiber/context. For asyncio, ambient settings like scheduler/log level/tracer fit this mechanism, but keep service implementations and References distinct from execution primitives only if the Python design can preserve scoped override and restoration semantics. (Context.ts:344-349, 1042-1048, 1091-1096; internal/references.ts:49-64)

## Gaps / cautions

The source establishes string-key lookup, class-based authoring, and fiber-scoped restoration. peffect preserves v4's string collision behavior; distinct tag declarations with the same key address the same slot. Static requirements remain nominal tag classes. The default cache is attached to the Reference object, which may surprise callers expecting per-run mutable defaults. (Context.ts:120-121, 546-561, 1091-1096)
