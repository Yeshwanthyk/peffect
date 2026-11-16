"""Scenario fixtures for batch settlement; not part of its service implementation."""

from typing import Never

from examples.parity.python_app import record
from examples.parity.python_batch import (
    make_audited_batch_app,
    make_batch_app,
    make_ledger,
    settle,
)
from examples.parity.python_composed import Order, Payments, PaymentsLive, PaymentsService
from peffect import Deferred, Effect, Layer, fork, gen, never, provide_layer


@gen
def cancel_settlement():
    hanging = yield from Deferred[None, Never].make()

    def charge(order: Order) -> Effect[str, Never, Never]:
        if order.user_id == 2:
            return record("charge.hanging:2").tap(hanging.succeed(None)).flat_map(lambda _: never)
        return record(f"charge.ok:{order.user_id}").as_(f"charged:{order.user_id}")

    payments = Layer.succeed(Payments, PaymentsService(charge))
    fiber = yield from fork(provide_layer(make_batch_app(payments))(settle([Order(1), Order(2)])))
    yield from hanging.await_()
    yield from record("settle.cancel.requested")
    yield from fiber.interrupt()
    yield from record("settle.cancel.returned")
    return (yield from fiber.join())


BATCH_SCENARIOS: list[tuple[str, Effect[object, object, Never]]] = [
    (
        "batch_settle_mixed",
        provide_layer(make_batch_app())(
            settle([Order(1), Order(2, "book", "declined"), Order(99), Order(1, "sold-out")])
        ),
    ),
    (
        "batch_settle_audited_payments",
        provide_layer(make_audited_batch_app())(settle([Order(1), Order(2)])),
    ),
    (
        "batch_settle_custom_audit",
        provide_layer(make_audited_batch_app(make_ledger(capacity=1)))(
            settle([Order(1), Order(2)])
        ),
    ),
    ("batch_settle_cancellation", cancel_settlement()),
    (
        "batch_settle_ledger_full",
        provide_layer(make_batch_app(PaymentsLive, make_ledger(capacity=1)))(
            settle([Order(1), Order(2)])
        ),
    ),
]
