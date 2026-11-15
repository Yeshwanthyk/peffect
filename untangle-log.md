# Untangle log: kernel boundary (tier 1)

Scope: structure only, the line between the kernel (`src/peffect/core.py`, `fiber.py`, `cause.py`)
and everything built on it. Comment and test cleanup were not selected. `core.py`'s length (715
lines) is not logged: it holds the seven nodes and their frames, which change together.

## Fixes, each above the problems it solves

- Write the boundary down as rules B1–B3 (`docs/INVARIANTS.md`, "Kernel boundary") and enforce
  them with `scripts/check_boundaries.py`, tested in `tests/test_boundaries.py` and run by
  `verify-peffect`. Kernel code is unchanged.
  - `docs/ROADMAP.md:10` (before this change) stated the rule, "only the kernel knows about
    fibers", in prose only; nothing checked it, and the planned check covered only `_private`
    names and Fiber fields.
  - Kernel internals have public-looking names, so the type checker's private-name rule doesn't
    cover them: `core.py:198` `Park`, `core.py:210` `Async`, `core.py:251` `Frame`,
    `core.py:267` `Then`, `core.py:285-404` the node/frame classes.
  - `fiber.py:54-60` `Fiber` exposes run-loop state without underscores (`children`,
    `interrupted`, `interruptible`, `running`, `stack`), and `fiber.py:136-200` exposes unsafe
    primitives (`interrupt_unsafe`, `take_interrupt`, `exit`, `add_observer`) next to the three
    public effects (`await_`, `join`, `interrupt`).

## Left in place, with the reason

- `src/peffect/__init__.py:3-32` (imports) and `:34-72` (`__all__`) list every public name twice.
  That is the Python convention for re-exports, and the lint now fails with exit 2 if an `__all__`
  name has no matching import, so the two can't drift silently.
- `tests/test_kernel_props.py:28-30` imports `Async`, `WithFiber`, `_start`, and
  `:158-160`/`:292-301` read private fiber state. These are white-box tests of the run loop;
  `tests/` is outside the lint's scope on purpose.

## Special cases in scope

Before: none enforced (the rule was prose). After, the lint has six, each on purpose:

1. `ESCAPE_HATCH`: `peffect.core.WithFiber` is importable though it is not in `__all__`.
2. `FIBER_PUBLIC`: the four Fiber members code outside the kernel may use.
3. `KERNEL_FILES`: the four kernel files are exempt, by exact path.
4. `tests/` and `typetests/` are exempt, by not being in the default paths.
5. A `WithFiber` callback that is neither a lambda nor a named def fails (it can't be checked).
6. `getattr`/`setattr`/`delattr`/`vars` on a known Fiber fails (a string name hides the member).

## Independent review corrections (2026-11-15)

- Separate definition-time expressions from body scope in the checker visitor.
  - `scripts/check_boundaries.py:198` previously visited defaults/decorators after entering the
    body scope, missing an outer package reach and misidentifying an ordinary lambda default.
- Validate the whole literal export declaration before constructing its mapping.
  - `scripts/check_boundaries.py:60` previously discarded malformed export metadata, allowing
    an empty or incomplete map to pass. Unsupported export mutations now fail closed.

Both CLI regression tests were red before the correction and green afterwards. Sol's fresh
structural search found no further tier 1 coupling problem worth fixing. The six policy
exceptions above are not a measured reduction in special cases.
