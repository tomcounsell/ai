---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/638
---

# Document and Test Parent-Child Session Hook Lifecycle

## Problem

The parent-child session lifecycle is production-ready but has documentation and test coverage gaps.

**Current behavior:**

1. `chat-dev-session-architecture.md` covers session types and routing but has zero mentions of the PreToolUse or SubagentStop hooks that drive the parent-child lifecycle. A reader understands what sessions are, but not how they get created or how results flow back.

2. There is no integration test exercising the full round-trip: parent PM session spawns child via PreToolUse, child runs, SubagentStop completes child, parent stage progresses. Each component is tested in isolation but no test verifies they work together.

**Desired outcome:**

- Architecture doc includes a hook-driven lifecycle section with ASCII diagram
- A round-trip integration test proves the hooks work end-to-end with real Redis
- Any additional gaps found during exploration are addressed

## Prior Art

- **Issue #492**: Wired `PipelineStateMachine.start_stage()` into the production SDLC dispatch path
- **Issue #467**: Pipeline cleanup + e2e tests, added `test_session_spawning.py` and `test_context_propagation.py`
- **Issue #597**: Session registry solving UUID-to-session_id mapping, documented in `session-isolation.md`

## Data Flow

The parent-child hook lifecycle flows as follows:

1. **PM session dispatches work** -- PM calls Agent tool with `type="dev-session"` and a prompt containing `Stage: BUILD` (or similar)
2. **PreToolUse hook fires** -- `pre_tool_use_hook()` detects `tool_name == "Agent"` with `type="dev-session"`, calls `_maybe_register_dev_session()` which:
   - Uses `session_registry.resolve(claude_uuid)` to find the bridge session ID
   - Creates a child AgentSession via `AgentSession.create_child(role="dev", ...)`
   - Extracts the SDLC stage from the prompt via `_extract_stage_from_prompt()`
   - Calls `_start_pipeline_stage()` which creates a `PipelineStateMachine` and calls `start_stage()`
3. **DevSession executes** -- The child agent runs, doing actual work (building, testing, etc.)
4. **SubagentStop hook fires** -- `subagent_stop_hook()` detects `agent_type == "dev-session"`, calls `_register_dev_session_completion()` which:
   - Resolves the parent session via `session_registry.resolve(claude_uuid)`
   - Marks the DevSession as completed in Redis
   - Extracts output tail for outcome classification
   - Calls `_record_stage_on_parent()` which creates a `PipelineStateMachine`, calls `classify_outcome()`, and routes to `complete_stage()` or `fail_stage()`
5. **PM receives pipeline state** -- The hook returns `{"reason": "Pipeline state: ..."}` injecting stage state back into the PM's context

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies beyond Redis (already available in test environment).

## Solution

### Key Elements

- **Architecture doc update**: Add a "Hook-Driven Lifecycle" section to `chat-dev-session-architecture.md` with an ASCII diagram showing the full spawn-execute-return flow
- **Round-trip integration test**: Create `tests/integration/test_parent_child_round_trip.py` that exercises the full lifecycle with real Redis
- **Cross-references**: Link to `session-isolation.md` for registry details and `pipeline_state.py` for stage tracking

### Technical Approach

1. **Doc update** -- Surgical addition of a new section after "Stage-by-Stage Orchestration". Include:
   - ASCII diagram showing: PreToolUse detects Agent tool -> `create_child()` -> agent executes -> SubagentStop -> `classify_outcome()` -> `complete_stage()`/`fail_stage()` on parent
   - Cross-reference to `session-isolation.md` for session registry details
   - Mention of outcome classification patterns

2. **Integration test** -- Real Redis test that:
   - Creates a PM session (AgentSession with `session_type="pm"`)
   - Registers it in the session registry
   - Simulates PreToolUse hook creating a DevSession with stage extraction
   - Simulates SubagentStop hook completing the DevSession
   - Verifies parent `stage_states` show the stage as completed
   - Tests both success and failure outcome paths

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The hooks have extensive try/except blocks that log warnings. The integration test will verify that failures in one hook do not crash the other, and that stage state remains consistent even on partial failures.

