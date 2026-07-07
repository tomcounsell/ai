---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1932
last_comment_id:
revision_applied: true
---

# SDLC router: crashed re-review dead-end (row 8b) + row 3 open-PR step-aside

## Problem

Two dispatch gaps in `agent/sdlc_router.py::decide_next_dispatch()` were observed
while supervising the #1924 pipeline to merge. Both strand or misroute a pipeline
that the router should recover automatically.

**Current behavior:**

- **(a) Crashed re-review dead-ends the router.** After a patch, row 8b
  (`_rule_patch_applied_after_review`, line 956) dispatches `/do-pr-review` to
  re-review. If that re-review subagent crashes before recording a verdict,
  `last_dispatched_skill` is now `/do-pr-review` (not `/do-patch`), so row 8b
  no longer matches. Row 8c (`_rule_review_in_progress_no_verdict`, line 967)
  only fires when `REVIEW == in_progress`; if the crash left the REVIEW marker
  in any other state, 8c also misses. All 17 rows fall through →
  `Blocked("no matching dispatch rule")`. A human/supervisor must notice and
  re-dispatch the review by hand (exactly what happened in the #1924 endgame).

- **(b) Row 3 lacks a PR-exists step-aside.** Row 3
  (`_rule_critique_needs_revision`, line 691) has a staleness step-aside (#1639)
  but no open-PR guard. The G3 gate (`guard_g3_pr_lock`, line 330) only trips
  when `last_dispatched_skill ∈ {/do-plan, /do-plan-critique}` or a plan-family
  `proposed_skill` is supplied. When a PR is already open but the last dispatch
  was a PR-stage skill and no `proposed_skill` is passed, G3 returns None and a
  non-stale NEEDS REVISION critique verdict routes the pipeline back to
  `/do-plan` — re-opening plan work on shipped code.

**Desired outcome:**

- (a) A crashed re-review after a patch is recovered automatically: the router
  re-dispatches `/do-pr-review` instead of dead-ending, regardless of the exact
  stage-marker value the crash left behind.
- (b) A NEEDS REVISION critique verdict never routes to `/do-plan` when an open
  PR exists for the issue; row 3 steps aside and PR-stage rows own the state.

## Freshness Check

**Baseline commit:** `8485db994a98998e87378b8e13c76385a0d1a70d`
**Issue filed at:** 2026-07-07T05:36:58Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sdlc_router.py:956` `_rule_patch_applied_after_review` — claim holds. Returns `last == SKILL_DO_PATCH` after requiring `pr_number` and `PATCH == completed`.
- `agent/sdlc_router.py:967` `_rule_review_in_progress_no_verdict` (row 8c) — claim holds. Requires `REVIEW == STATUS_IN_PROGRESS`, steps aside when 8b matches.
- `agent/sdlc_router.py:691` `_rule_critique_needs_revision` — claim holds. Only step-aside is `_critique_verdict_is_stale`; no `pr_number` guard.
- `agent/sdlc_router.py:330` `guard_g3_pr_lock` — claim holds. Early-returns None unless `last`/`proposed` is in the plan family `{SKILL_DO_PLAN, SKILL_DO_PLAN_CRITIQUE}`.
- `Blocked("no matching dispatch rule")` sink confirmed at `agent/sdlc_router.py:1279`.

**Cited sibling issues/PRs re-checked:**
- #1760 — still OPEN (PLAN↔CRITIQUE convergence; different rules, same subsystem).
- #1871 — still OPEN (G5 fast-path vs plan_revising; different rule).
- #1639 / PR #1659 — merged; added row 2b stale-critique step-aside (direct mirror for fix b).
- #1668 / PR #1670 — merged; added row 2c CRITIQUE-empty-verdict recovery.
- #1641 — merged; row 8/8b ordering after patch.
- PR #1755 — merged; added row 8c REVIEW empty-verdict re-dispatch (direct mirror for fix a).

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=<createdAt> -- agent/sdlc_router.py tests/unit/test_sdlc_router.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** None touching the router dispatch table. `sdlc-1111.md` is unrelated (a specific issue plan).

**Notes:** No drift. Line numbers cited in the issue are exact against baseline.

## Prior Art

- **PR #1755** — `fix(router): add row 8c REVIEW empty-verdict re-dispatch rule (Gap A)`. Added `_rule_review_in_progress_no_verdict` to recover a stalled review that never recorded a verdict. Succeeded, but is gated on `REVIEW == in_progress` — it does not cover the *patch-then-crashed-re-review* variant this issue reports. **The row-8c fix is the structural template for fix (a).**
- **PR #1670 / #1668** — Added row 2c (`_rule_critique_in_progress_no_verdict`) for the CRITIQUE-side empty-verdict dead-end. Made "marker-agnostic by design" in later hardening. **Precedent that recovery predicates should not hinge on a specific marker value.**
- **PR #1659 / #1639** — Added row 2b stale-critique step-aside (`_critique_verdict_is_stale`) so a NEEDS REVISION verdict on an already-revised plan re-critiques instead of dead-ending on `/do-plan`. **Row 3's staleness step-aside; fix (b) adds a sibling open-PR step-aside right next to it.**
- **PR #1657** — Verdict normalization + plan-existence gate + stale-verdict supersession. Establishes the `normalize_verdict` / `_latest_*_verdict` helpers this plan reuses.
- **#1687** — Confirm/refute reported router dead-ends; closed the REVIEW empty-verdict + non-persisted critique verdict gaps. Same investigation lineage.

## Why Previous Fixes Failed

The prior fixes did not "fail" — each closed a specific dead-end. This issue is the
**next variant in the same family**: the recovery rows added so far each carry a
narrow marker/last-dispatch guard, and the guards leave a seam.

| Prior Fix | What It Did | Why It Doesn't Cover This Case |
|-----------|-------------|-------------------------------|
| PR #1755 (row 8c) | Recovers a stalled review with empty verdict | Gated on `REVIEW == in_progress`; a crashed re-review can leave a different marker state |
| PR #1641 (row 8b) | Re-review after patch | Gated on `last == /do-patch`; a re-review already dispatched (then crashed) makes `last == /do-pr-review` |
| PR #1659 (row 2b) | Stale-critique → re-critique step-aside | Row 3 got a *staleness* step-aside but never an *open-PR* step-aside |

**Root cause pattern:** recovery/step-aside predicates are written tightly against
the *expected* `last_dispatched_skill` and marker value. When a subagent crashes,
those exact values don't hold, and the router has no marker-agnostic backstop for
the "PATCH done + PR open + no review verdict" state. Fix (a) closes that seam;
fix (b) generalizes the plan-stage lock so it does not depend on `last` being a
plan-family skill.

## Data Flow

1. **Entry point:** `sdlc-tool next-skill --issue-number N` → `decide_next_dispatch(stage_states, meta, context)`.
2. **Guards (G1–G7)** run first (`GUARDS` list, line 625). G3 (`guard_g3_pr_lock`) can redirect plan-stage dispatches when a PR exists — but only if `last`/`proposed` is plan-family.
3. **Dispatch table** (`DISPATCH_TABLE`, ~line 1078) evaluated top-to-bottom; first matching `state_predicate` wins. Rows 2b/2c/3 own CRITIQUE recovery; rows 8/8b/8c own REVIEW recovery.
4. **Output:** first matching `DispatchRule` → `Dispatch(skill, reason, row_id)`, else `Blocked("no matching dispatch rule")` (line 1279).

The two bugs live entirely in step 2 (G3 seam for fix b) and step 3 (missing REVIEW recovery predicate for fix a). No state is written by the router — it is a pure decision function over `stage_states`/`meta`/`context`, which is why the reproduction is a straightforward table-driven unit test.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none. Both fixes are internal predicate changes plus (for fix a) one new `DispatchRule` row appended to `DISPATCH_TABLE`. `decide_next_dispatch` signature and return types are unchanged.
- **Coupling:** unchanged. The new row and the row-3 guard use existing helpers (`_latest_review_verdict`, `_critique_verdict_is_stale`, `meta.get("pr_number")`).
- **Data ownership:** unchanged.
- **Reversibility:** trivial — revert the predicate edits and the one new row.
- **Parity contract:** the router's row set is mirrored in `.claude/skills/sdlc/SKILL.md`, which currently claims "16 rows." Verified against `git grep -c 'DispatchRule(' agent/sdlc_router.py` on baseline commit `8485db99`: the table actually has **17** rows today (`1,2,2b,2c,3,4a,4b,4c,5,6,7,8,8b,8c,9,10,10b`) — SKILL.md's "16 rows" claim is already off-by-one *before* this plan touches anything. Adding row 8d brings the table to **18** rows. This plan's documentation task fixes SKILL.md to state "18 rows" (the correct post-fix count), which also retires the pre-existing baseline drift rather than compounding it (i.e. it does not naively bump whatever SKILL.md currently says by one).

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the marker-agnostic recovery design for fix a)
- Review rounds: 1

Two one-predicate fixes in a pure decision function with an existing table-driven
test harness (`tests/unit/test_sdlc_router.py`). The coding is small; the care is
in the reproduction tests and row-ordering (disjointness) reasoning.

## Prerequisites

No prerequisites — this work modifies a pure in-process decision function and its unit tests, with no external dependencies.

## Solution

### Key Elements

- **Fix (a) — marker-agnostic re-review recovery:** Close the "PATCH completed +
  PR open + no review verdict recorded, REVIEW left in `completed`/`failed`"
  dead-end. **Corrected scope (post-critique):** row 7
  (`_rule_pr_exists_no_review`, line 905) already dispatches `/do-pr-review`
  for `REVIEW in (None, "pending", "ready")` with no verdict, and row 8c
  (`_rule_review_in_progress_no_verdict`, line 967) already owns
  `REVIEW == in_progress` with no verdict. The truly uncovered state — the
  actual gap this issue reports — is narrower than the original problem
  statement implied: `REVIEW ∈ {completed, failed}` with an empty verdict.
  That combination is unreachable through rows 7/8/8b/8c/9/10 today because
  9/10 require a recorded verdict, 8/8b require `PATCH == completed` +
  specific `last`, and 7/8c require `REVIEW` in the None/pending/ready/
  in_progress set. Preferred implementation: **add a new recovery row (8d)**
  whose predicate is disjoint from 7, 8b, and 8c, rather than widening 8b or
  8c (see Concern-1 resolution below for why 8d over widening 8c). The 8d
  predicate fires when: `pr_number` set, `PATCH == completed`, no recorded
  REVIEW verdict, `REVIEW in (STATUS_COMPLETED, STATUS_FAILED)`,
  `last_dispatched_skill == /do-pr-review`, and neither row 7 nor 8b nor 8c
  owns the state (explicit step-asides for all three). Dispatches
  `/do-pr-review`.
- **Fix (b) — row 3 open-PR step-aside:** Add `if meta.get("pr_number"): return
  False` to `_rule_critique_needs_revision`, mirroring the existing
  `_critique_verdict_is_stale` step-aside. With a PR open, row 3 steps aside and
  the PR-stage rows (7/8/8b/8c/8d/9/10) own the state — no re-plan on shipped
  code.

### Flow

**`sdlc-tool next-skill` (state: PATCH done, PR open, re-review crashed)** → router evaluates rows → **row 8d matches** → `Dispatch(/do-pr-review, row_id="8d")` → re-review runs → pipeline continues (no human intervention).

**`sdlc-tool next-skill` (state: PR open, no review yet, non-stale NEEDS REVISION critique)** → row 3 predicate sees `pr_number` → steps aside → **row 7 owns the PR-stage state** → `Dispatch(/do-pr-review, row_id="7")` (never `/do-plan`).

### Technical Approach

- **Determine the crashed-marker state before coding (spike-1).** The issue's
  Next Steps ask what stage-marker state a crashed re-review actually leaves.
  Rather than depend on the answer, design 8d to be **marker-agnostic within
  its own disjoint band** (`completed`/`failed`, mirroring how row 2c was made
  marker-agnostic). The spike (corrected below) confirms exactly which band
  rows 7/8c leave uncovered so 8d is provably reached, not shadowed.
- **Disjointness is the correctness contract — now against three rows, not
  two.** 8d must not overlap row 7 (`REVIEW in (None, "pending", "ready")`,
  no verdict), row 8b (`last == /do-patch`), or row 8c
  (`REVIEW == in_progress`, no verdict). Encode all three step-asides
  explicitly: 8d returns False if `_rule_pr_exists_no_review(...)` is True (row
  7 owns it), `_rule_patch_applied_after_review(...)` is True (8b owns it), or
  `REVIEW == in_progress` (8c owns it). The 8d docstring must assert
  disjointness from **row 7 as well as 8b/8c** (mirroring 8c's own docstring
  style, which already documents its step-aside from 8b). Placement:
  immediately after 8c in `DISPATCH_TABLE`.
- **Concern-1 resolution: new row 8d vs. relaxing row 8c's gate.** Evaluated
  widening row 8c's `REVIEW == STATUS_IN_PROGRESS` gate to
  `REVIEW in (STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_FAILED)` as a
  one-line alternative that would fold the crashed-completed/failed band into
  8c directly, avoiding a new row entirely. **Decision: keep 8d as a discrete
  new row, do not widen 8c.** Reasoning:
  1. **Contract preservation.** 8c's docstring and existing tests pin it to
     "REVIEW is in_progress but never recorded a verdict" — a live-review
     scenario. Widening its gate to also match `completed`/`failed` REVIEW
     changes what "row 8c" *means* (a rule keyed on `row_id == "8c"` in tests
     or telemetry would now cover two semantically distinct situations: review
     still running vs. review process crashed after leaving a terminal
     marker). That conflation makes future debugging and row-id-based
     telemetry harder to read.
  2. **Blast radius.** Widening 8c touches an already-shipped, already-tested
     rule (risk of perturbing its 2 existing regression tests and any
     external code keying off row_id "8c"). Adding 8d is purely additive — no
     existing row's contract changes, matching the plan's stated
     "Reversibility: trivial" and the Rabbit Holes guidance against touching
     the existing REVIEW-recovery cluster.
  3. **Precedent.** Prior Art's row 2c was added as a *new* disjoint row
     rather than folded into row 2b for the same reason (marker-agnostic
     recovery as its own row, not a widened existing predicate). 8d follows
     the same template on the REVIEW side.
  Both approaches would achieve equivalent dispatch coverage; the new-row
  approach was chosen for auditability and minimal blast radius, not because
  the widening approach is incorrect.
- **Fix (b) is the minimal guard** — one line at the top of
  `_rule_critique_needs_revision`, matching the shape of the row-8b staleness
  step-aside so the two read as siblings.
- **8d's `last == /do-pr-review` check is not the same antipattern this issue
  diagnoses, and that distinction is now explicit.** The root-cause pattern
  named in "Why Previous Fixes Failed" is recovery predicates hinging on
  `last_dispatched_skill` matching one narrow expected value as a *routing*
  discriminator — e.g. row 8b requiring `last == /do-patch` to decide "should
  we go to review next," which breaks the instant a crash leaves `last` at an
  unexpected value. Row 8d's use of `last == /do-pr-review` is a different
  kind of check: it is not choosing *where to route*, it is confirming *what
  crashed*. 8d's five-part predicate (`pr_number` set, `PATCH == completed`,
  no recorded REVIEW verdict, `REVIEW ∈ {completed, failed}`, `last ==
  /do-pr-review`) already narrows to a single reachable state by the other
  four conditions; `last == /do-pr-review` is the confirming signal that the
  dangling REVIEW marker was left by a review dispatch specifically (as
  opposed to, say, a stale marker from some earlier, already-resolved review
  cycle) — it is evidence corroborating the diagnosis, not a brittle gate an
  unrelated crash could dodge. Contrast with row 2c's generalization: 2c was
  made marker-agnostic because its bug *was* a `last`-as-router coupling
  (matching only one exact prior-skill value to decide the next hop). 8d has
  no such coupling to remove — it dispatches `/do-pr-review` regardless of
  what `last` was, using `last` only to help identify the terminal-but-empty
  REVIEW marker as review-crash residue rather than pre-review residue.
  Decision: keep the check as specified; it is intentional and safe, not an
  instance of the antipattern.
- **G4 loop-bound (Concern-2).** Mirror row 8c's docstring convention: 8d's
  docstring states "Loop-bound by G4 (guard_g4_oscillation): same_stage_dispatch_count
  caps re-dispatches and escalates to a human if the re-review keeps
  crashing." G4 is a universal guard (`agent/sdlc_router.py:379`) keyed on
  `meta["same_stage_dispatch_count"]` and `last_dispatched_skill`, evaluated
  in `GUARDS` before `DISPATCH_TABLE` — it bounds 8d automatically with no
  8d-specific guard code required, but the *test* proving that bound holds
  for 8d's specific dispatched skill (`/do-pr-review`) does not exist yet and
  must be added (see Test Impact / Task 4).
- **Reuse existing helpers:** `_latest_review_verdict`, `meta.get("pr_number")`,
  `stage_states.get("REVIEW")`, `SKILL_DO_PATCH`, `SKILL_DO_PR_REVIEW`,
  `_rule_pr_exists_no_review`. No new helpers required.
- **Update SKILL.md** row count (currently mislabeled "16 rows"; actual
  baseline per `grep -c 'DispatchRule(' agent/sdlc_router.py` is 17; correct
  post-fix value is "18 rows") and add the 8d row description so the
  router↔SKILL parity holds.

## Spike Results

### spike-1: What stage-marker state does a crashed re-review leave, and does row 8c already cover it?
- **Assumption (original, corrected by critique):** "A crashed re-review after a patch leaves REVIEW in a state that row 8c does NOT cover (REVIEW ≠ in_progress), so the router genuinely dead-ends."
- **Method:** code-read (dispatch/marker-write path) + reproduction unit test against `decide_next_dispatch()`
- **Finding (corrected):** The original framing was too broad and is **false as stated** — `REVIEW ≠ in_progress` is NOT uniformly uncovered. Row 7 (`_rule_pr_exists_no_review`, line 905) already dispatches `/do-pr-review` for `REVIEW in (None, "pending", "ready")` with no recorded verdict, and it is evaluated *before* rows 8/8b/8c/8d in `DISPATCH_TABLE`. So `REVIEW == None` (the most likely literal crash-leaves-nothing state) is already recovered by row 7, not by a dead end. Reading rows 7 and 8c together, the actual uncovered band is narrower: **`REVIEW ∈ {completed, failed}` with an empty verdict** — i.e., the re-review subagent progressed far enough to write a terminal REVIEW marker (or the marker was left from a prior real review) but crashed before persisting a verdict, and a fresh re-dispatch (`last == /do-pr-review`) is needed. That band is provably uncovered by rows 7 (requires None/pending/ready), 8 (requires a recorded verdict), 8b (requires `last == /do-patch`), and 8c (requires `REVIEW == in_progress`). This is sufficient to justify row 8d, scoped to `REVIEW ∈ {completed, failed}` — not to "any REVIEW ≠ in_progress" as originally assumed.
- **Confidence:** high (predicate reading is deterministic; row 7's condition is read verbatim from `agent/sdlc_router.py:905-916`)
- **Impact on plan:** Design 8d to require `REVIEW in (STATUS_COMPLETED, STATUS_FAILED)` explicitly (not "any non-in_progress value") and step aside for row 7 in addition to 8b/8c. The reproduction test must pin `REVIEW` to `STATUS_COMPLETED` (or `STATUS_FAILED`) — a `None`/`pending`/`ready` state would NOT be red, since row 7 already recovers it. A companion assertion confirms `_rule_pr_exists_no_review` returns `False` for the reproduction state, proving the reproduction doesn't accidentally land in row 7's territory.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_critique_verdict_is_stale` already wraps its body in `except Exception: return False` (fail-safe to "not stale"). Fix (b) adds a guard *before* that call, so no new exception surface. No `except Exception: pass` blocks are introduced by either fix.
- [ ] The router is a pure function; new predicates raise nothing (dict `.get` access only). State "No exception handlers added in scope."

### Empty/Invalid Input Handling
- [ ] Test 8d and row-3 guard with `meta` missing `pr_number` (→ no false recovery), missing `last_dispatched_skill` (→ `""`, predicate False), and empty `stage_states`.
- [ ] Confirm empty `_latest_review_verdict(...)` (the crashed-no-verdict case) is what *enables* 8d, and a *present* verdict makes 8d step aside (rows 8/8b own it).

### Error State Rendering
- [ ] Router output is machine-consumed by `sdlc-tool`, not user-facing. Assert the `Dispatch.row_id` is `"8d"` (fix a) and that row 3 does NOT emit `/do-plan` when a PR is open (fix b) — the reproduction tests assert the exact `skill`/`row_id`, so a regression surfaces as a failing assertion, not a silent misroute.

## Test Impact

- [ ] `tests/unit/test_sdlc_router.py` — UPDATE (additive): add a `TestReReviewCrashRecovery` class (fix a: reproduction pinned to `REVIEW ∈ {completed, failed}`, the `_rule_pr_exists_no_review == False` companion assertion, row 7/8b/8c regression checks, and the G4 loop-bound test) and a `TestRow3OpenPrStepAside` class (fix b) using the existing `_base_meta`/`_base_states`/`_dispatch_history` helpers. No existing test cases change behavior.
- [ ] Router↔SKILL parity check (if a parity test exists over `DISPATCH_TABLE` row count / docstrings) — UPDATE: bump expected row count to 18 (17 baseline + 8d) rather than incrementing whatever the test currently hardcodes. Grep for any test asserting `len(DISPATCH_TABLE)` or a hardcoded row-count string and update in lockstep with SKILL.md; prefer deriving the expected count from `len(DISPATCH_TABLE)` at test time over a second hardcoded literal.

No other test files touch the router. Justification: `decide_next_dispatch` is imported only by `sdlc-tool` and `tests/unit/test_sdlc_router.py`; the change is additive (one new row + one guard line) and does not alter any existing row's output for states those tests already cover.

## Rabbit Holes

- **Refactoring the whole REVIEW-recovery cluster (8/8b/8c/8d) into one predicate.** Tempting for elegance; risks perturbing the disjointness contract that the existing tests pin. Add 8d as a discrete row and stop.
- **Trying to reconstruct the exact #1924 crash from session telemetry.** The issue notes no transcript was retained. The reproduction test defines the state; do not spelunk telemetry.
- **Widening G3 to fire on any proposed plan-family dispatch regardless of `last`.** That would change G3's contract for every caller. Fix (b) is a local row-3 step-aside, which is narrower and safer than touching the guard.
- **Adding a general "PR open → never plan-stage" invariant across all plan-stage rows (2/2b/2c/4b).** Out of scope; only row 3 is implicated by this issue. Broadening invites regressions in the plan-revision flow (#1871 territory).

## Risks

### Risk 1: Row 8d overlaps row 7, 8b, or 8c, changing an existing happy-path dispatch
**Impact:** A state currently routed correctly by 7/8b/8c gets stolen by 8d, breaking the PR-review or patch→re-review flow.
**Mitigation:** 8d's predicate explicitly steps aside when `_rule_pr_exists_no_review` is True (row 7), `_rule_patch_applied_after_review` is True (8b), or `REVIEW == in_progress` (8c). Add a test asserting rows 7, 8b, and 8c states still route to their own rows after 8d is added (regression guard); add a companion assertion in the gap-(a) reproduction test that `_rule_pr_exists_no_review` is `False` for the 8d-target state.

### Risk 4: G4 does not actually bound row 8d re-dispatches (untested)
**Impact:** If a crashed re-review keeps crashing, row 8d could in principle re-dispatch `/do-pr-review` indefinitely if the universal G4 oscillation guard were assumed but never verified against 8d's specific dispatched skill.
**Mitigation:** G4 (`guard_g4_oscillation`) is universal and requires no 8d-specific guard code, but a regression test drives `meta["same_stage_dispatch_count"]` past `MAX_SAME_STAGE_DISPATCHES` with `last_dispatched_skill == SKILL_DO_PR_REVIEW` and asserts `Blocked(guard_id="G4")` — proving the bound holds for this specific recovery path rather than assuming it from G4's universality.

### Risk 2: Fix (b) strands a genuinely-needs-replan state
**Impact:** If a PR is open but the plan legitimately needs revision, stepping row 3 aside could leave no route.
**Mitigation:** With a PR open, PR-stage rows (7/8/8b/8c/8d/9/10) already own the state; the correct action on shipped code is review/patch/merge, not re-plan. Add a test asserting the open-PR + NEEDS REVISION state routes to a PR-stage skill (not `Blocked`, not `/do-plan`).

### Risk 3: SKILL.md parity drift
**Impact:** SKILL.md's row-count claim is *already* wrong at baseline (says "16 rows" when the table has 17) — adding row 8d without fixing both the pre-existing drift and the new increment leaves the documented contract wrong in two ways, and any parity test would either miss the baseline error or lock in a stale count.
**Mitigation:** Update SKILL.md to state "18 rows" (17 baseline, verified via `grep -c 'DispatchRule(' agent/sdlc_router.py`, plus 8d) rather than incrementing whatever string is currently there; add the 8d description in the same PR. Prefer a Verification check that derives the count dynamically (`grep -c 'DispatchRule('`) over a hardcoded literal so this can't silently drift again.

## Race Conditions

No race conditions identified. `decide_next_dispatch` is a synchronous pure function over its three dict arguments; it holds no shared mutable state and performs no I/O. Concurrency in the surrounding pipeline (dispatch recording, marker writes) is unchanged by this plan.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1760] PLAN↔CRITIQUE non-convergence (notes-only revision re-stales a clean verdict) — different rules, tracked separately.
- [SEPARATE-SLUG #1871] G5 fast-path dispatches `/do-build` while `plan_revising=true` — different guard, tracked separately.

## Update System

No update system changes required — this feature is purely internal to `agent/sdlc_router.py` and its unit tests. No new dependencies, config files, or migration steps. `sdlc-tool` consumes `decide_next_dispatch` unchanged (same signature and return shape).

## Agent Integration

No agent integration required — this is an internal change to the SDLC router decision function. The agent already reaches the router through `sdlc-tool next-skill` (declared in `pyproject.toml`), whose interface is unchanged. No MCP surface, `.mcp.json`, or bridge import changes.

## Documentation

### Feature Documentation
- [ ] Update `.claude/skills/sdlc/SKILL.md` — correct the router row count to "18 rows" (SKILL.md currently says "16 rows," which was already off-by-one against the actual baseline of 17 before this plan; 8d brings it to 18 — fix the underlying drift, not just increment the existing wrong string) and add the row 8d description (crashed re-review recovery). Note the row-3 open-PR step-aside alongside the existing staleness step-aside.
- [ ] Update `docs/features/` router/SDLC-pipeline doc if one enumerates the dispatch rows (grep `docs/features` for "row 8b"/"8c"/"dispatch rule"); add 8d and the row-3 guard. If none enumerates rows, state so in the PR.

### Inline Documentation
- [ ] Docstring on the new row-8d predicate explaining disjointness from 8b/8c (mirror the 8c docstring style) and citing #1932.
- [ ] One-line comment on the row-3 `pr_number` step-aside citing #1932, mirroring the #1639 staleness-step-aside comment.

## Success Criteria

- [ ] Reproduction test for gap (a) added and RED before the fix: {PATCH completed, PR open, `last == /do-pr-review`, no REVIEW verdict, `REVIEW == STATUS_COMPLETED` (also cover `STATUS_FAILED`)} → currently `Blocked("no matching dispatch rule")`.
- [ ] Companion assertion (same test): `_rule_pr_exists_no_review(stage_states, meta, context)` returns `False` for the reproduction state — proves the repro is genuinely outside row 7's coverage, not an accidental overlap.
- [ ] After fix (a): that state → `Dispatch(skill="/do-pr-review", row_id="8d")`.
- [ ] Row 8d's docstring asserts disjointness from row 7 (`REVIEW in (None, "pending", "ready")`), row 8b (`last == /do-patch`), and row 8c (`REVIEW == in_progress`) — mirroring 8c's own docstring step-aside style.
- [ ] Reproduction test for gap (b) added and RED before the fix: {PR open, non-stale NEEDS REVISION critique, `last` not plan-family, no review yet} → currently `Dispatch("/do-plan", row_id="3")`.
- [ ] After fix (b): that state → `Dispatch(skill="/do-pr-review", row_id="7")` (pinned), plus the general invariant `skill != "/do-plan"` asserted separately.
- [ ] Regression: existing 7, 8b, 8c, and stale-critique (2b) states still route to their own rows after 8d is added.
- [ ] G4 loop-bound regression test: repeatedly dispatch `/do-pr-review` for the gap-(a) reproduction state, incrementing `same_stage_dispatch_count` past `MAX_SAME_STAGE_DISPATCHES`, and assert the router escalates to `Blocked` with `guard_id="G4"` instead of looping forever on row 8d.
- [ ] `.claude/skills/sdlc/SKILL.md` row count corrected to "18 rows" (not just incremented from whatever it currently says) and description updated; any `len(DISPATCH_TABLE)` assertion updated in lockstep; prefer a dynamic `grep -c 'DispatchRule('`-derived check over a new hardcoded literal.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (router)**
  - Name: router-builder
  - Role: Add row 8d recovery predicate + row-3 open-PR step-aside; write reproduction + regression tests (red-green); update SKILL.md parity.
  - Agent Type: builder
  - Domain: async/decision-function correctness (pure function, disjoint-predicate reasoning)
  - Resume: true

- **Validator (router)**
  - Name: router-validator
  - Role: Verify both reproduction tests were RED pre-fix and GREEN post-fix; verify 8b/8c/2b regressions still pass; verify SKILL.md parity.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Reproduce both gaps (red tests)
- **Task ID**: build-repro
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_router.py (add TestReReviewCrashRecovery, TestRow3OpenPrStepAside)
- **Informed By**: spike-1 (design 8d marker-agnostic)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a test for gap (a): build `stage_states`/`meta` via existing helpers for {PATCH completed, PR open, `last_dispatched_skill=/do-pr-review`, no REVIEW verdict, `REVIEW == STATUS_COMPLETED`} (and a second parametrized case with `REVIEW == STATUS_FAILED`); assert current result is `Blocked` with reason `"no matching dispatch rule"`. Add a companion assertion in the same test that `_rule_pr_exists_no_review(stage_states, meta, context)` is `False` for this state, proving it does not overlap row 7's `(None, "pending", "ready")` coverage.
- Add a test for gap (b): {PR open, no review yet (`REVIEW` in `(None, "pending", "ready")`, no review verdict), non-stale NEEDS REVISION critique, `last` not plan-family, no `proposed_skill`}; assert current result is `Dispatch(skill="/do-plan", row_id="3")`.
- Run both; confirm they capture the buggy behavior (these will be inverted after the fix).

### 2. Fix (a): add row 8d recovery
- **Task ID**: build-fix-a
- **Depends On**: build-repro
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_rule_review_crashed_after_dispatch` (or similarly named) predicate: `pr_number` set, `PATCH == completed`, no recorded REVIEW verdict, `REVIEW in (STATUS_COMPLETED, STATUS_FAILED)`, `last == /do-pr-review`, and step aside if row 7 (`_rule_pr_exists_no_review`) matches, 8b matches, or `REVIEW == in_progress` (8c's territory).
- Docstring cites disjointness from row 7, 8b, AND 8c explicitly (not just 8b/8c), and states the G4 loop-bound (mirroring 8c's docstring convention).
- Append a `DispatchRule(row_id="8d", ..., skill=SKILL_DO_PR_REVIEW)` immediately after 8c.
- Flip the gap-(a) test to assert `Dispatch(skill="/do-pr-review", row_id="8d")` for both the `STATUS_COMPLETED` and `STATUS_FAILED` parametrized cases; keep the companion `_rule_pr_exists_no_review == False` assertion passing post-fix.

### 3. Fix (b): row 3 open-PR step-aside
- **Task ID**: build-fix-b
- **Depends On**: build-repro
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `if meta.get("pr_number"): return False` at the top of `_rule_critique_needs_revision`, with a comment citing #1932.
- Flip the gap-(b) test to assert the pinned outcome for the constructed state
  (PR open, no review yet): `Dispatch(skill=SKILL_DO_PR_REVIEW, row_id="7")`.
  Add a second, separate assertion for the general invariant
  (`result.skill != SKILL_DO_PLAN`) so the two failure modes (wrong row vs.
  regression to `/do-plan`) are distinguishable in CI output.

### 4. Regression + parity
- **Task ID**: build-regression
- **Depends On**: build-fix-a, build-fix-b
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add/confirm tests that row 7 (`REVIEW in (None, "pending", "ready")`), 8b (`last=/do-patch`), 8c (`REVIEW=in_progress`), and 2b (stale critique) states still route to their own rows unchanged after 8d is added.
- Add a G4 loop-bound regression test for row 8d (Concern-2): starting from the gap-(a) reproduction state, set `meta["last_dispatched_skill"] = SKILL_DO_PR_REVIEW` and `meta["same_stage_dispatch_count"] = MAX_SAME_STAGE_DISPATCHES` (reusing the existing G4 test helpers/constants already in `tests/unit/test_sdlc_router.py` for other guarded rows); assert `decide_next_dispatch(...)` returns `Blocked` with `guard_id="G4"` rather than `Dispatch(row_id="8d")` — proving repeated crash-and-redispatch cycles escalate to a human instead of looping.
- Update `.claude/skills/sdlc/SKILL.md` row count to "18 rows" (correcting the pre-existing "16 rows" baseline drift, not just incrementing it) and add the 8d description + row-3 step-aside note. Update any `len(DISPATCH_TABLE)` assertion, preferring a dynamically-derived check over a hardcoded literal.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-fix-a, build-fix-b, build-regression
- **Assigned To**: router-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm both reproduction tests were RED pre-fix (via git history / the red-state note) and are GREEN post-fix.
- Confirm the companion `_rule_pr_exists_no_review == False` assertion and the G4 loop-bound regression test both pass.
- Run `pytest tests/unit/test_sdlc_router.py -q`; confirm all pass including regressions.
- Confirm SKILL.md parity (row count matches `DISPATCH_TABLE`).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router tests pass | `pytest tests/unit/test_sdlc_router.py -q` | exit code 0 |
| Row 8d exists | `grep -c '"8d"' agent/sdlc_router.py` | output > 0 |
| Row 3 has PR step-aside | `grep -c 'pr_number' agent/sdlc_router.py` | output > 0 |
| Gap-a recovery covered | `grep -rc 'row_id == "8d"\|row_id=="8d"\|"8d"' tests/unit/test_sdlc_router.py` | output > 0 |
| SKILL parity holds | `test "$(grep -c 'DispatchRule(' agent/sdlc_router.py)" = "$(grep -oP '\d+(?= rows)' .claude/skills/sdlc/SKILL.md \| head -1)" && echo MATCH` | prints `MATCH` (derives both sides dynamically so the check can't drift out of sync again; expect `18` on each side post-fix) |
| Lint clean | `python -m ruff check agent/sdlc_router.py tests/unit/test_sdlc_router.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sdlc_router.py tests/unit/test_sdlc_router.py` | exit code 0 |
| Row 3 never plans with open PR | `grep -c 'if meta.get("pr_number"): return False' agent/sdlc_router.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| N/A | (unrecoverable) | The `/do-plan-critique` run recorded verdict `NEEDS REVISION` (`sdlc-tool verdict get --stage CRITIQUE --issue-number 1932`) but the critic session crashed before persisting its findings text anywhere durable — no issue comment, no `Critique Results` row, no session telemetry with reasoning. Only the verdict string and `artifact_hash` survive. `logs/worker/sdlc-local-1932.log` shows the session was stopped mid-run. This is the same failure family issue #1932 itself describes (row 8b dead-end after a crashed subagent) — confirmed independently while trying to recover the critique's own output. | This revision | Resolved the plan's own two Open Questions directly (see below) as the best-available substitute for the lost critique feedback, since the specific findings text could not be recovered. |
| BLOCKER | do-plan-critique (pass 2) | Gap-(a) reproduction state overlapped row 7; the "currently Blocked" claim was false for the natural REVIEW=None realization, since row 7 (`_rule_pr_exists_no_review`, line 905) already dispatches `/do-pr-review` for `REVIEW in (None, "pending", "ready")` and precedes rows 8/8b/8c/8d. The truly-uncovered set is only `REVIEW ∈ {completed, failed}` with verdict empty. | This revision (pass 2) | Pinned gap-(a) reproduction to `STATUS_COMPLETED`/`STATUS_FAILED` (verdict empty); corrected spike-1 to acknowledge row 7's existing coverage; added a companion assertion that `_rule_pr_exists_no_review` returns `False` for the reproduction state; row 8d's docstring/task now asserts disjointness from row 7 in addition to 8b/8c. |
| CONCERN | do-plan-critique (pass 2) | Minimal-fix alternative not evaluated: whether relaxing row 8c's `REVIEW == in_progress` gate (a one-line edit) yields the same disjoint coverage as adding a new row 8d. | This revision (pass 2) | Added an explicit Concern-1 resolution in Technical Approach: evaluated widening 8c vs. adding 8d, decided on 8d (discrete new row) for contract preservation, minimal blast radius, and consistency with the row-2c precedent; documented the reasoning inline rather than treating it as an unexamined default. |
| CONCERN | do-plan-critique (pass 2) | Row 8d's loop-bound (G4) was never stated or tested — risk that a repeatedly-crashing re-review could dispatch `/do-pr-review` in an unbounded loop. | This revision (pass 2) | Added a G4 docstring note on row 8d (mirroring 8c's convention), a new Risk 4 entry, a Step-by-Step Tasks addition (task 4) driving `same_stage_dispatch_count` past `MAX_SAME_STAGE_DISPATCHES` and asserting `Blocked(guard_id="G4")`, and a matching Success Criteria / Verification-adjacent checklist item. |

---

## Revision Notes (this pass)

The prior critique's specific findings were unrecoverable (see Critique Results
row above). In their place, this revision resolves the plan's two Open
Questions — the same ambiguities a critic would most likely have flagged —
so the plan can proceed to build without an outstanding decision:

1. **Fix (a) shape — new row 8d vs. widen 8b: DECIDED — row 8d.** Keeping this
   as a discrete, disjoint recovery row (not widening 8b) avoids entangling the
   patch→re-review happy path with crash recovery, matches the row 2c precedent
   (marker-agnostic recovery added as its own row, not folded into an existing
   predicate), and keeps 8b's contract ("last dispatch was `/do-patch`")
   unchanged for the tests that already pin it. No plan or code change needed
   beyond what was already specified — this decision is now final, not open.
2. **Fix (b) target row assertion: DECIDED — pin the exact row, plus the
   general invariant.** The gap-(b) reproduction test constructs one concrete
   state (PR open, no review yet, non-stale NEEDS REVISION critique, `last`
   not plan-family) — that state is deterministic under
   `_rule_pr_exists_no_review` (row 7: `REVIEW in (None, "pending", "ready")`
   and no review verdict recorded). The test asserts the exact outcome
   `Dispatch(skill=SKILL_DO_PR_REVIEW, row_id="7")` for that constructed state
   *and* the weaker invariant (`result.skill != SKILL_DO_PLAN`) as a named,
   separate assertion so a future refactor that changes which PR-stage row
   wins still fails loudly on the specific-row assertion rather than silently
   passing on the weak one alone. Both the Step by Step Tasks (task 3) and
   Success Criteria below are updated to reflect the pinned row.

## Revision Notes (pass 2)

This pass addresses a real (non-lost) critique verdict with three findings —
1 BLOCKER, 2 CONCERNs — recorded via `sdlc-tool verdict record --stage
CRITIQUE`. See the corresponding rows added to Critique Results above.

1. **BLOCKER — gap-(a) reproduction overlapped row 7.** The pass-1 plan's
   reproduction language ("REVIEW marker ≠ in_progress") was too broad: it
   would include `REVIEW == None`, which row 7 (`_rule_pr_exists_no_review`)
   already recovers ahead of rows 8/8b/8c/8d in dispatch-table order, so a
   test built that way would not actually be RED. Fixed by re-deriving
   spike-1's finding directly from row 7's code (`agent/sdlc_router.py:905-916`):
   the genuinely uncovered band is `REVIEW ∈ {completed, failed}` with an
   empty verdict. The plan's Solution, Technical Approach, Spike Results,
   Success Criteria, Step-by-Step Tasks, and Risks sections were all updated
   in lockstep to reflect the corrected band and the row-7 step-aside/companion
   assertion.
2. **CONCERN 1 — 8d vs. relaxing 8c not evaluated.** Added an explicit
   evaluation in Technical Approach: widening row 8c's gate to
   `REVIEW in (in_progress, completed, failed)` would achieve the same
   coverage with a one-line change, but was rejected in favor of the discrete
   8d row for contract preservation (8c's existing docstring/tests pin it to
   the live-review case), minimal blast radius (additive-only vs. touching a
   shipped rule), and consistency with the row-2c precedent already cited in
   Prior Art. This is now a documented, reasoned decision rather than an
   unexamined default.
3. **CONCERN 2 — G4 loop-bound unstated/untested for 8d.** Added a G4
   docstring note to row 8d's spec (mirroring 8c's convention), a new Risk 4,
   a dedicated task-4 regression test that drives `same_stage_dispatch_count`
   past `MAX_SAME_STAGE_DISPATCHES` with `last_dispatched_skill ==
   SKILL_DO_PR_REVIEW` and asserts `Blocked(guard_id="G4")`, and a matching
   Success Criteria line — closing the gap between "G4 is universal so it
   must cover this" (assumed) and "a test proves it does" (now required).

