---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2193
last_comment_id:
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
cannot partially complete, plus a **post-return self-check** the supervisor/router
can call to refuse advancing when persistence is incomplete. An APPROVED review can
never leave the skill with a null verdict, a trailer-less verdict, or a non-completed
REVIEW marker.

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

**After the fix**, step 3 becomes a single `sdlc-tool review-finalize` call that is
atomic and self-verifying, and step 4's supervisor gains a `review-selfcheck` probe
that refuses to advance (surfacing loudly) instead of silently re-looping.

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

### Key Elements

- **`review-finalize` atomic helper** (`tools/sdlc_review_finalize.py`, exposed as
  `sdlc-tool verdict finalize` / a `review-finalize` subcommand): given `--pr`,
  `--issue-number`, `--verdict`, `--blockers`, `--tech-debt`, `--run-id`, it computes
  the PR head SHA itself, records the verdict with the `REVIEW_CONTEXT head_sha=` trailer
  appended (idempotent if already present), writes the REVIEW `completed` marker on the
  APPROVED path, reads all three back, and exits **non-zero with a named error** if any
  is missing. Collapses the hand-run 3-call sequence into one operation that cannot
  partially complete.
- **`review-selfcheck` verify-only probe** (`sdlc-tool verdict selfcheck` / a
  `review-selfcheck` subcommand): given `--pr`, `--issue-number`, returns typed JSON
  `{ok, verdict_present, trailer_matches_head, marker_completed, reason}`. Read-only,
  no `--run-id`. The supervisor/router calls it post-return to refuse advancing on an
  incomplete-persistence state.
- **Writer/gate trailer enforcement**: extend the existing WS3c completion-marker gate
  in `tools/sdlc_stage_marker.py` so a REVIEW `completed` marker on the APPROVED path
  also requires a well-formed `REVIEW_CONTEXT head_sha=<40-hex>` trailer on the recorded
  verdict (reusing `merge_predicate._HEAD_SHA_TRAILER_RE`). Fails closed. Closes failure
  #2 at the same gate that already closes the #1642 desync — no new dependency.
- **Shared persistence-check module**: `finalize` (write+verify) and `selfcheck`
  (verify-only) share one `check_review_persistence(pr, issue) -> dict` function so the
  two paths can never disagree (single-source invariant, mirroring the sdlc_verdict
  single-writer pattern).

### Flow

`/do-pr-review` posts review → calls `sdlc-tool verdict finalize --pr N --issue-number M --verdict "APPROVED" --run-id …` (atomic: record+trailer+marker+readback) → helper exits 0 only when all three persisted → skill emits OUTCOME. Supervisor, on skill return, calls `sdlc-tool verdict selfcheck --pr N --issue-number M` → `ok:true` advances / `ok:false` refuses and surfaces the named reason.

### Technical Approach

- **Reuse, don't reinvent.** `record_verdict` stays the single writer; `finalize`
  orchestrates `record_verdict` + `stage-marker completed` + readback. The trailer regex
  is imported from `merge_predicate` (or hoisted to a shared `_sdlc_utils` constant so
  both consume one definition — decide during build; prefer hoist to avoid a
  tools→merge_predicate import edge).
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
  recording" with the single `finalize` invocation, and update `SKILL.md` Step 5 / Hard
  Rule #8 to state the OUTCOME block MUST NOT be emitted until `finalize` exits 0. Add
  the supervisor `selfcheck` call to `.claude/skills/do-sdlc` (or `sdlc` router skill)
  post-review.

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
- [ ] New `tests/unit/test_sdlc_review_finalize.py` — REPLACE/CREATE: atomic finalize (record+trailer+marker+readback), each named-error branch, idempotent trailer append, self-check pass/fail matrix.
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

### Risk 3: Import-edge coupling (`tools` → `merge_predicate`) for the shared regex
**Impact:** Circular or fragile import if `finalize`/`stage_marker` import from `merge_predicate`.
**Mitigation:** Hoist `_HEAD_SHA_TRAILER_RE` to `tools/_sdlc_utils.py` (already imported by both `sdlc_verdict` and `sdlc_stage_marker`) and have `merge_predicate` consume it too — one definition, no new edge.

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

