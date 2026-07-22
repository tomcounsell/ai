---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2193
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-22T09:36:33Z
---

# do-pr-review: fail-closed verdict/marker persistence for local SDLC runs

## Problem

During a local `/do-sdlc` supervision run (psyoptimal PR #638, issue #611), the
`do-pr-review` skill posted a correct APPROVED review as a GitHub comment but
**never persisted the local substrate state** the router consumes. The pipeline
router re-dispatched REVIEW three separate times on the same PR head; a human had
to hand-repair pipeline state each time before `/do-sdlc` could advance.

Three distinct persistence gaps were observed on one PR head:

1. **No `verdict record` call at all** — `latest_review_verdict: null` after an
   APPROVED review. The router had nothing to consume.
2. **Missing freshness trailer** — the recorded verdict lacked the required
   `REVIEW_CONTEXT head_sha=<40-hex>` trailer, so the merge predicate treated it
   as stale against the PR head.
3. **REVIEW stage marker never set to `completed`** — even with a valid verdict,
   the dispatch table couldn't route to DOCS.

**Current behavior:**
The verdict + trailer + completion-marker writes are documented as mandatory and
terminal (`do-pr-review/SKILL.md` Step 5 + Hard Rules #8/#9; `docs/sdlc/do-pr-review.md`
"Verdict recording" and "Mandatory Finalize" sections). But nothing *mechanically*
enforces them. A local hook-less run relies entirely on the agent hand-executing
three separate `sdlc-tool` calls in the right order — and when it skips them, the
router (`agent/sdlc_router.py` rows 8/8b/9) re-dispatches REVIEW forever, looping
against a skill that keeps skipping the same writes. This is called out in
`SKILL.md` line 241 as "the #1 local-pipeline failure mode."

**Desired outcome:**
The three substrate writes become **one atomic, fail-closed operation** the skill
cannot partially complete (`sdlc-tool verdict finalize`), plus a **committed,
load-bearing supervisor self-check gate** (`sdlc-tool verdict selfcheck`) that makes
advancing past REVIEW *strictly conditional* on `ok:true`. An APPROVED review can
never leave the skill with a null verdict, a trailer-less verdict, or a non-completed
REVIEW marker — and if it somehow does, the supervisor **refuses to advance and
surfaces the named reason loudly** instead of the router silently re-looping.

**Why this closes the root cause (not just narrows it):** The atomic `finalize` call
is still *nominally* skippable by a misbehaving skill — collapsing three calls into
one does not by itself make the one call un-skippable. Two committed mechanisms make
the failure *self-correcting and loud* rather than a human-repair loop:

1. **Router re-dispatch self-heals (existing, unchanged).** Rows 8/8b/9
   (`agent/sdlc_router.py:1183/1277/1343`) already fail-closed: a null verdict or
   non-completed marker re-dispatches REVIEW. Because the skill now calls the *atomic*
   `finalize` on every run, a re-dispatch re-runs `finalize` and persists all three
   writes — so the loop that previously required hand-repair **self-terminates after
   one retry**. No router-row change is needed (see Rabbit Holes); the fix rides the
   router's existing behavior.
2. **Supervisor gate makes it loud (committed scope, this slug).** The `/do-sdlc`
   supervisor advances past REVIEW *only* when `selfcheck` returns `ok:true`; on
   `ok:false` it halts and prints the machine-readable reason. This converts a silent
   infinite re-loop into a single loud refusal an operator sees.

## Freshness Check

**Baseline commit:** 3ab9dda68
**Issue filed at:** 2026-07-21T07:39:31Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills-global/do-pr-review/SKILL.md:171-199` (Step 5 "Record the Verdict") — still present, still instruction-only. Holds.
- `.claude/skills-global/do-pr-review/SKILL.md:241-242` (Hard Rules #8/#9) — still present. Holds.
- `docs/sdlc/do-pr-review.md:76-102` ("Verdict recording" block) — 3 separate hand-run `sdlc-tool` calls. Holds.
- `docs/sdlc/do-pr-review.md:142-146` ("Mandatory Finalize — Verdict + Marker Co-Write (#1642)") — WS3c gate description accurate. Holds.
- `tools/sdlc_verdict.py:257-381` (`record_verdict`) — free-form store, no trailer validation. Holds.
- `tools/sdlc_stage_marker.py:118-148` (`_review_verdict_readable`) — truthiness-only probe. Holds.
- `tools/merge_predicate.py:530-581` (`_check_verdict_freshness`), `104-105` (`_HEAD_SHA_TRAILER_RE`) — trailer required at merge. Holds.
- `agent/sdlc_router.py:1183` (`_rule_review_in_progress_no_verdict`), `:1277` (`_rule_review_completed_no_verdict`), row 9 `_rule_review_approved_docs_not_done` — re-dispatch on missing verdict. Holds.

**Cited sibling issues/PRs re-checked:**
- #1642 (verdict+marker co-write invariant) — landed; the WS3c gate enforces marker-after-verdict but not trailer-presence. Relevant.
- #2062 (WS3c `_review_verdict_readable` gate), #2124 (WS-D artifact-presence gate) — landed; both are the enforcement layer this plan extends.
- #2003 (run_id ownership + head_sha trailer freshness) — landed; established the trailer contract this plan makes mandatory.
- #1932 (row 9 tightened to require a recorded APPROVED verdict) — landed; consistent with this plan.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=2026-07-21T07:39:31Z` over all referenced files is empty.

**Active plans in `docs/plans/` overlapping this area:** none. No open plan touches `sdlc_verdict.py`, `sdlc_stage_marker.py`, or the do-pr-review skill.

**Notes:** The bug is fully reproducible by inspection — the writer and marker-gate code paths confirm the missing-trailer and null-verdict holes are still open at HEAD.

## Prior Art

- **PR #2010 (#2003)**: "SDLC substrate: run_id ownership, live merge-predicate enforcement, PR-number single writer" — introduced the `REVIEW_CONTEXT head_sha=` trailer and the merge-predicate freshness rung. Established the contract this plan makes mandatory at record time. Succeeded; did not close the "record without trailer" hole.
- **PR #2177 (#2124, #2026)**: "SDLC fork artifact-grounding guards" — added the WS-D artifact-presence gate to `sdlc_stage_marker.py` (REVIEW `completed` refused unless a posted review artifact + readable verdict exist). This is the exact gate this plan extends with trailer validation. Succeeded.
- **PR #2076 (#2026)**: "SDLC fork/supervisor hardening" — verdict-gated routing, supervised-run signal. Established the router's verdict-gate rows (8/8b/9) that loop on missing verdicts. Succeeded; the loop is the observed symptom, not the fix.
- **Issue #1672 (closed)**: "/do-plan-critique can leave CRITIQUE in_progress without ever recording a verdict" — the CRITIQUE-stage structural twin of this bug. Its fix pattern (`_critique_verdict_readable` gate) is the template for the REVIEW-side hardening here.
- **Issue #1731 (closed)**: "stage skills fork onto wrong issue context ... verdicts diverted, markers left stale" — related failure family (persistence not reaching the router); resolved by issue-number authoritative resolution, orthogonal to the atomicity gap here.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2010 (#2003) | Added the `head_sha` trailer + merge-predicate freshness check | Made the trailer *required downstream* (at merge) but never enforced it *at record time*. A trailer-less APPROVED verdict is accepted by the writer and only fails later at MERGE — too late, and it re-loops REVIEW. |
| PR #2177 (#2124) | WS3c/WS-D gate: REVIEW `completed` refused without a readable verdict + posted artifact | Closed the "marker without verdict" desync, but the readability probe is truthiness-only — a verdict with **no trailer** still reads as present. And the gate only fires when a marker write is *attempted*; a skill that writes *nothing* (failure #1) never trips it. |
| do-pr-review SKILL / addendum instructions | Documented all three writes as mandatory + terminal (Hard Rules #8/#9) | Instruction-only. The entire bug is that a local hook-less run **skipped the instructions**. No amount of stronger prose fixes an instruction-fidelity failure — it must be mechanized. |

**Root cause pattern:** The three substrate writes are a hand-executed, non-atomic
sequence with no fail-closed backstop for the "skill wrote nothing" and "verdict
without trailer" cases. Every prior fix tightened a *downstream consumer* (merge
predicate, marker gate) rather than making the *producer* atomic and self-verifying.

## Data Flow

1. **Entry point**: `/do-sdlc` supervisor dispatches `/do-pr-review` for PR N (local, hook-less).
2. **Review**: skill posts the GitHub review/comment (this part worked — verdict was correct).
3. **Persistence (BROKEN)**: skill is supposed to run 3 `sdlc-tool` calls —
   `verdict record` (with head_sha trailer), `stage-marker REVIEW completed`, then
   `verdict get` readback. In the failure, some/all were skipped.
4. **Router read**: `agent/sdlc_router.py` reads `_verdicts.REVIEW` + REVIEW marker
   via `sdlc_stage_query`. On null verdict / non-completed marker → rows 8/8b
   re-dispatch REVIEW (the loop). On trailer-less-but-present verdict → advances to
   DOCS/MERGE, then `merge_predicate._check_verdict_freshness` fails → stall at MERGE.
5. **Output**: pipeline never advances to DOCS/MERGE without human hand-repair.

**After the fix**, step 3 becomes a single `sdlc-tool verdict finalize` call that is
atomic and self-verifying, and step 4's supervisor gains a committed
`sdlc-tool verdict selfcheck` gate: it advances past REVIEW *only* on `ok:true`, and
on `ok:false` halts and surfaces the named reason (loud) instead of the router
silently re-looping. The router's own rows 8/8b/9 stay unchanged — they already
re-dispatch on missing state, and because the skill now calls the atomic `finalize`,
that re-dispatch self-heals rather than looping forever.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the finalize/self-check tool surface shape)
- Review rounds: 1

