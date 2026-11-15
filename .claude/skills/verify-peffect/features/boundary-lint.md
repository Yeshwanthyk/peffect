# Boundary lint

Code outside the kernel that imports or touches kernel internals fails the lint, naming the
file, the line and the rule broken.

## Behaviors

- **L1** The repo itself passes: exit 0, `0 violations`.
- **L2** Each forbidden reach is reported on its own line: B1 import, B2 attribute through the
  `peffect` module, B3 Fiber member. Exit 1.
- **L3** The allowed spellings pass: public names imported from `peffect` or from the kernel
  module that defines them (relative imports too), `WithFiber`, and the public Fiber members.
- **L4** A path that can't be read or parsed is an error (exit 2), never a clean pass.

## User entry points

- `uv run python scripts/check_boundaries.py` (default paths: `src/peffect`, `examples/`)
- `uv run python scripts/check_boundaries.py PATH ...` (files or directories)
- `uv run pytest tests/test_boundaries.py`, which drives the same CLI on probe files

## Drive

```sh
uv run python scripts/check_boundaries.py          # L1: "boundaries: N files checked, 0 violations"
uv run pytest -q tests/test_boundaries.py          # L2-L4 on probes in a temporary package
```

To see L2 by hand, write `from peffect.core import Async` into a scratch file and pass its path:
the lint prints `<path>:1: B1 \`Async\` from peffect.core is kernel-internal` and exits 1.

## Proof

`boundaries.log` and `boundaries.exit` (L1), and `pytest.log` (L2–L4) in the run's evidence
directory.

## Gotchas

- The summary line counts files. `0 files checked` means the paths matched nothing, which is not
  proof that the code is clean.
- The kernel files are exempt by exact path. A file elsewhere named `core.py` is still checked.
