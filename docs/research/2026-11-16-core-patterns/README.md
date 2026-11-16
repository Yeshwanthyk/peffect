# Three reference files: patterns to prove before spreading

Status: design draft for discussion, 2026-11-16. No production refactor in this round yet.
Baseline: peffect 0.1.3, source `ee3433082c61bc2159a65edf1a5b0d26146584a6`;
Effect 4.1.4 source `289a53f7b1fc40049219c6e69022db2fb859c356`.

Start with `core.py`, `fiber.py` and `queue.py`. These represent three different jobs:
interpreting effects, coordinating execution, and implementing a stateful library primitive.
Queue is a reference application of the kernel, not itself a kernel module. `cause.py`
remains part of the actual kernel but is not one of these three design exercises.

The objective is that a contributor can find the owner of a transition, change it locally,
and identify which observable ordering must survive. Lower scores alone do not establish this.

## Start with usage and observable behavior

These are existing caller shapes, not proposed new APIs:

```python
# A callback may finish before register returns, or resume a parked fiber later.
effect = async_(register)

# Failure stops admission, interrupts started work and awaits cleanup.
results = for_each(items, process, concurrency=4)

# Removal delivers to the first accepting continuation before interruption can win.
message = queue.take()
```

For each shape, trace success, typed failure/defect, interruption and synchronous reentry.
Readability is measured against a concrete maintenance exercise: add a completion path,
change which workers may start, or change a waiter admission policy without changing unrelated
owners. The design must make the affected places and ordering assumptions visible.

## What the sources establish

1. **Callbacks are control-transfer boundaries.** Pinned Effect's Async resumes a parked
   fiber synchronously. Queue explicitly rechecks after resuming a producer because it may
   take, offer or shut down the queue. Local `Async._eval`, `Queue._release_capacity` and
   traversal `record` follow this contract. Checked in local and pinned vendor source.
