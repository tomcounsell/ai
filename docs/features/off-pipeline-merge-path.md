# Off-Pipeline Merge Path

A PR that did not originate inside the SDLC pipeline — a dependabot bump, a
hand-authored bug fix, a follow-up filed from another PR's review findings —
reaches the merge gate through the same four predicate groups as a planned
feature. What it does not have is a plan document, and therefore no CRITIQUE
stage and no CRITIQUE verdict. This document describes how such a PR states
that truthfully and merges.

## The state before this existed (issue #2577)

`tools/merge_predicate.py` group (b) passes on `stages.DOCS == completed`.
Reaching that marker, and reaching the REVIEW `completed` marker that
`sdlc-tool verdict finalize` writes, both route through
`PipelineStateMachine._backfill_predecessors`, which promotes the ISSUE-rooted
spine behind the target stage. The spine contains CRITIQUE, and the verdict
invariant (#2415/#2554) refuses to force-complete a REVIEW or CRITIQUE that
carries no recorded verdict:

```
Cannot backfill predecessors of REVIEW: CRITIQUE would be force-completed for
issue #N but carries no finalized verdict (verdict invariant unsatisfied)
```

That refusal was correct and its remediation was unsatisfiable. It assumed
CRITIQUE had been skipped by accident and told the operator to re-run it; for a
PR with no plan there is nothing to critique and no honest verdict to record.
The degraded `docs/features/{slug}.md` fallback did not help either — it derives
the slug from the PR head ref, so `fix/dangling-command-table-refs` and
`dependabot/uv/uv-374350d79f` resolve to paths that cannot exist.

The two remaining routes were to write a synthetic CRITIQUE verdict, which is
the forgery the invariant exists to prevent (it would turn any `--stage DOCS
--status completed` call into a way to manufacture an approval), or to
break-glass the merge guard. Eleven break-glass merges in a single night is what
prompted the fix: a break-glass that becomes routine stops being a signal.

## The `skipped` disposition

The pipeline now distinguishes **"this stage has not run yet"** from **"this
stage was never dispatched and does not apply"**. The second is a first-class
stage status, `skipped`, alongside `pending`/`ready`/`in_progress`/`completed`/
`failed`.

`completed` and `skipped` together form `SETTLED_STATUSES` in
`agent/pipeline_state.py` — the stage is behind us, so predecessor checks, the
backfill scan, `has_remaining_stages`, `next_stage` and the router's row-10
readiness check all accept either. They stay distinct on read: `completed`
asserts the stage ran and succeeded, `skipped` asserts it never ran.

### Recording a skip

Nothing extra to run. The predecessor backfill records it, at the moment it
would otherwise refuse:

```bash
sdlc-tool session-ensure --issue-number N
sdlc-tool verdict finalize --pr P --issue-number N --verdict APPROVED --run-id X
```

`finalize` writes the REVIEW completion marker on the APPROVED path, whose
backfill walks CRITIQUE. Finding a CRITIQUE that verifiably never ran and does
not apply, it records `skipped` instead of raising. An agent reviewing a bug
fix filed while verifying `main` does not have to know in advance that this
issue has no plan.

The disposition is also recordable **deliberately, up front**:

```bash
sdlc-tool stage-marker --stage PLAN     --status skipped --issue-number N --run-id X
sdlc-tool stage-marker --stage CRITIQUE --status skipped --issue-number N --run-id X
```

Both entry points run the same predicate
(`tools/sdlc_stage_marker.py::_skip_precondition_error`) and reach the same
ledger state, so they cannot disagree about a stage. Either way a `_stage_skips`
record persists on the `PipelineLedger` with the reason and timestamp, so the
disposition survives with its justification rather than being inferable only
from the absence of a verdict.

### This also changes runs that DID originate in the pipeline

The auto-skip is not scoped to off-pipeline PRs, because nothing in the ledger
identifies one. It fires wherever the predicate holds: no plan document
resolvable, no recorded CRITIQUE verdict, and no recorded `/do-plan-critique`
dispatch. A pipeline run whose plan exists but is not findable from the tool's
working directory — written on a session branch and not yet on `main`, say, or a
cross-repo run where `SDLC_TARGET_REPO` points elsewhere — satisfies all three
and will record PLAN and CRITIQUE as `skipped`.

Expect this and read it correctly. It is not the tool losing track of a plan; it
is the tool reporting what the ledger can actually establish. Compare it to what
the old code did in the same situation: force-complete PLAN, asserting a plan
stage that never produced anything, and raise on CRITIQUE with a remedy nobody
could satisfy. Neither of those was honest either, and `skipped` at least never
claims work happened.

The two checks that keep this narrow are the verdict and the dispatch history. A
CRITIQUE that actually ran leaves a verdict, so it is promoted to `completed` and
never considered for a skip. A CRITIQUE that was dispatched and crashed before
recording anything — the #1668 shape — leaves a `_sdlc_dispatches` entry, which
refuses the skip and preserves the evidence that a critique was attempted. What
remains is the case where the pipeline genuinely never critiqued this issue,
whoever wrote the diff.

`docs/plans/` resolution order is in `tools/lane_identity.py::find_plan_path`:
`SDLC_TARGET_REPO`, then the cwd's git toplevel, then the `__file__`-relative
fallback. If you see an unexpected skip on in-pipeline work, that ordering is
where to look first.

## Why this is not a way to forge an approval

The skip is verified rather than asserted, and the verification lives in the
tool (`tools/sdlc_stage_marker.py::_skip_precondition_error`), not in the
caller's good intentions. Five properties hold together:

1. **Closed skippable set.** `agent.pipeline_state.SKIPPABLE_STAGES` is exactly
   `{PLAN, CRITIQUE}`. `--stage REVIEW --status skipped` is refused
   unconditionally with `STAGE_NOT_SKIPPABLE`, as are DOCS and MERGE. Those
   three are the stages `tools/merge_predicate.py` reads; a skippable one would
   be a way to merge without the guarantee the stage exists to provide. The
   refusal is enforced twice, in the tool and again in
   `PipelineStateMachine.skip_stage`.
2. **A derived precondition, not a claim.** The tool refuses with
   `PLAN_EXISTS_NOT_SKIPPABLE` when `find_plan_path(issue_number)` resolves a
   plan document. You cannot skip the CRITIQUE of an issue that has a plan.
3. **No retroactive skipping.** `STAGE_RAN_NOT_SKIPPABLE` refuses when the stage
   carries a recorded verdict, carries a recorded `_sdlc_dispatches` entry for
   its skill, or holds any status other than `pending`/`ready`. A CRITIQUE that
   actually ran keeps its verdict requirement.
4. **The backfill's auto-skip is bounded by the same closed set.** It fires only
   for `SKIPPABLE_STAGES` and only where the explicit call would also have been
   accepted. A verdict-less REVIEW still raises there on every path, so
   `--stage DOCS --status completed` cannot produce a REVIEW completion as a
   side effect — which is the specific forgery this design has to refuse.
5. **REVIEW's invariant got stronger, not weaker.**
   `verdict_invariant_satisfied("REVIEW", n)` requires a readable verdict, a
   resolvable head SHA, **and** a posted GitHub review artifact. The artifact
   conjunct is new (#2577): `finalize` records the verdict and its head SHA
   *before* it checks for the artifact, so a run that refused with
   `REVIEW_ARTIFACT_MISSING` left both behind, which the
   backfill read as a satisfied invariant. It was unreachable while CRITIQUE
   blocked first; clearing CRITIQUE exposed it. Both call sites — the backfill
   and `write_marker`'s direct completed-path — now enforce the same three
   facts. Merge-predicate group (c) still independently requires a recorded,
   APPROVED, head-fresh verdict on top.

Every probe fails closed. An unreadable ledger, an errored plan lookup, or a
malformed dispatch history refuses the skip — "cannot confirm the stage never
ran" is never read as "the stage never ran".

## The full sanctioned sequence

```bash
sdlc-tool session-ensure --issue-number N          # run_id + issue lease
# /do-pr-review: post the review artifact, then
sdlc-tool verdict finalize --pr P --issue-number N --verdict APPROVED --run-id X
sdlc-tool stage-marker --stage DOCS --status completed --issue-number N --run-id X
python -m tools.merge_predicate --pr-number P --run-id X --json   # allowed: true
```

The `finalize` call records the PLAN/CRITIQUE skips on its way through. Posting
the review artifact first is not optional: `finalize` refuses with
`REVIEW_ARTIFACT_MISSING` when no formal GitHub review and no `## Review:`
comment exist, and the DOCS marker then refuses too.

The PR body still needs a `Closes/Fixes/Resolves #N` line for group (a).
Dependabot rewrites its own PR body on every rebase, so an issue link added by
hand has to be re-checked immediately before the gate runs.

## Spent break-glass overrides

`data/merge_authorized_{pr}` is the break-glass override the merge-guard hook
honors. `data/` is gitignored and the "delete it immediately after use"
instruction was repeatedly not followed, leaving well-formed overrides on disk
whose PRs had merged a day earlier. The hook now classifies an override whose PR
is MERGED or CLOSED as `spent` and ignores it, logging a WARNING and telling the
operator to delete the file. An unresolvable PR state honors the override —
break-glass has to work in a degraded environment, and this check exists only to
stop a spent file authorizing a later merge.

## Related

- [`docs/features/pipeline-state-machine.md`](pipeline-state-machine.md) — stage
  statuses, predecessor backfill.
- [`docs/sdlc/do-merge.md`](../sdlc/do-merge.md) — the four predicate groups.
- [`docs/sdlc/do-pr-review.md`](../sdlc/do-pr-review.md) — where the skips are
  run in the review stage.
