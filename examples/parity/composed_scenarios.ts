// Scenario fixtures, separate from the application people can copy.
import { Deferred, Effect, Fiber, Layer } from "effect"
import { record } from "./app.ts"
import { makeCheckoutApp, Payments, placeOrder, placeOrPending, type Order } from "./composed.ts"

const order = (userId: number, sku = "book", card = "ok"): Order => ({ userId, sku, card })
const batchOrders = [order(1), order(2)]
const batch = Effect.forEach(batchOrders, placeOrder, { concurrency: 2 })

// Each worker reaches the payment gate before either is released. This proves overlap,
// shares one Users/Repo and Inventory, and retains the input result order.
const parallelBatch = Effect.gen(function*() {
  const bothStarted = yield* Deferred.make<void>()
  const release = yield* Deferred.make<void>()
  let started = 0
  const payments = Layer.succeed(Payments, {
    charge: (order: Order) => Effect.gen(function*() {
      yield* record(`batch.started:${order.userId}`)
      if (++started === 2) yield* Deferred.succeed(bothStarted, undefined)
      yield* Deferred.await(release)
      yield* record(`batch.finished:${order.userId}`)
      return `charged:${order.userId}`
    })
  })
  const fiber = yield* Effect.forkChild(Effect.provide(batch, makeCheckoutApp(payments)))
  yield* Deferred.await(bothStarted)
  yield* record("batch.release")
  yield* Deferred.succeed(release, undefined)
  return yield* Fiber.join(fiber)
})

const cancelBatch = Effect.gen(function*() {
  const bothStarted = yield* Deferred.make<void>()
  let started = 0
  const payments = Layer.succeed(Payments, {
    charge: (order: Order) => Effect.gen(function*() {
      yield* record(`batch.started:${order.userId}`)
      if (++started === 2) yield* Deferred.succeed(bothStarted, undefined)
      return yield* Effect.never
    })
  })
  const fiber = yield* Effect.forkChild(Effect.provide(batch, makeCheckoutApp(payments)))
  yield* Deferred.await(bothStarted)
  yield* record("batch.cancel.requested")
  yield* Fiber.interrupt(fiber)
  yield* record("batch.cancel.returned")
  return yield* Fiber.join(fiber)
})

export const composedScenarios: ReadonlyArray<readonly [string, Effect.Effect<unknown, unknown>]> = [
  ["composed_success", Effect.provide(placeOrder(order(1)), makeCheckoutApp())],
  ["composed_user_failure", Effect.provide(placeOrder(order(99)), makeCheckoutApp())],
  ["composed_stock_failure", Effect.provide(placeOrder(order(1, "sold-out")), makeCheckoutApp())],
  ["composed_payment_failure", Effect.provide(placeOrder(order(1, "book", "declined")), makeCheckoutApp())],
  ["composed_payment_recovery", Effect.provide(placeOrPending(order(1, "book", "declined")), makeCheckoutApp())],
  ["composed_payment_replacement", Effect.provide(placeOrder(order(1)), makeCheckoutApp(Layer.succeed(Payments, {
    charge: (order) => record(`sandbox.charge:${order.userId}`).pipe(Effect.as("sandbox"))
  })))],
  ["composed_parallel_batch", parallelBatch],
  ["composed_batch_cancellation", cancelBatch]
]
