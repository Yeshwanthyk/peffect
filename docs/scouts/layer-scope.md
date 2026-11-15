# Effect v4 Layer / Scope / Runtime notes

Scope: the checked-in `vendor/effect-v4` source under `packages/effect/src` (no runtime/build tools run). Citations below are paths relative to that `src` directory. The wording distinguishes direct source behavior from Python design implications.

## 1. Layer representation

- **Source fact:** `Layer<ROut,E,RIn>` is an interface with `build(memoMap, scope): Effect<Context<ROut>,E,RIn>`; it is not merely an `Effect<Context>` value. `LayerProto` supplies variance and pipeability; each layer instance is created by `Object.create(LayerProto)` and gets its own `build` field. (`Layer.ts:56-61`, `Layer.ts:302-320`.)
- **Meaning:** the function resembles `(memoMap, scope) => Effect<Context>`, but that function's captured identity is a first-class part of the layer object and is used by MemoMap as a key. (`Layer.ts:245-249`, `Layer.ts:315-320`, `Layer.ts:429-430`.)
- `fromBuild` forks a child scope for the layer build and closes it on failure. `fromBuildMemo` wraps that with `memoMap.getOrElseMemoize(self, scope, build)`. (`Layer.ts:345-370`, `Layer.ts:408-412`.)

## 2. MemoMap semantics

- **Key:** `Map<Layer object, MemoMapEntry>`; this is layer-instance identity, not structural equality or service tag. A child MemoMap checks its local map then its parent; child-local builds stay local while inherited entries can be reused. (`Layer.ts:418-430`, `Layer.ts:432-440`.)
- **Concurrent same-instance build:** the first requester synchronously installs an entry and builds in the entry's private scope; later requesters get the same already-completed deferred effect or await `Deferred.await(entry.deferred)`. Completion resolves the Deferred with the build's `Exit`, so failures/interruption reach waiters too. (`Layer.ts:245-263`, `Layer.ts:432-468`.)
- **Observer lifetime:** every requesting open scope increments `observers` and receives a finalizer. On scope close, the count drops; only the last observer deletes the map entry and closes the entry-owned scope, passing through that observer's `Exit`. A closed requesting scope cannot own a shared entry, so that request builds directly in its scope. (`Layer.ts:253-275`, `Layer.ts:444-466`.)
- **Interruption invariant:** the exit handler is installed before publishing the new entry, and completes the Deferred even if the first builder is interrupted; observer counting/finalizer registration are synchronous to avoid stranded counts. (`Layer.ts:445-467`, `Layer.ts:268-275`.)
- **Fresh:** `Layer.fresh(layer)` invokes `layer.build` against a newly allocated MemoMap on each fresh layer build. It intentionally breaks normal instance sharing; source tests assert same named layer builds once while `fresh` causes a second build. (`Layer.ts:2207-2208`; `test/Layer.test.ts:527-538`.)

## 3. Composition and constructors

