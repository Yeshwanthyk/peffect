# Optional packages: adoption and practical priorities
Research date: 2026-11-15. Recommendation for peffect after its already-active Queue work. Package names below are proposals, not existing distributions.

## Answer
Inferred priority: HTTPX client → FastAPI/ASGI bridge → SQLAlchemy async integration, with pytest helpers alongside them. OpenTelemetry follows when request/worker tracing can prove propagation. Redis, AI providers, CLI and streaming protocols follow actual application demand.

These are optional Python integrations, not ports of Node/Bun adapters. Keep generic Effect, Scope, Layer, runtime, Schedule and concurrency in core. Do not import framework dependencies from core or recreate existing transport pools/ORMs.

## Adoption evidence
Checked: first-party npm API, inclusive 2026-11-08 through 2026-11-14. Luna scout retrieved all 16; lead independently retrieved the largest three. Corrected scout's prose ordering: platform-node exceeds vitest.
- [@effect/platform-node](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fplatform-node): 4,329,811
- [@effect/vitest](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fvitest): 2,952,447
- [@effect/opentelemetry](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fopentelemetry): 2,356,841
- [@effect/platform](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fplatform): 1,889,431
- [@effect/sql-pg](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fsql-pg): 1,675,145
- [@effect/platform-bun](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fplatform-bun): 1,630,549
- [@effect/experimental](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fexperimental): 966,780
- [@effect/rpc](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Frpc): 904,420
- [@effect/sql](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fsql): 583,088
- [@effect/cluster](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fcluster): 517,621
- [@effect/workflow](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fworkflow): 483,190
- [@effect/sql-sqlite-node](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fsql-sqlite-node): 459,164
- [@effect/ai-openai](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fai-openai): 242,536
- [@effect/cli](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fcli): 236,006
- [@effect/typeclass](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Ftypeclass): 235,127
- [@effect/ai](https://api.npmjs.org/downloads/point/2026-11-08:2026-11-14/%40effect%2Fai): 76,434

These count downloads, not unique adopters, production projects, or direct dependencies. CI and transitive dependencies overlap; do not sum counts. These npm package boundaries do not establish popularity of pinned Effect v4 or peffect. Method: [npm download-count API](https://github.com/npm/registry/blob/main/docs/download-counts.md).

Documented survey evidence: [JetBrains 2026](https://blog.jetbrains.com/go/2026/10/13/python-vs-go/) reports FastAPI 50%, Django 31%, Flask 30% among the weighted Python-section respondents to the framework question (n=4,276; multiple selections). This supports an audience for FastAPI integration, not a global market-share claim.

## Proposed packages and minimum useful scope
All priority and effort judgments below are inferred from existing peffect source research and documented integration surfaces.

| Priority | Proposed package | Useful first scope | Proof |
|---|---|---|---|
| 1 | peffect-httpx | Shared scoped AsyncClient; request effects; explicit transport/status/decode errors; buffered response first | Local server: success, timeout, cancellation, typed failure, close exactly once |
| 2 | peffect-fastapi | ManagedRuntime in application lifespan; request scope; typed handler bridge and explicit error-to-response mapping | Startup/shutdown; concurrent request isolation; cancelled request cleans up; replacement test Layer |
| 3 | peffect-sqlalchemy | Async engine Layer; scoped per-task/session transactions; typed driver failure mapping | Commit success, rollback failure/interruption, no shared concurrent session, connection returned |
| Parallel | peffect-pytest | Runtime fixture, Layer overrides, TestClock helpers, deterministic cleanup assertions | Same HTTP/DB app exercised under real and substituted services |
| 4 | peffect-opentelemetry | Fiber/request span propagation, meaningful error/cancellation status, scoped exporter lifetime | Parent-child trace relationships across forks; no cross-request leakage |
| 5 | peffect-redis | Scoped async client; typed commands, timeouts; explicit cache invalidation | Cleanup and cancellation; separate external Redis semantics from local Cache |
| Demand-led | AI provider adapter | Typed model/tool calls and structured outputs; provider-specific errors | Cancellation, rate limits, schema failures; streaming deferred until pull/lifetime support |
| Later | CLI integration | Run/dispose effects from a host CLI; validate config before services | Exit codes, interruption, finalizers |

### Documented external surfaces
- [HTTPX async client](https://www.python-httpx.org/async/): reusable client and async response lifecycle. Use its pooling; no peffect Pool prerequisite.
- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/): startup/shutdown async contextmanager. Fit existing FastAPI applications; do not replace framework DI wholesale.
- [SQLAlchemy asyncio 2.1](https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html): async engine, sessions and transaction interfaces. Integration must respect per-concurrent-task session ownership. Its pool already exists; a generic peffect Pool is not a prerequisite.
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/): existing observability ecosystem. Added value should be Effect context propagation rather than duplicate automatic HTTP instrumentation.
- [redis-py asyncio](https://redis.io/docs/latest/develop/clients/redis-py/async/): async client and explicit lifecycle.
- [Pydantic AI](https://pydantic.dev/docs/ai/overview/): candidate existing AI ecosystem to integrate, not reason to rebuild an agent framework.

### Local foundations
Earlier source investigation: [attempt/from_async](../../../src/peffect/core.py#L940), [host cancellation](../../../src/peffect/fiber.py#L967), [resource acquisition](../../../src/peffect/resources.py#L17), ManagedRuntime and Layer support these integrations. Pydantic validation already appears [at the application boundary](../../../examples/parity/python_app.py#L197).
See [full foundation research](REPORT.md) for source dependency evidence.

## Recommended proof application
After Queue acceptance: a FastAPI webhook service validates input, puts bounded in-memory jobs onto Queue, workers call an HTTP API through HTTPX with explicit Schedule retry policy, and persist results through SQLAlchemy. Scope owns clients/workers; shutdown and failure paths are tested. Start with outbound HTTP worker, then add server and DB as independently verified slices. Do not claim durable queue/exactly-once delivery from an in-memory Queue; durable jobs require persistence and a separate recovery protocol.

## Deferred / limits
RPC, cluster and durable workflow packages unlock narrower, harder distributed guarantees; downloads alone do not justify implementing them early. SSE/WebSockets/streaming bodies add lifetime and backpressure requirements; buffered HTTP does not need general Stream first. Pydantic validation is already used, so a wrapper needs demonstrated boilerplate savings. No broad PyPI download comparison was collected, so Python usefulness order is engineering judgment, not a measured Python popularity ranking. No implementation or tests were run. Only scratch research artifacts were written.
