# Context

A program asks for services by tag, gets them from `provide`, and sees an override only inside
the `provide` that installed it.

## Behaviors

- **X1** `yield from Users()` returns the service a surrounding `provide(Users.context(repo))`
  installed.
- **X2** A `Reference` with no override returns its default; an override applies inside its
  `provide` only, and the default is back afterwards, also when the inside failed.
- **X3** A forked fiber sees the services of the fiber that forked it.
- **X4** Running a program whose R still names a tag is a pyright error, not a runtime surprise.

## User entry points

- `uv run python examples/context.py`
- `uv run pytest -q tests/test_context.py tests/test_types.py`

## Drive

```sh
uv run python examples/context.py
```

Expected output, exit 0:

```text
hello, ada
HEY, ada
no 7
hello, ada
hello, grace
context ok
```

## Proof

`context.log` and `context.exit` in the run's evidence directory (X1-X3; the example asserts its
lines, so a leaked override fails with an `AssertionError`). X4 is `pyright.log` plus the
`MISSING_AT_RUN` blocks of `typetests/should_fail.py`, which the `pytest` step checks.

## Gotchas

- Restoration after an interrupt, and an interrupt landing at the moment of install, are not
  visible from the example; `tests/test_context.py` C1-C2 cover them.
