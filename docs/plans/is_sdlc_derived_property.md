---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-17
tracking: https://github.com/tomcounsell/ai/issues/436
last_comment_id:
---

# Make is_sdlc a Derived Property from Stage Progress

## Problem

`is_sdlc_job()` on `AgentSession` relies on `classification_type == "sdlc"` as the primary signal, with a fallback that parses `[stage]` entries in history. This creates a disconnect:

**Current behavior:**
- The classifier must explicitly set `classification_type = "sdlc"` at routing time
- If classification is missed or happens too late, `is_sdlc_job()` returns `False` even when stages are actively running
- The summarizer then renders Chat format instead of the SDLC template
- Multiple prior fixes (#246, #276, #279, #284, #285) tried to patch the timing gap without addressing the root cause

**Desired outcome:**
- SDLC status is derived from observable state (stage progress), not from a stored flag
- If any stage has started, the session is SDLC — no flag needed, no race condition possible
- Clean `@property` interface: `session.is_sdlc` instead of `session.is_sdlc_job()`

## Prior Art

- **Issue #246 / PR #247**: "Force AgentSession into SDLC mode at classification time" — added the `classification_type` field as the primary signal. Addressed the symptom (missing flag) rather than making SDLC status self-describing.
- **Issue #276 / PR #284**: "Fix SDLC session tracking: classifier type + auto-continue propagation" — patched propagation of `classification_type` through auto-continue chains. Another symptom fix.
- **Issue #279**: "send_to_chat uses stale in-memory AgentSession for SDLC routing decisions" — stale session objects didn't reflect the flag set by another code path.
- **Issue #285 / PR #286**: "AgentSession as single source of truth for auto-continue" — stopped creating duplicate records, reducing flag-loss scenarios.
- **Issue #374**: "Observer returns early on continuation sessions due to session cross-wire" — classification mismatch causing observer to bail.
- **Issue #375**: "Observer: classification race, stage detector drops typed outcomes" — explicitly identified the classification race condition this issue resolves.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #247 | Added `classification_type` field, set at routing | Relies on classification happening before `is_sdlc_job()` is called — timing-dependent |
| PR #284 | Propagated `classification_type` through auto-continues | Still depends on the flag being set correctly upstream — doesn't handle new race windows |
| PR #286 | Single session record to prevent flag loss on duplicates | Reduced but didn't eliminate the timing gap |

**Root cause pattern:** Every fix tries to ensure a stored flag is set "early enough." The fundamental issue is that SDLC-ness is being treated as a flag to toggle rather than a property to observe. The correct fix is to derive SDLC status from the stage progress data that already exists.

## Data Flow

1. **Entry point**: Message arrives → classifier determines type → sets `classification_type` on session
2. **SDLC routing**: Observer/auto-continue reads `is_sdlc_job()` to decide routing strategy
3. **Stage progress**: Skills call `session_progress()` / `PipelineStateMachine` → writes `[stage]` history entries and/or `stage_states` JSON
4. **Decision point**: `is_sdlc_job()` checks `classification_type` first, then falls back to history parsing

**The fix moves the decision point** to step 3's output: if `stage_states` has any non-pending stage OR history has `[stage]` entries → it's SDLC. No dependency on step 1.

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

- **`is_sdlc` property**: Replaces `is_sdlc_job()` method. Derives status from `stage_states` (primary) and history `[stage]` entries (fallback), plus `classification_type` as a tertiary signal for freshly-classified sessions that haven't started a stage yet.
- **Caller migration**: All 5 production callers updated from `session.is_sdlc_job()` to `session.is_sdlc`
- **Test migration**: All test references updated to use property syntax

### Flow

**Message arrives** → classifier sets `classification_type` → Observer calls `session.is_sdlc` → property checks `stage_states` / history / `classification_type` → returns True/False

### Technical Approach

1. Replace `is_sdlc_job()` method with `@property is_sdlc` on `AgentSession`
2. New property checks (in priority order):
   a. `stage_states` JSON — if any stage is not "pending" or "ready" (i.e., `in_progress`, `completed`, `failed`) → True
   b. History `[stage]` entries — legacy fallback, same as current
   c. `classification_type == "sdlc"` — tertiary signal for fresh sessions
3. Keep `classification_type` field for analytics — it just stops driving `is_sdlc` as the primary signal
4. Update all callers: method call `()` → property access

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `get_stage_progress()` already handles malformed history entries gracefully — no new exception paths added
- [ ] `PipelineStateMachine.__init__` handles invalid JSON `stage_states` — property delegates to same code path

### Empty/Invalid Input Handling
- [ ] Property returns `False` when session has no history, no `stage_states`, and no `classification_type`
- [ ] Property handles `None` stage_states gracefully
- [ ] Property handles empty history list gracefully

### Error State Rendering
- [ ] Not applicable — this is internal routing logic, not user-visible output

## Test Impact

- [ ] `tests/unit/test_sdlc_mode.py::TestIsSdlcJob` — UPDATE: rename test class, change `.is_sdlc_job()` to `.is_sdlc`, update assertions for new priority order (stage_states first)
- [ ] `tests/integration/test_stage_aware_auto_continue.py::TestIsSdlcJob` — UPDATE: all `.is_sdlc_job()` calls to `.is_sdlc`
- [ ] `tests/integration/test_agent_session_lifecycle.py` (lines 749, 798) — UPDATE: `.is_sdlc_job()` to `.is_sdlc`
- [ ] `tests/integration/test_enqueue_continuation.py` (lines 289, 306) — UPDATE: `.is_sdlc_job()` to `.is_sdlc`
- [ ] `tests/unit/test_observer.py` (line 46) — UPDATE: mock attribute name from `is_sdlc_job` to `is_sdlc`
- [ ] `tests/unit/test_stop_reason_observer.py` (line 24) — UPDATE: mock attribute name
- [ ] `tests/unit/test_summarizer.py` (multiple lines) — UPDATE: mock attribute name and call syntax
- [ ] `tests/e2e/test_message_pipeline.py` (line 178) — UPDATE: test name reference if applicable

## Rabbit Holes

- **Removing `classification_type` entirely**: Tempting but risky. Other code may use it for analytics. Keep it, just stop using it as the primary SDLC signal.
- **Rewriting `get_stage_progress()` to only use `stage_states`**: The history fallback is still needed for sessions that predate `PipelineStateMachine`. This is a separate cleanup.
- **Adding deprecation warnings for `is_sdlc_job()`**: Not worth it — this is internal code with no external consumers.

## Risks

### Risk 1: Fresh SDLC sessions return `is_sdlc = False` before first stage starts
**Impact:** A session classified as SDLC but with no stage started yet would return False, changing routing behavior.
**Mitigation:** Keep `classification_type == "sdlc"` as a tertiary check. This preserves current behavior for the window between classification and first stage start.

### Risk 2: Mock-heavy tests break due to property vs method change
**Impact:** Tests that mock `is_sdlc_job` as a method will fail.
**Mitigation:** Update all test mocks. Use `PropertyMock` or set the attribute directly for mock sessions.

## Race Conditions

No race conditions identified — the property reads existing persisted state (`stage_states`, `history`, `classification_type`). All three are written by other code paths before `is_sdlc` is called. The entire point of this change is to eliminate the timing race that exists with the current flag-based approach.

## No-Gos (Out of Scope)

- Removing `classification_type` field entirely
- Rewriting `get_stage_progress()` or `PipelineStateMachine`
- Changing the observer or auto-continue logic beyond updating the caller syntax
- Adding backwards-compatible `is_sdlc_job()` shim method

## Update System

No update system changes required — this is a bridge-internal refactoring with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. The `is_sdlc` property is only called by bridge/observer/summarizer code, not by MCP tools or agent-facing interfaces.

## Documentation

- [ ] Update `docs/features/session-isolation.md` if it references `is_sdlc_job()`
- [ ] Update inline docstrings on `AgentSession` class
- [ ] No new feature doc needed — this is a refactoring of existing internals

## Success Criteria

- [ ] `is_sdlc_job()` method replaced with `@property is_sdlc` on `AgentSession`
- [ ] Property derives SDLC status from `stage_states` (primary), history (secondary), `classification_type` (tertiary)
- [ ] All 5 production callers updated: `bridge/response.py`, `bridge/summarizer.py`, `bridge/observer.py`, `agent/job_queue.py`, `scripts/reflections.py`
- [ ] All test files updated to use property syntax
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`ruff check`, `ruff format`)

