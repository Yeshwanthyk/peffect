"""Counterpart of batch.ts: settle a batch of orders over the checkout app into one Ledger."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Never, TypedDict

from examples.parity.python_app import UserNotFound, exit_kind, record
from examples.parity.python_composed import (
    Checkout,
    Order,
    OutOfStock,
    PaymentDeclined,
    Payments,
    PaymentsLive,
    PaymentsService,
    make_checkout_app,
    place_or_pending,
)
from peffect import (
    Effect,
    Layer,
    Tag,
    acquire_release,
    catch_tag,
    fail,
    for_each,
    gen,
    provide,
    succeed,
    suspend,
    sync,
    tap,
)


class Line(TypedDict):
    userId: int
    status: str
    detail: str


@dataclass(frozen=True)
class LedgerFull:
    capacity: int


@dataclass(frozen=True)
class LedgerService:
    append: Callable[[Line], Effect[None, LedgerFull, Never]]


@dataclass(frozen=True)
class BatchService:
    settle: Callable[[Sequence[Order]], Effect[list[Line], Never, Never]]


class Ledger(Tag[LedgerService], key="parity/Ledger"): ...


class Batch(Tag[BatchService], key="parity/Batch"): ...


def make_ledger(capacity: int) -> Layer[Ledger]:
    """A resource: opened when the layer is built, closed with its line count."""

    def over(lines: list[Line]) -> LedgerService:
        def append(line: Line) -> Effect[None, LedgerFull, Never]:
            def commit() -> Effect[None, LedgerFull, Never]:
                if len(lines) >= capacity:
                    return record(f"ledger.full:{line['userId']}").flat_map(
                        lambda _: fail(LedgerFull(capacity))
                    )
                lines.append(line)
                return record(f"ledger.append:{line['userId']}:{line['status']}")

            return suspend(commit)

        return LedgerService(append)

    return Layer.effect(
        Ledger,
        acquire_release(
            record("ledger.open").flat_map(lambda _: sync(lambda: list[Line]())),
            lambda lines, ex: record(f"ledger.close:{exit_kind(ex)}:{len(lines)}"),
        ).map(over),
    )


LedgerLive = make_ledger(capacity=100)


def line_for(order: Order, status: str, detail: str) -> Line:
    return {"userId": order.user_id, "status": status, "detail": detail}


def settle_one(order: Order) -> Effect[Line, Never, Checkout]:
    """Every domain error becomes a line: a declined payment is pending, the rest rejected."""
    return (
        place_or_pending(order)
        .map(lambda receipt: line_for(order, receipt["status"], receipt["payment"]))
        .pipe(
            catch_tag(
                UserNotFound,
                lambda e: succeed(line_for(order, "rejected", f"unknown-user:{e.user_id}")),
            ),
            catch_tag(
                OutOfStock,
                lambda e: succeed(line_for(order, "rejected", f"out-of-stock:{e.sku}")),
            ),
        )
    )


@gen
def make_batch():
    checkout = yield from Checkout()
    ledger = yield from Ledger()
    yield from record("batch.build")

    def settle_and_record(order: Order) -> Effect[Line, Never, Never]:
        # The tap keeps the settled line; when the Ledger refuses it, the order is unrecorded.
        return settle_one(order).pipe(
            provide(Checkout.context(checkout)),
            tap(ledger.append),
            catch_tag(
                LedgerFull,
                lambda e: succeed(line_for(order, "unrecorded", f"ledger-full:{e.capacity}")),
            ),
        )

    return BatchService(
        lambda orders: for_each(orders, lambda order, _: settle_and_record(order), concurrency=2)
    )


BatchLive = Layer.effect(Batch, make_batch())


@gen
def make_audited_payments():
    ledger = yield from Ledger()
    yield from record("payments.audited.build")

    def charge(order: Order) -> Effect[str, PaymentDeclined, Never]:
        # A charge that cannot be audited is declined.
        return (
            ledger.append(line_for(order, "charge", "sandbox"))
            .as_(f"sandbox:{order.user_id}")
            .pipe(catch_tag(LedgerFull, lambda _: fail(PaymentDeclined(order.user_id))))
        )

    return PaymentsService(charge)


# Keep the Ledger requirement open until the application chooses its resource.
AuditedPayments = Layer.effect(Payments, make_audited_payments())


def make_batch_app(
    payments: Layer[Payments] = PaymentsLive, ledger: Layer[Ledger] = LedgerLive
) -> Layer[Batch]:
    return BatchLive.pipe(Layer.provide(Layer.merge_all(make_checkout_app(payments), ledger)))


def settle(orders: Sequence[Order]) -> Effect[list[Line], Never, Batch]:
    return Batch().use().flat_map(lambda batch: batch.settle(orders))


def make_audited_batch_app(ledger: Layer[Ledger] = LedgerLive) -> Layer[Batch]:
    """Give payment auditing and settlement exactly the same Ledger layer identity."""
    payments = AuditedPayments.pipe(Layer.provide(ledger))
    return make_batch_app(payments, ledger)
