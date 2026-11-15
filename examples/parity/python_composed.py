"""Counterpart of composed.ts: nested resources, error unions, and concurrent checkout."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, TypedDict

from examples.parity.python_app import UserNotFound, Users, UsersLive, exit_kind, record
from peffect import (
    Effect,
    Layer,
    Tag,
    acquire_release,
    acquire_use_release,
    catch_tag,
    fail,
    gen,
    succeed,
)


@dataclass(frozen=True)
class OutOfStock:
    sku: str


@dataclass(frozen=True)
class PaymentDeclined:
    user_id: int


@dataclass(frozen=True)
class Order:
    user_id: int
    sku: str = "book"
    card: str = "ok"


class Receipt(TypedDict):
    user: str
    payment: str
    status: str


type CheckoutError = UserNotFound | OutOfStock | PaymentDeclined


@dataclass(frozen=True)
class InventoryService:
    reserve: Callable[[Order], Effect[str, OutOfStock, Never]]


@dataclass(frozen=True)
class PaymentsService:
    charge: Callable[[Order], Effect[str, PaymentDeclined, Never]]


@dataclass(frozen=True)
class RiskService:
    check: Callable[[int], Effect[None, UserNotFound, Never]]


@dataclass(frozen=True)
class CheckoutService:
    place: Callable[[Order], Effect[Receipt, CheckoutError, Never]]


class Inventory(Tag[InventoryService], key="parity/Inventory"): ...


class Payments(Tag[PaymentsService], key="parity/Payments"): ...


class Risk(Tag[RiskService], key="parity/Risk"): ...


class Checkout(Tag[CheckoutService], key="parity/Checkout"): ...


def reserve(order: Order) -> Effect[str, OutOfStock, Never]:
    return record(f"inventory.reserve:{order.user_id}:{order.sku}").flat_map(
        lambda _: (
            fail(OutOfStock(order.sku))
            if order.sku == "sold-out"
            else succeed(f"{order.user_id}:{order.sku}")
        )
    )


InventoryLive = Layer.effect(
    Inventory,
    acquire_release(
        record("inventory.acquire").as_(InventoryService(reserve)),
        lambda _, ex: record(f"inventory.release:{exit_kind(ex)}"),
    ),
)


def charge(order: Order) -> Effect[str, PaymentDeclined, Never]:
    return record(f"payments.charge:{order.user_id}").flat_map(
        lambda _: (
            fail(PaymentDeclined(order.user_id))
            if order.card == "declined"
            else succeed(f"charged:{order.user_id}")
        )
    )


PaymentsLive = Layer.effect(
    Payments,
    acquire_release(
        record("payments.acquire").as_(PaymentsService(charge)),
        lambda _, ex: record(f"payments.release:{exit_kind(ex)}"),
    ),
)


@gen
def make_checkout():
    users = yield from Users()
    inventory = yield from Inventory()
    payments = yield from Payments()
    risk = yield from Risk()
    yield from record("checkout.build")

    @gen
    def place(order: Order):
        yield from risk.check(order.user_id)
        user = yield from users.get(order.user_id)

        def paid(payment: str) -> Receipt:
            return {"user": user.name, "payment": payment, "status": "paid"}

        return (
            yield from acquire_use_release(
                inventory.reserve(order),
                lambda _: payments.charge(order).map(paid),
                lambda reservation, ex: record(
                    f"reservation.release:{reservation}:{exit_kind(ex)}"
                ),
            )
        )

    return CheckoutService(place)


CheckoutLive = Layer.effect(Checkout, make_checkout())


@gen
def make_risk():
    users = yield from Users()
    yield from record("risk.build")
    return RiskService(
        lambda user_id: users.get(user_id).flat_map(lambda _: record(f"risk.checked:{user_id}"))
    )


RiskLive = Layer.effect(Risk, make_risk()).pipe(Layer.provide(UsersLive))


def make_checkout_app(payments: Layer[Payments] = PaymentsLive) -> Layer[Checkout]:
    # Real diamond: both the direct consumer and RiskLive build the SAME UsersLive identity.
    return CheckoutLive.pipe(
        Layer.provide(Layer.merge_all(UsersLive, InventoryLive, payments, RiskLive))
    )


def place_order(order: Order) -> Effect[Receipt, CheckoutError, Checkout]:
    return Checkout().use().flat_map(lambda checkout: checkout.place(order))


def place_or_pending(order: Order) -> Effect[Receipt, UserNotFound | OutOfStock, Checkout]:
    def recover(error: PaymentDeclined) -> Effect[Receipt, Never, Never]:
        pending: Receipt = {"user": "pending", "payment": "manual-review", "status": "pending"}
        return record(f"payment.recovered:{error.user_id}").as_(pending)

    return place_order(order).pipe(catch_tag(PaymentDeclined, recover))
