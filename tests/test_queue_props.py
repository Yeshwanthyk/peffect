"""Independent Queue ownership ledgers, vendor pin 289a53f7.

Claim: unique offered IDs have exactly one owner after every controlled command;
reads preserve FIFO, cancellation withdraws only unadmitted suffixes, graceful
completion drains accepted/pending work, shutdown explicitly discards buffered
work and returns pending suffixes. Entry: real Queue methods/fibers, no prototype.
Domain: normal capacities 0,1,2,infinity; all strategies in the state machine;
concurrent suspended producers in the sequence property. Numeric pathologies
are explicit upstream characterizations, not silently normalized by this oracle.
Controls: run-to-park fibers and manually pumped creator dispatcher. These are
sequence/controlled parking properties; arbitrary operation interruption is
covered separately by test_queue_interleavings.py. No timing oracle.
Oracle: an ID ledger and intent-level FIFO list, never target internals. Per-ID
terminal owners distinguish rejection, sliding eviction, withdrawal, shutdown
and consumer acceptance; accounting alone cannot disguise loss as cancellation.
Budgets: 75 state-machine examples x 30 steps; 150 producer sequences x <=30 steps.
Mutation sensitivity against Queue is deliberately unclaimed at this RED stage.
"""

from dataclasses import dataclass, field
from math import inf
from typing import Any, Literal

from hypothesis import event, example, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
from test_queue_upstream import Harness, failed, interrupted, successful

from peffect import Cause, Done, Fiber
from peffect.option import Some


class StrategyLedger(RuleBasedStateMachine):
    @initialize(
        capacity=st.sampled_from([0, 1, 2, inf]),
        strategy=st.sampled_from(["suspend", "dropping", "sliding"]),
    )
    def setup_queue(
        self, capacity: float, strategy: Literal["suspend", "dropping", "sliding"]
    ) -> None:
        self.h = Harness()
        self.q = self.h.queue(capacity, strategy)
        self.capacity = capacity
        self.strategy = strategy
        self.ids = 0
        self.owners: dict[int, str] = {}
        self.fifo: list[int] = []
        self.phase = "open"
        self.terminal: Cause[Any] = Cause.fail(Done())

    def new_ids(self, count: int) -> list[int]:
        result = list(range(self.ids, self.ids + count))
        self.ids += count
        return result

    def consume(self, values: list[int]) -> None:
        for value in values:
            assert self.owners[value] == "buffer"
            self.owners[value] = "consumer"
        if not self.fifo and self.phase == "closing":
            self.phase = "done"

    @rule(count=st.integers(0, 5), batch=st.booleans())
    def offer(self, count: int, batch: bool) -> None:
        values = self.new_ids(count if batch else 1)
        event(f"strategy={self.strategy}, capacity={self.capacity}")
        if self.phase != "open":
            accepted: list[int] = []
        elif self.strategy == "sliding":
            accepted = values
        else:
            room = (
                len(values) if self.capacity == inf else max(0, int(self.capacity) - len(self.fifo))
            )
            accepted = values[:room]
        rejected = values[len(accepted) :]
        if batch:
            assert self.q.offer_all_unsafe(values) == rejected
        else:
            assert self.q.offer_unsafe(values[0]) is bool(accepted)
        for value in accepted:
            self.owners[value] = "buffer"
        for value in rejected:
            self.owners[value] = "rejected"
        self.fifo.extend(accepted)
        if self.strategy == "sliding" and self.capacity != inf:
            keep = int(self.capacity)
            evicted = self.fifo[: max(0, len(self.fifo) - keep)]
            self.fifo = self.fifo[len(evicted) :]
            for value in evicted:
                self.owners[value] = "slid"

    @rule()
    def poll(self) -> None:
        expected = self.fifo[:1]
        self.fifo = self.fifo[1:]
        assert self.h.value(self.q.poll()) == (Some(expected[0]) if expected else None)
        self.consume(expected)

    @rule()
    def clear(self) -> None:
        expected, self.fifo = self.fifo, []
        if self.phase == "done" and self.terminal != Cause.fail(Done()):
            failed(self.h.spawn(self.q.clear()), self.terminal)
        else:
            assert self.h.value(self.q.clear()) == expected
        self.consume(expected)

    @rule(failure=st.booleans())
    def complete(self, failure: bool) -> None:
        cause = Cause.fail("boom") if failure else Cause.fail(Done())
        assert self.q.fail_cause_unsafe(cause) is (self.phase == "open")
        if self.phase == "open":
            self.terminal = cause
            self.phase = "closing" if self.fifo else "done"

    @rule()
    def shutdown(self) -> None:
        expected = self.phase != "done"
        assert self.q.shutdown_unsafe() is expected
        if expected:
            for value in self.fifo:
                self.owners[value] = "shutdown"
            self.fifo.clear()
            if self.phase == "open":
                self.terminal = Cause.interrupt()
            self.phase = "done"

    @rule()
    def flush(self) -> None:
        self.q.flush_unsafe()
        self.h.pump()

    @invariant()
    def every_id_has_one_owner_and_public_size_agrees(self) -> None:
        assert set(self.owners) == set(range(self.ids))
        assert len(self.fifo) == len(set(self.fifo))
        assert set(self.fifo) == {i for i, owner in self.owners.items() if owner == "buffer"}
        assert self.q.size_unsafe() == len(self.fifo)
        assert self.h.value(self.q.size()) == len(self.fifo)
        assert self.q.is_full_unsafe() is (len(self.fifo) == self.capacity)
        assert self.h.value(self.q.is_full()) is (len(self.fifo) == self.capacity)

    def teardown(self) -> None:
        if hasattr(self, "q"):
            self.q.shutdown_unsafe()
            self.h.pump()