## Team Orchestration

### Team Members

- **Builder (refactor)**
  - Name: sdlc-property-builder
  - Role: Replace method with property, update all callers and tests
  - Agent Type: builder
  - Resume: true

- **Validator (refactor)**
  - Name: sdlc-property-validator
  - Role: Verify property behavior, caller migration completeness, test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Replace `is_sdlc_job()` with `is_sdlc` property
- **Task ID**: build-property
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_mode.py, tests/integration/test_stage_aware_auto_continue.py
- **Assigned To**: sdlc-property-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `is_sdlc_job()` method in `models/agent_session.py` with `@property is_sdlc`
- New property checks: (1) `stage_states` via `PipelineStateMachine` — any stage `in_progress`/`completed`/`failed`, (2) history `[stage]` entries, (3) `classification_type == "sdlc"`
- Update all 5 production callers from `.is_sdlc_job()` to `.is_sdlc`
- Update all test files: change method calls to property access, update mock setups
- Run `ruff format . && ruff check .` to ensure lint clean

### 2. Validate migration completeness
- **Task ID**: validate-property
- **Depends On**: build-property
- **Assigned To**: sdlc-property-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep for any remaining `is_sdlc_job` references (should be zero outside `.claude/worktrees/`)
- Verify property returns correct values for: no-state session, classified-only session, stage-active session
- Run full test suite
- Verify lint clean

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No old method refs | `grep -rn 'is_sdlc_job' --include='*.py' . \| grep -v worktrees \| grep -v '.pyc'` | exit code 1 |

---

## Open Questions

No open questions — the issue description is comprehensive and the solution is well-scoped.
