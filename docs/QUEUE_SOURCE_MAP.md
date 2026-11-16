# Queue source map

Vendor pin: `289a53f7b1fc40049219c6e69022db2fb859c356`. All sources below are in
`vendor/effect-v4/packages/effect`. Python operational implementation: `src/peffect/queue.py`.

## Public inventory

All **39 value exports** and three model types (`Queue`, `Enqueue`, `Dequeue`) have operational
counterparts. Module duals become instance snake_case methods; `await` becomes `await_`.
Constructors live on `Queue[A, E]`; guards live in `peffect.queue`. Capability views return
exactly the same object. State/OfferEntry/Taker/Variance metadata and TS
Pipeable/Inspectable/toJSON spellings are excluded; no operational method is deferred.

| Vendor export / source line | Test name fragments |
| --- | --- |
| `isQueue` — Queue.ts:48 | constructors_guards |
| `isEnqueue` — Queue.ts:73 | constructors_guards |
| `isDequeue` — Queue.ts:94 | constructors_guards |
| `asEnqueue` — Queue.ts:118 | constructors_guards |
| `asDequeue` — Queue.ts:140 | constructors_guards |
| `make` — Queue.ts:480 | constructors_guards; overflow_strategy |
| `bounded` — Queue.ts:533 | constructors_guards; bounded_batch |
| `sliding` — Queue.ts:571 | constructors_guards; overflow_strategy; zero_sliding |
| `dropping` — Queue.ts:608 | constructors_guards; overflow_strategy |
| `unbounded` — Queue.ts:647 | constructors_guards; offer_all_copies |
| `offer` — Queue.ts:682 | bounded_batch; zero_capacity_all_strategies; completion_drains |
| `offerUnsafe` — Queue.ts:739 | creator_dispatcher; finalization_publishes; StrategyLedger; cancelled_reader_ownership |
| `offerAll` — Queue.ts:799 | bounded_batch; collect_waits; zero_batch_closing; producer_ownership |
| `offerAllUnsafe` — Queue.ts:850 | offer_all_copies; partial_batch; StrategyLedger; cancelled_reader_ownership |
| `fail` — Queue.ts:910 | closing_short_batch; completion_drains; shutdown_preserves |
| `failCause` — Queue.ts:943 | completion_filter_keeps |
| `failCauseUnsafe` — Queue.ts:989 | shutdown_preserves; failure_at_every_partial; StrategyLedger |
| `end` — Queue.ts:1050 | cancel_offer_after_end; closing_short_batch; completion_drains |
| `endUnsafe` — Queue.ts:1102 | await_waits; finalization_publishes |
| `interrupt` — Queue.ts:1147 | completion_drains |
| `shutdown` — Queue.ts:1187 | shutdown_discards; reentrant_shutdown |
| `shutdownUnsafe` — Queue.ts:1211 | shutdown_discards; shutdown_preserves; creator_dispatcher; StrategyLedger |
| `clear` — Queue.ts:1261 | zero_batch_closing; completion_filter_keeps; interrupt_each_observed; StrategyLedger |
| `takeAll` — Queue.ts:1308 | bounded_batch; completion_drains; interrupt_each_observed |
| `collect` — Queue.ts:1337 | collect_waits; cancel_offer_after_end; completion_filter_keeps |
| `takeN` — Queue.ts:1398 | take_n_count; counts_normalize; partial_batch; failure_at_every_partial; interrupt_each_observed |
| `takeBetween` — Queue.ts:1444 | counts_normalize; closing_short_batch; interrupt_each_observed |
| `take` — Queue.ts:1496 | reserves_before_resuming; resuming_blocked; interrupt_each_observed; reader_ownership |
| `poll` — Queue.ts:1534 | poll_preserves; zero_batch_closing; interrupt_each_observed; producer_ownership |
| `peek` — Queue.ts:1574 | peek_waits; offer_at_every_initial; offer_all_copies |
| `takeUnsafe` — Queue.ts:1630 | poll_preserves |
| `flushUnsafe` — Queue.ts:1683 | flush_wakes; creator_dispatcher |
| `flush` — Queue.ts:1722 | flush_wakes |
| `size` — Queue.ts:1812 | constructors_guards; offer_all_copies; StrategyLedger |
| `isFull` — Queue.ts:1841 | constructors_guards; StrategyLedger; producer_ownership |
| `sizeUnsafe` — Queue.ts:1892 | shutdown_discards; capacity_pathologies; StrategyLedger |
| `isFullUnsafe` — Queue.ts:1926 | capacity_pathologies; StrategyLedger |
| `into` — Queue.ts:1963 | into_completes |
| `await` — Queue.ts:1724 | await_waits; completion_filter_keeps; finalization_publishes; shutdown_discards |

