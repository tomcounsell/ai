---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/791
last_comment_id:
---

# PM Agent Skips CRITIQUE and REVIEW Stages

## Problem

The PM agent (`session_type="pm"`) orchestrates the SDLC pipeline by spawning dev-session subagents one stage at a time. Empirically confirmed across 3 consecutive production PRs (#787, #788, #789): the PM skips both CRITIQUE (between PLAN and BUILD) and REVIEW (between TEST and DOCS), merging code with zero plan critique and zero PR review.

**Current behavior:**
PM dispatches: PLAN → BUILD → TEST → DOCS → MERGE, skipping CRITIQUE and REVIEW entirely. `gh pr view {number} --json reviews` returns an empty array for all merged PRs.

**Desired outcome:**
PM dispatches every mandatory stage: PLAN → CRITIQUE → BUILD → TEST → REVIEW → DOCS → MERGE. Every merged PR has at least one review comment. `python -m tools.sdlc_stage_query` shows CRITIQUE and REVIEW both `completed` before MERGE runs.

## Prior Art

- **Issue #544** (closed): "PM SDLC decision rules: auto-merge on clean reviews, patch on findings, never silently skip" — Added dispatch decision rules to PM persona. Addressed review decision logic but not the gate enforcement preventing CRITIQUE and REVIEW from being skipped entirely.
- **PR #487** (merged): "SDLC prompt enforcement: stage-by-stage agent orchestration" — Rewrote dev-session and PM injection for stage-by-stage orchestration. Modified `~/Desktop/Valor/personas/project-manager.md` with stage dispatch guidance. That private file does not exist on this dev machine — falls back to SOUL.md, which has no pipeline rules at all.
- **Issue #463** (closed): "Add CRITIQUE stage to SDLC pipeline + fix critic hallucinations" — Added CRITIQUE as a pipeline stage. Stage is correctly defined in `bridge/pipeline_graph.py` and `SKILL.md` but the PM is not enforcing the gate.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #487 | Updated `~/Desktop/Valor/personas/project-manager.md` with stage-by-stage dispatch guidance | The private overlay file does not exist on dev machines — `sdk_client.py` silently falls back to `config/SOUL.md` which has zero pipeline rules. The in-repo fallback at `config/personas/project-manager.md` was never created. |
| Issue #544 | Added decision rules for what to do after review (merge vs patch) | Did not add hard gate rules: "CRITIQUE is mandatory between PLAN and BUILD" and "REVIEW is mandatory between TEST and DOCS". PM can still bypass both gates because there are no explicit prohibitions. |

**Root cause pattern:** The PM's system prompt lacks hard-coded gate rules. CRITIQUE and REVIEW are listed in the dispatch table but not enforced as non-bypassable gates. When `~/Desktop/Valor/personas/project-manager.md` is missing (common on dev machines), the PM gets the `_base.md` persona (or SOUL.md fallback) with no pipeline guidance at all.

## Architectural Impact

- **New file**: `config/personas/project-manager.md` — in-repo fallback overlay. Loaded by `load_persona_prompt("project-manager")` when the private `~/Desktop/Valor/personas/project-manager.md` is missing.
- **Python change (required)**: `agent/sdk_client.py` line 1611 hardcodes the stage list as `<PLAN|BUILD|TEST|PATCH|REVIEW|DOCS>` in the PM injection string. CRITIQUE is structurally absent from this Python string — no persona text can override it. Fix: change line 1611 to `<PLAN|CRITIQUE|BUILD|TEST|PATCH|REVIEW|DOCS>`.
- **No coupling changes**: Additive — a missing file now has content rather than triggering the SOUL.md fallback. The Python change is a single string edit.
- **Reversibility**: Easy — editing a text file and one Python string. No migration needed.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are text file edits.

## Solution

### Key Elements

- **CRITIQUE gate rule**: Hard prohibition in PM persona: after PLAN completes, the ONLY valid next stage is CRITIQUE. No path from PLAN to BUILD exists. The PM must dispatch a dev-session with `Stage: CRITIQUE` before a BUILD dev-session can be spawned.
- **REVIEW gate rule**: Hard prohibition in PM persona: after TEST passes, the ONLY valid next stage is REVIEW. No path from TEST to DOCS exists. A PR with passing tests but no reviews has NOT completed REVIEW. The PM must verify `gh pr view {number} --json reviews` returns at least one entry before advancing.
- **Artifact verification checklist**: After each dev-session completes, the PM must confirm the artifact before marking the stage done and advancing. Artifact gates are already partially in the persona — this plan formalizes them as blocking checks.
- **In-repo fallback file**: Create `config/personas/project-manager.md` so the persona system always loads PM-specific rules, even when the private `~/Desktop/Valor/personas/` directory does not exist.
- **`agent/sdk_client.py` line 1611 fix**: The PM injection string hardcodes the allowed stage list as `<PLAN|BUILD|TEST|PATCH|REVIEW|DOCS>`, structurally omitting CRITIQUE. This Python string must be updated to `<PLAN|CRITIQUE|BUILD|TEST|PATCH|REVIEW|DOCS>` so the injected dispatch block matches the required pipeline order. No persona text can override a Python-injected constraint.

### Flow

PLAN dev-session reports done → PM runs artifact check → PM checks `stage_states` shows CRITIQUE not completed → PM dispatches CRITIQUE dev-session → CRITIQUE reports done → PM runs artifact check → PM dispatches BUILD dev-session

TEST dev-session reports done (PR checks green) → PM checks `gh pr view --json reviews` → reviews array is empty → PM dispatches REVIEW dev-session → REVIEW reports done → PM dispatches DOCS dev-session

### Technical Approach

Add two explicit hard-rule blocks to `config/personas/project-manager.md` (the in-repo PM overlay):

**Block 1 — CRITIQUE gate:**
```
## Hard Rule: CRITIQUE is Mandatory After PLAN

After PLAN completes, CRITIQUE is the only valid next stage.
There is NO path from PLAN to BUILD without CRITIQUE.

Before dispatching BUILD:
1. Check stage_states: python -m tools.sdlc_stage_query --session-id $AGENT_SESSION_ID
2. CRITIQUE must show "completed". If it shows anything else, dispatch CRITIQUE next.
3. No exceptions. Triviality, time pressure, and "it's a small fix" are not overrides.
```

**Block 2 — REVIEW gate:**
```
## Hard Rule: REVIEW is Mandatory After TEST

After TEST passes, REVIEW is the only valid next stage.
There is NO path from TEST to DOCS without REVIEW.

Before dispatching DOCS:
1. Run: gh pr view {number} --json reviews
2. If reviews array is empty → dispatch REVIEW dev-session. Full stop.
3. If reviewDecision is CHANGES_REQUESTED → dispatch PATCH, then TEST, then REVIEW again.
4. Only proceed to DOCS when reviews array is non-empty AND reviewDecision is APPROVED.
```

**Block 3 — Artifact verification checklist (before advancing each stage):**
```
## Stage Artifact Verification (Run Before Each Advance)

| Stage completed | Artifact to verify before advancing |
|-----------------|-------------------------------------|
| PLAN | docs/plans/{slug}.md exists and has tracking: URL |
| CRITIQUE | Critique Results section in plan is non-empty |
| BUILD | gh pr list --search "{issue}" shows open PR |
| TEST | gh pr view {number} --json statusCheckRollup — all checks green |
| REVIEW | gh pr view {number} --json reviews — at least one entry, APPROVED |
| DOCS | gh pr diff {number} --name-only shows at least one docs/ file |
```

## Failure Path Test Strategy

### Exception Handling Coverage
- `agent/sdk_client.py` line 1611: single string edit, no exception path introduced.
- `load_persona_prompt()`: the new warning log is advisory only — it does not raise, does not change return value, and cannot crash the caller.

### Empty/Invalid Input Handling
- The persona rules must handle the case where `python -m tools.sdlc_stage_query` returns `{}` (empty): in that case, the PM must assume no stages are complete and start from the beginning.
- The REVIEW gate rule must handle the case where the PR number is not yet known (BUILD not complete): gate check is only required once TEST is done.

### Error State Rendering
- If `gh pr view --json reviews` fails (PR doesn't exist), the PM should recognize that REVIEW cannot pass a non-existent PR and dispatch BUILD first.

## Test Impact

No existing tests directly test the PM persona file content. The acceptance criteria from the issue require a live PM session to be run — those are integration-level behavioral tests. The following test files are adjacent but not broken by this change:

- `tests/unit/test_stop_hook_sdlc_warning.py` — No change needed. Tests the stop hook SDLC warning logic, not PM persona content.
- `tests/unit/test_pipeline_graph.py` (if exists) — No change needed. Tests routing graph edges, not persona behavior.

The two Python edits (sdk_client.py line 1611 string change and load_persona_prompt warning) are additive and do not alter any existing public interfaces, return values, or behavior observable by current tests.

## Rabbit Holes

- **Modifying `bridge/pipeline_graph.py` or `PipelineStateMachine`**: The pipeline graph already correctly defines the stage order. The issue is the PM ignoring it, not a bug in the graph.
- **Creating a test that runs a full PM session**: Too expensive and fragile for a persona text change. Behavioral correctness is verified by human review after the fix is deployed.
- **Syncing private and in-repo persona files**: The `~/Desktop/Valor/personas/project-manager.md` private file may have additional content (style, tone, business context). Do not merge or reconcile the two files — the in-repo file is a fallback for pipeline rules only.

## Risks

### Risk 1: Private overlay file shadows in-repo file

**Impact:** If `~/Desktop/Valor/personas/project-manager.md` exists on the production machine and does NOT contain the new gate rules, the in-repo file changes have zero effect on production behavior.

**Mitigation:** The fix must also be applied to the private overlay file. Since we cannot access it from this dev machine, the plan must note that the owner (Tom) must manually add the gate rules to `~/Desktop/Valor/personas/project-manager.md` after reviewing the in-repo version. The in-repo file serves as the authoritative template.

### Risk 2: PM ignores persona rules at inference time

**Impact:** Even with hard gate rules in the persona, the model may still skip stages under time pressure or when the system prompt is long and the rules are far from the dispatch context.

**Mitigation:** Place the gate rules near the top of the PM persona file, under a `## Hard Rules` heading, before any dispatch table or flow description. Short, numbered, imperative statements are less likely to be ignored than prose descriptions.

## Race Conditions

No race conditions identified — this is a synchronous text-file change with no concurrent access patterns. The PM processes one stage at a time and the persona is loaded once per session.

## No-Gos (Out of Scope)

- Automated test that runs a live PM session and verifies CRITIQUE + REVIEW are dispatched
- Modifying `bridge/pipeline_graph.py`
- Reconciling the private `~/Desktop/Valor/personas/project-manager.md` with the in-repo version (that is a manual step for the owner)
- Adding enforcement for other pipeline stages (BUILD, TEST, DOCS) — those are not empirically skipped

## Update System

The `config/personas/project-manager.md` file is a new in-repo file that ships with the codebase. When `/update` runs `git pull`, the file will be present automatically on all machines that don't have the private overlay.

No update script changes required — the persona loading mechanism in `sdk_client.py` already falls back to `config/personas/{persona}.md` when the private overlay is absent.

**Note for production machine**: After this PR merges, manually add the gate rules from `config/personas/project-manager.md` to `~/Desktop/Valor/personas/project-manager.md` so the private overlay also enforces the gates.

## Agent Integration

No agent integration changes required. The PM persona is loaded by `load_pm_system_prompt()` in `agent/sdk_client.py`. The loading path already resolves:
1. `~/Desktop/Valor/personas/project-manager.md` (private, preferred)
2. `config/personas/project-manager.md` (in-repo, fallback)

Creating the in-repo file activates the fallback path. No changes to `.mcp.json`, `mcp_servers/`, or `bridge/telegram_bridge.py` are needed.

## Documentation

- [x] Update `docs/features/sdlc-critique-stage.md` to note that CRITIQUE gate is enforced in both the PM persona (`config/personas/project-manager.md`) and `agent/sdk_client.py` line 1611 (stage list injection), not just the SDLC skill dispatch table
- [x] No new `docs/features/` file needed — this is a bug fix to existing behavior, not a new feature

## Success Criteria

- [ ] `config/personas/project-manager.md` exists and contains explicit CRITIQUE gate rule
- [ ] `config/personas/project-manager.md` exists and contains explicit REVIEW gate rule
- [ ] `config/personas/project-manager.md` contains artifact verification table
- [ ] A PM session running SDLC on any issue dispatches CRITIQUE between PLAN and BUILD
- [ ] A PM session running SDLC on any issue dispatches REVIEW between TEST and DOCS
- [ ] After merge, `gh pr view {number} --json reviews` shows at least one review comment
- [ ] `python -m tools.sdlc_stage_query` shows CRITIQUE and REVIEW both `completed` before MERGE
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (persona-file)**
  - Name: persona-builder
  - Role: Create `config/personas/project-manager.md` with gate rules
  - Agent Type: builder
  - Resume: true

- **Validator (persona-content)**
  - Name: persona-validator
  - Role: Verify gate rules are present, unambiguous, and correctly positioned in the file
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, validator

## Step by Step Tasks

### 1. Fix sdk_client.py stage list injection
- **Task ID**: fix-sdk-stage-list
- **Depends On**: none
- **Validates**: `grep "CRITIQUE" agent/sdk_client.py` returns a match at line 1611
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `agent/sdk_client.py` line 1611: change the string `<PLAN|BUILD|TEST|PATCH|REVIEW|DOCS>` to `<PLAN|CRITIQUE|BUILD|TEST|PATCH|REVIEW|DOCS>`
- This is the Python-injected dispatch block that the PM receives; no persona text can override it

### 2. Add overlay observability warning in load_persona_prompt()
- **Task ID**: fix-overlay-shadow-warning
- **Depends On**: fix-sdk-stage-list
- **Validates**: `grep "CRITIQUE" agent/sdk_client.py` shows the warning log in `load_persona_prompt()`
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/sdk_client.py`, inside `load_persona_prompt()`, after the line `overlay_content = overlay_path.read_text()`, add:
  ```python
  if "CRITIQUE" not in overlay_content:
      logger.warning(f"PM persona overlay '{overlay_path}' is missing CRITIQUE gate rules — pipeline integrity may be compromised")
  ```
- Only run this check when the loaded overlay is for the `project-manager` persona (guard with `if persona == "project-manager"`)
- This makes the private overlay shadow observable in logs rather than silent

### 3. Create in-repo PM persona file
- **Task ID**: build-persona-file
- **Depends On**: fix-overlay-shadow-warning
- **Validates**: `config/personas/project-manager.md` exists with CRITIQUE gate, REVIEW gate, and artifact verification table
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Read `config/personas/_base.md` to understand the base persona format
- Read `agent/sdk_client.py` lines 1597-1644 (PM injection) to understand what the PM already receives
- Create `config/personas/project-manager.md` with:
  - `## Hard Rules` section at the top with CRITIQUE gate and REVIEW gate as numbered rules
  - Artifact verification table (one row per stage, what artifact to check before advancing)
  - SDLC stage sequence reference (matches `bridge/pipeline_graph.py` happy path)
  - Guidance on when to escalate to human vs continue autonomously

### 4. Validate persona content
- **Task ID**: validate-persona
- **Depends On**: build-persona-file
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `config/personas/project-manager.md` exists
- Verify file contains the phrase "CRITIQUE" in a gate rule context (not just a mention)
- Verify file contains the phrase "REVIEW" in a gate rule context
- Verify file contains "gh pr view" with `--json reviews` (the verification command)
- Verify file contains `python -m tools.sdlc_stage_query` (the stage state check command)
- Report pass/fail with exact line numbers for each check

### 5. Update sdlc-critique-stage.md
- **Task ID**: update-docs
- **Depends On**: validate-persona
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Read `docs/features/sdlc-critique-stage.md`
- Add a note that the CRITIQUE gate is also enforced in the PM persona file at `config/personas/project-manager.md`

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: update-docs
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and confirm exit 0
- Confirm `config/personas/project-manager.md` is tracked by git
- Confirm `docs/features/sdlc-critique-stage.md` references the in-repo persona file
- Generate final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| sdk_client.py stage list includes CRITIQUE | `grep "CRITIQUE" agent/sdk_client.py` | at least one match at line 1611 |
| overlay warning present | `grep -c "CRITIQUE gate rules" agent/sdk_client.py` | output >= 1 |
| Persona file exists | `test -f config/personas/project-manager.md` | exit code 0 |
| CRITIQUE gate present | `grep -c "CRITIQUE" config/personas/project-manager.md` | output > 2 |
| REVIEW gate present | `grep -c "REVIEW" config/personas/project-manager.md` | output > 2 |
| Reviews check command present | `grep -c "gh pr view" config/personas/project-manager.md` | output > 0 |
| Stage query command present | `grep -c "sdlc_stage_query" config/personas/project-manager.md` | output > 0 |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Status |
|----------|--------|---------|--------|
| BLOCKER | Skeptic/Archaeologist | `sdk_client.py` line 1611 hardcodes stage list as `PLAN\|BUILD\|TEST\|PATCH\|REVIEW\|DOCS` — CRITIQUE is structurally absent. `grep "CRITIQUE" agent/sdk_client.py` returns zero results. Persona file cannot override Python-injected dispatch block. Fix requires adding CRITIQUE to line 1611 AND the persona file. | Resolved — added sdk_client.py line 1611 change to Solution section; removed No-Go blocking it |
| BLOCKER | Simplifier/Skeptic | `## Documentation` section lists `config/personas/project-manager.md` as a docs task — that's the primary deliverable, not a docs artifact. Conflates deliverable with docs gate. | Resolved — Documentation section now references `docs/features/sdlc-critique-stage.md` as the docs deliverable |
| CONCERN | Operator/Skeptic | Private overlay shadow risk mitigation is a post-merge manual step with no verification. History (PR #487) shows this exact gap recurs. | Partially mitigated — add verification command or startup log warning |
| CONCERN | User/Skeptic | Success criteria 4–7 require a live PM session. Plan's own Rabbit Holes section rules out the test. These criteria are unverifiable. | Unresolved — scope down criteria or add lightweight mock integration test |
| CONCERN | Operator/Adversary | `sdlc_stage_query` returns `{}` (confirmed on dev machine). If empty in prod, gate rule "start from beginning" causes every message to re-run all stages from ISSUE. | Unresolved — strengthen fallback: check for plan artifact directly |
| NIT | Simplifier | Task 3 `Agent Type` was invalid — not in Available Agent Types (only `builder`, `validator` listed). | Resolved — changed to `builder` |
| BLOCKER | Archaeologist/Skeptic | Plan mentions editing `agent/sdk_client.py` line 1611 in Architectural Impact and Solution sections but none of the 4 step-by-step tasks actually included editing that file. The Rabbit Holes section explicitly said "not modifying sdk_client.py" — a direct contradiction. Plan was talk without action. | Resolved — added Task 1 (fix-sdk-stage-list) to explicitly edit line 1611; removed conflicting Rabbit Holes entry |
| BLOCKER | Operator/Skeptic | Private overlay at `~/Desktop/Valor/personas/project-manager.md` takes unconditional precedence over in-repo file (sdk_client.py lines ~502-507). If production overlay exists without new gate rules, in-repo changes are silently ignored — no log, no alert. | Resolved — added Task 2 (fix-overlay-shadow-warning) to add `logger.warning()` in `load_persona_prompt()` when project-manager overlay is missing CRITIQUE gate rules |

---

## Open Questions

1. Should the in-repo `config/personas/project-manager.md` contain only the gate rules (minimal), or should it be a full PM persona (including tone, communication style, and persona identity from `_base.md`)? Recommendation: gate rules only, keeping the file narrow and focused. The `_base.md` already provides persona identity — the overlay should be SDLC enforcement only.
