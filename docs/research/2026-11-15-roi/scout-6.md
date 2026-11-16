# Scout 6

<t3-thread://v2/c94b318d-6569-4fbb-ada1-8f29f58f639f/thread:delegated-task:command%3Amcp%3A2bb3f3e0-03ef-4a0d-af8e-26799735b205%3Adelegate-task%3Apeffect-roi-codex-6>

**PASS** — live state matches the assigned baseline: `/Users/yesh/code/personal/peffect` is on `build/primitives` at `da88bfccb383d660fd96bf78d33ca0207bd658dd`; `vendor/effect-v4` is at pinned `289a53f7b1fc40049219c6e69022db2fb859c356`. Both status outputs were clean. No files were changed.

### Findings

- **Async boundary — exact source call:** [`core.py`](../../../src/peffect/core.py#L940) `attempt` calls a thunk synchronously, turns only caught exception types into typed failures, and treats other exceptions as defects. Awaitable results are scheduled via `asyncio.ensure_future` at lines 966–988; fiber interruption cancels the task and waits for its completion (lines 982–986). This is a useful adapter seam for async Python libraries, but there is no general runtime mechanism here for blocking I/O or moving work to a thread.

- **Runtime boundary — exact source calls:** [`fiber.py`](../../../src/peffect/fiber.py#L967) exposes `await_exit`, `run_exit`, and `run`; cancelling the awaiting asyncio task interrupts the fiber and waits for finalizers before re-raising `CancelledError` (lines 968–980). [`managed_runtime.py`](../../../src/peffect/managed_runtime.py#L1) documents a runtime that lazily builds a `Layer`, runs effects with its services, and disposes managed fibers/resources. Its own port notes say there is no sync or callback runner and no AbortSignal (`managed_runtime.py:19–24`). The direct host idiom in [`quickstart.py`](../../../examples/quickstart.py#L51) is an async `main` that awaits `run(...)` and is entered through `asyncio.run(main())` (lines 83–84).

- **Resource lifecycle — exact source calls:** [`resources.py`](../../../src/peffect/resources.py#L17) composes public `Scope`, `WithFiber`, `uninterruptible`, and `on_exit`; `acquire_release` masks acquisition and registers release before interruption can resume (lines 24–47). This plus `Layer`/`ManagedRuntime` gives an existing basis for scoped clients and pools; there is no observed HTTP, database, or CLI adapter in `src/peffect` or `examples`.

- **HTTP opportunity — vendored upstream architecture:** [`HttpServer.ts`](../../../vendor/effect-v4/packages/effect/src/http/HttpServer.ts#L41) defines a server service with `serve` and `address`; its contract takes a response effect and supplies request service per request (lines 69–77, 80–87). This points to a small Python integration opportunity: an optional ASGI adapter that maps request/response and lifespan to peffect services/scopes, with an effect runtime at the host boundary. The vendored server contract is a reference, not evidence that ASGI exists in peffect or that a particular Python framework API has been verified.

- **Database opportunity — vendored upstream architecture:** [`SqlConnection.ts`](../../../vendor/effect-v4/packages/effect/src/sql/SqlConnection.ts#L28) separates a low-level driver connection from the client; the connection contract has execute variants and streaming, and its `Acquirer` requires a `Scope` (lines 68–75). That supports keeping generic SQL client/pool policy and each Python driver binding in an optional integration package. The current core has no `Stream` in its module inventory, so streaming SQL is a genuine missing primitive dependency for a faithful version of this surface; basic scoped connection acquisition and async execution have more existing foundations.

- **CLI opportunity — evidence gap:** The vendored tree contains `packages/effect/src/cli/Command.ts` and `cli/index.ts`, but I did not inspect their contracts in this bounded pass. No CLI module appears under current `src/peffect` or `examples`. A Python CLI adapter could compose typed effects with existing `run`/`run_exit`, but command parsing, typed arguments, and help/completion are not established by the evidence gathered here.

### Candidate scope and proof targets

Inferred from these source boundaries: keep kernel work generic (runtime entry points, cancellation/finalizer behavior, scoped resource lifecycle); place ASGI, DB-driver, and CLI bindings in separate optional Python integrations. The high-value first steps appear to be an ASGI lifespan/request bridge and a scoped async database connection adapter. A blocking-driver/thread bridge is a separate missing capability and should not be implied by `attempt`.

Proof targets for a later implementation: request cancellation and disconnect behavior; startup/shutdown scope closure; connection release on success, typed failure, defect, and interruption; runtime disposal with active fibers; and host-level type checking around service requirements. This pass did not run tests or verify Python third-party APIs.
