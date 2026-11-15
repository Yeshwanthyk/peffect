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
| context | services found by tag, partial provide, a Reference default and override put back, a forked fiber seeing its parent's services | [context.md](context.md) |
| clock | wall time from the live Clock, a real timed sleep, an infinite sleep interrupted, a provided Clock replacing time for everything inside its `provide` | [clock.md](clock.md) |
| scope | `scoped` releases finalizers newest first after the work, an interrupted `scoped` releases with the interrupt exit, a finalizer added to a closed scope runs at once | [scope.md](scope.md) |
| boundary lint | code outside the kernel that reaches into kernel internals fails the lint, with file and line | [boundary-lint.md](boundary-lint.md) |

**Not covered here:** the fiber protocol under every interleaving (see
[verify-formal-kernel](../../verify-formal-kernel/SKILL.md)), and the type-level contract, which
`tests/test_types.py` pins on `typetests/` and the `pytest` step runs.
