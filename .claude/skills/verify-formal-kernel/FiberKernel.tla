---------------------------- MODULE FiberKernel ----------------------------
(* The fiber kernel's park / resume / interrupt / yield / children protocol.

   Code: src/peffect/fiber.py (Fiber.evaluate, yield_now, interrupt_unsafe, _done, _await_all,
   _start), src/peffect/core.py (Async, Failure, SetInterruptible). Effect v4 runs a fiber
   synchronously inside whoever wakes it until the per-loop depth budget is reached. A deep
   wake instead queues a fresh Park whose token can be claimed by an interrupt. WakeBudget=1
   makes that branch reachable with two fibers; runtime sweep covers the real 24-drive cutoff.
   A resume, interrupt, immediate fork and root start otherwise call evaluate at once. Drives nest:
   `cur` is the innermost running drive and `stk` the drives suspended under it, outermost
   first. Every action is atomic: one asyncio loop, and `evaluate` never awaits.

   Wakers may act at any point, nested in whatever drive is running (user code can call a
   resume, `interrupt_unsafe` or a dispatcher `flush` from any node) or from the loop:
   ExtResume, Interrupt and Drive (a dispatcher task) carry no `cur = "none"` guard.

   Two fibers. P (the root, started at once) forks C with default or inherited mask, yields,
   explicitly enables interruption, parks, and ends while C may still run. C yields, parks,
   explicitly enables interruption, masks, parks inside the mask, and unmasks. Both regain
   interruption before finishing: K7 does not cover permanently masked completion. An
   interrupter interrupts P once. Async completions arrive at any point, late ones included. *)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS Break, WakeBudget
\* WakeBudget=1 deliberately makes a two-fiber instance reach runtime's deferred-wake branch.

Fibers == {"P", "C"}
Prog == [f \in Fibers |->
           IF f = "P" THEN <<"fork", "yield", "enable", "park">>
           ELSE <<"yield", "park", "enable", "mask", "park", "unmask">>]
End(f) == Len(Prog[f]) + 1
WAIT == 99  \* pc and park id while P is parked on _await_all in Fiber._done

VARIABLES
  cur,      \* the fiber whose evaluate is innermost, or "none" (no drive is running)
  stk,      \* drives suspended under cur's (nested evaluate), outermost first
  fb,       \* per-fiber state, fields below
  started,  \* C has been forked (fiber._start)
  obs,      \* P's _await_all observer is registered on C (Fiber.add_observer)
  sent,     \* the interrupter has called P.interrupt_unsafe
  selfUsed  \* a register has interrupted its own fiber (bounded to once)
vars == <<cur, stk, fb, started, obs, sent, selfUsed>>

