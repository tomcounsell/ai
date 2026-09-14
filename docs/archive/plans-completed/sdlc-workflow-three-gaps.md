---
status: Planning
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-04-28
tracking: https://github.com/tomcounsell/ai/issues/1186
last_comment_id: 0
---

# SDLC Workflow — Three Gaps from PR #1184

## Problem

Three small frictions in the SDLC workflow surfaced while shepherding PR #1184 to merge. Each is a hole in an existing skill or tool — none are architectural. The issue body (#1186) is the source of truth; this plan executes its `## Solution Sketch` verbatim, with one open question on Finding 1 (see below).

**Current behavior:**

1. **TEST stage can be skipped between BUILD and REVIEW.** `agent/sdlc_router.py:521` (`_rule_pr_exists_no_review`) fires whenever a PR exists and `REVIEW` is `pending`/`ready`/empty, with no precondition that `TEST == "completed"`. Result on PR #1184: `/do-pr-review` and `/do-docs` both completed against a `TEST=pending` state, then `/do-merge` was dispatched 3× before G4 oscillation surfaced the gap. Manual `/do-test` was needed to unblock.
2. **`/do-build` ships work without ticking plan checkboxes.** PR #1184 hit the merge gate's plan completion check (`.claude/commands/do-merge.md:179-249`) with 27 unchecked items even though every deliverable shipped. `git log --grep='tick off\|check off'` shows 8 prior commits paying the same manual-cleanup tax. The `/do-build` skill files (`PR_AND_CLEANUP.md`, `WORKFLOW.md`) never instruct the agent to mutate the plan file's checkboxes.
3. **Pre-existing ruff errors on main block the merge gate.** `python -m ruff check .` exits with 18 errors on `main` at `4266ecc9`: 17 auto-fixable `datetime.timezone.utc → datetime.UTC` (`UP017`) plus one `E501` line-too-long at `worker/idle_sweeper.py:28`. `docs/sdlc/do-merge.md:8-13` declares `ruff check . exit 0` a hard merge gate.

**Desired outcome:**

1. The router refuses to dispatch `/do-pr-review` or `/do-merge` when `stage_states["TEST"] != "completed"` — instead it falls through to `/do-test` (or defers / blocks).
2. `/do-build` leaves the plan with all non-excluded checkboxes ticked once the PR is open and the build's own validators pass. No follow-up cleanup commit.
3. `python -m ruff check .` exits 0 on the new tip of `main`. Single small chore PR.

## Freshness Check

**Baseline commit:** `4266ecc9` (`Bump deps: claude-agent-sdk 0.1.68->0.1.69`)
**Issue filed at:** `2026-04-28T01:31:21Z`
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sdlc_router.py:521-532` (`_rule_pr_exists_no_review`) — matches issue claim verbatim. No TEST precondition. Confirmed.
- `agent/sdlc_router.py:579-584` (`_rule_ready_to_merge`) — `needed = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS"]`. TEST IS in the list. Asymmetry between Row 7 and Row 10 confirmed.
- `agent/sdlc_router.py:516-518` (`_rule_tests_failing`) — only fires on `STATUS_FAILED`, not on `pending`. Important: this means today nothing dispatches `/do-test` for a `pending` TEST when the BUILD stage was completed without producing a TEST status update. **Drift discovery → see Open Question 1.**
- `.claude/commands/do-merge.md:179-249` (`Plan Completion Gate`) — present and correct. Issue body cited "`docs/sdlc/do-merge.md` → `## Plan Completion Gate`" but that section actually lives in the global command at `.claude/commands/do-merge.md`. The repo addendum at `docs/sdlc/do-merge.md` only adds Documentation Gate and Ruff Gates. **Minor drift in the issue's pointer**, not in the underlying logic. Plan uses the correct path.
- Excluded sections from the canonical gate (`.claude/commands/do-merge.md:223`): `exclude_sections = ['Open Questions', 'Critique Results']`. Heading regex: `^#{1,3} (.+)`. These are the canonical strings to mirror in Finding 2.
- `worker/idle_sweeper.py:28` — line is 102 chars. E501 confirmed.
- `python -m ruff check .` on main — 18 errors, 17 fixable with `--fix`. Confirmed.
- `tests/unit/test_sdlc_router.py` — does not exist. The relevant test file is `tests/unit/test_sdlc_router_decision.py` (router rule tests) and `tests/unit/test_sdlc_router_oscillation.py` (G4 tests). **Minor drift in the issue's AC pointer.** Plan adds tests to `test_sdlc_router_decision.py`.

