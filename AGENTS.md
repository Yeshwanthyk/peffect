# peffect

A Python port of Effect v4's runtime: lazy, typed effects (`Effect[A, E, R]`) run by fibers on
asyncio. Why it is built this way: `docs/DESIGN.md`. What gets ported in which order:
`docs/ROADMAP.md`. What the kernel promises and how each promise is proven: `docs/INVARIANTS.md`.
The upstream TypeScript source is vendored read-only in `vendor/effect-v4/`.

## Before calling a change done

- Any change: `.claude/skills/verify-peffect/run.sh`, which runs the examples, the boundary lint,
  pytest, ruff and pyright, and prints where it saved the evidence. Report that path.
- A change to `fiber.py`, or to `Async`, `Failure` or masking in `core.py`: also
  `.claude/skills/verify-formal-kernel/run.sh`, and read its SKILL.md "Update" section first.

## The kernel boundary

`src/peffect/core.py`, `fiber.py` and `cause.py` are the kernel. Every other module, and every
example, is written in peffect itself:

- Import public names: `from peffect import ...`, or from the kernel module that defines them
  (`from .core import Effect, succeed`) when importing the package would be circular.
- To read the running fiber, use `WithFiber(lambda fiber: ...)` from `peffect.core`, and touch
  only the Fiber members B3 allows. `fiber.context` is read-only; change it with
  `update_context` (or `provide`), never by writing to the fiber.
- Build suspension from `async_`, not by constructing `Async` or frames.

`scripts/check_boundaries.py` enforces this (rules B1–B3 in `docs/INVARIANTS.md`, with what it
can't see). If a module needs more than the boundary allows, the missing piece belongs in the
kernel, as a public combinator with its own invariant; widening the allowed list is a design
change that needs a matching update to INVARIANTS B1–B3.

`tests/` and `typetests/` are white-box: they may import kernel internals to drive the run loop.

## Services (Context, Tag, Reference, provide)

```python
class Users(Tag[UserRepo], key="app/Users"): ...  # one class per service, keyed by its string
class Level(Reference[str], key="app/Level"):     # a service with a default: never adds to R
    @classmethod
    def default(cls) -> str: return "info"

repo = yield from Users()                  # R gains Users
await run(provide(Users.context(repo) | Level.context("debug"))(program()))
```

Mistakes this prevents (rows C1–C8 in `docs/INVARIANTS.md`):

- Every tag declares an explicit, unique `key=` (no default). Two declarations with one key are
  one service at runtime, as upstream; raw context maps are keyed by `Users.key`, not the class.
- Subclassing a concrete tag (`class Admins(Users)`) raises: a `Users` service could never
  answer an `Admins` request that the types would accept.
- `Context(...)` can't be built directly; only `Tag.context` and `|` make one, so a
  `Context[Users]` always holds a Users service. On `|`, the right side wins.
- Don't widen R to make a call type-check, or `cast` it away. `provide` removes a tag only when
  R names it; a generic `R` is kept, which is correct, not a bug to work around.
- A Reference default is built once per declaration (not per key) and shared by every run. Make it a value, not
  per-run state; give per-run state its own `provide`.
- To change the context, use `provide` or `update_context`: both put the old one back however
  the effect ends, including an interrupt.

## Time (Clock, sleep)

Read time and sleep through `current_time_millis` / `sleep`, never `time.time()` or
`asyncio.sleep` inside an effect: those bypass the fiber's Clock, so a provided Clock (and later
TestClock) can't control them. Swap time with `provide(Clock.context(my_clock))`; `my_clock` only
needs the `ClockService` members, and its `sleep` receives a `Duration` (`peffect.duration`).
`sleep` takes a `Duration.Input` as upstream: a Duration, a string such as `"5 seconds"`, or a
bare number of **milliseconds** (`sleep(10)` is 10 ms, not 10 s). Rows CL1–CL5 in
`docs/INVARIANTS.md`.

## Testing time, output and scoped fibers (TestClock, TestConsole, fork_scoped)

Provide `TestClock.layer()` and drive time with `TestClock.adjust` / `TestClock.set_time`; a
`sleep` under it never touches the loop timer. Fork the sleeping work first, then adjust:
`adjust` waits for the woken fibers to settle, bounded by the busy-warning timeout. Assert right after it, with no
`yield_now` or `asyncio.sleep` to "let things run". Use `TestClock.with_live(eff)` for a real
wait inside a test. Capture output with `TestConsole.layer` and read `TestConsole.log_lines`.
Log through `log_warning` / `log_with_level(level)`, which add nothing to R.

Mistakes this prevents:
- Adding `yield_now` or live sleeps after `adjust` to "let fibers run": `adjust` is the settle
  point (a barrier before it, wake tracking after), and fixed yields hide ordering bugs.
- A test that waits on a TestClock sleep without adjusting: the clock prints a warning to the
  live console after `warning_delay`; it is not a hang to work around with live sleeps.
