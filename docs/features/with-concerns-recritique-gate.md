# With-concerns re-critique gate

A `READY TO BUILD (with concerns)` critique verdict does not reach `/do-build`
on the round that produced it. The concern-closing revision is judged before it
is built on, in a loop bounded by `MAX_CONCERN_RECRITIQUE_ROUNDS`.

Issue: [#2787](https://github.com/yudame/ai/issues/2787).

## The two states

Everything routes off two ledger states, distinguished by *when* the plan was
revised relative to *when* the verdict was recorded:

| State | Meaning | Owner |
|---|---|---|
| **S1** | The with-concerns verdict is the most recent event; no revision has landed since. | G7 gate 4 while the `plan_revising` lock is set, otherwise row 4b — both dispatch `/do-plan` |
| **S2**, below the bound | A `/do-plan` revision landed after the verdict. | Row 2b → `/do-plan-critique` |
| **S2**, bound spent | Same, but `MAX_CONCERN_RECRITIQUE_ROUNDS` with-concerns rounds have been recorded. | Row 4c → `/do-build`, residual concerns accepted |

The terminal round reads S1 → `/do-plan` (embed the final round's Implementation
Notes) → S2 → row 4c → `/do-build`. That last revision is not waste: it is what
puts the accepted concerns into the plan document before anyone builds from it.

## The re-critique edge already existed

Row 2b (`_rule_critique_verdict_stale`) has routed a stale CRITIQUE verdict back
to `/do-plan-critique` since [#1639](https://github.com/yudame/ai/issues/1639).
[#1760](https://github.com/yudame/ai/issues/1760) switched it off for the
with-concerns settle path because the loop had no terminating bound, and
[#2049](https://github.com/yudame/ai/issues/2049) then narrowed that latch *away*
from NEEDS REVISION, leaving `READY TO BUILD (with concerns)` as its live domain.

So the work was never "add a re-critique edge". It was **supply the bound, then
let row 2b run**. `_critique_verdict_is_stale` carries a with-concerns branch that
engages the #1760 latch only once the bound is spent.

The no-concerns and NEEDS REVISION / MAJOR REWORK paths are untouched.

## Why the below-bound arm is not a timestamp fallthrough

Below the bound the branch returns `_concern_revision_is_unjudged(...)` — "did a
revision actually land since this verdict?" — rather than falling through to the
function's usual `verdict_dt < plan_dt` comparison.

`plan_dt` comes from `_latest_dispatch_at`, which reads the `at` stamped when
`/do-plan` is **dispatched**, not when it finishes. A fallthrough would therefore
call the verdict stale the instant row 4b fires, and row 2b — registered ahead of
4b/4c, and matching on staleness alone — would re-critique a plan nobody had
revised yet, burning a bound slot whenever `/do-plan` crashed or no-opped.

The branch also sits **ahead of** the `if not latest_plan_at: return False` early
return, so the whole with-concerns decision is independent of the FIFO-truncated
`_sdlc_dispatches`.

## The bound

`_concern_round_count`, a plain integer in stage-states, incremented by
`tools/sdlc_verdict.py::record_verdict` inside the transaction it already owns.
`MAX_CONCERN_RECRITIQUE_ROUNDS` (`agent/pipeline_graph.py`) defaults to 3 and is
`os.getenv`-overridable.

| Property | Why it holds |
|---|---|
| Durable | Not in `_OWNED_METADATA_KEYS`, so `PipelineStateMachine._save()` merges it back on every write. |
| Monotonic | Increment-only; no reset path. |
| Loop-scoped | Only a `WITH CONCERNS` CRITIQUE verdict counts, so NEEDS REVISION rounds never consume it. |
| G4-inert | Absent from `_SNAPSHOT_PROJECTION_KEYS`, so the oscillation snapshot is unchanged. |

**There is deliberately no dedupe.** A replayed `verdict record` on unchanged plan
bytes and a genuine next round are byte-identical inputs. Over-counting costs a
build with a *recorded* acceptance one round early; under-counting costs an
unbounded loop. The counter takes the safe error.

**G4 cannot substitute for the bound.** `compute_same_stage_count` counts
*consecutive same-skill* dispatches and breaks its streak on any skill change.
This loop alternates `/do-plan` and `/do-plan-critique`, so the streak resets
every turn. The counter is the loop's only terminator — which is why nothing may
be allowed to stop it advancing.

## The counter must ride `_meta`

`sdlc-tool next-skill` reads `stage-query`'s projection, not raw stage-states, and
that projection threads only `("_verdicts", "_sdlc_dispatches")` into `stages`. A
bare `_concern_round_count` key would be dropped, producing a feature that passes
every unit test and is inert in production. It is therefore surfaced as
`_meta["concern_round_count"]` by `tools/sdlc_stage_query.py::_compute_meta`,
following the `critique_cycle_count` precedent, and mirrored in `_default_meta`
per the #2769 key-parity rule.

## G5 steps aside unconditionally

Guards run to completion *before* the dispatch table, and
`guard_g5_artifact_hash_cache`'s cached-verdict branch tests
`CRITIQUE_READY_TO_BUILD in verdict_text`, which matches
`READY TO BUILD (WITH CONCERNS)` too. Without a step-aside, rows 2b/4b/4c are
unreachable in production.

The step-aside is unconditional (`if "WITH CONCERNS" in verdict_text: return None`).
An earlier design gated it on `_concern_revision_is_unjudged`, which is `False` in
S1 — precisely the state right after a with-concerns verdict is recorded — so G5
still shipped `/do-build` in the state the guard was written to prevent.

A consequence worth stating plainly: **G5 is no longer row 2b's loop bound on this
path** — `MAX_CONCERN_RECRITIQUE_ROUNDS` is. The substitution is sound because the
counter advances on every recorded round regardless of whether the plan bytes
changed, so an unchanged-plan re-critique still consumes a slot and still
terminates.

`agent/sdlc_router.py`'s #1871 short-circuit is untouched; the new test sits ahead
of it and is strictly stronger for with-concerns verdicts.

## The predicate is verdict-kind-agnostic

`_concern_revision_is_unjudged` asks one question — did a `/do-plan` revision land
after the latest CRITIQUE verdict was recorded — and nothing about what that
verdict said. The `WITH CONCERNS` requirement is a caller obligation.

This is load-bearing, not stylistic. **G7 gate 3 calls it without any verdict-kind
test**, because the `plan_revising` lock it self-heals may have been left by a
NEEDS REVISION or MAJOR REWORK round. Moving the verdict test into the predicate
body would make gate 3 return `False` on those locks, so G7 would fall through to
its deadlock backstop and escalate to `Blocked` after
`MAX_PLAN_REVISING_DISPATCHES` — a new stall introduced by the fix.

Fail-safe direction: anything unreadable returns `False`, which routes to a
revision pass, never to a build.

## The repaired `plan_revising` lock

`/do-plan-critique` Step 5.6 sets the lock on verdict kind alone; the
`revision_applied: true` exemption is deleted from both the global skill body and
`docs/sdlc/do-plan-critique.md`. G7 gate 3 releases it on
`_concern_revision_is_unjudged` instead of the sticky boolean, so the lock is
armable per round instead of permanently inert after the first revision.

One reachable behavior change: with no `_verdicts["CRITIQUE"]` record at all the
predicate returns `False`, so a lock the sticky boolean would have released now
survives and G7's backstop escalates to `Blocked` with the documented manual
recovery. That is the backstop working as designed.

## Cap-reached behavior

Row 4c dispatches `/do-build`, not `Blocked`. CONCERNs are non-blocking by
definition and a `Blocked` strands the lane on a human who is usually not
watching. Accountability comes from the record: row 4c's `reason` names the bound
and the acceptance, and `/do-build` writes an **Accepted Residual Concerns** note
into the plan's `## Critique Results`, deriving the condition from
`sdlc-tool stage-query` rather than from a `row_id` that is never plumbed into
skill invocations. See `docs/sdlc/do-build.md`.

## Recovering a stuck lane

- `sdlc-tool meta-set` on the plan's `revision_applied_at` (written from plan frontmatter).
- `MAX_CONCERN_RECRITIQUE_ROUNDS=0` — the kill switch. The latch then engages on
  every with-concerns verdict, restoring exactly the pre-#2787 routing.

## See also

- [`sdlc-router-oscillation-guard.md`](sdlc-router-oscillation-guard.md) — the convergence latch this supersedes on the with-concerns path
- [`sdlc-pipeline.md`](sdlc-pipeline.md)
- [`../sdlc/plan-revising-lock.md`](../sdlc/plan-revising-lock.md)
