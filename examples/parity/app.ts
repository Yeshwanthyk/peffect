// The application under comparison: source-derived reference built on the pinned Effect v4
// APIs (Schema, Context.Service, Layer, acquireRelease, catchTag). Not an upstream test port.
import { Cause, Context, Data, Effect, Exit, Layer, Schema } from "effect"

// -- lifecycle events: the ordered log both runners emit ------------------------------------------

export const EventLog = Context.Reference<(event: string) => void>("parity/EventLog", {
  defaultValue: () => () => {}
})
export const record = (event: string): Effect.Effect<void> =>
  Effect.flatMap(EventLog, (sink) => Effect.sync(() => sink(event)))

export const exitKind = (exit: Exit.Exit<unknown, unknown>): "success" | "interrupt" | "failure" =>
  Exit.isSuccess(exit) ? "success" : Cause.hasInterruptsOnly(exit.cause) ? "interrupt" : "failure"

// -- validation: the request edge ----------------------------------------------------------------
// user_id: an integer (no string coercion), 1 <= user_id <= 1_000_000.
// Pydantic counterpart: `user_id: StrictInt = Field(ge=1, le=1_000_000)`.

export const UserRequest = Schema.Struct({
  user_id: Schema.Int.check(Schema.isGreaterThanOrEqualTo(1), Schema.isLessThanOrEqualTo(1_000_000))
})
export type UserRequest = typeof UserRequest.Type

// -- domain --------------------------------------------------------------------------------------

export class UserNotFound extends Data.TaggedError("UserNotFound")<{ readonly userId: number }> {}

export interface User {
  readonly id: number
  readonly name: string
}

export class Repo extends Context.Service<Repo, {
  readonly lookup: (id: number) => User | undefined
}>()("parity/Repo") {}

export class Users extends Context.Service<Users, {
  readonly get: (id: number) => Effect.Effect<User, UserNotFound>
}>()("parity/Users") {}

export class Notifier extends Context.Service<Notifier, {
  readonly send: (message: string) => Effect.Effect<void>
}>()("parity/Notifier") {}

// the two consumers that share one Users (and so one Repo)
export class Greeter extends Context.Service<Greeter, {
  readonly greet: (id: number) => Effect.Effect<string, UserNotFound>
}>()("parity/Greeter") {}

export class Auditor extends Context.Service<Auditor, {
  readonly audit: (id: number) => Effect.Effect<string, UserNotFound>
}>()("parity/Auditor") {}

// -- layers --------------------------------------------------------------------------------------

const TABLE: ReadonlyMap<number, string> = new Map([[1, "ann"], [2, "bob"]])

// the scoped resource: acquired when the layer is built, released when its scope closes
export const RepoLive = Layer.effect(
  Repo,
  Effect.acquireRelease(
    record("repo.acquire").pipe(Effect.as({
      lookup: (id: number): User | undefined => {
        const name = TABLE.get(id)
        return name === undefined ? undefined : { id, name }
      }
    })),
    (_, exit) => record(`repo.release:${exitKind(exit)}`)
  )
)

export const UsersLive = Layer.effect(
  Users,
  Effect.gen(function*() {
    const repo = yield* Repo
    yield* record("users.build")
    return {
      get: (id: number): Effect.Effect<User, UserNotFound> => {
        const user = repo.lookup(id)
        return user === undefined ? Effect.fail(new UserNotFound({ userId: id })) : Effect.succeed(user)
      }
    }
  })
).pipe(Layer.provide(RepoLive))

export const NotifierLive = Layer.succeed(Notifier, {
  send: (message: string) => record(`notify:${message}`)
})

const GreeterLive = Layer.effect(
  Greeter,
  Effect.gen(function*() {
    const users = yield* Users
    const notifier = yield* Notifier
    yield* record("greeter.build")
    return {
      greet: (id: number) =>
        users.get(id).pipe(
          Effect.map((user) => `hello ${user.name}`),
          Effect.tap((message) => notifier.send(message))
        )
    }
  })
)

const AuditorLive = Layer.effect(
  Auditor,
  Effect.gen(function*() {
    const users = yield* Users
    yield* record("auditor.build")
    return { audit: (id: number) => users.get(id).pipe(Effect.map((user) => `audited ${user.id}`)) }
  })
)

// The application's wiring. Callers choose the Users and Notifier implementations; the consumers
// and the use cases below don't change. One `users` value feeds both consumers.
export const makeApp = (
  users: Layer.Layer<Users>,
  notifier: Layer.Layer<Notifier>
): Layer.Layer<Greeter | Auditor> =>
  Layer.mergeAll(GreeterLive, AuditorLive).pipe(Layer.provide(users), Layer.provide(notifier))

export const AppLive = makeApp(UsersLive, NotifierLive)

// -- use cases -----------------------------------------------------------------------------------

export const greetUser = (req: UserRequest): Effect.Effect<string, UserNotFound, Greeter> =>
  Effect.gen(function*() {
    const greeter = yield* Greeter
    return yield* greeter.greet(req.user_id)
  })

export const greetOrGuest = (req: UserRequest): Effect.Effect<string, never, Greeter> =>
  greetUser(req).pipe(
    Effect.catchTag("UserNotFound", (e) => record(`recovered:UserNotFound:${e.userId}`).pipe(Effect.as("hello guest")))
  )

export const greetAndAudit = (
  req: UserRequest
): Effect.Effect<{ greeting: string; audit: string }, UserNotFound, Greeter | Auditor> =>
  Effect.gen(function*() {
    const greeting = yield* (yield* Greeter).greet(req.user_id)
    const audit = yield* (yield* Auditor).audit(req.user_id)
    return { greeting, audit }
  })

// The request edge: validate first, then build the application. Invalid input never builds a
// layer, so it never acquires the Repo.
export const handle = <A, E>(
  raw: unknown,
  useCase: (req: UserRequest) => Effect.Effect<A, E, Greeter | Auditor>,
  app: Layer.Layer<Greeter | Auditor> = AppLive
): Effect.Effect<A, E | Schema.SchemaError> =>
  Schema.decodeUnknownEffect(UserRequest)(raw).pipe(
    Effect.tap(() => record("request.valid")),
    Effect.flatMap((req) => Effect.provide(useCase(req), app))
  )
