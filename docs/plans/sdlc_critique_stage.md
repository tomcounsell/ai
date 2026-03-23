---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/463
last_comment_id:
---

# SDLC Critique Stage + Hallucination Fix

Closes #463, Closes #469

## Problem

**Current behavior:** After `/do-plan` completes, the Observer routes directly to `/do-build`. Plans with internal contradictions, missing tasks, unmet prerequisites, or architectural gaps proceed to build where they surface as costly rework. Additionally, when `/do-plan-critique` IS manually invoked, its critic agents hallucinate file names, constants, and file contents because they independently re-discover the codebase instead of receiving verified data.

**Desired outcome:** The SDLC pipeline includes a CRITIQUE stage between PLAN and BUILD that automatically validates plans before implementation. The critique skill passes verified file contents inline to critics so they cannot hallucinate source code artifacts.

## Prior Art

- **Issue #422**: Enhanced do-plan with spike tasks, RFC review, INFRA doc, and test mapping. Successfully added structured critique (RFC review) as a plan-time activity. Relevant because it shows the pattern of adding review stages to the pipeline.
- **Issue #467**: Pipeline cleanup + e2e tests. Recently merged. Relevant because it cleaned up dead code in the pipeline, making the graph simpler to extend.

No prior attempts to add CRITIQUE as a pipeline stage. The do-plan-critique skill was created as a standalone tool (v1.0.0, 2026-03-21) and has not been integrated into automated pipeline flow.

## Data Flow

1. **Entry point**: Observer detects PLAN stage completed (success outcome)
2. **Pipeline graph**: `get_next_stage("PLAN", "success")` returns `("CRITIQUE", "/do-plan-critique")`
3. **SDLC router**: Dispatches `/do-plan-critique {plan_path}` with plan file contents and referenced source files pre-read
4. **Critique skill**: Runs structural checks + parallel critics (with inline source code, not discovery)
5. **Verdict**: Returns READY TO BUILD, NEEDS REVISION, or MAJOR REWORK
6. **Pipeline graph**: `get_next_stage("CRITIQUE", "success")` returns `("BUILD", "/do-build")` or `get_next_stage("CRITIQUE", "fail")` returns `("PLAN", "/do-plan")`
7. **Output**: Observer routes to next stage or escalates to human (MAJOR REWORK)

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All touched modules are internal.

## Solution

### Key Elements

- **Pipeline graph extension**: Add CRITIQUE stage between PLAN and BUILD with success/fail/rework transitions
- **State machine update**: Track CRITIQUE stage in PipelineStateMachine with cycle limits
- **Critique skill fix**: Pre-read source files referenced in plans and pass contents inline to critics
- **SDLC router update**: Add CRITIQUE dispatch logic to the routing table

### Flow

**PLAN completes** -> Pipeline routes to CRITIQUE -> `/do-plan-critique` runs (with inline source code) -> Verdict returned -> **READY TO BUILD** routes to BUILD | **NEEDS REVISION** routes back to PLAN | **MAJOR REWORK** escalates to human

### Technical Approach

- Add CRITIQUE edges to `PIPELINE_EDGES` dict in `bridge/pipeline_graph.py`
- Add `MAX_CRITIQUE_CYCLES = 2` constant (analogous to `MAX_PATCH_CYCLES`)
- Add CRITIQUE to `ALL_STAGES` in `bridge/pipeline_state.py` and `SDLC_STAGES` in `models/agent_session.py`
- Modify `do-plan-critique/SKILL.md` Step 1 to extract file paths from plan, read them, and bundle contents
- Modify `do-plan-critique/CRITICS.md` prompt template to include `SOURCE_FILES` context block
- Add CRITIQUE outcome patterns to `classify_outcome()` in `bridge/pipeline_state.py`
- Add `critique` to `agent/pipeline_state.py` STAGES list
- Update SDLC router dispatch table in `.claude/skills/sdlc/SKILL.md`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `classify_outcome("CRITIQUE", ...)` must handle missing verdict patterns gracefully (return "ambiguous")
- [ ] Critique cycle limit must log warning when reached, not raise