## Prerequisites

No prerequisites — this work touches only in-repo SDLC substrate modules that all
exist and are unit-tested. No external services, secrets, or APIs beyond `gh` (already
a hard dependency of the pipeline).

## Solution

### Tool Surface Decision (resolves former Open Question #1)

**Chosen name: `sdlc-tool verdict finalize` and `sdlc-tool verdict selfcheck`** —
new subparsers added to `tools/sdlc_verdict.main()` alongside the existing `record`
and `get`. The `review-finalize` / `review-selfcheck` top-level naming is **dropped
entirely** — do not add it anywhere.

Why this is the only reachable wiring: `scripts/sdlc-tool` is a bash allowlist
(`ALLOWED_SUBCOMMANDS=(verdict dispatch stage-marker stage-query session-ensure
next-skill meta-set)`, line 19) that maps `sdlc-tool <sub>` → `python -m
tools.sdlc_<sub>` and passes remaining args through. So `sdlc-tool verdict finalize`
dispatches to `python -m tools.sdlc_verdict finalize`, which argparse routes to a
`finalize` subparser. A top-level `review-finalize` is **not** in the allowlist
(would exit 2, "unknown subcommand") and would require editing the allowlist — an
avoidable update-system change. Keeping it under `verdict` also keeps the whole
verdict producer surface cohesive in one module. **No allowlist edit; no update-system
change.**

