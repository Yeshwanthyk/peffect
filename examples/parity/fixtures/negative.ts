// Each numbered block is a mistake the compiler must reject. Checked by fixtures/check.ts.
import { Context, Effect, Layer } from "effect"
import { greetUser, makeApp, NotifierLive, Repo, Users } from "../app.ts"

// 1. MISSING_DEPENDENCY: greetUser needs Greeter; runPromise needs R = never
export const unprovided = Effect.runPromise(greetUser({ user_id: 1 }))

// 2. LAYER_MISSING_DEPENDENCY: this Users layer still needs Repo
const UsersNeedingRepo = Layer.effect(Users, Effect.gen(function*() {
  const repo = yield* Repo
  return { get: (id: number) => Effect.succeed(repo.lookup(id) ?? { id, name: "?" }) }
}))
export const partial = makeApp(UsersNeedingRepo, NotifierLive)

// 3. WRONG_IMPLEMENTATION: get must succeed with a User, not a string
export const wrongValue = Layer.succeed(Users, { get: (_id: number) => Effect.succeed("ann") })

// 4. UNDECLARED_ERROR: get may fail only with UserNotFound
export const wrongError = Layer.succeed(Users, { get: (_id: number) => Effect.fail(new Error("db down")) })

// 5. UNKNOWN_ERROR_TAG: greetUser has no error tagged "Timeout"
export const wrongTag = greetUser({ user_id: 1 }).pipe(Effect.catchTag("Timeout", () => Effect.succeed("")))

// 6. FORGED_SERVICE: a value of the wrong service cannot stand in for Users
export const forged = Context.make(Users, { lookup: (_id: number) => undefined })
