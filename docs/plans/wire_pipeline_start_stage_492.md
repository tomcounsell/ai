---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/492
last_comment_id:
---

# Wire PipelineStateMachine.start_stage() into Production SDLC Dispatch

## Problem

PipelineStateMachine has full start_stage/complete_stage/fail_stage logic, but start_stage() is only called in tests -- never in production. This means:

**Current behavior:**
- The PM (ChatSession) dispatches dev-sessions for SDLC stages via the Agent tool
- The `pre_tool_use` hook registers the DevSession in Redis (`_maybe_register_dev_session`)
- The `subagent_stop` hook fires when the dev-session returns and tries to find the current in_progress stage
- But no stage was ever marked in_progress, so `sm.current_stage()` returns None
- `stage_states` remains None on all production sessions

**Desired outcome:**
- When the PM dispatches a dev-session for an SDLC stage, `start_stage()` is called on the parent session BEFORE the subagent spawns
- When the subagent completes, `subagent_stop` finds the in_progress stage and marks it completed
- `stage_states` is populated with correct state on production sessions

## Prior Art

- **Issue #488**: Original PipelineStateMachine implementation -- built the state machine but did not wire the write path
- **Issue #489**: Discovered the gap during E2E observation -- confirmed zero stage completions in production logs
- **Issue #490**: SDLC stage tracking consolidation (open, may overlap)

## Data Flow

1. **Entry point**: Human message arrives via Telegram, ChatSession (PM) assesses SDLC state
2. **PM dispatches**: PM uses Agent tool with type="dev-session" and a prompt containing the stage assignment (e.g., "Stage: BUILD")
3. **PreToolUse hook fires**: `pre_tool_use_hook()` detects `tool_name == "Agent"` and `type == "dev-session"`, calls `_maybe_register_dev_session()` -- **THIS IS WHERE start_stage() MUST BE ADDED**
4. **Dev-session runs**: Subagent executes the stage work
5. **SubagentStop hook fires**: `subagent_stop_hook()` calls `_record_stage_on_parent()` which loads PipelineStateMachine and calls `sm.current_stage()` to find the in_progress stage, then `sm.complete_stage()`
6. **Output**: `stage_states` on parent ChatSession is updated with completed stage

## Architectural Impact

- **No new dependencies**: Uses existing PipelineStateMachine and AgentSession
- **Interface changes**: None -- start_stage() already exists, just needs to be called
- **Coupling**: Adds coupling between pre_tool_use hook and PipelineStateMachine (acceptable -- subagent_stop already has this coupling)
- **Reversibility**: Trivially reversible -- remove the start_stage() call

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Single-file change with test additions. The state machine and hooks already exist; this wires them together.

## Prerequisites

No prerequisites -- this work modifies existing internal code with no external dependencies.

## Solution

### Key Elements

- **PreToolUse hook enhancement**: Add `start_stage()` call inside `_maybe_register_dev_session()` in `agent/hooks/pre_tool_use.py`
- **Stage extraction from prompt**: Parse the stage name from the dev-session prompt text (the PM includes stage assignment like "Stage: PLAN" or "Stage to execute -- PLAN")
- **PipelineStateMachine initialization**: Ensure the parent session has stage_states initialized before calling start_stage()

### Flow

PM dispatches dev-session -> PreToolUse hook fires -> Extract stage from prompt -> Find parent ChatSession -> `sm.start_stage(STAGE)` -> Dev-session runs -> SubagentStop hook fires -> `sm.complete_stage(STAGE)` -> stage_states updated

### Technical Approach

1. In `_maybe_register_dev_session()` (agent/hooks/pre_tool_use.py), after registering the DevSession, call `start_stage()` on the parent ChatSession's PipelineStateMachine
2. Extract the SDLC stage from the prompt text using pattern matching against the known SDLC_STAGES list. The PM includes the stage name in the prompt when dispatching (e.g., "Stage: BUILD", "Stage to execute -- BUILD")
3. If no stage can be extracted from the prompt, log a warning and skip -- do not block the tool call
4. Handle the case where stage_states is None (fresh session) by letting PipelineStateMachine.__init__ initialize defaults

