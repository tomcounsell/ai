---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1687
last_comment_id: IC_kwDOEYGa088AAAABGqXoyg
revision_applied: true
---

# SDLC Router: Refute Phantom #1680 Dead-Ends + Close REVIEW Empty-Verdict Rule Gap

## Problem

The SDLC router (`agent/sdlc_router.py::decide_next_dispatch`) walks an ordered rule table to pick the next pipeline sub-skill from recorded stage/verdict state. It has a documented history of dead-end loops where a recorded verdict outlives the artifact it judged. Two such dead-ends were fixed on the CRITIQUE path: #1639 (row 2b, stale recorded verdict) and #1668 (row 2c, empty/unrecorded verdict). #1668 warned that a supervisor can *report* a dead-end that is not real by pattern-matching a stale agent-memory note and applying a harmless manual workaround.

During a `/do-sdlc` run of #1680 (PR #1685, merged 2026-06-13), the supervisor reported navigating both a NEEDS-REVISION→`/do-plan` dead-end and a CHANGES-REQUESTED→`/do-patch` dead-end. The persisted pipeline state does not corroborate that report (`critique_cycle_count=0`, `same_stage_dispatch_count=0`, only a final `READY TO BUILD` verdict recorded). This issue confirms or refutes that report, then closes two real gaps the investigation surfaced.

**Current behavior:**
- Supervisors can report phantom router dead-ends that the persisted state does not support, because the state is not self-describing (Gap B: intermediate critique verdicts not persisted, CRITIQUE marker not flipped to `completed`).
- The REVIEW path lacks the empty-verdict re-dispatch escape (analogous to CRITIQUE row 2c). A REVIEW marker stuck at `in_progress` with no recorded verdict and no completed PATCH is bounded only by the G4 oscillation guard, never self-correcting via re-review (Gap A).

**Desired outcome:**
- The reported #1680 dead-ends are confirmed or refuted against the recorded trail (gating spike — already executed at plan time; see Spike Results).
- The REVIEW path gains an empty-verdict re-dispatch rule mirroring row 2c, with a regression test mirroring `TestCritiqueInProgressNoVerdictDeadEnd`.
- Gap B (non-flipped CRITIQUE marker / non-persisted intermediate verdicts) is investigated read-only and a disposition recorded that defers the fix to #1654 (no fix-now branch — spike-1 proved the artifact benign; see critique concern #4).

## Freshness Check

