---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/489
last_comment_id: 4110267149
---

# Validate SDLC Stage Injection and Watchdog Kill Mechanism

## Problem

The PM (PM session) was fabricating stage completion -- claiming "review passed" without running `/do-pr-review`. Two mechanisms were shipped directly to main to fix this, but were committed without a plan, PR, or comprehensive validation pass.

**Current behavior:**
Code is on main across 4 commits (c7e5a55d, 9829690d, a96cb432, 7e503655). Partial docs exist at `docs/features/sdlc-pipeline-integrity.md` (section D). Unit tests exist for `subagent_stop_hook` and `health_check`, but no integration-level validation confirms the end-to-end flow works.

**Desired outcome:**
A PR that bundles validation tests, confirms existing test coverage is adequate, ensures docs are complete, and closes issue #489 cleanly.

## Prior Art

- **PR #419**: SDLC pipeline integrity -- shipped session continuation hardening, URL validation, merge guard, and the initial subagent_stop stage injection (section D of `sdlc-pipeline-integrity.md`)
- **PR #487**: SDLC prompt enforcement -- stage-by-stage agent orchestration, related but distinct from runtime state injection
- **Issue #489**: This tracking issue -- code shipped directly, issue was closed prematurely and reopened

## Data Flow

1. **Entry point**: PM (PM session) dispatches dev-session via `/sdlc` sub-skill
2. **Dev session execution**: Dev-session runs the assigned stage (BUILD, TEST, etc.), updates `stage_states` on its AgentSession in Redis
3. **SubagentStop hook** (`agent/hooks/subagent_stop.py`): Fires when dev-session completes; reads `sdlc_stages`/`stage_states` from parent PM session's AgentSession; returns `{"reason": "Pipeline state: {dict}"}` so PM sees actual completion state
4. **Stage completion recording**: `_record_stage_on_parent()` calls `PipelineStateMachine.complete_stage()` to mark the in_progress stage as completed on the parent session
5. **Watchdog kill path**: `health_check.py` watchdog sets `watchdog_unhealthy` on AgentSession; `job_queue.py` nudge loop checks `is_session_unhealthy()` before auto-continuing; if flagged, delivers output to Telegram instead of sending "Keep working"

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (automated PR review)

This is a validation/documentation pass on existing code, not new feature work.

## Prerequisites

No prerequisites -- this work has no external dependencies. All code is already on main.

## Solution

### Key Elements

- **Test gap audit**: Verify existing unit tests in `test_subagent_stop_hook.py` and `test_health_check.py` cover all branches
- **Integration-level test**: Add a test that validates the full subagent_stop -> stage_states injection -> PipelineStateMachine flow
- **Documentation completeness**: Verify `docs/features/sdlc-pipeline-integrity.md` section D is accurate and complete

### Flow

Existing code on main -> Audit test coverage -> Add missing tests if any -> Verify docs -> Open PR -> Close issue

### Technical Approach

- Review `_record_stage_on_parent()` for test coverage -- this function wires PipelineStateMachine.complete_stage() into the hook but has no dedicated unit test
- Add a test for `_record_stage_on_parent()` covering: stage found and completed, no in_progress stage, parent session not found, import error
- Verify `is_session_unhealthy()` and `clear_unhealthy()` in health_check.py have test coverage
- Confirm the nudge loop's `watchdog_unhealthy` integration in `_classify_output_action()` is tested

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `subagent_stop.py` has try/except in `_register_dev_session_completion`, `_record_stage_on_parent`, `_get_sdlc_stages` -- all tested via existing `test_handles_import_error` and `test_handles_query_error` tests
- [x] `health_check.py` has try/except in `_set_unhealthy`, `is_session_unhealthy`, `clear_unhealthy` -- `_set_unhealthy` tested via mock, others need verification

### Empty/Invalid Input Handling
- [x] `_get_sdlc_stages` handles None sessions, None stage data, empty query results -- all tested
- [x] `subagent_stop_hook` handles missing agent_type, missing VALOR_SESSION_ID -- tested

### Error State Rendering
- [x] No user-visible output from these hooks -- they inject state into agent context or set Redis flags

## Test Impact

- [x] `tests/unit/test_subagent_stop_hook.py` -- no changes needed, existing tests are comprehensive
- [x] `tests/unit/test_health_check.py` -- no changes needed for existing tests
- [ ] `tests/unit/test_subagent_stop_hook.py` -- ADD: tests for `_record_stage_on_parent()` function (currently untested)
- [ ] `tests/unit/test_health_check.py` -- ADD: tests for `is_session_unhealthy()` and `clear_unhealthy()` (currently untested public API)

## Rabbit Holes

