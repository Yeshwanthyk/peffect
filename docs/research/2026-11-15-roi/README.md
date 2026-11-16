# peffect ROI research — 2026-11-15

## Start here

- [Foundation priorities and source evidence](REPORT.md): ranked top five, dependency waves, proposed scopes, estimates, proof targets and deferred candidates.
- [Optional packages](optional-packages.md): HTTP, server, database, testing and observability integration priorities, with npm adoption evidence.
- [Dependency visualization](waves.html) and [package adoption chart](optional-packages.html).
- Archived raw scout evidence: [primitives](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-1.md), [scheduling](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-2.md), [cached resources](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-3.md), [data flow](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-4.md), [config/validation](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-5.md), [Python integration](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-6.md), [application ergonomics](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-7.md), [ecosystem breadth](https://github.com/Yeshwanthyk/peffect/tree/ee3433082c61bc2159a65edf1a5b0d26146584a6/docs/research/2026-11-15-roi/scout-8.md).

## Remaining foundations versus optional integrations

Queue was already owned by the implementation thread during this research. Finish and accept that work before treating its API as settled.

1. **Schedule/retry/repeat:** next core library unit; deterministic finite retry and pacing first. Reuse Clock/TestClock, Duration, effects and the accepted completion protocol. Queue itself is not a dependency. Random is needed only when adding jitter.
2. **Cache:** coalesce repeated lookups and add bounded/TTL retention. Deferred, fibers and Clock exist; lookup ownership, last-waiter cancellation and invalidation races need explicit contracts.
3. **ScopedRef:** replace resource-owning state using Scope and SynchronizedRef. Failed replacement and concurrent closure need proof; this does not provide borrower leases.
4. **Minimal scoped Pull/Stream:** completion distinct from failure, lazy sources, map/take and consumers with early-release cleanup. General Channel and concurrent operators can follow.
5. **PubSub then SubscriptionRef:** only when fan-out and observable state are needed. Subscriber lifetimes, backpressure and replay are real missing semantics.

Pool/RcMap belong later when applications need generic resource borrowing. HTTPX and SQLAlchemy already have pools; their adapters do not require a new peffect Pool. Small Option/Cause helpers can accompany demonstrated call-site needs. Config and a Random service are targeted additions, not reasons to postpone basic HTTP.

Basic async HTTP/server/database integration already has Layer, Scope, ManagedRuntime and the async boundary. The practical sequence is Queue acceptance → Schedule plus HTTP client → real application → server/database integration; add deeper foundations when their application requirement appears. This is an inferred sequence, not a claim that imports force that order.

## Evidence boundaries

These are historical research snapshots, not a live implementation status page or an approved specification. REPORT.md records the inspected commits; concurrent implementation may have advanced since. Source links are repository-relative with line anchors, which can drift as code evolves. The pinned upstream revision is recorded in the reports. The visuals use T3 theme variables; waves.html also loads its renderer from a CDN.

Research originally ran read-only and was saved to scratch. This bundle was copied into the repository at the user's request, preserving the findings and normalizing local source links. No source implementation, release or git commit was performed by this save operation. No test campaign was run under the research restriction.