**Cited sibling issues/PRs re-checked:**
- #1175 (open) — cwd resolution causes verdicts to go unrecorded. Different cause; no overlap with this work.
- #1043 (closed) — added G6 terminal-merge-ready guard. Closed. No re-introduction risk: G6 is downstream of the predicates being modified.
- #443 (closed) — added the plan completion gate. Closed. Finding 2 closes the symmetric writer side.
- #1155 (closed) — self-healing merge gate epic. Closed. This plan plugs residuals from that epic, not regressions.

**Commits on main since issue was filed (touching referenced files):** None.
- `git log --since="2026-04-28T01:31:21Z" -- agent/sdlc_router.py .claude/skills/do-build/ docs/sdlc/do-merge.md worker/idle_sweeper.py tools/sdlc_session_ensure.py` returns empty.
- Most recent commits on main (`4266ecc9`, `bd84d8d1`, `08f0ebf4`) are the dep bump and PR #1184 merge / cleanup. None touch the gap surfaces.

**Active plans in `docs/plans/` overlapping this area:** None. `ls docs/plans/*.md` shows no plan slug containing `router`, `ruff`, `checkbox`, or `test-precondition`.

**Notes:** Two minor pointer drifts from the issue body (gate location, test file name) are corrected inline above. Both findings still hold; no premise has changed.

## Prior Art

- **#1043 (closed)**: `/do-pr-review` dispatched 8× on a mergeable PR. Resolved by adding the G6 terminal-merge-ready guard (`e8ac6bd4`). G6 only force-passes when `ci_all_passing == True`, so it does NOT compensate for the missing TEST precondition on Row 7 — Finding 1 is upstream of G6.
- **#443 (closed)**: Added the plan completion gate that scans `- [ ]` items in plan markdown. Did not address how `/do-build` populates checkbox state. Finding 2 closes that loop.
- **#1155 (closed)**: Self-healing merge gate epic. Introduced `docs/sdlc/do-merge.md` and gate scripts. Findings 1, 2, 3 are residual gaps from that epic.
- **#704 (closed)**: SDLC router moved from artifact inference to `PipelineStateMachine`. Confirms the router's contract: it must trust `stage_states` exclusively. Finding 1 is fully consistent — the fix is a predicate-level precondition on `stage_states["TEST"]`.

No prior closed issues found for "ruff baseline on main" cleanup or "router TEST precondition" specifically. Finding 3 is greenfield mechanical cleanup. Finding 1 has no prior failed attempt.

## Research

No relevant external findings — this is purely internal to repo-private skill files, pure-Python router logic, and a one-shot ruff cleanup. Skipping WebSearch per Phase 0.7 skip rule (no external libraries, APIs, or ecosystem patterns).

## Why Previous Fixes Failed

Not applicable — Findings 1, 2, 3 are first-time fixes for distinct gaps. No prior attempt addressed any of them. The 8 manual checkbox-flip commits (`6a522469`, `8696ba0b`, `d5be75e3`, `18742489`, `79d9f8dc`, `6b6bf868`, `9142dade`, `8696ba0b`) cited in the issue are *workarounds* by the human shepherd, not failed fixes.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None public. Internal predicate signatures in `agent/sdlc_router.py` unchanged — only their predicate logic changes.
- **Coupling**: Unchanged. Finding 1 tightens the contract between `_rule_pr_exists_no_review` and `stage_states`, but does not introduce new cross-module coupling. Finding 2 introduces a small read+rewrite of the plan file from inside `/do-build`, mirroring the canonical exclusion list from `/do-merge`. Finding 3 is mechanical.
- **Data ownership**: Unchanged. Plan checkbox mutation in Finding 2 happens on the session branch, in the worktree, before push — same author chain that already commits the PR.
- **Reversibility**: All three findings revert with a single `git revert`. No state migration, no schema changes.

## Appetite

**Size:** Small

**Team:** Solo dev. One reviewer.

**Interactions:**
- PM check-ins: 0–1 (only on Open Question 1 below)
- Review rounds: 1

Each finding is ~5–30 lines of meaningful change. Total diff target: < 200 lines including tests.

## Prerequisites