**Stage extraction strategy**: Scan the prompt for known SDLC stage names (ISSUE, PLAN, CRITIQUE, BUILD, TEST, PATCH, REVIEW, DOCS, MERGE). Look for patterns like "Stage: X", "Stage to execute -- X", or just the uppercase stage name near keywords like "stage", "execute", "dispatch". Use the first match. This is robust because the PM always names the stage explicitly in the dev-session prompt.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_maybe_register_dev_session` function already has a try/except -- the new start_stage() call must be inside the same error boundary so failures do not block the Agent tool call
- [ ] Add test asserting that start_stage() failure (e.g., invalid stage) logs a warning but does not raise

### Empty/Invalid Input Handling
- [ ] Test with empty prompt text (no stage extractable) -- should log warning, skip start_stage()
- [ ] Test with prompt text that has no recognizable stage name -- should log warning, skip
- [ ] Test with prompt containing multiple stage names -- should pick the first one mentioned in the "Stage:" pattern

### Error State Rendering
- [ ] No user-visible output from this change -- all errors are logged server-side

## Test Impact

- [ ] `tests/unit/test_subagent_stop_hook.py` -- UPDATE: tests for `_record_stage_on_parent` may need updated fixtures that have stage_states pre-populated (since start_stage now runs before stop)
- [ ] `tests/unit/test_pipeline_state_machine.py` -- No changes needed, existing tests cover start_stage/complete_stage behavior

## Rabbit Holes

- **Outcome classification in subagent_stop**: The subagent_stop hook currently always calls `complete_stage()`. Adding `fail_stage()` based on outcome classification (using `classify_outcome()`) is a separate concern -- do not add it in this PR
- **SubagentStart hook in the SDK**: The Claude Agent SDK does not expose a SubagentStart hook. Do not attempt to add one -- use the PreToolUse hook which already fires at the right time
- **Stage extraction via LLM**: Do not use an LLM call to extract the stage from the prompt. Simple pattern matching is sufficient and deterministic

## Risks

### Risk 1: Stage extraction fails on some prompt formats
**Impact:** start_stage() not called, stage_states remains None (same as current broken behavior -- no regression)
**Mitigation:** Log warnings when extraction fails so we can iterate on patterns. Make extraction best-effort, not blocking.

### Risk 2: start_stage() raises ValueError for invalid stage ordering
**Impact:** Could block dev-session spawn if not caught
**Mitigation:** Wrap start_stage() in try/except, log the error, and let the Agent tool call proceed regardless

## Race Conditions

No race conditions identified -- PreToolUse hook runs synchronously before the subagent spawns, and SubagentStop runs synchronously after it returns. There is no concurrent access to stage_states between these two points.

## No-Gos (Out of Scope)

- Adding fail_stage() wiring based on outcome classification (separate issue)
- Creating a SubagentStart hook type in the SDK
- Modifying the SDLC skill or PM prompt to change how stages are dispatched
- Changing the subagent_stop hook logic (it already works correctly when start_stage has been called)

## Update System

No update system changes required -- this is a bridge-internal change that modifies hook behavior. No new dependencies, no config changes.

## Agent Integration

No agent integration required -- this change operates within the hook system that runs alongside the agent. No MCP server changes, no bridge imports, no new tools.

## Documentation

- [ ] Update `docs/features/pipeline-state-machine.md` to document the production wiring (currently only describes the state machine API, not where it is called)
- [ ] Add inline code comments in the modified hook function explaining the start_stage() call

## Success Criteria

- [ ] `start_stage()` called in production when PM dispatches a dev-session for an SDLC stage
- [ ] After dev-session returns, `stage_states` on parent session is non-null with correct state
- [ ] Bridge logs show `[subagent_stop] Recorded stage completion: <STAGE>` after dev-session completes
- [ ] Bridge logs show `[subagent_stop] Injecting stage state` with populated state dict
- [ ] Existing tests continue to pass
- [ ] New tests verify the wiring works end-to-end (start -> stop -> state updated)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hook-wiring)**
  - Name: hook-builder
  - Role: Add start_stage() call to pre_tool_use hook and stage extraction logic
  - Agent Type: builder
  - Resume: true

- **Validator (hook-wiring)**
  - Name: hook-validator
  - Role: Verify start_stage() is called and stage_states is populated
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add stage extraction helper and start_stage() call to PreToolUse hook
- **Task ID**: build-hook-wiring
- **Depends On**: none
- **Validates**: tests/unit/test_pre_tool_use_start_stage.py (create)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_extract_stage_from_prompt(prompt: str) -> str | None` helper to `agent/hooks/pre_tool_use.py` that scans prompt text for SDLC stage names
- Add `_start_pipeline_stage(parent_session_id: str, stage: str) -> None` helper that loads the parent AgentSession, creates PipelineStateMachine, and calls start_stage()
- Call `_start_pipeline_stage()` from `_maybe_register_dev_session()` after the DevSession is registered
- Wrap in try/except to ensure failures never block the Agent tool call
- Create `tests/unit/test_pre_tool_use_start_stage.py` with tests for stage extraction and start_stage wiring

### 2. Validate end-to-end flow
- **Task ID**: validate-e2e
- **Depends On**: build-hook-wiring
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify _extract_stage_from_prompt handles: explicit "Stage: BUILD", "Stage to execute -- BUILD", prompt with no stage, empty prompt
- Verify _start_pipeline_stage correctly initializes PipelineStateMachine and calls start_stage
- Verify that after start + stop sequence, stage_states is populated on the parent session
- Verify existing tests in test_subagent_stop_hook.py and test_pipeline_state_machine.py still pass

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-e2e
- **Assigned To**: hook-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pipeline-state-machine.md` to document production wiring
- Add entry to `docs/features/README.md` index table if not already present

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` to verify all unit tests pass
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Stage extraction works | `python -c "from agent.hooks.pre_tool_use import _extract_stage_from_prompt; assert _extract_stage_from_prompt('Stage: BUILD') == 'BUILD'"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the approach is straightforward and all components already exist. The only implementation detail is the exact regex/pattern for extracting stage names from the prompt, which can be iterated on based on actual PM prompt formats.
