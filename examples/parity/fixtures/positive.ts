// Must compile with no diagnostics. Checked by fixtures/check.ts.
import { Effect, Layer, Schema } from "effect"
import { AppLive, greetOrGuest, greetUser, handle, makeApp, NotifierLive, type UserNotFound, Users } from "../app.ts"
import type { Greeter } from "../app.ts"

// the full application needs nothing more: R = never, so it runs
export const program: Effect.Effect<string, UserNotFound | Schema.SchemaError> = handle({ user_id: 1 }, greetUser)
export const ran: Promise<string> = Effect.runPromise(Effect.orDie(program))

// catchTag removes exactly the handled domain error
export const recovered: Effect.Effect<string, never, Greeter> = greetOrGuest({ user_id: 1 })

// a test implementation with the right shape replaces Users without touching the use case
export const testApp = makeApp(Layer.succeed(Users, { get: (id: number) => Effect.succeed({ id, name: "t" }) }), NotifierLive)
export const replaced: Effect.Effect<string, UserNotFound | Schema.SchemaError> = handle({ user_id: 1 }, greetUser, testApp)
export const live = AppLive

// Composition keeps the remaining error union and discharges the complete service graph.
import { Checkout, makeCheckoutApp, OutOfStock, placeOrPending, type Receipt } from "../composed.ts"
export const pending: Effect.Effect<Receipt, UserNotFound | OutOfStock, Checkout> =
  placeOrPending({ userId: 1, sku: "book", card: "declined" })
export const composed: Effect.Effect<Receipt, UserNotFound | OutOfStock> =
  Effect.provide(pending, makeCheckoutApp())
