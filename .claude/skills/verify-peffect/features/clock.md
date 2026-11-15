# Clock

A program reads time and sleeps through the fiber's `Clock`, and a provided Clock replaces the
live one for everything inside its `provide`.

## Behaviors

- **CL-a** With nothing provided, `current_time_millis` is the wall clock and `sleep(s)` waits on
  a real asyncio timer.
- **CL-b** `sleep(math.inf)` parks with no timer until the fiber is interrupted.
- **CL-c** Inside `provide(Clock.context(fake))`, `sleep` and the time reads go to `fake`: an hour
  passes on the fake clock with no real waiting.

## User entry points

- `uv run python examples/clock.py`
- `uv run pytest -q tests/test_clock.py tests/test_clock_sleep.py`

## Drive

```sh
uv run python examples/clock.py
```

Expected output, exit 0:

```text
live wall clock matches time.time()
sleep(0.05) waited at least 50ms
sleep(inf) parked until interrupted
fake clock: slept an hour in no time (3600000 ms)
clock ok
```

## Proof

`clock.log` and `clock.exit` in the run's evidence directory. The example asserts each line and
gives the whole program 2s of real time, so a `sleep` that bypasses the provided Clock (and waits
a real hour) fails with `TimeoutError`.

## Gotchas

- The example can't see timers. That a zero or negative sleep is a yield with no timer, and that
  interrupting a finite sleep cancels its timer, are pinned by `tests/test_clock_sleep.py`, which
  spies on the loop's `call_later`.
- TestClock (batch D) is not built; the fake clock here is the example's own.
