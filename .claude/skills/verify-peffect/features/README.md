# verify-peffect features

**Baseline:** a checkout with `uv` installed; `uv run` builds `.venv` from `uv.lock`. Runs share
nothing except `.venv`, so concurrent runs are safe as long as nobody changes dependencies
meanwhile.

**Doctor and drive:** `run.sh` runs the doctor first, then each feature's command. To drive one
feature by hand, run its command from the repo root.

**Evidence and cleanup:** `run.sh` keeps one directory per run (see [SKILL.md](../SKILL.md),
"Evidence"). Nothing else needs cleaning up.

| feature | what the user sees | recipe |
|---|---|---|
| quickstart | typed failures caught by class, finalizers on failure, interrupt waiting for cleanup | [quickstart.md](quickstart.md) |
| boundary lint | code outside the kernel that reaches into kernel internals fails the lint, with file and line | [boundary-lint.md](boundary-lint.md) |

**Not covered here:** the fiber protocol under every interleaving (see
[verify-formal-kernel](../../verify-formal-kernel/SKILL.md)), and the type-level contract, which
`tests/test_types.py` pins on `typetests/` and the `pytest` step runs.
