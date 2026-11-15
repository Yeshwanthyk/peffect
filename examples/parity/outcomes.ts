// Fail-closed reporting boundary. Never discard extra reasons from a failed Exit.
import { Cause, Exit, Option, Schema } from "effect"
import { UserNotFound } from "./app.ts"
import { OutOfStock, PaymentDeclined } from "./composed.ts"

type Outcome =
  | { kind: "success"; value: unknown }
  | { kind: "failure"; category: "validation" | "domain"; tag: string; detail: unknown }
  | { kind: "interrupted" }

export const outcomeOf = (exit: Exit.Exit<unknown, unknown>): Outcome => {
  if (Exit.isSuccess(exit)) return { kind: "success", value: exit.value }
  if (Cause.hasInterruptsOnly(exit.cause)) return { kind: "interrupted" }
  // Fail closed: selecting the first error would hide an extra cleanup defect or failure.
  if (exit.cause.reasons.length !== 1) throw new Error(`unclassified exit: ${Cause.pretty(exit.cause)}`)
  const error = Option.getOrUndefined(Cause.findErrorOption(exit.cause))
  if (Schema.isSchemaError(error)) {
    // library-specific diagnostic text: reported, not compared
    return { kind: "failure", category: "validation", tag: error._tag, detail: { diagnostic_not_compared: error.message } }
  }
  if (error instanceof UserNotFound) {
    return { kind: "failure", category: "domain", tag: error._tag, detail: { userId: error.userId } }
  }
  if (error instanceof OutOfStock) {
    return { kind: "failure", category: "domain", tag: error._tag, detail: { sku: error.sku } }
  }
  if (error instanceof PaymentDeclined) {
    return { kind: "failure", category: "domain", tag: error._tag, detail: { userId: error.userId } }
  }
  throw new Error(`unclassified exit: ${Cause.pretty(exit.cause)}`)
}