No prerequisites — this work has no external dependencies. Validates against existing repo state.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ruff` installed | `python -m ruff --version` | Required for Finding 3 |
| `pytest` available | `python -m pytest --version` | Required for Finding 1 unit tests |

## Solution

### Key Elements

- **Finding 1 — Router TEST precondition**: A predicate-level guard inside `_rule_pr_exists_no_review` (and a parallel guard inside `_rule_review_approved_docs_not_done` for symmetry) so neither rule fires until `TEST == "completed"`. Plus the existing `_rule_tests_failing` is widened to also match `TEST in ("pending", "ready") AND BUILD == "completed" AND PR exists` so the router actually has a way to promote `/do-test` after BUILD finishes. **No new DispatchRule rows added — only predicate logic changes — to honor the issue's "no new dispatch rules" AC.**
- **Finding 2 — `/do-build` ticks plan checkboxes**: A new helper script `scripts/tick_plan_checkboxes.py` plus a new step in `.claude/skills/do-build/PR_AND_CLEANUP.md` (Step 6.4, between the doc-validation gate and the pre-PR commit verification). The helper rewrites `- [ ]` → `- [x]` in the plan markdown, excluding lines whose enclosing `^#{1,3}` heading matches `Open Questions` or `Critique Results` (mirroring `.claude/commands/do-merge.md:223`). Commit message: `docs(#{N}): tick off completed plan items`.
- **Finding 3 — Clean ruff baseline on main**: Single chore PR. Run `python -m ruff check --fix .` (auto-fixes 17 `UP017` instances). Manually wrap the line at `worker/idle_sweeper.py:28`. Verify `ruff check .` exits 0.

### Flow

There is no user-facing flow. The flows that change:

1. **Pipeline routing flow (Finding 1)**: `BUILD completed, PR exists, TEST pending` → router dispatches `/do-test` (not `/do-pr-review`). After `/do-test` completes, normal flow resumes.
2. **Build cleanup flow (Finding 2)**: `Builders complete → validators pass → doc gate passes → tick_plan_checkboxes.py runs → docs(#N) commit pushed → pre-PR verification → PR opened`.
3. **Static analysis flow (Finding 3)**: One-time. After this PR merges, `ruff check .` on main exits 0 going forward.

### Technical Approach

**Finding 1 — `agent/sdlc_router.py` predicate edits (~15 lines):**

- Modify `_rule_pr_exists_no_review` (line 521): early-return `False` if `stage_states.get("TEST") != "completed"`. This makes Row 7 defer until TEST passes.
- Modify `_rule_review_approved_docs_not_done` (line 569): no change needed — REVIEW completion is gated by Row 7, so this is transitively protected. **Verify in the test.**
- Modify `_rule_tests_failing` (line 516): widen to match `(TEST == STATUS_FAILED) OR (TEST in ("pending", "ready") AND BUILD == STATUS_COMPLETED AND meta.get("pr_number"))`. Update the docstring AND the SKILL.md row 6 wording to keep the parity test green. Row 6's `state` cell currently reads "Tests failing" — change to "Tests failing or not yet run after build" (or similar — see Open Question 2).
- Add tests in `tests/unit/test_sdlc_router_decision.py` under a new `class TestTestPrecondition`:
  - `test_pr_exists_test_pending_returns_do_test`: PR exists, BUILD=completed, TEST=pending → expects Row 6 dispatch (`SKILL_DO_PATCH` … wait — Row 6 dispatches `/do-patch`. **See Open Question 1.**)
  - `test_pr_exists_test_completed_review_pending_returns_do_pr_review`: PR exists, TEST=completed, REVIEW=pending → expects Row 7 (`SKILL_DO_PR_REVIEW`).
  - `test_pr_exists_test_pending_blocks_pr_review`: PR exists, TEST=pending → does NOT return Row 7.

**Finding 2 — `/do-build` ticks plan checkboxes (~25 lines + helper):**

