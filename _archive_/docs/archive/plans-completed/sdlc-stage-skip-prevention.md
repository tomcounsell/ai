---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/729
last_comment_id:
---

# SDLC Stage Skip Prevention

## Problem

During issue #723, the SDLC router skipped the `/do-docs` cascade entirely. After `/do-pr-review` approved the PR, the router saw "review approved + docs files exist in the PR diff" and concluded all stages were complete. The `/do-build` stage had created `docs/features/session-recovery-mechanisms.md` as a deliverable, which the docs check interpreted as "docs done." But `/do-docs` -- which greps all docs for stale references, checks downstream issues, and runs the semantic impact finder -- was never invoked.

**Current behavior:** Router's fallback path infers "docs done" from `docs/` files in the PR diff, skipping `/do-docs`

**Desired outcome:** Router only considers a stage done if it was explicitly dispatched and completed, both in the primary (stage_states) and fallback (local Claude Code) paths

## Prior Art

- **Issue #704** (closed): "SDLC router must use PipelineStateMachine for stage tracking instead of artifact inference" -- same core problem. PR #722 wired stage_states as primary signal but left the fallback inference path unchanged.
- **PR #722** (merged 2026-04-05): "Wire SDLC router to read stage_states as primary signal" -- added Step 2.0 to SKILL.md querying stage_states. Did not fix the artifact inference fallback or the `get_display_progress(slug=...)` merging behavior.
- **PR #490** (merged 2026-03-24): "Consolidate SDLC stage tracking, remove legacy fields" -- deleted `tools/session_progress.py` but left 5 dangling references in skills (do-docs, do-pr-review).
- **PR #494** (merged 2026-03-23): "Wire PipelineStateMachine.start_stage() into SDLC dispatch" -- wired bridge hooks (`pre_tool_use.py`, `subagent_stop.py`) to call start_stage/complete_stage, but did not add equivalent markers to skills.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #722 | Added stage_states as primary signal in SDLC router | Left fallback path (Steps 2a-2e) using artifact inference untouched. Local Claude Code invocations still skip stages. |
| PR #490 | Deleted session_progress.py | Left 5 skill references to the deleted tool. Skills silently fail to write stage markers. |
| PR #494 | Wired hooks to call start_stage/complete_stage | Only works for bridge-initiated sessions (hooks fire on Agent tool use). Skills themselves don't write markers. |

**Root cause pattern:** Each fix addressed one layer (router primary path, hook wiring, tool cleanup) without closing the full loop. The artifact inference path remains the de facto fallback and can always override explicit stage tracking when artifacts happen to exist.

## Data Flow

How stage completion flows through the system today, and where the breaks are:

1. **PM Session** invokes `/sdlc` which dispatches a sub-skill (e.g., `/do-build`)
2. **pre_tool_use hook** detects dev-session Agent tool use, extracts stage name from prompt, calls `PipelineStateMachine.start_stage()` on parent session -- **this works**
3. **Dev-session** runs the skill (e.g., `/do-build` creates code, PR, and docs)
4. **subagent_stop hook** fires on dev-session completion, calls `classify_outcome()` then `complete_stage()` or `fail_stage()` -- **this works**
5. **PM Session** re-invokes `/sdlc` which queries `stage_states` (Step 2.0) -- **this works when hooks fired**
6. **Fallback path** (local Claude Code, no hooks): `/sdlc` runs Steps 2a-2e artifact inference. `_infer_stage_from_artifacts()` sees docs/ files in PR and infers DOCS=completed -- **THIS IS WHERE IT BREAKS**
7. **do-merge gate** calls `get_display_progress(slug=...)` which merges artifact inference INTO stored state, filling gaps in stages that were never dispatched -- **SECOND BREAK POINT**

## Architectural Impact

