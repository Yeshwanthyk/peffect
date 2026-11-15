---------------------------- MODULE FiberKernel ----------------------------
(* The fiber kernel's park / resume / interrupt / children protocol.

   Code: src/peffect/fiber.py (Fiber, _await_all), src/peffect/core.py (Async, Failure,
   SetInterruptible). One asyncio loop runs one callback at a time and Fiber._drive never
   awaits, so every action below is atomic with respect to every other fiber and callback.
   The one exception, code a node calls synchronously inside the drive (an Async `register`)
   calling interrupt_unsafe on its own fiber, is the `self` choice in Park.

   Two fibers: P forks C, parks, and ends while C may still run. C parks, masks, parks again
   inside the mask, and unmasks. An interrupter interrupts P once, at any loop turn.
   Async completions (resume callbacks) arrive at any loop turn, late ones included.     *)
EXTENDS Naturals, Sequences

CONSTANT Break  \* "none" = the code as it is; the other values mirror scratch breaks (README)

Fibers == {"P", "C"}
Prog == [f \in Fibers |->
           IF f = "P" THEN <<"fork", "park">>
           ELSE <<"park", "mask", "park", "unmask">>]
End(f) == Len(Prog[f]) + 1
WAIT == 99  \* pc and park id while P is parked on _await_all in Fiber._done

VARIABLES
  cur,      \* the fiber whose _drive is running, or "none" (the loop is between callbacks)
  fb,       \* per-fiber state, fields below
  started,  \* C has been forked (fiber._start)
  obs,      \* P's _await_all observer is registered on C (Fiber.add_observer)
  sent,     \* the interrupter has called P.interrupt_unsafe
  selfUsed  \* a register has interrupted its own fiber (bounded to once)
vars == <<cur, fb, started, obs, sent, selfUsed>>

(* fb[f] fields and the Fiber field each models:
   pc      next instruction (straight-line Prog), WAIT while waiting on children
   node    the node the running drive evaluates next: "start" | "ok" | "int" | "await" | "none"
   intr    Fiber.interruptible          irq     Fiber.interrupted is not None
   dfr     Fiber._deferred              parked  Fiber._parked is not None
   pid     id of the current Park       pdone   Park.done
   wakes   resumes delivered for the current Park (K2)
   ext     ids of Parks whose async completion is still pending (resume callbacks)
   drv     scheduled _drive calls (loop.call_soon), with the node each starts on
   ending  Fiber._ending                exit    Fiber._exit ("none" | "ok" | "int")
   pubs    times _publish ran (K1)      req     an interrupt was requested before the exit *)

Init ==
  /\ cur = "none"
  /\ fb = [f \in Fibers |->
            [pc |-> 1, node |-> "none", intr |-> TRUE, irq |-> FALSE, dfr |-> FALSE,
             parked |-> FALSE, pid |-> 0, pdone |-> TRUE, wakes |-> 0, ext |-> {},
             drv |-> IF f = "P" THEN <<"start">> ELSE <<>>,
             ending |-> "none", exit |-> "none", pubs |-> 0, req |-> FALSE]]
  /\ started = FALSE /\ obs = FALSE /\ sent = FALSE /\ selfUsed = FALSE

Masked(f) == f = "C" /\ fb[f].pc \in {3, 4}  \* between C's "mask" and "unmask"

\* Fiber.interrupt_unsafe -> _interrupt_parked -> take_interrupt, _wake
IntUnsafe(r, running) ==
  IF r.exit # "none" THEN r
  ELSE IF ~running /\ r.parked /\ (r.intr \/ Break = "no_mask_check") /\ ~r.pdone
       THEN [r EXCEPT !.irq = TRUE, !.req = TRUE, !.pdone = TRUE, !.dfr = FALSE,
                      !.parked = FALSE, !.drv = Append(@, "int"), !.wakes = @ + 1]
       ELSE [r EXCEPT !.irq = TRUE, !.req = TRUE, !.dfr = TRUE]

\* Async resume -> Fiber._wake
Wake(r) == [r EXCEPT !.pdone = TRUE, !.parked = FALSE, !.drv = Append(@, "ok"), !.wakes = @ + 1]

