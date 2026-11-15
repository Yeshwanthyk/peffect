// A deeper application built on the actual pinned Effect: not an upstream test port.
import { Context, Data, Effect, Layer } from "effect"
import { exitKind, record, UserNotFound, Users, UsersLive } from "./app.ts"

export class OutOfStock extends Data.TaggedError("OutOfStock")<{ readonly sku: string }> {}
export class PaymentDeclined extends Data.TaggedError("PaymentDeclined")<{ readonly userId: number }> {}
export type Order = { readonly userId: number; readonly sku: string; readonly card: string }
export type Receipt = { readonly user: string; readonly payment: string; readonly status: string }
export type CheckoutError = UserNotFound | OutOfStock | PaymentDeclined

export class Inventory extends Context.Service<Inventory, {
  readonly reserve: (order: Order) => Effect.Effect<string, OutOfStock>
}>()("parity/Inventory") {}
export class Payments extends Context.Service<Payments, {
  readonly charge: (order: Order) => Effect.Effect<string, PaymentDeclined>
}>()("parity/Payments") {}
export class Risk extends Context.Service<Risk, {
  readonly check: (userId: number) => Effect.Effect<void, UserNotFound>
}>()("parity/Risk") {}
export class Checkout extends Context.Service<Checkout, {
  readonly place: (order: Order) => Effect.Effect<Receipt, CheckoutError>
}>()("parity/Checkout") {}

const InventoryLive = Layer.effect(Inventory, Effect.acquireRelease(
  record("inventory.acquire").pipe(Effect.as({
    reserve: (order: Order): Effect.Effect<string, OutOfStock> =>
      record(`inventory.reserve:${order.userId}:${order.sku}`).pipe(Effect.andThen(
        order.sku === "sold-out" ? Effect.fail(new OutOfStock({ sku: order.sku })) :
          Effect.succeed(`${order.userId}:${order.sku}`)
      ))
  })),
  (_, exit) => record(`inventory.release:${exitKind(exit)}`)
))

export const PaymentsLive = Layer.effect(Payments, Effect.acquireRelease(
  record("payments.acquire").pipe(Effect.as({
    charge: (order: Order): Effect.Effect<string, PaymentDeclined> =>
      record(`payments.charge:${order.userId}`).pipe(Effect.andThen(
        order.card === "declined" ? Effect.fail(new PaymentDeclined({ userId: order.userId })) :
          Effect.succeed(`charged:${order.userId}`)
      ))
  })),
  (_, exit) => record(`payments.release:${exitKind(exit)}`)
))

const CheckoutLive = Layer.effect(Checkout, Effect.gen(function*() {
  const users = yield* Users
  const inventory = yield* Inventory
  const payments = yield* Payments
  const risk = yield* Risk
  yield* record("checkout.build")
  return {
    place: (order: Order) => Effect.gen(function*() {
      yield* risk.check(order.userId)
      const user = yield* users.get(order.userId)
      return yield* Effect.acquireUseRelease(
        inventory.reserve(order),
        () => payments.charge(order).pipe(Effect.map((payment): Receipt => ({
          user: user.name, payment, status: "paid"
        }))),
        (reservation, exit) => record(`reservation.release:${reservation}:${exitKind(exit)}`)
      )
    })
  }
}))

const RiskLive = Layer.effect(Risk, Effect.gen(function*() {
  const users = yield* Users
  yield* record("risk.build")
  return { check: (userId: number) => users.get(userId).pipe(
    Effect.flatMap(() => record(`risk.checked:${userId}`))
  ) }
})).pipe(Layer.provide(UsersLive))

export const makeCheckoutApp = (payments: Layer.Layer<Payments> = PaymentsLive): Layer.Layer<Checkout> =>
  // A real diamond: UsersLive appears directly AND beneath RiskLive in the same MemoMap.
  CheckoutLive.pipe(Layer.provide(Layer.mergeAll(UsersLive, InventoryLive, payments, RiskLive)))

export const placeOrder = (order: Order): Effect.Effect<Receipt, CheckoutError, Checkout> =>
  Effect.flatMap(Checkout, (checkout) => checkout.place(order))

export const placeOrPending = (order: Order): Effect.Effect<Receipt, UserNotFound | OutOfStock, Checkout> =>
  placeOrder(order).pipe(Effect.catchTag("PaymentDeclined", (error) =>
    record(`payment.recovered:${error.userId}`).pipe(Effect.as({
      user: "pending", payment: "manual-review", status: "pending"
    }))
  ))