- **Interface changes**: `get_display_progress()` simplified — returns stored state only, `_infer_stage_from_artifacts()` deleted
- **Coupling**: Eliminates coupling between artifact existence and stage completion status entirely
- **Data ownership**: Stage completion status exclusively owned by PipelineStateMachine (stored state)
- **Reversibility**: Fully reversible — restore the deleted function from git history if needed

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (plan review)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work modifies existing internal components with no external dependencies.

## Solution

### Key Elements

- **Remove artifact inference entirely**: Delete `_infer_stage_from_artifacts()` and all callers from `get_display_progress()`. Stage completion is exclusively determined by stored state (PipelineStateMachine). No `for_routing` parameter — just remove the inference code. Rewrite tests accordingly.
- **Replace dangling session_progress calls with PipelineStateMachine CLI**: Create a lightweight CLI tool that skills can call to write stage markers, replacing the deleted `tools/session_progress.py`
- **Strengthen do-merge gate**: Show very strong warnings/reminders when stages are skipped. Not a hard blocker — the agent can override for emergency hotfixes, but only after explicit acknowledgment of what was skipped.

### Flow

**PM invokes /sdlc** -> Router checks stage_states (primary) or dispatch history (fallback) -> Dispatches next skill -> Skill writes stage markers via CLI -> subagent_stop records completion -> PM re-invokes /sdlc

### Technical Approach

#### Fix 1: Remove artifact inference from get_display_progress entirely

`get_display_progress(slug=...)` currently calls `_infer_stage_from_artifacts()` and merges inferred state into gaps. This doesn't work in practice — rip it out completely.

Concretely:
- Delete `_infer_stage_from_artifacts()` function from `bridge/pipeline_state.py`
- Remove the merge logic in `get_display_progress()` that calls it
- `get_display_progress()` returns stored state only — no inference, no `for_routing` parameter
- Rewrite all tests that relied on artifact inference to use stored state instead

#### Fix 2: Create `tools/sdlc_stage_marker.py` CLI tool

A CLI tool that skills invoke to write stage markers to the PipelineStateMachine. This replaces the deleted `tools/session_progress.py`:

```bash
python -m tools.sdlc_stage_marker --stage DOCS --status in_progress
python -m tools.sdlc_stage_marker --stage DOCS --status completed
```

The tool resolves the session from `VALOR_SESSION_ID` or `AGENT_SESSION_ID` environment variables, loads the PipelineStateMachine, and calls `start_stage()` or `complete_stage()`.

#### Fix 3: Wire stage markers into all 7 display-stage skills

Replace the 5 dangling `session_progress` calls and add markers to the 5 skills that currently have none:

| Skill | Current State | Action |
|-------|--------------|--------|
| `/do-issue` | No markers | Add in_progress/completed markers |
| `/do-plan` | No markers | Add in_progress/completed markers |
| `/do-plan-critique` | No markers | Add in_progress/completed markers |
| `/do-build` | No markers (hooks handle it) | Add markers as backup |
| `/do-test` | No markers (hooks handle it) | Add markers as backup |
| `/do-pr-review` | Dangling session_progress calls | Replace with sdlc_stage_marker |
| `/do-docs` | Dangling session_progress calls | Replace with sdlc_stage_marker |

#### Fix 4: Strengthen do-merge gate

Change do-merge.md to show very strong warnings when `get_display_progress()` shows stages as pending/skipped. The gate is a reminder, not a hard blocker. For emergency hotfixes, the agent can choose to proceed, but only after explicit acknowledgment listing every skipped stage.

#### Fix 5: Update SDLC router fallback path