- New file: `scripts/tick_plan_checkboxes.py`. Pure-Python, no third-party deps. Reads the plan path argv-1, uses the canonical regex from `.claude/commands/do-merge.md:230-237` (`^#{1,3} (.+)` for headings, `^[ \t]*- \[ \] (.+)` for unchecked items), excludes sections in `EXCLUDED_SECTIONS = {"Open Questions", "Critique Results"}`, rewrites in-place. Idempotent. Returns the count of items ticked.
- New step in `.claude/skills/do-build/PR_AND_CLEANUP.md` after Step 6.3 (`Create Review Issues for Discrepancies`) and before Step 6.5 (`Pre-PR Commit Verification`): "Step 6.4 — Tick Off Plan Checkboxes". Runs the helper inside the worktree, commits as `docs(#{N}): tick off completed plan items`, pushes nothing (the existing Step 7 push handles it).
- Add a unit test `tests/unit/test_tick_plan_checkboxes.py` with table-driven cases: (a) ticks normal items, (b) skips `## Open Questions` items, (c) skips `## Critique Results` items, (d) idempotent (no-op on already-ticked plan), (e) preserves heading regex semantics (`#`, `##`, `###` only — `####` is NOT a section change).

**Finding 3 — Clean ruff baseline (~5 lines + auto-fixes):**

- Run `python -m ruff check --fix .` from repo root on a clean branch. Inspect the diff — must touch only the 5 listed files plus any other UP017 sites the issue body missed. (Issue body says "5 files affected"; verify with `ruff check . --output-format=concise | cut -d: -f1 | sort -u`.)
- Manually fix `worker/idle_sweeper.py:28` E501 — wrap the long docstring line at ~95 cols.
- Verify: `python -m ruff check .` → exit 0; `python -m ruff format --check .` → exit 0.
- Bundle into a single PR titled `chore(#1186): clean ruff baseline on main`.

**Bundling decision:** Three independent landings preferred per the issue's downstream constraint. Findings 1 + 2 land together (they're both SDLC workflow edits and their tests are colocated), Finding 3 lands as a separate `chore` PR (purely mechanical, no behavior change, smallest reviewer friction). **See Open Question 3.**

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No `except Exception: pass` blocks introduced. The new `tick_plan_checkboxes.py` raises on missing-file or read errors (no silent swallow).
- [ ] If the helper script encounters a malformed plan (no headings), it logs a warning and exits 0 — tested explicitly.

### Empty/Invalid Input Handling
- [ ] `tick_plan_checkboxes.py` on a missing plan path → exits 1 with stderr message.
- [ ] `tick_plan_checkboxes.py` on an empty plan file → exits 0, ticks nothing.
- [ ] `tick_plan_checkboxes.py` on a plan with zero `- [ ]` items → exits 0, ticks nothing, makes no changes.
- [ ] Router predicates: `stage_states={}` (the empty / unavailable case) is already handled by Row 10b. No new empty-state path.

### Error State Rendering
- [ ] If `tick_plan_checkboxes.py` fails inside `/do-build`, the build does NOT silently succeed — the helper's non-zero exit propagates to the orchestrator, which reports failure (mirrors Step 6.5's `COMMIT_COUNT == 0` abort pattern).

## Test Impact

- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: existing `class TestRow6TestsFailing` and `class TestRow7PrExistsNoReview` get new test methods. Existing assertions stay green (predicate logic is widened, not narrowed, for Row 6; Row 7 is narrowed to require TEST=completed).
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: row 6's `state` cell text changes (`"Tests failing"` → `"Tests failing or not yet run after build"` or similar). The parity test reads the markdown table; the SKILL.md edit and the parity test must agree.
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — VERIFY (no changes expected): G4 still applies to all stages including the widened Row 6.
- [ ] `tests/unit/test_tick_plan_checkboxes.py` — CREATE: new test file for Finding 2's helper script.
- [ ] No pytest run currently fails on main due to ruff fixes — the 5 affected files (`tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_valor_session_project_key.py`, `tests/unit/test_valor_session_working_dir_resolution.py`, `tools/sdlc_session_ensure.py`, `worker/idle_sweeper.py`) get only `datetime.UTC` import alias swaps (functionally identical) and one docstring line wrap (no logic). Behavior preserved.

No existing tests deleted. No `xfail` markers found related to these gaps (`grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` returned nothing in the router/build/ruff space).

## Rabbit Holes

