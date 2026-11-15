# Effect v4 Cause & Exit

Scope: read-only inspection of the checked-in `packages/effect/src` implementation and focused tests. This describes the vendored v4 snapshot; no interpreter/build/test was run.

## 1. Cause representation

The v4 `Cause<E>` representation is not a `Then`/`Both` sequencing/parallelism tree. It is branded and stores `reasons: ReadonlyArray<Reason<E>>`; `Reason` is the discriminated union `Fail<E> | Die | Interrupt` (`Cause.ts:79-82,151`). A `Fail` carries `error`, a `Die` carries `defect: unknown`, and an `Interrupt` carries optional `fiberId` (`Cause.ts:339-341,375-377,403-405`). Each reason has `_tag`, an `annotations` map, and `annotate`; annotations are attached to individual reasons, not tree nodes (`Cause.ts:271-278`; `internal/core.ts:180-222`). Runtime stack-frame annotation is applied as an exit failure is evaluated (`internal/core.ts:601-629`); public annotation APIs describe stack/span metadata (`Cause.ts:1766-1772,1868-1884`).

`empty` is a valid Cause with zero reasons; `fail`, `die`, and `interrupt` each make a singleton reason list (`Cause.ts:444-475,500-546`; `internal/core.ts:346-385`). `fromReasons` wraps the provided sequence directly; callers must treat it as immutable (`Cause.ts:408-446`).

## 2. Exit representation and constructors

`Exit<A,E> = Success<A,E> | Failure<A,E>`. Its tagged variants hold respectively `value: A` and `cause: Cause<E>` (`Exit.ts:61,122-125,159-162`). Constructors: `succeed(value)`, `failCause(cause)`, `fail(error)` (wraps typed error in `Fail`), `die(defect)` (wraps as `Die`), and `interrupt(fiberId?)` (`Exit.ts:223,254,285,317,345`; `internal/core.ts:581-588,601-637`). At runtime Exit is an effect-like primitive with `_tag` equal to `Success`/`Failure`, and JSON contains `_id: "Exit"`, `_tag`, and `value` or `cause` (`internal/core.ts:532-578`; `../test/Exit.test.ts:5-15`).

## 3. Combining failures

`Cause.combine(a,b)` concatenates reason order, removes equal duplicate reasons, and treats empty as identity; it does not remember whether combination was sequential or parallel (`internal/core.ts:283-323`; `../test/Cause.test.ts:539-573`). This loses the draft’s `Then`/`Both` provenance. The fibers/combinators must decide when to merge exits; no generic Cause-level parallel operator exists in this representation.

When an interrupt reaches an interruptible failure continuation, the runtime adds interruption reasons to the current cause. If interruption skips a recovery handler, it drops typed `Fail` reasons first; otherwise the existing failure can coexist with interruption (`internal/core.ts:611-629`). This is distinct from Cause.combine’s general equality-deduping behavior.

Finalizers run on a failed exit and, if they also fail, `combineFinalizerCause` combines original and finalizer causes then elects defects; on an originally successful exit, finalizer outcome is returned directly (`internal/effect.ts:4069-4075`). Finalizers are run in reverse registration order; scope policy can run them sequentially or in parallel (`internal/effect.ts:4077-4099,4103-4107`). Defect election converts every non-`Done` typed failure to `Die` when any defect is present, preserving annotations; this is the documented priority rule for finalizer/mixed causes (`internal/effect.ts:177-200`). Concurrent iteration similarly merges non-interrupt reasons from late failing siblings into an existing terminal failure and elects defects (`internal/effect.ts:5233-5249`).

## 4. Squash and rendering

`squash` is lossy: first `Fail.error` wins regardless of earlier interrupts/defects; absent Fail, first Die defect wins; interrupt-only gives `Error("All fibers interrupted without error")`; empty gives `Error("Empty cause")` (`internal/effect.ts:289-299`; `../test/Cause.test.ts:316-346`).

`prettyErrors` preserves each Fail/Die as a separate Error, uses original Error/object message/name/stack/cause where possible, turns strings into messages and other primitives into an “Unknown error” message; it attaches available stack annotations. Interrupts are gathered; only-interrupt causes become one `InterruptError` carrying interruptor IDs in its cause; empty yields `[]` (`Cause.ts:1071-1090`; `internal/effect.ts:302-340,343-385`). `pretty` renders those errors’ stack traces joined by newlines, including nested Error causes (`Cause.ts:1121-1139,1155`; `internal/effect.ts:468-475`).

## 5. Handlers and classification

`catchCause` receives the complete Cause and installs an `OnFailure` continuation (`internal/effect.ts:2674-2689`). `sandbox(effect)` is exactly `catchCause(effect, fail)`: it moves the whole Cause into the typed error channel (`internal/effect.ts:1683-1685`).

