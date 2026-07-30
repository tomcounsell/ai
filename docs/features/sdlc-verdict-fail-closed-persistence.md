# SDLC Verdict Fail-Closed Persistence

**Status:** Shipped · **Issues:** [#2193](https://github.com/tomcounsell/ai/issues/2193) (REVIEW), [#2447](https://github.com/tomcounsell/ai/issues/2447) (CRITIQUE)

## The verdict-findings persistence contract (CRITIQUE + REVIEW)

This is **one persistence contract, not two stage patches**: the stage that
records a verdict must persist the evidence that justifies it, at the tool level,
in the same finalize step. A verdict must never land without its findings.

- **CRITIQUE** (#2447): `/do-plan-critique` writes the war-room's aggregated
  finding bodies into the plan's `## Critique Results` table — the durable,
  machine-checkable record of what the critics said — in the same Step 5.5
  finalize block as the verdict record. A `NEEDS REVISION` verdict recorded
  against a plan whose table is empty of real findings is refused with a named
  error (`CRITIQUE_FINDINGS_MISSING`), fail-closed.
- **REVIEW** (#2193): `/do-pr-review` finalizes its own verdict + freshness
  trailer + completion marker atomically via `sdlc-tool verdict finalize`, which
  reads all three writes back and fails closed with named errors. This is the
  symmetric guarantee CRITIQUE is now built to mirror.

The two stages share the philosophy from #1690: critique/review completion must
be **mechanically verifiable**, not asserted in prose. The CRITIQUE table is the
`## Critique Results` section of the plan doc; the REVIEW record is the substrate
verdict + trailer + marker. Both are fail-closed at record time, not repaired
after the fact by a later actor.

### CRITIQUE: findings-persistence write + `CRITIQUE_FINDINGS_MISSING` gate

**The write (skill side).** `/do-plan-critique` Step 5.5 renders the aggregated
findings into the plan's `## Critique Results` table (one row per finding:
`| {SEVERITY} | {critics} | {finding} | pending | {implementation note} |`,
literal pipes escaped as `\|`), resolves the plan path via the shared
`find_plan_path(issue_number)` resolver, writes + commits on `main`, and only
THEN calls `sdlc-tool verdict record --stage CRITIQUE`. The ordering guarantees
the gate sees the populated table. READY TO BUILD (no concerns) writes an
explicit `No findings from the war room.` line — the gate never fires on READY.

**The gate (tool side).** A strict **real-finding-row parser**
(`critique_table_has_findings` in `tools/sdlc_verdict.py`) reads the
`## Critique Results` section: it strips HTML comments, splits cells on
`(?<!\\)\|` (respecting the writer's escaping so a Finding cell containing a pipe
is never mis-columned), and counts a row as a real finding only when its Severity
cell is `BLOCKER`/`CONCERN`/`NIT` **and** its Finding cell is non-empty and not a
bracketed placeholder (`^\[.*\]$`). The template placeholder row therefore reads
as empty. Any parse/read error returns False — **fail-closed: an unreadable table
cannot satisfy the invariant.**

The `_cli_record` gate fires **only** on a `NEEDS REVISION` verdict paired with a
table that has no real finding row, raising `CritiqueFindingsMissingError`
(`CRITIQUE_FINDINGS_MISSING:` prefix, non-zero exit, no partial write). It never
fires on any `READY TO BUILD` variant or `MAJOR REWORK` (incl.
`MAJOR REWORK (CRITIQUE INCOMPLETE)`, which legitimately has no findings). The
gate sits AFTER lease resolution/revalidation so an ownership failure
(`LEASE_ABSENT`/`ISSUE_LOCKED`) is still adjudicated first. The `record_verdict`
Python API keeps its graceful-failure contract (returns `{}`, never raises) — the
refusal lives only in the CLI path.

**Orphaned-table recovery.** If `verdict record` fails after the table commit, the
plan carries a table with no verdict — self-healing, never a half-written verdict:
the table is idempotently overwritten by the next critique pass, and the router
never advances past CRITIQUE without a recorded verdict, so a re-dispatch
re-records.

## Problem

A local `/do-sdlc` supervision run posted a correct APPROVED review as a
GitHub comment but never persisted the local substrate state the pipeline
router reads to advance. Three distinct persistence gaps were observed on one
PR head:

1. **No `verdict record` call at all** — `latest_review_verdict: null` after
   an APPROVED review; the router had nothing to consume.
2. **Missing freshness trailer** — the recorded verdict lacked the required
   `REVIEW_CONTEXT head_sha=<40-hex>` trailer, so the merge predicate treated
   it as stale against the PR head.
3. **REVIEW stage marker never set to `completed`** — even with a valid
   verdict, the dispatch table couldn't route to DOCS.

The three writes were a hand-executed, non-atomic sequence with no
fail-closed backstop. A skill that skipped any of them left
`agent/sdlc_router.py` re-dispatching REVIEW forever, and a human had to
hand-repair pipeline state before `/do-sdlc` could advance.

## Solution

### `sdlc-tool verdict finalize` — atomic write+verify

New subparser on `tools/sdlc_verdict.py` (logic lives in
`tools/sdlc_review_finalize.py::finalize`), reached through the existing
`verdict` → `tools.sdlc_verdict` mapping in `scripts/sdlc-tool`'s
`ALLOWED_SUBCOMMANDS` — no allowlist edit required.

Given `--pr`, `--issue-number`, `--verdict`, `--blockers`, `--tech-debt`,
`--run-id`, it:

1. Resolves the PR's head SHA via `gh pr view <pr> --json headRefOid -q .headRefOid`.
2. Records the verdict via the existing single-writer `record_verdict`, with
   a `REVIEW_CONTEXT head_sha=<40-hex>` trailer appended if not already
   present (idempotent).
3. On the APPROVED path, writes the REVIEW `completed` stage marker.
4. Reads all three back through the shared `check_review_persistence()` and
   raises `ReviewFinalizeError` — non-zero exit, named reason on stderr — if
   any of the three didn't land.

`finalize` is state-mutating and requires `--run-id` (inherits the existing
`RUN_ID_REQUIRED` gate + heal path). It collapses the previous hand-run
3-call sequence (`verdict record`, `stage-marker REVIEW completed`, `verdict
get` readback) into one operation that cannot partially complete.

**Named error taxonomy** (mirrors the existing WS3c/WS-D gate vocabulary):

| Error | Meaning |
|-------|---------|
| `REVIEW_VERDICT_MISSING` | No readable REVIEW verdict for the issue. |
| `REVIEW_TRAILER_MISSING` | Recorded verdict lacks a well-formed `REVIEW_CONTEXT head_sha=<40-hex>` trailer matching the PR's current head (or the head SHA itself couldn't be resolved via `gh`). |
| `REVIEW_MARKER_INCOMPLETE` | REVIEW stage marker is not `completed`. |

**Fail-closed semantics:** every probe treats any exception (Redis hiccup,
`gh` failure, malformed record) as the corresponding named failure, never as
a silent pass. `finalize` refuses loudly with `REVIEW_TRAILER_MISSING`
rather than ever recording a trailer-less verdict when `gh` is unreachable.

**Head-SHA resolution — target-repo scoped, fail-loud (#2377 / absorbed #2394):**
`_fetch_pr_head_sha(pr, repo=None)` threads the target repo into the `gh`
lookup so it resolves against the right repository even when `sdlc-tool` forces
the process cwd to `~/src/ai` on a cross-repo local `/do-sdlc` run — `finalize`
passes the lease's pinned `target_repo` slug; `check_review_persistence`/
`selfcheck` resolve it via `resolve_target_repo_for_read(issue_number)`. Without
this, an unscoped `gh pr view` returned the wrong repo's PR head (or a
real-but-wrong same-numbered PR), silently passing or failing the REVIEW gate.
The helper also no longer returns a silent `None`: it raises the named
`HeadShaResolutionError` with the concrete cause (missing `gh`, non-zero exit +
stderr, timeout, empty output), logged at `error` level. The write path
re-raises it as `REVIEW_TRAILER_MISSING` (loud, non-zero exit); the read path
catches it and fails closed (`reason: REVIEW_TRAILER_MISSING`, exit-0). Full
`gh`-slug contract: `docs/features/sdlc-tool-resolver.md`.

### `sdlc-tool verdict selfcheck` — read-only probe

Same module, read-only path (`_cli_selfcheck` → `check_review_persistence`).
Given `--pr`, `--issue-number` (no `--run-id`), always returns (never
raises) typed JSON:

```json
{
  "ok": true,
  "verdict_present": true,
  "trailer_matches_head": true,
  "marker_completed": true,
  "reason": null
}
```

`ok` carries the verdict, not the process exit code — callers branch on the
JSON, same convention as `stage-query` and `verdict get`. `finalize`
(write+verify) and `selfcheck` (verify-only) share one
`check_review_persistence(pr, issue_number)` function so the two paths can
never disagree.

### APPROVED-only trailer-enforcement gate

`tools/sdlc_stage_marker.py::_review_trailer_present()` extends the existing
WS3c completion-marker gate: a REVIEW `completed` marker on the APPROVED path
now also requires a well-formed `REVIEW_CONTEXT head_sha=<40-hex>` trailer on
the recorded verdict (reusing the shared `_HEAD_SHA_TRAILER_RE`, hoisted from
`tools/merge_predicate.py` into `tools/_sdlc_utils.py` as the single
definition). It closes failure #2 at the same gate that already closes the
#1642 verdict/marker desync — the prior gate (`_review_verdict_readable`) was
truthiness-only, so an APPROVED verdict with no trailer still read as
"present" and let the marker through.

Non-APPROVED verdicts (CHANGES REQUESTED, BLOCKED_ON_CONFLICT, PR_CLOSED)
are exempt — they legitimately carry no head_sha trailer and leave the
marker `in_progress` by contract; the trailer conjunct is a pass-through for
them.

### The backfill write path is now closed too (issue #2305 defect 4)

The gates above close the *direct* `write_marker` completed-path — a REVIEW
or CRITIQUE stage marker cannot be written `completed` without a readable
verdict (and, for REVIEW, a matching trailer). But there was a second, open
write path to the exact same `completed` state: `PipelineStateMachine.
_backfill_predecessors()` (`agent/pipeline_state.py`), reached whenever ANY
downstream stage starts with `backfill_predecessors=True` (the normal path
taken by `write_marker(status="in_progress")` → `start_stage(stage,
backfill_predecessors=True)`). Backfill force-completes on-spine
predecessors that are still open — including REVIEW/CRITIQUE — with **zero**
verdict checks, so a downstream stage marker could mint a verdict-less
REVIEW/CRITIQUE `completed` marker that the direct-path gates above never
saw.

Issue #2305 (defect 4) closes this gap by making `_backfill_predecessors`
enforce the identical invariant, not a re-implementation of it. The three
`sdlc_stage_marker.py` probes (`_review_verdict_readable`,
`_review_trailer_present`, `_critique_verdict_readable`) were hoisted into
`tools/sdlc_verdict.py` — the verdict source of truth — and are now thin
delegates from `sdlc_stage_marker.py`. A new combining predicate,
`verdict_invariant_satisfied(stage, issue_number)` (also in
`tools/sdlc_verdict.py`), ANDs verdict-readability with trailer-presence for
REVIEW and wraps verdict-readability alone for CRITIQUE; it fails **CLOSED**
(returns `False`) on any error or missing data.

`_backfill_predecessors` calls this shared predicate during its **scan**
phase — before any mutation, preserving the existing scan-then-mutate
no-partial-state property. For each to-promote member that is REVIEW or
CRITIQUE, it resolves `issue_number` from `self._ledger.issue_number` and
checks the predicate; if unsatisfied, or if `issue_number` cannot be
resolved at all (a `PipelineStateMachine` constructed session-keyed, with no
`_ledger`), it raises `ValueError` before touching `self.states` — symmetric
with the machine's existing failed-predecessor raise. The real trigger path
(`write_marker` → `PipelineStateMachine.for_issue(target_repo,
issue_number)`, `tools/sdlc_stage_marker.py:401`) always populates
`self._ledger.issue_number`, so the unresolvable-issue_number case is itself
a fail-closed backstop rather than a live path.

Net effect: a REVIEW or CRITIQUE `completed` marker can no longer be minted
via the backfill path without a finalized, trailer-complete (for REVIEW)
verdict — the write-path closure this doc previously described only for the
direct `write_marker` completed-path and the downstream `selfcheck` gate now
also covers the backfill route that fed it.

**Operator note — ledger-wipe recovery.** Backfill is the sanctioned repair
path for a wiped ledger (re-run `session-ensure`, then backfill the lost
stage markers). That path deliberately **cannot** reconstruct a REVIEW or
CRITIQUE `completed` marker whose verdict is genuinely gone — the backfill
raises `ValueError` (fail-closed, no partial state) instead of synthesizing
a verdict-less completion. This is intentional: a laundered approval is far
more costly than a second recovery step, and re-review is cheap. When you
hit the raise, the message names the concrete next step — **re-run the named
stage (REVIEW/CRITIQUE) for that issue to record a real verdict (e.g. via
`sdlc-tool verdict finalize`), then retry the backfill.** The honest recovery
is two unambiguous steps, never a one-step backfill with a silent gap in the
one stage that gates merge.

### Two-mechanism self-healing story

The atomic `finalize` call is still *nominally* skippable by a misbehaving
skill — collapsing three calls into one does not by itself make the one call
un-skippable. Two mechanisms make the failure self-correcting:

1. **Router re-dispatch self-heals (all local runs).** `agent/sdlc_router.py`
   rows 8/8b/9 already fail-closed: a null verdict or non-completed marker
   re-dispatches REVIEW. Because the skill now calls the *atomic* `finalize`
   on every run, a re-dispatch re-runs `finalize` and persists all three
   writes in one shot — the loop that previously required hand-repair
   self-terminates after one retry, whether or not a `/do-sdlc` supervisor is
   present. No router-row change was needed; this rides the router's
   existing behavior unchanged.
2. **Supervisor gate makes it loud (supervised `/do-sdlc` runs only).** The
   `/do-sdlc` supervisor (`.claude/skills-global/do-sdlc/SKILL.md`) calls
   `sdlc-tool verdict selfcheck --pr N --issue-number M` after `do-pr-review`
   returns and advances past REVIEW **only** on `ok:true`. On `ok:false` it
   halts and prints the machine-readable `reason` — a single loud refusal an
   operator sees, instead of a silent router re-loop.

**Scope boundary (honest):** the supervisor gate is prose in the `/do-sdlc`
skill body, so it is itself instruction-gated and only protects *supervised*
runs. A bare, unsupervised local `/do-pr-review` (no `/do-sdlc` wrapper) that
skips `finalize` gets the self-healing router re-dispatch of mechanism 1 —
bounded, no longer a human-repair loop — but not the loud refusal. Making
the router itself consult `selfcheck` so unsupervised runs are also
protected mechanically is deferred to a separate slug (see the plan's
No-Gos).

## Skill wiring

- `docs/sdlc/do-pr-review.md` — the 3-call "Verdict recording" block is
  replaced with the single `sdlc-tool verdict finalize` invocation.
- `.claude/skills-global/do-pr-review/SKILL.md` Step 5 / Hard Rule #8 — the
  OUTCOME block must not be emitted until `finalize` exits 0.
- `.claude/skills-global/do-sdlc/SKILL.md` — the supervisor `selfcheck` call
  and advance-only-on-`ok:true` gate described above.

## Related

- [SDLC Fork Artifact-Grounding Guards](sdlc-fork-artifact-grounding.md) —
  the sibling WS-D REVIEW-artifact-presence gate this trailer conjunct sits
  alongside (same completion-marker function).
- [Enforce REVIEW/DOCS Stages](enforce-review-docs-stages.md) — the
  merge-predicate freshness check (`_check_verdict_freshness`) that is the
  downstream consumer of the trailer this doc's writer now enforces at
  record time instead of only at merge time.
- [SDLC Router Oscillation Guard](sdlc-router-oscillation-guard.md) — the
  router rows (8/8b/9) whose existing re-dispatch behavior is what makes
  mechanism 1 self-healing.