(* fb[f] fields and the Fiber field each models:
   pc      next instruction (straight-line Prog), WAIT while waiting on children
   node    the node the running drive evaluates next: "start" | "ok" | "int" | "await" | "none"
   intr    Fiber.interruptible          irq     Fiber.interrupted is not None
   dfr     Fiber._deferred              parked  Fiber._parked is not None
   pid     id of the current Park (an Async, or a queued yield step)   pdone   Park.done
   wakes   claims of the current Park: resume, queued step or interrupt (K2)
   ext     ids of Async Parks whose completion is still pending (resume callbacks)
   drv     dispatcher tasks queued for this fiber: <<"start", 0>> (scheduled fork, a no-op
           once exited) or <<"y", id>> (yield_now's step, a no-op once its Park is done)
   st      the fiber's first evaluate has run (an unstarted one has an empty stack)
   ending  Fiber._ending                exit    Fiber._exit ("none" | "ok" | "int")
   pubs    times _publish ran (K1)      req     an interrupt was requested before the exit *)

Init ==
  \E parentIntr \in BOOLEAN :
  /\ cur = "P" /\ stk = <<>>  \* run_exit -> _start(None, ...): the root's evaluate runs at once
  /\ fb = [f \in Fibers |->
            [pc |-> 1, node |-> IF f = "P" THEN "start" ELSE "none",
             intr |-> IF f = "P" THEN parentIntr ELSE TRUE, irq |-> FALSE, dfr |-> FALSE,
             parked |-> FALSE, pid |-> 0, pdone |-> TRUE, wakes |-> 0, ext |-> {}, drv |-> <<>>,
             st |-> f = "P",
             ending |-> "none", exit |-> "none", pubs |-> 0, req |-> FALSE]]
  /\ started = FALSE /\ obs = FALSE /\ sent = FALSE /\ selfUsed = FALSE

InStk(f) == \E i \in 1..Len(stk) : stk[i] = f
Running(f) == cur = f \/ InStk(f)    \* Fiber.running

\* f's evaluate starts now, nested in the running drive (or from the loop)
Push(f) == cur' = f /\ stk' = IF cur = "none" THEN stk ELSE Append(stk, cur)
\* the running drive returns to the drive it was nested in, or to the loop
EndDrive == IF stk = <<>> THEN cur' = "none" /\ stk' = stk
            ELSE cur' = stk[Len(stk)] /\ stk' = SubSeq(stk, 1, Len(stk) - 1)
\* cur's drive ends and f's starts in its place (the caller has nothing left to run)
Replace(f) == cur' = f /\ UNCHANGED stk

\* the claim a resume, queued step or interrupt makes on the current Park, then evaluate
Claim(r, node) == [r EXCEPT !.pdone = TRUE, !.parked = FALSE, !.node = node,
                            !.wakes = IF r.parked THEN @ + 1 ELSE @]
AtBudget == Len(stk) + (IF cur = "none" THEN 0 ELSE 1) >= WakeBudget
\* Fiber.evaluate's depth branch: after the old Park is claimed, install a fresh one. The
\* queued step carries its own token and intended node; an interrupt may claim it first.
Fresh(r, node) == [r EXCEPT !.pid = @ + 1000, !.pdone = FALSE, !.parked = TRUE,
                            !.wakes = 0, !.node = "none",
                            !.drv = Append(@, <<"d", r.pid + 1000, node>>)]

Masked(f) == ~fb[f].intr \/ (f = "C" /\ fb[f].pc \in {5, 6})
\* Keep the explicit-mask interval independent of intr; also check inherited startup masks.

\* Fiber.interrupt_unsafe: deferred while running or masked, otherwise _interrupt_now at once
\* (take_interrupt, mark the parked Park done, evaluate the Failure). `now`: that drive starts.
\* A fiber whose queued start has not run has an empty stack and no children or observers
\* (only C is ever unstarted, and only P waits on it, after this call): its Failure goes
\* straight to _done and _publish, running no user code, so it publishes atomically here.
IntUnsafe(r, running) ==
  IF r.exit # "none" THEN [rec |-> r, now |-> FALSE]
  ELSE IF running \/ (~r.intr /\ Break # "no_mask_check")
       THEN [rec |-> [r EXCEPT !.irq = TRUE, !.req = TRUE, !.dfr = TRUE], now |-> FALSE]
       ELSE IF ~r.st /\ ~r.parked
       THEN [rec |-> [r EXCEPT !.irq = TRUE, !.req = TRUE, !.dfr = FALSE, !.exit = "int",
                               !.pubs = @ + 1], now |-> FALSE]
       ELSE [rec |-> [Claim(r, "int") EXCEPT !.irq = TRUE, !.req = TRUE, !.dfr = FALSE],
             now |-> TRUE]

\* end of evaluate: parked while an interrupt waited (it came in during register) -> a fresh
\* evaluate of the interrupt at the same nesting level. Python exits the fiber's contextvars
\* Context before this tail action re-enters it; context state is outside this model.
DriveTail(f, r) ==
  IF r.dfr /\ r.intr /\ r.parked
  THEN /\ fb' = [fb EXCEPT ![f] = [Claim(r, "int") EXCEPT !.dfr = FALSE]]
       /\ UNCHANGED <<cur, stk>>
  ELSE /\ fb' = [fb EXCEPT ![f] = r]
       /\ EndDrive

\* Fiber.evaluate loop top: `if self._deferred and self.interruptible` -> Failure(take_interrupt())
LoopTop(f) ==
  LET r == fb[f] IN
  /\ cur = f /\ r.dfr /\ r.intr /\ Break # "no_loop_top"
  /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.node = "int", !.dfr = FALSE]]
  /\ UNCHANGED <<cur, stk, started, obs, sent, selfUsed>>

Ready(f) == cur = f /\ (~(fb[f].dfr /\ fb[f].intr) \/ Break = "no_loop_top")

