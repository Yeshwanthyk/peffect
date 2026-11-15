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
class Users(Tag[UserRepo]): ...            # one class per service; it is the lookup key
class Level(Reference[str]):               # a service with a default: never adds to R
    @classmethod
    def default(cls) -> str: return "info"

repo = yield from Users()                  # R gains Users
await run(provide(Users.context(repo) | Level.context("debug"))(program()))
```

Mistakes this prevents (rows C1–C8 in `docs/INVARIANTS.md`):

- Subclassing a concrete tag (`class Admins(Users)`) raises: lookup is by exact class, so a
  `Users` service could never answer an `Admins` request that the types would accept.
- `Context(...)` can't be built directly; only `Tag.context` and `|` make one, so a
  `Context[Users]` always holds a Users service. On `|`, the right side wins.
- Don't widen R to make a call type-check, or `cast` it away. `provide` removes a tag only when
  R names it; a generic `R` is kept, which is correct, not a bug to work around.
- A Reference default is built once per class and shared by every run. Make it a value, not
  per-run state; give per-run state its own `provide`.
- To change the context, use `provide` or `update_context`: both put the old one back however
  the effect ends, including an interrupt.

## Conventions

- Leave `@gen` bodies unannotated so pyright infers `Effect[A, E, R]` from the yields; ruff's
  return-annotation rule is off for `tests/`, `typetests/` and `examples/` for that reason.
- Pin a type-level promise with a block in `typetests/should_pass.py` or a numbered
  `# N. NAME` block in `typetests/should_fail.py`; `tests/test_types.py` checks both.
- Each roadmap tier ships a lesson in `docs/lessons/`, its rows in `docs/INVARIANTS.md`, and a
  section here with the idiom and the mistakes it prevents.
