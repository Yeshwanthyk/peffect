"""A bounded job pool: services, per-job errors, backpressure and owned workers.

Run: uv run python examples/queue_worker.py
No sleeps or guessed yields: readiness queues and Deferred gates expose the
backpressure/interruption states. Queue.await_ means drained; join workers to
also wait for processing already removed jobs. Interrupted processing is not
rolled back or automatically requeued.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Never

from peffect import (
    Deferred,
    Done,
    Effect,
    Failure,
    Fiber,
    Queue,
    Ref,
    Success,
    Tag,
    acquire_release,
    add_finalizer,
    fail,
    fork_scoped,
    gen,
    provide,
    run_exit,
    scoped,
    succeed,
    suspend,
)
from peffect.pull import catch_done


@dataclass(frozen=True)
class Job:
    id: int


@dataclass(frozen=True)
class Receipt:
    id: int


@dataclass(frozen=True)
class Rejected:
    id: int
    reason: str


@dataclass(frozen=True)
class ProducerFailed:
    reason: str


@dataclass(frozen=True)
class ProcessorService:
    process: Callable[[Job], Effect[Receipt, Rejected, Never]]


class Processor(Tag[ProcessorService], key="queue-example/Processor"): ...


type Outcome = Receipt | Rejected


@gen
def worker(
    name: str,
    inbox: Queue[Job, Done | ProducerFailed],
    outcomes: Ref[list[Outcome]],
    stopped: Ref[list[str]],
):
    processor = yield from Processor()

    def remember(outcome: Outcome) -> Effect[None, Never, Never]:
        return outcomes.update(lambda old: [*old, outcome])

    def process(job: Job) -> Effect[None, Never, Never]:
        return processor.process(job).catch_all(lambda error: succeed(error)).flat_map(remember)

    def loop() -> Effect[None, Done | ProducerFailed, Never]:
        return inbox.take().flat_map(process).flat_map(lambda _: suspend(loop))

    yield from catch_done(suspend(loop), lambda _: succeed(None)).ensuring(
        stopped.update(lambda old: [*old, name])
    )


@gen
def pool(service: ProcessorService, started: Queue[int], gate: Deferred[None, Never]):
    inbox = yield from Queue[Job, Done | ProducerFailed].bounded(2)
    outcomes = yield from Ref[list[Outcome]].make([])
    stopped = yield from Ref[list[str]].make([])
    producer_finished = yield from Ref[bool].make(False)
    # Registered before workers: on exceptional exit workers stop before Queue cleanup.
    yield from add_finalizer(lambda _: inbox.shutdown().as_(None))
    workers: list[Fiber[None, ProducerFailed]] = []
    for name in ["worker-a", "worker-b"]:
        fiber = yield from fork_scoped(
            provide(Processor.context(service))(worker(name, inbox, outcomes, stopped)),
            start_immediately=True,
        )
        workers.append(fiber)
    producer = yield from fork_scoped(
        inbox.into(inbox.offer_all([Job(i) for i in range(6)])).flat_map(
            lambda _: producer_finished.set(True)
        ),
        start_immediately=True,
    )
    assert sorted((yield from started.take_n(2))) == [0, 1]
    assert (yield from inbox.size()) == 2
    assert (yield from producer_finished.get()) is False  # real backpressure
    yield from gate.succeed(None)
    yield from producer.join()
    yield from inbox.await_()
    for fiber in workers:
        yield from fiber.join()  # drained does not imply in-flight processing finished
    result = yield from outcomes.get()
    assert sorted(item.id for item in result) == list(range(6))
    assert [item.id for item in result if isinstance(item, Rejected)] == [3]
    assert sorted((yield from stopped.get())) == ["worker-a", "worker-b"]
    return result


@gen
def interrupted_worker(service: ProcessorService, started: Queue[int]):
    inbox = yield from Queue[Job, Done | ProducerFailed].bounded(1)
    outcomes = yield from Ref[list[Outcome]].make([])
    stopped = yield from Ref[list[str]].make([])
    yield from add_finalizer(lambda _: inbox.shutdown().as_(None))
    fiber = yield from fork_scoped(
        provide(Processor.context(service))(worker("cancelled", inbox, outcomes, stopped)),
        start_immediately=True,
    )
    yield from inbox.offer(Job(7))
    assert (yield from started.take()) == 7
    yield from fiber.interrupt()
    result = yield from fiber.await_()
    assert isinstance(result, Failure) and result.cause.is_interrupted
    assert (yield from outcomes.get()) == []
    assert (yield from stopped.get()) == ["cancelled"]


@gen
def producer_failure(service: ProcessorService):
    inbox = yield from Queue[Job, Done | ProducerFailed].bounded(1)
    outcomes = yield from Ref[list[Outcome]].make([])
    stopped = yield from Ref[list[str]].make([])
    yield from add_finalizer(lambda _: inbox.shutdown().as_(None))
    fiber = yield from fork_scoped(
        provide(Processor.context(service))(worker("failed-input", inbox, outcomes, stopped)),
        start_immediately=True,
    )
    yield from inbox.offer(Job(8))
    assert (yield from inbox.into(fail(ProducerFailed("source disconnected")))) is True
    terminal = yield from fiber.await_()
    assert isinstance(terminal, Failure)
    assert terminal.cause.failures == [ProducerFailed("source disconnected")]
    assert (yield from outcomes.get()) == [Receipt(8)]


@gen
def program():
    lifecycle = yield from Ref[list[str]].make([])

    @gen
    def session():
        started = yield from Queue[int].unbounded()
        gate = yield from Deferred[None, Never].make()

        @gen
        def process(job: Job):
            yield from started.offer(job.id)
            yield from gate.await_()
            if job.id == 3:
                return (yield from fail(Rejected(job.id, "invalid payload")))
            return Receipt(job.id)

        service = yield from acquire_release(
            lifecycle.update(lambda old: [*old, "processor opened"]).as_(ProcessorService(process)),
            lambda _, _exit: lifecycle.update(lambda old: [*old, "processor closed"]),
        )
        result = yield from scoped(pool(service, started, gate))
        assert len(result) == 6
        # The previous gate stays open. Give the interruption scenario its own gate.
        pending = yield from Deferred[None, Never].make()

        def blocked(job: Job) -> Effect[Receipt, Rejected, Never]:
            return started.offer(job.id).flat_map(lambda _: pending.await_()).as_(Receipt(job.id))

        # Previous processing announcements are intentionally irrelevant to cancellation.
        yield from started.clear()
        yield from scoped(interrupted_worker(ProcessorService(blocked), started))
        yield from scoped(producer_failure(service))

    yield from scoped(session())
    assert (yield from lifecycle.get()) == ["processor opened", "processor closed"]
    return [
        "bounded pool: producer parked; six jobs drained; one typed rejection recorded",
        "interruption: processing stopped; worker finalized; Queue cleanup owned by scope",
        "producer failure: admitted job processed before typed source failure reached worker",
        "processor resource closed after worker scopes",
    ]


async def main() -> None:
    result = await run_exit(program())
    assert isinstance(result, Success), result
    for line in result.value:
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
