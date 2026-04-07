---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/759
---

# Fix get_stage_progress() broken API contract

## Problem

`AgentSession.get_stage_progress()` calls `PipelineStateMachine.get_display_progress(slug=slug)`, but PR #733 removed the `slug` parameter from `get_display_progress()`. Any code path invoking `session.get_stage_progress()` now raises:

```
TypeError: PipelineStateMachine.get_display_progress() got an unexpected keyword argument 'slug'
```

**Current behavior:** 9 integration tests fail in `tests/integration/test_agent_session_lifecycle.py`. The dashboard and summarizer emoji rendering silently degrade — `_get_status_emoji()` in `bridge/summarizer.py` swallows the exception and returns an empty string instead of the checkmark for completed sessions.

**Desired outcome:** `get_stage_progress()` delegates cleanly, the `slug` parameter is removed (vestigial since PR #733), and all 9 failing tests pass.

## Prior Art

- **PR #733** ("fix: remove artifact inference from pipeline state, add skill stage markers") — Removed `slug` parameter from `get_display_progress()` but missed updating the `AgentSession.get_stage_progress()` caller. This is the direct cause of the bug.

## Data Flow

1. **Entry point:** Test / dashboard / summarizer calls `session.get_stage_progress()`.
2. **AgentSession (models/agent_session.py:1172):** Builds a `PipelineStateMachine` for the session and calls `sm.get_display_progress(slug=slug)` — this throws `TypeError`.
3. **Failure propagation:** `bridge/summarizer.py:_get_status_emoji()` catches the exception and returns `""`, so downstream display is silently wrong.

## Architectural Impact

- **Interface changes:** `AgentSession.get_stage_progress()` loses its `slug` parameter. Nothing in the tree currently passes `slug=`, so this is non-breaking in practice.
- **Coupling:** No change.
- **Reversibility:** Trivially reversible (one-line revert).

## Appetite

**Size:** Small
**Team:** Solo dev
**Interactions:** PM check-ins 0, Review rounds 1.

## Prerequisites

No prerequisites — this is a pure code fix with no external dependencies.

## Solution

### Key Elements

- **models/agent_session.py:** Drop the vestigial `slug` parameter from `get_stage_progress()` and call `sm.get_display_progress()` with no kwargs.

### Technical Approach

1. Change signature: `def get_stage_progress(self) -> dict[str, str]:`
2. Change call site: `return sm.get_display_progress()`
3. Leave docstring intact (no mention of `slug` to update — verify).

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `bridge/summarizer.py:_get_status_emoji()` swallows exceptions from `get_stage_progress()`. After the fix, the checkmark emoji path is exercised by the existing 3 cascade tests — no new exception-handler tests needed.

### Empty/Invalid Input Handling
- [x] `get_stage_progress()` takes no inputs after the fix — N/A.

### Error State Rendering
- [x] Existing `TestStageProgress` suite already covers stage rendering paths.

## Test Impact

- [x] `tests/integration/test_agent_session_lifecycle.py::TestStageProgress` (5 tests) — UPDATE not required; they already call `session.get_stage_progress()` with no args. They will pass once the TypeError is fixed.
- [x] `tests/integration/test_agent_session_lifecycle.py::TestSDLCLifecycle` (1 test) — Same as above.
- [x] 3 summarizer cascade tests relying on `_get_status_emoji()` returning the checkmark — will pass once the underlying call stops raising.

No test files need edits. All 9 failures resolve from the production code fix alone.

## Rabbit Holes

- Do NOT reintroduce artifact inference or any form of slug-based lookup in `PipelineStateMachine` — that path was intentionally removed in PR #733.
- Do NOT refactor `_get_status_emoji()` to stop swallowing exceptions in this PR — that is a separate concern (tracked separately if needed).

## Risks

### Risk 1: A hidden caller passes `slug=` positionally or by keyword
**Impact:** New `TypeError` after removing the parameter.
**Mitigation:** Already verified — `grep -rn "get_stage_progress(" --include="*.py"` shows zero callers passing any argument. Build step will re-verify.

## Race Conditions

No race conditions identified — this is a synchronous single-threaded API contract fix.

## No-Gos (Out of Scope)

- Fixing the silent-swallow in `_get_status_emoji()`.
- Broader refactor of `PipelineStateMachine` or `AgentSession` stage APIs.
- Backfilling stage state for existing sessions.

## Update System

No update system changes required — this is a pure Python code fix deployed via normal git pull.

## Agent Integration

No agent integration required — `get_stage_progress()` is an internal Python API consumed by tests, the dashboard, and the summarizer. Not exposed via MCP.

## Documentation

No documentation changes needed — this is a bug fix restoring an existing API contract. No new feature, no behavior change from the documented perspective. The `slug` parameter was never documented as a public affordance (it was vestigial from pre-#733).

## Success Criteria

- [x] `AgentSession.get_stage_progress()` signature takes no arguments beyond `self`.
- [x] `models/agent_session.py:1183` calls `sm.get_display_progress()` without kwargs.
- [x] `pytest tests/integration/test_agent_session_lifecycle.py` — all 9 previously failing tests pass.
- [x] `grep -rn "get_stage_progress(" --include="*.py"` confirms no caller passes `slug=`.
- [x] `python -m ruff format .` clean.

## Team Orchestration

### Team Members

- **Builder (agent-session-fix)**
  - Name: agent-session-builder
  - Role: Apply the two-line fix in `models/agent_session.py`
  - Agent Type: builder
  - Resume: true

- **Validator (lifecycle-tests)**
  - Name: lifecycle-validator
  - Role: Run the integration test file and confirm all 9 failures pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix AgentSession.get_stage_progress
- **Task ID**: build-agent-session-fix
- **Depends On**: none
- **Validates**: tests/integration/test_agent_session_lifecycle.py
- **Assigned To**: agent-session-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `models/agent_session.py` line 1172: remove `slug: str | None = None` parameter.
- Edit `models/agent_session.py` line 1183: change `sm.get_display_progress(slug=slug)` to `sm.get_display_progress()`.
- Verify docstring has no stale `slug` reference.

### 2. Validate fix
- **Task ID**: validate-lifecycle
- **Depends On**: build-agent-session-fix
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_agent_session_lifecycle.py -v`.
- Confirm all 9 previously failing tests pass.
- Run `grep -rn "get_stage_progress(" --include="*.py"` to confirm no caller passes `slug=`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lifecycle tests pass | `pytest tests/integration/test_agent_session_lifecycle.py -q` | exit code 0 |
| No slug= callers | `grep -rn "get_stage_progress(slug" --include="*.py"` | exit code 1 |
| Format clean | `python -m ruff format --check models/agent_session.py` | exit code 0 |