| API | Build behavior |
|---|---|
| `merge` | Delegates to `mergeAll(self, that)`; outputs are merged Contexts. (`Layer.ts:1329-1360`.) |
| `mergeAll` | Forks a parallel parent scope, gives each input its own sequential child scope, builds with concurrency equal to layer count, then merges all Contexts. Shared same-instance dependencies still deduplicate through the common MemoMap. (`Layer.ts:1209-1223`; `Layer.ts:1275-1281`.) |
| `provide` | `provideWith` builds the dependency layer(s) first; then builds `self` with dependency Context provided. Returns only self's Context. Thus dependency construction is sequential before dependent construction. (`Layer.ts:1361-1380`; `Layer.ts:1463-1510`.) |
| `provideMerge` | Same build order as `provide`; combines dependency and self Contexts (self's services take precedence through `Context.merge(that,self)`). (`Layer.ts:1582-1638`.) |
| `succeed` | Creates a one-service Context, then uses `succeedContext`, which is an already-successful build. (`Layer.ts:827-839`, `Layer.ts:885-886`.) |
| `effect` | Maps the effect's value into a one-service Context and routes through `effectContext`; the effect executes with the layer scope provided. (`Layer.ts:1039-1058`, `Layer.ts:1099-1101`.) |
| Scoped acquisition | This source file exposes no `Layer.scoped` constructor. Scope-bound acquisition is composed with `Effect.acquireRelease` inside `Layer.effect` / `effectContext`; `acquireRelease` registers release into the current layer scope. (`Layer.ts:1039-1058`, `Layer.ts:1099-1101`; `internal/effect.ts:4240-4258`.) |
| `effectDiscard` | Maps a construction effect's result to `Context.empty()`: side effects/resources still execute, but no services are exposed. (`Layer.ts:1133-1134`.) |

The standard `merge` path is parallel; `provide`/`provideMerge` are dependency-first and then dependent. Do not infer a single global order for parallel finalizers: scopes explicitly select sequential or parallel finalizer strategy. (`Layer.ts:1209-1223`; `Layer.ts:1369-1378`; `Scope.ts:47-52`.)

## 4. Scope and resource acquisition

- **Representation:** a scope has strategy (`sequential | parallel`), optional parent, and mutable state `Empty | Open | Closed`; open state stores either one finalizer or a Map of keyed finalizers. Construction defaults to sequential. (`Scope.ts:47-53`; `internal/effect.ts:4177-4195`, `internal/effect.ts:4138-4166`.)
- `addFinalizerExit` registers a function receiving the closing `Exit`; adding to an already-closed scope executes it immediately with the scope's stored exit. `addFinalizer` adapts a no-argument finalizer. (`internal/effect.ts:4118-4135`.)
- `close(scope, exit)` is uninterruptible, marks the scope closed before running finalizers, detaches it from its parent, then executes finalizers in reverse registration order. Sequential strategy runs one-by-one; parallel strategy forks finalizer fibers and awaits them. (`internal/effect.ts:4033-4056`, `internal/effect.ts:4077-4100`.)
- Finalizer failures do not prevent later finalizers: sequential close collects their exits and combines causes; if the original exit already failed, finalizer causes are combined with it. (`internal/effect.ts:4060-4100`.)
- `fork` creates a child scope and installs a parent finalizer that closes the child with the same Exit. Forking from a closed parent yields an already-closed child. (`internal/effect.ts:4103-4115`.)
- `acquireRelease` masks acquisition and finalizer registration as one operation; unless explicitly requested, acquisition is uninterruptible. After acquire succeeds it registers release in the current scope, passes the eventual scope Exit into release, and restores the captured Context for release. (`internal/effect.ts:4240-4258`.)

## 5. `Effect.provide(layer)` at runtime

`provideLayer` is `scopedWith`: allocate a fresh scope for this provided effect; build the layer with either a new MemoMap when `local: true`, or the current fiber's memo map; provide the produced Context to the user effect; close the fresh scope with that effect's Exit. Consequently layer resources live for the dynamic extent of the provided effect, including its completion/failure/interruption. (`internal/layer.ts:14-28`; `internal/effect.ts:4231-4238`.) The default memo map is obtained via `CurrentMemoMap.forkOrCreate`, so nested provision in a runtime shares memoized entries subject to their observer scopes. (`Layer.ts:710-724`.)

## 6. Runtime and ManagedRuntime

- `Runtime.ts` is the host entrypoint/teardown adapter (`makeRunMain`, exit-code mapping), not the long-lived layer owner; the managed layer-runtime abstraction is `ManagedRuntime`. (`Runtime.ts:1-14`; `ManagedRuntime.ts:120-129`.)
- `ManagedRuntime.make(layer)` allocates one MemoMap, a root sequential scope, a sequential layer scope and parallel fiber scope. Its lazy `contextEffect` starts one build fiber if absent; all callers await that fiber, and successful Context is cached. (`ManagedRuntime.ts:325-382`.)
- `context()` returns cached Context after build, otherwise evaluates `contextEffect`. `runFork` and the other run methods use cached Context when available; otherwise they provide the runtime and trigger context construction. (`ManagedRuntime.ts:384-399`, `ManagedRuntime.ts:425-429`.)
- Disposal is idempotent/coordinated: first caller marks disposed, prevents future context build, clears cache, closes root scope; later external callers await completion. This interrupts/awaits fibers registered in `fiberScope` and closes layer resources through `layerScope`. (`ManagedRuntime.ts:332-345`, `ManagedRuntime.ts:400-423`.)

## 7. Invariants to carry forward

1. One live construction per Layer object per MemoMap; every consumer scope participates in the resource lifetime. (`Layer.ts:432-466`.)
2. No published entry can leave concurrent waiters unresolved when the builder exits, even by interruption. (`Layer.ts:445-467`.)
3. Scope closure is one-way and idempotent; late finalizers run immediately with the already-recorded Exit. (`internal/effect.ts:4033-4056`, `internal/effect.ts:4118-4129`.)
4. Finalizers receive the acquisition scope's closing Exit; resource release is registered only after successful acquisition. (`internal/effect.ts:4240-4258`.)
5. `provide` requires dependency Context before building the dependent layer; `merge` is independent/concurrent. (`Layer.ts:1209-1223`, `Layer.ts:1369-1378`.)
6. A MemoMap is not just a cache: it coordinates in-flight construction and owns the shared layer's child scope until all observing scopes release it. (`Layer.ts:245-275`, `Layer.ts:432-468`.)

## 8. Python design implications and prototype gap

**Inference from source:** the draft's phrase “Layer is an Effect that builds a Context, with a MemoMap of Deferreds” captures the output and in-flight sharing idea, but should state the explicit `build(memo_map, scope)` protocol and per-layer identity. (`Layer.ts:56-61`, `Layer.ts:245-249`.)

**Inference from source vs the supplied `pfx/src/pfx/layer.py`:** the current prototype memoizes `dict[id(layer)] -> Context` in the Scope, without an in-flight Deferred, observer count/finalizer ownership per requesting scope, or a memo entry-owned child scope. Therefore concurrent builds can race and duplicate acquisition; caching also does not model when the shared resource may be released. Python `id()` is only an approximation of the source's object-identity map key, with scope/lifetime semantics being the larger gap. (`Layer.ts:245-275`, `Layer.ts:432-468`; prototype comparison is based on the supplied file.)

For asyncio, a faithful minimum needs: (a) `Layer.build(memo_map, scope) -> Effect[Context]`; (b) per-MemoMap identity map entry with a Future/Deferred carrying the complete success/failure/cancellation outcome; (c) atomic-under-event-loop entry publication plus waiter registration; (d) per-consumer-scope observer finalizers and a shared entry scope closed only on last observer; (e) scope state and parent-child ownership, finalizers receiving Exit, reverse order, sequential/parallel policy, and failure aggregation; (f) `provide` building dependencies before dependent with Context injection, while `merge` builds independently concurrently; (g) ManagedRuntime lazy one-time build/cache and explicit idempotent async dispose. These are design implications, not claims about the current Python implementation. Source anchors: `Layer.ts:56-61`, `Layer.ts:245-275`, `Layer.ts:1209-1223`, `Layer.ts:1361-1380`, `internal/effect.ts:4033-4100`, `ManagedRuntime.ts:325-423`.

**Unsettled here:** exact tested behavior for finalizer defects under parallel closure and acquisition interruptibility options is visible in implementation but not exhaustively characterized by tests in these notes; Python policy should be chosen to preserve the Cause/Exit contract rather than assuming `AsyncExitStack` equivalence.
