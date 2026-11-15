# Effect v4 Schedule and Clock notes

Scope: source under `packages/effect/src` in the checked-out Effect v4 tree. Citations use paths relative to that `src` directory. These are source findings; no vendor code was executed.

## Schedule representation

- `Schedule<Output, Input, Error, Env>` is a typed wrapper whose runtime `step` is an `Effect` that initializes a step closure. The closure takes `(now: number, input)` and returns a `Pull` yielding `[output, delay]`, failing with an error or finishing with an output. Thus each schedule instance can carry private mutable state in the closure; the public type tracks output, input, error, and environment. (`Schedule.ts:55-62,262-279`)
- The basic step is timestamp-in / decision-out, but v4's step is not generally a pure function: initialization and every step are effects, and `Pull` can fail or complete. Deterministic schedules can be effect-free after initialization; `jittered`, effectful predicates, or custom schedules need effects/services. (`Schedule.ts:262-279,319-339,358-389,1133-1140,1368-1400`)
- `fromStep` initializes user state once and closes over it. Metadata-aware constructors also close over counters/start/previous timestamps; metadata includes input, 1-based attempt, start, now, elapsed, and elapsed-since-previous. (`Schedule.ts:262-279,290-301,319-339`)

## Constructors and composition

- `recurs(n)` is `while_(forever, attempt <= n)`; since metadata attempts are 1-based and `forever` emits immediately, it permits exactly n schedule decisions. (`Schedule.ts:290-301,1211-1212`)
- `spaced(d)` emits attempt minus one and the same fixed delay each time; its delay is relative to the previous action completion because its driver sleeps after each action and before the next iteration. (`Schedule.ts:1241-1244`; `internal/schedule.ts:13-47`)
- `exponential(base, factor=2)` emits `baseMillis * factor^(attempt-1)` as both output and delay. (`Schedule.ts:885-893`)
- `fixed(interval)` tracks start and last-run time in closure state, aligning recurrence to interval boundaries. If action execution is behind by more than an interval it emits zero delay rather than replaying missed ticks; zero interval also yields zero delay. (`Schedule.ts:970-991`)
- `jittered(s)` is `modifyDelay` over `s`: it draws a random value and scales each delay within 0.8x–1.2x. This makes it effectful and adds the randomness service requirement. (`Schedule.ts:1082-1105,1133-1140`)
- `while(s, predicate)` runs `s` first, builds metadata from the current step, then keeps the decision if the possibly-effectful predicate is true; false completes with the schedule output. `until` is exported as the predicate-inverting counterpart. (`Schedule.ts:1368-1400,1402-1430`)
- `upTo(s, {times,duration})` specializes `while`: continue while 1-based attempt is within `times` and elapsed is within duration. (`Schedule.ts:1339-1365`)
- The prompt's `union`, `intersect`, and `andThen` names are not exported/defined in this v4 `Schedule.ts` source (searched the file); do not port assumed v3 composition semantics. The present public surface instead includes mechanisms such as `while`, `upTo`, `map`, `modifyDelay`, `concat`, and `max`. (`Schedule.ts:482-555,638-701,1025-1040,1082-1105,1339-1400`)

## Retry and repeat driver

- `repeatOrElse` obtains one metadata-aware schedule stepper, runs the effect initially, and feeds each successful effect result back as the next schedule input. The schedule step decides output/delay; its helper sleeps before yielding metadata, and the loop then reruns the effect with `CurrentMetadata` provided. (`internal/schedule.ts:13-47`; `Schedule.ts:392-425`)
- `retryOrElse` likewise initializes one stepper, but catches recoverable failures only. It feeds each error into the schedule; a schedule decision sleeps and loops to rerun the original effect. When the schedule completes, `Pull.catchDone` invokes `orElse(lastError, scheduleOutput)`. Non-recoverable causes pass through. (`internal/schedule.ts:49-80`; `Schedule.ts:392-425`)
- Retry/repeat expose the latest schedule metadata as `CurrentMetadata` while running the user effect. Repeat starts with default metadata; on each accepted schedule decision it updates metadata before the next execution. (`Schedule.ts:101-112`; `internal/schedule.ts:13-80`)
- `Effect.sleep` converts any `Duration.Input` to `Duration` then dispatches through the active `Clock`. Durations accept milliseconds as numbers, nanoseconds as bigint, or unit strings; internally `Duration` is a branded value with millis/nanos conversions. (`internal/effect.ts:6473-6477`; `Duration.ts:112-123,203-233,275-303,891-912`)

