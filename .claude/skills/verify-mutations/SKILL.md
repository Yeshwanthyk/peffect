---
name: verify-mutations
description: Check that peffect's child-wait regression test detects a real kernel break. Run after changing child completion or its regression test; extend the mutation catalog when adding another critical behavior guarantee.
---

# verify-mutations

## Run

From the repository root:

```sh
uv run python scripts/check_mutations.py
```

The runner copies current source into a temporary directory, verifies the import location,
runs the clean regression test, then changes `_done` to publish without waiting for children.
It requires one assertion failure in the mutated run. A passing mutant, missing source anchor,
timeout, collection/import error, or failing baseline fails verification. Production files
are never mutated. Temporary source is removed; logs and JUnit reports remain in the printed
`/tmp/peffect-mutations-*` evidence directory (the actual base follows the OS temp directory).

## Extend

For a new critical guarantee, identify a concrete wrong transition and the test that detects
its observable harm. Add a separate isolated mutation and baseline to the runner. Require the
expected assertion failure, not merely a nonzero process exit. Keep mutations targeted:
removing a resume claim, skipping a finalizer, or allowing a second publication are candidates
until an actual test has killed each one. They are not covered by the current command.

## Report

Report the baseline result, each killed or surviving mutation, and the evidence path.
Fix missing behavior assertions for survivors; classify equivalent mutations explicitly.
The current catalog has one child-wait mutation. Passing it proves sensitivity to that break,
not a mutation score for the entire library or arbitrary concurrency correctness.

Model breaks are owned by [verify-formal-kernel](../verify-formal-kernel/SKILL.md).
Run [verify-peffect](../verify-peffect/SKILL.md) after changing tests or the runner.
