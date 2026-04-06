---
status: Planning
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

- **Interface changes**: `get_display_progress()` behavior changes -- artifact inference no longer fills gaps by default
- **Coupling**: Reduces coupling between artifact existence and stage completion status
- **Data ownership**: Stage completion status becomes exclusively owned by PipelineStateMachine (stored state), not inferred from side effects
- **Reversibility**: Fully reversible -- revert the `get_display_progress` change and restore fallback inference in SKILL.md

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

- **Eliminate artifact inference from stage completion decisions**: Remove `_infer_stage_from_artifacts()` from `get_display_progress()` merge logic, and update the SDLC router fallback path to use conversation-scoped dispatch tracking instead of artifact inference
- **Replace dangling session_progress calls with PipelineStateMachine CLI**: Create a lightweight CLI tool that skills can call to write stage markers, replacing the deleted `tools/session_progress.py`
- **Strengthen do-merge gate**: Block merge when stages show as pending/skipped instead of just warning

### Flow

**PM invokes /sdlc** -> Router checks stage_states (primary) or dispatch history (fallback) -> Dispatches next skill -> Skill writes stage markers via CLI -> subagent_stop records completion -> PM re-invokes /sdlc

### Technical Approach

#### Fix 1: Remove artifact inference from get_display_progress merge

`get_display_progress(slug=...)` currently calls `_infer_stage_from_artifacts()` and merges inferred state into gaps (pending/ready slots). Change this so artifact inference is only used for **display/informational** purposes (e.g., dashboard) but NOT for routing decisions.

Concretely:
- Add a `for_routing: bool = False` parameter to `get_display_progress()`
- When `for_routing=True`, skip artifact inference entirely -- return only stored state
- Update do-merge gate to use `for_routing=True`
- The SDLC router skill's fallback path (Step 2.0 failure) already queries `sdlc_stage_query` which returns stored state only -- no change needed there

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

Change do-merge.md to block merge (not just warn) when `get_display_progress(for_routing=True)` shows stages as pending/skipped. The user can override with explicit confirmation, but the default is to block.

#### Fix 5: Update SDLC router fallback path

Update SKILL.md Steps 2a-2e to document that artifact inference is informational only and cannot satisfy stage completion. When stage_states is unavailable AND the conversation has no dispatch history, the router should dispatch from the beginning of the pipeline rather than inferring completion from artifacts.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdlc_stage_marker.py` -- verify tool returns exit code 0 and empty JSON on all error paths (no session found, invalid stage, Redis down)
- [ ] `get_display_progress(for_routing=True)` -- verify no artifact inference is called

### Empty/Invalid Input Handling
- [ ] `sdlc_stage_marker` with no env vars set -- should print `{}` and exit 0
- [ ] `sdlc_stage_marker` with invalid stage name -- should print error and exit 0

### Error State Rendering
- [ ] do-merge gate with pending stages -- should print clear blocker message, not just warning

## Test Impact

- [ ] `tests/integration/test_artifact_inference.py::TestDisplayProgress::test_display_progress_with_plan_slug_fills_gaps` -- UPDATE: this test verifies gap-filling behavior; update to test `for_routing=False` (display) vs `for_routing=True` (routing) split
- [ ] `tests/integration/test_artifact_inference.py::TestDisplayProgress::test_display_progress_with_merged_pr_slug` -- UPDATE: same split
- [ ] `tests/unit/test_pipeline_state_machine.py` -- UPDATE: add tests for `for_routing` parameter

## Rabbit Holes

- **Rewriting artifact inference entirely** -- Artifact inference is still useful for dashboard display. Only its use in routing decisions is the problem. Don't delete `_infer_stage_from_artifacts()`.
- **Adding a full state persistence layer for local Claude Code** -- The fallback path (no PM session) doesn't need Redis-backed state. Conversation context is sufficient for the router to track what it dispatched in this invocation chain.
- **Making skills aware of their own stage** -- Skills shouldn't need to know which pipeline stage they represent. The marker calls are a simple fire-and-forget pattern, not a two-way integration.

## Risks

### Risk 1: Skills fail to write markers due to missing env vars
**Impact:** Stage markers not written, pipeline stalls at "pending" stages
**Mitigation:** The CLI tool is designed to fail silently (exit 0, empty JSON) like the existing `sdlc_stage_query`. Skills add markers with `2>/dev/null || true`. The bridge hooks remain the primary marker path; skill markers are a belt-and-suspenders backup.

### Risk 2: do-merge gate becomes too strict, blocks legitimate merges
**Impact:** Developer friction when stages were legitimately completed outside the pipeline
**Mitigation:** do-merge still allows explicit user override after showing which stages appear skipped. The change is from "warn and ask" to "block and ask" -- same UX flow, different default.

## Race Conditions

No race conditions identified -- stage marker writes are idempotent (start_stage on already in_progress is a no-op, complete_stage on already completed is a no-op) and the CLI tool operates on a single session at a time.

## No-Gos (Out of Scope)

- Removing artifact inference entirely (still needed for dashboard/display)
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

- [ ] The SDLC router's fallback path does not infer stage completion from artifacts for routing decisions
- [ ] `get_display_progress(for_routing=True)` returns stored state only, no artifact inference
- [ ] All 7 display-stage skills write in_progress/completed markers via `sdlc_stage_marker`
- [ ] do-merge gate blocks (not just warns) when prior stages show as pending/skipped
- [ ] A test verifies that artifact existence alone does not satisfy the docs stage check in routing mode
- [ ] A test verifies the do-merge gate rejects when DOCS stage is not explicitly completed
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

### 1. Add `for_routing` parameter to `get_display_progress()`
- **Task ID**: build-display-progress
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py (update), tests/integration/test_artifact_inference.py (update)
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `for_routing: bool = False` parameter to `get_display_progress()` in `bridge/pipeline_state.py`
- When `for_routing=True`, return stored state only (skip `_infer_stage_from_artifacts()` call)
- When `for_routing=False` (default), preserve current behavior for backward compatibility
- Update existing tests and add new tests for the `for_routing` parameter

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
- Update do-merge.md to use `get_display_progress(for_routing=True)` (no slug parameter)
- Change gate behavior from "warn and ask" to "block and ask" for skipped stages
- Keep user override path (they can confirm after seeing what was skipped)

### 5. Update SDLC router SKILL.md fallback path
- **Task ID**: build-router-fallback
- **Depends On**: none
- **Validates**: manual review of SKILL.md
- **Assigned To**: pipeline-state-builder
- **Agent Type**: builder
- **Parallel**: true
- Update Steps 2a-2e to clarify that artifact checks are informational only
- Add explicit instruction: "Never infer a stage is done from artifacts alone for routing decisions"
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
| for_routing parameter exists | `grep -c 'for_routing' bridge/pipeline_state.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. Should `_infer_stage_from_artifacts()` be removed entirely from `get_display_progress()` (breaking the display/dashboard use case), or should the `for_routing` parameter split be preserved? The plan currently proposes the split.
2. For the do-merge gate, should there be a `--force` flag or env var to skip the stage check entirely (for emergency hotfixes), or is explicit user confirmation sufficient?