## Clock and TestClock

- `Clock` provides wall time in millis/nanos, monotonic nanos, effectful and unsafe accessors, and `sleep(Duration)`. Wall time may jump; monotonic time is for elapsed intervals. (`Clock.ts:53-153`)
- The `Clock` Context reference defaults to `new ClockImpl()`. The live clock reads `Date.now()` and a monotonic source; sleep yields for nonpositive durations, never completes for infinity, and chunks very long timers at the platform timeout limit. (`internal/effect.ts:6395-6397,6402-6430`; `Clock.ts:192`)
- `TestClock` extends `Clock`; `adjust` advances virtual time and runs due sleeps in order, while `setTime` moves to an absolute timestamp. (`TestClock.ts:102-115`)
- TestClock starts at virtual timestamp zero and stores sleepers in a timestamp-sorted list with latches; equal-deadline sleepers retain registration order. `sleep` registers a latch at current time + duration and blocks on it, cleaning up its entry on completion/interruption. (`TestClock.ts:287-302,329-357`)
- `adjust` serializes advancement, first gives pending fibers a scheduler turn, then pops all due sleepers earliest-first, advances to each deadline, opens its latch, and waits for woken work (including chained scheduler tasks) to suspend before checking the next deadline. It finally advances to the requested endpoint. This drain-to-quiescence behavior is the determinism guarantee to preserve in Python. (`TestClock.ts:359-365,453-490`; `TestClock.ts:301-306,371-390`)
- Tests can install it as the Clock service using `TestClock.layer`; documented examples fork a sleeping fiber, adjust time, then join/assert. (`TestClock.ts:42-46,81-92,571-582`)

## Python port implications and DESIGN.md comparison

- DESIGN.md's `(now, input, state) -> (state, delay | Done)` accurately captures the conceptual recurrence but differs from v4's actual closure-based state and effectful/Pull step protocol. Decide whether Python should deliberately keep the simpler pure core or support effectful schedules and schedule-level failures; the latter needs an async step contract plus explicit done/error outcomes. (`Schedule.ts:262-279,358-389,1368-1400`)
- Keep retry/repeat as explicit async loops: initialize schedule once, run effect, feed success/error according to operation, request next decision, await the injected Clock, then continue. Preserve retry's recoverable-error filtering and its terminal output/error callback. (`internal/schedule.ts:13-80`)
- For asyncio, a `Clock` Protocol can expose wall/monotonic reads and `async sleep(Duration)`. The test clock needs a monotonic virtual counter plus ordered sleepers (`deadline`, registration sequence, `Future`); advancing should resolve due futures by deadline/order and yield/drain tasks after each wake so a resumed task can register its next sleep before the advance finishes. This is a Python design inference from TestClock's source algorithm. (`TestClock.ts:287-357,359-390,467-490`)
- Use monotonic time for elapsed and fixed schedule alignment; retain wall time only for timestamp-facing APIs. Keep duration normalization at the boundary so number/string/bigint representations do not leak through driver logic. (`Clock.ts:53-153`; `Duration.ts:203-233,275-303,891-912`)
- Gap: this scout did not read the schedule-specific test file(s) or execute tests, per the read-only/no-build constraint. Source findings are line-cited; runtime behavior is inferred from implementation and API comments.