\* Fiber._drive tail: parked while an interrupt waited (it came in during register)
DriveTail(r) ==
  IF r.dfr /\ r.intr /\ r.parked
  THEN [r EXCEPT !.pdone = TRUE, !.dfr = FALSE, !.parked = FALSE,
                 !.drv = Append(@, "int"), !.wakes = @ + 1]
  ELSE r

\* Fiber._drive entry (scheduled by _start, _wake, _interrupt_parked)
Drive(f) ==
  LET r == fb[f] IN
  /\ cur = "none" /\ r.drv # <<>>
  /\ cur' = f
  /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.node = Head(r.drv), !.drv = Tail(r.drv), !.parked = FALSE]]
  /\ UNCHANGED <<started, obs, sent, selfUsed>>

\* Fiber._drive loop top: `if self._deferred and self.interruptible` -> Failure(take_interrupt())
LoopTop(f) ==
  LET r == fb[f] IN
  /\ cur = f /\ r.dfr /\ r.intr /\ Break # "no_loop_top"
  /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.node = "int", !.dfr = FALSE]]
  /\ UNCHANGED <<cur, started, obs, sent, selfUsed>>

Ready(f) == cur = f /\ (~(fb[f].dfr /\ fb[f].intr) \/ Break = "no_loop_top")

\* Fiber._done, then _publish (single call site). o = obs after any cancel ran.
Done(f, e, o) ==
  LET r == fb[f]
      e2 == IF r.ending = "none" THEN e ELSE IF e = "int" THEN "int" ELSE r.ending
      liveC == f = "P" /\ started /\ fb["C"].exit = "none" /\ Break # "no_wait"
      p == fb["P"]
      pub == [r EXCEPT !.exit = e2, !.pubs = @ + 1, !.node = "none"]
      \* C's _publish calls P's _await_all observer: one_done -> resume(UNIT)
      notify == f = "C" /\ o /\ p.pid = WAIT /\ Break # "no_notify" /\ (~p.pdone \/ Break = "no_park_done")
      \* the pre-fix _done published P from C's observer, outside P's run loop
      oldPub == f = "C" /\ Break = "old_done" /\ p.pc = WAIT /\ p.exit = "none"
  IN
  IF ~liveC THEN
     /\ fb' = IF f = "P" THEN [fb EXCEPT !["P"] = pub,  \* no_wait: interrupt C, don't wait
                                         !["C"] = IF Break = "no_wait" THEN IntUnsafe(@, FALSE) ELSE @]
              ELSE [fb EXCEPT !["C"] = pub,
                              !["P"] = IF oldPub THEN [p EXCEPT !.exit = p.ending, !.pubs = @ + 1]
                                       ELSE IF notify THEN Wake(p) ELSE p]
     /\ obs' = IF f = "C" THEN FALSE ELSE o
     /\ cur' = "none"
     /\ UNCHANGED <<started, sent, selfUsed>>
  ELSE IF Break = "old_done" THEN
     /\ fb' = [fb EXCEPT !["P"] = [r EXCEPT !.ending = e2, !.pc = WAIT, !.node = "none"],
                         !["C"] = IntUnsafe(@, FALSE)]
     /\ obs' = TRUE
     /\ cur' = "none"
     /\ UNCHANGED <<started, sent, selfUsed>>
  ELSE  \* remember the exit, interrupt C, and evaluate _await_all inside this drive
     /\ fb' = [fb EXCEPT !["P"] = [r EXCEPT !.ending = e2, !.pc = WAIT, !.node = "await"],
                         !["C"] = IntUnsafe(@, FALSE)]
     /\ obs' = o
     /\ UNCHANGED <<cur, started, sent, selfUsed>>

\* core.py Async._eval: register (which may interrupt its own fiber), _park, end of drive
Park(f) ==
  \E self \in (IF selfUsed THEN {FALSE} ELSE BOOLEAN) :
    LET r == fb[f]
        r1 == IF self THEN IntUnsafe(r, TRUE) ELSE r
        r2 == [r1 EXCEPT !.ext = @ \cup {r.pc}, !.parked = TRUE, !.pid = r.pc, !.pdone = FALSE,
                         !.wakes = 0, !.pc = @ + 1, !.node = "none"]
    IN /\ fb' = [fb EXCEPT ![f] = DriveTail(r2)]
       /\ cur' = "none"
       /\ selfUsed' = (selfUsed \/ self)
       /\ UNCHANGED <<started, obs, sent>>

