// Batch settlement over the checkout application, built on the actual pinned Effect: settle a
// batch of orders into one Ledger. Not an upstream test port.
import { Context, Data, Effect, Layer } from "effect"
import { exitKind, record } from "./app.ts"
import {
  Checkout,
  makeCheckoutApp,
  type Order,
  PaymentDeclined,
  Payments,
  PaymentsLive,
  placeOrPending
} from "./composed.ts"

export type Line = { readonly userId: number; readonly status: string; readonly detail: string }
export class LedgerFull extends Data.TaggedError("LedgerFull")<{ readonly capacity: number }> {}

export class Ledger extends Context.Service<Ledger, {
  readonly append: (line: Line) => Effect.Effect<void, LedgerFull>
}>()("parity/Ledger") {}

export class Batch extends Context.Service<Batch, {
  readonly settle: (orders: ReadonlyArray<Order>) => Effect.Effect<ReadonlyArray<Line>>
}>()("parity/Batch") {}

// A resource: opened when the layer is built, closed with its line count.
export const makeLedger = (capacity: number): Layer.Layer<Ledger> =>
  Layer.effect(Ledger, Effect.acquireRelease(
    record("ledger.open").pipe(Effect.andThen(Effect.sync((): Array<Line> => []))),
    (lines, exit) => record(`ledger.close:${exitKind(exit)}:${lines.length}`)
  ).pipe(Effect.map((lines) => ({
    append: (line: Line): Effect.Effect<void, LedgerFull> =>
      Effect.suspend(() => {
        if (lines.length >= capacity) {
          return record(`ledger.full:${line.userId}`).pipe(Effect.andThen(Effect.fail(new LedgerFull({ capacity }))))
        }
        lines.push(line)
        return record(`ledger.append:${line.userId}:${line.status}`)
      })
  }))))

export const LedgerLive = makeLedger(100)

const lineFor = (order: Order, status: string, detail: string): Line => ({ userId: order.userId, status, detail })

// Every domain error becomes a line: a declined payment is pending, the rest rejected.
export const settleOne = (order: Order): Effect.Effect<Line, never, Checkout> =>
  placeOrPending(order).pipe(
    Effect.map((receipt) => lineFor(order, receipt.status, receipt.payment)),
    Effect.catchTag("UserNotFound", (e) => Effect.succeed(lineFor(order, "rejected", `unknown-user:${e.userId}`))),
    Effect.catchTag("OutOfStock", (e) => Effect.succeed(lineFor(order, "rejected", `out-of-stock:${e.sku}`)))
  )

const BatchLive = Layer.effect(Batch, Effect.gen(function*() {
  const checkout = yield* Checkout
  const ledger = yield* Ledger
  yield* record("batch.build")
  // The tap keeps the settled line; when the Ledger refuses it, the order is unrecorded.
  const settleAndRecord = (order: Order): Effect.Effect<Line> =>
    settleOne(order).pipe(
      Effect.provideService(Checkout, checkout),
      Effect.tap(ledger.append),
      Effect.catchTag("LedgerFull", (e) => Effect.succeed(lineFor(order, "unrecorded", `ledger-full:${e.capacity}`)))
    )
  return {
    settle: (orders: ReadonlyArray<Order>) => Effect.forEach(orders, settleAndRecord, { concurrency: 2 })
  }
}))

// A replacement Payments that itself needs the Ledger: one Ledger serves it and Batch.
export const AuditedPayments = Layer.effect(Payments, Effect.gen(function*() {
  const ledger = yield* Ledger
  yield* record("payments.audited.build")
  return {
    // A charge that cannot be audited is declined.
    charge: (order: Order) =>
      ledger.append(lineFor(order, "charge", "sandbox")).pipe(
        Effect.as(`sandbox:${order.userId}`),
        Effect.catchTag("LedgerFull", () => Effect.fail(new PaymentDeclined({ userId: order.userId })))
      )
  }
}))

export const makeBatchApp = (
  payments: Layer.Layer<Payments> = PaymentsLive,
  ledger: Layer.Layer<Ledger> = LedgerLive
): Layer.Layer<Batch> => BatchLive.pipe(Layer.provide(Layer.mergeAll(makeCheckoutApp(payments), ledger)))

export const settle = (orders: ReadonlyArray<Order>): Effect.Effect<ReadonlyArray<Line>, never, Batch> =>
  Effect.flatMap(Batch, (batch) => batch.settle(orders))

// Both consumers receive the chosen resource, including a custom-capacity Ledger.
export const makeAuditedBatchApp = (ledger: Layer.Layer<Ledger> = LedgerLive): Layer.Layer<Batch> =>
  makeBatchApp(AuditedPayments.pipe(Layer.provide(ledger)), ledger)
