---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/717
last_comment_id:
---

# SDLC Quality Gate Verification for Session Zombie Fix

## Problem

PR #703 fixed the session completion zombie loop (issue #700) as an emergency merge. The fix is correct and working in production, but several SDLC pipeline stages were not independently verified.

**Current behavior:**
- Unit tests exist (11 tests in `tests/unit/test_session_completion_zombie.py`) but use mocks exclusively
- No integration test validates the health check orphan-fixing path against real Redis
- Docs exist but were never formally verified against acceptance criteria
- No independent `/do-test` run was recorded

**Desired outcome:**
- One integration test added that validates health check orphan-fixing preserves `status="completed"` against real Redis
- All 11 existing unit tests verified passing via recorded `/do-test` run
- Docs confirmed complete against the 7 acceptance criteria from #700
- Clean audit trail proving the fix meets all quality gates

## Prior Art

- **PR #703**: Fix session completion zombie loop -- emergency merge that shipped the actual fix (merged 2026-04-05)
- **Issue #700**: Original bug report with full diagnosis and 7 acceptance criteria

## Solution

### Key Elements

- **Integration test**: One new test in `tests/integration/` that creates a completed child session with an orphaned parent in real Redis, runs the health check, and verifies status stays `completed`
- **Test run recording**: Run full unit suite and integration test via `/do-test` for an auditable record
- **Doc verification**: Cross-reference existing docs against #700 acceptance criteria

### Technical Approach

- Create `tests/integration/test_session_zombie_health_check.py` with a single test class
- The test creates real `AgentSession` records in Redis (using the `redis_test_db` fixture), simulates the orphan scenario, calls `_agent_session_hierarchy_health_check()`, and asserts status preservation
- Verify existing docs (`session-lifecycle.md`, `agent-session-queue.md`, `session-lifecycle-diagnostics.md`, `README.md`) cover all 7 acceptance criteria from #700

## Failure Path Test Strategy

### Exception Handling Coverage
- No new exception handlers in scope -- the integration test exercises the existing health check exception handling

### Empty/Invalid Input Handling
- [ ] Test should verify behavior when the orphaned parent ID points to a non-existent session (the standard orphan case)

### Error State Rendering
- No user-visible output in scope

## Test Impact

No existing tests affected -- this is purely additive work. The 11 existing unit tests in `tests/unit/test_session_completion_zombie.py` are unchanged; we are adding one new integration test alongside them.

## Rabbit Holes

- Rewriting existing unit tests to use real Redis -- they are correct as mock-based tests for the logic they cover
- Adding integration tests for the nudge guard (Bug 2) -- that path is harder to exercise end-to-end and the unit tests cover it well
- Refactoring the health check function itself -- it works correctly, this is verification only

## Risks

### Risk 1: Integration test flakiness due to Redis state
**Impact:** Test fails intermittently, adding noise to CI
**Mitigation:** Use `redis_test_db` fixture for isolated test database; clean up sessions after test

## Race Conditions

No race conditions identified -- the integration test runs the health check synchronously in a controlled environment with no concurrent workers.

## No-Gos (Out of Scope)

- No changes to the core fix (`_AGENT_SESSION_FIELDS`, nudge guard)
- No refactoring of health check function
- No additional integration tests beyond the orphan-fixing path
- No changes to existing unit tests

## Update System

No update system changes required -- this is a test-only addition with no runtime impact.

## Agent Integration

No agent integration required -- this is a test and verification pass with no new tools or bridge changes.

## Documentation

- [ ] Verify `docs/features/session-lifecycle.md` accurately describes the zombie fix code
- [ ] Verify `docs/features/README.md` has session-lifecycle entry
- [ ] Verify cascade updates in `docs/features/agent-session-queue.md` and `docs/features/session-lifecycle-diagnostics.md`
- [ ] No new docs needed -- this verifies existing docs are complete

## Success Criteria

- [ ] Integration test `tests/integration/test_session_zombie_health_check.py` passes against real Redis
- [ ] All 11 unit tests in `tests/unit/test_session_completion_zombie.py` pass
- [ ] Full unit suite passes (`pytest tests/unit/ -x -q`)
- [ ] Docs verified complete: `session-lifecycle.md` covers zombie prevention, nudge guard, and field extraction
- [ ] All 7 acceptance criteria from #700 verified with evidence

## Team Orchestration

### Team Members

- **Builder (integration-test)**
  - Name: test-builder
  - Role: Create integration test for health check orphan-fixing
  - Agent Type: test-engineer
  - Resume: true

- **Validator (verification)**
  - Name: quality-validator
  - Role: Run tests, verify docs, audit acceptance criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Integration Test
- **Task ID**: build-integration-test
- **Depends On**: none
- **Validates**: `tests/integration/test_session_zombie_health_check.py`
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_session_zombie_health_check.py`
- Test: create completed child session with orphaned parent in real Redis
- Call `_agent_session_hierarchy_health_check()`
- Assert child status remains `completed` after health check
- Assert `parent_agent_session_id` is cleared to `None`

### 2. Run Full Test Suite
- **Task ID**: run-tests
- **Depends On**: build-integration-test
- **Assigned To**: quality-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_completion_zombie.py -v` and verify all 11 pass
- Run `pytest tests/integration/test_session_zombie_health_check.py -v` and verify new test passes
- Run `pytest tests/unit/ -x -q` for full unit suite

### 3. Verify Documentation Completeness
- **Task ID**: verify-docs
- **Depends On**: none
- **Assigned To**: quality-validator
- **Agent Type**: validator
- **Parallel**: true
- Cross-reference `docs/features/session-lifecycle.md` against all 7 acceptance criteria from #700
- Verify `docs/features/README.md` entry exists and is alphabetically sorted
- Verify cascade updates in `agent-session-queue.md` and `session-lifecycle-diagnostics.md`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: run-tests, verify-docs
- **Assigned To**: quality-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all success criteria met
- Generate summary report with evidence

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Integration test passes | `pytest tests/integration/test_session_zombie_health_check.py -v` | exit code 0 |
| Zombie unit tests pass | `pytest tests/unit/test_session_completion_zombie.py -v` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Session lifecycle docs exist | `test -f docs/features/session-lifecycle.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the scope is narrow and well-defined by the issue's recon summary.