- **Don't add a new DispatchRule row for "PR exists, TEST not completed"**. The issue body suggested a sibling rule but the AC says "No new dispatch rules." Predicate-only edits are the correct scope. Adding a row would also require updating `tests/unit/test_sdlc_skill_md_parity.py` canonical row list — exactly the kind of architectural creep the issue forbids.
- **Don't generalize `tick_plan_checkboxes.py` into a "plan ORM" or yaml-frontmatter mutator.** It's a 50-line text-processing script. If it grows beyond ~75 lines, the scope has slipped.
- **Don't refactor `worker/idle_sweeper.py` to fix the long line cosmetically.** Wrap the docstring at the natural sentence boundary; touching the function body is out of scope.
- **Don't migrate `.claude/skills/do-build/PR_AND_CLEANUP.md` to a different organization** while editing it. Add Step 6.4 in place; preserve all other content.
- **Don't fix other ruff warnings outside the 18 errors** even if they appear during the fix run. Stay scoped to UP017 + the one E501.

## Risks

### Risk 1: Widening `_rule_tests_failing` causes infinite loop after BUILD completes
**Impact:** If TEST stays `pending` after `/do-test` runs (e.g., `/do-test` crashes before writing the marker), Row 6 keeps dispatching `/do-patch`, hitting G4 oscillation after 3 dispatches.
**Mitigation:** This is the desired G4 behavior — visible escalation is better than silent skip. Add a unit test asserting G4 trips at `same_stage_dispatch_count >= 3` for the new precondition path. Document in `docs/sdlc/do-merge.md` that G4 escalation on Row 6 means `/do-test` is failing to write its marker — escalate to human.

### Risk 2: `tick_plan_checkboxes.py` ticks an item that wasn't actually delivered
**Impact:** A reviewer relying on plan checkbox state could be misled.
**Mitigation:** This is exactly the pre-existing risk the issue accepts. The plan completion gate already exists; this script is the symmetric writer. The actual safety net is `/do-pr-review`'s Step 4b (`code-review.md:207-228`) which assesses each `- [ ]` item against the diff regardless of checkbox state. Document this dual-layer in the helper's docstring. The script ticks ALL non-excluded items wholesale, which is consistent with the AC ("ticks all `- [ ]` checkboxes in the plan file *outside*…").

### Risk 3: ruff `--fix` touches unintended files
**Impact:** Scope creep on Finding 3's PR.
**Mitigation:** Run `ruff check --output-format=concise | cut -d: -f1 | sort -u` BEFORE `--fix` to confirm the file list. If it differs from the issue's 5-file list, expand the AC explicitly in the PR body before fixing — don't quietly broaden scope.

## Race Conditions

No race conditions identified. All three findings are synchronous, single-threaded, and operate on quiescent state:
- Finding 1: `decide_next_dispatch()` is a pure function with no shared mutable state.
- Finding 2: `tick_plan_checkboxes.py` runs once, in the worktree, between validators-passing and PR-creation. No concurrent writers.
- Finding 3: One-shot static analysis cleanup. No runtime path.

## No-Gos (Out of Scope)

- New `DispatchRule` rows. AC forbids it.
- Changes to `bridge/pipeline_state.py`. AC forbids it.
- Restructuring `docs/sdlc/`. AC forbids it.
- Adding new G-guards (G7, etc.). AC forbids it.
- Generalizing `tick_plan_checkboxes.py` to handle plan frontmatter, status fields, or task list IDs. Stay surgical.
- Fixing other lint/format issues uncovered during Finding 3. Strictly the 18 known errors.
- Changing the canonical exclusion list (`Open Questions`, `Critique Results`). The writer mirrors the gate; both must agree, and the gate is authoritative.
- Editing `.claude/commands/do-merge.md` plan completion gate. Read-only reference.

## Update System

