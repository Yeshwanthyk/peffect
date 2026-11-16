"""Fresh, bounded differential probes at Effect pin 289a53f7 (never cached goldens).

The existing application harness verifies vendor pin, clean source and import resolution.
These probes add primitive contracts. Fiber IDs are deliberately not compared: identities
come from independent process counters. We compare reason kinds/payloads and event order.
Python structural containers/custom equality are a separate adaptation test, NOT JS parity.
"""

import json
import math
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Never

import pytest

from peffect import (
    Cause,
    Die,
    Done,
    Effect,
    Interrupt,
    Queue,
    Success,
    Tag,
    async_,
    fail,
    fail_cause,
    for_each,
    fork,
    gen,
    provide,
    run,
    run_exit,
    run_fork,
    succeed,
    sync,
)
from peffect import duration as duration
from peffect.scheduler import Scheduler
from peffect.semaphore import Semaphore

ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "examples/parity"
PIN = "289a53f7b1fc40049219c6e69022db2fb859c356"


@pytest.fixture(scope="module", autouse=True)
def reference_identity():
    # Same identity checks as examples/parity/compare.py, without rerunning applications
    # for each individual case. Subprocess failures/timeouts are failures, never skips.
    def command(args: list[str], cwd: Path) -> str:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    assert command(["git", "rev-parse", "HEAD"], ROOT / "vendor/effect-v4") == PIN
    command(
        ["git", "diff", "--exit-code", "HEAD", "--", "packages/effect/src"],
        ROOT / "vendor/effect-v4",
    )
    resolved = command(["bun", "-e", 'console.log(import.meta.resolve("effect"))'], PARITY)
    assert Path(resolved.removeprefix("file://")).resolve() == (
        ROOT / "vendor/effect-v4/packages/effect/src/index.ts"
    )


