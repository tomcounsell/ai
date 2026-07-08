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

- **(b) NEEDS REVISION can route to `/do-plan` with a PR open, via two
  independent routes.**
  - **Route 1 — row 3 (`last` not plan-family):** Row 3
    (`_rule_critique_needs_revision`, line 691) has a staleness step-aside
    (#1639) but no open-PR guard. The G3 gate (`guard_g3_pr_lock`, line 330)
    only trips when `last_dispatched_skill ∈ {/do-plan, /do-plan-critique}` or a
    plan-family `proposed_skill` is supplied. When a PR is already open but the
    last dispatch was a PR-stage skill and no `proposed_skill` is passed, G3
    returns None and a non-stale NEEDS REVISION critique verdict routes the
    pipeline back to `/do-plan` — re-opening plan work on shipped code.
  - **Route 2 — guard G1 (`last == /do-plan-critique`) [found pass-5]:**
    `guard_g1_critique_loop` (line 273) routes NEEDS REVISION / MAJOR REWORK +
    `last == /do-plan-critique` → `/do-plan` with **no** `pr_number` check, and
    it runs *before* the dispatch table and *before* G3 (`evaluate_guards`
    returns the first tripped guard, and G1 is `GUARDS[0]`). So whenever the
    last dispatch was `/do-plan-critique`, G1 wins and re-opens planning on
    shipped code before row 3 is ever consulted. Patching row 3 alone leaves
    this route open — the pass-5 critique BLOCKER.

**Desired outcome:**

- (a) A crashed re-review after a patch is recovered automatically: the router
  re-dispatches `/do-pr-review` instead of dead-ending, regardless of the exact
  stage-marker value the crash left behind.
- (b) A NEEDS REVISION critique verdict **never routes to `/do-plan` when an open
  PR exists for the issue**, on *either* path that can produce that route:
  - **Guard path (`last == /do-plan-critique`):** `guard_g1_critique_loop`
    (`agent/sdlc_router.py:273`) currently routes NEEDS REVISION → `/do-plan`
    with no PR check, and it runs *before* the dispatch table (and before
    `guard_g3_pr_lock`), so it wins whenever `last == /do-plan-critique`. Fix
    (b2) makes G1 step aside when a PR exists, deferring to G3 which redirects
    to the correct PR-stage skill.
  - **Dispatch-table path (`last` not plan-family):** row 3
    (`_rule_critique_needs_revision`) currently routes NEEDS REVISION →
    `/do-plan`. Fix (b1) makes row 3 step aside when a PR exists, so row 7 owns
    the state.
  Both step-asides together deliver the invariant; neither alone does (see the
  pass-5 BLOCKER analysis in Critique Results and Revision Notes).

## Freshness Check

**Baseline commit:** `8485db994a98998e87378b8e13c76385a0d1a70d`
**Issue filed at:** 2026-07-07T05:36:58Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sdlc_router.py:956` `_rule_patch_applied_after_review` — claim holds. Returns `last == SKILL_DO_PATCH` after requiring `pr_number` and `PATCH == completed`.
- `agent/sdlc_router.py:967` `_rule_review_in_progress_no_verdict` (row 8c) — claim holds. Requires `REVIEW == STATUS_IN_PROGRESS`, steps aside when 8b matches.
- `agent/sdlc_router.py:691` `_rule_critique_needs_revision` — claim holds. Only step-aside is `_critique_verdict_is_stale`; no `pr_number` guard.
- `agent/sdlc_router.py:330` `guard_g3_pr_lock` — claim holds. Early-returns None unless `last`/`proposed` is in the plan family `{SKILL_DO_PLAN, SKILL_DO_PLAN_CRITIQUE}`.
- `agent/sdlc_router.py:273` `guard_g1_critique_loop` (pass-5 BLOCKER) — verified: routes NEEDS REVISION / MAJOR REWORK + `last == /do-plan-critique` → `/do-plan` with **no** `pr_number` check. It is `GUARDS[0]` (line 626), evaluated by `evaluate_guards` (line 636) which returns the first tripped guard, so G1 fires ahead of G3 (`GUARDS[2]`) and the dispatch table on that path.
- `agent/sdlc_router.py:1003` `_rule_review_approved_docs_not_done` (row 9, pass-5 CONCERN 1) — verified verdict-blind: predicate is only `pr_number` set + `REVIEW == STATUS_COMPLETED` + `DOCS != completed`, no verdict inspection despite the "Review APPROVED" docstring. `REVIEW_APPROVED = "APPROVED"` constant exists at line 115 (already used as a gate at line 616).
- `agent/sdlc_router.py:1401` `compute_same_stage_count` D5 branch (pass-5 CONCERN 2) — verified: returns `(0, skill)` when the live snapshot diverges from the last recorded dispatch snapshot, so `same_stage_dispatch_count` self-clears; a G4 accumulation test must hold the snapshot **identical** across crash re-dispatches to prove the bound.
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
2. **Guards (G1–G7)** run first (`GUARDS` list, line 625); `evaluate_guards` returns the *first* tripped guard. G1 (`guard_g1_critique_loop`, index 0) routes NEEDS REVISION + `last == /do-plan-critique` → `/do-plan` with no PR check — it fires ahead of G3. G3 (`guard_g3_pr_lock`, index 2) redirects plan-stage dispatches to the PR-stage skill when a PR exists — but only if `last`/`proposed` is plan-family and G1 didn't already win.
3. **Dispatch table** (`DISPATCH_TABLE`, ~line 1078) evaluated top-to-bottom; first matching `state_predicate` wins. Rows 2b/2c/3 own CRITIQUE recovery; rows 8/8b/8c own REVIEW recovery; row 9 owns the review-approved→docs handoff.
4. **Output:** first matching `DispatchRule` → `Dispatch(skill, reason, row_id)`, else `Blocked("no matching dispatch rule")` (line 1279).

The bugs span step 2 and step 3: fix (a) adds a REVIEW recovery predicate (row 8d) in step 3; fix (b) closes the NEEDS-REVISION→/do-plan invariant on *both* the guard path (G1, step 2, fix b2) and the dispatch-table path (row 3, step 3, fix b1); fix (c) corrects row 9's verdict-blind misroute in step 3. No state is written by the router — it is a pure decision function over `stage_states`/`meta`/`context`, which is why the reproductions are straightforward table-/guard-driven unit tests.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none. The fixes are internal predicate/guard changes plus (for fix a) one new `DispatchRule` row appended to `DISPATCH_TABLE`. Fix (b1) edits row 3, fix (b2) edits `guard_g1_critique_loop`, fix (c) edits row 9 (`_rule_review_approved_docs_not_done`) — all in place. `decide_next_dispatch` signature and return types are unchanged.
- **Coupling:** unchanged. The new row, the row-3/row-9 gates, and the G1 gate use existing helpers (`_latest_review_verdict`, `normalize_verdict`, `REVIEW_APPROVED`, `_critique_verdict_is_stale`, `meta.get("pr_number")`).
- **Data ownership:** unchanged.
- **Reversibility:** trivial — revert the predicate edits and the one new row.
- **Parity contract:** the router's row set is mirrored in `.claude/skills/sdlc/SKILL.md`, which currently claims "16 rows." Verified against `git grep -c 'DispatchRule(' agent/sdlc_router.py` on baseline commit `8485db99`: the table actually has **17** rows today (`1,2,2b,2c,3,4a,4b,4c,5,6,7,8,8b,8c,9,10,10b`) — SKILL.md's "16 rows" claim is already off-by-one *before* this plan touches anything. Adding row 8d brings the table to **18** rows. This plan's documentation task fixes SKILL.md to state "18 rows" (the correct post-fix count), which also retires the pre-existing baseline drift rather than compounding it (i.e. it does not naively bump whatever SKILL.md currently says by one).

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the marker-agnostic recovery design for fix a)
- Review rounds: 1

Four small changes in a pure decision function with an existing table-driven test
harness (`tests/unit/test_sdlc_router.py`): one new recovery row (8d) plus three
one-line step-aside/verdict gates (row 3, guard G1, row 9). Each is a few lines;
the care is in the reproduction tests and the disjointness/verdict-exclusivity
reasoning. The pass-5 revision grew the fix from two changes to four after the
critique surfaced that the NEEDS-REVISION→/do-plan invariant needed the G1 route
closed too (BLOCKER) and the row-9 misroute fixed at its source (CONCERN 1) —
still Small in code, but the correctness surface is wider than the pass-4 framing.

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
  **The two terminal-marker values have DIFFERENT current behavior** (pass-4
  correction — the residual coverage gap the earlier passes missed): the claim
  that "9/10 require a recorded verdict" is **false for row 9**. Row 9
  (`_rule_review_approved_docs_not_done`, line 1003) checks only
  `REVIEW == STATUS_COMPLETED` + DOCS pending and never inspects the verdict.
  So `REVIEW == failed` + empty verdict currently dead-ends at
  `Blocked("no matching dispatch rule")`, but `REVIEW == completed` + empty
  verdict + DOCS pending is currently caught by **row 9** and misrouted to
  `/do-docs`, silently advancing past review on an unreviewed PR. Both are
  wrong. **Fix (c) closes the `completed`-marker misroute at row 9's source**
  (row 9 gains an `APPROVED`-verdict gate, so it no longer catches the
  empty-verdict state for any `last`); **8d adds automatic recovery** for the
  confident crash-residue subset (`last == /do-pr-review`). Preferred
  implementation for 8d: **add a new recovery row** whose predicate is disjoint
  from rows 7, 8b, and 8c (see Concern-1 resolution below for why a new row over
  widening 8c). The 8d predicate fires when: `pr_number` set, `PATCH ==
  completed`, no recorded REVIEW verdict, `REVIEW in (STATUS_COMPLETED,
  STATUS_FAILED)`, `last_dispatched_skill == /do-pr-review`, and neither row 7
  nor 8b nor 8c owns the state (explicit step-asides for all three). Dispatches
  `/do-pr-review`. **8d and row 9 are now disjoint by verdict** (after fix (c),
  row 9 requires an `APPROVED` verdict; 8d requires *no* recorded verdict), so
  8d cannot steal row 9's legitimate case and row 9 cannot misroute 8d's case.
  8d is still placed immediately after 8c (clustering with the REVIEW-recovery
  rows), but its correctness **no longer depends on preceding row 9** — the two
  predicates are mutually exclusive on the verdict dimension. For the
  `REVIEW == completed` + empty-verdict state with `last != /do-pr-review`
  (8d steps aside, row 9 now also steps aside), the router correctly falls
  through to `Blocked` instead of the old silent `/do-docs` misroute.
- **Fix (b) — open-PR step-aside on BOTH NEEDS-REVISION→/do-plan routes
  (pass-5 BLOCKER).** The invariant "NEEDS REVISION never routes to `/do-plan`
  when a PR is open" requires closing *two* independent routes, because the
  route taken depends on `last_dispatched_skill`:
  - **Fix (b1) — row 3 step-aside (`last` not plan-family):** Add
    `if meta.get("pr_number"): return False` to `_rule_critique_needs_revision`,
    mirroring the existing `_critique_verdict_is_stale` step-aside. Row 3 is
    only *reached* when no guard fires — i.e. `last` is not `/do-plan-critique`
    (else G1 fires) and G3 does not trip (G3 needs `last`/`proposed` plan-family).
    With a PR open, row 3 steps aside and the PR-stage rows (7/8/8b/8c/8d/9/10)
    own the state.
  - **Fix (b2) — G1 step-aside (`last == /do-plan-critique`):** Add
    `if meta.get("pr_number"): return None` at the top of
    `guard_g1_critique_loop`. G1 is `GUARDS[0]`, evaluated before the dispatch
    table and before `guard_g3_pr_lock` (`GUARDS[2]`); `evaluate_guards` returns
    the first tripped guard. So on the `last == /do-plan-critique` path, G1
    routes NEEDS REVISION → `/do-plan` **before row 3 is ever reached** — row
    3's guard alone cannot deliver the invariant. Making G1 return `None` when a
    PR exists lets G3 (which already exists to redirect plan-stage dispatches to
    the correct PR-stage skill when a PR is open) own the redirect. This is the
    minimal, targeted fix; it does not broaden G1's contract for the no-PR case
    (the normal plan↔critique convergence loop is untouched).
- **Fix (c) — row 9 verdict-gate at the source (pass-5 CONCERN 1).** Add a
  verdict-presence gate to `_rule_review_approved_docs_not_done` so it fires
  only when the review verdict actually normalizes to `APPROVED`:
  `if REVIEW_APPROVED not in normalize_verdict(_latest_review_verdict(stage_states, meta)): return False`
  (mirroring the existing `REVIEW_APPROVED not in ...` gate at
  `agent/sdlc_router.py:616`). Today row 9 checks only
  `REVIEW == STATUS_COMPLETED` + DOCS pending and **never inspects the
  verdict**, so a `REVIEW == completed` + empty-verdict state is silently
  misrouted to `/do-docs` for **any** `last` value — 8d's `last == /do-pr-review`
  recovery only intercepts the subset where `last` happens to be `/do-pr-review`,
  leaving the misroute open for every other `last`. Fixing row 9 at its source
  closes the misroute for **all** `last` values: after fix (c), the empty-verdict
  completed-review state no longer matches row 9; 8d auto-recovers it when
  `last == /do-pr-review` (the confident crash-residue case), and every other
  `last` value falls through to `Blocked` — a safe, human-escalating dead-end
  rather than a silent advance past review on an unreviewed PR. Row 9's
  legitimate case (`REVIEW == completed` **with** an APPROVED verdict) is
  unchanged.

### Flow

**`sdlc-tool next-skill` (state: PATCH done, PR open, re-review crashed, `last == /do-pr-review`)** → guards pass → dispatch table → **row 8d matches** → `Dispatch(/do-pr-review, row_id="8d")` → re-review runs → pipeline continues (no human intervention).

**`sdlc-tool next-skill` (state: PR open, no review yet, non-stale NEEDS REVISION critique, `last` NOT plan-family)** → G1/G3 pass (last not plan-family) → dispatch table → row 3 sees `pr_number` → steps aside (fix b1) → **row 7 owns the PR-stage state** → `Dispatch(/do-pr-review, row_id="7")` (never `/do-plan`).

**`sdlc-tool next-skill` (state: PR open, NEEDS REVISION critique, `last == /do-plan-critique`)** → **G1 sees `pr_number` → returns None (fix b2)** → G2 passes → **G3 trips** (last is plan-family + PR open) → `Dispatch(<PR-stage skill>, row_id="G3")` (never `/do-plan`). Without fix (b2), G1 would have returned `Dispatch(/do-plan, row_id="G1")` before row 3 was ever consulted.

**`sdlc-tool next-skill` (state: PR open, REVIEW completed, empty verdict, DOCS pending, `last != /do-pr-review`)** → row 9 sees no `APPROVED` verdict → steps aside (fix c) → 8d steps aside (`last != /do-pr-review`) → falls through → `Blocked` (safe human escalation, not the old silent `/do-docs` misroute).

### Technical Approach

- **Determine the crashed-marker state before coding (spike-1).** The issue's
  Next Steps ask what stage-marker state a crashed re-review actually leaves.
  Rather than depend on the answer, design 8d to be **marker-agnostic within
  its own disjoint band** (`completed`/`failed`, mirroring how row 2c was made
  marker-agnostic). The spike (corrected below) confirms exactly which band
  rows 7/8c leave uncovered so 8d is provably reached, not shadowed.
- **Disjointness is the correctness contract — three explicit step-aside rows,
  and (after fix (c)) disjoint-by-verdict from row 9.** 8d must not overlap row
  7 (`REVIEW in (None, "pending", "ready")`, no verdict), row 8b
  (`last == /do-patch`), or row 8c (`REVIEW == in_progress`, no verdict).
  Encode all three step-asides explicitly: 8d returns False if
  `_rule_pr_exists_no_review(...)` is True (row 7 owns it),
  `_rule_patch_applied_after_review(...)` is True (8b owns it), or
  `REVIEW == in_progress` (8c owns it). **Row 9 relationship (changed by
  fix (c)):** in the pass-4 design, row 9 was verdict-blind and 8d had to
  *win by table position* to intercept the `REVIEW == completed` + empty-verdict
  misroute — a fragile ordering dependency that also left the misroute open for
  `last != /do-pr-review` (pass-5 CONCERN 1). Fix (c) gates row 9 on an
  `APPROVED` verdict, so 8d (no verdict) and row 9 (`APPROVED` verdict) are now
  **mutually exclusive on the verdict dimension** — they cannot both match the
  same state, and 8d's correctness no longer depends on preceding row 9. 8d is
  still placed immediately after 8c for clustering, but the ordering is
  belt-and-suspenders, not load-bearing. The 8d docstring must assert
  disjointness from **rows 7, 8b, and 8c** (via explicit step-asides) and note
  that it is disjoint from **row 9 by verdict** (8d requires no recorded verdict;
  row 9 now requires `APPROVED`). Placement: immediately after 8c in
  `DISPATCH_TABLE`.
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
- **Fix (b) is two minimal guards, one per route (pass-5 BLOCKER).** Fix (b1)
  is one line at the top of `_rule_critique_needs_revision`
  (`if meta.get("pr_number"): return False`), matching the shape of the row-3
  staleness step-aside so the two read as siblings. Fix (b2) is one line at the
  top of `guard_g1_critique_loop` (`if meta.get("pr_number"): return None`),
  placed *before* G1 reads the verdict/last so an open PR short-circuits the
  guard entirely. **Why both are required:** the guards run before the dispatch
  table and `evaluate_guards` returns the first tripped guard, so when
  `last == /do-plan-critique`, G1 (`GUARDS[0]`) fires and returns `/do-plan`
  before row 3 is ever evaluated — patching row 3 alone leaves the guard-path
  route open, which is exactly the false-invariant BLOCKER the pass-5 review
  caught. After fix (b2), G1 defers to G3 (`GUARDS[2]`, already the canonical
  "PR open locks plan-stage → redirect to PR-stage skill" guard) on the
  open-PR path.
- **Fix (c) is the source fix for the row-9 verdict-blind misroute (pass-5
  CONCERN 1).** One gate at the top of `_rule_review_approved_docs_not_done`
  requiring `REVIEW_APPROVED in normalize_verdict(_latest_review_verdict(...))`,
  mirroring the existing gate at `agent/sdlc_router.py:616`. This is preferred
  over relying on 8d's narrow `last == /do-pr-review` interception because it
  closes the misroute for **all** `last` values at the point where the defect
  lives (row 9 claiming "APPROVED" without checking the verdict), rather than
  papering over one subset downstream. Interaction with 8d: for the
  empty-verdict completed state, row 9 now steps aside; 8d recovers the
  `last == /do-pr-review` subset and every other subset falls through to
  `Blocked` (safe). This does **not** widen scope to a general "PR open → never
  plan-stage" invariant — it is a targeted correction of one rule's own stated
  contract. (Row 10, `_rule_ready_to_merge`, is also verdict-blind but is
  guarded by *all* stages being completed including DOCS — a different, merge-gate
  state not implicated by this issue; it is explicitly out of scope, see
  No-Gos.)
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
- **G4 loop-bound + D5 self-clearing — the test must exercise history, not just
  set a counter (pass-5 CONCERN 2).** Mirror row 8c's docstring convention:
  8d's docstring states "Loop-bound by G4 (guard_g4_oscillation):
  same_stage_dispatch_count caps re-dispatches and escalates to a human if the
  re-review keeps crashing." G4 is a universal guard
  (`agent/sdlc_router.py:379`) keyed on `meta["same_stage_dispatch_count"]` and
  `last_dispatched_skill`, evaluated in `GUARDS` before `DISPATCH_TABLE`. **The
  subtlety pass-5 flagged:** `same_stage_dispatch_count` is not a free-standing
  meta value a real caller sets to `MAX`; it is *derived* by
  `compute_same_stage_count` (`agent/sdlc_router.py:1348`) from the
  `_sdlc_dispatches` history, and its **D5 branch resets the streak to 0 when
  the live stage snapshot diverges from the last recorded dispatch snapshot**
  (`agent/sdlc_router.py:1401`). A naive test that merely sets
  `meta["same_stage_dispatch_count"] = MAX_SAME_STAGE_DISPATCHES` proves nothing
  about 8d, because it bypasses the derivation and never demonstrates that a
  *real* 8d crash-loop actually accumulates. The correct regression must build a
  `_sdlc_dispatches` history of `MAX_SAME_STAGE_DISPATCHES` consecutive entries
  that all share `skill == /do-pr-review` **and an identical `stage_snapshot`
  equal to the 8d crash state**, then drive the count through
  `compute_same_stage_count` (or the same code path `decide_next_dispatch` uses)
  with a matching `current_snapshot`, asserting (i) the derived count reaches the
  cap (D5 does **not** reset it, because the crashed re-review leaves the same
  terminal marker each cycle → snapshot stable) and (ii) `decide_next_dispatch`
  returns `Blocked(guard_id="G4")`. As a companion, assert the *contrast*: if the
  snapshot moves between dispatches (a genuine stage/verdict correction), D5
  resets and G4 does **not** fire — proving the bound is crash-loop-specific, not
  a blanket cap. This is the 8d-specific, D5-aware regression the concern asks
  for; see Test Impact / Task 4.
- **Reuse existing helpers:** `_latest_review_verdict`, `meta.get("pr_number")`,
  `stage_states.get("REVIEW")`, `SKILL_DO_PATCH`, `SKILL_DO_PR_REVIEW`,
  `_rule_pr_exists_no_review`. No new helpers required.
- **Update SKILL.md** row count (currently mislabeled "16 rows"; actual
  baseline per `grep -c 'DispatchRule(' agent/sdlc_router.py` is 17; correct
  post-fix value is "18 rows" — only 8d is a *new* row; fixes (b2) and (c)
  modify a guard and an existing row in place, so they do not change the count)
  and add the 8d row description, the row-9 `APPROVED`-verdict gate, the row-3
  open-PR step-aside, and the G1 open-PR step-aside so the router↔SKILL parity
  holds.

## Spike Results

### spike-1: What stage-marker state does a crashed re-review leave, and does row 8c already cover it?
- **Assumption (original, corrected by critique):** "A crashed re-review after a patch leaves REVIEW in a state that row 8c does NOT cover (REVIEW ≠ in_progress), so the router genuinely dead-ends."
- **Method:** code-read (dispatch/marker-write path) + reproduction unit test against `decide_next_dispatch()`
- **Finding (corrected twice — see pass-4 note):** The original framing was too broad and is **false as stated** — `REVIEW ≠ in_progress` is NOT uniformly uncovered. Row 7 (`_rule_pr_exists_no_review`, line 905) already dispatches `/do-pr-review` for `REVIEW in (None, "pending", "ready")` with no recorded verdict, and it is evaluated *before* rows 8/8b/8c/8d in `DISPATCH_TABLE`. So `REVIEW == None` (the most likely literal crash-leaves-nothing state) is already recovered by row 7, not by a dead end. Reading rows 7 and 8c together, the residual band is **`REVIEW ∈ {completed, failed}` with an empty verdict** — the re-review subagent progressed far enough to write a terminal REVIEW marker (or the marker was left from a prior real review) but crashed before persisting a verdict, and a fresh re-dispatch (`last == /do-pr-review`) is needed. **Critical second correction (pass 4): the two terminal-marker values do NOT share the same current behavior.** They must be split:
  - **`REVIEW == STATUS_FAILED` + empty verdict → currently `Blocked("no matching dispatch rule")`** (a genuine dead-end). Verified: row 7 requires None/pending/ready (no), row 8 (`_rule_review_has_findings`) short-circuits `if not review_verdict: return False` *before* its `REVIEW == STATUS_FAILED` branch is reached (no), row 8b requires `last == /do-patch` (no), row 8c requires `REVIEW == in_progress` (no), row 9 (`_rule_review_approved_docs_not_done`) requires `REVIEW == STATUS_COMPLETED` (no — REVIEW is `failed`), row 10 requires REVIEW completed (no), row 10b requires empty `stage_states` (no). Falls through → Blocked.
  - **`REVIEW == STATUS_COMPLETED` + empty verdict + DOCS pending → currently `Dispatch("/do-docs", row_id="9")` (a silent MISROUTE, NOT Blocked).** This is the error the pass-4 self-review caught: **row 9 (`_rule_review_approved_docs_not_done`, `agent/sdlc_router.py:1003`) does NOT check the review verdict** — its predicate is only `pr_number` set, `REVIEW == STATUS_COMPLETED`, and `DOCS != completed`. Its docstring *says* "Review APPROVED, zero findings" but the code never verifies the verdict. So a crashed re-review that left `REVIEW == completed` with an empty verdict is caught by row 9 and routed to `/do-docs`, advancing the pipeline past review on an unreviewed PR — arguably worse than a dead-end because it is silent. The earlier passes' claim that "9/10 require a recorded verdict" is **false for row 9** and was the residual coverage-analysis gap.
- **Confidence:** high (row 9's predicate is read verbatim from `agent/sdlc_router.py:1003-1010`; row 8's short-circuit ordering from `agent/sdlc_router.py:919-965`)
- **Impact on plan (updated pass-5):** Design 8d to require
  `REVIEW in (STATUS_COMPLETED, STATUS_FAILED)` and step aside for rows 7, 8b,
  **and** 8c. **Pass-5 change (CONCERN 1):** rather than rely on 8d intercepting
  the row-9 misroute by table position (which only covered `last == /do-pr-review`
  and left the misroute open for every other `last`), fix row 9 **at its source**
  — gate `_rule_review_approved_docs_not_done` on an `APPROVED` verdict (fix c).
  After fix (c), 8d and row 9 are disjoint by verdict, so 8d's placement relative
  to row 9 is no longer load-bearing; 8d still sits immediately after 8c for
  clustering. The **pre-fix** reproduction test still asserts **different**
  current behavior per parametrized case (this documents the bug being fixed):
  `STATUS_FAILED` → `Blocked`; `STATUS_COMPLETED` → `Dispatch("/do-docs",
  row_id="9")` misroute. Companion assertions (pre-fix): `_rule_pr_exists_no_review`
  returns `False` for both cases (proves no row-7 overlap), and
  `_rule_review_approved_docs_not_done` returns `True` for the `STATUS_COMPLETED`
  case (proves the row-9 misroute is real) and `False` for the `STATUS_FAILED`
  case. **Post-fix**, add a case proving fix (c) closes the misroute for
  `last != /do-pr-review`: `REVIEW == completed` + empty verdict + DOCS pending +
  `last` some non-review skill → `Blocked` (row 9 and 8d both step aside), where
  pre-fix it was `Dispatch("/do-docs", row_id="9")`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_critique_verdict_is_stale` already wraps its body in `except Exception: return False` (fail-safe to "not stale"). Fix (b1) adds a guard *before* that call; fix (b2) adds a guard at the very top of `guard_g1_critique_loop`; fix (c) adds a gate at the top of row 9 using `normalize_verdict`/`_latest_review_verdict` — all dict `.get`-based, no new exception surface. No `except Exception: pass` blocks are introduced by any fix.
- [ ] The router is a pure function; new predicates/guards raise nothing (dict `.get` access + `normalize_verdict` on strings only). State "No exception handlers added in scope."

### Empty/Invalid Input Handling
- [ ] Test 8d, the row-3/G1 guards, and the row-9 gate with `meta` missing `pr_number` (→ no false recovery, guards return None/False → normal routing), missing `last_dispatched_skill` (→ `""`, predicate False), and empty `stage_states`.
- [ ] Confirm empty `_latest_review_verdict(...)` (the crashed-no-verdict case) is what *enables* 8d and *disables* row 9 (fix c), and a *present* APPROVED verdict makes 8d step aside and row 9 fire.
- [ ] Confirm the G1 guard with `pr_number` set returns `None` (steps aside) regardless of verdict/last, and with `pr_number` absent behaves exactly as before.

### Error State Rendering
- [ ] Router output is machine-consumed by `sdlc-tool`, not user-facing. Assert the `Dispatch.row_id` is `"8d"` (fix a), that neither row 3 (b1) nor G1 (b2) emits `/do-plan` when a PR is open, and that row 9 does NOT emit `/do-docs` on an empty-verdict completed review (fix c) — the reproduction tests assert exact `skill`/`row_id`, so a regression surfaces as a failing assertion, not a silent misroute.

## Test Impact

- [ ] `tests/unit/test_sdlc_router.py` — UPDATE (mostly additive): add
  - a `TestReReviewCrashRecovery` class (fix a): two parametrized pre-fix reproduction cases with DIFFERENT current behavior — `REVIEW == failed` → `Blocked`, `REVIEW == completed` → `Dispatch("/do-docs", row_id="9")` misroute; the `_rule_pr_exists_no_review == False` companion (both cases); the `_rule_review_approved_docs_not_done` companion (`True` for COMPLETED pre-fix, `False` for FAILED); post-fix flip to `Dispatch("/do-pr-review", row_id="8d")` for both; row 7/8b/8c regression checks;
  - a `TestRow3OpenPrStepAside` class (fix b1): open-PR + NEEDS REVISION + `last` not plan-family → pre-fix `Dispatch("/do-plan", row_id="3")`, post-fix `Dispatch("/do-pr-review", row_id="7")` + the separate `skill != /do-plan` invariant;
  - a `TestG1OpenPrStepAside` class (fix b2): open-PR + NEEDS REVISION + `last == /do-plan-critique` → pre-fix `Dispatch("/do-plan", row_id="G1")`, post-fix `Dispatch(row_id="G3")` (PR-stage skill) + `skill != /do-plan` invariant; plus a no-PR regression proving G1 still routes NEEDS REVISION + `last==/do-plan-critique` → `/do-plan` when no PR exists (G1's normal contract unchanged);
  - a `TestRow9VerdictGate` class (fix c): `REVIEW == completed` + empty verdict + DOCS pending + `last` non-review skill → pre-fix `Dispatch("/do-docs", row_id="9")`, post-fix `Blocked` (row 9 and 8d both step aside); plus the row-9 legitimate case (`REVIEW == completed` + `APPROVED` verdict + DOCS pending) → `Dispatch("/do-docs", row_id="9")` unchanged before and after;
  - a `TestRow8dLoopBound` class (fix a, CONCERN 2): build a real `_sdlc_dispatches` history of `MAX_SAME_STAGE_DISPATCHES` identical (skill=`/do-pr-review`, snapshot=8d-crash-state) entries, assert the derived count reaches the cap (D5 does not reset — snapshot stable) and `decide_next_dispatch` → `Blocked(guard_id="G4")`; plus the contrast case (snapshot moves between dispatches → D5 resets → not `Blocked(G4)`).
  Use the existing `_base_meta`/`_base_states`/`_dispatch_history` helpers.
- [ ] **Existing row-9 tests (fix c may change behavior — audit, do not assume additive).** Grep `tests/unit/test_sdlc_router.py` for tests constructing `REVIEW == STATUS_COMPLETED` and routing to `/do-docs`/`row_id="9"`. Any that build that state **without** an `APPROVED` review verdict encode the pre-fix verdict-blind behavior; UPDATE them to include an `APPROVED` verdict (reflecting real pipeline state) so they still exercise row 9, or re-point them at the new `Blocked` expectation if the no-verdict state was the intent. This is the one place fix (c) is NOT purely additive.
- [ ] Router↔SKILL parity check (if a parity test exists over `DISPATCH_TABLE` row count / docstrings) — UPDATE: bump expected row count to 18 (17 baseline + 8d; fixes b2/c add no rows) rather than incrementing whatever the test currently hardcodes. Grep for any test asserting `len(DISPATCH_TABLE)` or a hardcoded row-count string and update in lockstep with SKILL.md; prefer deriving the expected count from `len(DISPATCH_TABLE)` at test time over a second hardcoded literal.

No other test files touch the router. Justification: `decide_next_dispatch` is imported only by `sdlc-tool` and `tests/unit/test_sdlc_router.py`; the change is one new row + one guard line + two in-place predicate gates. It is additive for all rows except row 9, whose existing tests must be audited per the item above.

## Rabbit Holes

- **Refactoring the whole REVIEW-recovery cluster (8/8b/8c/8d) into one predicate.** Tempting for elegance; risks perturbing the disjointness contract that the existing tests pin. Add 8d as a discrete row and stop.
- **Trying to reconstruct the exact #1924 crash from session telemetry.** The issue notes no transcript was retained. The reproduction test defines the state; do not spelunk telemetry.
- **Widening G3 to fire on any proposed plan-family dispatch regardless of `last`.** That would change G3's contract for every caller. Fix (b) is a local row-3 step-aside, which is narrower and safer than touching the guard.
- **Adding a general "PR open → never plan-stage" invariant across all plan-stage rows (2/2b/2c/4b).** Out of scope. Fix (b) touches exactly the two routes that produce NEEDS-REVISION→`/do-plan` with a PR open: row 3 (b1) and `guard_g1_critique_loop` (b2). Rows 2/2b/2c/4b route to `/do-plan-critique` or other targets, not `/do-plan` on a NEEDS-REVISION verdict, and are not implicated by this issue. Broadening to all of them invites regressions in the plan-revision flow (#1871 territory).
- **Verdict-gating row 10 (`_rule_ready_to_merge`) as well as row 9.** Out of scope. Row 10 is also verdict-blind, but it is guarded by *every* stage (including DOCS) being completed — a merge-gate state, not the review→docs handoff this issue's crashed-re-review scenario produces. Fix (c) corrects only the row implicated by gap (a); a broader review-verdict audit across the table is separate work.

## Risks

### Risk 1: Row 8d overlaps row 7, 8b, 8c, or collides with row 9
**Impact:** A state currently routed correctly by 7/8b/8c gets stolen by 8d (breaks PR-review or patch→re-review flow), OR 8d and row 9 both match the same state.
**Mitigation:** 8d's predicate explicitly steps aside when `_rule_pr_exists_no_review` is True (row 7), `_rule_patch_applied_after_review` is True (8b), or `REVIEW == in_progress` (8c). The row-9 collision is now structurally impossible after fix (c): row 9 requires an `APPROVED` verdict and 8d requires *no* recorded verdict, so the two predicates are mutually exclusive on the verdict dimension regardless of table order. Tests: assert rows 7/8b/8c states still route to their own rows; assert row 9's verdict-present APPROVED case still routes to `/do-docs`; add the companion assertions (`_rule_pr_exists_no_review == False` both cases; `_rule_review_approved_docs_not_done == True` for the COMPLETED empty-verdict repro pre-fix, proving the misroute was real; and `== False` for the same state post-fix, proving fix (c) closed it).

### Risk 4: G4 does not actually bound row 8d re-dispatches, and a naive test masks it via D5 (pass-5 CONCERN 2)
**Impact:** If a crashed re-review keeps crashing, row 8d could in principle re-dispatch `/do-pr-review` indefinitely. Worse, a superficial regression that merely *sets* `meta["same_stage_dispatch_count"] = MAX` would pass without proving anything, because in a real 8d loop the count is *derived* from `_sdlc_dispatches` by `compute_same_stage_count`, whose D5 branch resets the streak to 0 the moment the live snapshot diverges — so the test could go green while the actual accumulation path is broken.
**Mitigation:** The regression builds a real `_sdlc_dispatches` history of `MAX_SAME_STAGE_DISPATCHES` entries sharing `skill == /do-pr-review` **and an identical `stage_snapshot`** equal to the 8d crash state, drives it through `compute_same_stage_count`/`decide_next_dispatch` with a matching `current_snapshot`, and asserts (i) the derived count reaches the cap — D5 does **not** reset it because a repeatedly-crashing re-review leaves the same terminal marker each time (snapshot stable) — and (ii) `decide_next_dispatch` returns `Blocked(guard_id="G4")`. A contrast assertion (snapshot *moves* between dispatches → D5 resets → G4 does not fire) proves the bound is crash-loop-specific rather than a blanket cap. This proves the bound holds for 8d's specific recovery path *and* that D5's self-clearing is accounted for, rather than assuming either from G4's universality.

### Risk 2: Fix (b) strands a genuinely-needs-replan state
**Impact:** If a PR is open but the plan legitimately needs revision, stepping row 3 aside (b1) *and* G1 aside (b2) could leave no route.
**Mitigation:** With a PR open, on the guard path G3 (which fixes b2 defers to) redirects `last == /do-plan-critique` to the right PR-stage skill; on the dispatch-table path the PR-stage rows (7/8/8b/8c/8d/9/10) own the state. Either way the correct action on shipped code is review/patch/merge, not re-plan. Add tests for both paths: (i) open-PR + NEEDS REVISION + `last == /do-plan-critique` → `Dispatch(row_id="G3")` (not `/do-plan`, not `Blocked`); (ii) open-PR + NEEDS REVISION + `last` not plan-family → `Dispatch(row_id="7")` (not `/do-plan`).

### Risk 5: Fix (c) strands a legitimate no-verdict completed review
**Impact:** If the normal pipeline can legitimately reach `REVIEW == completed` with DOCS pending but *without* a recorded/parseable `APPROVED` verdict, gating row 9 on `APPROVED` would step it aside and (if `last != /do-pr-review`, so 8d also steps aside) route it to `Blocked` instead of advancing to docs.
**Mitigation:** Row 9's own docstring asserts the intended state *is* "Review APPROVED," so a completed review without an APPROVED verdict is already anomalous — `Blocked` (human escalation) is the correct outcome for it, strictly better than silently advancing past review. The common healthy path (review approved → verdict `APPROVED` recorded → REVIEW marked completed) still matches row 9 unchanged. Test: assert row 9's APPROVED-verdict case still routes to `/do-docs` after fix (c); and audit existing row-9 tests for any that construct `REVIEW == completed` *without* an APPROVED verdict — those encode the pre-fix verdict-blind behavior and must be updated to include an APPROVED verdict (they reflect real pipeline state) or re-pointed at the new `Blocked` expectation.

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
- [ ] Update `.claude/skills/sdlc/SKILL.md` — correct the router row count to "18 rows" (SKILL.md currently says "16 rows," which was already off-by-one against the actual baseline of 17 before this plan; only 8d is a new row, so 8d brings it to 18 — fix the underlying drift, not just increment the existing wrong string). Add the row 8d description (crashed re-review recovery); document row 9's new `APPROVED`-verdict gate (fix c); note the row-3 open-PR step-aside alongside its existing staleness step-aside; and note the G1 (`guard_g1_critique_loop`) open-PR step-aside in the guards description (fix b2 — G1 now defers to G3 when a PR exists).
- [ ] Update `docs/features/` router/SDLC-pipeline doc if one enumerates the dispatch rows or guards (grep `docs/features` for "row 8b"/"8c"/"dispatch rule"/"guard_g1"/"G1"); add 8d, the row-9 gate, the row-3 guard, and the G1 step-aside. If none enumerates rows/guards, state so in the PR.

### Inline Documentation
- [ ] Docstring on the new row-8d predicate explaining disjointness from rows 7/8b/8c (mirror the 8c docstring style), noting disjoint-by-verdict from row 9 (after fix c), and citing #1932.
- [ ] One-line comment on the row-3 `pr_number` step-aside citing #1932, mirroring the #1639 staleness-step-aside comment.
- [ ] One-line comment on the G1 `pr_number` step-aside citing #1932 (defers to G3 when a PR exists).
- [ ] One-line comment on the row-9 `APPROVED`-verdict gate citing #1932 (closes the verdict-blind `/do-docs` misroute at the source); update row 9's docstring so the "Review APPROVED" claim now matches the code.

## Success Criteria

- [ ] Reproduction test for gap (a), case FAILED, added and RED before the fix: {PATCH completed, PR open, `last == /do-pr-review`, no REVIEW verdict, `REVIEW == STATUS_FAILED`, DOCS pending} → currently `Blocked("no matching dispatch rule")`.
- [ ] Reproduction test for gap (a), case COMPLETED, added and RED before the fix: {PATCH completed, PR open, `last == /do-pr-review`, no REVIEW verdict, `REVIEW == STATUS_COMPLETED`, DOCS pending} → currently `Dispatch(skill="/do-docs", row_id="9")` (a misroute, **not** Blocked — row 9 does not check the verdict).
- [ ] Companion assertion (both cases): `_rule_pr_exists_no_review(stage_states, meta, context)` returns `False` — proves the repro is genuinely outside row 7's coverage.
- [ ] Companion assertion (COMPLETED case, pre-fix): `_rule_review_approved_docs_not_done(stage_states, meta, context)` returns `True` — proves the row-9 misroute is real; and `False` for the FAILED case.
- [ ] After fix (a): both cases → `Dispatch(skill="/do-pr-review", row_id="8d")` (8d and row 9 are disjoint by verdict after fix (c), so this holds regardless of table order).
- [ ] Regression: row 9's legitimate case (`REVIEW == completed` **with** an APPROVED verdict, DOCS pending) still routes to `Dispatch(skill="/do-docs", row_id="9")` after 8d is added and fix (c) lands (row 9 requires the APPROVED verdict; 8d steps aside because a verdict is recorded).
- [ ] Row 8d's docstring asserts disjointness from row 7 (`REVIEW in (None, "pending", "ready")`), row 8b (`last == /do-patch`), and row 8c (`REVIEW == in_progress`) via explicit step-asides, and notes it is disjoint from row 9 by verdict (8d requires no recorded verdict; row 9 requires APPROVED) — mirroring 8c's own docstring step-aside style.

**Fix (b1) — row 3 open-PR step-aside (`last` not plan-family):**
- [ ] Reproduction test for gap (b1) added and RED before the fix: {PR open, non-stale NEEDS REVISION critique, `last` not plan-family, no review yet} → currently `Dispatch("/do-plan", row_id="3")`.
- [ ] After fix (b1): that state → `Dispatch(skill="/do-pr-review", row_id="7")` (pinned), plus the general invariant `skill != "/do-plan"` asserted separately.

**Fix (b2) — G1 open-PR step-aside (`last == /do-plan-critique`) [pass-5 BLOCKER]:**
- [ ] Reproduction test for gap (b2) added and RED before the fix: {PR open, NEEDS REVISION critique, `last == /do-plan-critique`} → currently `Dispatch("/do-plan", row_id="G1")` (proves the guard-path route the row-3 fix alone does not close).
- [ ] After fix (b2): that state → `Dispatch(row_id="G3")` (a PR-stage skill), plus the invariant `skill != "/do-plan"` asserted separately.
- [ ] Regression: with **no** PR open, G1 still routes NEEDS REVISION + `last == /do-plan-critique` → `Dispatch("/do-plan", row_id="G1")` (G1's normal plan↔critique contract unchanged).

**Fix (c) — row 9 verdict-gate at source [pass-5 CONCERN 1]:**
- [ ] Reproduction test added and RED before the fix: {PR open, `REVIEW == completed`, empty verdict, DOCS pending, `last` non-review skill} → currently `Dispatch("/do-docs", row_id="9")` (the misroute open to any `last`, which 8d's narrow gate does not close).
- [ ] After fix (c): that state → `Blocked` (row 9 steps aside on missing APPROVED verdict; 8d steps aside on `last != /do-pr-review`) — safe escalation, not a silent docs advance.
- [ ] `_rule_review_approved_docs_not_done` returns `False` post-fix for the empty-verdict completed state and `True` for the APPROVED-verdict completed state.

**Cross-cutting:**
- [ ] Regression: existing 7, 8b, 8c, and stale-critique (2b) states still route to their own rows after 8d/fix(c) land.
- [ ] G4 loop-bound regression test (CONCERN 2, D5-aware): build a real `_sdlc_dispatches` history of `MAX_SAME_STAGE_DISPATCHES` entries sharing `skill == /do-pr-review` and an **identical** `stage_snapshot` equal to the 8d crash state; assert the derived `same_stage_dispatch_count` reaches the cap (D5 does not reset it — snapshot stable) and `decide_next_dispatch` → `Blocked(guard_id="G4")`. Companion contrast: a history whose snapshot **moves** between dispatches → D5 resets → not `Blocked(G4)`.
- [ ] `.claude/skills/sdlc/SKILL.md` row count corrected to "18 rows" (not just incremented from whatever it currently says); row-9 gate, row-3 step-aside, and G1 step-aside documented; any `len(DISPATCH_TABLE)` assertion updated in lockstep; prefer a dynamic `grep -c 'DispatchRule('`-derived check over a new hardcoded literal.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (router)**
  - Name: router-builder
  - Role: Add row 8d recovery predicate (fix a); row-3 + G1 open-PR step-asides (fix b1/b2); row-9 APPROVED-verdict gate (fix c); write reproduction + regression tests (red-green), including the D5-aware G4 loop-bound test; update SKILL.md parity.
  - Agent Type: builder
  - Domain: async/decision-function correctness (pure function, disjoint-predicate reasoning)
  - Resume: true

- **Validator (router)**
  - Name: router-validator
  - Role: Verify all four reproduction families (a, b1, b2, c) were RED pre-fix and GREEN post-fix; verify 7/8b/8c/2b and row-9-APPROVED regressions plus the G1 no-PR regression still pass; verify the D5-aware G4 loop-bound test; verify SKILL.md parity (18 rows).
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Reproduce all gaps (red tests)
- **Task ID**: build-repro
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_router.py (add TestReReviewCrashRecovery, TestRow3OpenPrStepAside, TestG1OpenPrStepAside, TestRow9VerdictGate)
- **Informed By**: spike-1 (design 8d marker-agnostic); pass-5 BLOCKER (G1) + CONCERN 1 (row 9)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a test for gap (a) with **two parametrized cases that assert DIFFERENT current behavior** (build `stage_states`/`meta` via existing helpers; both share {PATCH completed, PR open, `last_dispatched_skill=/do-pr-review`, no REVIEW verdict, DOCS pending}):
  - `REVIEW == STATUS_FAILED` → assert current result is `Blocked` with reason `"no matching dispatch rule"`.
  - `REVIEW == STATUS_COMPLETED` → assert current result is `Dispatch(skill="/do-docs", row_id="9")` (row 9 misroutes because it does not check the verdict — do NOT assert `Blocked` for this case; it would be a false RED).
  - Companion assertions: `_rule_pr_exists_no_review(...)` is `False` for both cases (no row-7 overlap); `_rule_review_approved_docs_not_done(...)` is `True` for the COMPLETED case (proves the row-9 misroute) and `False` for the FAILED case.
- Add a test for gap (b1): {PR open, no review yet (`REVIEW` in `(None, "pending", "ready")`, no review verdict), non-stale NEEDS REVISION critique, `last` not plan-family, no `proposed_skill`}; assert current result is `Dispatch(skill="/do-plan", row_id="3")`.
- Add a test for gap (b2) [pass-5 BLOCKER]: {PR open, NEEDS REVISION critique, `last_dispatched_skill = /do-plan-critique`}; assert current result is `Dispatch(skill="/do-plan", row_id="G1")` — this is the guard-path route that fires *before* row 3, so it must be reproduced independently.
- Add a test for gap (c) [pass-5 CONCERN 1]: {PR open, `REVIEW == STATUS_COMPLETED`, no REVIEW verdict, DOCS pending, `last_dispatched_skill` = a non-review skill (so 8d cannot recover it)}; assert current result is `Dispatch(skill="/do-docs", row_id="9")` — the verdict-blind misroute open to any `last`.
- Run all; confirm they capture the buggy behavior (these will be inverted after the fixes).

### 2. Fix (a): add row 8d recovery
- **Task ID**: build-fix-a
- **Depends On**: build-repro
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_rule_review_crashed_after_dispatch` (or similarly named) predicate: `pr_number` set, `PATCH == completed`, no recorded REVIEW verdict, `REVIEW in (STATUS_COMPLETED, STATUS_FAILED)`, `last == /do-pr-review`, and step aside if row 7 (`_rule_pr_exists_no_review`) matches, 8b matches, or `REVIEW == in_progress` (8c's territory).
- Docstring cites disjointness from rows 7, 8b, AND 8c explicitly (via step-asides), notes it is **disjoint from row 9 by verdict** (after fix (c) row 9 requires APPROVED; 8d requires no verdict — they cannot co-match), and states the G4 loop-bound (mirroring 8c's docstring convention).
- Insert a `DispatchRule(row_id="8d", ..., skill=SKILL_DO_PR_REVIEW)` immediately after 8c in `DISPATCH_TABLE` (clustered with the REVIEW-recovery rows; ordering vs. row 9 is belt-and-suspenders once fix (c) makes them verdict-disjoint).
- Flip the gap-(a) test: assert `Dispatch(skill="/do-pr-review", row_id="8d")` for both the `STATUS_COMPLETED` and `STATUS_FAILED` cases; keep the companion `_rule_pr_exists_no_review == False` assertions passing post-fix. (Depends on fix (c), task 3b, for the row-9 disjointness to hold cleanly.)

### 3. Fix (b): open-PR step-aside on BOTH NEEDS-REVISION→/do-plan routes
- **Task ID**: build-fix-b
- **Depends On**: build-repro
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- **Fix (b1) — row 3:** Add `if meta.get("pr_number"): return False` at the top of `_rule_critique_needs_revision`, with a comment citing #1932.
- **Fix (b2) — G1 [pass-5 BLOCKER]:** Add `if meta.get("pr_number"): return None` at the very top of `guard_g1_critique_loop` (before it reads the verdict/last), with a comment citing #1932 and noting it defers to G3 (the canonical open-PR plan-stage redirect) on the open-PR path.
- Flip the gap-(b1) test: PR open, no review yet → `Dispatch(skill=SKILL_DO_PR_REVIEW, row_id="7")`, plus a separate `result.skill != SKILL_DO_PLAN` invariant assertion.
- Flip the gap-(b2) test: PR open, NEEDS REVISION, `last == /do-plan-critique` → `Dispatch(row_id="G3")` (a PR-stage skill), plus a separate `result.skill != SKILL_DO_PLAN` invariant assertion.
- Add the no-PR regression for G1: NEEDS REVISION + `last == /do-plan-critique` + no PR → still `Dispatch(skill=SKILL_DO_PLAN, row_id="G1")` (G1's normal contract preserved).

### 3b. Fix (c): row 9 verdict-gate at source
- **Task ID**: build-fix-c
- **Depends On**: build-repro
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `if REVIEW_APPROVED not in normalize_verdict(_latest_review_verdict(stage_states, meta)): return False` at the top of `_rule_review_approved_docs_not_done` (mirroring the existing gate at `agent/sdlc_router.py:616`), with a comment citing #1932; update row 9's docstring so the "Review APPROVED" claim now matches the code.
- Flip the gap-(c) test: PR open, `REVIEW == completed`, empty verdict, DOCS pending, `last` non-review → `Blocked` (was `Dispatch("/do-docs", row_id="9")`).
- Add the row-9 legitimate regression: PR open, `REVIEW == completed`, **APPROVED** verdict, DOCS pending → `Dispatch("/do-docs", row_id="9")` unchanged.
- Assert `_rule_review_approved_docs_not_done` returns `False` for the empty-verdict completed state and `True` for the APPROVED-verdict completed state, post-fix.

### 4. Regression + parity
- **Task ID**: build-regression
- **Depends On**: build-fix-a, build-fix-b, build-fix-c
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add/confirm tests that row 7 (`REVIEW in (None, "pending", "ready")`), 8b (`last=/do-patch`), 8c (`REVIEW=in_progress`), **row 9's legitimate case (`REVIEW == completed` WITH an APPROVED verdict, DOCS pending → still `Dispatch("/do-docs", row_id="9")`)**, and 2b (stale critique) states still route to their own rows unchanged after 8d and fix (c) land. The row-9 regression is critical: it proves fix (c) keeps row 9 firing on the APPROVED case while 8d/`Blocked` handle the empty-verdict case.
- **Audit existing row-9 tests (fix c is not purely additive):** grep for tests building `REVIEW == STATUS_COMPLETED` routing to row 9 without an APPROVED verdict; UPDATE them to include an APPROVED verdict or re-point them at the new `Blocked` expectation (see Test Impact).
- Add a **D5-aware** G4 loop-bound regression test for row 8d (CONCERN 2): build a real `_sdlc_dispatches` history of `MAX_SAME_STAGE_DISPATCHES` entries all sharing `skill == SKILL_DO_PR_REVIEW` and an **identical** `stage_snapshot` equal to the 8d crash state (reusing existing G4 test helpers/constants), pass a matching `current_snapshot`, and assert (i) the derived count reaches the cap — proving D5 does NOT reset because the crashed re-review leaves a stable snapshot — and (ii) `decide_next_dispatch(...)` returns `Blocked(guard_id="G4")` rather than `Dispatch(row_id="8d")`. Add the contrast case: a history whose snapshot **moves** between dispatches resets the streak (D5) and does NOT block on G4. Do **not** write the naive `meta["same_stage_dispatch_count"] = MAX` shortcut — it bypasses `compute_same_stage_count` and proves nothing about the real loop.
- Update `.claude/skills/sdlc/SKILL.md` row count to "18 rows" (correcting the pre-existing "16 rows" baseline drift, not just incrementing it) and add the 8d description + row-9 APPROVED-gate note + row-3 step-aside note + G1 step-aside note. Update any `len(DISPATCH_TABLE)` assertion, preferring a dynamically-derived check over a hardcoded literal.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-fix-a, build-fix-b, build-fix-c, build-regression
- **Assigned To**: router-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all four reproduction families (gap a, b1, b2, c) were RED pre-fix (via git history / the red-state note) and are GREEN post-fix.
- Confirm the companion `_rule_pr_exists_no_review == False` assertion, the row-9 `_rule_review_approved_docs_not_done` assertions, the G1 no-PR regression, and the D5-aware G4 loop-bound regression (with its snapshot-moves contrast) all pass.
- Confirm any audited pre-existing row-9 tests still pass after being updated for fix (c).
- Run `pytest tests/unit/test_sdlc_router.py -q`; confirm all pass including regressions.
- Confirm SKILL.md parity (row count matches `DISPATCH_TABLE`, expect 18).

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
| Row 3 never plans with open PR (fix b1) | `grep -c 'if meta.get("pr_number"): return False' agent/sdlc_router.py` | output > 0 |
| G1 steps aside with open PR (fix b2) | `grep -c 'if meta.get("pr_number"): return None' agent/sdlc_router.py` | output > 0 |
| Row 9 gated on APPROVED verdict (fix c) | `grep -c 'REVIEW_APPROVED not in' agent/sdlc_router.py` | output >= 2 (existing gate at :616 + new row-9 gate) |
| G1 fix precedes verdict read | `grep -n 'pr_number' agent/sdlc_router.py \| head` | a `pr_number` line inside `guard_g1_critique_loop` (lines ~273-297) before the `normalize_verdict` call |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| N/A | (unrecoverable) | The `/do-plan-critique` run recorded verdict `NEEDS REVISION` (`sdlc-tool verdict get --stage CRITIQUE --issue-number 1932`) but the critic session crashed before persisting its findings text anywhere durable — no issue comment, no `Critique Results` row, no session telemetry with reasoning. Only the verdict string and `artifact_hash` survive. `logs/worker/sdlc-local-1932.log` shows the session was stopped mid-run. This is the same failure family issue #1932 itself describes (row 8b dead-end after a crashed subagent) — confirmed independently while trying to recover the critique's own output. | This revision | Resolved the plan's own two Open Questions directly (see below) as the best-available substitute for the lost critique feedback, since the specific findings text could not be recovered. |
| BLOCKER | do-plan-critique (pass 2) | Gap-(a) reproduction state overlapped row 7; the "currently Blocked" claim was false for the natural REVIEW=None realization, since row 7 (`_rule_pr_exists_no_review`, line 905) already dispatches `/do-pr-review` for `REVIEW in (None, "pending", "ready")` and precedes rows 8/8b/8c/8d. The truly-uncovered set is only `REVIEW ∈ {completed, failed}` with verdict empty. | This revision (pass 2) | Pinned gap-(a) reproduction to `STATUS_COMPLETED`/`STATUS_FAILED` (verdict empty); corrected spike-1 to acknowledge row 7's existing coverage; added a companion assertion that `_rule_pr_exists_no_review` returns `False` for the reproduction state; row 8d's docstring/task now asserts disjointness from row 7 in addition to 8b/8c. |
| CONCERN | do-plan-critique (pass 2) | Minimal-fix alternative not evaluated: whether relaxing row 8c's `REVIEW == in_progress` gate (a one-line edit) yields the same disjoint coverage as adding a new row 8d. | This revision (pass 2) | Added an explicit Concern-1 resolution in Technical Approach: evaluated widening 8c vs. adding 8d, decided on 8d (discrete new row) for contract preservation, minimal blast radius, and consistency with the row-2c precedent; documented the reasoning inline rather than treating it as an unexamined default. |
| CONCERN | do-plan-critique (pass 2) | Row 8d's loop-bound (G4) was never stated or tested — risk that a repeatedly-crashing re-review could dispatch `/do-pr-review` in an unbounded loop. | This revision (pass 2) | Added a G4 docstring note on row 8d (mirroring 8c's convention), a new Risk 4 entry, a Step-by-Step Tasks addition (task 4) driving `same_stage_dispatch_count` past `MAX_SAME_STAGE_DISPATCHES` and asserting `Blocked(guard_id="G4")`, and a matching Success Criteria / Verification-adjacent checklist item. |
| N/A | (unrecoverable, pass 3) | The pass-3 `/do-plan-critique` recorded `NEEDS REVISION` (verified: verdict `artifact_hash` `sha256:c9e33d86…` matches the current plan body exactly, so the verdict is genuine and against this plan, not stale) but the critic session was stopped mid-run (`logs/worker/sdlc-local-1932.log`: "I was stopped and won't resume automatically") before persisting findings text anywhere durable — no issue comment, no Critique Results row, no telemetry with reasoning. Only the verdict string + hash survive. This is the **fourth** consecutive lost-critique on this issue and is itself the failure family #1932 addresses. | This revision (pass 4) | As in pass 1, performed a rigorous critic-substitute self-review against the actual router source. Found a real BLOCKER-class coverage-analysis gap (row 9), the same class as the pass-2 row-7 finding, and fixed it (below). |
| BLOCKER | pass-4 self-review | Gap-(a) coverage analysis was still incomplete: it claimed `REVIEW ∈ {completed, failed}` + empty verdict is uniformly `Blocked`, asserting "9/10 require a recorded verdict." **False for row 9.** Row 9 (`_rule_review_approved_docs_not_done`, `agent/sdlc_router.py:1003`) checks only `REVIEW == STATUS_COMPLETED` + DOCS pending and never inspects the verdict. So `REVIEW == completed` + empty verdict + DOCS pending is NOT Blocked — it is caught by row 9 and misrouted to `/do-docs`, silently advancing past review on an unreviewed PR. A reproduction test asserting `Blocked` for the completed case would be a false RED, and 8d's disjointness/ordering contract omitted row 9 entirely. | This revision (pass 4) | Split the gap-(a) reproduction into two cases with different current behavior (`failed` → `Blocked`; `completed` → `Dispatch("/do-docs", row_id="9")` misroute). Corrected spike-1, Solution, Technical Approach, Risk 1, Test Impact, Success Criteria, and Step-by-Step Tasks (tasks 1, 2, 4). Established that 8d's placement **before row 9** is load-bearing (intercepts the completed-marker misroute), that 8d must NOT steal row 9's verdict-present case (guaranteed by 8d's "no recorded REVIEW verdict" condition), and added a row-9 companion assertion (`_rule_review_approved_docs_not_done == True` pre-fix for the completed case) plus a row-9 verdict-present regression. |
| BLOCKER | do-plan-critique (pass 4, 3-critic consensus) | Desired Outcome (b) and Success Criteria claimed a NEEDS REVISION verdict "never routes to `/do-plan` when an open PR exists," but the fix only patched row 3. `guard_g1_critique_loop` (`agent/sdlc_router.py:273-297`) runs BEFORE the dispatch table, has no `pr_number` check, and routes NEEDS REVISION + `last == /do-plan-critique` → `/do-plan` unconditionally; `evaluate_guards` returns the first tripped guard (G1 is `GUARDS[0]`), so row 3 is never reached on that path. The row-3-only success test constructs only the row-3 state, so it passes green while the stated invariant is false. | This revision (pass 5) | Extended the fix to G1 (Option B): added fix (b2) — `if meta.get("pr_number"): return None` at the top of `guard_g1_critique_loop`, deferring to G3 on the open-PR path. Split fix (b) into b1 (row 3) + b2 (G1) across Desired Outcome, Problem, Solution, Technical Approach, Data Flow, Flow, Risk 2, No-Gos, Test Impact, Success Criteria, Step-by-Step Tasks (task 1, 3), and Verification. Added a gap-(b2) reproduction test (`Dispatch("/do-plan", row_id="G1")` pre-fix → `Dispatch(row_id="G3")` post-fix) and a no-PR G1 regression preserving G1's normal contract. The invariant is now delivered by both step-asides together. |
| CONCERN | do-plan-critique (pass 4) | 8d's `last == /do-pr-review` gate leaves the row-9 verdict-blind misroute (row 9 routes to `/do-docs` on `REVIEW == completed` with no verdict check, for any `last`) open for `last` values other than `/do-pr-review` — fix row 9 at the source instead of gating narrowly on 8d. | This revision (pass 5) | Added fix (c): a source-level `APPROVED`-verdict gate on `_rule_review_approved_docs_not_done`, closing the misroute for all `last` values (empty-verdict completed → `Blocked` when 8d also steps aside). Reframed the 8d↔row-9 relationship from load-bearing table-ordering to disjoint-by-verdict throughout (Solution, Technical Approach, spike-1, Risk 1). Added a `TestRow9VerdictGate` reproduction (`last` non-review → `/do-docs` pre-fix, `Blocked` post-fix) and updated Test Impact to flag that pre-existing row-9 tests must be audited (fix c is not purely additive). Row 10's similar verdict-blindness explicitly scoped out in No-Gos. |
| CONCERN | do-plan-critique (pass 4) | The G4 loop-bound test was not 8d-specific, and D5's self-clearing behavior (`compute_same_stage_count`, `agent/sdlc_router.py:1401`, resets the streak when the snapshot diverges) defeats accumulation — a naive `same_stage_dispatch_count = MAX` test proves nothing about the real 8d loop. Needs a more targeted regression. | This revision (pass 5) | Rewrote the G4 test spec (Technical Approach G4 bullet, Risk 4, Test Impact `TestRow8dLoopBound`, Success Criteria, Task 4) to build a real `_sdlc_dispatches` history of `MAX_SAME_STAGE_DISPATCHES` entries sharing `skill == /do-pr-review` and an **identical** `stage_snapshot`, prove the derived count reaches the cap (D5 does not reset — snapshot stable across crash re-dispatches), assert `Blocked(guard_id="G4")`, and add a snapshot-moves contrast case proving D5 resets. Explicitly banned the naive count-set shortcut. |

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

## Revision Notes (pass 4)

The pass-3 `/do-plan-critique` returned `NEEDS REVISION` but its findings text
was unrecoverable — the critic session was stopped mid-run before persisting
anything durable (no issue comment, no Critique Results row, no telemetry).
Confirmed the verdict is genuine and against the current plan, not a stale
cache artifact: the stored critique `artifact_hash`
(`sha256:c9e33d868c5a…`) matches `_compute_artifact_hash('CRITIQUE', 1932)`
against the current plan body **exactly**. This is the fourth consecutive
lost-critique on this issue — the same failure family #1932 itself fixes.

As in pass 1, the substitute was a rigorous critic-substitute self-review
conducted **against the actual `agent/sdlc_router.py` source** (not against the
plan's own prose). That review found one real BLOCKER-class defect — the same
*class* the pass-2 critique caught for row 7, now recurring for row 9:

1. **BLOCKER — the gap-(a) coverage analysis omitted row 9's no-verdict-check
   semantics.** The plan claimed the `REVIEW ∈ {completed, failed}` +
   empty-verdict band is uniformly `Blocked`, on the premise that "9/10 require
   a recorded verdict." Reading `_rule_review_approved_docs_not_done`
   (`agent/sdlc_router.py:1003-1010`) directly disproves that: row 9's
   predicate is only `pr_number` set + `REVIEW == STATUS_COMPLETED` + DOCS not
   completed — **no verdict inspection**, despite a docstring that says
   "Review APPROVED." Consequences: (a) the `REVIEW == completed` +
   empty-verdict + DOCS-pending state is not a dead-end but a *silent misroute*
   to `/do-docs` (worse — it advances past review on an unreviewed PR); (b) a
   reproduction test asserting `Blocked` for that case would be a false RED and
   would pass trivially without exercising the bug; (c) 8d's disjointness
   contract never mentioned row 9, so nothing pinned that 8d must be ordered
   before row 9 to intercept the misroute, nor that 8d must avoid stealing row
   9's legitimate verdict-present case.

   **Fix:** Split the gap-(a) reproduction into two parametrized cases with
   explicitly different current behavior — `REVIEW == failed` → `Blocked`;
   `REVIEW == completed` → `Dispatch("/do-docs", row_id="9")` misroute.
   Corrected spike-1, Solution/Key Elements, Technical Approach (disjointness
   now covers rows 7/8b/8c as step-asides **plus** the row-9 ordering
   dependency), Risk 1, Test Impact, Success Criteria, and Step-by-Step Tasks
   (tasks 1, 2, 4). Added a row-9 companion assertion
   (`_rule_review_approved_docs_not_done == True` pre-fix for the completed
   case, `False` for the failed case) proving the misroute is real, and a
   row-9 verdict-present regression proving 8d does not steal row 9's normal
   docs handoff (8d's "no recorded REVIEW verdict" condition guarantees the
   step-aside).

Verified against current main (baseline `8485db99`): `grep -c 'DispatchRule('
agent/sdlc_router.py` = 17; row_ids `1,2,2b,2c,3,4a,4b,4c,5,6,7,8,8b,8c,9,10,10b`;
SKILL.md still says "16 rows" — the plan's row-count claims are unchanged and
correct. No open questions remain; plan proceeds to `/do-plan-critique`
(pass 4) for verification.

## Revision Notes (pass 5)

The pass-4 `/do-plan-critique` returned `NEEDS REVISION` (verdict
`artifact_hash` `sha256:599080…`, verified via `sdlc-tool verdict get --stage
CRITIQUE --issue-number 1932`) — this time a **real, recovered** critique with
one BLOCKER (3-critic consensus) and two CONCERNs, all verified against
`agent/sdlc_router.py` source. Each was independently re-verified against the
current source before revising (see the Freshness Check additions for the exact
lines). All three are resolved:

1. **BLOCKER (RESOLVED) — the NEEDS-REVISION→/do-plan invariant was false
   because `guard_g1_critique_loop` was never patched.** The invariant "NEEDS
   REVISION never routes to `/do-plan` when an open PR exists" has *two* source
   routes: the dispatch-table row 3 (`last` not plan-family) and guard G1
   (`last == /do-plan-critique`). G1 is `GUARDS[0]`, runs before the table and
   before G3, has no `pr_number` check, and `evaluate_guards` returns the first
   tripped guard — so on the `/do-plan-critique` path, G1 routes to `/do-plan`
   before row 3 is ever consulted, and the row-3-only success test passed green
   while the invariant was false. **Chose Option B (extend the fix to G1)** over
   Option A (narrow the claim + add a No-Go), because Option B keeps the stated
   Desired Outcome (b) *true* rather than narrowing it: added fix (b2),
   `if meta.get("pr_number"): return None` at the top of
   `guard_g1_critique_loop`, which defers to G3 (the canonical open-PR
   plan-stage redirect) on the open-PR path while leaving G1's normal no-PR
   plan↔critique contract untouched. Fix (b) is now b1 (row 3) + b2 (G1);
   both are required and neither alone delivers the invariant. Updated Desired
   Outcome, Problem, Solution/Key Elements, Technical Approach, Data Flow, Flow,
   Risk 2, No-Gos, Test Impact, Success Criteria, Step-by-Step Tasks (1, 3), and
   Verification. Added a gap-(b2) RED reproduction and a no-PR G1 regression.

2. **CONCERN 1 (RESOLVED — fixed at source, not deferred) — 8d's narrow
   `last == /do-pr-review` gate left the row-9 verdict-blind misroute open for
   other `last` values.** Row 9 (`_rule_review_approved_docs_not_done`) routes
   `REVIEW == completed` + DOCS-pending to `/do-docs` with **no verdict check**,
   for any `last`; 8d only intercepted the `/do-pr-review` subset. Added fix (c):
   a source-level `APPROVED`-verdict gate on row 9, mirroring the existing gate
   at `agent/sdlc_router.py:616`. This closes the misroute for *all* `last`
   values (empty-verdict completed → 8d recovers the `/do-pr-review` subset,
   everything else → `Blocked`, a safe escalation rather than a silent advance).
   Bonus: fix (c) makes 8d and row 9 disjoint by verdict, retiring the fragile
   "8d must precede row 9" load-bearing-ordering claim from pass 4. Scoped row 10
   (also verdict-blind, but merge-gated) explicitly out in No-Gos.

3. **CONCERN 2 (RESOLVED) — the G4 loop-bound test was not 8d-specific and D5's
   self-clearing defeats a naive counter-set test.** `same_stage_dispatch_count`
   is derived by `compute_same_stage_count` from `_sdlc_dispatches`, and its D5
   branch resets the streak to 0 when the live snapshot diverges from the last
   recorded dispatch — so setting the count to `MAX` in `meta` bypasses the
   derivation and proves nothing. Rewrote the test spec to build a real dispatch
   history of `MAX_SAME_STAGE_DISPATCHES` identical (skill=`/do-pr-review`,
   snapshot=8d-crash-state) entries, prove the derived count reaches the cap
   (snapshot stable across crash re-dispatches → D5 does not reset), assert
   `Blocked(guard_id="G4")`, and add a snapshot-moves contrast proving D5 resets.
   The naive shortcut is explicitly banned.

Scope grew from two changes to four (one new row 8d + three in-place gates: row
3, guard G1, row 9) — still Small in code, wider in correctness surface. Row
count is unchanged at 18 (only 8d is a new `DispatchRule`; b2 edits a guard and
(c) edits an existing row). No open questions remain; plan proceeds to
`/do-plan-critique` (pass 5) for verification.