No update system changes required — all three findings are purely in-repo. No new dependencies, no env vars, no config files. The `/update` skill propagates the next `git pull` automatically. The widened router predicates take effect on the next process start (worker / bridge restart per CLAUDE.md Development Principle #10).

## Agent Integration

No agent integration required. The agent already invokes `/do-build`, `/do-test`, `/do-pr-review`, `/do-merge` via the SDLC pipeline. No new MCP tools, no `.mcp.json` changes, no new bridge imports. The router is already on the agent's hot path; widened predicates flow through automatically. The new helper script `scripts/tick_plan_checkboxes.py` is invoked from inside the `/do-build` skill (a markdown skill file the agent reads) — not exposed as a top-level agent tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/plan-completion-gate.md` to document the new symmetric writer (Finding 2). Add a section "Auto-tick on /do-build" describing the new Step 6.4 and noting that `/do-pr-review`'s Step 4b remains the authoritative diff-vs-plan validator.
- [ ] No new feature doc needed for Finding 1 — it's a predicate-level fix to existing routing. Add a one-paragraph note in `docs/features/sdlc-pipeline-integrity.md` (if it exists; if not, in `docs/features/self-healing-merge-gate.md`) describing the TEST precondition.
- [ ] No feature doc for Finding 3 — pure mechanical chore.

### External Documentation Site
- [ ] No external doc site for this repo. Skip.

### Inline Documentation
- [ ] Update `_rule_pr_exists_no_review` and `_rule_tests_failing` docstrings in `agent/sdlc_router.py` to reflect the new TEST precondition / widened match.
- [ ] Update SKILL.md (`.claude/skills/sdlc/SKILL.md`) Row 6 wording to match the widened predicate (parity test enforces this).
- [ ] Add module docstring to `scripts/tick_plan_checkboxes.py` describing the canonical exclusion list and pointing to `.claude/commands/do-merge.md:223` as the source of truth.

## Success Criteria

- [ ] **Finding 1 AC**: `agent/sdlc_router.py` rejects `/do-pr-review` and `/do-merge` dispatches when `stage_states["TEST"] != "completed"`. Verified by new tests in `tests/unit/test_sdlc_router_decision.py::TestTestPrecondition` that assert: (a) PR exists + TEST=pending → does NOT return Row 7; (b) PR exists + TEST=completed + REVIEW=pending → returns Row 7; (c) PR exists + TEST=pending + BUILD=completed → returns Row 6 (`/do-test` via widened `_rule_tests_failing`, contingent on Open Question 1).
- [ ] **Finding 2 AC**: `.claude/skills/do-build/PR_AND_CLEANUP.md` has an explicit Step 6.4 that runs `scripts/tick_plan_checkboxes.py` against the plan, committed as `docs(#1186): tick off completed plan items` before pre-PR verification. Verified by running `/do-build` against a freshly-created plan and observing 0 unchecked items in the post-build commit (excluding `## Open Questions` and `## Critique Results`).
- [ ] **Finding 3 AC**: `python -m ruff check .` exits 0 on the new tip of `main`. Single PR touches only the listed files (`tests/unit/test_sdlc_session_ensure.py`, `tests/unit/test_valor_session_project_key.py`, `tests/unit/test_valor_session_working_dir_resolution.py`, `tools/sdlc_session_ensure.py`, `worker/idle_sweeper.py`).
- [ ] **Scope guard**: No new gates, no new `DispatchRule` rows, no `bridge/pipeline_state.py` changes, no `docs/sdlc/` restructure. Verified by `git diff --stat main...HEAD` showing only edits in `agent/sdlc_router.py`, `.claude/skills/sdlc/SKILL.md`, `.claude/skills/do-build/PR_AND_CLEANUP.md`, `scripts/tick_plan_checkboxes.py` (new), `tests/unit/test_sdlc_router_decision.py`, `tests/unit/test_tick_plan_checkboxes.py` (new), and the 5 ruff files.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Plan completion gate passes on this plan's PR (i.e., `/do-build` ticks this plan's checkboxes — eat-our-own-dogfood verification of Finding 2).

## Team Orchestration

### Team Members

- **Builder (router)**
  - Name: `router-builder`
  - Role: Edit `agent/sdlc_router.py` predicates and `.claude/skills/sdlc/SKILL.md` Row 6 wording.
  - Agent Type: builder
  - Resume: true

- **Builder (build-tick)**
  - Name: `build-tick-builder`
  - Role: Create `scripts/tick_plan_checkboxes.py`, edit `.claude/skills/do-build/PR_AND_CLEANUP.md` Step 6.4.
  - Agent Type: builder
  - Resume: true

- **Builder (ruff-baseline)**
  - Name: `ruff-baseline-builder`
  - Role: Run `ruff check --fix .`, manually wrap `worker/idle_sweeper.py:28`, verify exit 0.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `test-writer`
  - Role: Add tests for Findings 1 and 2 (`tests/unit/test_sdlc_router_decision.py::TestTestPrecondition`, `tests/unit/test_tick_plan_checkboxes.py`). Verify `tests/unit/test_sdlc_skill_md_parity.py` stays green.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `final-validator`
  - Role: Run all `## Verification` checks; confirm scope guard.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Finding 3 — Ruff Baseline (independent, ship first)
