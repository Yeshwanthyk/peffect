---
name: verify-peffect
description: Prove peffect works as a library a user imports. Runs the public-API examples, the kernel boundary lint, and the full pytest, ruff and pyright checks, saving each step's output. Run before calling any peffect change done, and after adding a module outside the kernel.
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

Steps, in order: `doctor`, `example` (`examples/quickstart.py`), `context`
(`examples/context.py`), `clock` (`examples/clock.py`), `scope` (`examples/scope.py`),
`parity` (fresh TypeScript/Python application comparison), `parity_ts` (TypeScript compilation),
`parity_types` (positive/negative TypeScript fixtures), `boundaries`
(`scripts/check_boundaries.py` on `src/peffect` outside the kernel, and `examples/`), `pytest`
(all tests, including the boundary probes in `tests/test_boundaries.py`), `ruff check .`, and
`pyright`. A failing step doesn't stop the later ones, so one run shows every failure.

The parity steps also need Bun and the pinned TypeScript dev dependency in `examples/parity/`.
Setup and the comparison boundary are documented in `examples/parity/README.md`. Missing tools
fail the steps; the verifier never substitutes captured output for an actual reference run.

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

## Select deeper checks

The full run remains required for every change. Ruff must run as `uv run ruff check .`,
not just on changed files. Its owned Python scope includes source, tests, type fixtures,
examples and scripts; vendor, generated/ignored files and the pinned TLC chart helper follow
`pyproject.toml` exclusions. A pass does not mean those excluded files were linted.

| Changed contract | Additional verification |
| --- | --- |
| Wake, cancellation, queue or runtime lifecycle | Run the existing kernel/queue property and interleaving suites below; preserve minimized failing schedules as regressions. |
| Kernel protocol | Read and run `../verify-formal-kernel/SKILL.md`, including its Update requirements. |
| Child completion or its regression assertions | Run `../verify-mutations/SKILL.md`. |
| Public signature, generic variance, service/error channel | Add positive and negative cases to `typetests/`; `tests/test_types.py` runs in the full suite. Exercise changed public behavior through a consumer example. |
| Context/resource ownership | Extend weak-reference/cleanup assertions in `tests/test_runtime_hardening.py` or the relevant lifecycle suite; distinguish retained handles from leaks. |
| Performance claim | Use repeated measurements of the actual workload and record environment/baseline. No general performance gate exists yet. |

```sh
uv run pytest -q tests/test_kernel_props.py tests/test_queue_props.py tests/test_queue_interleavings.py tests/test_managed_runtime_interleavings.py tests/test_runtime_hardening.py
# Optional longer generated search; retain the seed/reproducer if it fails:
uv run pytest -q tests/test_kernel_props.py tests/test_queue_props.py --hypothesis-profile=deep
uv run python examples/queue_worker.py
```

The default generated suites already run in pytest. Use the deep profile for a wider search,
not as evidence that every schedule has been explored. The model abstracts context and values;
runtime tests cover selected concrete histories. Lint/types cannot prove callback ownership,
API usability, useful comments, or that every remaining file is needed.

For cleanup, identify callers, public exports, packaging and documentation references before
removing code. Keep a deletion decision in `untangle-log.md`; unused imports are mechanically
caught, while unreferenced public APIs may still have external consumers.
