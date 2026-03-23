---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/471
last_comment_id:
---

# Test Coverage Gaps: Nudge Loop, Cross-Project Routing, Revival Path

## Problem

PR #470 (pipeline cleanup) removed the simple session type, workflow_id, and dead code. Three areas of existing code affected by these changes lack test coverage.

**Current behavior:**
- Revival path in `bridge/catchup.py` calls `enqueue_job` without `workflow_id` kwarg (removed in #470) but no test verifies the path still works. Broken revival = abandoned sessions stay abandoned silently.
- Cross-project "Dev:" routing has no test confirming "Dev: Popoto" resolves to `project_key="popoto"` vs "Dev: Valor" to `project_key="valor"`.
- Nudge loop removed `is_simple_session` fast-path. No test confirms non-SDLC Q&A messages still deliver correctly through the full nudge logic.

**Desired outcome:**
Three focused tests covering each gap, all passing.

## Prior Art

- **PR #470**: Pipeline cleanup — removed simple session type, workflow_id, dead code. The direct cause of these gaps.
- **Issue #459**: Nudge loop implementation — established the current nudge model but focused on SDLC behavior.

## Solution

### Key Elements

- **Revival test**: Create an abandoned AgentSession in Redis, call `scan_for_missed_messages` with mocked Telegram client, verify `enqueue_job_fn` is called with correct `session_type` and without `workflow_id`.
- **Cross-project routing test**: Set up `GROUP_TO_PROJECT` with multiple projects from `sample_config`, call `find_project_for_chat` with "Dev: Popoto" and "Dev: Valor", verify correct `_key` and project config returned.
- **Nudge loop delivery test**: Mock `get_stop_reason` to return `"end_turn"`, call `send_to_chat` with a non-SDLC Q&A response, verify `send_cb` is called (delivery) not `_enqueue_nudge` (nudge).

### Technical Approach

- Revival test: Integration test using real Redis (via `redis_test_db` fixture). Mock the Telegram client's `get_dialogs` and `get_messages` to return synthetic messages. Mock `should_respond_fn` to return True. Verify `enqueue_job_fn` is called with correct params.
- Cross-project routing test: Unit test. Set `routing.GROUP_TO_PROJECT` and `routing.ACTIVE_PROJECTS` directly from `sample_config` fixture, then call `find_project_for_chat`.
- Nudge loop test: Unit test. Construct a `_RedisJob` with a Q&A message, mock `get_stop_reason` → `"end_turn"`, mock `send_cb`, verify delivery happens on first call.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handlers introduced — these are test-only changes

### Empty/Invalid Input Handling
- [ ] Nudge loop test covers empty output case (already tested in `test_nudge_loop.py` constants, but behavioral test added)

### Error State Rendering
- [ ] Not applicable — no user-visible output changes

## Test Impact

No existing tests affected — this is purely additive test coverage. No existing behavior or interfaces are modified.

## Rabbit Holes

- Do NOT mock the entire nudge loop end-to-end with real Claude API calls — that's e2e territory
- Do NOT test Telegram client authentication or real network calls in catchup test
- Do NOT expand scope to test all routing edge cases (team chats, DMs, etc.) — focus only on the cross-project "Dev:" resolution gap

## Risks

### Risk 1: Catchup test fragility from mocking Telegram client
**Impact:** Test breaks when Telegram client API changes
**Mitigation:** Mock at the function boundary (`enqueue_job_fn`, `should_respond_fn`) not deep internals

## Race Conditions

No race conditions identified — all tests are single-threaded with mocked async operations.

## No-Gos (Out of Scope)

- Full e2e nudge loop testing with real agent execution
- Testing Telegram client authentication
- Expanding routing test coverage beyond "Dev:" prefix resolution
- Modifying any production code

## Update System

No update system changes required — this is purely test code.

## Agent Integration

No agent integration required — test-only changes with no bridge or tool modifications.

## Documentation

- [ ] Update `tests/README.md` if new test markers are added
- [ ] Inline docstrings on each new test class explaining what gap it covers

## Success Criteria

- [ ] Revival path test: abandoned session -> catchup -> re-enqueued with correct session_type, no workflow_id
- [ ] Cross-project routing test: "Dev: Popoto" -> project_key="popoto", "Dev: Valor" -> project_key="valor"
- [ ] Nudge loop test: non-SDLC Q&A message delivers via send_cb without nudging
- [ ] All tests pass (`pytest tests/ -x -q`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (tests)**
  - Name: test-builder
  - Role: Write all three test files
  - Agent Type: test-writer
  - Resume: true

- **Validator (tests)**
  - Name: test-validator
  - Role: Verify tests pass and cover the gaps
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Write cross-project routing test
- **Task ID**: build-routing-test
- **Depends On**: none
- **Validates**: tests/unit/test_cross_project_routing.py (create)
- **Assigned To**: test-builder
- **Agent Type**: test-writer
- **Parallel**: true
- Create `tests/unit/test_cross_project_routing.py`
- Test `find_project_for_chat("Dev: Popoto")` returns project with `_key="popoto"`
- Test `find_project_for_chat("Dev: Valor")` returns project with `_key="valor"`
- Test `find_project_for_chat("Dev: Django Template")` returns django project
- Use `sample_config` fixture, set `routing.GROUP_TO_PROJECT` via `build_group_to_project_map`

### 2. Write revival path test
- **Task ID**: build-revival-test
- **Depends On**: none
- **Validates**: tests/integration/test_catchup_revival.py (create)
- **Assigned To**: test-builder
- **Agent Type**: test-writer
- **Parallel**: true
- Create `tests/integration/test_catchup_revival.py`
- Mock Telegram client with synthetic dialogs and messages
- Mock `should_respond_fn` to return `(True, False)`
- Mock `is_duplicate_message` to return False
- Call `scan_for_missed_messages` and verify `enqueue_job_fn` called with correct kwargs
- Assert `workflow_id` is NOT in the kwargs (it was removed)
- Assert `session_type` is not passed or defaults correctly

### 3. Write nudge loop delivery test
- **Task ID**: build-nudge-test
- **Depends On**: none
- **Validates**: tests/unit/test_nudge_loop.py (update — add new test class)
- **Assigned To**: test-builder
- **Agent Type**: test-writer
- **Parallel**: true
- Add `TestNonSdlcDelivery` class to existing `tests/unit/test_nudge_loop.py`
- Test that a Q&A message with `stop_reason="end_turn"` triggers delivery (send_cb called)
- Test that no nudge is enqueued for simple Q&A completion
- Verify `auto_continue_count` stays at 0 after delivery

### 4. Validate all tests
- **Task ID**: validate-tests
- **Depends On**: build-routing-test, build-revival-test, build-nudge-test
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_cross_project_routing.py tests/integration/test_catchup_revival.py tests/unit/test_nudge_loop.py -v`
- Verify all tests pass
- Verify lint clean: `python -m ruff check tests/unit/test_cross_project_routing.py tests/integration/test_catchup_revival.py tests/unit/test_nudge_loop.py`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-tests
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify no regressions

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New routing test | `pytest tests/unit/test_cross_project_routing.py -v` | exit code 0 |
| New revival test | `pytest tests/integration/test_catchup_revival.py -v` | exit code 0 |
| Updated nudge test | `pytest tests/unit/test_nudge_loop.py -v` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