\* fiber._await_all: register observers on C, park (interruptible), end of drive
AwaitPark ==
  LET r == fb["P"]
      r2 == [r EXCEPT !.parked = TRUE, !.pid = WAIT, !.pdone = FALSE, !.wakes = 0, !.node = "none"]
  IN /\ fb' = [fb EXCEPT !["P"] = DriveTail(r2)]
     /\ obs' = TRUE
     /\ cur' = "none"
     /\ UNCHANGED <<started, sent, selfUsed>>

\* one node of Fiber._drive's loop (`cur = cur._eval(self)`)
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
     ELSE IF ins = "fork" THEN
        /\ started' = TRUE
        /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.node = "ok"],
                            !["C"] = [@ EXCEPT !.drv = Append(@, "start")]]
        /\ UNCHANGED <<cur, obs, sent, selfUsed>>
     ELSE IF ins = "mask" THEN
        /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.node = "ok", !.intr = FALSE]]
        /\ UNCHANGED <<cur, started, obs, sent, selfUsed>>
     ELSE \* "unmask": SetInterruptible.on_any delivers a waiting interrupt via take_interrupt
        /\ fb' = [fb EXCEPT ![f] = [r EXCEPT !.pc = @ + 1, !.intr = TRUE,
                                             !.node = IF r.irq THEN "int" ELSE "ok",
                                             !.dfr = IF r.irq THEN FALSE ELSE r.dfr]]
        /\ UNCHANGED <<cur, started, obs, sent, selfUsed>>

\* an async completion fires: Async resume checks `not park.done`
ExtResume(f) ==
  \E id \in fb[f].ext :
    LET r == fb[f]
        r1 == [r EXCEPT !.ext = @ \ {id}]
        hit == (id = r.pid /\ ~r.pdone) \/ (Break = "no_park_done" /\ r.exit = "none")
    IN /\ cur = "none"
       /\ fb' = [fb EXCEPT ![f] = IF hit THEN Wake(r1) ELSE r1]
       /\ UNCHANGED <<cur, started, obs, sent, selfUsed>>

\* someone calls P.interrupt_unsafe from another callback
Interrupt ==
  /\ cur = "none" /\ ~sent /\ fb["P"].exit = "none"
  /\ sent' = TRUE
  /\ fb' = [fb EXCEPT !["P"] = IntUnsafe(@, FALSE)]
  /\ UNCHANGED <<cur, started, obs, selfUsed>>

Settled(f) == fb[f].drv = <<>> /\ fb[f].ext = {} /\ (fb[f].exit # "none" \/ (f = "C" /\ ~started))
Finished == cur = "none" /\ \A f \in Fibers : Settled(f)
Done_ == Finished /\ UNCHANGED vars

Next ==
  \/ \E f \in Fibers : Drive(f) \/ LoopTop(f) \/ Eval(f) \/ ExtResume(f)
  \/ Interrupt
  \/ Done_

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

\* ---- invariants (docs/INVARIANTS.md) ----
K1_ExitOnce      == \A f \in Fibers : fb[f].pubs <= 1
K2_ResumeOnce    == \A f \in Fibers : fb[f].wakes <= 1
K3_OneRunner     == \A f \in Fibers : Len(fb[f].drv) + (IF cur = f THEN 1 ELSE 0) <= 1
K6_MasksHold     == \A f \in Fibers : (cur = f /\ fb[f].node = "int") => ~Masked(f)
K7_InterruptsLand == \A f \in Fibers : (fb[f].exit # "none" /\ fb[f].req) => fb[f].exit = "int"
K8_NoOrphans     == (fb["P"].exit # "none" /\ started) => fb["C"].exit # "none"

\* progress: every fiber that started publishes
AllPublish == <>Finished
=============================================================================