No open questions remain; the plan proceeds to `/do-plan-critique` (pass 3)
for verification that these three items are resolved to satisfaction.

## Revision Notes (pass 3)

This pass addresses the CRITIQUE pass-2 verdict (`NEEDS REVISION`, 1 BLOCKER + 1
non-blocking CONCERN):

1. **BLOCKER — SKILL.md row-count parity fix started from a wrong baseline.**
   Verified directly against current main rather than trusting the critique's
   number blindly: `grep -c 'DispatchRule(' agent/sdlc_router.py` returns **17**
   at baseline (commit `8485db99`), with row_ids
   `1,2,2b,2c,3,4a,4b,4c,5,6,7,8,8b,8c,9,10,10b`. SKILL.md's "16 rows" claim was
   already off-by-one *before* this plan's changes. Every "17 rows" reference
   in the plan (Technical Approach, Documentation task, Success Criteria,
   Verification, Risk 3, Problem section's "16 rows fall through") has been
   corrected: the pre-existing baseline gap is now treated as in-scope for this
   plan's documentation task (SKILL.md is fixed to say "18 rows" — the true
   post-8d count — not incremented from whatever it currently claims), and the
   Verification check now derives both sides of the parity comparison
   dynamically (`grep -c 'DispatchRule('` vs. a `grep -oP` extraction from
   SKILL.md) instead of asserting a hardcoded literal, so it cannot drift out
   of sync again.
2. **CONCERN — row 8d's `last == /do-pr-review` predicate looked like the
   same last-coupling antipattern the plan itself names as root cause.**
   Decision: keep the check, with reasoning now documented inline in Technical
   Approach. The antipattern is `last` used as a *routing* discriminator (row
   8b/2b-before-generalization style: "if last == X, go to Y"). Row 8d's
   `last == /do-pr-review` is not deciding *where* to route (it always
   dispatches `/do-pr-review` for its band) — it is one of five conjunctive
   conditions confirming *what crashed*, narrowing an already-narrow band
   (`REVIEW ∈ {completed, failed}`, no verdict, PR open, patch completed) to
   confirm the dangling marker was left by a review dispatch specifically. No
   generalization needed; documented as an intentional, safe narrow check
   rather than an instance of the pattern being fixed.

Both items resolved without introducing new open questions. Plan proceeds to
`/do-plan-critique` (pass 3) for verification.