\* Fiber._done, then _publish (single call site). o = obs after any cancel ran.
Done(f, e, o) ==
  LET r == fb[f]
      e2 == IF r.ending = "none" THEN e ELSE IF e = "int" THEN "int" ELSE r.ending
      liveC == f = "P" /\ started /\ fb["C"].exit = "none" /\ Break # "no_wait"
      p == fb["P"]
      ic == IntUnsafe(fb["C"], Running("C"))
      pub == IF r.exit # "none" THEN r
             ELSE [r EXCEPT !.exit = e2, !.pubs = @ + 1, !.node = "none"]
      \* C's _publish calls P's _await_all observer: one_done -> resume(UNIT) -> P.evaluate.
      \* C's drive has nothing left once it publishes, so P's drive replaces it; Python still
      \* has C.running set here (upstream has already cleared it), but every entry to C now
      \* returns at the `_exit` check, so reentry is harmless. White-box observers can see
      \* the running flag difference; that observation is outside this protocol model.
      notify == f = "C" /\ o /\ p.pid = WAIT /\ Break # "no_notify" /\ (~p.pdone \/ Break = "no_park_done")
      \* the pre-fix _done published P from C's observer, outside P's run loop
      oldPub == f = "C" /\ Break = "old_done" /\ p.pc = WAIT /\ p.exit = "none"
  IN
  IF ~liveC THEN
     /\ IF f = "P" THEN  \* no_wait: interrupt C, don't wait
           IF Break = "no_wait" /\ started
           THEN /\ fb' = [fb EXCEPT !["P"] = pub, !["C"] = ic.rec]
                /\ IF ic.now THEN Replace("C") ELSE EndDrive
           ELSE /\ fb' = [fb EXCEPT !["P"] = pub]
                /\ EndDrive
        ELSE IF notify THEN
           /\ fb' = [fb EXCEPT !["C"] = pub, !["P"] = Claim(p, "ok")]
           /\ Replace("P")
        ELSE
           /\ fb' = [fb EXCEPT !["C"] = pub,
                               !["P"] = IF oldPub THEN [p EXCEPT !.exit = p.ending, !.pubs = @ + 1]
                                        ELSE p]
           /\ EndDrive
     /\ obs' = IF f = "C" THEN FALSE ELSE o
     /\ UNCHANGED <<started, sent, selfUsed>>
  ELSE IF Break = "old_done" THEN
     /\ fb' = [fb EXCEPT !["P"] = [r EXCEPT !.ending = e2, !.pc = WAIT, !.node = "none"],
                         !["C"] = ic.rec]
     /\ obs' = TRUE
     /\ IF ic.now THEN Replace("C") ELSE EndDrive
     /\ UNCHANGED <<started, sent, selfUsed>>
  ELSE  \* remember the exit, interrupt C (it may run now, nested), then evaluate _await_all
     /\ fb' = [fb EXCEPT !["P"] = [r EXCEPT !.ending = e2, !.pc = WAIT, !.node = "await"],
                         !["C"] = ic.rec]
     /\ obs' = o
     /\ IF ic.now THEN Push("C") ELSE UNCHANGED <<cur, stk>>
     /\ UNCHANGED <<started, sent, selfUsed>>

\* core.py Async._eval: register (which may interrupt its own fiber), _park, end of drive
Park(f) ==
  \E self \in (IF selfUsed THEN {FALSE} ELSE BOOLEAN) :
    LET r == fb[f]
        r1 == IF self THEN IntUnsafe(r, TRUE).rec ELSE r
        r2 == [r1 EXCEPT !.ext = @ \cup {r.pc}, !.parked = TRUE, !.pid = r.pc, !.pdone = FALSE,
                         !.wakes = 0, !.pc = @ + 1, !.node = "none"]
    IN /\ DriveTail(f, r2)
       /\ selfUsed' = (selfUsed \/ self)
       /\ UNCHANGED <<started, obs, sent>>

\* Fiber.yield_now: queue the step on the dispatcher, park on it, end of drive
Yield(f) ==
  LET r == fb[f]
      r2 == [r EXCEPT !.drv = Append(@, <<"y", r.pc>>), !.parked = TRUE, !.pid = r.pc,
                      !.pdone = FALSE, !.wakes = 0, !.pc = @ + 1, !.node = "none"]
  IN /\ DriveTail(f, r2)
     /\ UNCHANGED <<started, obs, sent, selfUsed>>