2. **Scheduling boundaries belong to semantics.** Trio 0.35.0 documents checkpoints as
   cancellation and scheduling points. This supports reviewing boundaries explicitly; it
   does not make Trio's checkpoint placement the correct placement for peffect. Peffect
   must preserve its Effect-compatible operation boundaries and synchronous reentry.
   [Trio checkpoints](https://trio.readthedocs.io/en/v0.35.0/reference-core.html#checkpoints).
3. **Types describe contracts, not runtime enforcement.** Python 3.13 typing documentation
   explicitly distinguishes annotations from runtime checks. Precise callback signatures
   help maintainers, but cannot prove single settlement, ordering or cancellation safety.
   [Python typing](https://docs.python.org/3.13/library/typing.html).

Effect anchors: [Async](https://github.com/Effect-TS/effect/blob/289a53f7b1fc40049219c6e69022db2fb859c356/packages/effect/src/internal/effect.ts#L1206),
[Queue release](https://github.com/Effect-TS/effect/blob/289a53f7b1fc40049219c6e69022db2fb859c356/packages/effect/src/Queue.ts#L2109).
[Source notes](source-notes.md) separate inspected facts from transfer hypotheses.

## Patterns worth making explicit

| Pattern | Reference | Why it helps | When not to apply |
|---|---|---|---|
| One owner for settlement | `core.Park` | Resume, queued execution and interrupt compete over one fact | Do not merge registration phase and settlement into one vague flag |
| Independent facts; derived admission | `fiber._run_window` | Cancellation and failure remain independently meaningful; no synchronized done flag | An enum is useful only if states are mutually exclusive |
| Publish before reentry; revalidate after | Queue reservation, registry removal, resume, state check | A resumed producer sees a consistent state | Do not move race loser interruption before winner resumption; that has a different contract |
| Registration owns withdrawal | `_await_take` and its cancellation token | Cleanup removes precisely the waiter it registered | Resource ownership may transfer; cleanup must then follow the new owner |
| Choose live iteration or snapshot deliberately | Queue `_Registry` versus race loser list | Makes membership during reentry part of the operation's contract | Neither snapshots nor live collections are universally safer |
| Extract a contract, not a branch count | Queue `_admit` owns batch offset progress | A helper answers a domain question and has one reason to change | A helper that merely hides lines or duplicates state adds navigation without clarity |

These are candidate reference patterns, not a claim that all implementations need rewriting.
No blanket bans on loops, conditionals, closures, Optional, Any or isinstance follow from them.

## Three bounded design comparisons

### 1. core.py: settlement ownership first

Current shape: `Park.done` is written directly by Async resume, the scheduled step and
interrupt delivery. Registration timing is additionally represented by `registering/sync`.
This is already a small explicit lifecycle object.

Alternative A: retain direct assignments and document each transition where it occurs.
Alternative B: give Park a small `try_claim()` operation used by competing paths, while
keeping registration timing local to Async. This could put the one-way transition at its
owner, but risks adding a forwarding method with no real benefit. An interrupt path may
need unconditional invalidation rather than a conditional winner check; preserve that distinction.

**First experiment:** prototype B against A without changing scheduling or adding effects.
Accept only if settlement writers become simpler and all three caller paths remain easy to
read. Reject a generic state-machine framework, callback runner, or merged Async/yield protocol.
No performance or safety result for B has been measured yet.

Proof: synchronous registration/resume; repeated/late resume; interrupt-before-resume;
queued-step cancellation; stale queued token must not resume a newly parked finalizer;
registration exception and cancellation cleanup. Use fresh pinned JS outcomes, existing
resume/scheduler tests, formal linked tests and a mutation that permits double settlement.

### 2. fiber.py: explicit lifecycle object versus scoped callbacks

Current shape: traversal callbacks share invocation-local progress, live workers, failure
and cancellation. A per-pump pause marker is deliberately narrower than the invocation.

Alternative A: retain the closures, with progress/admission/result/cleanup responsibilities
visible at their current boundaries.
Alternative B: a private traversal object owns invocation state, with named lifecycle methods;
per-pump pause ownership remains separate. Immutable inputs must not become writable temporary
fields merely because a class makes access convenient.

**Recommendation:** A is the current reference until B demonstrates fewer coordination facts
or fewer places that must change together. The previous class sketch did not establish that.
Compare both using the same change exercise: add an early-stop condition while preserving
failure accumulation, cleanup and refill. Do not repeat a conversion solely for lower closure metrics.

Proof: inline completion, refill, mapper exception, fail-fast, simultaneous failures,
external interruption during cleanup, masking, result order and reentrant observers.
The existing bounded model does not model the traversal algorithm.

### 3. queue.py: local protocol versus precomputed transition plans

Current shape: the queue owns lifecycle and capacity; offer entries own their remaining
payload/offset. A live ordered registry permits membership changes during resume.

Alternative A: keep reserve, remove, resume and recheck visibly ordered in the local loop.
Alternative B: calculate a transition result first and publish it separately. It must represent
one admission only and revalidate after each callback; calculating a whole batch ahead would
use stale capacity and membership.

**Recommendation:** A is the reference unless B removes duplicated decisions without copying
authoritative state. `_admit` already extracts progress calculation where it is useful.
An additional possible experiment is entry-owned accept/reject behavior, but only as one
coherent change across `_take_offer`, `_accept` and `_reject`, not mixed dispatch styles.

Proof: reentrant take/offer/shutdown, appended/cancelled waiters during iteration, partial
batches, zero capacity, raw fractional/NaN capacity characterization, shutdown suffixes,
message conservation up to first acceptance, and timed-out take followed by a new offer.

## What to standardize after the experiments

Write a short reference card for each accepted pattern:

- Problem and concrete caller.
- The actual implementation, with a link to its test.
- Authoritative state and allowed writers.
- What happens before and after callbacks, yields or effect-operation boundaries.
- Counterexample: where this pattern would be the wrong choice.
- A real change exercise showing why maintenance is easier.

Then audit other files as **matches**, **justified exception**, or **candidate change**.
Examples: Semaphore for registration/withdrawal; ManagedRuntime for claim/publication;
TestClock for scoped tracker restoration. They should share contracts where appropriate,
not be forced into the same class/closure template.

Lints can reliably guard measured complexity growth, declaration drift and some syntax.
Choosing a live collection, deciding independent versus exclusive states, and preserving
reentrant publication require semantic review and behavioral tests. Keep that distinction.

## Evidence and next step

This round read source and official documentation; it ran no refactor or runtime experiment.
Prior release results establish the baseline only, not safety of any proposed alternative.
Start with the Park ownership experiment, discuss its before/after and test evidence, then
choose the next file. Do not widen complexity limits automatically to accommodate a candidate.
