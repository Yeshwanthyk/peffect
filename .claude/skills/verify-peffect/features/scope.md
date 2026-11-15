# Scope

A program registers finalizers on a Scope and they run when it closes: newest first, with the
Exit it closed with, and at once if the scope is already closed.

## Behaviors

- **S-a** `scoped(body)` runs the body, then its finalizers newest first.
- **S-b** Interrupting a fiber inside `scoped` runs its finalizer with an Exit holding the
  interrupt.
- **S-c** A finalizer added to a closed scope runs at once.

## User entry points

- `uv run python examples/scope.py`
- `uv run pytest -q tests/test_scope.py`

## Drive

```sh
uv run python examples/scope.py
```

Expected output, exit 0:

```text
scoped: work, then finalizers newest first
interrupted scoped: finalizer ran with the interrupt exit
closed scope: a late finalizer runs at once
scope ok
```

## Proof

`scope.log` and `scope.exit` in the run's evidence directory. The example asserts each line.

## Gotchas

- Sequential strategy only. Closing a `"parallel"` scope with several finalizers raises
  NotImplementedError until batch G ports upstream's forked close.
- That `scoped` restores the context before finalizers run, and that a direct close keeps every
  failure reason, are pinned by `tests/test_scope.py`, not this example.