- `TestClock.make()` needs a Scope (it stops its warning on close); the layer provides one.
- `fork_scoped` adds `Scope` to R and ties the fiber to that scope, not to its parent; use it
  for a background fiber that must outlive the forking fiber but not the scope.
- A Logger listed twice in `CurrentLoggers` logs once; levels are upstream's names (`"Warn"`).
Rows TC1–TC6, CO1, LG1, FS1 in `docs/INVARIANTS.md`; lesson `docs/lessons/06-testclock.html`.

## Conventions

- Leave `@gen` bodies unannotated so pyright infers `Effect[A, E, R]` from the yields; ruff's
  return-annotation rule is off for `tests/`, `typetests/` and `examples/` for that reason.
- Pin a type-level promise with a block in `typetests/should_pass.py` or a numbered
  `# N. NAME` block in `typetests/should_fail.py`; `tests/test_types.py` checks both.
- Each roadmap tier ships a lesson in `docs/lessons/`, its rows in `docs/INVARIANTS.md`, and a
  section here with the idiom and the mistakes it prevents.

## Mutable state

Use `Ref[int].make(0)` for synchronous cell updates and `SynchronizedRef[int].make(0)`
when an update runs effects. Synchronized writes serialize; `get()` remains unlocked.
Do not run a synchronized write inside another write on the same cell: the permit is not
reentrant. An interrupted update may already have committed; cancellation does not imply
rollback. Use immutable replacement values when rollback of callback-side mutation matters.

## Completion primitives

Use `suspend_exit` when a destructive synchronous read returns an Exit that must reach its
first consumer in the same operation; return nonterminal effects to wait normally. For
end-of-input, use `Done` in E and `peffect.pull.catch_done`: it preserves real failures merged
with completion. The remaining Pull surface belongs to the Stream lane. Contracts and proof limits:
`docs/INVARIANTS.md`, QP1-QP4.


## Queue workers

Create `Queue[Job, Done | SourceError].bounded(2)` for backpressure. Send with `offer` /
`offer_all`; `into(producer)` publishes the producer's terminal outcome. Read with `take`
or batches, and handle completion with `pull.catch_done`. End closes admission and drains;
shutdown discards buffered work. Use `fork_scoped` and join workers after `await_()` because
drained input can still be processing. Runnable composition: `examples/queue_worker.py`;
source map and edge cases: `docs/QUEUE_SOURCE_MAP.md`; invariants Q1-Q10, lesson 08.

- An interrupted receive may already have delivered its message; later processing is not
  rolled back or automatically requeued. Track application acknowledgments when required.
- A zero-capacity queue rendezvouses; peek only sees buffered handoffs. take_n above capacity
  may return fewer than n, and Closing permits short batches.
- Keep raw capacity compatibility separate from normal bounded guarantees. Use nonnegative
  integer capacity (or unbounded) in applications; counts are normalized by the primitive.
- Completion payload unions type-check as `Done[X | Y]`; Pyright's exclusion inference for
  `Done[X] | Done[Y]` can retain a Done member even though runtime filtering removes both.


## Enforced Python quality

Run `uv run ruff check .` and `uv run pyright`; both run in the full verifier and
in `.github/workflows/quality.yml` on pushes and pull requests.

| Rule | Enforcement |
| --- | --- |
| Remove casts that do not change the inferred type | Pyright `reportUnnecessaryCast` |
| No duplicate class fields or enum values, useless container-factory lambdas, or repeated prefix/suffix calls | Ruff PIE794, PIE796, PIE807, PIE810 |
| No duplicate parametrized test cases or literal assertions that always fail/pass accidentally | Ruff PT014, PT015, PLW0129 and existing F631 |
| Use specific `noqa` codes; remove unused suppressions | Ruff PGH004 and existing RUF100 |
| Write slots as a collection, with stable ordering | Ruff PLC0205 and existing RUF023 |
| Respect the kernel boundary and pipe typing contracts | `check_boundaries.py` and `test_pipe_signatures.py` |

Slots/final, identity versus value equality, keyword-only API changes, exception policies,
and comment/test usefulness require caller and behavior evidence. Do not apply them as blanket
style rules. Tests may inspect kernel internals; `@gen` bodies remain unannotated for inference.

## Verification skills by change

| Change | Read and run |
| --- | --- |
| Any repository change | [.claude/skills/verify-peffect/SKILL.md](.claude/skills/verify-peffect/SKILL.md); lint the full owned tree with `uv run ruff check .` |
| Fiber/Async/Failure/masking protocol | [.claude/skills/verify-formal-kernel/SKILL.md](.claude/skills/verify-formal-kernel/SKILL.md), including Update |
| Child completion or its regression test | [.claude/skills/verify-mutations/SKILL.md](.claude/skills/verify-mutations/SKILL.md) |

The library verifier's deeper-check table routes concurrency, type/API, retention and
performance work. A skill is the runbook; its executable tests and scripts provide evidence.