- **Task ID**: build-ruff-baseline
- **Depends On**: none
- **Validates**: `python -m ruff check .` exit 0; `python -m ruff format --check .` exit 0
- **Informed By**: Freshness Check (18 errors confirmed on `4266ecc9`)
- **Assigned To**: ruff-baseline-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `python -m ruff check --output-format=concise . | cut -d: -f1 | sort -u > /tmp/ruff_files.txt` to capture the affected file list. Confirm it matches the 5 files in the issue body. If it differs, STOP and surface the discrepancy.
- Run `python -m ruff check --fix .` to apply 17 UP017 auto-fixes.
- Manually wrap `worker/idle_sweeper.py:28` — split the long docstring sentence at a natural boundary, keeping the description faithful.
- Verify `python -m ruff check .` exits 0.
- Verify `python -m ruff format --check .` exits 0.
- Commit on `session/sdlc-workflow-three-gaps-ruff` (or shared branch — see Open Question 3): `chore(#1186): clean ruff baseline on main (UP017 + E501)`.

### 2. Finding 1 — Router TEST Precondition
- **Task ID**: build-router-test-precondition
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_router_decision.py`, `tests/unit/test_sdlc_skill_md_parity.py`
- **Informed By**: Freshness Check (predicates verified at lines 516, 521, 569, 583); Open Question 1 (resolved before build)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `agent/sdlc_router.py::_rule_pr_exists_no_review`: add `if stage_states.get("TEST") != STATUS_COMPLETED: return False` as the first condition AFTER the `pr_number` check. Update docstring to "PR exists, TEST completed, no review."
- Edit `agent/sdlc_router.py::_rule_tests_failing`: widen to `STATUS_FAILED OR (TEST in ("pending", "ready") AND BUILD == STATUS_COMPLETED AND meta.get("pr_number"))`. Update docstring to match.
- Edit `.claude/skills/sdlc/SKILL.md` Row 6 `state` cell to mirror the new docstring (parity test enforces this).
- Verify `_rule_review_approved_docs_not_done` is transitively protected (REVIEW completion requires Row 7 to fire, which now requires TEST=completed). Add inline comment confirming the chain.

### 3. Finding 2 — `/do-build` Ticks Plan Checkboxes
- **Task ID**: build-tick-plan-checkboxes
- **Depends On**: none
- **Validates**: `tests/unit/test_tick_plan_checkboxes.py`
- **Informed By**: `.claude/commands/do-merge.md:223` (canonical exclusion list); `.claude/skills/do-pr-review/sub-skills/code-review.md:207-228` (existing checkbox validator)
- **Assigned To**: build-tick-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/tick_plan_checkboxes.py`: pure-Python script taking a plan path, exclusion-aware (`Open Questions`, `Critique Results`), idempotent, writes in-place. Module docstring points to `.claude/commands/do-merge.md:223` as source of truth for the exclusion list.
- Edit `.claude/skills/do-build/PR_AND_CLEANUP.md`: insert a new "Step 6.4 — Tick Off Plan Checkboxes" between Step 6.3 and Step 6.5. The step runs the helper inside the worktree and commits on the session branch as `docs(#{N}): tick off completed plan items`. Issue number is sourced from the plan's `tracking:` frontmatter (or from the build context).
- Verify Step 6.5's pre-PR commit verification still detects the new commit (it counts `main..HEAD` commits — should now show one extra).

### 4. Tests for Findings 1 and 2
- **Task ID**: write-tests
- **Depends On**: build-router-test-precondition, build-tick-plan-checkboxes
- **Validates**: `pytest tests/unit/test_sdlc_router_decision.py tests/unit/test_tick_plan_checkboxes.py tests/unit/test_sdlc_skill_md_parity.py`
- **Assigned To**: test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `class TestTestPrecondition` to `tests/unit/test_sdlc_router_decision.py` with three test methods covering the AC matrix (PR + TEST=pending, PR + TEST=completed, PR + TEST=pending + BUILD=completed).
- Create `tests/unit/test_tick_plan_checkboxes.py` with table-driven tests: normal items ticked, Open Questions skipped, Critique Results skipped, idempotent on already-ticked plan, malformed plan handled gracefully.
- Run parity test (`tests/unit/test_sdlc_skill_md_parity.py`) — must pass after SKILL.md row 6 edit.