TestStrategyLedger = StrategyLedger.TestCase
TestStrategyLedger.settings = settings(max_examples=75, stateful_step_count=30, deadline=None)


@dataclass
class Offer:
    ids: list[int]
    batch: bool
    remaining: list[int]
    fiber: Fiber[Any, Any]
    cancelled: bool = False
    settled: bool = False
    result: Any = None


@dataclass
class OwnershipLedger:
    """Intent-level serial producer ownership, independent of Queue representation."""

    capacity: float
    owners: dict[int, str] = field(default_factory=dict[int, str])
    fifo: list[int] = field(default_factory=list[int])
    calls: list[Offer] = field(default_factory=list[Offer])
    pending: list[Offer] = field(default_factory=list[Offer])
    phase: str = "open"
    terminal: Cause[Any] = field(default_factory=lambda: Cause.fail(Done()))

    def settle(self, call: Offer, rejected: bool = False) -> None:
        call.settled = True
        call.result = list(call.remaining) if call.batch else not rejected
        if rejected:
            for value in call.remaining:
                self.owners[value] = "rejected"
        call.remaining.clear()

    def admit(self, call: Offer) -> None:
        free = (
            len(call.remaining)
            if self.capacity == inf
            else max(0, int(self.capacity) - len(self.fifo))
        )
        admitted = call.remaining[:free]
        del call.remaining[:free]
        self.fifo.extend(admitted)
        for value in admitted:
            self.owners[value] = "buffer"
        if not call.remaining:
            self.settle(call)

    def finish_if_drained(self) -> None:
        if self.phase == "closing" and not self.fifo and not self.pending:
            self.phase = "done"

    def refill(self) -> None:
        for call in list(self.pending):
            self.admit(call)
            if call.settled:
                self.pending.remove(call)
        self.finish_if_drained()

    def read(self, batch: bool) -> list[int]:
        if self.fifo:
            count = len(self.fifo) if batch else 1
            result = self.fifo[:count]
            del self.fifo[:count]
        elif self.capacity == 0 and self.pending:
            call = self.pending[0]
            result = [call.remaining.pop(0)]
            if not call.remaining:
                self.pending.remove(call)
                self.settle(call)
        else:
            result = []
        for value in result:
            self.owners[value] = "consumer"
        self.refill()
        return result

    def audit(self, h: Harness, q: Any) -> None:
        pending_ids = [i for call in self.pending for i in call.remaining]
        assert len(pending_ids) == len(set(pending_ids))
        assert set(pending_ids) == {i for i, owner in self.owners.items() if owner == "producer"}
        assert set(self.fifo) == {i for i, owner in self.owners.items() if owner == "buffer"}
        assert len(self.fifo) == len(set(self.fifo))
        assert q.size_unsafe() == len(self.fifo)
        for call in self.calls:
            if call.cancelled:
                interrupted(call.fiber)
            elif call.settled:
                successful(call.fiber, call.result)
            else:
                assert call.fiber.exit is None
        assert h.value(q.is_full()) is (len(self.fifo) == self.capacity)


