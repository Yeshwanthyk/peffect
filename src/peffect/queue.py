"""Queue at Effect v4 289a53f7: FIFO ownership, backpressure and completion.

Queue.ts:480-2175. Public module duals become methods; capability views are the
same object. No Ref or kernel protocol dependency. The creator dispatcher owns
coalesced taker releases. A wake means retry, never reservation. Destructive
reads use suspend_exit to deliver to the first accepting continuation atomically.

Numeric capacities are deliberately not normalized (including vendor quirks).
Counts are normalized. Metadata/Pipeable/Inspectable spellings are not ported.
Batch capability methods bind their receiver type independently: Python list is
invariant, but each result contains only that call's supplied/read messages.
This preserves the vendor's contravariant Enqueue and covariant Dequeue views.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from math import floor, inf, isnan
from typing import Literal, Never, TypeIs, final, overload

from .cause import Cause, Done
from .core import (
    Effect,
    Exit,
    Failure,
    Success,
    async_,
    exit_,
    fiber_id,
    succeed,
    suspend,
    suspend_exit,
    sync,
    uninterruptible_mask,
)
from .fiber import with_dispatcher
from .option import Some
from .pull import catch_done
from .scheduler import SchedulerDispatcher

type Strategy = Literal["suspend", "dropping", "sliding"]
type _Resume[A, E] = Callable[[Effect[A, E, Never]], None]


class Enqueue[A, E = Never](ABC):
    """Write capability, contravariant in both message and error (Queue.ts:175)."""

    @abstractmethod
    def offer(self, message: A) -> Effect[bool, Never, Never]: ...

    @abstractmethod
    def offer_unsafe(self, message: A) -> bool: ...

    @abstractmethod
    def offer_all[B](
        self: Enqueue[B, E], messages: Iterable[B]
    ) -> Effect[list[B], Never, Never]: ...

    @abstractmethod
    def offer_all_unsafe[B](self: Enqueue[B, E], messages: Iterable[B]) -> list[B]: ...

    @abstractmethod
    def fail_cause_unsafe(self, cause: Cause[E]) -> bool: ...

    def fail_cause(self, cause: Cause[E]) -> Effect[bool, Never, Never]:
        return sync(lambda: self.fail_cause_unsafe(cause))

    def fail(self, error: E) -> Effect[bool, Never, Never]:
        return self.fail_cause(Cause.fail(error))

    def end[B, F](self: Enqueue[B, F | Done]) -> Effect[bool, Never, Never]:
        return self.fail_cause(Cause.fail(Done()))

    def end_unsafe[B, F](self: Enqueue[B, F | Done]) -> bool:
        return self.fail_cause_unsafe(Cause.fail(Done()))

    def interrupt(self) -> Effect[bool, Never, Never]:
        return fiber_id.flat_map(lambda by: self.fail_cause(Cause.interrupt(by)))

    @abstractmethod
    def shutdown_unsafe(self) -> bool: ...

    def shutdown(self) -> Effect[bool, Never, Never]:
        return sync(self.shutdown_unsafe)

    @abstractmethod
    def flush_unsafe(self) -> None: ...

    def flush(self) -> Effect[None, Never, Never]:
        return sync(self.flush_unsafe)

    def into[B, F, X, R](
        self: Enqueue[B, F | Done], effect: Effect[X, F, R]
    ) -> Effect[bool, Never, R]:
        """1963-1991: publish the producer Exit, ignoring its success value."""

        def publish(result: Exit[X, F]) -> Effect[bool, Never, Never]:
            return self.fail_cause(result.cause) if isinstance(result, Failure) else self.end()

        return uninterruptible_mask(lambda restore: exit_(restore(effect)).flat_map(publish))


class Dequeue[A, E = Never](ABC):
    """Read capability, covariant in message and error (Queue.ts:245)."""

    @abstractmethod
    def take_unsafe(self) -> Exit[A, E] | None: ...

    @abstractmethod
    def _await_take(self, ready: Callable[[], bool]) -> Effect[None, E, Never]: ...

    @abstractmethod
    def _can_take(self, minimum: float) -> bool: ...

    def take(self) -> Effect[A, E, Never]:
        def attempt() -> Effect[A, E, Never]:
            result = self.take_unsafe()
            if result is not None:
                return result
            return self._await_take(lambda: self._can_take(1)).flat_map(lambda _: self.take())

        return suspend_exit(attempt)

    def poll(self) -> Effect[Some[A] | None, Never, Never]:
        def attempt() -> Effect[Some[A] | None, Never, Never]:
            result = self.take_unsafe()
            return Success(Some(result.value) if isinstance(result, Success) else None)

        return suspend_exit(attempt)

    @abstractmethod
    def peek(self) -> Effect[A, E, Never]: ...

    @abstractmethod
    def take_between[B](
        self: Dequeue[B, E], minimum: float, maximum: float
    ) -> Effect[list[B], E, Never]: ...

    def take_n[B](self: Dequeue[B, E], count: float) -> Effect[list[B], E, Never]:
        return self.take_between(count, count)

    def take_all[B](self: Dequeue[B, E]) -> Effect[list[B], E, Never]:
        return self.take_between(1, inf)

    @abstractmethod
    def _clear_raw[B](self: Dequeue[B, E]) -> Effect[list[B], E, Never]: ...

    @overload
    def clear[B](self: Dequeue[B, Done[object]]) -> Effect[list[B], Never, Never]: ...
    @overload
    def clear[B, F](self: Dequeue[B, Done[object] | F]) -> Effect[list[B], F, Never]: ...
    def clear[B, F](self: Dequeue[B, Done[object] | F]) -> Effect[list[B], F, Never]:
        return catch_done(self._clear_raw(), lambda _: succeed(list[B]()))

    @overload
    def collect[B](self: Dequeue[B, Done[object]]) -> Effect[list[B], Never, Never]: ...
    @overload
    def collect[B, F](self: Dequeue[B, Done[object] | F]) -> Effect[list[B], F, Never]: ...
    def collect[B, F](self: Dequeue[B, Done[object] | F]) -> Effect[list[B], F, Never]:
        def begin() -> Effect[list[B], F, Never]:
            messages: list[B] = []

            def append(batch: list[B]) -> Effect[list[B], Done[object] | F, Never]:
                messages.extend(batch)
                return suspend(loop)

            def loop() -> Effect[list[B], Done[object] | F, Never]:
                return self.take_all().flat_map(append)

            return catch_done(suspend(loop), lambda _: succeed(messages))

        return suspend(begin)

    @abstractmethod
    def _await_raw(self) -> Effect[None, E, Never]: ...

    @overload
    def await_[B](self: Dequeue[B, Done[object]]) -> Effect[None, Never, Never]: ...
    @overload
    def await_[B, F](self: Dequeue[B, Done[object] | F]) -> Effect[None, F, Never]: ...
    def await_[B, F](self: Dequeue[B, Done[object] | F]) -> Effect[None, F, Never]:
        return catch_done(self._await_raw(), lambda _: succeed(None))

    @abstractmethod
    def size_unsafe(self) -> int: ...

    def size(self) -> Effect[int, Never, Never]:
        return sync(self.size_unsafe)

    @abstractmethod
    def is_full_unsafe(self) -> bool: ...

    def is_full(self) -> Effect[bool, Never, Never]:
        return sync(self.is_full_unsafe)


class _Registry[T]:
    """Live ordered Set traversal, including append/delete during callbacks.

    Python dict iteration forbids mutation. Tokens keep cancellation ownership;
    tombstones remain during iteration, then compact with amortized bounded slack.
    No copied waiter snapshot can accidentally wake a cancelled waiter.
    """

    __slots__ = ("_iterators", "_next", "_order", "_values")

    def __init__(self) -> None:
        self._values: dict[int, T] = {}
        self._order: list[int] = []
        self._next = 0
        self._iterators = 0

    def __len__(self) -> int:
        return len(self._values)

    def add(self, value: T) -> int:
        token = self._next
        self._next += 1
        self._values[token] = value
        self._order.append(token)
        return token

    def remove(self, token: int) -> None:
        self._values.pop(token, None)
        self._compact()

    def clear(self) -> None:
        self._values.clear()
        self._compact()

    def _compact(self) -> None:
        if not self._iterators and len(self._order) > 2 * len(self._values):
            self._order = list(self._values)

    def __iter__(self) -> Iterator[tuple[int, T]]:
        self._iterators += 1
        try:
            index = 0
            while index < len(self._order):
                token = self._order[index]
                index += 1
                if token in self._values:
                    yield token, self._values[token]
        finally:
            self._iterators -= 1
            self._compact()


@dataclass(slots=True, eq=False)
class _Single[A]:
    message: A
    resume: _Resume[bool, Never]


@dataclass(slots=True, eq=False)
class _Batch[A]:
    remaining: list[A]
    resume: _Resume[list[A], Never]
    offset: int = 0


@dataclass(slots=True, eq=False)
class _Taker[E]:
    ready: Callable[[], bool]
    resume: _Resume[None, E]


@dataclass(slots=True, eq=False)
class _Active[A, E]:
    # None is Open; a stored terminal Failure is Closing. Done owns no registries.
    terminal: Failure[E] | None = None
    takers: _Registry[_Taker[E]] = field(default_factory=_Registry[_Taker[E]])
    offers: _Registry[_Single[A] | _Batch[A]] = field(
        default_factory=_Registry[_Single[A] | _Batch[A]]
    )
    awaiters: _Registry[_Resume[None, E]] = field(default_factory=_Registry[_Resume[None, E]])


@dataclass(frozen=True, slots=True)
class _Done[E]:
    terminal: Failure[E]


def _count(value: float) -> float:
    return 0 if isnan(value) or value <= 0 else inf if value == inf else floor(value)


@final
class Queue[A, E = Never](Enqueue[A, E], Dequeue[A, E]):
    """Invariant FIFO owner. Capacity is raw, counts are normalized upstream."""

    def __init__(
        self, capacity: float, strategy: Strategy, dispatcher: SchedulerDispatcher
    ) -> None:
        self.capacity = capacity
        self.strategy = strategy
        self._dispatcher = dispatcher
        self._messages: deque[A] = deque()
        self._state: _Active[A, E] | _Done[E] = _Active()
        self._scheduled = False

    @classmethod
    def make(
        cls, *, capacity: float = inf, strategy: Strategy = "suspend"
    ) -> Effect[Queue[A, E], Never, Never]:
        return with_dispatcher(lambda dispatcher: succeed(cls(capacity, strategy, dispatcher)))

    @classmethod
    def bounded(cls, capacity: float) -> Effect[Queue[A, E], Never, Never]:
        return cls.make(capacity=capacity)

    @classmethod
    def dropping(cls, capacity: float) -> Effect[Queue[A, E], Never, Never]:
        return cls.make(capacity=capacity, strategy="dropping")

    @classmethod
    def sliding(cls, capacity: float) -> Effect[Queue[A, E], Never, Never]:
        return cls.make(capacity=capacity, strategy="sliding")

    @classmethod
    def unbounded(cls) -> Effect[Queue[A, E], Never, Never]:
        return cls.make()

    def as_enqueue(self) -> Enqueue[A, E]:
        return self

    def as_dequeue(self) -> Dequeue[A, E]:
        return self

    def offer_unsafe(self, message: A) -> bool:
        state = self._state
        if isinstance(state, _Done) or state.terminal is not None:
            return False
        # Do not invert >= into <: NaN capacity follows the ordinary admission path.
        if not len(self._messages) >= self.capacity:
            self._messages.append(message)
            self._schedule_takers()
            return True
        if self.capacity <= 0 and len(state.takers) > (
            0 if self.strategy == "sliding" else len(self._messages)
        ):
            self._messages.append(message)
            if self.strategy == "sliding":
                self._trim(len(state.takers))
            self.flush_unsafe()
            return True
        if self.strategy != "sliding":
            return False
        if self.capacity > 0:
            self._messages.popleft()
            self._messages.append(message)
        return True

    def offer(self, message: A) -> Effect[bool, Never, Never]:
        def attempt() -> Effect[bool, Never, Never]:
            if self.offer_unsafe(message):
                return Success(True)
            state = self._state
            if isinstance(state, _Done) or state.terminal is not None or self.strategy != "suspend":
                return Success(False)

            def register(resume: _Resume[bool, Never]) -> Effect[None, Never, Never] | None:
                if self.offer_unsafe(message):
                    resume(Success(True))
                    return None
                return self._wait_offer(_Single(message, resume))

            return async_(register)

        return suspend(attempt)

    def _trim(self, capacity: float) -> None:
        # MutableList.takeNVoid normalizes the removal count (MutableList.ts:516).
        excess = _count(len(self._messages) - capacity)
        while excess > 0 and self._messages:
            self._messages.popleft()
            excess -= 1

    def offer_all_unsafe[B, F](self: Queue[B, F], messages: Iterable[B]) -> list[B]:
        state = self._state
        if isinstance(state, _Done) or state.terminal is not None:
            return list(messages)
        if self.capacity == inf or self.strategy == "sliding":
            self._messages.extend(messages)
            if self.strategy == "sliding":
                self._trim(len(state.takers) if self.capacity <= 0 else self.capacity)
            self._schedule_takers()
            return []
        free = (len(state.takers) if self.capacity <= 0 else self.capacity) - len(self._messages)
        if free <= 0:
            return list(messages)
        remaining: list[B] = []
        for index, message in enumerate(messages):
            if index < free:
                self._messages.append(message)
            else:
                remaining.append(message)
        self._schedule_takers()
        return remaining

    def offer_all[B, F](self: Queue[B, F], messages: Iterable[B]) -> Effect[list[B], Never, Never]:
        def register(resume: _Resume[list[B], Never]) -> Effect[None, Never, Never] | None:
            remaining = self.offer_all_unsafe(messages)
            if not remaining or self.strategy == "dropping":
                resume(Success(remaining))
                return None
            return self._wait_offer(_Batch(remaining, resume))

        return async_(register)

    def _wait_offer(self, entry: _Single[A] | _Batch[A]) -> Effect[None, Never, Never] | None:
        state = self._state
        if isinstance(state, _Done) or state.terminal is not None:
            self._reject(entry)
            return None
        token = state.offers.add(entry)

        def cancel() -> None:
            current = self._state
            if isinstance(current, _Done):
                return
            state.offers.remove(token)
            if current.terminal is not None and not state.offers and not self._messages:
                self._finalize(current.terminal)

        return sync(cancel)

    @staticmethod
    def _reject(entry: _Single[A] | _Batch[A]) -> None:
        if isinstance(entry, _Single):
            entry.resume(Success(False))
        else:
            entry.resume(Success(entry.remaining[entry.offset :]))

    def fail_cause_unsafe(self, cause: Cause[E]) -> bool:
        state = self._state
        if isinstance(state, _Done) or state.terminal is not None:
            return False
        terminal = Failure(cause)
        if not state.offers and not self._messages:
            self._finalize(terminal)
        else:
            state.terminal = terminal
            self._schedule_takers()
        return True

    def shutdown_unsafe(self) -> bool:
        state = self._state
        if isinstance(state, _Done):
            return False
        self._messages.clear()
        self._finalize(state.terminal if state.terminal is not None else Failure(Cause.interrupt()))
        for _, entry in state.offers:
            self._reject(entry)
        state.offers.clear()
        return True

    def _finalize(self, terminal: Failure[E]) -> None:
        state = self._state
        if isinstance(state, _Done):
            return
        self._state = _Done(terminal)
        for _, taker in state.takers:
            taker.resume(terminal)
        state.takers.clear()
        for _, resume in state.awaiters:
            resume(terminal)
        state.awaiters.clear()

    def _schedule_takers(self) -> None:
        state = self._state
        if self._scheduled or isinstance(state, _Done) or not state.takers:
            return
        self._scheduled = True

        def release() -> None:
            self._scheduled = False
            self.flush_unsafe()

        self._dispatcher.schedule_task(release, 0)

    def flush_unsafe(self) -> None:
        state = self._state
        if isinstance(state, _Done):
            return
        for token, taker in state.takers:
            if not taker.ready():
                continue
            state.takers.remove(token)
            taker.resume(Success(None))
            if not self._messages:
                break

    def _await_take(self, ready: Callable[[], bool]) -> Effect[None, E, Never]:
        def register(resume: _Resume[None, E]) -> Effect[None, Never, Never] | None:
            state = self._state
            if isinstance(state, _Done):
                resume(state.terminal)
                return None
            if ready():
                resume(Success(None))
                return None
            token = state.takers.add(_Taker(ready, resume))
            return sync(lambda: state.takers.remove(token))

        return async_(register)

    def _can_take(self, minimum: float) -> bool:
        state = self._state
        if isinstance(state, _Done):
            return False
        # JS capacity || 1 treats NaN as false as well as zero.
        capacity = self.capacity if self.capacity and not isnan(self.capacity) else 1
        threshold = 1 if state.terminal is not None else min(minimum, capacity)
        return len(self._messages) >= threshold or (self.capacity <= 0 and bool(state.offers))

    def _take_offer(self, offers: _Registry[_Single[A] | _Batch[A]]) -> A:
        token, entry = next(iter(offers))
        if isinstance(entry, _Single):
            message = entry.message
            offers.remove(token)
            entry.resume(Success(True))
            return message
        message = entry.remaining[entry.offset]
        entry.offset += 1
        if entry.offset == len(entry.remaining):
            offers.remove(token)
            entry.resume(Success([]))
        return message

    def _release_capacity(self) -> None:
        """Admit waiting offers into freed capacity, oldest first (releaseCapacity,
        Queue.ts:2109). Each offer is reserved in the buffer and removed before its producer
        resumes: resuming can synchronously take, offer or shut down this queue, so capacity
        and state are read again for every offer."""
        state = self._state
        if isinstance(state, _Done):
            return
        if not state.offers:
            if state.terminal is not None and not self._messages:
                self._finalize(state.terminal)
            return
        for token, entry in state.offers:
            free = self.capacity - len(self._messages)
            if free <= 0 or not self._admit(entry, free):
                return
            state.offers.remove(token)
            self._accept(entry)
            if isinstance(self._state, _Done):
                return

    def _admit(self, entry: _Single[A] | _Batch[A], free: float) -> bool:
        """Buffer the offer's messages while `free` lasts; True when none are left to send.
        A batch keeps its progress in `offset` and stays registered until then."""
        if isinstance(entry, _Single):
            self._messages.append(entry.message)
            return True
        while entry.offset < len(entry.remaining):
            if free == 0:  # upstream's `n === 0`: a fractional raw capacity never stops here
                return False
            self._messages.append(entry.remaining[entry.offset])
            free -= 1
            entry.offset += 1
        return True

    @staticmethod
    def _accept(entry: _Single[A] | _Batch[A]) -> None:
        if isinstance(entry, _Single):
            entry.resume(Success(True))
        else:
            entry.resume(Success([]))

    def take_unsafe(self) -> Exit[A, E] | None:
        state = self._state
        if isinstance(state, _Done):
            return state.terminal
        if self._messages:
            message = self._messages.popleft()
        elif self.capacity <= 0 and state.offers:
            message = self._take_offer(state.offers)
        else:
            return None
        self._release_capacity()
        return Success(message)

    def take_between(self, minimum: float, maximum: float) -> Effect[list[A], E, Never]:
        minimum, maximum = _count(minimum), _count(maximum)

        def attempt() -> Effect[list[A], E, Never]:
            state = self._state
            if isinstance(state, _Done):
                return state.terminal
            if maximum <= 0 or minimum <= 0:
                return Success([])
            if not self._can_take(minimum):
                return self._await_take(lambda: self._can_take(minimum)).flat_map(
                    lambda _: suspend_exit(attempt)
                )
            messages: list[A] = []
            if self._messages:
                while self._messages and len(messages) < maximum:
                    messages.append(self._messages.popleft())
            else:
                messages.append(self._take_offer(state.offers))
            self._release_capacity()
            return Success(messages)

        return suspend_exit(attempt)

    def _clear_raw(self) -> Effect[list[A], E, Never]:
        def attempt() -> Effect[list[A], E, Never]:
            state = self._state
            if isinstance(state, _Done):
                return state.terminal
            if self._messages:
                messages = list(self._messages)
                self._messages.clear()
                self._release_capacity()
            elif state.offers:
                messages = [self._take_offer(state.offers)]
                self._release_capacity()
            else:
                messages = []
            self._release_capacity()
            return Success(messages)

        return suspend_exit(attempt)

    def peek(self) -> Effect[A, E, Never]:
        def attempt() -> Effect[A, E, Never]:
            state = self._state
            if isinstance(state, _Done):
                return state.terminal
            if self._messages:
                return Success(self._messages[0])
            return self._await_take(lambda: bool(self._messages)).flat_map(lambda _: self.peek())

        return suspend(attempt)

    def _await_raw(self) -> Effect[None, E, Never]:
        def register(resume: _Resume[None, E]) -> Effect[None, Never, Never] | None:
            state = self._state
            if isinstance(state, _Done):
                resume(state.terminal)
                return None
            token = state.awaiters.add(resume)
            return sync(lambda: state.awaiters.remove(token))

        return async_(register)

    def size_unsafe(self) -> int:
        return 0 if isinstance(self._state, _Done) else len(self._messages)

    def is_full_unsafe(self) -> bool:
        return self.size_unsafe() == self.capacity


def is_queue(value: object) -> TypeIs[Queue[object, object]]:
    return isinstance(value, Queue)


def is_enqueue(value: object) -> TypeIs[Enqueue[object, object]]:
    return isinstance(value, Enqueue)


def is_dequeue(value: object) -> TypeIs[Dequeue[object, object]]:
    return isinstance(value, Dequeue)


__all__ = ["Dequeue", "Enqueue", "Queue", "is_dequeue", "is_enqueue", "is_queue"]