def paired(unit: str, python: object, source: str) -> None:
    result = subprocess.run(
        ["bun", "-e", source], cwd=PARITY, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    reference = json.loads(result.stdout)
    # JSON encoding also distinguishes bool from int. No broad normalization.
    evidence = {"unit": unit, "pin": PIN, "EffectJS": reference, "Python": python}
    print(json.dumps(evidence, sort_keys=True))
    assert json.dumps(python, sort_keys=True) == json.dumps(reference, sort_keys=True), evidence


class Value(Tag[object], key="Cleanup/Value"): ...


def test_context_shared_numeric_contract():
    cases = [
        (1, 1.0),
        (1, 2),
        (True, 1),
        (False, 0),
        (float("nan"), float("nan")),
        (float("nan"), 1),
        (True, True),
        (float("inf"), float("inf")),
        (-0.0, 0),
    ]
    paired(
        "context",
        [Value.context(a) == Value.context(b) for a, b in cases],
        """
import { Context, Equal } from "effect"
const V = Context.Service("Cleanup/Value")
const cases = [[1,1],[1,2],[true,1],[false,0],[NaN,NaN],[NaN,1],
               [true,true],[Infinity,Infinity],[-0,0]]
console.log(JSON.stringify(cases.map(([a,b]) =>
  Equal.equals(Context.make(V,a), Context.make(V,b)))))
""",
    )


def test_context_python_adaptation_and_custom_equality():
    calls: list[str] = []

    class Custom:
        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            return isinstance(other, Custom)

        def __ne__(self, other: object) -> bool:
            raise AssertionError("numeric NaN path must never call custom __ne__")

    assert Value.context(Custom()) == Value.context(Custom())
    assert calls == ["eq"]
    left: list[object] = [float("nan")]
    right: list[object] = [float("nan")]
    left.append(left)
    right.append(right)
    assert Value.context(left) == Value.context(right)
    assert Value.context([1]) != Value.context((1,))
    assert Value.context({"a": [1, float("nan")]}) == Value.context({"a": [1, float("nan")]})
    assert Value.context(True) != Value.context(1)


def test_numeric_guard_excludes_bool_and_float_subclasses():
    values = [False, True, 0, 1, 0.0, 1.0, -0.0, float("nan"), float("inf")]
    for a in values:
        for b in values:
            if type(a) in (int, float) and type(b) in (int, float):
                expected = a == b or (math.isnan(a) and math.isnan(b))
            else:
                expected = type(a) is type(b) and a == b
            assert (Value.context(a) == Value.context(b)) is expected

    class NumericService(float):
        def __eq__(self, other: object) -> bool:
            return False

        def __ne__(self, other: object) -> bool:
            raise AssertionError("a float subclass is a service, not an exact builtin number")

    assert Value.context(NumericService(1)) != Value.context(NumericService(1))


def test_duration_parsing_contract():
    inputs: list[Any] = [
        "1 nano",
        "-0.5 nanos",
        "1.5 micros",
        "-1.0005 micros",
        "0.0000000005 seconds",
        "9007199254740993 nanos",
        "1 millis",
        "-2 seconds",
        "Infinity",
        "-Infinity",
        "nope",
        "1\u00a0second",
        "1\u0085second",
        True,
        None,
    ]
    actual: list[object] = []
    for item in inputs:
        try:
            value = duration.from_input_unsafe(item)  # runtime invalid-input boundary
            actual.append(
                str(duration.to_nanos_unsafe(value))
                if duration.is_finite(value)
                else "Infinity"
                if duration.to_millis(value) > 0
                else "-Infinity"
            )
        except ValueError as error:
            actual.append(str(error))
    paired(
        "duration",
        actual,
        """
import { Duration } from "effect"
const inputs = ["1 nano","-0.5 nanos","1.5 micros","-1.0005 micros","0.0000000005 seconds",
"9007199254740993 nanos","1 millis","-2 seconds","Infinity","-Infinity","nope",
"1\\u00a0second","1\\u0085second",true,null]
console.log(JSON.stringify(inputs.map(x => {
try { const d = Duration.fromInputUnsafe(x); return Duration.isFinite(d)
? String(Duration.toNanosUnsafe(d)) : Duration.toMillis(d) > 0 ? "Infinity" : "-Infinity" }
catch(e) { return e.message }
})))
""",
    )


async def test_queue_raw_capacity_contract():
    actual: list[object] = []
    for strategy in ("suspend", "dropping", "sliding"):
        for capacity in (0.0, -1.0, 0.5, 1.5, float("nan"), float("inf")):
            q = await run(Queue[int, str | Done].make(capacity=capacity, strategy=strategy))
            events: list[int] = []

            def items(events: list[int] = events):
                for i in [2, 3, 4]:
                    events.append(i)
                    yield i

            first = q.offer_unsafe(1)
            remaining = q.offer_all_unsafe(items())
            size = q.size_unsafe()
            taken = await run_exit(q.take_all()) if size else Success([])
            assert isinstance(taken, Success)
            values = taken.value
            q.end_unsafe()
            rejected = q.offer_unsafe(9)
            actual.append([first, remaining, size, values, events, rejected])
    paired(
        "queue",
        actual,
        """
import { Effect, Queue } from "effect"
const results = []
for (const strategy of ["suspend","dropping","sliding"]) {
for (const capacity of [0,-1,0.5,1.5,NaN,Infinity]) {
const q = Effect.runSync(Queue.make({capacity,strategy}))
const events = []
function* items() { for (const i of [2,3,4]) { events.push(i); yield i } }
const first = Queue.offerUnsafe(q,1)
const remaining = Queue.offerAllUnsafe(q,items())
const size = Queue.sizeUnsafe(q)
const values = size ? Effect.runSync(Queue.takeAll(q)) : []
Queue.endUnsafe(q)
results.push([first,remaining,size,values,events,Queue.offerUnsafe(q,9)])
}}
console.log(JSON.stringify(results))
""",
    )


@pytest.mark.parametrize("mode", ["success", "cancel", "failure"])
async def test_traversal_events_contract(mode: str):
    events: list[str] = []
    resumes: dict[int, Callable[[Effect[int, str, Never]], None]] = {}

    def item(i: int, _: int) -> Effect[int, str, Never]:
        events.append(f"map:{i}")
        if i == 0:
            return succeed(0)

        def register(resume: Callable[[Effect[int, str, Never]], None]):
            events.append(f"start:{i}")
            resumes[i] = resume
            return sync(lambda: events.append(f"cleanup:{i}")).flat_map(
                lambda _: fail_cause(Cause[Never]((Die(f"cleanup:{i}"),)))
            )

        return async_(register)

    fiber = run_fork(for_each([0, 1, 2, 3], item, concurrency=2))
    fiber.add_observer(lambda _: events.append("settled"))
    events.append("registered")
    if mode == "success":
        resumes[2](succeed(20))
        resumes[1](succeed(10))
        resumes[3](succeed(30))
    elif mode == "failure":
        resumes[1](fail("boom"))
    else:
        fiber.interrupt_unsafe()
    events.append("returned")
    ex = fiber.exit
    assert ex is not None
    outcome = (
        ex.value
        if isinstance(ex, Success)
        else [
            "Interrupt"
            if isinstance(r, Interrupt)
            else f"Die:{r.defect}"
            if isinstance(r, Die)
            else f"Fail:{r.error}"
            for r in ex.cause.reasons
        ]
    )
    paired(
        "traversal:" + mode,
        [events, outcome],
        """
import { Effect } from "effect"
const events = [], resumes = new Map()
const fiber = Effect.runFork(Effect.forEach([0,1,2,3], i => {
events.push(`map:${i}`)
if(i===0) return Effect.succeed(0)
return Effect.callback(resume => {
events.push(`start:${i}`); resumes.set(i,resume)
return Effect.flatMap(Effect.sync(() => events.push(`cleanup:${i}`)),
  () => Effect.die(`cleanup:${i}`))
})
}, {concurrency:2}))
fiber.addObserver(() => events.push("settled"))
events.push("registered")
const mode = MODE
if(mode === "success") {
resumes.get(2)(Effect.succeed(20)); resumes.get(1)(Effect.succeed(10))
resumes.get(3)(Effect.succeed(30)) }
else if(mode === "failure") resumes.get(1)(Effect.fail("boom"))
else fiber.interruptUnsafe()
events.push("returned")
const ex = fiber.pollUnsafe()
if(!ex) throw new Error("fiber still pending")
const outcome = ex._tag === "Success" ? ex.value : ex.cause.reasons.map(r =>
  r._tag === "Interrupt" ? "Interrupt" : `${r._tag}:${r._tag === "Die" ? r.defect : r.error}`)
console.log(JSON.stringify([events,outcome]))
""".replace("MODE", json.dumps(mode)),
    )


@pytest.mark.parametrize("cancel_woken", [False, True])
async def test_semaphore_overtaking_and_cancel_contract(cancel_woken: bool):
    tasks: list[Callable[[], None]] = []
    events: list[str] = []

    class Manual:
        execution_mode: Literal["async"] = "async"
        pause = False

        def should_yield(self, fiber: Any) -> bool:
            pause, self.pause = self.pause, False
            return pause

        def make_dispatcher(self):
            return self

        def schedule_task(self, task: Callable[[], None], priority: int) -> None:
            tasks.append(task)

        def flush(self) -> None:
            pass

    scheduler = Manual()
    sem = Semaphore.make_unsafe(0)
    large = run_fork(
        provide(Scheduler.context(scheduler))(
            sem.take(2).flat_map(lambda _: sync(lambda: events.append("large")))
        )
    )
    small = run_fork(
        provide(Scheduler.context(scheduler))(
            sem.take(1).flat_map(lambda _: sync(lambda: events.append("small")))
        )
    )
    await run(provide(Scheduler.context(scheduler))(sem.release(1)))
    events.append(f"released:{sem.free}")
    if cancel_woken:
        scheduler.pause = True
        tasks.pop(0)()  # wake, but yield before the request can recheck eligibility
        stolen = await run(sem.take_if_available(1))
        events.append(f"stolen:{str(stolen).lower()}:{sem.free}")
        small.interrupt_unsafe()
    while tasks:
        tasks.pop(0)()
    large.interrupt_unsafe()
    while tasks:
        tasks.pop(0)()
    events.append(f"cancelled:{sem.free}")
    assert small.exit is not None and large.exit is not None
    paired(
        "semaphore",
        events,
        """
import { Effect, Scheduler, Semaphore } from "effect"
const tasks = [], events = []
let pause = false
const scheduler = {executionMode:"async", shouldYield:()=>{
const result = pause; pause = false; return result },
makeDispatcher:()=>({scheduleTask:t=>tasks.push(t),flush:()=>{}})}
const sem = Semaphore.makeUnsafe(0)
const on = eff => Effect.provideService(eff,Scheduler.Scheduler,scheduler)
const large = Effect.runFork(on(Effect.flatMap(sem.take(2),
  ()=>Effect.sync(()=>events.push("large")))))
const small = Effect.runFork(on(Effect.flatMap(sem.take(1),
  ()=>Effect.sync(()=>events.push("small")))))
Effect.runSync(on(sem.release(1)))
events.push(`released:${sem.free}`)
if(CANCEL_WOKEN) {
pause = true; tasks.shift()()
const stolen = Effect.runSync(sem.takeIfAvailable(1))
events.push(`stolen:${stolen}:${sem.free}`)
small.interruptUnsafe()
}
while(tasks.length) tasks.shift()()
large.interruptUnsafe()
while(tasks.length) tasks.shift()()
events.push(`cancelled:${sem.free}`)
if(!large.pollUnsafe() || !small.pollUnsafe()) throw new Error("pending waiter")
console.log(JSON.stringify(events))
""".replace("CANCEL_WOKEN", str(cancel_woken).lower()),
    )


def test_pipe_left_to_right_contract():
    events: list[int] = []

    def step(value: int) -> int:
        events.append(value)
        return value + 1

    # Both pipe implementations operate on arbitrary intermediate values at runtime.
    value = succeed(1).pipe(lambda _: 0, *([step] * 19))
    paired(
        "pipe",
        [value, events],
        """
import { Effect } from "effect"
const events = [], step = x => { events.push(x); return x+1 }
const value = Effect.succeed(1).pipe(()=>0,...Array(19).fill(step))
console.log(JSON.stringify([value,events]))
""",
    )


async def test_queue_worker_backpressure_and_drain_contract():
    events: list[str] = []

    @gen
    def program():
        q = yield from Queue[int, str | Done].bounded(1)
        worker = yield from fork(
            sync(lambda: events.append("worker.start"))
            .flat_map(lambda _: q.collect())
            .tap(lambda _: sync(lambda: events.append("worker.done"))),
            start_immediately=True,
        )
        for i in [1, 2, 3]:
            yield from q.offer(i)
            events.append(f"offered:{i}")
        q.end_unsafe()
        events.append("ended")
        values = yield from worker.join()
        events.append("joined")
        return values

    ex = await run_exit(program())
    assert isinstance(ex, Success)
    paired(
        "queue-worker",
        [events, ex.value],
        """
import { Effect, Fiber, Queue } from "effect"
const events = []
const values = await Effect.runPromise(Effect.gen(function*() {
const q = yield* Queue.bounded(1)
const worker = yield* Effect.forkChild(
Effect.flatMap(Effect.sync(()=>events.push("worker.start")), ()=>Queue.collect(q)).pipe(
Effect.tap(()=>Effect.sync(()=>events.push("worker.done")))), {startImmediately:true})
for(const i of [1,2,3]) { yield* Queue.offer(q,i); events.push(`offered:${i}`) }
Queue.endUnsafe(q); events.push("ended")
const values = yield* Fiber.join(worker)
events.push("joined"); return values
}))
console.log(JSON.stringify([events,values]))
""",
    )


async def test_queue_batch_offset_is_published_before_producer_resume():
    """White-box publication witness on both real runtimes, not a new public API promise."""
    q = await run(Queue[int, Never].bounded(0))
    observed: list[int] = []
    entry: Any
    producer = run_fork(
        q.offer_all([1, 2]).flat_map(lambda _: sync(lambda: observed.append(entry.offset)))
    )
    # Inspect the actual entry's publication, like Queue.state.offers in the JS witness.
    entry = next(iter(vars(q)["_state"].offers))[1]
    assert hasattr(entry, "offset")
    first = q.take_unsafe()
    second = q.take_unsafe()
    assert isinstance(first, Success) and isinstance(second, Success)
    assert isinstance(producer.exit, Success)
    paired(
        "queue-offset",
        [first.value, second.value, observed, q.size_unsafe()],
        """
import { Effect, Queue } from "effect"
const q = Effect.runSync(Queue.bounded(0)), observed = []
let entry
const producer = Effect.runFork(Effect.flatMap(Queue.offerAll(q,[1,2]),
()=>Effect.sync(()=>observed.push(entry.offset))))
entry = Array.from(q.state.offers)[0]
const first = Queue.takeUnsafe(q), second = Queue.takeUnsafe(q)
if(first?._tag !== "Success" || second?._tag !== "Success" || !producer.pollUnsafe())
throw new Error("handoff did not complete")
console.log(JSON.stringify([first.value,second.value,observed,Queue.sizeUnsafe(q)]))
""",
    )