### Empty/Invalid Input Handling
- [ ] Plan with no file path references should still pass through critique (structural checks only, no source files to inline)
- [ ] Empty critic output should be treated as "no findings" not as an error

### Error State Rendering
- [ ] CRITIQUE failure (NEEDS REVISION) must propagate the findings back to PLAN stage, not silently drop them

## Test Impact

- [ ] `tests/unit/test_pipeline_graph.py::TestHappyPath::test_plan_to_build` -- UPDATE: PLAN now routes to CRITIQUE, not BUILD
- [ ] `tests/unit/test_pipeline_graph.py::TestHappyPath::test_full_happy_path_traversal` -- UPDATE: add CRITIQUE between PLAN and BUILD
- [ ] `tests/unit/test_pipeline_graph.py::TestExports::test_display_stages_order` -- UPDATE: add CRITIQUE to expected list
- [ ] `tests/unit/test_pipeline_graph.py::TestExports::test_pipeline_edges_are_complete` -- UPDATE: CRITIQUE needs success edge
- [ ] `tests/unit/test_pipeline_graph.py::TestExports::test_stage_to_skill_values` -- UPDATE: add CRITIQUE assertion
- [ ] `tests/unit/test_pipeline_state_machine.py::TestStartStage::test_start_build_requires_plan_completed` -- UPDATE: BUILD now requires CRITIQUE completed (not PLAN)
- [ ] `tests/unit/test_pipeline_state_machine.py::TestCompleteStage::test_complete_marks_next_stage_ready` -- UPDATE: completing ISSUE marks PLAN ready (unchanged), completing PLAN marks CRITIQUE ready
- [ ] `tests/unit/test_pipeline_state_machine.py::TestDisplayProgress::test_returns_all_display_stages` -- UPDATE: add CRITIQUE to expected list
- [ ] `tests/unit/test_pipeline_integrity.py::TestMergeStageTracking::test_merge_in_sdlc_stages` -- no change needed (still checks MERGE)
- [ ] `tests/integration/test_agent_session_lifecycle.py` -- UPDATE: SDLC_STAGES import and iteration now includes CRITIQUE

## Rabbit Holes

- Making CRITIQUE optional/skippable per plan -- every plan goes through critique for now. Skip logic can be added later if small plans prove too slow.
- Changing the critic prompts beyond adding inline source code -- the existing critic definitions work well, the hallucination is a data problem not a prompt problem.
- Adding new critic roles or changing the war-room pattern -- out of scope.
- Implementing the "remove multi-agent parallelism" fix from issue #469 -- the inline data fix addresses the root cause.

## Risks

### Risk 1: Critique stage slows pipeline for trivial plans
**Impact:** Small appetite work takes longer to reach BUILD
**Mitigation:** Critique skill already handles small plans quickly (structural checks only, skip RFC review). If needed, a future skip-for-small-plans gate can be added without pipeline changes.

### Risk 2: CRITIQUE -> PLAN loop doesn't converge
**Impact:** Plan gets stuck in revision cycles
**Mitigation:** MAX_CRITIQUE_CYCLES = 2 caps the loop. After 2 revisions, escalate to human.

## Race Conditions

No race conditions identified -- pipeline stages execute sequentially within a single ChatSession. Stage transitions are persisted atomically via session.save().

## No-Gos (Out of Scope)