Update SKILL.md Steps 2a-2e to remove artifact inference references entirely. When stage_states is unavailable AND the conversation has no dispatch history, the router should dispatch from the beginning of the pipeline rather than guessing.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdlc_stage_marker.py` -- verify tool returns exit code 0 and empty JSON on all error paths (no session found, invalid stage, Redis down)
- [ ] `get_display_progress()` -- verify no artifact inference is called (function deleted)

### Empty/Invalid Input Handling
- [ ] `sdlc_stage_marker` with no env vars set -- should print `{}` and exit 0
- [ ] `sdlc_stage_marker` with invalid stage name -- should print error and exit 0

### Error State Rendering
- [ ] do-merge gate with pending stages -- should print clear blocker message, not just warning

## Test Impact

- [ ] `tests/integration/test_artifact_inference.py::TestDisplayProgress::test_display_progress_with_plan_slug_fills_gaps` -- REPLACE: rewrite to verify stored-state-only behavior (no artifact inference)
- [ ] `tests/integration/test_artifact_inference.py::TestDisplayProgress::test_display_progress_with_merged_pr_slug` -- REPLACE: rewrite to verify stored-state-only behavior
- [ ] `tests/unit/test_pipeline_state_machine.py` -- UPDATE: add tests verifying `_infer_stage_from_artifacts` is deleted and `get_display_progress` returns stored state only
- [ ] Any other tests referencing `_infer_stage_from_artifacts` -- DELETE or REPLACE

## Rabbit Holes

- **Building a new artifact inference system** -- We're deleting the old one. Don't build a replacement. Stored state is the single source of truth.
- **Adding a full state persistence layer for local Claude Code** -- The fallback path (no PM session) doesn't need Redis-backed state. Conversation context is sufficient for the router to track what it dispatched in this invocation chain.
- **Making skills aware of their own stage** -- Skills shouldn't need to know which pipeline stage they represent. The marker calls are a simple fire-and-forget pattern, not a two-way integration.

## Risks

### Risk 1: Skills fail to write markers due to missing env vars
**Impact:** Stage markers not written, pipeline stalls at "pending" stages
**Mitigation:** The CLI tool is designed to fail silently (exit 0, empty JSON) like the existing `sdlc_stage_query`. Skills add markers with `2>/dev/null || true`. The bridge hooks remain the primary marker path; skill markers are a belt-and-suspenders backup.

### Risk 2: do-merge gate warnings get ignored
**Impact:** Agent treats strong warnings as routine and skips past them
**Mitigation:** Warnings list every skipped stage explicitly and require the agent to acknowledge. For emergency hotfixes, the agent can proceed but the language is designed to make this a deliberate, visible choice — not a routine click-through.

## Race Conditions

No race conditions identified -- stage marker writes are idempotent (start_stage on already in_progress is a no-op, complete_stage on already completed is a no-op) and the CLI tool operates on a single session at a time.

## No-Gos (Out of Scope)

- Building a new inference system to replace the deleted one
- Adding Redis-backed state for local Claude Code sessions (conversation context is sufficient)
- Changing the bridge hook wiring (pre_tool_use/subagent_stop work correctly)
- Modifying the pipeline graph or stage order

## Update System

No update system changes required -- this feature modifies skills (SKILL.md files), one bridge module (pipeline_state.py), and adds one CLI tool (sdlc_stage_marker.py). All propagated via normal git pull.

## Agent Integration

No new MCP server integration required. The `sdlc_stage_marker.py` CLI tool is invoked directly by skills via bash commands in SKILL.md files, not through MCP. The bridge hooks that call `start_stage()`/`complete_stage()` are already wired and unchanged.

## Documentation

- [ ] Update `docs/features/sdlc-pipeline-graph.md` (or equivalent) to document the artifact inference vs stored state distinction
- [ ] Add entry to `docs/features/README.md` if a new feature doc is created

## Success Criteria

- [ ] `_infer_stage_from_artifacts()` is deleted from `bridge/pipeline_state.py`
- [ ] `get_display_progress()` returns stored state only — no artifact inference code remains
- [ ] All 7 display-stage skills write in_progress/completed markers via `sdlc_stage_marker`
- [ ] do-merge gate shows strong warnings when prior stages are pending/skipped (not a hard blocker)
- [ ] A test verifies that `get_display_progress()` returns only stored state
- [ ] A test verifies the do-merge gate warns when DOCS stage is not explicitly completed
- [ ] Dangling `session_progress` references removed from all skill files
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pipeline-state)**
  - Name: pipeline-state-builder
  - Role: Modify `get_display_progress()`, create `sdlc_stage_marker.py`, update skill files
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline-state)**
  - Name: pipeline-state-validator
  - Role: Verify routing isolation, test stage marker CLI, validate do-merge gate
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove artifact inference from `get_display_progress()` entirely
- **Task ID**: build-display-progress
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py (update), tests/integration/test_artifact_inference.py (rewrite)
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `_infer_stage_from_artifacts()` function from `bridge/pipeline_state.py`
- Remove the merge/gap-filling logic in `get_display_progress()` that calls it
- `get_display_progress()` now returns stored state only — simple and clean
- Rewrite tests that relied on artifact inference to set up stored state instead

### 2. Create `tools/sdlc_stage_marker.py` CLI tool
- **Task ID**: build-stage-marker
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_stage_marker.py (create)
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: true
- Create CLI tool that accepts `--stage STAGE --status STATUS` arguments
- Resolve session from `VALOR_SESSION_ID` or `AGENT_SESSION_ID` env vars
- Call `PipelineStateMachine.start_stage()` for `in_progress` or `complete_stage()` for `completed`
- Return exit code 0 always, print `{}` on error (match sdlc_stage_query pattern)

### 3. Wire stage markers into all 7 display-stage skills
- **Task ID**: build-skill-markers
- **Depends On**: build-stage-marker
- **Validates**: grep confirms all 7 skills reference sdlc_stage_marker
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace 5 dangling `session_progress` calls in do-docs, do-pr-review, post-review with `sdlc_stage_marker`
- Add marker calls to do-issue, do-plan, do-plan-critique, do-build, do-test skills
- Each skill gets: `python -m tools.sdlc_stage_marker --stage STAGE --status in_progress 2>/dev/null || true` at start, and `--status completed` at end

### 4. Strengthen do-merge gate
- **Task ID**: build-merge-gate
- **Depends On**: build-display-progress
- **Validates**: manual review of do-merge.md
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: false
- Update do-merge.md to call `get_display_progress()` (stored state only now)
- Show very strong warnings listing every skipped/pending stage
- Not a hard blocker — agent can proceed for emergency hotfixes after explicit acknowledgment
- Include language like "WARNING: The following stages were NOT completed: ..." with clear emphasis

### 5. Update SDLC router SKILL.md fallback path
- **Task ID**: build-router-fallback
- **Depends On**: none
- **Validates**: manual review of SKILL.md
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove artifact inference references from Steps 2a-2e entirely
- Add explicit instruction: "Stage completion is determined exclusively by stored state. Never infer from artifacts."
- When stage_states unavailable and no dispatch history in conversation, start from beginning of pipeline

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-display-progress, build-stage-marker, build-skill-markers, build-merge-gate, build-router-fallback
- **Assigned To**: pipeline-state-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pipeline_state_machine.py tests/integration/test_artifact_inference.py -v`
- Verify no dangling `session_progress` references: `grep -r 'session_progress' .claude/`
- Verify all 7 skills have `sdlc_stage_marker` calls: `grep -r 'sdlc_stage_marker' .claude/skills/`
- Run full test suite: `pytest tests/ -x -q`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: pipeline-state-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update or create docs on the artifact inference vs stored state distinction
- Add entry to `docs/features/README.md` if needed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No dangling session_progress | `grep -r 'session_progress' .claude/skills/` | exit code 1 |
| All skills have markers | `grep -rl 'sdlc_stage_marker' .claude/skills/do-issue/ .claude/skills/do-plan/ .claude/skills/do-plan-critique/ .claude/skills/do-build/ .claude/skills/do-test/ .claude/skills/do-pr-review/ .claude/skills/do-docs/ \| wc -l` | output > 6 |
| Artifact inference deleted | `grep -c '_infer_stage_from_artifacts' bridge/pipeline_state.py` | exit code 1 (not found) |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-06. -->