commands = st.lists(
    st.tuples(
        st.sampled_from(
            ["single", "batch", "poll", "clear", "cancel", "end", "fail", "shutdown", "turn"]
        ),
        st.integers(0, 4),
    ),
    max_size=30,
)


@example(0, [("batch", 3), ("end", 0), ("poll", 0), ("cancel", 0), ("clear", 0)])
@example(1, [("batch", 4), ("single", 0), ("poll", 0), ("shutdown", 0)])
@example(2, [("batch", 4), ("single", 0), ("fail", 0), ("clear", 0), ("clear", 0), ("clear", 0)])
@settings(max_examples=150, deadline=None)
@given(st.sampled_from([0, 1, 2, inf]), commands)
def test_suspended_producer_ownership_survives_generated_drain_cancel_and_completion(
    capacity: float, sequence: list[tuple[str, int]]
):
    h = Harness()
    q = h.queue(capacity)
    model = OwnershipLedger(capacity)
    next_id = 0
    for command, parameter in sequence:
        event(f"operation={command}")
        if command in {"single", "batch"}:
            batch = command == "batch"
            ids = list(range(next_id, next_id + (parameter if batch else 1)))
            next_id += len(ids)
            model.owners.update(dict.fromkeys(ids, "producer"))
            call = Offer(
                ids, batch, list(ids), h.spawn(q.offer_all(ids) if batch else q.offer(ids[0]))
            )
            model.calls.append(call)
            if model.phase != "open":
                model.settle(call, rejected=True)
            else:
                model.admit(call)
                if not call.settled:
                    model.pending.append(call)
        elif command in {"poll", "clear"}:
            was_done = model.phase == "done"
            expected = model.read(command == "clear")
            fiber = h.spawn(q.clear() if command == "clear" else q.poll())
            if command == "clear" and was_done and model.terminal != Cause.fail(Done()):
                if model.terminal.is_interrupted:
                    interrupted(fiber)
                else:
                    failed(fiber, model.terminal)
            else:
                successful(
                    fiber,
                    expected if command == "clear" else (Some(expected[0]) if expected else None),
                )
        elif command == "cancel":
            if model.calls:
                call = model.calls[parameter % len(model.calls)]
                call.fiber.interrupt_unsafe(991)
                if not call.settled and not call.cancelled:
                    call.cancelled = True
                    model.pending.remove(call)
                    for value in call.remaining:
                        model.owners[value] = "withdrawn"
                    call.remaining.clear()
                    model.finish_if_drained()
        elif command in {"end", "fail"}:
            cause = Cause.fail(Done()) if command == "end" else Cause.fail("boom")
            assert q.fail_cause_unsafe(cause) is (model.phase == "open")
            if model.phase == "open":
                model.terminal = cause
                model.phase = "closing"
                model.finish_if_drained()
        elif command == "shutdown":
            assert q.shutdown_unsafe() is (model.phase != "done")
            if model.phase != "done":
                for value in model.fifo:
                    model.owners[value] = "shutdown"
                model.fifo.clear()
                for call in model.pending:
                    model.settle(call, rejected=True)
                model.pending.clear()
                if model.phase == "open":
                    model.terminal = Cause.interrupt()
                model.phase = "done"
        else:
            assert command == "turn"
            h.pump()
        assert set(model.owners) == set(range(next_id))
        model.audit(h, q)

    # Accepted and still-owned pending IDs must all drain without loss/duplication,
    # including Closing after cancellation. Oracle is the ledger's outstanding IDs.
    expected = model.fifo + [i for call in model.pending for i in call.remaining]
    actual: list[int] = []
    for _ in range(len(expected) + 1):
        result = h.value(q.poll())
        if result is None:
            break
        actual.append(result.value)
    assert actual == expected
    assert len(actual) == len(set(actual))
    for call in model.calls:
        if not call.cancelled and not call.settled:
            successful(call.fiber, [] if call.batch else True)
    q.shutdown_unsafe()
    h.pump()


