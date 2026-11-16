# Highest-ROI additions after Queue

Research date: 2026-11-15 Toronto / 2026-11-16 UTC. Lead: Astra medium. Eight completed, bounded, read-only Codex GPT-6 Luna high scouts. **Recommendation: finish the already-owned Queue release, then implement a small faithful Schedule/retry/repeat slice and prove it in a real-I/O worker app.**

## Scope and current-state correction

The initial source snapshot was clean `build/primitives` at `da88bfccb383d660fd96bf78d33ca0207bd658dd`; vendor was `289a53f7b1fc40049219c6e69022db2fb859c356`. At final handback, the parent had advanced to `5ee9c861db42338011cfbb70e417f1b9b095357a` (“Pin Queue atomic delivery and Pull completion contracts”), adding tests/type fixtures only; source and vendor remained clean. This is evidence of concurrent Queue progress, not completed implementation or acceptance. Source findings below remain anchored to the unchanged production modules.

The implementation parent explicitly owns **Queue → verification → v0.1.2 bump/push**, including Ref/SynchronizedRef. Its latest messages at positions 2506–2518 say the research is for **after Queue**, and that Sol delivery has started. Parent:
<t3-thread://v2/c94b318d-6569-4fbb-ada1-8f29f58f639f/4faad3a0-db13-4ad1-8d80-4b9620120c4d>

Queue is therefore an **incoming baseline, not a new candidate or a competing lane**. Done/Pull and atomic dequeue delivery belong to that work. This report neither approves the proposed `suspend_exit` kernel design nor assumes its implementation has passed. Reconcile the accepted API after Queue lands.

The source investigation made no repository, source, vendor or git changes and ran no test campaign. Scout PASS means their evidence pass completed, not product correctness. The reported 718 tests / 21 paired scenarios are handoff evidence, not new measurements. All recommendations, scopes and effort ranges below are judgments; source relationships are separately identified.

## Top five

Ranking weighs concrete caller value, prerequisites beyond the incoming baseline, downstream reuse, correctness/type risk, and ongoing maintenance. No numerical ROI score is implied.

| Rank | Candidate | Why it earns the slot | Main cost/risk | Boundary | Effort estimate |
|---|---|---|---|---|---|
| 1 | Schedule + retry/repeat | Reusable transient-failure recovery and polling; immediately strengthens Queue workers and clients | Completion typing, mixed causes, timing metadata, independent state per run | Core library module, outside runtime kernel | 2–4 engineering days |
| 2 | One real async client integration + composed worker example | Turns existing DI/resource/cancellation foundations into a useful application; exposes missing behavior the in-memory examples cannot | Correct exception classification, host cancellation, client cleanup | Example first; optional integration package after reuse appears | 1–3 days |
| 3 | Cache | Same-key lookup coalescing, bounded retention and TTL help API enrichment, metadata and configuration lookups | Last-waiter cancellation, context capture, refresh/invalidation and stale completion races | Core library module | 3–6 days |
| 4 | ScopedRef | Focused next use of SynchronizedRef: replace a resource-owning generation safely | Close-before-publish semantics, failed acquisition/release, owner-close races | Core library module | 1–3 days |
| 5 | Sequential Stream slice over a faithful scoped pull boundary | Lazy paged imports and large-data processing without materializing everything; base for later PubSub and streaming integrations | Chunk/Done/error typing, iterator ownership, early termination cleanup | Core library module; source adapters separate | 4–8 days |

Estimates are broad engineering effort including source tests, typing, review and acceptance documentation, not agent wall-clock promises. They exclude the ongoing Queue work. They may widen if the accepted kernel boundary cannot express a required atomic step. The larger Stream surface and more Python adapters are not included.

## 1. Schedule / retry / repeat

**Caller sketch — proposed API, not executable today:**

```python
policy = Schedule.exponential("100 millis").pipe(
    Schedule.up_to(times=3),
)
response = yield from retry(fetch_snapshot(url), policy)
yield from repeat(poll_once(), Schedule.spaced("30 seconds"))
```

A retryable operation must expose only the intended recoverable failures or use an explicit policy predicate. Do not retry arbitrary writes unless the application supplies a safe replay/idempotency contract.