- [SEPARATE-SLUG] Nothing deferred to a separate slug.
- Nothing deferred — every relevant item (atomic finalize, self-check, trailer-gate extension, skill wiring, tests, docs) is in scope for this plan. The router decision-table and merge-predicate downstream checks are deliberately left untouched (see Rabbit Holes) because they already behave correctly.

## Update System

No update system changes required — this is a purely internal SDLC-substrate change.
The new `sdlc-tool` subcommands (`verdict finalize`, `verdict selfcheck`) ship with the
repo and are invoked in-process; the do-pr-review skill lives in `.claude/skills-global/`
and is already synced by `/update`'s existing hardlink wiring (no new sync entry). No new
dependencies, config files, or migrations.

## Agent Integration

No new agent/MCP surface required — this is a bridge-internal SDLC-pipeline change. The
finalize/self-check tools are invoked by SDLC skills via the existing `sdlc-tool` CLI
entry point (already in `pyproject.toml [project.scripts]`), not by the conversational
agent through MCP. Integration is verified by the do-pr-review skill's OUTCOME contract
and the supervisor `selfcheck` call, exercised in a local `/do-sdlc` run.

- Confirm the new subcommands register under the existing `sdlc-tool` argparse dispatcher (`tools/sdlc_*` → the `sdlc-tool` console script).
- Integration check: a scripted local review-finalize round-trip asserts verdict+trailer+marker all persist and `selfcheck` returns `ok:true`.

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
- [ ] `do-pr-review` skill + `docs/sdlc/do-pr-review.md` invoke the single finalize helper; the supervisor calls `selfcheck` post-return.
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
- Move `_HEAD_SHA_TRAILER_RE` into `tools/_sdlc_utils.py`; update `merge_predicate.py` to import it.
- Keep behavior identical; add a regression test that the constant matches both raw and normalized trailer forms.

### 2. Implement finalize + selfcheck helper
- **Task ID**: build-finalize
- **Depends On**: build-regex-hoist
- **Validates**: tests/unit/test_sdlc_review_finalize.py (create)
- **Assigned To**: finalize-builder
- **Agent Type**: builder
- **Domain**: async/Redis-Popoto data
- **Parallel**: false
- Create `tools/sdlc_review_finalize.py` with `check_review_persistence(pr, issue)`, a `finalize` path (compute head SHA via `gh pr view`, record verdict+trailer via `record_verdict`, write REVIEW completed marker on APPROVED, read all three back), and a read-only `selfcheck` path.
- Named errors: `REVIEW_VERDICT_MISSING`, `REVIEW_TRAILER_MISSING`, `REVIEW_MARKER_INCOMPLETE`; non-zero exit + stderr on any.
- Register `verdict finalize` / `verdict selfcheck` (or `review-finalize` / `review-selfcheck`) under the `sdlc-tool` dispatcher. `finalize` requires `--run-id`; `selfcheck` does not.

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
- Replace the 3-call block in `docs/sdlc/do-pr-review.md` with the single `finalize` invocation; update `SKILL.md` Step 5 + Hard Rule #8.
- Add the supervisor `selfcheck` call post-review in the `/do-sdlc` (or `sdlc` router) skill; refuse to advance on `ok:false`.

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
- **Depends On**: build-tests, build-gate, build-skill-wiring, document-feature
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

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Tool surface naming:** `sdlc-tool verdict finalize` / `verdict selfcheck` (subcommands under the existing `verdict` group) vs. top-level `sdlc-tool review-finalize` / `review-selfcheck`? The former keeps the verdict-writer surface cohesive; the latter reads more discoverably at the review stage. Preference?
2. **Self-check owner:** should the post-return `selfcheck` live in the `/do-sdlc` supervisor skill only, or also be a mechanical gate the router (`sdlc-tool next-skill`) consults so even a non-`/do-sdlc` local run is protected? (Leaning: supervisor call now; router integration is a cheap follow-up if needed — but flagging in case you want both in this slug.)
3. **CHANGES REQUESTED trailer:** the addendum currently records a trailer on CHANGES REQUESTED too. Should `finalize` also guarantee/require it there (harmless, keeps freshness data on re-review rounds), or keep trailer enforcement strictly APPROVED-only to minimize blast radius?