@settings(max_examples=100, deadline=None)
@given(
    st.sampled_from([0, 1, 2, inf]),
    st.sampled_from(["suspend", "dropping", "sliding"]),
    st.lists(
        st.tuples(st.sampled_from(["reader", "cancel", "single", "batch"]), st.integers(0, 4)),
        max_size=25,
    ),
)
def test_cancelled_reader_ownership_never_steals_generated_offers(
    capacity: float,
    strategy: Literal["suspend", "dropping", "sliding"],
    sequence: list[tuple[str, int]],
):
    """Parked reader FIFO + explicit dispatcher settle after each command.

    Unlike the producer property, consumers are concurrent here. Offers are the
    nonblocking variants, so every ID is accepted, rejected or slid immediately.
    Each successful consumer is one terminal ledger owner, including readers
    completed before the driver attempts cancellation.
    """
    h = Harness()
    q = h.queue(capacity, strategy)
    fifo: list[int] = []
    readers: list[Fiber[Any, Any]] = []
    waiting: list[int] = []
    expected: dict[int, int] = {}
    cancelled: set[int] = set()
    owners: dict[int, str] = {}
    next_id = 0
    for command, parameter in sequence:
        if command == "reader":
            reader_id = len(readers)
            readers.append(h.spawn(q.take()))
            if fifo:
                value = fifo.pop(0)
                expected[reader_id] = value
                owners[value] = f"reader:{reader_id}"
            else:
                waiting.append(reader_id)
        elif command == "cancel":
            if readers:
                reader_id = parameter % len(readers)
                readers[reader_id].interrupt_unsafe(991)
                if reader_id in waiting:
                    waiting.remove(reader_id)
                    cancelled.add(reader_id)
        else:
            batch = command == "batch"
            ids = list(range(next_id, next_id + (parameter if batch else 1)))
            next_id += len(ids)
            if strategy == "sliding":
                admitted = ids
            else:
                slots = len(waiting) if capacity == 0 else capacity
                room = len(ids) if slots == inf else max(0, int(slots) - len(fifo))
                admitted = ids[:room]
            rejected = ids[len(admitted) :]
            if batch:
                assert q.offer_all_unsafe(ids) == rejected
            else:
                assert q.offer_unsafe(ids[0]) is bool(admitted)
            owners.update(dict.fromkeys(admitted, "buffer"))
            owners.update(dict.fromkeys(rejected, "rejected"))
            fifo.extend(admitted)
            if strategy == "sliding" and capacity != inf:
                keep = len(waiting) if capacity == 0 else int(capacity)
                excess = max(0, len(fifo) - keep)
                for value in fifo[:excess]:
                    owners[value] = "slid"
                del fifo[:excess]
            while waiting and fifo:
                reader_id = waiting.pop(0)
                value = fifo.pop(0)
                expected[reader_id] = value
                owners[value] = f"reader:{reader_id}"
        h.pump()
        assert set(owners) == set(range(next_id))
        assert q.size_unsafe() == len(fifo)
        for reader_id, reader in enumerate(readers):
            if reader_id in cancelled:
                interrupted(reader)
            elif reader_id in expected:
                successful(reader, expected[reader_id])
            else:
                assert reader.exit is None
        assert len(set(expected.values())) == len(expected)
        assert set(fifo) == {i for i, owner in owners.items() if owner == "buffer"}
    assert h.value(q.clear()) == fifo
    q.shutdown_unsafe()
    for reader_id in waiting:
        interrupted(readers[reader_id])
    h.pump()
