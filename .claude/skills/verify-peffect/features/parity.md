# Application parity

Run from the repository root:

```sh
uv run python -m examples.parity.compare
```

The command verifies the vendor commit and resolved Effect import, runs `reference.ts` on
actual Effect v4 and `python_reference.py` on peffect, then compares all 16 scenarios. It saves
both unmodified JSON outputs and `comparison.json` in the printed evidence directory. Pass
`--output-dir PATH` to choose a directory. A mismatch prints a unified diff and exits nonzero.

The first eight scenarios cover the request edge and service lifecycle. The next eight cover
a Checkout/Risk/Users dependency diamond, three domain errors, selective payment recovery,
reservation cleanup, service replacement, gated concurrent orders and cancellation of the batch.
Every outcome, count and ordered event is compared. Only validation-library error names and
diagnostic text are normalized; JSON types remain distinct.

Prerequisites: Bun and `bun install --frozen-lockfile` in `examples/parity` for the pinned
TypeScript compiler. The full verifier separately compiles the TypeScript application and
checks each negative fixture; its pytest/type steps check the Python fixtures. See the
application README for limits, including the narrower Python catch-tag type guarantee.

Sensitivity: `tests/test_application_parity.py` rejects altered values, counts, event order,
domain-error payloads, replacement events, scenario sets and JSON types. It also runs a real
Python application with its release action disabled in memory and rejects the result.