`catch` uses `findRecoverableError`: typed Fail is recoverable unless the cause also contains a Die; defects then take precedence and typed failures are converted to Die, while interrupts do not themselves block typed recovery (`internal/effect.ts:184-200,2754-2769`). `catchDefect` filters for Die (`internal/effect.ts:2783-2799`). `orDie` catches typed failures and re-fails them as defects (`internal/effect.ts:3488-3491`).

`catchTag` is over typed error values: the single-tag predicate is `isTagged(k)`, and multi-tag matching requires `_tag` and checks membership in the requested tag array, then delegates to typed `catchIf` (`internal/effect.ts:2919-2933,3116-3121`). The public contract requires a readonly `_tag` on the error (`Effect.ts:2816-2859`). It does not inspect Die defects or Interrupt reasons as tagged errors. Classification is structural: `Fail` is expected typed error, `Die` is untyped defect, and `Interrupt` is fiber cancellation (`Cause.ts:121-151,306-317,344-405`).

Thrown exceptions in the interpreter run loop are turned into `exitDie(error)`; thrown errors during eager generator construction are converted with `die(error)` (`internal/effect.ts:724-729,1447-1484`). A synchronous throw from a finalizer callback is likewise made `exitDie` (`internal/effect.ts:4058-4066`). Therefore Python should catch `BaseException` at narrowly owned effect boundaries only if cancellation/system-exit semantics are deliberately defined; ordinary caught exceptions should become `Die`, while explicit typed failure remains `Fail`.

## 6. Invariants worth preserving

- Empty Cause means an empty reason sequence and is identity for combination (`internal/core.ts:313-323,346`; `../test/Cause.test.ts:118-125,559-565`).
- Reason order matters for “first” extraction and squash; combine retains left-to-right order, with equal duplicates removed (`internal/core.ts:283-323`; `internal/effect.ts:290-299`).
- Each reason’s metadata must survive transforms; `causeMap` replaces only Fail while carrying its annotations and preserves Die/Interrupt values (`internal/effect.ts:271-285`); `Exit.mapError` behavior is asserted for mixed causes (`../test/Exit.test.ts:26-40`).
- Interrupt-only is nonempty and distinct from empty; mixed causes can include Fail, Die, and Interrupt together (`internal/core.ts:247-248`; `../test/Cause.test.ts:299-313,567-573`).
- Defects are outside the typed error parameter. When any Die participates in recovery/finalizer combination, Die takes precedence over ordinary Fail; special `Done` failures are exempt (`internal/effect.ts:177-200`).
- Annotation merge reads reasons in order; later reason wins duplicate annotation keys (`internal/effect.ts:255-267`; `Cause.ts:1833-1844`).

## 7. Python design recommendation / draft delta

Represent the actual model as frozen, slotted dataclasses with a literal tag union:

```python
@dataclass(frozen=True, slots=True)
class Fail(Generic[E]): error: E; annotations: Mapping[str, object]
@dataclass(frozen=True, slots=True)
class Die: defect: object; annotations: Mapping[str, object]
@dataclass(frozen=True, slots=True)
class Interrupt: fiber_id: int | None; annotations: Mapping[str, object]
Reason = Fail[E] | Die | Interrupt
@dataclass(frozen=True, slots=True)
class Cause(Generic[E]): reasons: tuple[Reason[E], ...]
```

Use frozen tuple/mapping snapshots rather than mutable list/dict so callers cannot mutate a Cause after construction. Keep `Exit` as `Success(value)` / `Failure(cause)` tagged dataclasses, and make explicit constructors (`fail`, `die`, `interrupt`, `fail_cause`) so the typed/defect distinction is never inferred from whether a Python value happens to subclass Exception. `ExceptionGroup` can be an interoperability rendering of multiple Fail/Die errors, but should not be the canonical Cause: groups lack Effect’s separate Interrupt reason, per-reason annotation metadata, empty value, ordered dedupe semantics, and Fail-vs-Die classification. Preserve all reasons in the internal model; only squash at an explicit Python exception boundary.

Change DESIGN.md’s `Cause = Fail | Die | Interrupt | Then | Both` to `Cause(reasons: tuple[Reason, ...])`, where reason entries are `Fail(error) | Die(defect) | Interrupt(fiber_id, annotations)`. The flat sequence is the v4 representation, but it cannot encode sequential-vs-parallel topology; if the Python runtime needs that provenance for fiber semantics/debugging, retain it as separate execution metadata or intentionally extend the model, rather than claiming parity with v4. `Exit = Success | Failure(Cause)` already matches structurally (`DESIGN.md:18-19`). Evidence for the current draft is `../../../../../../docs/DESIGN.md:18-19`.

## Coverage limits

Source behavior was read, not executed. The snapshot does not establish upstream v4 commit identity. The task’s specified Cause/Exit tests confirm shape, constructors, combination, and squash examples; no dedicated catchTag or runtime-throw tests were run in this research pass.