- Making critique optional or skippable for any plan type
- Removing multi-agent parallelism (issue #469 fix #2)
- Changing critic role definitions or adding new critics
- Modifying the do-plan skill itself
- Cross-repo critique (critique only runs in the target repo context, same as today)

## Update System

No update system changes required -- this feature modifies pipeline routing and skill definitions, all of which are pulled via `git pull` during updates. No new dependencies, config files, or migration steps.

## Agent Integration

No new MCP server or tool registration needed. The `/do-plan-critique` skill is already invocable as a Claude Code skill. The pipeline graph change causes the SDLC router to dispatch it automatically -- no bridge code needs to import or call critique directly.

Integration verification: After build, confirm that `bridge/pipeline_graph.py` STAGE_TO_SKILL maps CRITIQUE to "/do-plan-critique" and the SDLC router SKILL.md dispatch table includes the CRITIQUE row.

## Documentation

- [ ] Create `docs/features/sdlc-critique-stage.md` describing the CRITIQUE pipeline stage
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings in `bridge/pipeline_graph.py` and `bridge/pipeline_state.py`

## Success Criteria

- [ ] `PIPELINE_EDGES` includes CRITIQUE stage with success/fail transitions
- [ ] `STAGE_TO_SKILL` maps CRITIQUE to `/do-plan-critique`
- [ ] `DISPLAY_STAGES` includes CRITIQUE between PLAN and BUILD
- [ ] `ALL_STAGES` in pipeline_state.py includes CRITIQUE
- [ ] `SDLC_STAGES` in agent_session.py includes CRITIQUE
- [ ] SDLC router dispatches `/do-plan-critique` when plan exists but hasn't been critiqued
- [ ] Observer classifies critique verdict via `classify_outcome("CRITIQUE", ...)` patterns
- [ ] Critique -> Plan loop capped at MAX_CRITIQUE_CYCLES = 2
- [ ] do-plan-critique SKILL.md extracts file paths from plan and passes contents inline
- [ ] do-plan-critique CRITICS.md prompt template includes SOURCE_FILES context block
- [ ] Pipeline graph tests updated for new CRITIQUE stage
- [ ] Full SDLC pipeline test includes CRITIQUE stage
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pipeline)**
  - Name: pipeline-builder
  - Role: Update pipeline graph, state machine, agent session model, and all related tests
  - Agent Type: builder
  - Resume: true

- **Builder (critique-skill)**
  - Name: critique-fixer
  - Role: Update do-plan-critique SKILL.md and CRITICS.md to inline source file contents
  - Agent Type: builder
  - Resume: true

- **Builder (router)**
  - Name: router-builder
  - Role: Update SDLC router SKILL.md dispatch table and pipeline reference
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: pipeline-validator
  - Role: Verify all pipeline transitions, test updates, and skill modifications
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update Pipeline Graph
- **Task ID**: build-pipeline-graph
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_graph.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `MAX_CRITIQUE_CYCLES = 2` constant to `bridge/pipeline_graph.py`
- Add CRITIQUE edges to PIPELINE_EDGES: `("PLAN", "success"): "CRITIQUE"`, `("CRITIQUE", "success"): "BUILD"`, `("CRITIQUE", "fail"): "PLAN"`
- Add `"CRITIQUE": "/do-plan-critique"` to STAGE_TO_SKILL
- Add `"CRITIQUE"` to DISPLAY_STAGES between PLAN and BUILD
- Update module docstring to reference CRITIQUE stage
- Add cycle limit check for CRITIQUE in `get_next_stage()` (analogous to PATCH cycle limit)

### 2. Update Pipeline State Machine
- **Task ID**: build-pipeline-state
- **Depends On**: build-pipeline-graph
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `"CRITIQUE"` to ALL_STAGES in `bridge/pipeline_state.py`
- Add CRITIQUE outcome patterns to `classify_outcome()`: "ready to build" -> success, "needs revision" -> fail, "major rework" -> return None (escalate)
- Add `_critique_cycle_count` tracking (analogous to `patch_cycle_count`) or reuse the same counter with a separate max

### 3. Update Agent Session Model
- **Task ID**: build-agent-session
- **Depends On**: none
- **Validates**: tests/integration/test_agent_session_lifecycle.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"CRITIQUE"` to `SDLC_STAGES` in `models/agent_session.py` between PLAN and BUILD

### 4. Update Agent Pipeline State
- **Task ID**: build-agent-pipeline-state
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"critique"` to STAGES list in `agent/pipeline_state.py` between "plan" and "branch"

