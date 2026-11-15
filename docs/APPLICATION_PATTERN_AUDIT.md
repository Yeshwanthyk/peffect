# Application composition audit

Scope: the two application modules in `examples/parity`, their real EffectJS counterparts,
the runners, comparator and typing fixtures. This audit accompanies the 16-scenario acceptance
comparison. It is not a quality verdict on every peffect API.

## What the existing foundation supported

Both applications use existing public APIs without changing `src/peffect`: Tag and Reference,
Layer sharing/provision, typed errors, `gen`, `attempt`, resource combinators, Deferred, fork and
bounded traversal. The domain modules contain no `Any`, casts, suppressions, private access,
hand-written scheduler, memoizer, or cancellation implementation. Nine service tags and one
event-sink Reference compose through those patterns.

`make_checkout` captures its four services when the layer builds. Its returned operation has
no hidden service requirements. The shared Users layer appears directly and beneath Risk;
one MemoMap keeps their Repo lifetime shared. The request boundary catches only Pydantic's
declared ValidationError. Payment recovery removes only PaymentDeclined from the error union.
Positive and negative typing fixtures verify these claims rather than relying on annotations
alone.

## Findings corrected

1. **Global trace ownership.** The original reference and first Python counterpart used one
   mutable event list. Concurrent application runs contaminated each other's traces. A new
   regression failed before correction. Both now provide an EventLog Reference per run; its
   default is a stateless no-op. The concurrent-run regression passes without a lock or new
   primitive. This is the existing Context/Reference pattern doing useful work.
2. **Lossy failure reporting.** Independent review demonstrated that the TS reporter selected
   the first typed error from a multi-reason Cause, hiding a cleanup defect or second error.
   `outcomes.ts` now rejects such causes, matching Python. The retained regression was red
   before the correction. The comparator also distinguishes JSON booleans from numbers, which
   Python equality alone does not.
3. **Fixture/application mixing.** Gated payment implementations and scenario orchestration
   lived beside the checkout service. They now live in separate scenario modules. Reusable
   application files contain contracts, domain operations and Layer wiring; runners own their
   observations and process entry points.

## Guidance and skills

**Do not add another broad skill on this evidence.** AGENTS already teaches public primitives,
typed requirements, Reference defaults and inferred `gen` bodies. The existing structure-review,
state-ownership and prove-it-works guidance identified the actual problems. More prose would
duplicate those instructions; the two regressions enforce the corrections.

The missing discoverability was a composed usage example. The parity README now maps each
pattern to executable code and separates application modules from test infrastructure. The
typing fixtures also demonstrate when an intermediate yielded Fiber needs an explicit type
to retain the result/error channels through Python generator inference.

This session is **not** a controlled test of authoring from AGENTS alone: the author also read
the actual TypeScript application, library implementation and tests, and received independent
review. It establishes that the base APIs suffice for this application. To establish that a
new skill improves authoring, give independent authors the same new application specification,
compare existing guidance with the proposed recipe, and judge their code against hidden
runtime/type checks. That experiment has not been run; no skill-effectiveness claim is made.

## Remaining limits

- `catch_tag` permits a nonmatching error class while retaining the original error channel;
  upstream rejects an unknown string tag at the call. Both guarantees are tested and documented.
- Pydantic StrictInt and Effect Schema are not generally equivalent, notably for integral
  Python floats. Only the stated application input scenarios are compared.
- In-memory Repo/Inventory/Payments demonstrate composition and cleanup, not external I/O,
  transactional stock accounting or real payment effects.
- The comparator proves these observed traces, not arbitrary concurrency equivalence. The
  existing bounded kernel model and property tests have their own documented scope.
- No project LICENSE exists at the repo root. Pinning and reviewing code makes a technical
  reference reproducible; it does not settle public redistribution terms.