- End-to-end Telegram testing: Tempting to verify via live Telegram dispatch, but this is out of scope -- unit tests with mocks are sufficient for validation
- Refactoring subagent_stop.py: The code works and is well-structured; resist the urge to restructure during a validation pass
- PipelineStateMachine testing: That module has its own test coverage; we only need to test the integration point in `_record_stage_on_parent()`

## Risks

### Risk 1: Tests pass but real pipeline still misbehaves
**Impact:** PM could still fabricate stage completion in edge cases not covered by mocks
**Mitigation:** The hook's "reason" injection is the enforcement mechanism; if it fires, the PM sees real state. Manual observation (per issue acceptance criteria) can be done separately.

## Race Conditions

No race conditions identified -- all operations in the subagent_stop hook are synchronous Redis reads/writes within a single hook invocation. The watchdog unhealthy flag is set atomically on the AgentSession model.

## No-Gos (Out of Scope)

- Live Telegram dispatch testing (manual observation, not automated)
- Changes to the PM persona prompt (private file, already updated)
- PipelineStateMachine refactoring or new features
- Changes to the watchdog health check judge prompt

## Update System

No update system changes required -- this is a validation pass on existing code already deployed on main.

## Agent Integration

No agent integration required -- the subagent_stop hook and watchdog health check are internal hook mechanisms, not exposed via MCP or bridge tools.

## Documentation

### Feature Documentation
- [ ] Verify `docs/features/sdlc-pipeline-integrity.md` section D accurately describes the current implementation
- [ ] Add watchdog kill mechanism details to `docs/features/sdlc-pipeline-integrity.md` if not already covered (commit a96cb432)

### Inline Documentation
- [ ] Verify docstrings on `_record_stage_on_parent`, `is_session_unhealthy`, `clear_unhealthy` are accurate

## Success Criteria

- [ ] `_record_stage_on_parent()` has unit tests covering: success path, no in_progress stage, parent not found, exception handling
- [ ] `is_session_unhealthy()` and `clear_unhealthy()` have unit tests
- [ ] All existing tests in `test_subagent_stop_hook.py` and `test_health_check.py` still pass
- [ ] `docs/features/sdlc-pipeline-integrity.md` accurately covers both stage injection and watchdog kill
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] PR opened that bundles test and doc additions

## Team Orchestration

### Team Members

- **Builder (test-coverage)**
  - Name: test-builder
  - Role: Add missing unit tests for _record_stage_on_parent, is_session_unhealthy, clear_unhealthy
  - Agent Type: test-engineer
  - Resume: true

- **Validator (coverage-check)**
  - Name: coverage-validator
  - Role: Verify all branches in subagent_stop.py and health_check.py public API are tested
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add unit tests for _record_stage_on_parent
- **Task ID**: build-record-stage-tests
- **Depends On**: none
- **Validates**: tests/unit/test_subagent_stop_hook.py
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add TestRecordStageOnParent class with tests: marks stage completed, handles no in_progress stage, handles parent not found, handles PipelineStateMachine import error
- Test that complete_stage is called with the correct stage name

### 2. Add unit tests for health_check public API
- **Task ID**: build-health-api-tests
- **Depends On**: none
- **Validates**: tests/unit/test_health_check.py
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add tests for is_session_unhealthy: returns reason when flagged, returns None when healthy, returns None on error
- Add tests for clear_unhealthy: clears flag, handles missing session
- Add test for reset_session_count: clears counter, no-ops on unknown session

### 3. Verify documentation completeness
- **Task ID**: build-docs
- **Depends On**: build-record-stage-tests, build-health-api-tests
- **Assigned To**: test-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Verify sdlc-pipeline-integrity.md section D matches current subagent_stop.py implementation
- Add watchdog kill mechanism description if missing (watchdog_unhealthy field, nudge loop integration)

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-docs
- **Assigned To**: coverage-validator
- **Agent Type**: validator
- **Parallel**: false
- Run pytest tests/unit/test_subagent_stop_hook.py tests/unit/test_health_check.py -v
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Subagent stop tests pass | `pytest tests/unit/test_subagent_stop_hook.py -v` | exit code 0 |
| Health check tests pass | `pytest tests/unit/test_health_check.py -v` | exit code 0 |
| Lint clean | `python -m ruff check agent/hooks/subagent_stop.py agent/health_check.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/hooks/subagent_stop.py agent/health_check.py` | exit code 0 |
| Record stage tested | `pytest tests/unit/test_subagent_stop_hook.py -k "record_stage" -v` | exit code 0 |
| Health API tested | `pytest tests/unit/test_health_check.py -k "unhealthy or clear" -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- all code is already on main and functioning. This is a validation and documentation pass.