### 5. Fix Critique Skill - Inline Source Files
- **Task ID**: build-critique-skill
- **Depends On**: none
- **Validates**: manual review (skill files are markdown)
- **Assigned To**: critique-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/do-plan-critique/SKILL.md` Step 1 to:
  - Extract file paths referenced in the plan (regex for paths like `path/to/file.py`)
  - Read each referenced file
  - Bundle contents into a `SOURCE_FILES` context block
- Update Step 3 to pass `SOURCE_FILES` block to each critic prompt
- Add Step 1.5: "If file read fails, note the file as 'not found' -- do NOT ask critics to discover it"

### 6. Update Critic Prompt Template
- **Task ID**: build-critic-prompts
- **Depends On**: build-critique-skill
- **Validates**: manual review
- **Assigned To**: critique-fixer
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/do-plan-critique/CRITICS.md` prompt template to include:
  ```
  SOURCE_FILES:
  {verified file contents with paths}
  ```
- Add instruction: "Use ONLY the provided SOURCE_FILES for code references. Do NOT read files yourself. If a file is not in SOURCE_FILES, state 'file not provided' rather than guessing its contents."
- Add citation requirement: "Any BLOCKER or CONCERN referencing a specific file must include a file:line citation from SOURCE_FILES."

### 7. Update SDLC Router
- **Task ID**: build-router
- **Depends On**: build-pipeline-graph
- **Validates**: manual review (SKILL.md)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add CRITIQUE row to dispatch table in `.claude/skills/sdlc/SKILL.md`
- Update pipeline reference diagram and table
- Update Step 2 to check for critique completion status

### 8. Update Tests
- **Task ID**: build-tests
- **Depends On**: build-pipeline-graph, build-pipeline-state, build-agent-session
- **Validates**: pytest tests/unit/test_pipeline_graph.py tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_integrity.py -v
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- Update test_pipeline_graph.py: fix happy path assertions (PLAN->CRITIQUE->BUILD), add CRITIQUE tests
- Update test_pipeline_state_machine.py: fix BUILD predecessor test, add CRITIQUE state tests
- Update test_pipeline_integrity.py: update DISPLAY_STAGES and SDLC_STAGES assertions
- Add new test class for CRITIQUE cycle limit behavior

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: pipeline-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-critique-stage.md`
- Add entry to `docs/features/README.md`

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-critique-skill, build-critic-prompts, build-router, document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all pipeline tests: `pytest tests/unit/test_pipeline_graph.py tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_integrity.py tests/unit/test_pipeline_state.py -v`
- Verify CRITIQUE in all stage lists
- Verify SKILL.md files are internally consistent
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_pipeline_graph.py tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_integrity.py tests/unit/test_pipeline_state.py -v` | exit code 0 |
| Lint clean | `python -m ruff check bridge/pipeline_graph.py bridge/pipeline_state.py models/agent_session.py agent/pipeline_state.py` | exit code 0 |
| CRITIQUE in PIPELINE_EDGES | `python -c "from bridge.pipeline_graph import PIPELINE_EDGES; assert ('CRITIQUE', 'success') in PIPELINE_EDGES"` | exit code 0 |
| CRITIQUE in DISPLAY_STAGES | `python -c "from bridge.pipeline_graph import DISPLAY_STAGES; assert 'CRITIQUE' in DISPLAY_STAGES"` | exit code 0 |
| CRITIQUE in SDLC_STAGES | `python -c "from models.agent_session import SDLC_STAGES; assert 'CRITIQUE' in SDLC_STAGES"` | exit code 0 |

## Open Questions

1. Should CRITIQUE use its own cycle counter (`_critique_cycle_count`) or share `patch_cycle_count`? Recommendation: separate counter since the semantics differ (plan revision vs code fix). The limit (2) is also different from PATCH (3).
2. Should the CRITIQUE stage be skipped for plans with `appetite: Small`? The issue says no (every plan goes through critique), but the critique skill itself already fast-paths small plans. Recommendation: always route through CRITIQUE, let the skill handle speed internally.