\* fiber._await_all: add observers on C, park (interruptible), end of drive. If C already
\* published (it ran to its end inside the interrupt above), add_observer calls back during
\* register: the resume is synchronous and nothing parks.
AwaitPark ==
  LET r == fb["P"]
      r2 == [r EXCEPT !.parked = TRUE, !.pid = WAIT, !.pdone = FALSE, !.wakes = 0, !.node = "none"]
  IN IF fb["C"].exit # "none"
     THEN /\ fb' = [fb EXCEPT !["P"] = [r EXCEPT !.node = "ok"]]
          /\ UNCHANGED <<cur, stk, obs, started, sent, selfUsed>>
     ELSE /\ DriveTail("P", r2)
          /\ obs' = TRUE
          /\ UNCHANGED <<started, sent, selfUsed>>

\* one node of Fiber.evaluate's loop (`cur = cur._eval(self)`)
Eval(f) ==
  LET r == fb[f]
      ins == Prog[f][r.pc]
  IN
  /\ Ready(f)
  /\ r.node # "none"
  /\ IF r.node = "int" THEN
        \* the interrupt unwinds to the top; under _await_all, its _AsyncCancel removes the observer
        Done(f, "int", IF f = "P" /\ r.pc = WAIT THEN FALSE ELSE obs)
     ELSE IF r.node = "await" THEN AwaitPark
     ELSE IF r.pc = WAIT \/ r.pc = End(f) THEN Done(f, "ok", obs)
     ELSE IF ins = "park" THEN Park(f)
     ELSE IF ins = "yield" THEN Yield(f)
     ELSE IF ins = "fork" THEN
        \E imm \in BOOLEAN, inherit \in BOOLEAN :
        /\ started' = TRUE
        /\ IF imm THEN  \* _start(immediate=True): C's evaluate runs now, nested in P's
              /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.node = "ok"],
                                  !["C"] = IF AtBudget
                                     THEN Fresh([@ EXCEPT !.intr = IF inherit THEN r.intr ELSE TRUE], "start")
                                     ELSE [@ EXCEPT !.node = "start", !.parked = FALSE, !.st = TRUE,
                                        !.intr = IF inherit THEN r.intr ELSE TRUE]]
              /\ IF AtBudget THEN UNCHANGED <<cur, stk>> ELSE Push("C")
              /\ UNCHANGED <<obs, sent, selfUsed>>
           ELSE  \* _start: C's start is queued on P's dispatcher
              /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.node = "ok"],
                                  !["C"] = [@ EXCEPT !.drv = Append(@, <<"start", 0>>),
                                     !.intr = IF inherit THEN r.intr ELSE TRUE]]
              /\ UNCHANGED <<cur, stk, obs, sent, selfUsed>>
     ELSE IF ins = "mask" THEN
        /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.node = "ok", !.intr = FALSE]]
        /\ UNCHANGED <<cur, stk, started, obs, sent, selfUsed>>
     ELSE \* "enable" / "unmask": interruptible / SetInterruptible.on_any deliver via take_interrupt
        /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.intr = TRUE,
                                             !.node = IF r.irq THEN "int" ELSE "ok",
                                             !.dfr = IF r.irq THEN FALSE ELSE r.dfr]]
        /\ UNCHANGED <<cur, stk, started, obs, sent, selfUsed>>

\* a dispatcher task runs (its batch from the loop, or a flush nested in any drive)
Drive(f) ==
  LET r == fb[f]
      t == Head(r.drv)
      r1 == [r EXCEPT !.drv = Tail(@)]
      live == IF t[1] = "start" THEN r.exit = "none"
              ELSE (t[2] = r.pid /\ ~r.pdone)
                \/ (Break = "no_token_claim" /\ t[1] = "y" /\ r.exit = "none")
                \/ (Break = "no_deferred_token" /\ t[1] = "d" /\ r.exit = "none")
  IN /\ r.drv # <<>>
     /\ (t[1] # "d" \/ cur = "none") \* asyncio call_soon cannot run in a nested flush
     /\ IF ~live THEN fb' = [fb EXCEPT ![f] = r1] /\ UNCHANGED <<cur, stk>>
        ELSE IF t[1] = "start" THEN
           fb' = [fb EXCEPT ![f] = IF AtBudget THEN Fresh([r1 EXCEPT !.st = TRUE], "start")
                             ELSE [r1 EXCEPT !.node = "start", !.parked = FALSE, !.st = TRUE]]
           /\ IF AtBudget THEN UNCHANGED <<cur, stk>> ELSE Push(f)
        ELSE LET node == IF t[1] = "d" THEN t[3] ELSE "ok"
                 claimed == [Claim(r1, node) EXCEPT !.st = TRUE]
             IN /\ fb' = [fb EXCEPT ![f] = IF AtBudget THEN Fresh(claimed, node)
                                                 ELSE claimed]
                /\ IF AtBudget THEN UNCHANGED <<cur, stk>> ELSE Push(f)
     /\ UNCHANGED <<started, obs, sent, selfUsed>>

