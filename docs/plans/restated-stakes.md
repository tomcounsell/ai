---
tracking: https://github.com/tomcounsell/ai/issues/3673
slug: restated-stakes
type: feature
status: planned
critique_rounds: 2
review_rounds: 1
revision_applied: true
revision_applied_at: 2026-10-30T16:40:00Z
---

# The builder restates the stakes; a restatement can only add a review round

Tom, 2026-10-30: "the builder should be able to change the description from 'prompt only' to
something more accurate once the build is done". Today only the planner sets the stakes and only
critique raises the counts (`Fold.loops`, `core/machine.py:261-266`); nothing after the build
corrects a wrong one.

## Governance

**Grant** (one instance: a build or patch turn may add the review round). Tom's request of
2026-10-30: "Because it can add a review round it is governance: Tom's grant is this request."
**What the round buys:** when the builder judges higher stakes than the planner (plan 0, restated
1), a review `changes` or red test goes to patch at join row 3 before the last round at row 5, and
test gaps go to patch instead of merging at row 4. **Incident:** d0ec204a0, subject ending "(Tom,
team chat 2026-10-29; prompt only)", plan 15141b3ae "(prompt only)", whose plan said "Tags are in
the prompt only". The build also added an `intake.root_of` call in `session._send_places`, which
failed chat turns when a send's `reply_to` was a number (fixed in 3cf7b882c). The part of this
change that touches that incident is the reviewer reading the builder's accurate stakes in place
of "prompt only"; the extra round is what Tom granted on top. **Mission item 1** (own outcomes
across the whole job). **Ledger:** `core/guards.py` `SEEDED` gains `machine.GUARD_RESTATED =
"build.restated"`: this incident, `mission_items: [1]`, `source`/`via` "docs/plans/restated-stakes.md,
Governance", `granted_at` 2026-10-30, `expires` 2027-01-28, `note` Tom's words. It is stamped as in
step 4, so `firing_record` stays True and the expiry review sees whether it fired.

## Freshness

Baseline c099ec5b1. Re-read and current: `Fold.loops` `machine.py:261`, critique raise
`machine.py:494-503`, candidate fold `:448-453`, `feedback.given` `:542-547`, join `:310-341`,
`signals.collect` `:111-121` and `_filed` `:167-178`, `session._stage_verdict` `:687`,
`_collected` `:765`, `_parts`/`_drop` `:809-836`, `fresh.review_inputs` `:729-747`, stamp
`verdicts.py:272`. No overlapping plan; #3672 is out of scope.

## Solution

1. **Signal.** `core/signals.py` reads `.valor/stakes.json` exactly as `plan.json`, in both paths
   (`collect` in place and `_filed` from `handled/`): a JSON object into `Signals.stakes`, anything
   else one line in `unreadable`. `session._parts`/`_drop` gain a `("stakes",)` part.
2. **Collected.** `_collected` carries `found.stakes` as `restated` (null when none), unchecked.
3. **Fold.** On a `candidate` row, after `f.candidate` is set, the fold reads `p["restated"]`.
   Valid only when `stakes` is a str and `review_rounds` an int, not bool, in `ROUNDS` (the check
   at `machine.py:496`); otherwise ignored, never a returned error, so the row still enters
   CHECKS. Valid: `raised = review_rounds > f.loops.review_rounds`, then
   `f.raised["review_rounds"] = max(...)`, and `f.restated = {"stakes": ..., "raised": raised or
   earlier raised}`. `loops` is unchanged and still caps at `REVIEW_MAX`; `raised["critique_rounds"]`
   is never touched. `feedback.given` leaves both, as it leaves `raised`.
4. **Firing.** `verdicts.py:272`: a join row 3 with `f.restated` and `f.restated["raised"]` stamps
   `GUARD_RESTATED`; other row 3s and row 5 keep `GUARD_REVIEW`. With `REVIEW_MAX` 1, a raising
   restatement means the only row 3 round is the one it added.
5. **Reviewer.** `review_inputs` replaces `Its stakes:` with one line when `f.restated` is set:
   `Its stakes, as the builder restated them (superseding the plan's "<plan stakes>"): "<restated>"`.
   The rounds line prints `f.loops.review_rounds`.
6. **Words.** `skills/sdlc/build.md` and `patch.md` (the issue asks for both): one sentence each,
   that when the change risks more than the plan's stakes say, write `.valor/stakes.json` beside
   `done.md`; 1 is the ceiling and a restatement cannot lower the count.
   `docs/sdlc-state-machine.md` "Setting the counts" says so.

Timing: the restatement rides on the candidate's own `turn.collected`, folded before any check row.

## No-gos

Tom rejected: a payload-type validator, rewrites of `review.md`/`plan.md`, routine failure notices.
No SQL constraint, no `summary()` change, no lowering, no critique-round restatement.

## Tests

`tests/test_machine.py`: restated 1 over plan 0 gives `loops.review_rounds` 1; 0 over 1 leaves 1;
a later 0 after 1 leaves 1; 2 reads as 1; `{"review_rounds": true}` still enters CHECKS with
`restated` None. `tests/test_kernel.py`: `stakes.json` is read in place and from `handled/`.
`tests/test_pipeline.py`: plan.md carries exactly one stakes line, the restated one, and the
rounds line shows 1. `tests/test_orchestration.py`: the guard is ledgered 2026-10-30 / 2027-01-28 /
`[1]`; row 3 after a raising restatement carries `build.restated`; row 3 on a plan's own round and
row 5 carry `review.loop`.
