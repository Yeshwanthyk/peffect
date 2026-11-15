# Quickstart

A user program written only against `from peffect import ...` runs and ends the way the kernel
promises.

## Behaviors

- **Q1** A typed failure is caught by its class (`catch_tag`); the program continues with the fallback.
- **Q2** An uncaught typed failure ends the run as a `Failure` whose cause lists it.
- **Q3** A finalizer (`ensuring`) runs when the guarded program fails.
- **Q4** `fiber.interrupt()` on a forked fiber returns only after that fiber's finalizer has run,
  and the fiber ends interrupted.

## User entry points

- `uv run python examples/quickstart.py`

## Drive

From the repo root, with the doctor passing:

```sh
uv run python examples/quickstart.py
```

Expected output, exit 0:

```text
hello, ada
hello, stranger
uncaught: [NotFound(user_id=7)]
finalizer after failure: ['released']
interrupt waits for cleanup: ['worker cleaned', 'after interrupt']
quickstart ok
```

## Proof

`example.log` and `example.exit` in the run's evidence directory. The example asserts Q2–Q4
itself, so a wrong order or a missing cleanup fails with an `AssertionError` and a non-zero exit.

## Gotchas

- `run` raises on failure; Q2 uses `run_exit` to see the `Failure` instead.