| Severity | Critic(s) | Finding | Suggestion |
|----------|-----------|---------|------------|
| CONCERN | Skeptic | Plan says "Replace 5 dangling session_progress calls" but grep found only 3 files (do-docs/SKILL.md, do-pr-review/SKILL.md, do-pr-review/sub-skills/post-review.md). The count "5" is inaccurate. | Update the count in Fix 3 to match reality (3 files with 5 call sites across them, or correct the wording). Verify with `grep -rn session_progress .claude/skills/` during build. |
| CONCERN | Adversary | Skill writes `--status completed` marker, then subagent_stop hook fires and calls `classify_outcome()` which may call `fail_stage()`, overwriting the skill's completed marker. Two writers with no coordination. | Document that hooks are authoritative and skill markers are best-effort. Or add a guard: `complete_stage()` should no-op if status is already `completed` (it does), but `fail_stage()` should not overwrite `completed` (it already does no-op -- verify this is tested). |
| CONCERN | Operator | After removing artifact inference, `get_display_progress()` returns stored state only. If stored state is empty (fresh session, Redis cleared, local Claude Code), the do-merge gate shows all stages pending and blocks merge. No cold-start fallback. | Add a note in do-merge gate: if ALL stages are pending/ready (cold start), fall back to artifact checks or warn clearly that "no pipeline state found" rather than listing every stage as skipped. |
| CONCERN | Operator | do-merge.md currently calls `get_display_progress(slug='$SLUG')` on lines 23 and 77. After this change removes the slug parameter behavior, those calls need updating but Task 4 only mentions "show very strong warnings" without specifying the code change to remove `slug=` args. | Task 4 should explicitly include updating the two `get_display_progress(slug=...)` calls in do-merge.md to `get_display_progress()` (no slug). |
| CONCERN | Archaeologist | The plan adds a second write path (skill CLI markers) alongside the existing hook path (pre_tool_use/subagent_stop). This mirrors the layering pattern from prior fixes that each addressed one layer without closing the loop. If hooks stop firing, skill markers become the only path -- but they depend on env vars that may not be set. | Acceptable as belt-and-suspenders if explicitly tested. Add a test that verifies the skill marker path works independently of hooks (env var set, no hook context). |
| NIT | Simplifier | Fix 3 adds markers to do-build and do-test which already get markers from hooks (pre_tool_use/subagent_stop). This doubles write paths for those two skills with no additional safety benefit. | Consider only adding markers to the 5 skills that lack hook coverage. The "backup" argument is weak for skills that are always invoked via the bridge. |
| NIT | User | All success criteria are technical (function deleted, tests pass, lint clean). No end-to-end validation that a full SDLC pipeline run correctly avoids stage skipping. | Add one success criterion: "A manual or scripted end-to-end run of the SDLC pipeline for a test issue completes all stages without skipping." |
| NIT | Skeptic | Documentation section references `docs/features/sdlc-pipeline-graph.md` with "(or equivalent)" but this file does not exist. The builder will need to decide whether to create it or find the actual equivalent. | Specify the exact target file path. Check `docs/features/README.md` for the correct location. |

---

## Open Questions

1. ~~Should `_infer_stage_from_artifacts()` be removed entirely from `get_display_progress()`, or should the `for_routing` parameter split be preserved?~~ **RESOLVED**: Remove artifact inference entirely. It doesn't work in practice. Rip out `_infer_stage_from_artifacts()` and rewrite tests to validate the new behavior.
2. ~~For the do-merge gate, should there be a `--force` flag or env var to skip the stage check entirely (for emergency hotfixes)?~~ **RESOLVED**: No force flag. The merge gate is a reminder, not a hard blocker. For emergency hotfixes, the agent can choose to ignore it, but only after very strong warnings and reminders.
