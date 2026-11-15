// Runs each acceptance scenario of docs/FIRST_USABLE_SLICE.md against the pinned Effect v4 and
// prints one JSON document: per scenario the outcome, Repo acquire/release counts and the ordered
// lifecycle events. Source-derived application reference, not an upstream test port.
import { Cause, Deferred, Effect, Exit, Fiber, Layer, Option, Schema } from "effect"
import effectPackage from "effect/package.json" with { type: "json" }
import {
  events,
  exitKind,
  greetAndAudit,
  greetOrGuest,
  greetUser,
  handle,
  makeApp,
  Notifier,
  NotifierLive,
  record,
  UserNotFound,
  Users,
  UsersLive
} from "./app.ts"

type Outcome =
  | { kind: "success"; value: unknown }
  | { kind: "failure"; category: "validation" | "domain"; tag: string; detail: unknown }
  | { kind: "interrupted" }

const outcomeOf = (exit: Exit.Exit<unknown, unknown>): Outcome => {
  if (Exit.isSuccess(exit)) return { kind: "success", value: exit.value }
  if (Cause.hasInterruptsOnly(exit.cause)) return { kind: "interrupted" }
  const error = Option.getOrUndefined(Cause.findErrorOption(exit.cause))
  if (Schema.isSchemaError(error)) {
    // library-specific diagnostic text: reported, not compared
    return { kind: "failure", category: "validation", tag: error._tag, detail: { diagnostic_not_compared: error.message } }
  }
  if (error instanceof UserNotFound) {
    return { kind: "failure", category: "domain", tag: error._tag, detail: { userId: error.userId } }
  }
  throw new Error(`unclassified exit: ${Cause.pretty(exit.cause)}`)
}

const count = (prefix: string) => events.filter((e) => e.startsWith(prefix)).length

const scenario = async (name: string, program: Effect.Effect<unknown, unknown>) => {
  events.length = 0
  const exit = await Effect.runPromiseExit(program)
  events.push(`terminated:${exitKind(exit)}`)
  return {
    name,
    outcome: outcomeOf(exit),
    repo: { acquire: count("repo.acquire"), release: count("repo.release") },
    events: [...events]
  }
}

// a replacement Users for a test: no Repo, same application code
const TestUsers = Layer.succeed(Users, {
  get: (id: number) => record(`test-users.get:${id}`).pipe(Effect.as({ id, name: "test-user" }))
})

// Cancellation after acquisition: the Notifier signals that the use case is running (the Repo is
// acquired by then) and hangs. The parent waits for that signal, not for wall time, interrupts,
// and records when the interrupt returns: the release must come before it.
const cancellation = Effect.gen(function*() {
  const started = yield* Deferred.make<void>()
  const hanging = Layer.succeed(Notifier, {
    send: (message: string) =>
      record(`notify.started:${message}`).pipe(
        Effect.andThen(Deferred.succeed(started, undefined)),
        Effect.andThen(Effect.never)
      )
  })
  const fiber = yield* Effect.forkChild(handle({ user_id: 1 }, greetUser, makeApp(UsersLive, hanging)))
  yield* Deferred.await(started)
  yield* record("cancel.requested")
  yield* Fiber.interrupt(fiber)
  yield* record("cancel.returned")
  return yield* Fiber.await(fiber).pipe(Effect.flatMap((exit) => exit))
})

const results = [
  await scenario("success", handle({ user_id: 1 }, greetUser)),
  await scenario("invalid_input_range", handle({ user_id: 0 }, greetUser)),
  await scenario("invalid_input_type", handle({ user_id: "1" }, greetUser)),
  await scenario("domain_failure_after_acquire", handle({ user_id: 99 }, greetUser)),
  await scenario("domain_recovery", handle({ user_id: 99 }, greetOrGuest)),
  await scenario("shared_layer", handle({ user_id: 2 }, greetAndAudit)),
  await scenario("service_replacement", handle({ user_id: 1 }, greetUser, makeApp(TestUsers, NotifierLive))),
  await scenario("cancellation_after_acquire", cancellation)
]

console.log(JSON.stringify(
  {
    reference: {
      label: "source-derived application reference (not an upstream test port)",
      effect_version: effectPackage.version,
      effect_source: "vendor/effect-v4/packages/effect/src, submodule commit 289a53f7b1fc40049219c6e69022db2fb859c356"
    },
    scenarios: results
  },
  null,
  2
))