## Lifecycle and ownership

Queue.ts:480–502 captures the creator dispatcher. :1997–2020 coalesces scheduled wakes;
:2022–2056 defines normalized batch readiness and atomic delivery; :2059–2092 owns waiter
registration/cancellation; :2094–2145 reserves pending messages before synchronous producer
reentry and rechecks capacity afterwards; :2161–2175 publishes Done before notification.
Python uses one private active variant (optional stored terminal outcome distinguishes Open
from Closing) and a terminal variant. Token-owned live ordered registries preserve JS Set
iteration during synchronous deletion/addition; Python dict mutation during iteration would fail.

## Upstream scenario ports and adaptations

- Queue.test.ts:18–35,283–308,355–375: registration windows swept at each observed initial operation boundary; no padding or fixed yields. A bounded dispatcher drain is a settle point.
- :37–111: guards, identity views, copied batch inputs, bounded backpressure.
- :113–231: cancellation during Closing, producer/taker reentry, reserve-before-resume, shutdown during refill, bounded capacity after reentrant offers.
- :234–405: batch thresholds, partial waiting, short Closing drain, normalized counts and batch boundaries. A bounded task drain detects spurious wake loops.
- :407–505: collect conservation, dropping/sliding policies, zero-capacity sliding handoffs and interrupted producers/consumers.
- :507–555: per-operation interrupt sweeps, with acceptance logged at the first continuation even if the later fiber Exit is interrupted. Stops at the first unreached boundary; bound exhaustion fails.
- :558–815: completion, buffer/pending producer drain, poll/peek, shutdown, exact unaccepted suffixes, first terminal wins and await semantics.
- :817–918: synchronous flush, retained buffers, no terminal transition, one creator-dispatcher task across repeated flushes and offers.
- :920–990: zero rendezvous, handoff slots, buffer-before-pending ordering, mixed Done/defect and closing batch drain.
- Stream.fromQueue scenarios (:569–580,685–697,763–776) assert Queue drain/terminal outcomes directly. Stream integration belongs to the Stream module and is not claimed.
- Queue.ts:1963–1991 has source-derived into success/fail/defect/interrupt coverage, including ignored boolean success values. Source-derived tests also force interruption inside producer resumption, continuation defects and newly registered takers during live iteration.

## Numeric compatibility

Counts follow Count.normalize. Capacities remain raw. Tests characterize fractional pending
batch overfill, fractional sliding removal (MutableList.ts:516 floors the removal count),
NaN admission and JS `capacity || 1` readiness, equality-based fullness, and the negative
takeN defect. Negative-capacity take can retry forever and is deliberately not started.
These inputs are separate from ownership-ledger capacities `{0, 1, 2, infinity}`.

## Type contracts

Positive fixtures check constructor, bottom, completion exclusion, each variance axis,
capability views, and into environment/error channels. Negative blocks 134–154 reject wrong
variance directions, capability operations, end without Done, wrong messages/errors, lost
ordinary errors/Done and lost producer requirements. Fresh batch results bind the receiver
separately to retain capability variance despite Python list invariance. A broad writer
`Enqueue[object, E]` returns object-typed leftovers in repository method style.
Use `Done[X | Y]` for completion payload unions: Pyright may retain a Done member when
inferring exclusion from `Done[X] | Done[Y]`. Runtime filtering removes all such members.

## Red-test corrections

1. Unsafe take results compare their Success value or Failure cause, because Python Exit
objects use identity equality. Vendor Queue.test.ts:621–640/Queue.ts:1630–1647 asserts the
contained outcome; no payload or cause assertion was removed.
2. The offerAll registration sweep records its prefix check count before shutdown cleanup.
The previous baseline included checks after the producer had been rejected by cleanup,
outside the registration window tested by Queue.test.ts:18–35. Every reached registration
boundary still injects a release, and every injection must complete the producer and reader.
3. Mutation exploration found a generated-coverage gap for a zero-capacity batch drained
without cancellation. The minimal intent sequence `capacity=0; batch(3)` is retained as an
explicit property example. Assertions and generated domains were preserved.

## Proof limits

Tests execute real Queue methods and fibers. Independent per-ID ledgers distinguish consumer
acceptance, pending producer ownership, buffered ownership, rejection, sliding eviction,
withdrawal and shutdown discard. They check FIFO and accounting after commands. Separate
controlled sweeps establish interruption evidence. The vendor differential uses both real
implementations, not a prototype. Sensitivity is checked in isolated package copies, including
property-only mutants and green controls. These are bounded observations, not a formal Queue
proof. The unchanged kernel model abstracts away message ownership and callback values.
Atomic delivery ends at the first accepting continuation; later effects are not transactional.

