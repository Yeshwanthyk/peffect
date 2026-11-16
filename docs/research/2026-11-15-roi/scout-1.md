# Scout 1

<t3-thread://v2/c94b318d-6569-4fbb-ada1-8f29f58f639f/thread:delegated-task:command%3Amcp%3A2bb3f3e0-03ef-4a0d-af8e-26799735b205%3Adelegate-task%3Apeffect-roi-codex-1>

Method: direct — inspect the specified routing and repo guidance, then compare the live typed helpers against pinned source and app call sites.  
Plan: ends: compact evidence report for the lead.

**ISSUES** — Live state matches the assigned baseline and pinned vendor; the narrow API comparison shows several useful source primitives are absent. This is a source inspection only; I did not run tests or modify files.

- **Live state:** `/Users/yesh/code/personal/peffect` is on `build/primitives` at `da88bfccb383d660fd96bf78d33ca0207bd658dd`; `vendor/effect-v4` is at `289a53f7b1fc40049219c6e69022db2fb859c356`. `git status --short --branch` showed no working-tree changes.

- **Already implemented:** `Effect.pipe` (up to 20 steps), core effect nodes/combinators, `Exit` as `Success | Failure`, and `Cause` with ordered reasons and queries are present in [`core.py`](../../../src/peffect/core.py), [`cause.py`](../../../src/peffect/cause.py), and the public exports in [`__init__.py`](../../../src/peffect/__init__.py). The core’s stated rationale is iterative fiber frames to avoid recursive combinator calls (`core.py`, lines 1–20); preserve that model when adding helpers.

- **Strong small-scope candidates:**  
  - `Option` currently only supplies `Some[A] | None`; its module explicitly says only construction, value, and structural equality are ported ([`option.py`](../../../src/peffect/option.py), lines 1–6). Upstream has `match`, `map`, `flatMap`, `all`, `zipWith`, and collection helpers ([vendor `Option.ts`](../../../vendor/effect-v4/packages/effect/src/Option.ts), lines 418, 1124, 1256, 1677, 1740, 1858–1949). `Ref` already consumes `Option` in update operations ([`ref.py`](../../../src/peffect/ref.py), lines 78–126), so `map`/`match`/`flatMap` are natural ergonomic additions. `None` as absence is a deliberate Python representation; confirm typing and preserve distinction of `Some(None)`.
  - Core `Cause` has basic construction, combine, filtering, and queries, but lacks upstream’s `map` and structured `find*` helpers ([`cause.py`](../../../src/peffect/cause.py), lines 35–77; vendor [`Cause.ts`](../../../vendor/effect-v4/packages/effect/src/Cause.ts), lines 683, 817–1062). `Cause.map` is a compact addition with direct support in the current flat ordered tuple representation; structured tree traversal is not required for that subset.
  - Effect v4 exposes `fromResult`, `fromOption`, `all`, `zipWith`, `match`, and several error/cause handlers ([vendor `Effect.ts`](../../../vendor/effect-v4/packages/effect/src/Effect.ts), lines 507, 1905–1947, 2742, 3398, 5583). The Python API already has `exit_`, `catch_all`, `catch_cause`, and `catch_tag` ([`__init__.py`](../../../src/peffect/__init__.py), lines 8–37); prioritize additions only where app call sites show repeated boilerplate.

- **Concrete app usage / proof targets:** Examples directly use `.pipe(catch_tag(...))`, inspect `Failure`/`Cause`, and manually classify exits ([`quickstart.py`](../../../examples/quickstart.py), lines 47–77; [`scope.py`](../../../examples/scope.py), lines 40 onward; [`python_reference.py`](../../../examples/parity/python_reference.py), lines 45–73). These are good semantic proof targets for Option helpers, Cause mapping, and any exit match utility. They do not establish demand for broad collection combinators: `for_each` already exists in [`fiber.py`](../../../src/peffect/fiber.py), lines 642–669.

- **Async boundary:** The source includes `tryPromise` ([vendor `Effect.ts`](../../../vendor/effect-v4/packages/effect/src/Effect.ts), line 1008); peffect’s `async_` and `from_async` are public ([`__init__.py`](../../../src/peffect/__init__.py), lines 8–37). Any promise-like convenience would need a clear contract for Python awaitables, exception-to-typed-failure mapping, cancellation, and fiber interruption. Treat this as a correctness-sensitive boundary, not a cheap alias; inspect `async_` implementation and tests before proposing it.

- **Keep Result scope explicit:** Upstream `Result` is a separate module with constructors and mapping/composition functions ([vendor `Result.ts`](../../../vendor/effect-v4/packages/effect/src/Result.ts), lines 412–453, 755–889, 1314). Current Python `Option` and `Exit` already cover some adjacent use cases; a new `Result` module should be justified by app-facing conversions and types, rather than ported solely for parity.

**Inferred minimal next slice:** add small pure `Option` combinators and `Cause.map`, then derive proof cases from current examples and existing type tests. Evaluate `Result` and async wrappers separately. Potential risks are preserving `Some(None)`, correct generic narrowing/variance, and ensuring async failures retain interruption semantics.

Used: `/Users/yesh/.codex/skills/yesh-router/SKILL.md`, `../../../AGENTS.md` · playbook: — · principles: — · steps 1/1
