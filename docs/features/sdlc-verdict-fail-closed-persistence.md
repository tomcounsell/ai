# SDLC Verdict Fail-Closed Persistence

**Status:** Shipped · **Issue:** [#2193](https://github.com/tomcounsell/ai/issues/2193)

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
