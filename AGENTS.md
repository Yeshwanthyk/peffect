# peffect

A Python port of Effect v4's runtime: lazy, typed effects (`Effect[A, E, R]`) run by fibers on
asyncio. Why it is built this way: `docs/DESIGN.md`. What gets ported in which order:
`docs/ROADMAP.md`. What the kernel promises and how each promise is proven: `docs/INVARIANTS.md`.
The upstream TypeScript source is vendored read-only in `vendor/effect-v4/`.

## Before calling a change done

- Any change: `.claude/skills/verify-peffect/run.sh`, which runs the example, the boundary lint,
  pytest, ruff and pyright, and prints where it saved the evidence. Report that path.
- A change to `fiber.py`, or to `Async`, `Failure` or masking in `core.py`: also
  `.claude/skills/verify-formal-kernel/run.sh`, and read its SKILL.md "Update" section first.

## The kernel boundary

`src/peffect/core.py`, `fiber.py` and `cause.py` are the kernel. Every other module, and every
example, is written in peffect itself:

- Import public names: `from peffect import ...`, or from the kernel module that defines them
  (`from .core import Effect, succeed`) when importing the package would be circular.
- To read the running fiber, use `WithFiber(lambda fiber: ...)` from `peffect.core`, and touch only
  `fiber.context`, `fiber.await_()`, `fiber.join()` and `fiber.interrupt()`.
- Build suspension from `async_`, not by constructing `Async` or frames.

`scripts/check_boundaries.py` enforces this (rules B1–B3 in `docs/INVARIANTS.md`, with what it
can't see). If a module needs more than the boundary allows, the missing piece belongs in the
kernel, as a public combinator with its own invariant; widening the allowed list is a design
change that needs a matching update to INVARIANTS B1–B3.

`tests/` and `typetests/` are white-box: they may import kernel internals to drive the run loop.

## Conventions

- Leave `@gen` bodies unannotated so pyright infers `Effect[A, E, R]` from the yields; ruff's
  return-annotation rule is off for `tests/`, `typetests/` and `examples/` for that reason.
- Pin a type-level promise with a block in `typetests/should_pass.py` or a numbered
  `# N. NAME` block in `typetests/should_fail.py`; `tests/test_types.py` checks both.
- Each roadmap tier ships a lesson in `docs/lessons/`, its rows in `docs/INVARIANTS.md`, and a
  section here with the idiom and the mistakes it prevents.
