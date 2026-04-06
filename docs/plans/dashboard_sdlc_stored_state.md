---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/735
---

# Dashboard SDLC Stages Column: Route Through PipelineStateMachine

## Problem

The dashboard's SDLC stages column in `ui/data/sdlc.py` uses `_parse_stage_states()` to iterate over a locally-defined `SDLC_STAGES` list, while `PipelineStateMachine.get_display_progress()` uses `DISPLAY_STAGES` from `bridge/pipeline_graph.py`. Both lists currently contain the same 8 stages, but if they ever diverge — or if status coercion logic differs — the dashboard will show stale or incorrect stage data.

Additionally, the test file `tests/unit/test_ui_sdlc_data.py` contains a `TestArtifactInference` class that references removed functions (`_artifact_inference_cache`, `_get_artifact_enriched_stages`) and calls `get_display_progress(slug=...)` with an argument the method does not accept. These tests are broken remnants of the removed artifact inference system.

**Current behavior:**
`_session_to_pipeline()` calls `_parse_stage_states(session.stage_states)`, which iterates the local `SDLC_STAGES` constant. This is a parallel re-implementation of `PipelineStateMachine.get_display_progress()`.

**Desired outcome:**
`_session_to_pipeline()` routes stage reads through `PipelineStateMachine.get_display_progress()`, making the dashboard a direct consumer of the canonical stored-state path. The `SDLC_STAGES` constant in `ui/data/sdlc.py` is removed or derived from `DISPLAY_STAGES`.

## Prior Art

- **Issue #729 / PR #733**: Introduced `PipelineStateMachine` and `sdlc_stage_marker.py`, established the stored-state-only pattern. Removed artifact inference from `pipeline_state.py` but left `ui/data/sdlc.py` reading `stage_states` via its own parallel parser.
- **Issue #656**: Dashboard shows stale pipeline state — addressed artifact inference but the dashboard's parallel parse path was not consolidated.
- **Issue #549**: Original dashboard SDLC stage visibility work.

## Data Flow

1. **Write path**: SDLC skills invoke `python -m tools.sdlc_stage_marker --stage X --status Y`, which calls `PipelineStateMachine.start_stage()` / `complete_stage()`, persisting to `AgentSession.stage_states` in Redis.
2. **Read path (current)**: `_session_to_pipeline()` → `_parse_stage_states(session.stage_states)` → iterates local `SDLC_STAGES` list → returns `list[StageState]`.
3. **Read path (desired)**: `_session_to_pipeline()` → `PipelineStateMachine(session).get_display_progress()` → iterates canonical `DISPLAY_STAGES` → returns `dict[str, str]` → converted to `list[StageState]`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Route `_session_to_pipeline()` through `PipelineStateMachine`**: Replace `_parse_stage_states(session.stage_states)` with `PipelineStateMachine(session).get_display_progress()` for sessions that have stage data.
- **Remove or consolidate `SDLC_STAGES`**: Either remove the constant entirely or derive it from `bridge.pipeline_graph.DISPLAY_STAGES`.
- **Keep `_parse_stage_states()` as fallback**: Retain the function for edge cases (e.g., `PipelineStateMachine` constructor fails on corrupt data), but it should no longer be the primary path.
- **Fix stale tests**: Replace the `TestArtifactInference` class with tests that verify the new routing path through `PipelineStateMachine.get_display_progress()`.

### Technical Approach

In `_session_to_pipeline()`:
```python
from bridge.pipeline_state import PipelineStateMachine

# Try PipelineStateMachine first (canonical path)
if session.stage_states:
    try:
        sm = PipelineStateMachine(session)
        progress = sm.get_display_progress()
        stages = [StageState(name=name, status=status) for name, status in progress.items()]
    except Exception:
        # Fallback to direct parse if state machine fails
        stages = _parse_stage_states(session.stage_states)
else:
    stages = []
```

For `SDLC_STAGES`: replace with an import of `DISPLAY_STAGES` from `bridge.pipeline_graph`, or remove if no other code references the local constant.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `try/except` fallback in `_session_to_pipeline()` when `PipelineStateMachine` fails — test asserts fallback produces valid `StageState` list

### Empty/Invalid Input Handling
- [ ] Sessions with `stage_states=None` produce empty stages list (no `PipelineStateMachine` call)
- [ ] Sessions with `stage_states=""` produce empty stages list
- [ ] Sessions with malformed JSON in `stage_states` fall back gracefully

### Error State Rendering
- [ ] Not applicable — this is a data layer change, no user-visible error rendering

## Test Impact

- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_session_with_slug_uses_artifact_inference` — REPLACE: rewrite to verify `PipelineStateMachine.get_display_progress()` is called (without slug arg)
- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_session_without_slug_uses_stored_state` — REPLACE: rewrite to verify sessions with no stage_states get empty stages without calling PipelineStateMachine
- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_session_with_empty_slug_uses_stored_state` — REPLACE: rewrite to verify empty stage_states produces empty stages
- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_artifact_inference_failure_falls_back_to_stored_state` — REPLACE: rewrite to verify `PipelineStateMachine` exception triggers `_parse_stage_states` fallback (current test imports removed `_artifact_inference_cache` symbol)
- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_session_no_slug_no_stage_states_produces_empty_stages` — REPLACE: migrate to new test class (behavior unchanged, but host class `TestArtifactInference` is being replaced)
- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_cache_hit_within_ttl` — DELETE: references removed `_artifact_inference_cache`
- [ ] `tests/unit/test_ui_sdlc_data.py::TestArtifactInference::test_cache_miss_after_ttl` — DELETE: references removed `_artifact_inference_cache`
- [ ] `tests/unit/test_ui_sdlc_data.py::TestStageStateParsing` — UPDATE: tests remain valid since `_parse_stage_states()` is kept as a fallback utility

## Rabbit Holes

- Refactoring `PipelineStateMachine` to cache or optimize — out of scope, the constructor is cheap
- Moving `_parse_stage_states` into `bridge/pipeline_state.py` — nice but not necessary for this change
- Removing `_parse_stage_states` entirely — it's still useful as a fallback and for test utilities

## Risks

### Risk 1: Import cycle between `ui/data/sdlc.py` and `bridge/pipeline_state.py`
**Impact:** Module import failure at runtime
**Mitigation:** `bridge.pipeline_state` only imports from `bridge.pipeline_graph` and `models.agent_session` (TYPE_CHECKING). `ui.data.sdlc` already imports from `bridge.routing` and `config.enums`, so adding a `bridge.pipeline_state` import introduces no cycle. Verify with `python -c "from ui.data.sdlc import _session_to_pipeline"`.

## Race Conditions

No race conditions identified — `_session_to_pipeline()` is a synchronous read-only function that constructs a fresh `PipelineStateMachine` per call. No shared mutable state.

## No-Gos (Out of Scope)

- Caching `PipelineStateMachine` instances across calls
- Changing `PipelineStateMachine.get_display_progress()` signature
- Modifying how `sdlc_stage_marker` writes state
- Dashboard UI template changes

## Update System

No update system changes required — this is a purely internal refactor of the dashboard data layer.

## Agent Integration

No agent integration required — this is a dashboard-internal change. No MCP servers, tools, or bridge code affected.

## Documentation

- [ ] Grep `docs/` for references to `SDLC_STAGES` in `ui/data/sdlc.py` and update if found (note: `docs/features/sdlc-pipeline-graph.md` does not exist)
- [ ] Inline docstring updates on `_session_to_pipeline()` to document the routing through `PipelineStateMachine`

## Success Criteria

- [ ] `_session_to_pipeline()` routes stage reads through `PipelineStateMachine.get_display_progress()`
- [ ] `SDLC_STAGES` constant removed or derived from `DISPLAY_STAGES`
- [ ] Dashboard "SDLC Stages" column reflects the canonical `DISPLAY_STAGES` list
- [ ] All existing tests in `test_ui_sdlc_data.py` pass (stale ones fixed)
- [ ] New test verifies `PipelineStateMachine.get_display_progress()` is called in `_session_to_pipeline()`
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (sdlc-data-layer)**
  - Name: sdlc-data-builder
  - Role: Modify `ui/data/sdlc.py` to route through `PipelineStateMachine`, fix stale tests
  - Agent Type: builder
  - Resume: true

- **Validator (sdlc-data-layer)**
  - Name: sdlc-data-validator
  - Role: Verify routing works correctly and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Consolidate stage reading path
- **Task ID**: build-stage-routing
- **Depends On**: none
- **Validates**: tests/unit/test_ui_sdlc_data.py
- **Assigned To**: sdlc-data-builder
- **Agent Type**: builder
- **Parallel**: true
- In `ui/data/sdlc.py`, replace `SDLC_STAGES` with import of `DISPLAY_STAGES` from `bridge.pipeline_graph`
- In `_session_to_pipeline()`, replace `_parse_stage_states(session.stage_states)` with `PipelineStateMachine(session).get_display_progress()` wrapped in try/except with `_parse_stage_states` fallback
- Keep `_parse_stage_states()` as private fallback utility