The orchestration logic lives in a dedicated, unit-testable helper module
`tools/sdlc_review_finalize.py` (holds `check_review_persistence`, the finalize
write-path, and the selfcheck read-path); `sdlc_verdict.main()` imports it and wires
the two subparsers. `sdlc_verdict.py` stays the single verdict-writer surface.

### Key Elements

- **`verdict finalize` atomic helper** (logic in `tools/sdlc_review_finalize.py`,
  dispatched via `sdlc-tool verdict finalize`): given `--pr`, `--issue-number`,
  `--verdict`, `--blockers`, `--tech-debt`, `--run-id`, it computes the PR head SHA
  itself, records the verdict with the `REVIEW_CONTEXT head_sha=` trailer appended
  (idempotent if already present), writes the REVIEW `completed` marker on the
  APPROVED path, reads all three back, and exits **non-zero with a named error** if any
  is missing. Collapses the hand-run 3-call sequence into one operation that cannot
  partially complete. `finalize` is state-mutating and REQUIRES `--run-id` (mirrors
  `record`'s `requires_run_id=True` gate + heal path in `sdlc_verdict.main()`).
- **`verdict selfcheck` verify-only probe** (dispatched via `sdlc-tool verdict
  selfcheck`): given `--pr`, `--issue-number`, returns typed JSON
  `{ok, verdict_present, trailer_matches_head, marker_completed, reason}`. Read-only,
  no `--run-id`. **The `/do-sdlc` supervisor calls it post-return and advances past
  REVIEW *only* on `ok:true`** — this gate is committed scope (see below), the
  load-bearing loud backstop.
- **Writer/gate trailer enforcement**: extend the existing WS3c completion-marker gate
  in `tools/sdlc_stage_marker.py` so a REVIEW `completed` marker on the APPROVED path
  also requires a well-formed `REVIEW_CONTEXT head_sha=<40-hex>` trailer on the recorded
  verdict (reusing `merge_predicate._HEAD_SHA_TRAILER_RE`). Fails closed. Closes failure
  #2 at the same gate that already closes the #1642 desync — no new dependency.
- **Shared persistence-check module**: `finalize` (write+verify) and `selfcheck`
  (verify-only) share one `check_review_persistence(pr, issue) -> dict` function so the
  two paths can never disagree (single-source invariant, mirroring the sdlc_verdict
  single-writer pattern).
- **Committed supervisor self-check gate** (resolves former Open Question #2): the
  `/do-sdlc` supervisor calls `sdlc-tool verdict selfcheck --pr N --issue-number M`
  after `do-pr-review` returns and **advances past REVIEW strictly conditional on
  `ok:true`**. On `ok:false` it halts and prints the machine-readable `reason` — a
  single loud refusal instead of the router's silent re-loop. This is the genuine
  un-skippable backstop and is **in committed scope for this slug** (not deferred). It
  lands in the supervisor skill body, NOT in the router decision table — the router's
  existing rows 8/8b/9 already fail-closed and self-heal once the skill calls the
  atomic `finalize` (see Rabbit Holes; touching router rows stays out of scope).

### Flow

`/do-pr-review` posts review → calls `sdlc-tool verdict finalize --pr N --issue-number M --verdict "APPROVED" --run-id …` (atomic: record+trailer+marker+readback) → helper exits 0 only when all three persisted → skill emits OUTCOME. Supervisor, on skill return, calls `sdlc-tool verdict selfcheck --pr N --issue-number M` → `ok:true` advances / `ok:false` refuses and surfaces the named reason.

### Technical Approach

- **Reuse, don't reinvent.** `record_verdict` stays the single writer; `finalize`
  orchestrates `record_verdict` + `stage-marker completed` + readback. The trailer regex
  is **hoisted** to `tools/_sdlc_utils.py` as the single definition (see Risk 3 for the
  exact import-edge accounting): `merge_predicate.py`, `sdlc_stage_marker.py`, and the
  new `sdlc_review_finalize.py` all consume it from there.
- **Trailer handling.** `finalize` appends ` REVIEW_CONTEXT head_sha=<40hex>` to the
  verdict string if not already present (idempotent), so the skill can pass a bare
  `"APPROVED"` and the helper guarantees the trailer. The head SHA comes from
  `gh pr view <pr> --json headRefOid -q .headRefOid`.
- **Fail-closed semantics.** Every probe (verdict present, trailer well-formed, marker
  completed) fails CLOSED — any error ⇒ `ok:false` / non-zero exit — matching the
  existing WS3c/WS-D convention (`_review_verdict_readable` fails closed on any error).
- **Named errors.** Reuse the established taxonomy: `REVIEW_VERDICT_MISSING`,
  `REVIEW_TRAILER_MISSING` (new), `REVIEW_MARKER_INCOMPLETE`. Non-zero exit is the loud
  signal (matches `sdlc_verdict.main`'s exit-1-on-failure pattern).
- **Marker-gate extension** is additive: the current `_review_verdict_readable` check
  stays; a new `_review_trailer_present` conjunct is AND-ed into the APPROVED-path
  completion gate. Non-APPROVED verdicts (CHANGES REQUESTED, BLOCKED_ON_CONFLICT,
  PR_CLOSED) are exempt — they legitimately carry no head_sha trailer and leave the
  marker `in_progress`.
- **Skill wiring.** Replace the 3-call block in `docs/sdlc/do-pr-review.md` "Verdict
  recording" with the single `sdlc-tool verdict finalize` invocation, and update
  `SKILL.md` Step 5 / Hard Rule #8 to state the OUTCOME block MUST NOT be emitted until
  `finalize` exits 0. Add the committed supervisor `sdlc-tool verdict selfcheck` call to
  the `/do-sdlc` supervisor skill (`.claude/skills/do-sdlc/SKILL.md`) post-review, with
  advance-past-REVIEW gated strictly on `ok:true`.
- **`sdlc_verdict.main()` subparser wiring.** Add two subparsers mirroring `record`/`get`:
  `finalize` (with `--pr`, `--issue-number`, `--verdict`, `--blockers`, `--tech-debt`,
  `--run-id`; `set_defaults(func=_cli_finalize, requires_run_id=True)` so it inherits the
  existing RUN_ID_REQUIRED gate + heal path at lines 586-598) and `selfcheck` (with
  `--pr`, `--issue-number`; no run-id). Both `func`s delegate into
  `tools/sdlc_review_finalize.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `check_review_persistence` and the `gh pr view` head-SHA fetch must fail CLOSED — a `gh` error or Redis error yields `ok:false` / non-zero, never a false pass. Add tests asserting each error branch returns the fail-closed result (not `pass`).
- [ ] The existing `_review_verdict_readable` fails closed on any error (line 128-129); the new `_review_trailer_present` conjunct must follow the same pattern — test the exception branch.

### Empty/Invalid Input Handling
- [ ] `finalize`/`selfcheck` with a verdict of `""`, `None`, or whitespace → named error, no partial write (mirror `record_verdict`'s empty-verdict guard, lines 312-314).
- [ ] Trailer regex against a malformed/short SHA (< 40 hex, non-hex) → `REVIEW_TRAILER_MISSING`, not a false match.
- [ ] `selfcheck` when NO verdict was ever recorded (failure #1) → `ok:false, verdict_present:false` — the load-bearing case; this is the state the skill left when it wrote nothing.

### Error State Rendering
- [ ] Non-zero exit from `finalize` must print the named error to stderr (loud, like `sdlc_verdict.main`) so a `/do-sdlc` operator sees why the skill refused to complete.
- [ ] `selfcheck` `ok:false` reason must be machine-readable JSON the supervisor can branch on, AND surfaced to the human, not swallowed.

## Test Impact

- [ ] `tests/` REVIEW-verdict/marker suite (locate via `grep -rl "_review_verdict_readable\|stage-marker.*REVIEW\|record_verdict" tests/`) — UPDATE: add cases for the new trailer conjunct in the completion-marker gate; existing "marker refused without verdict" cases stay valid.
- [ ] `tests/` merge-predicate freshness tests (`grep -rl "_check_verdict_freshness\|head_sha" tests/`) — UPDATE only if the trailer regex is hoisted to a shared constant (import path change); behavior unchanged.
- [ ] New `tests/unit/test_sdlc_review_finalize.py` — REPLACE/CREATE: atomic finalize (record+trailer+marker+readback), each named-error branch, idempotent trailer append, self-check pass/fail matrix, and `sdlc-tool verdict finalize`/`selfcheck` subparser dispatch (assert the subparsers are registered in `sdlc_verdict.main()`).
- [ ] Regex-hoist regression (in the merge-predicate/`_sdlc_utils` test module) — add `import tools.merge_predicate` cycle-guard assertion per Risk 3.
- [ ] Supervisor-gate test — assert the `/do-sdlc` supervisor advances past REVIEW only on `selfcheck ok:true` and halts+surfaces on `ok:false`. If the supervisor gate is skill-body prose (not a Python function), cover it with the local-round-trip integration check in Agent Integration instead and note that here.
- [ ] No existing test asserts the *absence* of trailer enforcement, so nothing needs deletion — the change is additive over the current truthiness-only gate.

## Rabbit Holes

- **Do NOT rewrite the router's verdict-gate rows (8/8b/9).** They already re-dispatch correctly; the fix is at the producer + supervisor, not the router's decision table. Touching the router risks regressing the #2076/#1932 gate logic.
- **Do NOT add a `gh` dependency to `record_verdict`.** Keep the single-writer pure; the head-SHA fetch lives only in the `finalize` orchestrator, which already needs `gh` for the PR.
- **Do NOT make trailer enforcement retroactive for CHANGES REQUESTED / short-circuit verdicts.** They legitimately lack a trailer; over-constraining them would break the BLOCKED_ON_CONFLICT / PR_CLOSED paths.
- **Do NOT try to auto-repair a stale/mismatched trailer by re-reviewing.** Finalize only guarantees the trailer matches the *current* head at finalize time; head-drift after finalize is the merge predicate's job, already handled.
- **Avoid a full second consumer path.** `finalize` and `selfcheck` must share one `check_review_persistence` — do not fork the readback logic.

## Risks

### Risk 1: Trailer enforcement over-fires and blocks legitimate completions
**Impact:** A REVIEW `completed` marker is refused for a CHANGES REQUESTED or preflight short-circuit verdict that correctly has no trailer, stalling the pipeline the opposite way.
**Mitigation:** Gate the trailer conjunct strictly on the APPROVED path (verdict normalizes to `APPROVED`). Non-APPROVED verdicts leave the marker `in_progress` by contract and are never subject to the trailer check. Unit-test each non-APPROVED verdict passes the gate untouched.

### Risk 2: `gh pr view` unavailable in the finalize path
**Impact:** Head-SHA fetch fails, `finalize` cannot append the trailer.
**Mitigation:** Fail CLOSED with `REVIEW_TRAILER_MISSING` (non-zero) rather than recording a trailer-less verdict — the loud failure is strictly better than the silent stall the issue describes. The supervisor's `selfcheck` also surfaces it. `gh` is already a hard pipeline dependency.

### Risk 3: Import-edge coupling for the shared trailer regex
**Impact:** Circular or fragile import if `finalize`/`stage_marker` import `_HEAD_SHA_TRAILER_RE` directly from `merge_predicate`.
**Mitigation:** Hoist `_HEAD_SHA_TRAILER_RE` (currently defined at `tools/merge_predicate.py:104`) to `tools/_sdlc_utils.py` as the single definition. Accurate import-edge accounting (verified against the code, not assumed):
- `tools/sdlc_stage_marker.py` **already** imports from `tools._sdlc_utils` (line 86) → **no new edge**; the gate consumes the hoisted constant for free.
- `tools/sdlc_review_finalize.py` is new → it imports `_sdlc_utils` as an ordinary dependency (no pre-existing edge to disturb).
- `tools/merge_predicate.py` does **not** currently import `_sdlc_utils` (verified: `grep _sdlc_utils tools/merge_predicate.py` → no match) → hoisting adds **exactly one new edge** `merge_predicate → _sdlc_utils`. This is **acyclic**: `_sdlc_utils` does not import `merge_predicate` (verified), so no cycle is created. The earlier "no new edge" claim was wrong and is corrected here.

The regression test for the hoist (task build-regex-hoist) MUST assert the modules still import cleanly after the edge is added — in particular `import tools.merge_predicate` succeeds (guards against an accidental cycle) — in addition to asserting the constant matches raw and normalized trailer forms identically to before.

## Race Conditions

### Race 1: Head SHA drifts between finalize and router read
**Location:** `tools/sdlc_review_finalize.py` (head-SHA fetch) vs `merge_predicate._check_verdict_freshness`
**Trigger:** A new commit lands on the PR branch after `finalize` records the trailer but before MERGE.
**Data prerequisite:** The recorded trailer must reflect the head that was actually reviewed.
**State prerequisite:** Verdict freshness is evaluated against the *current* PR head at merge time.
**Mitigation:** Not this plan's job to solve — head-drift-after-review is exactly what the merge predicate's SHA-freshness rung already catches (it re-reviews on mismatch). `finalize` only guarantees the trailer matches the head at finalize time, which is correct. No new race introduced.

### Race 2: Concurrent finalize + marker write
**Location:** `record_verdict` + `stage-marker completed` within `finalize`
**Trigger:** Two finalize calls for the same issue (should not happen under single-owner lease, but defensively).
**Data prerequisite:** Marker must never be `completed` without a readable verdict+trailer.
**State prerequisite:** The WS3c gate already serializes marker-after-verdict.
**Mitigation:** The completion-marker gate (extended here) is the serialization point — it re-reads the verdict at marker-write time. The run_id lease (#2003) already prevents concurrent finalize for one issue.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] **Router-level mechanical selfcheck gate.** Making `sdlc-tool
  next-skill` (the router) itself consult `selfcheck` so a *non-`/do-sdlc`* local run is
  protected without a supervisor is explicitly deferred to a separate slug. It requires
  touching `agent/sdlc_router.py` rows 8/8b/9 (a Rabbit Hole here) and is unnecessary for
  the reported incident: the router already fails-closed via re-dispatch, and that
  re-dispatch now self-heals because the skill calls atomic `finalize`. This resolves the
  "both in this slug?" half of former Open Question #2 — the answer is **supervisor gate
  now, router integration later if ever needed.**
- **CHANGES REQUESTED / short-circuit verdict trailer (resolves former Open Question #3):**
  trailer enforcement stays **strictly APPROVED-only**. `finalize` may harmlessly append a
  trailer on a CHANGES REQUESTED verdict (freshness data for re-review rounds is fine) but
  **never gates or requires it** for non-APPROVED verdicts — the completion-marker trailer
  conjunct fires only on the APPROVED path (Risk 1). This minimizes blast radius and keeps
  the BLOCKED_ON_CONFLICT / PR_CLOSED paths untouched.
- Beyond the deferred router gate above, nothing else is deferred — atomic finalize,
  committed supervisor self-check gate, trailer-gate extension, skill wiring, tests, and
  docs are all in scope. The router decision-table and merge-predicate downstream checks
  are deliberately left untouched (see Rabbit Holes) because they already behave correctly.

## Update System

No update system changes required — this is a purely internal SDLC-substrate change.
The new subcommands are added as `finalize`/`selfcheck` subparsers **inside
`tools/sdlc_verdict.py`**, reached through the *existing* `verdict` entry in
`scripts/sdlc-tool`'s `ALLOWED_SUBCOMMANDS` — so **`scripts/sdlc-tool` is NOT edited**
and no allowlist propagation is needed (this was the deciding factor for the tool-surface
choice; a top-level `review-finalize` would have required an allowlist edit = an update-
system change, and is therefore rejected). The do-pr-review skill lives in
`.claude/skills-global/` and the `/do-sdlc` supervisor skill in `.claude/skills/`; both
are already synced by `/update`'s existing hardlink wiring (no new sync entry). No new
dependencies, config files, or migrations.

## Agent Integration

No new agent/MCP surface required — this is a bridge-internal SDLC-pipeline change. The
finalize/self-check tools are invoked by SDLC skills via the existing `sdlc-tool` CLI
entry point (already in `pyproject.toml [project.scripts]`), not by the conversational
agent through MCP. Integration is verified by the do-pr-review skill's OUTCOME contract
and the supervisor `selfcheck` call, exercised in a local `/do-sdlc` run.

- Confirm `finalize`/`selfcheck` register as subparsers in `tools/sdlc_verdict.main()` and resolve via `sdlc-tool verdict finalize` / `sdlc-tool verdict selfcheck` (the existing `verdict`→`tools.sdlc_verdict` allowlist mapping) — no `scripts/sdlc-tool` edit.
- Integration check: a scripted local `sdlc-tool verdict finalize` round-trip asserts verdict+trailer+marker all persist and `sdlc-tool verdict selfcheck` returns `ok:true`; then a deliberately-incomplete state asserts `selfcheck` returns `ok:false` and the supervisor refuses to advance.

## Documentation

### Feature Documentation
- [ ] Update `docs/sdlc/do-pr-review.md` — replace the 3-call "Verdict recording" block (lines 76-102) with the single `sdlc-tool verdict finalize` invocation; document the `selfcheck` supervisor call and the new trailer-gate behavior in "Mandatory Finalize".
- [ ] Update `.claude/skills-global/do-pr-review/SKILL.md` Step 5 + Hard Rule #8 to reference the atomic finalize helper and state OUTCOME must not emit until it exits 0.
- [ ] Add/update `docs/features/` coverage of the SDLC verdict-persistence contract (extend the existing multi-judge / verdict-substrate feature doc, or add a short `docs/features/sdlc-verdict-fail-closed-persistence.md`) and link it from `docs/features/README.md`.

### External Documentation Site
- [ ] N/A — no external docs site for SDLC substrate internals.

### Inline Documentation
- [ ] Docstrings on `tools/sdlc_review_finalize.py` (finalize + selfcheck + `check_review_persistence`) explaining the fail-closed contract and named-error taxonomy.
- [ ] Comment on the new `_review_trailer_present` conjunct in `sdlc_stage_marker.py` citing #2193 and the APPROVED-only scope.

## Success Criteria

- [ ] `sdlc-tool verdict finalize` records verdict + head_sha trailer + REVIEW completed marker atomically and exits non-zero (named error) if any of the three fails to read back.
- [ ] `sdlc-tool verdict selfcheck` returns `{ok:false, ...}` for each of the three observed failure states (null verdict, trailer-less verdict, non-completed marker) and `{ok:true}` only when all three persisted.
- [ ] The REVIEW completion-marker gate refuses an APPROVED-path `completed` marker when the recorded verdict lacks a well-formed `REVIEW_CONTEXT head_sha=<40-hex>` trailer.
- [ ] Non-APPROVED verdicts (CHANGES REQUESTED / BLOCKED_ON_CONFLICT / PR_CLOSED) pass the gate untouched and leave the marker `in_progress`.
- [ ] `do-pr-review` skill + `docs/sdlc/do-pr-review.md` invoke the single `sdlc-tool verdict finalize` helper; the `/do-sdlc` supervisor calls `sdlc-tool verdict selfcheck` post-return and advances past REVIEW **only** on `ok:true`, halting+surfacing the reason on `ok:false`.
- [ ] `sdlc-tool verdict finalize` and `sdlc-tool verdict selfcheck` resolve via the existing `verdict`→`tools.sdlc_verdict` allowlist entry — no `scripts/sdlc-tool` `ALLOWED_SUBCOMMANDS` edit, no top-level `review-finalize` name anywhere.
- [ ] `pyproject.toml` #2004 off-limits comment reconciled (plan landed at 19829e66b); S110/S112 allowlist entries retained.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `docs/sdlc/do-pr-review.md` references `verdict finalize` and no longer instructs the bare 3-call sequence as the primary path.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (finalize-tool)**
  - Name: finalize-builder
  - Role: Implement `tools/sdlc_review_finalize.py` (finalize + selfcheck + shared `check_review_persistence`) and register the subcommands under `sdlc-tool`.
  - Agent Type: builder
  - Domain: async/Redis-Popoto data (verdict substrate)
  - Resume: true

- **Builder (gate-extension)**
  - Name: gate-builder
  - Role: Extend the WS3c completion-marker gate in `tools/sdlc_stage_marker.py` with the APPROVED-only trailer conjunct; hoist `_HEAD_SHA_TRAILER_RE` to `_sdlc_utils`.
  - Agent Type: builder
  - Resume: true

- **Builder (skill-wiring)**
  - Name: skill-builder
  - Role: Rewire `docs/sdlc/do-pr-review.md` + `SKILL.md` to the single finalize call; add the supervisor `selfcheck` call.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: finalize-tester
  - Role: Unit tests for finalize/selfcheck/gate per Test Impact + Failure Path Test Strategy.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: verdict-validator
  - Role: Verify all Success Criteria + Verification table.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Hoist shared trailer regex
- **Task ID**: build-regex-hoist
- **Depends On**: none
- **Validates**: `tests/` merge-predicate freshness tests still pass
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Move `_HEAD_SHA_TRAILER_RE` from `tools/merge_predicate.py:104` into `tools/_sdlc_utils.py`; update `merge_predicate.py` to import it (this is the one new `merge_predicate → _sdlc_utils` edge — acyclic, see Risk 3).
- Keep behavior identical; add a regression test that (a) the constant matches both raw and normalized trailer forms as before, and (b) `import tools.merge_predicate` succeeds after the edge is added (cycle guard).

### 2. Implement finalize + selfcheck helper
- **Task ID**: build-finalize
- **Depends On**: build-regex-hoist
- **Validates**: tests/unit/test_sdlc_review_finalize.py (create)
- **Assigned To**: finalize-builder
- **Agent Type**: builder
- **Domain**: async/Redis-Popoto data
- **Parallel**: false
- Create `tools/sdlc_review_finalize.py` with `check_review_persistence(pr, issue)`, a `finalize` write-path (compute head SHA via `gh pr view <pr> --json headRefOid -q .headRefOid`, record verdict+trailer via `record_verdict`, write REVIEW completed marker on APPROVED, read all three back), and a read-only `selfcheck` path. Both call-side entrypoints (`_cli_finalize`, `_cli_selfcheck`) live here and are imported by `sdlc_verdict.main()`.
- Named errors: `REVIEW_VERDICT_MISSING`, `REVIEW_TRAILER_MISSING`, `REVIEW_MARKER_INCOMPLETE`; non-zero exit + stderr on any.
- Register the subcommands **only** as `finalize` / `selfcheck` subparsers inside `tools/sdlc_verdict.main()` (so `sdlc-tool verdict finalize` / `sdlc-tool verdict selfcheck` resolve). Do NOT add a top-level `review-finalize`/`review-selfcheck` and do NOT edit `scripts/sdlc-tool`'s `ALLOWED_SUBCOMMANDS`. `finalize` uses `set_defaults(func=_cli_finalize, requires_run_id=True)` (inherits the RUN_ID_REQUIRED gate + heal path); `selfcheck` takes no `--run-id`.

### 3. Extend the completion-marker gate
- **Task ID**: build-gate
- **Depends On**: build-regex-hoist
- **Validates**: REVIEW-marker gate tests (existing + new trailer cases)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_review_trailer_present` (fail-closed) and AND it into the APPROVED-path REVIEW `completed` gate in `tools/sdlc_stage_marker.py`.
- Ensure non-APPROVED verdicts bypass the trailer conjunct.

### 4. Wire the skill + supervisor
- **Task ID**: build-skill-wiring
- **Depends On**: build-finalize
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the 3-call block in `docs/sdlc/do-pr-review.md` with the single `sdlc-tool verdict finalize` invocation; update `SKILL.md` Step 5 + Hard Rule #8 (OUTCOME must not emit until finalize exits 0).
- Add the committed supervisor `sdlc-tool verdict selfcheck` call post-review in `.claude/skills/do-sdlc/SKILL.md`; advance past REVIEW **only** on `ok:true`, and on `ok:false` halt and surface the machine-readable `reason`. Do NOT modify `agent/sdlc_router.py` rows (Rabbit Hole).

### 4b. Reconcile stale #2004 off-limits marker (chore)
- **Task ID**: chore-pyproject-reconcile
- **Depends On**: build-gate
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- `pyproject.toml:136-138` claims `tools/sdlc_*.py` / `tools/_sdlc_utils.py` are "owned by the concurrent sdlc-run-ownership-merge-enforcement plan — off-limits to this sweep". That plan **migrated to completed at commit 19829e66b (2026-07-11)** — it is no longer concurrent and the files are no longer off-limits. Update the comment to reflect that the plan landed (drop "concurrent"/"off-limits" framing) while keeping the S110/S112 allowlist entries, which remain the correct policy for these silent-except sites. The new `tools/sdlc_review_finalize.py` matches the existing `tools/sdlc_*.py` glob, so it inherits the ignore with no additional entry.

### 5. Tests
- **Task ID**: build-tests
- **Depends On**: build-finalize, build-gate
- **Assigned To**: finalize-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Implement the Test Impact + Failure Path Test Strategy cases: atomic finalize, each named-error branch, idempotent trailer append, selfcheck pass/fail matrix, gate trailer cases, non-APPROVED bypass, fail-closed error branches.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-skill-wiring, build-tests
- **Assigned To**: skill-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Feature doc + `docs/features/README.md` index entry; inline docstrings/comments per Documentation section.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-gate, build-skill-wiring, chore-pyproject-reconcile, document-feature
- **Assigned To**: verdict-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_review_finalize.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| finalize subcommand registered | `sdlc-tool verdict finalize --help` | exit code 0 |
| selfcheck subcommand registered | `sdlc-tool verdict selfcheck --help` | exit code 0 |
| Skill wired to finalize | `grep -c "verdict finalize" docs/sdlc/do-pr-review.md` | output > 0 |
| Trailer gate present | `grep -c "_review_trailer_present" tools/sdlc_stage_marker.py` | output > 0 |
| Regex hoisted (no merge_predicate import edge in stage_marker) | `grep -c "from tools.merge_predicate" tools/sdlc_stage_marker.py` | match count == 0 |
| No cycle after hoist | `python -c "import tools.merge_predicate"` | exit code 0 |
| No top-level review-finalize allowlisted | `grep -c "review-finalize\|review-selfcheck" scripts/sdlc-tool` | 0 |
| Supervisor gates on selfcheck | `grep -c "verdict selfcheck" .claude/skills/do-sdlc/SKILL.md` | output > 0 |
| #2004 marker reconciled | `grep -c "concurrent sdlc-run-ownership" pyproject.toml` | 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | B1 | Tool surface inconsistent/unreachable (`review-finalize` not in `sdlc-tool` allowlist; standalone module vs `verdict finalize` mismatch) | Tool Surface Decision section; Key Elements; Technical Approach subparser wiring; task build-finalize; Update System; Verification | Chose `sdlc-tool verdict finalize`/`selfcheck` as subparsers in `sdlc_verdict.main()` (dispatches via existing `verdict`→`tools.sdlc_verdict` mapping). Logic in helper `tools/sdlc_review_finalize.py`. `review-finalize` naming dropped entirely; `ALLOWED_SUBCOMMANDS` untouched. Open Question #1 resolved. |
| BLOCKER | B2 | Mechanism still skippable; genuine backstop soft-scoped in OQ#2 while Rabbit Holes forbids the router rows where enforcement lands | Problem "Why this closes the root cause"; Data Flow; Key Elements (committed supervisor gate); Success Criteria; Test Impact; No-Gos | Committed the supervisor `selfcheck` gate (advance-past-REVIEW strictly conditional on `ok:true`) to this slug — the loud backstop. Router rows stay untouched: they already fail-closed and self-heal because the skill now calls atomic `finalize`. Router-level selfcheck integration deferred [SEPARATE-SLUG]. |
| CONCERN | C1 | Stale `pyproject.toml:136-140` #2004 "off-limits" marker | task chore-pyproject-reconcile; Success Criteria; Verification | Owning plan `sdlc-run-ownership-merge-enforcement` migrated to completed at 19829e66b (2026-07-11) — no longer concurrent. Comment reconciled; S110/S112 allowlist entries retained. |
| CONCERN | C2 | Risk 3 wrongly claimed "no new import edge" for the regex hoist | Risk 3 (corrected); Technical Approach; task build-regex-hoist; Test Impact | Verified: `merge_predicate` does not import `_sdlc_utils` → hoist adds exactly one edge `merge_predicate → _sdlc_utils` (acyclic). `sdlc_stage_marker` already imports `_sdlc_utils` (no new edge). Regression test asserts `import tools.merge_predicate` (cycle guard). |