## Per-test anchors

| Test | Source contract |
| --- | --- |
| `tests/test_queue_upstream.py:84` — `test_constructors_guards_and_same_object_capability_views` | 37-79; Queue.ts:429-644,1812-1927. All five constructors and guards. |
| `tests/test_queue_upstream.py:108` — `test_offer_all_copies_input_and_retains_fifo_at_batch_boundaries` | 82-89,234-242,377-389,407-415. |
| `tests/test_queue_upstream.py:125` — `test_bounded_batch_waits_and_releases_only_its_remaining_suffix` | 91-111,469-488: interruption leaves the admitted prefix owned by Queue. |
| `tests/test_queue_upstream.py:146` — `test_cancel_offer_after_end_withdraws_only_its_owned_suffix` | 113-139: cancelling the last pending offer can finalize Closing. |
| `tests/test_queue_upstream.py:166` — `test_rendezvous_reserves_before_resuming_reentrant_producer` | 157-186. First consumer owns the message even if producer immediately takes. |
| `tests/test_queue_upstream.py:180` — `test_resuming_blocked_producer_does_not_enqueue_twice` | 141-155. |
| `tests/test_queue_upstream.py:193` — `test_reentrant_shutdown_settles_other_offer_and_preserves_removed_value` | 188-209: shutdown from a resumed producer occurs inside the taking step. |
| `tests/test_queue_upstream.py:208` — `test_reentrant_offer_cannot_overfill_released_capacity` | 211-231. Recompute capacity after producer callback reentry. |
| `tests/test_queue_upstream.py:225` — `test_partial_batch_does_not_wake_or_spin_and_later_completes` | 244-281,310-329. Empty task queue is an explicit scheduler settle point. |
| `tests/test_queue_upstream.py:242` — `test_closing_short_batch_drains_before_terminal_failure` | 331-353,668-683. |
| `tests/test_queue_upstream.py:267` — `test_counts_normalize_independently_and_done_precedes_zero_bounds` | 391-405; Queue.ts:1444-1452,2022-2038. min > max is not rejected. |
| `tests/test_queue_upstream.py:281` — `test_take_n_count_and_capacity_threshold_are_source_faithful` | Queue.ts:1398-1400,2052-2056: n > capacity uses capacity readiness. |
| `tests/test_queue_upstream.py:298` — `test_overflow_strategy_results_and_retained_order` | 417-435. |
| `tests/test_queue_upstream.py:309` — `test_zero_sliding_discards_without_waiter_and_keeps_newest_waiting_handoff` | 437-467: offerAll schedules; the following single offer releases synchronously. |
| `tests/test_queue_upstream.py:327` — `test_cancelled_taker_does_not_steal_the_next_message` | 490-505; cancellation removes only that taker. |
| `tests/test_queue_upstream.py:343` — `test_completion_drains_registered_batches_and_single_offers_in_order` | 558-619,668-683,763-776. Stream case adapted to direct Queue reads. |
| `tests/test_queue_upstream.py:375` — `test_collect_waits_then_completes_without_duplication` | 407-415,569-580; collect removes Done, not real failures. |
| `tests/test_queue_upstream.py:388` — `test_poll_preserves_some_none_and_swallows_terminal_failures_only_as_empty` | 621-640; Queue.ts:1534-1545 (including arbitrary terminal causes). |
| `tests/test_queue_upstream.py:407` — `test_peek_waits_without_removing_and_ignores_pending_rendezvous_offer` | 642-666; Queue.ts:1574-1589: peek only sees buffered values. |
| `tests/test_queue_upstream.py:430` — `test_shutdown_discards_buffer_settles_suffix_and_interrupts_waiters` | 685-756; shutdown is immediate, whereas fail/end/interrupt drain. |
| `tests/test_queue_upstream.py:453` — `test_shutdown_preserves_first_closing_or_completed_cause` | 734-761; Queue.ts:989-1015,1211-1236. |
| `tests/test_queue_upstream.py:468` — `test_await_waits_for_drain_and_can_be_cancelled_independently` | 778-815; cancelled awaiter cannot cancel Queue or another awaiter. |
| `tests/test_queue_upstream.py:486` — `test_completion_filter_keeps_real_failures_and_order` | 965-971; Queue.ts:1261-1272,1337-1353,1724-1740; Pull.ts:202-220. |
| `tests/test_queue_upstream.py:506` — `test_flush_wakes_synchronously_without_draining_or_completing` | 817-849,884-918. |
| `tests/test_queue_upstream.py:530` — `test_creator_dispatcher_coalesces_even_across_flush_and_other_fiber_scheduler` | 851-882; Queue.ts:480-502,2011-2020. Creator dispatcher owns wake tasks. |
| `tests/test_queue_upstream.py:554` — `test_zero_offer_after_buffered_handoff_waits_for_another_taker` | 920-949. Zero capacity has transient buffer but no spare admission slot. |
| `tests/test_queue_upstream.py:569` — `test_zero_take_all_consumes_buffer_before_pending_offer` | 951-963. |
| `tests/test_queue_upstream.py:581` — `test_zero_batch_closing_drain_finishes_using_all_nonblocking_receivers` | 973-990: clear/poll each consume one pending rendezvous element. |
| `tests/test_queue_upstream.py:596` — `test_into_completes_queue_from_producer_exit_without_enqueuing_result` | Queue.ts:1963-1991. into restores producer interruptibility then publishes masked. |
| `tests/test_queue_upstream.py:631` — `test_capacity_pathologies_are_literal_vendor_characterizations` | Queue.ts:739-778,850-880,1926,2052; not the normal-capacity property domain. |
| `tests/test_queue_upstream.py:657` — `test_finalization_publishes_done_before_reentrant_awaiters_and_takers` | Source-derived Queue.ts:2161-2175: terminal state precedes all callbacks. |
| `tests/test_queue_upstream.py:681` — `test_zero_capacity_all_strategies_can_handoff_to_registered_taker` | Queue.ts:739-765,850-879. A waiting taker is a zero-capacity admission slot. |
| `tests/test_queue_upstream.py:696` — `test_into_ignores_every_producer_success_value` | Queue.ts:1987: onSuccess always calls end, including boolean results. |
| `tests/test_queue_upstream.py:705` — `test_fractional_sliding_trims_a_normalized_removal_count` | Queue.ts:858-864 delegates to MutableList.ts:516 (floor, not ceil). |
| `tests/test_queue_upstream.py:713` — `test_nan_capacity_uses_js_falsey_readiness_and_still_schedules_empty_batch` | Queue.ts:868-882,2052-2056: NaN <= 0 is false; NaN || 1 is one. |
| `tests/test_queue_interleavings.py:55` — `test_interrupt_each_observed_operation_preserves_exactly_one_owner` | 507-555, extended to acceptance logging rather than only successful Exit. |
| `tests/test_queue_interleavings.py:103` — `test_offer_at_every_initial_wait_registration_boundary_cannot_be_lost` | 283-308. Includes arrival before check, between checks, and before async registration. |
| `tests/test_queue_interleavings.py:140` — `test_capacity_release_at_every_offer_all_registration_boundary_cannot_be_lost` | 18-35; Queue.ts:799-825: admission and pending suffix ownership share a step. |
| `tests/test_queue_interleavings.py:182` — `test_failure_at_every_partial_batch_registration_boundary_short_drains` | 355-375; termination before registration must trigger the readiness recheck. |
| `tests/test_queue_interleavings.py:222` — `test_reentrant_producer_interrupt_still_delivers_to_first_accepting_continuation` | Queue.ts:2042-2049,2094-2145. Deliberately interrupt INSIDE removal/refill. |
| `tests/test_queue_interleavings.py:252` — `test_throwing_resumed_producer_continuation_does_not_corrupt_queue_ownership` | Source-derived Queue.ts:2109-2145. Producer's own defect does not undo admission. |
| `tests/test_queue_interleavings.py:270` — `test_taker_reentry_registers_another_waiter_during_live_release_iteration` | Source-derived Queue.ts:1997-2009: deletion precedes synchronous resume/reentry. |
| `tests/test_queue_props.py:262` — `test_suspended_producer_ownership_survives_generated_drain_cancel_and_completion` | Executable public worker example |
| `tests/test_queue_props.py:363` — `test_cancelled_reader_ownership_never_steals_generated_offers` | Parked reader FIFO + explicit dispatcher settle after each command.  Unlike the producer property, consumers are concurrent here. Offers are the nonblocking variants, so every ID is accepted, rejected or slid immediately. Each successful consumer is one terminal ledger owner, including readers completed before the driver attempts cancellation. |
| `tests/test_queue_worker.py:8` — `test_composed_worker_example_completes_all_its_controlled_scenarios` | Executable public worker example |