**Reuse:** Clock, Duration, sleep, TestClock, Reference/context provision, suspend/map/flat_map/catch_cause, existing recovery semantics, and incoming Done/Pull completion. No Queue operation, Ref, Cache, Random service, or Stream is required by deterministic Schedule drivers.

**Exact evidence:** [Schedule.fromStep](../../../vendor/effect-v4/packages/effect/src/Schedule.ts#L262) accepts an effect allocating a step function, whose pull yields output plus Duration and terminates with Done carrying final output. [toStep](../../../vendor/effect-v4/packages/effect/src/Schedule.ts#L358) defers construction failures to the step. [toStepWithMetadata](../../../vendor/effect-v4/packages/effect/src/Schedule.ts#L392) reads Clock, runs the step, forms metadata, and sleeps. [retryOrElse](../../../vendor/effect-v4/packages/effect/src/internal/schedule.ts#L64) stores the last recoverable error, provides CurrentMetadata, steps on failure, and handles Done; [repeatOrElse](../../../vendor/effect-v4/packages/effect/src/internal/schedule.ts#L30) steps after success.

The source chain is **Done/Pull completion + Effect + Clock/Duration + metadata Reference → Schedule drivers**. Queue comes first because it is already committed delivery, not because retry imports it.

**Smallest useful faithful scope:** the four-parameter Schedule model (output/input/error/environment), fresh per-execution step allocation, from_step/to_step, metadata and CurrentMetadata, recurs, spaced, exponential, a count bound (`up_to(times=...)`), and retry/repeat. Include the shared predicate combinator needed to express those constructors, rather than a bespoke retry loop with a different contract. Defer jitter/Random, calendar/cron policies, broad algebra and convenience overload families; label those as unported API, not equivalent behavior.

I include exponential + a bound, beyond the scout's recurs/spaced suggestion, because an unbounded fixed-spacing policy alone is a weak first retry API. [exponential](../../../vendor/effect-v4/packages/effect/src/Schedule.ts#L885), [recurs/spaced](../../../vendor/effect-v4/packages/effect/src/Schedule.ts#L1211), and [upTo](../../../vendor/effect-v4/packages/effect/src/Schedule.ts#L1339) show these are a coherent deterministic slice.

**Missing gaps after Queue:** Schedule's state/metadata/type surface and drivers; check that incoming Pull includes the exact handlers and ExcludeDone typing required here. Do not recreate Done or add a full Result module just because TS uses Result internally. No new kernel transition is established as necessary by this reading.

**Proof:** port the relevant [Schedule tests](../../../vendor/effect-v4/packages/effect/test/Schedule.test.ts) and [retry tests](../../../vendor/effect-v4/packages/effect/test/Effect.test.ts#L4533). Test initial run vs recurrence count (recurs(0) still allows the original operation), exact delays with TestClock, exhaustion returns the last typed failure, repeat returns schedule completion output, construction/step failures, two concurrent executions of one policy, metadata visibility, and interruption while sleeping. Pure interruption and defects are not retried. Mixed causes need the actual recovery rule: upstream permits typed recovery when interrupts coexist but no defect exists, rather than a blanket “any interruption prevents recovery” rule ([findRecoverableError](../../../vendor/effect-v4/packages/effect/src/internal/effect.ts#L199)). Pin E/R unions and Done subtraction in positive/negative typetests.

**Unlocks:** retrying workers, polling, reconnection, refresh loops, later scheduled streams. This is the recommended NEXT implementation unit **after the Queue release's acceptance**.

## 2. Real async integration and proving app

**Caller sketch — mostly existing primitives, adapter/domain names proposed:**

```python
class SnapshotClient(Tag[Client], key="snapshot/Client"): ...

@gen
def fetch_snapshot(url):
    client = yield from SnapshotClient()
    return (yield from attempt(lambda: client.fetch(url), TransientFetchError))

# A client Layer acquires/releases its session.
# ManagedRuntime owns the shared Layer for the worker's process lifetime.
```

**Reuse:** `attempt` already accepts synchronous or awaitable thunks, catches declared exception classes, and maps others to defects. [_await](../../../src/peffect/core.py#L940) schedules the awaitable and cancels/awaits it on interruption. [await_exit](../../../src/peffect/fiber.py#L967) carries host asyncio cancellation into the fiber; a second host cancellation can stop waiting while the fiber finishes. [acquire_release](../../../src/peffect/resources.py#L24), Layer and [ManagedRuntime](../../../src/peffect/managed_runtime.py#L1) already supply resource ownership.

**Missing primitive gaps:** none demonstrated for an async client and existing exception-based failures. The gaps are a real adapter, error mapping, a runnable composition and proof. Blocking work is different: `attempt` executes the thunk synchronously before inspecting its result, so it does not offload blocking I/O. A thread bridge needs its own lifetime/cancellation contract and is not part of this recommendation.

**Smallest scope:** one read-only HTTP client operation and its scoped session layer in an example/integration module; validate request data with the already-used Pydantic boundary; map transport and retryable response failures intentionally; run on the existing asyncio host. Choose/verify the concrete client library at implementation time. Do not build a universal HTTP client/server stack, database API and CLI framework together.

**Proof:** use a real loopback HTTP server and real async client socket traffic. The server deliberately returns transient failure then success, terminal input errors, delayed replies and malformed payloads. Verify typed failures vs defects, client release under success/failure/interruption, timeout cancellation of the actual operation, shared session construction once, per-run event isolation, and runtime disposal with live requests. A mock implementing the same protocol is useful for domain tests but is not the external-boundary proof.

**Unlocks/boundary:** an immediate useful app, reusable client-layer idiom, and evidence for an optional Python HTTP integration. ASGI request/lifespan bridges and async DB-driver bindings are good follow-ups once this boundary works. Keep third-party dependencies out of the kernel; expose ordinary peffect Effects/Tags/Layers from integration packages. Upstream [HttpServer.serve](../../../vendor/effect-v4/packages/effect/src/http/HttpServer.ts#L69) and [SqlConnection.Acquirer](../../../vendor/effect-v4/packages/effect/src/sql/SqlConnection.ts#L68) support those service/scope boundaries, but are not verified Python-framework APIs.

## 3. Cache

**Caller sketch — proposed:**

```python
cache = yield from Cache.make(
    lookup=fetch_metadata,
    capacity=256,
    time_to_live="5 minutes",
)
metadata = yield from cache.get(document_id)
yield from cache.invalidate(document_id)
```

**Reuse:** dictionary/key semantics, Effects, captured Context, fibers/join/interrupt, Clock/Duration, exit handling. Neither Ref nor Queue is a hard dependency established by the implementation.

**Exact evidence:** [Cache.get](../../../vendor/effect-v4/packages/effect/src/Cache.ts#L430) checks expiry, touches map order, creates one entry, observes completion, sets TTL from completion time, excludes interrupted results and enforces capacity. [EntryImpl.await](../../../vendor/effect-v4/packages/effect/src/Cache.ts#L475) tracks awaiters and registers cleanup in the same evaluation as incrementing the count. The last departing waiter detaches the entry before interrupting unfinished lookup, so new callers cannot join an abandoned generation. [removeEntry](../../../vendor/effect-v4/packages/effect/src/Cache.ts#L509) checks identity, preventing an old completion from deleting a newer entry.

**Smallest faithful scope:** construction/context behavior, get with same-key in-flight sharing, capacity/LRU behavior, constant TTL including zero/infinite, cached successes and failures, invalidate, and necessary inspection for tests. Defer refresh and exit/key-dependent TTL APIs until a follow-up, explicitly marking them missing. Do not ship “cache forever in a dict” under the full Cache contract.

**Missing gaps:** an entry ownership state machine and safe attach/cleanup/publication using the allowed public boundary. Upstream uses unsafe fiber observers and onExit registration; merely replacing those calls with separate public Effects can create interruption windows. Existing mask/on_exit/fork facilities are promising building blocks, not proof that a faithful sequence already exists. Require a shape proof before deciding if a generic kernel helper is necessary.

**Proof:** [Cache.test.ts](../../../vendor/effect-v4/packages/effect/test/Cache.test.ts#L88) includes context capture and zero-TTL cases. Add/port one-vs-all waiter cancellation, synchronous completion/reentry, distinct-key concurrency, cached failure policy, TTL starts at completion, zero TTL at capacity, eviction order and invalidation during lookup. Check stale generation identity and keep the lookup error/environment channels precise.

**Unlocks:** coalesced metadata/enrichment lookups, avoiding duplicate expensive reads, bounded in-memory response reuse. No durable cache or resource lease semantics implied. I rank ordinary Cache ahead of ScopedCache because it serves broader data-read use cases with fewer resource-lifetime obligations; the scout's preference for ScopedCache lacked a concrete local resource-cache caller.

## 4. ScopedRef

**Caller sketch — proposed:**

```python
current = yield from ScopedRef.from_acquire(open_index())
yield from current.set(open_new_index())
index = yield from current.get()
```

**Reuse:** Scope child generations, SynchronizedRef/Semaphore, masks, exit/finalizer handling, scoped provision. No Queue, Stream, Cache or new kernel transition is demonstrated as necessary.

**Exact behavior:** [fromAcquire](../../../vendor/effect-v4/packages/effect/src/ScopedRef.ts#L87) creates owner/generation scopes, closes on acquisition failure, checks whether the generation is already closed, and runs masked. [set](../../../vendor/effect-v4/packages/effect/src/ScopedRef.ts#L182) serializes the replacement; acquires the candidate; **closes the old generation before publishing the new pair**; closes the candidate if old release fails; checks generation closure; then updates the pair. This corrects the scout's acquire → swap → close description.

**Smallest faithful scope:** make/from_acquire, get, set, owner-scope cleanup, and corresponding unsafe access only where the public convention warrants it. Preserve failed-acquisition behavior: old value remains live. Do not market it as a lease—an earlier get does not keep the old generation alive across replacement.

**Missing gaps:** the module and type-level Scope exclusion; direct scope state inspection must use the existing scope model/public surface rather than private fiber access. No broad new abstraction is justified.

**Proof:** port [ScopedRef tests](../../../vendor/effect-v4/packages/effect/test/ScopedRef.test.ts#L75), notably owner finalizer ordering, failed replacement preserving old resource, old finalizer defect releasing candidate, parallel setters, interruption and owner close during acquisition. Avoid asserting that arbitrary old readers survive replacement.

**Unlocks:** hot-swappable file-backed indexes, resource-owning configuration/client generations and controlled refresh. It is small enough to implement independently after the main app demonstrates a replaceable resource need.

## 5. Sequential Stream

**Caller sketch — proposed:**

```python
rows = Stream.from_iterable(records).pipe(
    Stream.map(normalize),
    Stream.take(100),
)
yield from Stream.run_for_each(rows, save_row)
```

The useful next extension is a resource-scoped paginated/async source. Plain mapping of an already-loaded list alone is not enough app value to justify Stream.

**Reuse:** incoming Done/Pull, Scope/resources, Effect/gen and typed errors. Queue is useful for buffering/concurrency, but is not an intrinsic prerequisite for every sequential operator. Schedule becomes necessary only for scheduled operators.

**Exact vs proposed structure:** pinned [Stream.fromPull](../../../vendor/effect-v4/packages/effect/src/Stream.ts#L581) calls Channel.fromPull; [fromIterable](../../../vendor/effect-v4/packages/effect/src/Stream.ts#L999) uses array/channel sources; [Stream type](../../../vendor/effect-v4/packages/effect/src/Stream.ts#L130) carries nonempty chunks and [runCollect](../../../vendor/effect-v4/packages/effect/src/Stream.ts#L11129) flattens them. A Python implementation directly over scoped Pull is an architectural adaptation, not the exact upstream module graph. Prefer a minimal faithful Channel/scoped-pull substrate and explicitly settle that boundary before coding; do not imply full Channel can be omitted without analysis.

**Smallest scope:** lazy scoped source acquisition, from_pull/from_iterable, map/map_effect, take, run_collect/run_for_each, preserving chunk and completion semantics. Include a real paginated source example. Defer merge, concurrent flat_map, generalized Sink, fan-out and the full operator catalog.

**Missing gaps:** typed nonempty chunks, source acquisition ownership, fresh per-run pull state, early-stop propagation and the chosen Channel boundary. The incoming Queue alone supplies none of those contracts.

**Proof:** empty source; no empty successful chunks; lazy acquisition; repeated runs; failure vs Done payload; take(0); early-stop finalization; interruption during source acquisition and pull; downstream failure closes upstream; chunk partition invariance where appropriate. Then compare chosen scenarios against pinned TS.

**Unlocks:** bounded-memory ingestion, paginated APIs and async-source integration; later PubSub-backed subscriptions and concurrent processing. Test a stream's memory/backpressure properties separately before making performance claims.

## Dependency ledger: facts versus sequencing choices

| Relationship | Evidence and classification |
|---|---|
| Done/Pull → Schedule | Exact calls: internal/schedule.ts:33,78; Schedule.ts:262,392. Share the incoming completion implementation. |
| Queue → Schedule | **Not a source dependency.** Delivery order only, because Queue is already in progress. |
| Ref → Queue | **Not a source dependency.** Queue owns Open/Closing/Done, waiter lists and dispatcher directly. |
| Atomic terminal delivery → faithful Queue take | Exact source requirement: [Queue.ts:2041](../../../vendor/effect-v4/packages/effect/src/Queue.ts#L2041) evaluates a returned Exit in the dequeue step. `suspend_exit` is one proposed Python mechanism; the parent owns deciding/proving it. |
| Queue → Pool | Exact only for creation-TTL strategy: [Pool.ts:1134](../../../vendor/effect-v4/packages/effect/src/Pool.ts#L1134). Basic borrowing uses its own [waiter/lease state machine](../../../vendor/effect-v4/packages/effect/src/Pool.ts#L580). Do not make all Pool wait for full Queue by import inference. |
| SynchronizedRef + Scope → ScopedRef | Exact storage/locking and scope operations in ScopedRef.ts:65,87,182. Public Python composition still needs its interruption checks. |
| PubSub + Semaphore + Stream → SubscriptionRef | Exact: [make](../../../vendor/effect-v4/packages/effect/src/SubscriptionRef.ts#L116) makes replay-1 PubSub and publishes initial value; changes uses Stream.fromPubSub, updates serialize. It is not just Ref plus an observer list. |
| Channel → Stream | Exact upstream wrapper/type relationship; minimal Python sequential substrate is a design choice to prove. |
| Scope + Deferred + Context + keyed ownership → RcMap | Exact [get](../../../vendor/effect-v4/packages/effect/src/RcMap.ts#L346) publishes entry/refcount before lookup and installs borrower finalizer; [release](../../../vendor/effect-v4/packages/effect/src/RcMap.ts#L498) closes at zero or starts idle expiry. Queue/Cache are not required by that path. |
| Config → Schema engine port | Exact TS Config uses Schema helpers; **full TS engine is not justified as Python prerequisite.** Native decoding is a deliberate adaptation, not full schema equivalence. |

## Delivery waves

**Wave 0 — already owned:** finish Queue's kernel/completion work, full operational API and proofs, then the parent release. No duplicate implementation or redesign from this report.

**Wave 1 — resilient useful app:** Schedule/retry/repeat; in parallel or immediately after, one real async-client layer and a concrete worker app. They touch different conceptual boundaries but the app integrates only accepted primitives. No new integration package is required merely to host its first example.

**Wave 2 — reuse and live resources:** Cache and ScopedRef, independently. A repeated real need for keyed resource leases promotes RcMap here. Add tiny map_error/Cause.map/Option helpers only when these callers need them, with tests for exact semantics.

**Wave 3 — continuous data:** minimal Channel/scoped Pull + sequential Stream, then PubSub and SubscriptionRef with scope-managed subscriptions, then bounded concurrent stream operators using Queue. Resource-aware cache/pool features are separate candidates, not mandatory stepping stones for Stream.

**Wave 4 — selective ecosystem:** Config provider recipes where settings substitution is useful; ASGI lifecycle bridge; one async DB integration; request batching where the backend really batches; metrics/tracing/export adapters when operational requirements exist. Durable workflows, distributed coordination, SQL platform breadth and STM need their own evidence and design.

These are delivery waves, not artificial prerequisite chains. Cache/ScopedRef/RcMap do not intrinsically require Schedule. The source-dependency map accompanies this report.

## The proving app: resilient snapshot worker

Extend the parent's basic Queue worker example rather than writing another equivalent one.

**User value:** ingest a bounded set of endpoint URLs, retrieve and validate snapshots with limited concurrency, retry transient fetch failures, and emit a structured summary of successes and terminal failures. First target read-only GETs; do not imply exactly-once delivery or durable jobs.

**Composition:** Pydantic validates jobs before any client resource opens; a Tag exposes fetch; Layer acquires one shared async client; ManagedRuntime owns its lifetime; incoming bounded Queue feeds scoped workers; Schedule provides bounded backoff; timeout bounds individual attempts; Ref records immutable counters; Console/log output is captured in tests. Cache is an optional later enrichment step, not required to prove Schedule.

**Acceptance observations:**
1. Invalid job input produces typed validation failure without acquiring a client.
2. Scripted loopback endpoint fails transiently twice then succeeds: three attempts, expected backoff, one final success.
3. Terminal response/validation failure is not retried; schedule exhaustion preserves the final typed error.
4. Defect and pure interruption propagate; cancellation during backoff creates no later request.
5. Queue capacity bounds admitted work; graceful end drains accepted jobs, forced shutdown follows the already-accepted Queue contract.
6. Cancellation during actual socket I/O releases the client/response and awaits worker cleanup; disposal does not strand requests.
7. Two concurrent runs do not share counters or event sinks.
8. Final JSON summary and captured event order distinguish success, exhausted retry, terminal failure and shutdown.

Use TestClock for schedule traces and a separate real-loopback integration check for network lifecycle. A deterministic fake service alone proves neither actual transport cancellation nor socket cleanup. Keep the tests' dependency injection and the production adapter identical at the Tag/Layer boundary. Include strict typetests for the composed program, avoiding casts/Any to erase remaining services/errors.

## Keep in core versus separate integration

“Core library” here means peffect modules, **not** adding everything to core.py/fiber.py/cause.py.

| Keep as generic peffect modules | Keep outside the runtime / optional integration |
|---|---|
| Schedule, Cache, ScopedRef, RcMap, Pool, Pull/Channel/Stream, PubSub, typed error combinators | HTTP client bindings, ASGI request/lifespan mapping, DB driver bindings, CLI framework adapters |
| Generic provider recipe/Context hooks if Config is implemented | Pydantic decode/settings-specific glue and serializers |
| Generic logging/trace/metric interfaces once needed | OpenTelemetry/backend exporters, storage/Redis, cloud clients |
| Kernel additions only for a demonstrated invariant the public boundary cannot express | Domain retryability, idempotency keys, business transactions |

Pydantic and pydantic-settings are **currently mandatory runtime dependencies**, verified in [pyproject.toml](../../../pyproject.toml#L1). Separating them into extras/packages would be a future packaging migration, not a description of today's package. Request validation already works with [attempt(model_validate, ValidationError)](../../../examples/parity/python_app.py#L197). Do not rebuild that capability merely to add a module name.

## Deferred or rejected now

| Candidate | Decision and reason to reconsider |
|---|---|
| RcMap | Strongest overlooked candidate; promote for shared tenant clients/connections leased by borrower Scope. More useful than ScopedRef when old users must keep resources alive. Needs keyed acquisition, per-borrower refcounts, owner close and abandoned-generation rules; not a cheap map wrapper. |
| ScopedCache | Distinct ownership: entries own resources and removal closes them. Do not substitute it for RcMap leases. Add for a demonstrated resource-cache lifetime, after ordinary Cache or independently if that caller dominates. |
| Pool | Defer general pool machinery until a real resource needs bounded exclusive/shared leases that the driver's own pool cannot supply. Fixed-size borrowing can precede Queue TTL policies, but waiter fairness, invalidation and shutdown remain substantial. |
| PubSub / SubscriptionRef | Valuable for live state/change feeds, but needs real subscriber lifetime and replay/backpressure semantics. Build after the scoped stream boundary, not as a tiny Ref extension. |
| Full Stream / Channel / Sink catalog | Too broad for the next unit. Start sequential and prove early termination, then concurrency/backpressure. |
| Config | A sensible small core recipe layer, lower immediate value because Pydantic already covers validation/settings integration. [Config.evaluate](../../../vendor/effect-v4/packages/effect/src/Config.ts#L131) reads active provider at execution; [provider.load](../../../vendor/effect-v4/packages/effect/src/ConfigProvider.ts#L278) distinguishes missing nodes from source errors. [withDefault](../../../vendor/effect-v4/packages/effect/src/Config.ts#L486) consumes missing-resolution results, not all Effect failures. Preserve missing vs malformed distinctions and provider substitution if implemented. |
| Full Schema/AST/JSON Schema port | Reject for current direction: Python's existing validator ecosystem covers the main caller need. An effectful decode adapter may be useful; it is not TS Schema parity. |
| Option/Cause helpers, Result, all/zip/match | Small helpers can save boilerplate, but no standalone “port every helper” phase. Some(None), error mapping and heterogeneous tuple typing deserve explicit tests. Do not add another awaitable wrapper: attempt already exists. |
| Type-inference cleanup | Keep supplied multi-catch/provide and pipeable-tap limitations visible; no fresh reproducer was run here. Add focused real-adapter fixtures first; preserve explicit intermediate bindings if needed. No checker plugin or broad new skill justified. |
| RequestResolver | Promote only with a real batched backend/N+1 workload. Coalescing batches and persistent cached values are different contracts. Requires cancellation-aware request completion, not just grouping a list. |
| Metrics/tracing | Targeted spans/annotations can help the real app; a full metrics/exporter framework is premature. Test context propagation and isolated runtimes before breadth. |
| Persistent rate limiter / workflow / cluster / event log / RPC platform | Defer separate packages. External state, durability and recovery dwarf a small primitive port. Semaphore + sleep is not equivalent to a distributed quota or durable workflow. |
| STM / TxQueue | Defer until a transactional runtime is a goal. Queue name similarity does not supply transaction semantics. |
| Python collection reimplementations | Prefer native collections; port only a missing observable behavior actually used by the app. |
| Reloadable / KeyedPool names | Scouts did not find those exact modules in this pinned source. Do not silently import a v3 ecosystem plan into v4. |

## Evidence coverage and reconciliation

Eight Codex Luna high result files are retained: [primitive APIs](./scout-1.md), [Schedule](./scout-2.md), [cached resources](./scout-3.md), [data flow](./scout-4.md), [Config/validation](./scout-5.md), [Python integration](./scout-6.md), [app ergonomics](./scout-7.md), [ecosystem breadth](./scout-8.md). Every completed child thread was read. Earlier dispatches were interrupted during the user's provider correction; none supplies conclusions here.

The lead independently read the decisive Schedule/Pull/recovery, awaitable/host cancellation, Cache entry, ScopedRef replacement, Pool waiter/TTL, RcMap get/release, Config/provider/default, Queue atomic-take and Stream/SubscriptionRef paths. This corrected three misleading implications: Pool does not universally need Queue; ScopedRef does not swap before release; Cache is not simply Deferred + map. It also removed Queue from the “new work” ranking after reconciling the parent's live thread.

Breadth coverage is an inventory plus focused source samples, **not an exhaustive line-by-line audit of all Effect packages**. CLI, exporters, durable platform families and third-party Python APIs were not deeply validated. No performance, production-readiness, formal library correctness or test-pass claim is made. Kernel bounded-model evidence does not prove these future libraries. Future implementations must satisfy the repository's real verifier and any applicable kernel/formal gates.

## Decision

**After Queue is accepted: implement the deterministic Schedule/retry/repeat unit, reusing its accepted Done/Pull contract, then prove it through the real-I/O snapshot worker.** Keep the thin async integration next to that app first. Follow with Cache; use ScopedRef or RcMap when the app demonstrates replacement versus borrower-lease ownership; grow the Stream boundary before the broad streaming ecosystem.
