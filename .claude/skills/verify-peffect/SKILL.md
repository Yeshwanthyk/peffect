---
name: verify-peffect
description: Prove peffect works as a library a user imports. Runs the public-API example, the kernel boundary lint, and the full pytest, ruff and pyright checks, saving each step's output. Run before calling any peffect change done, and after adding a module outside the kernel.
---

# verify-peffect

peffect's surface is a Python library: users write `from peffect import ...` and run effects on
asyncio. This skill drives that surface through `examples/`, which import nothing else, and
checks that code outside the kernel stays on the public side (docs/INVARIANTS.md, "Kernel
boundary"). The fiber protocol itself is model-checked by
[verify-formal-kernel](../verify-formal-kernel/SKILL.md); run that one too when `fiber.py`, or
`Async` or masking code in `core.py`, changes.

## Launch

Nothing to start: no server, port or data directory. `uv run` syncs `.venv` from `uv.lock` and
installs peffect from `src/` in editable mode. Each step is a short-lived process that exits.

## Doctor

`run.sh` starts with a read-only check: Python is 3.13+ and `import peffect` resolves to this
checkout's `src/peffect/__init__.py`, not some other install. If the doctor fails, nothing is
driven and the run exits 1.

## Drive

```sh
.claude/skills/verify-peffect/run.sh
```

Steps, in order: `doctor`, `example` (`examples/quickstart.py`), `boundaries`
(`scripts/check_boundaries.py` on `src/peffect` outside the kernel, and `examples/`), `pytest`
(all tests, including the boundary probes in `tests/test_boundaries.py`), `ruff check .`, and
`pyright`. A failing step doesn't stop the later ones, so one run shows every failure.

## Evidence

Each run writes `<base>/<UTC time>-<pid>/`, where `<base>` is `$PEFFECT_EVIDENCE` or
`${TMPDIR:-/tmp}/peffect-verify`. Inside it, each step gets `<step>.log` (the command, then its
output) and `<step>.exit`, and `summary.txt` collects every step's exit code and the verdict. The
last lines printed are the evidence path and either `all steps passed` or `FAILED: <steps>`.
The run exits 0 only if every step exits 0.

## Cleanup

The run owns no processes, ports or scratch files outside pytest's own temporary directories,
which pytest removes. Keep the evidence directory; delete old runs under `<base>` by hand.

## Features

[features/README.md](features/README.md) lists what this skill covers and how each feature is
driven.
