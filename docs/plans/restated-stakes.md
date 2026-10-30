---
tracking: https://github.com/tomcounsell/ai/issues/3673
slug: restated-stakes
type: feature
status: planned
critique_rounds: 2
review_rounds: 1
---

# The builder restates the stakes; a restatement can only add a review round

Tom, 2026-10-30: "the builder should be able to change the description from 'prompt only' to
something more accurate once the build is done". Today only the planner sets the stakes and only
critique may raise the counts (`machine.Fold.loops`, `core/machine.py:261-266`); nothing after the
build can correct a wrong one.

## Governance

**Grant** (one instance: a build or patch turn may add the review round). Tom, 2026-10-30, the
request quoted above. **Incident:** the 2026-10-30 outage from d0ec204a0. Its plan said "prompt
only" with `critique_rounds` 0; the build changed `intake.root_of`, which compared `text = smallint`
and took chat sessions down on all four Valor Macs; review took the plan's word and no round ran.
The same break left the nightly verify routine dead for 8 nights (3cf7b882c, aee0336e5).
**Mission item 1** (own outcomes across the whole job: resolving discovered defects).
**Ledger:** `core/guards.py` `SEEDED` gains `machine.GUARD_RESTATED = "build.restated"`, with this
incident, `mission_items: [1]`, `source`/`via` "docs/plans/restated-stakes.md, Governance",
`granted_at` 2026-10-30, `expires` 2027-01-28 (ninety days), `note` Tom's words. It fires as below,
so `firing_record` stays True.

## Freshness

Baseline aee0336e5; issue filed 2026-10-30T15:39Z, after the two fixes; nothing since touches the
files. References re-read and current: `Fold.loops` `machine.py:261`, critique raise
`machine.py:494-503`, candidate fold `machine.py:448-453`, `session._stage_verdict` `:749-758`,
`session._collected` `:765`, `fresh.review_inputs` `:729-747`, review firing `verdicts.py:272`.
No overlapping plan. #3672 (message-id normalization) is out of scope.

## Solution

1. **Signal.** `core/signals.py` reads `.valor/stakes.json` (`{"stakes": str, "review_rounds":
   0..2}`) as it reads `plan.json`, into `Signals.stakes`; the docstring lists it.
2. **Collected.** `session._stage_verdict`, on a `candidate` in BUILD or PATCH, reads it the way
   `_plan` reads its counts: a wrong type or range is one line in `errors` and the restatement is
   dropped; the candidate still counts. `_collected` carries it as `restated` (null when none).
3. **Fold.** On `turn.collected` with verdict `candidate`, `Fold.restated` keeps the latest stakes
   text and the higher of its earlier and new `review_rounds`. `Fold.loops` takes the max of
   plan, critique raise and restatement, still capped at `REVIEW_MAX`; `critique_rounds` is
   untouched (critique is over). `feedback.given` leaves it, as it leaves `raised`.
4. **Firing.** In `verdicts.py:272`, a join row 3 whose round exists only because of the
   restatement (`counts["review_rounds"]` not below the loops without it) records
   `GUARD_RESTATED` instead of `GUARD_REVIEW`.
5. **Reviewer.** `review_inputs`' header adds two lines when `f.restated` is set: the builder's
   restated stakes and the review rounds now in force. `summary()` shows `restated`.
6. **Words.** `skills/sdlc/build.md` and `patch.md`: one sentence each, that when the change risks
   more than the plan's stakes say, write `.valor/stakes.json` beside `done.md`; it can add the
   review round, never remove it. `docs/sdlc-state-machine.md` "Setting the counts" says so.

Timing: the restatement is in the candidate's own `turn.collected` row, folded before any check
row, so the join reads the raised loops.

## No-gos

Tom rejected: a payload-type validator, rewrites of `review.md`/`plan.md`, routine failure notices.
No SQL constraint for `restated` (the session's read is the one check). No lowering, no
critique-round restatement.

## Tests

`tests/test_machine.py`: a restated 1 over a plan's 0 makes `loops.review_rounds` 1; a restated 0
over a plan's 1 leaves 1; a later 0 after an earlier 1 leaves 1; a restated 2 reads as 1. Session:
a valid `stakes.json` lands in `turn.collected` as `restated`; a string count is one error and the
candidate still counts. Review inputs: the header shows both stakes. `test_orchestration.py`: the
new guard is ledgered with 2026-10-30 / 2027-01-28 / `[1]`. A join row 3 on the restated round
carries `build.restated`.
