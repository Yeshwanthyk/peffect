# Untangle log: kernel boundary (tier 1)

Scope: structure only, the line between the kernel (`src/peffect/core.py`, `fiber.py`, `cause.py`)
and everything built on it. Comment and test cleanup were not selected. `core.py`'s length (715
lines) is not logged: it holds the seven nodes and their frames, which change together.

## Fixes, each above the problems it solves

- Write the boundary down as rules B1–B3 (`docs/INVARIANTS.md`, "Kernel boundary") and enforce
  them with `scripts/check_boundaries.py`, tested in `tests/test_boundaries.py` and run by
  `verify-peffect`. Kernel code is unchanged.
  - `docs/ROADMAP.md:10` (before this change) stated the rule, "only the kernel knows about
    fibers", in prose only; nothing checked it, and the planned check covered only `_private`
    names and Fiber fields.
  - Kernel internals have public-looking names, so the type checker's private-name rule doesn't
    cover them: `core.py:198` `Park`, `core.py:210` `Async`, `core.py:251` `Frame`,
    `core.py:267` `Then`, `core.py:285-404` the node/frame classes.
  - `fiber.py:54-60` `Fiber` exposes run-loop state without underscores (`children`,
    `interrupted`, `interruptible`, `running`, `stack`), and `fiber.py:136-200` exposes unsafe
    primitives (`interrupt_unsafe`, `take_interrupt`, `exit`, `add_observer`) next to the three
    public effects (`await_`, `join`, `interrupt`).

## Left in place, with the reason

- `src/peffect/__init__.py:3-32` (imports) and `:34-72` (`__all__`) list every public name twice.
  That is the Python convention for re-exports, and the lint now fails with exit 2 if an `__all__`
  name has no matching import, so the two can't drift silently.
- `tests/test_kernel_props.py:28-30` imports `Async`, `WithFiber`, `_start`, and
  `:158-160`/`:292-301` read private fiber state. These are white-box tests of the run loop;
  `tests/` is outside the lint's scope on purpose.

## Special cases in scope

Before: none enforced (the rule was prose). After, the lint has six, each on purpose:

1. `ESCAPE_HATCH`: `peffect.core.WithFiber` is importable though it is not in `__all__`.
2. `FIBER_PUBLIC`: the four Fiber members code outside the kernel may use.
3. `KERNEL_FILES`: the four kernel files are exempt, by exact path.
4. `tests/` and `typetests/` are exempt, by not being in the default paths.
5. A `WithFiber` callback that is neither a lambda nor a named def fails (it can't be checked).
6. `getattr`/`setattr`/`delattr`/`vars` on a known Fiber fails (a string name hides the member).

## Independent review corrections (2026-11-15)

- Separate definition-time expressions from body scope in the checker visitor.
  - `scripts/check_boundaries.py:198` previously visited defaults/decorators after entering the
    body scope, missing an outer package reach and misidentifying an ordinary lambda default.
- Validate the whole literal export declaration before constructing its mapping.
  - `scripts/check_boundaries.py:60` previously discarded malformed export metadata, allowing
    an empty or incomplete map to pass. Unsupported export mutations now fail closed.

Both CLI regression tests were red before the correction and green afterwards. Sol's fresh
structural search found no further tier 1 coupling problem worth fixing. The six policy
exceptions above are not a measured reduction in special cases.

## Context review corrections

Sol reproduced two dependency-evidence mutation holes in `Context.services` and `provide.ctx`.
Both now use private storage and getter-only properties. Four runtime replacement/deletion
probes failed before the fix; negative typing blocks 15–18 also failed the harness before it.
No new lint rule was needed. The explicit contravariant Context TypeVar and its narrow UP046
configuration remain: Fable and Sol independently found inferred covariance unsound.

The type harness now recognizes indented numbered blocks; its regression removes each block's
actual diagnostics in turn and requires rejection. The interrupt-at-install test observes the
fiber after the real swap; a scratch mutation omitting installation now fails its assertion.
Verification: `/var/folders/rf/y4ygjrb13bd2nzm69xrc22h40000gn/T/peffect-verify/20261115T155931Z-76623`.
Upstream test coverage reconciliation remains pending; no broader primitive redesign was made.

## Upstream batches A+B (Context, finalization)

Fixed at the owning primitives, against the committed upstream tests (1c2828b), with no new
architecture: `OnExit` now turns a raising callback into that finalizer's defect and elects
defects when combining causes (one method, `Cause.elect_defects`); `on_error` and `on_interrupt`
are `on_exit` compositions; `Context` gained `empty`, empty-operand identity in `|`, and
upstream equality/hash. Two scratch breaks went red and were restored: dropping the election
(4 tests, including the property reference) and letting a raising callback escape `OnExit`
(2 upstream tests). `Die.defect` widened to `object` because election moves a Fail's value,
which need not be an exception, into a Die.

## Approved race, traversal and Queue refactor (2026-11-16)

Scope supplied by the coordinator; comments/test cleanup and releases excluded. Existing
Queue/setup/release/research edits and parent race/timeout tests remain in place.

- Split race algorithms, sharing only lifecycle, racer registration/removal and loser interrupts.
  - Needless link: `src/peffect/fiber.py:848` previously selected algorithms with `first_exit`
    (four reads); raceAll eagerly materializes input, raceAllFirst lazily closes its iterator.
  - Lifecycle ownership now sits in `_racing`; no new class, cast, suppression or protocol write.
- Separate ready-Exit traversal from the Async window, then separate record/pump/finish.
  - Surplus phase states: former `fiber.py:702-704` optional parent/resume exist only before
    registration. `_run_window` now requires both; the two phase assertions are unnecessary.
  - Hidden links: former `fiber.py:732-797` observer/refill/failure handling must retain nested
    callback order and interrupt siblings even when the unbounded pump has already finished.
- Separate admission, removal, producer completion and state recheck.
  - Hidden link: former `src/peffect/queue.py:531-558` reserves messages and advances batch
    offsets before resuming a producer, which may synchronously take, offer or shut down.
  - Keep raw fractional `free == 0`, live offer iteration and the Done recheck unchanged.

Characterization first: `test_unbounded_fail` ports Effect.test.ts:891 using real TestClock.
Old code passes; isolated removal of the observer's `go()` fails with done=[1,2,3,4,5], expected
[1,2,3]. Refactored code passes; removing `record`'s fail-fast `finish()` gives the same red.
No production mutation was made. Root artifacts were neither removed nor imported.

Measured structure (same Harrow/closure script on actual before and after files):
- Unnecessary representations: one algorithm mode flag and two optional phase cells → zero.
- Race family cognitive sum 25 → 25; deepest closure 4 → 2. The gain is algorithm locality,
  not a lower aggregate score. Explicit lazy iterator close and cleanup branches remain required.
- Traversal family cognitive sum 67 → 55 (5 inline + 1 step + 49 window); max function 36 → 15;
  nonlocal names in the window family 6 → 4; statement nesting stays 4; nested defs 7 → 9 across
  both phases. More named callbacks expose responsibilities; the remaining reentry state is real.
- Queue admission/completion family cognitive sum 21 → 15 (11 release + 4 admit + 0 accept),
  max statement nesting 4 → 2. Harrow includes the two lambdas omitted by the closure-family
  script: traversal sum 69 → 57; Queue sum 21 → 17. Scores are static proxies, not speed claims.

Real checkout proof: full `.claude/skills/verify-peffect/run.sh`, no deselections, every step 0;
892 pytest tests passed (one existing TestClockService collection warning). Evidence:
`/var/folders/rf/y4ygjrb13bd2nzm69xrc22h40000gn/T/peffect-verify/20261116T053945Z-9688`.
Doctor resolves `peffect` to `/Users/yesh/code/personal/peffect/src/peffect/__init__.py`.
Formal runner: exit 0, 2232 states, depth 21, 24 linked tests; seven Break configs each exit 1.
Required isolated `_done` break: formal model remains green, three linked runtime tests fail;
production fiber SHA256 unchanged. The model remains bounded to its documented two fibers.

Isolated behavior mutations all exit 1: winner-before-interrupt order; eager/lazy startup
cutoffs; iterator close (including throwing-close regression); loser await; traversal refill,
fail-fast and result order; Queue remove/reserve-before-resume and fractional free semantics.
These probes import isolated src copies; baseline and final verification import the real src.
Parent `tests/test_race_timeout_upstream.py` is byte-identical to the starting snapshot.
Pillow not run: executable unavailable on PATH. Fresh independent review belongs to the parent;
this implementation delegate did not spawn children. No benchmark, arbitrary interleaving proof,
commit, push, version change or release is claimed.

Frozen implementation delta, owned-file hashes, exact metrics, scripts and logs:
`/Users/yesh/.t3/scratch/2026-11-15-what-if-we-wanted-to-20120c4d/work/sol-implementation/`.

## Full-source cleanup checkpoint ledger (coordinator GO)

Refactoring only; assertions/comments preserved. Shape and initial real EffectJS baseline:
`/tmp/peffect-cleanup-shape/SHAPE.md` (21/21 applications matched). Pin: 289a53f7b1fc40049219c6e69022db2fb859c356.
This section is appended; prior log content belongs to earlier work and is preserved.

Problems/dispositions to close, separately from correctness:
- Hidden links, `fiber.py:721`: traversal completion/admission and synchronous callback family.
  Compare closure-owned closed admission/failure against current sentinel and a state class.
- Hidden links, `semaphore.py:135`: acquisition/register/wake/recheck/cancel ownership.
  Compare a request object; retain only with concrete coordination-cost evidence.
- Hidden links, `core.py:67`, `layer.py:63`: independent 0..20 overload families can drift.
  Add a lightweight source-only consistency gate, not a typing-erasing shared base.
- Surplus decisions, `context.py:115`: repeated builtin-float checks inside exact numeric guard.
  Pin numeric/custom equality separately from Python structural-container adaptation.
- Interpreter complexity, `fiber.py:249`: evaluate's yield/resume/interrupt flags are not
  interchangeable; reject helper-only branch relocation with a phase-by-phase explanation.
- Nested admission, `queue.py:331`, bulk admission `:378`: compare in-place flattening;
  preserve NaN/fractional/raw capacities, iterator consumption and reentrant publication.
- Duplicated scale selection, `duration.py:249`: compare nano/micro data with two parser branches.
- Missing gate, `pyproject.toml`: all-source metrics and family burden need repeatable,
  fail-closed checks without suppressing the audit's retained findings.

Each small production refactor must stop for focused Python tests AND fresh pinned EffectJS
comparison before the next. Retention is not called a fix; no universal equivalence is claimed.

### Cleanup outcome and proof

- Traversal: removed independently synchronized `done` and the UNIT cancellation sentinel.
  `can_start` derives admission from index, failure and cancellation. Pump owns index/pending,
  observer owns live removal, record owns failure folding, cancellation alone owns its flag.
  Failure/cancellation are published before worker interrupts can reenter. Per-pump paused
  ownership remains. Rejected state class adds immutable-input fields and still needs closures.
  Lexical family 11→12 scopes, cognitive 51→52, depth 2→2, direct captured bindings 5→5:
  better state meaning, NOT a claimed score reduction. File decision contributions 135→137.
- Context: exact builtin numeric guard retained; two repeated float decisions removed.
  Cyclomatic/cognitive 17/16→15/16. Nine paired numeric cases match real JS; an 81-pair
  bool/int/float/NaN matrix and custom/float-subclass equality are explicit Python gates.
- Queue: normal single-offer admission is a guard return; cognitive 16→10, cyclomatic 10→10.
  Bulk admission retained at 11/15: ceil/islice changes raw NaN/fractional/iterator behavior;
  shared single offers change scheduling. Reserve/remove/offset-before-resume and live
  registry iteration stay unchanged. Eighteen paired capacity/strategy cases match.
- Duration: nano/micro scales now share the exact parser/constructor via data, as existing
  millisecond unit scales do. Cyclomatic/cognitive 10/12→9/10. Fifteen real paired input
  cases match, including huge/rounded nanos, JS whitespace, infinities and invalid values.
- Semaphore retained (7 scopes/cognitive 5): request-class conversion preserves every phase
  plus extra back-references. Semaphore owns counts/membership; waiter owns optional resume.
  Paired overtaking and wake/yield/steal/cancel traces match; exit-frame and reserving-wake
  scratch breaks are red. Retention is not called a fix.
- Interpreter retained at 15/15: resumption allowance expires after one operation; failed
  dispatch suppression lasts the slice. Helper extraction only relocates these obligations.
- Pipe: exact AST 0..20 chain gate guards both overload families. Eight signature mutations
  are rejected; existing positive zero/twenty and negative chain/twenty-one typing fixtures
  pass. Public inference remains separate and precise; generator/mixin rejected.
- Added fail-closed native Harrow policy and sensitivity tests, public pin
  `uv tool install harrow-code==0.4.1`. Every source file, named hotspot, file aggregate,
  function inventory and reported callback family is checked; extracted helpers require
  review even with lower scores. Unsupported/incomplete evidence is a failure, not a skip.
  No new analyzer, dependency, cast, suppression, runtime type or public API.

Per-unit focused Python + fresh real JS/app checkpoints, before the next production unit:
Context 46 pass; traversal/scheduler/race 42 pass; Queue 126 pass; Duration 108 pass;
pipe/types/Semaphore 40 pass. Each application checkpoint matched 21/21. Commands, finite
inputs and raw expected/actual events are frozen below. Final focused set: 34 pass, including
11 real paired probes and two Python-adaptation tests (13 parity-module cases total).
The final capacity-one Queue worker matches JS event order and drains [1,2,3]; the actual
Python Queue CLI also completes all controlled scenarios. Queue offset publication has a
separate white-box witness on both real runtimes, not a new public API promise.

Final full verifier: every step exit 0; 926 tests pass with the existing TestClockService
collection warning. Evidence `/tmp/peffect-cleanup-shape/full-verifier-final/20261116T132353Z-96392`.
Formal main: all rules hold, 2232 states/depth 21, 24 linked tests pass; seven Break configs
exit 1. Isolated _done break leaves TLC green and makes three linked runtime tests fail.
Ten focused source-ordering mutations exit 1; production source hashes stay unchanged.
One initial Queue mutation test selection missed the violation; the existing duplicated-
enqueue test detects it. The initial selection and corrected red evidence are both retained.

All-source native decision contributions 576→575, cognitive sum 727→720; these are static
proxies, not performance claims. Two redundant traversal encodings and three repeated
numeric/unit decisions removed; required edge cases retained. Raw audit still shows 215
findings (213 source, two parent-owned packaging), zero suppressed/baselined. Every source
record has a fix/retention disposition; no dead-library-API deletion or comment/test pruning.
Pillow not run: executable unavailable. No universal equivalence or unbounded formal proof.
No children, commits, push, release, version edits or directory cleanup. Independent review
and package/release integration remain parent-owned.

Frozen own delta, before/after snapshots, exact hashes, all raw reports, checkpoints and
mutations: `/Users/yesh/.t3/scratch/2026-11-15-what-if-we-wanted-to-20120c4d/work/sol-cleanup/`.


## Final review and packaging

- Fixed independent review P2: normal pytest required a global Harrow executable. Pin harrow-code==0.4.1 in the development lock; the source gate passes with PATH limited to .venv/bin and system directories.
- Removed the superseded prototypes/pfx tracked tree after confirming no runtime/verifier imports. DESIGN now links its historical commit.
- Core imports neither Pydantic nor pydantic-settings. Pydantic is retained in development dependencies for examples/type fixtures; settings belonged only to the removed prototype. Published runtime has no external dependencies.