\* an async completion fires: a foreign thread first marshals onto the owning loop without
\* touching Park. On the loop, Async resume checks `not park.done`, then evaluates at once.
\* AtBudget installs a fresh queued Park after the old registration's claim. A stale deferred
\* step is checked against its own token by Drive; Break_no_deferred_token makes this red.
ExtResume(f) ==
  \E id \in fb[f].ext :
    LET r == fb[f]
        r1 == [r EXCEPT !.ext = @ \ {id}]
        hit == r.exit = "none" /\ ((id = r.pid /\ ~r.pdone) \/ Break = "no_park_done")
    IN /\ IF hit THEN fb' = [fb EXCEPT ![f] = IF AtBudget
                                               THEN Fresh(Claim(r1, "ok"), "ok")
                                               ELSE Claim(r1, "ok")]
                         /\ IF AtBudget THEN UNCHANGED <<cur, stk>> ELSE Push(f)
          ELSE fb' = [fb EXCEPT ![f] = r1] /\ UNCHANGED <<cur, stk>>
       /\ UNCHANGED <<started, obs, sent, selfUsed>>

\* someone calls P.interrupt_unsafe, from the loop or from inside any drive
Interrupt ==
  LET x == IntUnsafe(fb["P"], Running("P")) IN
  /\ ~sent /\ fb["P"].exit = "none"
  /\ sent' = TRUE
  /\ fb' = [fb EXCEPT !["P"] = IF x.now /\ AtBudget THEN Fresh(x.rec, "int") ELSE x.rec]
  /\ IF x.now /\ ~AtBudget THEN Push("P") ELSE UNCHANGED <<cur, stk>>
  /\ UNCHANGED <<started, obs, selfUsed>>

Settled(f) == fb[f].drv = <<>> /\ fb[f].ext = {} /\ (fb[f].exit # "none" \/ (f = "C" /\ ~started))
Finished == cur = "none" /\ \A f \in Fibers : Settled(f)
Done_ == Finished /\ UNCHANGED vars

Next ==
  \/ \E f \in Fibers : Drive(f) \/ LoopTop(f) \/ Eval(f) \/ ExtResume(f)
  \/ Interrupt
  \/ Done_

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

\* ---- invariants (docs/INVARIANTS.md) ----
LiveTasks(f) == {i \in 1..Len(fb[f].drv) :
                   IF fb[f].drv[i][1] = "start" THEN fb[f].exit = "none"
                   ELSE fb[f].drv[i][2] = fb[f].pid /\ ~fb[f].pdone}
Drives(f) == Cardinality({i \in 1..Len(stk) : stk[i] = f}) + (IF cur = f THEN 1 ELSE 0)

K1_ExitOnce      == \A f \in Fibers : fb[f].pubs <= 1
K2_ResumeOnce    == \A f \in Fibers : fb[f].wakes <= 1
\* one evaluate per fiber at a time, nested or not, and none queued while it runs or waits
K3_OneRunner     == \A f \in Fibers : Drives(f) + Cardinality(LiveTasks(f)) <= 1
K6_MasksHold     == \A f \in Fibers : (cur = f /\ fb[f].node = "int") => ~Masked(f)
K7_InterruptsLand == \A f \in Fibers : (fb[f].exit # "none" /\ fb[f].req) => fb[f].exit = "int"
K8_NoOrphans     == (fb["P"].exit # "none" /\ started) => fb["C"].exit # "none"

\* progress: every fiber that started publishes
AllPublish == <>Finished
=============================================================================