### Empty/Invalid Input Handling
- [ ] Test with empty prompt (no stage extractable) -- verify graceful skip
- [ ] Test with missing session registry entry -- verify graceful skip

### Error State Rendering
- Not applicable -- no user-visible output in this work

## Test Impact

No existing tests affected -- this is additive work creating a new integration test file. Existing unit tests in `test_subagent_stop_hook.py` and `test_pre_tool_use_start_stage.py` remain valid and unchanged since we are not modifying any production code.

## Rabbit Holes

- Do not refactor the existing hook code; this is docs + tests only
- Do not add mocks for Redis; use real Redis consistent with project testing philosophy
- Do not rewrite the architecture doc; make surgical additions only

## Risks

### Risk 1: Redis availability in CI
**Impact:** Integration test fails in CI if Redis is not running
**Mitigation:** Use the same Redis connection patterns as existing integration tests (e.g., `test_redis_models.py`). Mark test with appropriate pytest markers.

## Race Conditions

No race conditions identified -- integration test is single-threaded and sequential by design. The hooks themselves are designed for single-threaded asyncio.

## No-Gos (Out of Scope)

- No production code changes
- No refactoring of hooks or pipeline state machine
- No changes to session registry internals
- No new MCP servers or tools

## Update System

No update system changes required -- this is purely documentation and test additions.

## Agent Integration

No agent integration required -- this is documentation and test work that does not add any new agent-facing functionality.

## Documentation

- [ ] Update `docs/features/chat-dev-session-architecture.md` with hook-driven lifecycle section and ASCII diagram
- [ ] Add cross-references to `session-isolation.md` for session registry details

## Success Criteria

- [ ] `chat-dev-session-architecture.md` contains a "Hook-Driven Lifecycle" section explaining the spawn-execute-return flow
- [ ] ASCII diagram shows PreToolUse -> create child -> SubagentStop -> update parent stage
- [ ] Cross-reference to `session-isolation.md` for session registry details
- [ ] Integration test exercises full round-trip with real Redis (not mocks)
- [ ] Integration test verifies parent `stage_states` updates after child completion
- [ ] Integration test covers both success and failure outcome paths
- [ ] All existing tests still pass
- [ ] Lint and format checks pass

## Team Orchestration

### Team Members

- **Builder (docs-and-test)**
  - Name: docs-test-builder
  - Role: Write documentation update and integration test
  - Agent Type: builder
  - Resume: true

- **Validator (round-trip)**
  - Name: round-trip-validator
  - Role: Verify test passes and documentation is accurate
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update Architecture Documentation
- **Task ID**: build-docs
- **Depends On**: none
- **Validates**: docs/features/chat-dev-session-architecture.md contains "Hook-Driven Lifecycle"
- **Assigned To**: docs-test-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "Hook-Driven Lifecycle" section after "Stage-by-Stage Orchestration" section
- Include ASCII diagram showing the full PreToolUse -> DevSession -> SubagentStop -> stage update flow
- Add cross-reference to `session-isolation.md` for session registry
- Mention outcome classification (success/fail/ambiguous routing)

### 2. Create Round-Trip Integration Test
- **Task ID**: build-test
- **Depends On**: none
- **Validates**: tests/integration/test_parent_child_round_trip.py passes with `pytest tests/integration/test_parent_child_round_trip.py -v`
- **Assigned To**: docs-test-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/integration/test_parent_child_round_trip.py`
- Test full lifecycle: create PM session -> register in session registry -> simulate PreToolUse -> simulate SubagentStop -> verify stage_states
- Test success path (stage completed)
- Test failure path (stage failed)
- Test edge cases: empty prompt, missing registry entry

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-docs, build-test
- **Assigned To**: round-trip-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_parent_child_round_trip.py -v`
- Run `python -m ruff check .`
- Run `python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_parent_child_round_trip.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Doc section exists | `grep -c "Hook-Driven Lifecycle" docs/features/chat-dev-session-architecture.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---
