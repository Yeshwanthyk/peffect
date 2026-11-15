"""Scenario fixtures for the checkout application; not part of its service implementation."""

from typing import Never

from examples.parity.python_app import record
from examples.parity.python_composed import (
    CheckoutError,
    Order,
    Payments,
    PaymentsService,
    Receipt,
    make_checkout_app,
    place_or_pending,
    place_order,
)
from peffect import Deferred, Effect, Fiber, Layer, for_each, fork, gen, never, provide_layer

BATCH = for_each([Order(1), Order(2)], lambda order, _: place_order(order), concurrency=2)


@gen
def parallel_batch():
    both_started = yield from Deferred[None, Never].make()
    release = yield from Deferred[None, Never].make()
    started = 0

    @gen
    def gated_charge(order: Order):
        nonlocal started
        yield from record(f"batch.started:{order.user_id}")
        started += 1
        if started == 2:
            yield from both_started.succeed(None)
        yield from release.await_()
        yield from record(f"batch.finished:{order.user_id}")
        return f"charged:{order.user_id}"

    payments = Layer.succeed(Payments, PaymentsService(gated_charge))
    fiber: Fiber[list[Receipt], CheckoutError] = yield from fork(
        provide_layer(make_checkout_app(payments))(BATCH)
    )
    yield from both_started.await_()
    yield from record("batch.release")
    yield from release.succeed(None)
    return (yield from fiber.join())


@gen
def cancel_batch():
    both_started = yield from Deferred[None, Never].make()
    started = 0

    @gen
    def hanging_charge(order: Order):
        nonlocal started
        yield from record(f"batch.started:{order.user_id}")
        started += 1
        if started == 2:
            yield from both_started.succeed(None)
        return (yield from never)

    payments = Layer.succeed(Payments, PaymentsService(hanging_charge))
    fiber: Fiber[list[Receipt], CheckoutError] = yield from fork(
        provide_layer(make_checkout_app(payments))(BATCH)
    )
    yield from both_started.await_()
    yield from record("batch.cancel.requested")
    yield from fiber.interrupt()
    yield from record("batch.cancel.returned")
    return (yield from fiber.join())


COMPOSED_SCENARIOS: list[tuple[str, Effect[object, object, Never]]] = [
    ("composed_success", provide_layer(make_checkout_app())(place_order(Order(1)))),
    ("composed_user_failure", provide_layer(make_checkout_app())(place_order(Order(99)))),
    (
        "composed_stock_failure",
        provide_layer(make_checkout_app())(place_order(Order(1, "sold-out"))),
    ),
    (
        "composed_payment_failure",
        provide_layer(make_checkout_app())(place_order(Order(1, "book", "declined"))),
    ),
    (
        "composed_payment_recovery",
        provide_layer(make_checkout_app())(place_or_pending(Order(1, "book", "declined"))),
    ),
    (
        "composed_payment_replacement",
        provide_layer(
            make_checkout_app(
                Layer.succeed(
                    Payments,
                    PaymentsService(
                        lambda order: record(f"sandbox.charge:{order.user_id}").as_("sandbox")
                    ),
                )
            )
        )(place_order(Order(1))),
    ),
    ("composed_parallel_batch", parallel_batch()),
    ("composed_batch_cancellation", cancel_batch()),
]