### 2. Fix stale tests
- **Task ID**: build-fix-tests
- **Depends On**: build-stage-routing
- **Validates**: tests/unit/test_ui_sdlc_data.py
- **Assigned To**: sdlc-data-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `TestArtifactInference` class: remove references to `_artifact_inference_cache`, `_get_artifact_enriched_stages`, and `slug=` parameter on `get_display_progress()`
- Add test verifying `PipelineStateMachine.get_display_progress()` is called for sessions with `stage_states`
- Add test verifying fallback to `_parse_stage_states()` when `PipelineStateMachine` raises
- Add test verifying sessions with no `stage_states` get empty stages without constructing `PipelineStateMachine`

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-fix-tests
- **Assigned To**: sdlc-data-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_ui_sdlc_data.py -x -q`
- Run `python -c "from ui.data.sdlc import _session_to_pipeline"` to verify no import cycles
- Verify `SDLC_STAGES` is no longer defined as a separate constant (or is derived from `DISPLAY_STAGES`)

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_ui_sdlc_data.py -x -q` | exit code 0 |
| No import cycle | `python -c "from ui.data.sdlc import _session_to_pipeline"` | exit code 0 |
| SDLC_STAGES consolidated | `grep -c "^SDLC_STAGES = \[" ui/data/sdlc.py` | output contains 0 |
| Lint clean | `python -m ruff check ui/data/sdlc.py tests/unit/test_ui_sdlc_data.py` | exit code 0 |

## Critique Results

# Plan Critique: Dashboard SDLC Stages Column Route Through PipelineStateMachine

**Plan**: docs/plans/dashboard_sdlc_stored_state.md
**Issue**: #735
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 3 total (1 blocker, 1 concern, 1 nit)

## Blockers

### Test disposition mismatch for `test_artifact_inference_failure_falls_back_to_stored_state`
- **Severity**: BLOCKER
- **Critics**: Skeptic, Archaeologist
- **Location**: Test Impact section, line 4
- **Finding**: The plan says to UPDATE this test by "removing references to `_artifact_inference_cache`", but the test imports `_artifact_inference_cache` from `ui.data.sdlc` (line 757 of test file) which no longer exists. The entire test body references removed symbols (`_artifact_inference_cache.clear()`). This test currently fails at import time and cannot be merely updated -- it must be rewritten from scratch.
- **Suggestion**: Change disposition from UPDATE to REPLACE. Rewrite the test to verify that when `PipelineStateMachine()` raises an exception, `_session_to_pipeline()` falls back to `_parse_stage_states()` and produces valid stages.

## Concerns

### Ambiguous migration of `test_session_no_slug_no_stage_states_produces_empty_stages`
- **Severity**: CONCERN
- **Critics**: Simplifier
- **Location**: Test Impact section, line 5
- **Finding**: The plan says to keep this test "as-is (behavior unchanged)" but it lives inside the `TestArtifactInference` class which the plan says to REPLACE. The plan doesn't clarify whether this test migrates to the new replacement test class or stays in a remnant of the old class.
- **Suggestion**: Explicitly state that this test moves to the new test class (or a more appropriate existing class like `TestSessionToPipeline`) during the REPLACE of `TestArtifactInference`.

## Nits

### Documentation task references non-existent file
- **Severity**: NIT
- **Critics**: Archaeologist
- **Location**: Documentation section
- **Finding**: The documentation task says to check `docs/features/sdlc-pipeline-graph.md` for references to `SDLC_STAGES` -- this file does not exist in the codebase.
- **Suggestion**: Remove or update this documentation task. A `grep` for `SDLC_STAGES` across `docs/` would be more reliable than checking a specific non-existent file.

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All 4 required sections present and non-empty |
| Task numbering | PASS | Tasks 1, 2, 3 sequential with no gaps |
| Dependencies valid | PASS | All Depends On references resolve to valid task IDs |
| File paths exist | PASS | All primary paths exist (ui/data/sdlc.py, bridge/pipeline_state.py, bridge/pipeline_graph.py, tests/unit/test_ui_sdlc_data.py) |
| Prerequisites met | PASS | No prerequisites defined |
| Cross-references | PASS | Success criteria map to tasks; No-Gos do not appear as planned work |

## Verdict

**READY TO BUILD** -- The 1 blocker (test disposition mismatch) was resolved inline: changed UPDATE to REPLACE for `test_artifact_inference_failure_falls_back_to_stored_state` and `test_session_no_slug_no_stage_states_produces_empty_stages`. Documentation nit also fixed. Concerns are acknowledged risks, not plan defects.

---

## Open Questions

No open questions — the issue is well-scoped with a clear solution path.
