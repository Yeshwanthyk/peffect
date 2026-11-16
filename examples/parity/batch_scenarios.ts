// Scenario fixtures for batch settlement, separate from the application people can copy.
import { Deferred, Effect, Fiber, Layer } from "effect"
import { record } from "./app.ts"
import { makeAuditedBatchApp, makeBatchApp, makeLedger, settle } from "./batch.ts"
import { type Order, Payments, PaymentsLive } from "./composed.ts"

const order = (userId: number, sku = "book", card = "ok"): Order => ({ userId, sku, card })

// Order 1 settles and is recorded; order 2 signals from inside its charge and hangs. The
// interrupt must release its reservation and every shared resource before it returns.
const cancelSettlement = Effect.gen(function*() {
  const hanging = yield* Deferred.make<void>()
  const payments = Layer.succeed(Payments, {
    charge: (order: Order) =>
      order.userId === 2
        ? record("charge.hanging:2").pipe(
          Effect.tap(Deferred.succeed(hanging, undefined)),
          Effect.andThen(Effect.never)
        )
        : record(`charge.ok:${order.userId}`).pipe(Effect.as(`charged:${order.userId}`))
  })
  const fiber = yield* Effect.forkChild(Effect.provide(settle([order(1), order(2)]), makeBatchApp(payments)))
  yield* Deferred.await(hanging)
  yield* record("settle.cancel.requested")
  yield* Fiber.interrupt(fiber)
  yield* record("settle.cancel.returned")
  return yield* Fiber.join(fiber)
})

export const batchScenarios: ReadonlyArray<readonly [string, Effect.Effect<unknown, unknown>]> = [
  ["batch_settle_mixed", Effect.provide(
    settle([order(1), order(2, "book", "declined"), order(99), order(1, "sold-out")]),
    makeBatchApp()
  )],
  ["batch_settle_audited_payments", Effect.provide(settle([order(1), order(2)]), makeAuditedBatchApp())],
  ["batch_settle_custom_audit", Effect.provide(settle([order(1), order(2)]), makeAuditedBatchApp(makeLedger(1)))],
  ["batch_settle_cancellation", cancelSettlement],
  ["batch_settle_ledger_full", Effect.provide(
    settle([order(1), order(2)]),
    makeBatchApp(PaymentsLive, makeLedger(1))
  )]
]
