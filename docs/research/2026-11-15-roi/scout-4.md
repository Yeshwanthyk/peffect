# Scout 4

<t3-thread://v2/c94b318d-6569-4fbb-ada1-8f29f58f639f/thread:delegated-task:command%3Amcp%3A2bb3f3e0-03ef-4a0d-af8e-26799735b205%3Adelegate-task%3Apeffect-roi-codex-4>

I’ll read the required router and repo instructions first, then inspect the live checkout and only the stream/dataflow files needed for downstream scope evidence.

Method: how — inspect the stream/dataflow source path to assess downstream unlocks and prerequisite cost.  
Plan: Choose a view → Gather evidence → Explain the execution path · ends: compact source evidence for the parent to synthesize.

**PASS** — the live checkout is at baseline `da88bfccb383d660fd96bf78d33ca0207bd658dd`, and the read-only vendor is pinned at `289a53f7b1fc40049219c6e69022db2fb859c356`. The reported app-scenario and test counts were not re-run. I found no local PubSub, Stream, Channel, Pull, or Queue module; the existing scout note and vendored upstream source provide the evidence below.

### Candidate observations

- **Staged stream/dataflow is a substantial downstream unlock (inference).** A small sequential pull-and-transform layer would let callers compose lazy sources, maps, bounded consumption, and collection without taking on concurrent channels immediately. Upstream types separate stream elements from internal nonempty chunks: [`Stream.ts:130-135`]( ../../../vendor/effect-v4/packages/effect/src/Stream.ts ) gives Stream a channel of `NonEmptyReadonlyArray<A>`. `runCollect` flattens chunks into an array at [`Stream.ts:11129-11139`]( ../../../vendor/effect-v4/packages/effect/src/Stream.ts ). Existing local types appear to supply much of the effect and scope substrate, but the local scout note recommends adding bounded queue and cancellable child-task support before concurrent merge or flatMap. See [`docs/scouts/stream.md`]( ../../../docs/scouts/stream.md ) for that prior source reading.

- **Smallest faithful first slice (inference):** a single-consumer pull protocol with a distinct completion outcome, iterable source, `map`, `take`, `runCollect`, and `runForEach`. Upstream defines Pull as one effectful step whose normal completion is `Cause.Done<Done>`, distinct from ordinary failure ([`Pull.ts:22-44`]( ../../../vendor/effect-v4/packages/effect/src/Pull.ts )). That distinction should be preserved in Python’s typed boundary. A private sentinel could implement it at runtime, but typed completion should remain distinct from user errors.

- **High app value with modest scope: SubscriptionRef changes stream (inference).** Upstream initializes a replay-1 PubSub with the initial value, then exposes `changes` by adapting that PubSub into a Stream ([`SubscriptionRef.ts:116-124`]( ../../../vendor/effect-v4/packages/effect/src/SubscriptionRef.ts ), [`SubscriptionRef.ts:126-166`]( ../../../vendor/effect-v4/packages/effect/src/SubscriptionRef.ts )). This supports watching state for UI, cache invalidation, or background processing. It depends on a working stream pull boundary plus subscription lifetime management; it need not wait for general Channel or concurrent merge.

- **PubSub is a separate primitive with meaningful semantic weight (inference).** Bounded upstream PubSub retains messages until all current subscribers take them and suspends publishers at capacity ([`PubSub.ts:338-349`]( ../../../vendor/effect-v4/packages/effect/src/PubSub.ts )). Subscription installs an unsubscribe finalizer and shutdown interrupts waiting pollers ([`PubSub.ts:1259-1288`]( ../../../vendor/effect-v4/packages/effect/src/PubSub.ts )). Therefore even a minimal credible PubSub needs subscriber accounting, capacity strategy, waiting/wakeup, cancellation cleanup, and explicit scope ownership. It is not just a list plus an async condition.

- **Channel and concurrent Stream operators should follow foundational pull/resource work (inference).** Upstream Stream wraps Channel, while Channel owns scoped pull transformations. The existing scout notes show concurrent merge uses fibers, child scopes, and a queue for output/failure coordination and bounded backpressure. A staged implementation can make useful progress before that cost, but should not call itself a faithful concurrent Channel until cancellation, finalization, and backpressure are covered.

### Proof targets

Prioritize semantic checks for: empty source completion; no empty chunks on successful pull; exactly-once advancement; error vs completion distinction; `take(0)` and early termination cleanup; subscriber replay/current value; slow subscriber backpressure; unsubscribe waking blocked operations; and scope closure on normal completion, failure, and interruption. These follow the upstream contracts above and scout note; they are proof targets, not claims about current local behavior.

### Maintainability and boundary risks

The repo instructions place non-kernel modules outside `core.py`, `fiber.py`, and `cause.py` under the kernel boundary. Keep stream operations in user-space effects where possible; require a new kernel combinator only if the boundary cannot express a needed invariant. The key type risk is collapsing completion into ordinary errors. The key lifecycle risk is allowing early downstream termination to leave producers or subscriptions alive. The key usability risk is hiding whether PubSub capacity means backpressure, dropping, or sliding.

Used: how (`SKILL.md`) · playbook: — · principles: — · steps 2/3 (skip: Gather evidence — source evidence gathered directly from scout note and vendored snippets; no additional tests or interpreter run per read-only scope).