### 5. Documentation
- **Task ID**: document-changes
- **Depends On**: build-router-test-precondition, build-tick-plan-checkboxes
- **Assigned To**: build-tick-builder (lightweight, no specialist needed)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/plan-completion-gate.md` with the new "Auto-tick on /do-build" section.
- Add TEST precondition note to `docs/features/sdlc-pipeline-integrity.md` (or `self-healing-merge-gate.md`).
- Verify `docs/features/README.md` index is unchanged (no new feature pages).

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: write-tests, document-changes
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all rows in `## Verification` table.
- Confirm scope guard: `git diff --stat main...HEAD` shows only the expected files.
- Confirm Findings 1, 2, 3 ACs all check.
- Confirm parity test green.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_router_decision.py tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_skill_md_parity.py tests/unit/test_tick_plan_checkboxes.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| Plan completion gate passes on this plan | `python scripts/tick_plan_checkboxes.py docs/plans/sdlc-workflow-three-gaps.md && ! grep -E '^[ \t]*- \[ \] ' docs/plans/sdlc-workflow-three-gaps.md \| grep -v -A0 -B999 'Open Questions\\|Critique Results'` | exit code 0 |
| Scope guard — only expected files touched | `git diff --name-only main...HEAD \| sort` | output contains only: `agent/sdlc_router.py`, `.claude/skills/sdlc/SKILL.md`, `.claude/skills/do-build/PR_AND_CLEANUP.md`, `scripts/tick_plan_checkboxes.py`, `tests/unit/test_sdlc_router_decision.py`, `tests/unit/test_tick_plan_checkboxes.py`, `docs/features/plan-completion-gate.md`, `docs/features/sdlc-pipeline-integrity.md`, plus the 5 ruff files |
| Helper script idempotent | `python scripts/tick_plan_checkboxes.py docs/plans/sdlc-workflow-three-gaps.md && python scripts/tick_plan_checkboxes.py docs/plans/sdlc-workflow-three-gaps.md` | exit code 0 both runs, second run reports 0 ticks |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Row 6 dispatch on TEST=pending**: Today, Row 6 (`_rule_tests_failing`) dispatches `/do-patch` on `TEST == STATUS_FAILED`. The Solution Sketch widens it to also fire on `TEST in ("pending", "ready") AND BUILD == STATUS_COMPLETED AND PR exists`. But `/do-patch` is for fixing failures — dispatching it on `pending` is semantically wrong. The right skill for "TEST never ran" is `/do-test`. **Question:** Should we (a) widen Row 6 to dispatch `/do-test` on the pending-after-build case (changes Row 6's skill mapping — touches the parity test more invasively), or (b) widen Row 6 to keep `/do-patch` but rely on `/do-patch`'s own dispatch logic to invoke `/do-test` (clean for parity test, but routes through patch unnecessarily), or (c) accept that the AC's "no new dispatch rules" prohibition can't be honored cleanly and add a minimal Row 6.5 (`/do-test` for pending-after-build) — which requires adding it to SKILL.md, the router, and the parity test's `expected = [...]` list? **My recommendation: (a)** — Row 6's `state` cell becomes "TEST not completed (failed or never ran)" and `skill` becomes context-dependent (`/do-patch` if FAILED, `/do-test` if pending). This requires changing `DispatchRule.skill` from a static string to a callable for Row 6 specifically — a small structural change but contained to one row.

2. **SKILL.md Row 6 wording**: After resolving Question 1, what's the canonical wording for Row 6's `state` and `skill` cells in `.claude/skills/sdlc/SKILL.md`? The parity test reads these literally, so the wording must be both human-readable and stable. Suggested: state="Tests not completed (failed or pending after build)", skill="`/do-test` if pending, `/do-patch` if failed".

3. **PR bundling**: Issue body says "Findings 1 and 3 are pure mechanical fixes; Finding 2 needs the most thought… can ship as one PR or three; prefer whichever produces less reviewer friction." My read: Finding 3 ships separately (purely mechanical, zero behavior change, easiest to review and revert). Findings 1 and 2 ship together (both touch SDLC workflow and have intertwined tests). **Confirm preferred bundling** before `/do-build` creates branches.

4. **Eat-our-own-dogfood**: Finding 2's verification includes "ticking this plan's checkboxes when /do-build runs." But this plan's `## Open Questions` section will (correctly) NOT be ticked. Confirm that the success criterion "0 unchecked items in the post-build commit (excluding the two reserved sections)" is what we want — i.e., the plan PR itself will still have unchecked items in `## Open Questions` after `/do-build`, and that's fine.