**Baseline commit:** `6b407cde4001b90654922a939d872896b20a132e`
**Issue filed at:** 2026-06-14T14:32:16Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/sdlc_router.py:785-818` (`_critique_verdict_is_stale`) — issue cited this range — drifted to `:738-771`; symbol + claim unchanged.
- `agent/sdlc_router.py:838-867` (`_rule_critique_in_progress_no_verdict`) — drifted to `:791-820`; registered as row 2c at `:981-986` (issue cited `:1028-1033`); claim unchanged.
- `agent/sdlc_router.py:757-782` (`_review_verdict_is_stale`) — drifted to `:710-735`; claim unchanged.
- `agent/sdlc_router.py:884-918` (`_rule_review_has_findings`) — drifted to `:837-871`; claim unchanged. Confirmed row 8 → `/do-patch` and row 8b (`_rule_patch_applied_after_review`, `:874-882`) → `/do-pr-review`.
- Confirmed the REVIEW path has NO empty-verdict re-dispatch rule (no analogue to row 2c) — Gap A is real and structural.

**Cited sibling issues/PRs re-checked:**
- #1639, #1659, #1668, #1670 — all merged; rows 2b/2c on main, `TestCritiqueInProgressNoVerdictDeadEnd` present.
- #1654 (non-persisted critique verdicts) — referenced as the likely Gap B root; still the linked tracking issue.
- #1736 (issue-number resolution in forked critique/review skills) — merged per issue comment; removes "verdict written to wrong issue" as a Gap B confounder. Future Gap B investigation can assume issue-number diversion is no longer a cause.
- #1710 (`631818b6`, G5 fast diagnostic) — merged per issue comment; G5 now defers to PR-stage rows once BUILD completes / a PR exists, removing one re-dispatch confounder.

**Commits on main since issue was filed (touching `agent/sdlc_router.py`):**
- `ec25e58e` (#1710) — granite startup fast diagnostic, bundled the G5 defer fix — *changed an adjacent guard, did not touch the REVIEW rule table or verdict persistence.*
- `dd926192` (#1691) — bridge-role merge / SessionType collapse — *refactor that drifted line numbers; no rule-logic change.*

**Active plans in `docs/plans/` overlapping this area:** none (`ls docs/plans/` shows no other sdlc-router plan in flight).

**Notes:** Drift is purely line-number movement from two refactors. All cited symbols, rule rows, and behavioral claims still hold. Corrected line references are folded into Technical Approach below.

## Prior Art

- **#1639 / PR #1659**: CRITIQUE NEEDS-REVISION + revision-applied looped on `/do-plan`. Fixed via `_critique_verdict_is_stale` + dispatch row 2b. Succeeded — on main and green. This is the staleness pattern Gap A mirrors on the REVIEW side.
- **#1668 / PR #1670**: CRITIQUE `in_progress` + empty/unrecorded verdict + no PR matched no rule → `Blocked`. Fixed via row 2c (`_rule_critique_in_progress_no_verdict`). Succeeded — on main and green. This is the EXACT pattern Gap A replicates for REVIEW; the new rule is a structural twin.
- **#1654**: War-room critics complete but the verdict stays `in_progress`; supervisor aggregates inline without persisting. Open. This is the likely root of Gap B; Gap B's investigation links here rather than re-solving it.
- **#1736**: Forked critique/review skills used a stale `$SDLC_ISSUE_NUMBER`, so verdict/marker writes could land on the wrong issue. Merged. Eliminates one Gap B confounder.
- **PR #1657**: Introduced the REVIEW staleness pattern (`_review_verdict_is_stale`) that #1659 mirrored for CRITIQUE. The new Gap A rule is the second half of that symmetry restored.

## Why Previous Fixes Failed

The prior fixes did NOT fail — they fixed the CRITIQUE path correctly. The gap is that they fixed only one of two symmetric paths. The root cause pattern is **rule asymmetry**: each fix to the CRITIQUE side (rows 2b, 2c) was not mirrored to the REVIEW side at the same time, leaving the REVIEW path with a staleness escape but no empty-verdict escape (no 2c analogue).

**Correct twin mapping (re-critique concern — the prior table mis-mapped row 8b as the row-2b twin):** Row 2b is `_rule_critique_verdict_stale` (CRITIQUE stale-verdict re-critique). Its REVIEW twin is NOT row 8b — it is the `_review_verdict_is_stale(...)` step-aside embedded INSIDE row 8 (`_rule_review_has_findings`, `agent/sdlc_router.py:862`), introduced by PR #1657. Row 8b (`_rule_patch_applied_after_review`) is a DIFFERENT concept — patch-applied → re-review — and has no CRITIQUE counterpart in the 2x rows. So: 2b's twin = the staleness check in row 8 (via `_review_verdict_is_stale`), and the missing twin is 2c's empty-verdict escape, which has no REVIEW analogue at all (that absence is Gap A).

| Prior Fix | What It Did | REVIEW Twin Status |
|-----------|-------------|--------------------|
| PR #1659 (row 2b, `_rule_critique_verdict_stale`) | CRITIQUE stale-verdict → re-critique | REVIEW already had its staleness twin — the `_review_verdict_is_stale` step-aside INSIDE row 8 (`_rule_review_has_findings`), from PR #1657. NOT row 8b. No gap here. |
| PR #1670 (row 2c, `_rule_critique_in_progress_no_verdict`) | CRITIQUE empty-verdict in_progress → re-critique | REVIEW path was NOT given the matching empty-verdict rule (no 2c analogue exists) — **this is Gap A**. |

**Root cause pattern:** The CRITIQUE and REVIEW paths are structural twins, but rule additions were applied to one path at a time. Row 2b's staleness twin lives inside row 8; row 8b is unrelated (patch-applied re-review). Gap A is the missing mirror of row 2c — the REVIEW empty-verdict escape that was never added.

## Data Flow

1. **Entry point**: SDLC supervisor (`/sdlc` router skill) or worker calls `decide_next_dispatch(stage_states, meta, context)`.
2. **Guards G1–G7** (`evaluate_guards`): if any trips (oscillation G4, plan-hash cache G5, etc.), it short-circuits with a decision before rules run.
3. **Rule table walk** (`DISPATCH_RULES`): rows tried in order; the first predicate returning True wins. The REVIEW-stage rows are 7 (`_rule_pr_exists_no_review`), 8 (`_rule_review_has_findings`), 8b (`_rule_patch_applied_after_review`), 9, 10, 10b.
4. **State predicates** read `stage_states["REVIEW"]`, `stage_states["_verdicts"]["REVIEW"]`, `meta["latest_review_verdict"]`, `meta["pr_number"]`, and `stage_states["PATCH"]`.
5. **Output**: a `Dispatch(skill, reason, row_id)` or `Blocked(reason)`.

Gap A inserts a new rule (proposed `8c`) into step 3 so that REVIEW `in_progress` + empty verdict + no completed PATCH returns `Dispatch(/do-pr-review)` instead of falling through to `Blocked` or oscillating on row 8.

## Spike Results

### spike-1 (GATING): Confirm or refute the reported #1680 dead-ends
- **Assumption**: "The reported CRITIQUE→`/do-plan` and REVIEW→`/do-patch` dead-ends in the #1680 run were phantom (router would have routed correctly)."
- **Method**: code-read + replay (`decide_next_dispatch` invoked against reconstructed #1680 state, executed at plan time).
- **Finding**: **REFUTED — the reported dead-ends are phantom.**
  - The mid-run state the supervisor claimed looped on `/do-plan` (CRITIQUE `in_progress`, recorded `READY TO BUILD` verdict, no PR) routes to **`/do-build` via row 4a** — NOT a `/do-plan` loop. No completion-signal-present state returned `/do-plan` or `/do-patch`.
  - The persisted counters (`critique_cycle_count=0`, `same_stage_dispatch_count=0`) are inconsistent with any actual loop — corroborating phantom.
  - The recorded final all-completed state returns `Blocked('no matching dispatch rule')`, but that is a *terminal merged pipeline* (MERGE=completed), not a live dead-end — the router is never asked to route past merge. This is a benign artifact of Gap B (CRITIQUE marker never flipped), not the reported loop.
- **Confidence**: high.
- **Impact on plan**: The CRITIQUE-path fix work is dropped (confirmed unnecessary). The refute-branch narrows to a memory-hygiene check (spike-2). Gap A and Gap B proceed as the only real work.

### spike-2: Does `project_sdlc_router_needs_revision_deadlock.md` still exist?
- **Assumption**: "A stale memory note describing the obsolete `/do-plan-critique`-directly workaround exists and must be pruned."
- **Method**: `memory_search` (project=valor) + repo-wide grep for the note name and its workaround text.
- **Finding**: The note is **NOT present** — not in the agent memory store (`memory_search` returns nothing for multiple queries) and not anywhere in the repo as a `.md` file. The refute-branch "prune the note" task is therefore largely moot.
- **Confidence**: high (negative result; a session-scoped memory could in principle reappear, hence the residual verify task).
- **Impact on plan**: The refute task becomes "verify no stale sibling note exists; if one surfaces during build, prune/correct it" rather than "delete a known file."

### spike-gap-b (Gap B): Read-Only Investigation Disposition

#### 1. Verdict-Recording Code Path (`_verdicts.CRITIQUE`)

The sole writer of `_verdicts.CRITIQUE` is `tools/sdlc_verdict.py::record_verdict()`. It is
called by the critique skill via the CLI alias `sdlc-tool verdict record --stage CRITIQUE
--verdict "$VERDICT_STRING" --issue-number "$ISSUE_NUMBER"` (do-plan-critique/SKILL.md, Step
5.5). Inside `record_verdict`, a dict of the form
`{"verdict": ..., "recorded_at": ..., "artifact_hash": ...}` is atomically written to
`AgentSession.stage_states["_verdicts"]["CRITIQUE"]` via
`tools.stage_states_helpers.update_stage_states`. The router reads this dict back through
`_latest_critique_verdict()` in `agent/sdlc_router.py` (lines 212-221), which prefers
`meta["latest_critique_verdict"]` when already populated by `sdlc_stage_query`, and falls back
to `stage_states["_verdicts"]["CRITIQUE"]` directly. Guards G1, G5, and dispatch rows 2b, 2c,
3, 4a-4c all depend on this field being present and non-empty for correct routing.

#### 2. Marker Flip: Who Should Flip CRITIQUE to `completed` (and Why It Stays `in_progress`)

The completion marker is written by `tools/sdlc_stage_marker.py::write_marker()` via
`sdlc-tool stage-marker --stage CRITIQUE --status completed`. The critique skill
(`do-plan-critique/SKILL.md`, Step 5.5) is the sole caller, and it explicitly writes the
`completed` marker **only on a `READY TO BUILD` verdict**. For `NEEDS REVISION` and
`MAJOR REWORK` verdicts, the skill intentionally leaves the marker at `in_progress` so that
router rows 2b and 3 can re-route to `/do-plan`. The Stage Marker section of the SKILL.md
makes this explicit: "On a READY TO BUILD verdict, write the completion marker; on any other
verdict, leave it `in_progress`." This is deliberate design, not a bug. Intermediate
`NEEDS REVISION` verdicts ARE persisted in `_verdicts.CRITIQUE` via the mandatory Step 5.5
`sdlc-tool verdict record` call on every exit path, but the stage marker is intentionally left
at `in_progress` because the critique cycle is not complete until the plan passes. The CRITIQUE
marker being `in_progress` through revision rounds is correct. It transitions to `completed`
only when the critique produces a `READY TO BUILD` verdict, at which point Step 5.5
co-locates the verdict record and the completion marker write in a single mandatory block.

#### 3. Why the `in_progress` Marker Through Merge Is Benign (and the Fix Belongs to #1654)

The gap — the CRITIQUE stage marker lingering at `in_progress` after a successful
`READY TO BUILD` verdict has been recorded and `/do-build` dispatched — arises when the
Step 5.5 completion marker write either fails silently (e.g., Redis unreachable at that moment)
or the marker is not written because the session's `CRITIQUE` stage was never explicitly
started before the critique ran. Because the router is never invoked after `/do-merge`
completes, a stale `in_progress` CRITIQUE marker has no runtime effect on routing decisions
post-merge and cannot cause a misroute. The artifact is benign for the same reason that row
10 (`_rule_ready_to_merge`) gates on `_stages_completed(stage_states, needed)` which includes
`CRITIQUE` — if CRITIQUE is still `in_progress` the router would route to critique rather than
merge, which is a visible signal that prompts investigation rather than a silent failure. The
correct fix — ensuring the completion marker and verdict record are always co-located atomically
on the `READY TO BUILD` path, and adding a post-merge audit to surface lingering
`in_progress` markers — is deferred entirely to issue #1654, which owns the
verdict-persistence and marker-lifecycle correctness work. No code changes are made in this
investigation.

References: https://github.com/tomcounsell/ai/issues/1654

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (Gap B fix-now-vs-defer decision is closed in favor of defer per critique concern #4 — no PM gate remains)
- Review rounds: 1

The gating spike is already resolved. Remaining work is one small router rule + its regression test (Gap A) plus a bounded read-only investigation with a written disposition (Gap B). This is a narrow, well-scoped change to a single file with an established test surface.

## Prerequisites

No prerequisites — this work has no external dependencies. The router is pure Python with no external services or API keys.

## Solution

### Key Elements

- **spike-1 outcome (done)**: Reported #1680 dead-ends refuted. Recorded in this plan; no code change for the CRITIQUE path.
- **Gap A rule (new row 8c)**: A REVIEW empty-verdict re-dispatch predicate `_rule_review_in_progress_no_verdict` — structural twin of `_rule_critique_in_progress_no_verdict` (row 2c). Fires when REVIEW is `in_progress`, no review verdict is recorded (neither `_verdicts.REVIEW` nor `meta.latest_review_verdict`), and PATCH is not completed → dispatch `/do-pr-review`.
- **Gap A regression test**: `TestReviewInProgressNoVerdictDeadEnd`, mirroring `TestCritiqueInProgressNoVerdictDeadEnd` (re-dispatch case, defers-when-PATCH-completed case, recorded-verdict-unaffected case).
- **Gap B investigation**: A read-only trace of why the CRITIQUE marker is not flipped to `completed` on the final verdict and why intermediate verdicts are not persisted. Output: a written disposition in the plan linking #1654. **Per critique concern #4, there is no fix-now branch** — spike-1 proved the artifact benign, so Gap B is a documentation-only trace deferred entirely to #1654; do not add any marker-flip or verdict-record code change in this plan.

### Flow

Router asked to route → REVIEW marker `in_progress`, verdict empty, PATCH not completed → **new row 8c fires** → `Dispatch(/do-pr-review)` → review re-runs and records a verdict → normal REVIEW-stage routing resumes (rows 8 / 8b / 9 / 10).

### Technical Approach

- **Gap A predicate** — add `_rule_review_in_progress_no_verdict(stage_states, meta, context)` near the existing REVIEW rules (~`agent/sdlc_router.py:874`), modeled on `_rule_critique_in_progress_no_verdict` (`:791-820`):
  - Return False if `not meta.get("pr_number")` — REVIEW only exists once a PR exists, so unlike row 2c this rule REQUIRES `meta.get("pr_number")`. (This is the one intentional asymmetry: CRITIQUE row 2c gates on *no* PR; REVIEW row 8c gates on *a* PR existing.)
  - Return False if `stage_states.get("REVIEW") != STATUS_IN_PROGRESS`.
  - Return False if a review verdict IS recorded (check both `meta.get("latest_review_verdict")` and `_verdict_text(stage_states["_verdicts"].get("REVIEW"))`) — let rows 8/8b own a recorded verdict. **Implementation note (critique concern #2, correctness — most actionable):** the verdict-empty predicate MUST mirror twin row 2c EXACTLY, including the `.strip()`. Row 2c's empty check is `if _latest_critique_verdict(stage_states, meta).strip():` at `agent/sdlc_router.py:818` (the critique-cited `:121` was a drift artifact; the real anchor is `:818`). A whitespace-only verdict (`" "`, `"\n"`) MUST be treated as empty so the rule fires; without `.strip()`, a whitespace verdict reads as "recorded," the rule returns False, and the state falls through to `Blocked` — re-introducing the exact dead-end Gap A closes. So: build the REVIEW analogue as a `_latest_review_verdict(stage_states, meta)` helper (mirroring `_latest_critique_verdict`) and gate on `.strip()` being falsy. This is a one-line fix; do not invent a new emptiness convention — copy 2c.
  - Step aside for row 8b on the SAME predicate row 8b uses — do NOT use a bare `PATCH == completed` check. **Implementation note (re-critique concern, correctness — MOST load-bearing):** row 8b (`_rule_patch_applied_after_review`, `agent/sdlc_router.py:874-882`) requires THREE conditions to own a state — `meta.get("pr_number")` present, `stage_states.get("PATCH") == STATUS_COMPLETED`, AND `meta.get("last_dispatched_skill") == SKILL_DO_PATCH`. A bare `if stage_states.get("PATCH") == STATUS_COMPLETED: return False` in row 8c is therefore NOT the disjoint complement of 8b: a PATCH-completed state whose `last_dispatched_skill` is something OTHER than `/do-patch` makes 8b return False (8b does not own it), and the bare check would ALSO make 8c return False — so the state falls through every REVIEW row and leaks back to `Blocked`, re-introducing the exact dead-end Gap A closes. FIX: gate row 8c's step-aside on `_rule_patch_applied_after_review(stage_states, meta, context)` returning True — i.e. `if _rule_patch_applied_after_review(stage_states, meta, context): return False` — so 8b and 8c are properly disjoint with no Blocked leak. Row 8c steps aside ONLY for the precise states 8b actually claims, and OWNS every other PATCH-completed-but-not-8b-claimed empty-verdict state itself (re-dispatching `/do-pr-review`). Do NOT use the bare `PATCH == STATUS_COMPLETED` comparison.
  - Otherwise return True → `/do-pr-review`.
  - **Implementation note (critique concern #1, prove-the-gap-first):** Gap A is currently asserted by *symmetry* with row 2c — spike-1 refuted the originating #1680 report, so the dead-end has not been observed directly, only inferred from the missing 2c analogue. Before (or as the first step of) writing the fix, demonstrate the gap is real: (1) a short reachability check that the UNFIXED router (REVIEW `in_progress`, empty/whitespace verdict, no completed PATCH, PR present) genuinely falls through every existing REVIEW row to `Blocked`; and (2) a regression test capturing that pre-fix behavior — assert the UNFIXED state returns `Blocked`, THEN (after row 8c lands) flip the same state to assert `Dispatch(/do-pr-review)`. The test should make the before/after observable in one place (e.g., the "previously-`Blocked`" assertion in Error State Rendering below is the after-half; add the before-half as a documented baseline so a reviewer can see the gap existed). This converts "asserted by symmetry" into "demonstrated, then closed."
- **Rule placement** — register as row `8c` in `DISPATCH_RULES` AFTER row 8b and BEFORE row 9 (`_rule_review_approved_docs_not_done`). Placement after 8b ensures a completed-PATCH state is owned by 8b (re-review), and before 9 ensures it pre-empts the docs/merge rows that require a completed REVIEW. The predicate's `__doc__` must be non-empty — this is the ONLY SKILL.md⇄router parity gate (`test_every_dispatch_rule_has_documented_predicate` asserts every predicate's `__doc__` is non-empty; there is no per-row markdown table to update). **Implementation note (re-critique concern — where the parity gate is satisfied):** the `__doc__` parity gate is satisfied by adding an entry to the dedicated `_rule_<name>.__doc__ = "..."` OVERRIDE/registration block near the bottom of `agent/sdlc_router.py` (the same block that already sets `_rule_critique_in_progress_no_verdict.__doc__`, `_rule_review_has_findings.__doc__`, `_rule_patch_applied_after_review.__doc__`, etc. at `agent/sdlc_router.py:915-933`) — NOT by writing an inline triple-quoted docstring inside the `def`. Because the override block reassigns `.__doc__`, an inline docstring on the function would be silently overwritten and would NOT be what the parity test reads. So: add `_rule_review_in_progress_no_verdict.__doc__ = "Review in_progress, no verdict recorded (stalled) — re-review"` to that override block. An inline docstring on the `def` is optional human documentation, but the builder must NOT expect it to be the parity gate — the override-block entry is the gate. Do NOT add a `| 8c | ... |` row to SKILL.md — the Step-4 table was removed in #1216 and `test_step4_has_no_hand_authored_dispatch_table` asserts no `| digit |` rows exist; re-adding one would fail.
- **Parity-set update (required)** — `test_dispatch_rules_cover_expected_row_ids` (`tests/unit/test_sdlc_skill_md_parity.py:132-149`) hardcodes an `expected` row-id set and asserts no extras. Registering row `8c` without adding `"8c"` to that set fails on `extra={'8c'}`. Add `"8c",` to the `expected` set in that test.
- **Loop bound** — like row 2c, the new rule is bounded by G4 oscillation (it does not increment `critique_cycle_count`), not G2. Document this in the predicate docstring. **Implementation note (critique concern #3, loop-safety):** at build time, explicitly confirm the G4 oscillation guard covers row 8c. Row 8c re-dispatches `/do-pr-review` (a forward REVIEW-stage skill that writes a verdict), NOT same-stage critique re-runs, so each fire moves the pipeline toward a recorded verdict; once a verdict lands, rows 8/8b own the state and 8c returns False. The only way 8c could re-fire is if `/do-pr-review` completes without ever persisting a verdict — and that repeated no-verdict re-dispatch is exactly what G4's `same_stage_dispatch_count` bound catches and escalates. Verify (read `evaluate_guards`/G4) that a row-8c dispatch increments the same-stage dispatch counter G4 reads, so a pathological "review never records a verdict" loop is bounded and escalates rather than spinning forever. If G4 does NOT count 8c dispatches, that is a finding to surface — but do not add a new guard; 8c is a twin of 2c and inherits 2c's bounding.
- **Gap B** — trace the verdict-recording path: who writes `_verdicts.CRITIQUE` and flips the CRITIQUE marker to `completed`. Likely owners: the `/do-plan-critique` skill's verdict-record step and `tools/sdlc_*` stage-marker helpers. With #1736 merged (issue-number diversion ruled out), determine whether the final-verdict path fails to (a) flip the marker and (b) persist intermediate NEEDS REVISION verdicts. **Implementation note (critique concern #4 — no fix-now escape hatch):** spike-1 proved the non-flipped CRITIQUE marker is a *benign* artifact (it only produces a terminal `Blocked` on an already-merged pipeline that the router is never asked to route past — not a live dead-end). Gap B is therefore a **read-only trace that produces a written disposition pointing at #1654 — there is NO fix-now option in this plan.** Do not add any marker-flip or intermediate-verdict-record write here; the deep fix is fully owned by #1654. The disposition is documentation only: confirm the path and link #1654. (This removes the prior fix-now-vs-defer fork — the fork is closed in favor of defer.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_rule_review_in_progress_no_verdict` must NOT contain a bare `except` — it reads dict keys with `.get()` and compares constants, no parsing. Mirror row 2c's exception-free style. The router's `decide_next_dispatch` already wraps every predicate in `try/except` and logs at debug (`agent/sdlc_router.py:1128-1131`); a regression test asserts the new predicate never raises on malformed `_verdicts`.
- [ ] Gap B adds NO marker-flip/verdict-record write in this plan (concern #4 cut the fix-now branch), so there is no new Redis write or exception surface to cover here. Gap B is read-only.

### Empty/Invalid Input Handling
- [ ] Test the new rule against `_verdicts={}`, `_verdicts={"REVIEW": None}`, `_verdicts={"REVIEW": {}}`, missing `REVIEW` marker (None), and whitespace-only `latest_review_verdict` — all must NOT spuriously fire or raise.
- [ ] This is router state routing, not agent-output processing — no silent-loop risk beyond what G4 already bounds (and the test asserts the rule self-corrects via re-review rather than oscillating).

### Error State Rendering
- [ ] The router has no user-visible output directly; its `Blocked`/`Dispatch` is consumed by the `/sdlc` skill. Test asserts the previously-`Blocked` REVIEW empty-verdict state now returns a `Dispatch(/do-pr-review)` — i.e. the dead-end is observably gone.

## Test Impact

- [ ] `tests/unit/test_sdlc_router_decision.py::TestDispatchRulesTable` — UPDATE: the rule-count / row-id assertions must include new row `8c`.
- [ ] `tests/unit/test_sdlc_router_decision.py` — ADD: new class `TestReviewInProgressNoVerdictDeadEnd` mirroring `TestCritiqueInProgressNoVerdictDeadEnd` (re-dispatch, defers-on-completed-PATCH, recorded-verdict-unaffected). **Per critique concern #1, this class MUST include a gap-demonstration case** asserting the UNFIXED state (REVIEW `in_progress`, empty/whitespace verdict, no completed PATCH, PR present) returns `Blocked` before row 8c exists — i.e. the gap is real — paired with the post-fix `Dispatch(/do-pr-review)` assertion. The whitespace-only-verdict variant (concern #2) is part of this same case set. **Implementation note (re-critique concern — pin the REVIEW state explicitly):** every test case in this class MUST set `stage_states["REVIEW"] == STATUS_IN_PROGRESS` explicitly — do NOT construct fixtures that merely leave REVIEW in some arbitrary non-completed state (e.g. `None`, missing, or `pending`). Row 8c gates on `stage_states.get("REVIEW") == STATUS_IN_PROGRESS`, so a test that does not pin REVIEW to `in_progress` would either not exercise the rule (false green) or assert against the wrong code path. Pin `REVIEW == "in_progress"` in the re-dispatch case, the whitespace-verdict case, and the gap-demonstration (pre-fix `Blocked`) case alike; the defers-on-completed-PATCH case also pins REVIEW `in_progress` so it isolates the PATCH step-aside as the only reason 8c does not fire.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py::test_dispatch_rules_cover_expected_row_ids` — UPDATE: add `"8c"` to the hardcoded `expected` row-id set (`:132-149`) or it fails on `extra={'8c'}`. The `__doc__`-non-empty parity gate (`test_every_dispatch_rule_has_documented_predicate`) is satisfied automatically by the new predicate's docstring — no SKILL.md edit. Do NOT add a `| 8c |` table row to `.claude/skills-global/sdlc/SKILL.md`: `test_step4_has_no_hand_authored_dispatch_table` asserts Step 4 has no `| digit |` rows (table removed in #1216), so adding one would FAIL.
- [ ] Existing `TestRow8ReviewHasFindings`, `TestRow8bPatchAppliedAfterReview`, `TestRow9ReviewApprovedDocsNotDone` — verify (not modify) that inserting 8c between 8b and 9 does not change their outcomes; add an ordering assertion if absent.

No existing test is deleted — the change is additive (one new rule + new tests). The only updates are to count/parity assertions that must learn about row 8c.

## Rabbit Holes

- **Rewriting war-room verdict aggregation** — Gap B's deep fix (persisting intermediate critique verdicts from the war room) belongs to #1654. This plan only investigates and dispositions Gap B read-only (no code change — concern #4); it does NOT rewrite the aggregation pipeline or flip the marker.
- **Fixing the CRITIQUE path** — spike-1 refuted the CRITIQUE dead-end and #1639/#1668 already cover it. Do not touch rows 2b/2c.
- **Reconciling the terminal `Blocked('no matching rule')` on a fully-merged pipeline** — this is a benign artifact of a merged pipeline being re-queried; chasing a "route past merge" rule is scope creep. Note it, move on.
- **Generalizing all CRITIQUE/REVIEW rules into a shared abstraction** — tempting given the twin structure, but a shared meta-rule generator would obscure the per-row reasoning the SKILL.md parity test depends on. Keep row 8c as an explicit twin of 2c.

## Risks

### Risk 1: New row 8c overlaps with row 8 or 8b, changing existing routing
**Impact:** A state currently routed by row 8 (CHANGES REQUESTED) or 8b (patch-applied) gets stolen by 8c, breaking established flows.
**Mitigation:** 8c's predicate explicitly returns False when a verdict is recorded (8 owns that) and when `_rule_patch_applied_after_review(...)` returns True (8b owns that — gated on the SAME three-condition predicate 8b uses, NOT a bare `PATCH == completed`, so there is no Blocked leak for PATCH-completed-but-not-8b-claimed states), so it is disjoint by construction. The ordering test (8b before 8c before 9) and the verify-existing-rows tasks lock this in.

### Risk 2: Gap B "fix-now" balloons into a #1654-sized change
**Impact:** Scope creep; the small Gap A fix gets stuck behind an aggregation rewrite.
**Mitigation:** Per critique concern #4, the fix-now branch is cut entirely — spike-1 proved the artifact benign, so Gap B is a read-only investigation with a written disposition that defers to #1654. No marker-flip or verdict-record write lands in this plan, so there is nothing to balloon. This risk is closed by removing the option, not by a PM decision.

## Race Conditions

No race conditions identified — `decide_next_dispatch` and all dispatch-rule predicates are pure, synchronous functions over an immutable input dict. They perform no I/O and hold no shared mutable state. (Gap B adds no code change per concern #4 — it is a read-only trace — so it introduces no new concurrency surface at all.)

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1654] Persisting intermediate war-room critique verdicts, the CRITIQUE marker-flip, and the deep aggregation fix — Gap B links here. Per critique concern #4, NO Gap B code change is in scope for this plan (the fix-now option is cut); Gap B is a read-only trace that defers entirely to #1654.
- Nothing else deferred — the CRITIQUE-path work is dropped (refuted/already-covered, not deferred), and the Gap A rule + test are fully in scope for this plan.

## Update System

No update system changes required — this is a pure-Python change to `agent/sdlc_router.py` and its unit tests (one new dispatch rule plus the `expected` row-id set in `tests/unit/test_sdlc_skill_md_parity.py`). No SKILL.md edit, no new dependencies, config files, services, or migration steps. The router ships with the repo; `/update` propagates it via the normal git pull with no special handling.

## Agent Integration

No new agent integration required — `agent/sdlc_router.py::decide_next_dispatch` is already imported and called by the `/sdlc` router skill and the worker's supervision loop; it is not exposed via an MCP server or a CLI entry point and does not need to be. The new row 8c is reached through the existing call path. The bridge does not import the router directly. Integration coverage is the existing `/sdlc` skill exercising the rule table; the new behavior is verified by the unit regression tests in `tests/unit/test_sdlc_router_decision.py`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-router-oscillation-guard.md` to document the new REVIEW empty-verdict re-dispatch rule (row 8c) alongside the existing CRITIQUE row 2c, noting the restored CRITIQUE/REVIEW symmetry.
- [ ] Update `docs/features/sdlc-pipeline-state.md` if it enumerates the dispatch rule rows, to add row 8c.
- [ ] No new `docs/features/README.md` index entry needed — these are existing feature docs being updated, not a new feature page.

### External Documentation Site
- [ ] No external docs site — this repo does not use Sphinx/MkDocs/RTD for the router. N/A.

### Inline Documentation
- [ ] Add the `_rule_review_in_progress_no_verdict.__doc__` human-state string to the override/registration block at `agent/sdlc_router.py:915-933` (this is the parity-test requirement — the override block, not an inline docstring, is the gate). An optional inline docstring mirroring row 2c's (citing #1687 and the G4 loop-bound) may be added as human-readable documentation, but it does NOT satisfy the parity gate since the override block reassigns `__doc__`.
- [ ] Do NOT edit `.claude/skills-global/sdlc/SKILL.md` — the Step-4 dispatch table was removed in #1216, and `test_step4_has_no_hand_authored_dispatch_table` forbids re-adding any `| digit |` row. Router⇄SKILL.md parity is satisfied solely by the new predicate carrying a non-empty `__doc__`.
- [ ] Add `"8c"` to the hardcoded `expected` row-id set in `tests/unit/test_sdlc_skill_md_parity.py:132-149` (otherwise `test_dispatch_rules_cover_expected_row_ids` fails on `extra={'8c'}`).
- [ ] Record the spike-1 refutation and the Gap B disposition in this plan (the durable investigation record).

## Success Criteria

- [ ] spike-1 outcome recorded: reported #1680 dead-ends confirmed/refuted (DONE — refuted, see Spike Results).
- [ ] New rule `_rule_review_in_progress_no_verdict` added and registered as row 8c, disjoint from rows 8 and 8b.
- [ ] `TestReviewInProgressNoVerdictDeadEnd` added, mirroring `TestCritiqueInProgressNoVerdictDeadEnd`, and passing.
- [ ] A previously-`Blocked` REVIEW `in_progress`-empty-verdict-no-completed-PATCH state now returns `Dispatch(/do-pr-review)`.
- [ ] `"8c"` added to the `expected` set in `tests/unit/test_sdlc_skill_md_parity.py`; both parity tests (`test_dispatch_rules_cover_expected_row_ids`, `test_every_dispatch_rule_has_documented_predicate`, `test_step4_has_no_hand_authored_dispatch_table`) pass. No `.claude/skills-global/sdlc/SKILL.md` edit (the Step-4 table was removed in #1216).
- [ ] Gap B read-only disposition written, deferring the fix to #1654 with a link (no fix-now marker-flip — concern #4).
- [ ] No stale `project_sdlc_router_needs_revision_deadlock.md` memory note exists (verified; prune if one surfaces).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (router-rule)**
  - Name: router-rule-builder
  - Role: Add `_rule_review_in_progress_no_verdict` + row 8c registration + non-empty docstring (no SKILL.md edit).
  - Agent Type: builder
  - Resume: true

- **Builder (gap-b-investigator)**
  - Name: gapb-investigator
  - Role: Read-only trace of the verdict-recording / marker-flip path; produce written Gap B disposition.
  - Agent Type: debugging-specialist
  - Resume: true

- **Test Engineer (router-tests)**
  - Name: router-test-engineer
  - Role: Add `TestReviewInProgressNoVerdictDeadEnd`; update count/parity/ordering assertions.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (router)**
  - Name: router-validator
  - Role: Verify rule disjointness, test pass, parity, and Gap B disposition completeness.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: router-documentarian
  - Role: Update `docs/features/sdlc-router-oscillation-guard.md` and `sdlc-pipeline-state.md`.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — builder, validator, test-engineer, debugging-specialist, documentarian used above.)

## Step by Step Tasks

### 1. Gap B investigation (read-only)
- **Task ID**: investigate-gapb
- **Depends On**: none
- **Validates**: written disposition in this plan's Spike Results / a comment on #1687
- **Informed By**: spike-1 (refuted CRITIQUE loop), #1654, #1736 (issue-number diversion ruled out)
- **Assigned To**: gapb-investigator
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Trace who flips the CRITIQUE marker to `completed` and who writes `_verdicts.CRITIQUE`.
- Identify the code path where the marker stays `in_progress` through merge and where intermediate verdicts fail to reach the verdict store.
- Produce a read-only disposition that defers the fix to #1654 with a link (per critique concern #4, there is NO fix-now branch — do not write any marker-flip or verdict-record code). The fix-now-vs-defer decision is already closed in favor of defer; this task only documents the trace and links #1654.

### 2. Add Gap A rule (row 8c)
- **Task ID**: build-row-8c
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_router_decision.py (new class), router⇄SKILL.md parity test
- **Informed By**: spike-1, row 2c (`_rule_critique_in_progress_no_verdict`) as the structural template
- **Assigned To**: router-rule-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_rule_review_in_progress_no_verdict` near `agent/sdlc_router.py:874`, modeled on row 2c but gating on `pr_number` PRESENT and `PATCH != completed`.
- Register as row `8c` between 8b and 9 in `DISPATCH_RULES`; add the `_rule_review_in_progress_no_verdict.__doc__ = "..."` entry to the override/registration block at `agent/sdlc_router.py:915-933` (this override-block entry — NOT an inline docstring — is what satisfies the `__doc__`-non-empty parity gate; an inline docstring would be overwritten by the override block).
- Do NOT edit `.claude/skills-global/sdlc/SKILL.md` — the Step-4 table was removed in #1216 and re-adding a `| 8c |` row fails `test_step4_has_no_hand_authored_dispatch_table`. The only parity update is adding `"8c"` to the `expected` set in `tests/unit/test_sdlc_skill_md_parity.py` (done by the test engineer in task 3).

### 3. Add Gap A regression tests
- **Task ID**: test-row-8c
- **Depends On**: build-row-8c
- **Assigned To**: router-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestReviewInProgressNoVerdictDeadEnd` mirroring `TestCritiqueInProgressNoVerdictDeadEnd`: re-dispatch case, defers-when-PATCH-completed case, recorded-verdict-unaffected case, malformed-`_verdicts`-does-not-raise case, whitespace-only-verdict case, and the pre-fix gap-demonstration (`Blocked`) case. Every case MUST pin `stage_states["REVIEW"] == STATUS_IN_PROGRESS` explicitly (not an arbitrary non-completed state). The defers-when-PATCH-completed case must construct a state that 8b actually CLAIMS (`pr_number` present, `PATCH == completed`, `last_dispatched_skill == /do-patch`) so it confirms 8c steps aside via `_rule_patch_applied_after_review` rather than a bare `PATCH == completed` check; add a companion case where PATCH is completed but `last_dispatched_skill != /do-patch` asserting 8c OWNS that state (re-dispatches `/do-pr-review`, no Blocked leak).
- Update `TestDispatchRulesTable` count/row-id assertions to include row 8c.
- Add `"8c"` to the hardcoded `expected` row-id set in `tests/unit/test_sdlc_skill_md_parity.py:132-149` so `test_dispatch_rules_cover_expected_row_ids` does not fail on `extra={'8c'}`. Do NOT touch `.claude/skills-global/sdlc/SKILL.md`.
- Add/confirm an ordering assertion: 8b before 8c before 9.

### 4. Validate
- **Task ID**: validate-router
- **Depends On**: investigate-gapb, build-row-8c, test-row-8c
- **Assigned To**: router-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_router_decision.py -q`.
- Verify row 8c is disjoint from 8 and 8b (no existing test outcome changed).
- Verify Gap B disposition is written and the PM decision is captured.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-router
- **Assigned To**: router-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-router-oscillation-guard.md` and `docs/features/sdlc-pipeline-state.md` for row 8c.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: router-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit suite for the router, lint, and format checks.
- Verify all success criteria including documentation and Gap B disposition.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router tests pass | `pytest tests/unit/test_sdlc_router_decision.py -q` | exit code 0 |
| New rule present | `grep -c "_rule_review_in_progress_no_verdict" agent/sdlc_router.py` | output > 1 |
| Row 8c registered | `grep -c 'row_id="8c"' agent/sdlc_router.py` | output contains 1 |
| New test class present | `grep -c "class TestReviewInProgressNoVerdictDeadEnd" tests/unit/test_sdlc_router_decision.py` | output contains 1 |
| Lint clean | `python -m ruff check agent/sdlc_router.py tests/unit/test_sdlc_router_decision.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sdlc_router.py tests/unit/test_sdlc_router_decision.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Plan referenced non-existent `.claude/skills/sdlc/SKILL.md` (5×) | Plan revision 2026-06-22 | All occurrences corrected to `.claude/skills-global/sdlc/SKILL.md` (the path `test_sdlc_skill_md_parity.py:28` resolves); most SKILL.md-edit tasks removed as moot — see next row. |
| BLOCKER | critique | "Add row 8c cell to SKILL.md" task was a false premise — `test_step4_has_no_hand_authored_dispatch_table` forbids `\| digit \|` rows (table removed #1216), and `test_dispatch_rules_cover_expected_row_ids` hardcodes the expected set and rejects extras | Plan revision 2026-06-22 | Removed all "add SKILL.md row 8c" tasks; the non-empty `__doc__` parity gate is the only SKILL parity requirement. Added a task to append `"8c"` to the `expected` set at `tests/unit/test_sdlc_skill_md_parity.py:132-149`. |
| NIT | critique | Open Question 2 (REVIEW empty-verdict `pr_number` gate) is self-resolving but framed as pending | Plan revision 2026-06-22 | Marked RESOLVED inline in Open Questions; builder treats the `pr_number`-present gate as decided, with a no-PR regression case asserting non-firing. |
| CONCERN #2 | re-critique | Row 8c verdict-empty predicate must mirror twin row 2c EXACTLY, including the `.strip()` — a whitespace-only verdict must read as empty or it falls through to `Blocked` | Notes-embed 2026-06-22 | Technical Approach Gap A predicate bullet now mandates a `_latest_review_verdict(...)` helper gated on `.strip()` being falsy, mirroring row 2c at `agent/sdlc_router.py:818` (the critique-cited `:121` was a drift artifact). One-line fix; copy 2c, do not invent a new emptiness convention. |
| CONCERN #1 | re-critique | Gap A is asserted by symmetry only (spike-1 refuted the originating #1680 report) — demonstrate the gap is real before closing it | Notes-embed 2026-06-22 | Added a reachability-check + before/after regression note: assert the UNFIXED state (REVIEW `in_progress`, empty verdict, no completed PATCH, PR present) returns `Blocked`, THEN assert post-fix `Dispatch(/do-pr-review)`. Folded into Technical Approach and the `TestReviewInProgressNoVerdictDeadEnd` test-impact item. |
| CONCERN #3 | re-critique | Confirm the G4 oscillation loop-bound applies to row 8c at build time (8c re-dispatches `/do-pr-review`, not same-stage critique) — confirm it cannot infinite-loop | Notes-embed 2026-06-22 | Loop-bound bullet now directs the builder to verify (read `evaluate_guards`/G4) that a row-8c dispatch increments the same-stage dispatch counter G4 reads, so a "review never records a verdict" loop is bounded and escalates rather than spinning. 8c inherits 2c's bounding; do not add a new guard. |
| CONCERN #4 | re-critique | Cut the Gap B "fix-now" escape hatch — spike-1 proved the artifact benign; Gap B stays a deferred read-only trace to #1654 | Notes-embed 2026-06-22 | Removed the fix-now branch everywhere (Desired Outcome, Solution, Technical Approach, Risk 2, No-Gos, Open Question 1, task 1, Success Criteria, Appetite, Race Conditions, Failure Path). Gap B is now documentation-only: trace the path, link #1654, write NO marker-flip/verdict-record code. PM check-in dropped to 0. |
| NIT | re-critique | Minor wording nit (non-blocking) | Notes-embed 2026-06-22 | No action required for build; subsumed by the concern edits above. |
| CONCERN (correctness, MOST load-bearing) | re-critique | Row 8c's "skip if PATCH completed" must NOT be a bare `if PATCH == completed: return False` — row 8b additionally requires `last_dispatched_skill == /do-patch`, so a PATCH-completed state with a DIFFERENT last skill would leak back to `Blocked` | Notes-embed 2026-06-22 (additive) | Technical Approach + task 2 + Risk 1 now gate the step-aside on `_rule_patch_applied_after_review(stage_states, meta, context)` (the same predicate row 8b uses), making 8b and 8c properly disjoint with no Blocked leak. The bare `PATCH == STATUS_COMPLETED` comparison is explicitly forbidden. |
| CONCERN (test rigor) | re-critique | Test cases must pin `REVIEW == "in_progress"` explicitly, not just any non-completed state | Notes-embed 2026-06-22 (additive) | Test Impact + task 3 now require every `TestReviewInProgressNoVerdictDeadEnd` case to set `stage_states["REVIEW"] == STATUS_IN_PROGRESS` explicitly; added a companion case (PATCH completed, `last_dispatched_skill != /do-patch`) asserting 8c OWNS that state. |
| CONCERN (parity-gate location) | re-critique | The `.__doc__` parity gate is satisfied by the override/registration block, not an inline docstring — builder must not add a redundant inline docstring expecting it to be the gate | Notes-embed 2026-06-22 (additive) | Technical Approach, task 2, and Inline Documentation now direct the builder to add `_rule_review_in_progress_no_verdict.__doc__ = "..."` to the override block at `agent/sdlc_router.py:915-933`; an inline docstring is optional and would be overwritten by the override block, so it does NOT satisfy the gate. |
| CONCERN (doc accuracy) | re-critique | "Why Previous Fixes Failed" table mis-mapped row 8b as the row-2b twin | Notes-embed 2026-06-22 (additive) | Corrected: row 2b's REVIEW twin is the `_review_verdict_is_stale` step-aside INSIDE row 8 (PR #1657), NOT row 8b (which is `_rule_patch_applied_after_review`, patch-applied re-review). Table and root-cause paragraph rewritten. |

---

## Open Questions

1. **Gap B disposition (RESOLVED — critique concern #4, no human input needed):** spike-1 showed the non-flipped CRITIQUE marker causes a benign terminal `Blocked` on a fully-merged pipeline, not a live dead-end. The earlier fix-now-vs-defer fork is **closed in favor of defer**: Gap B is a read-only trace that produces a written disposition linking #1654, with NO code change in this plan. The builder treats this as decided — do not implement any marker-flip or verdict-record write; this plan stays focused on Gap A.
2. **Row 8c `pr_number` gate (RESOLVED — self-resolving, no human input needed):** the proposed rule requires `pr_number` PRESENT (REVIEW only exists post-PR), inverting row 2c's no-PR gate. This is the intended asymmetry — a REVIEW empty-verdict state with no PR is structurally impossible (REVIEW is reachable only after BUILD opens a PR), so the gate acts as a defensive safety assertion, not a behavior change. **Resolution:** implement the `pr_number`-present gate as specified in Technical Approach; the regression test includes a no-PR case asserting the rule does NOT fire (returns False). The builder should treat this as decided, not pending.
