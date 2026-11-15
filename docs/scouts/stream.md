# Effect v4 stream/channel/pull notes

Scope: vendored Effect v4 source under `packages/effect/src` (snapshot in this checkout). Claims below cite source-relative paths and lines. This is an implementation reading, not a claim that every API behavior is covered.

## Shape

- **Pull is one effectful step.** `Pull<A, E, Done, R>` extends `Effect<A, E | Cause.Done<Done>, R>`: success gives a value, ordinary failure gives `E`, and normal exhaustion is represented as the special `Cause.Done<Done>` failure, optionally carrying a completion value/leftover. The type and module contract state this explicitly (`Pull.ts:4-5`, `Pull.ts:23-42`); helpers exclude and catch Done separately (`Pull.ts:81-137`, `Pull.ts:165-` ).
- **Channel is a typed transformation, not a stored collection.** Its parameters are output element/error/done, input element/error/done, and environment (`Channel.ts:146-154`). Runtime channels are objects whose transform accepts upstream `Pull` plus `Scope` and returns an Effect producing the output Pull (`Channel.ts:259-309`). So the useful mental type is `(upstream Pull, Scope) -> Effect[Pull]`; input and output types remain distinct. `toPull` supplies the completed upstream (`Cause.done()`), obtains the scoped transform, and serializes pull evaluation with a semaphore (`Channel.ts:8678-8726`).
- **Stream wraps a chunk channel.** `Stream<A,E,R>` has a readonly channel emitting `NonEmptyReadonlyArray<A>` chunks, with `void` completion and unused unknown input (`Stream.ts:130-137`). `fromChannel` maps channel chunks back to stream elements (`Stream.ts:338-341`). A stream pull therefore returns a nonempty chunk, not one element (`Stream.ts:11551-11553`).
- **Chunking is an implementation choice, not the stream's public element type.** `fromIterable` uses a specialized array path by default, otherwise `Channel.fromIterableArray`; the latter advances an iterator up to normalized `chunkSize`, emits the nonempty buffer, and defers completion value until the next pull (`Stream.ts:999-1012`, `Channel.ts:791-824`).

## How operations compose

- `map` is lazy/suspended, maps each element within each chunk, and carries an index across chunks (`Stream.ts:1769-1790`). This preserves chunk boundaries.
- `flatMap` first converts each source chunk into individual elements (`Channel.flattenArray`), then uses `Channel.flatMap` to select each inner channel; concurrency and buffer size are optional (`Stream.ts:2357-2390`).
- `merge` delegates to `Channel.merge` (`Stream.ts:3087-3130`). Channel merge builds two pulls and coordinates their pulls; the lower-level concurrent merge path uses child scopes/fibers, a queue, optional semaphore/concurrency control, and closes child scopes on exit (`Channel.ts:6430-6630`). This is why merge is not merely alternating two iterators. The queue mediates emitted values and failures; finite queue capacity supplies backpressure (`Channel.ts:477-495`, `Channel.ts:6538-6590`).
- `runCollect` folds every chunk into a flat array (`Stream.ts:11129-11139`). `runForEach` invokes the effect per element, iterating each chunk sequentially (`Stream.ts:11378-11412`). Channel's consumer loops pull until Done through `runWith`; `runForEach` is implemented as repeated pull/effect (`Channel.ts:8325-8355`), while `runCollect` is a fold (`Channel.ts:8465-8472`).
- `take(n)` normalizes the count, returns empty at zero, otherwise delegates to `takeUntil` with the final allowed index (`Stream.ts:6682-6690`). `Channel.haltWhen` forks a watcher in a forked scope and checks its exit before each pull, so termination propagates as completion or failure (`Channel.ts:7452-7480`).
- `scoped` runs a channel under a provided child scope and removes `Scope` from its environment requirement (`Channel.ts:7195-7207`); `Stream.scoped` is this channel operation (`Stream.ts:1749-1755`). Scope finalizers close resources when execution ends. For background channel producers, the queue is registered for shutdown and the producer is forked into the channel scope (`Channel.ts:477-495`).

## Kernel implications and Python design

- **Not fully derivable from a tiny pure kernel.** Sequential pull/map/collect can be expressed over Effect plus Scope; meaningful concurrent merge and scoped producers use Fiber, Queue, and Scope directly in this source (`Channel.ts:477-495`, `Channel.ts:6538-6590`). The local design draft already places fibers and Scope in its kernel (`DESIGN.md:9-17`, `DESIGN.md:46-70`). Queue can potentially be layered over async/fiber primitives, but bounded backpressure and cancellation-safe shutdown need an explicit abstraction or equivalent kernel support.
- **Backpressure is demand-driven by pull.** A consumer requests the next pull only after handling the prior chunk; merge's bounded queue blocks producers when full. With a zero/unbounded capacity policy the behavior differs, so expose and document capacity rather than hiding it (`Channel.ts:477-495`, `Channel.ts:6538-6590`).
- **Python recommendation (inference from the representation):** define `Pull[Chunk[A], E]` as an async effectful `pull()` returning a nonempty chunk and raising typed stream error or a private `Done` sentinel; keep `Done` outside ordinary user errors. Model `Channel` as a scoped factory from upstream pull to output pull, retaining its input/output/error/done type parameters even if Python's runtime generics cannot encode all unions. Model `Stream[A,E]` as the narrower chunk channel. Start with a single-consumer pull protocol plus `from_iterable`, map, take, fold/collect, and for-each; add `Scope` ownership/finalizers before resource streams, then bounded Queue + cancellable child tasks for merge/flatMap concurrency. An async generator is a convenient adapter for simple sequential sources, but by itself does not encode typed Done, upstream channel composition, or explicit concurrent resource ownership.

## Invariants to preserve

1. A successful pull always emits a nonempty chunk; end-of-stream is not an empty chunk (`Stream.ts:130-137`, `Channel.ts:791-824`).
2. Completion and failure are distinct outcomes even though Pull encodes completion in its failure channel (`Pull.ts:23-42`).
3. Pull state advances once per evaluation; `toPull` serializes concurrent callers (`Channel.ts:8678-8726`).
4. Scope owns child fibers, queues, and finalizers; completion, error, and interruption must close resources (`Channel.ts:477-495`, `Channel.ts:6538-6590`, `Channel.ts:7195-7207`).
5. Bounded buffering must suspend producers when full, and early downstream termination must cancel/close upstream work; otherwise `take` and merge leak work or defeat backpressure (`Channel.ts:6538-6590`, `Stream.ts:6682-6690`).

### Gaps

The file is intentionally a first-slice design note: it does not trace every `flatMap`/merge halt strategy or resource API, and no interpreter/build/test was run per the read-only source constraint. `DESIGN.md` was used only for kernel context; it is not vendored Effect source and therefore its references are labeled separately.
