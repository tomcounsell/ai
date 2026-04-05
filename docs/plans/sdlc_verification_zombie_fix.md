---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/708
---

# SDLC Quality Gate Verification for Session Zombie Fix

## Problem

PR #703 fixed the session completion zombie loop (issue #700) but was emergency-merged while the bug was actively spamming duplicate Telegram messages. Three SDLC quality gates were skipped: TEST, REVIEW, and DOCS. The pipeline state records were destroyed when `kill --all` cleaned up the zombie sessions.

**Current behavior:**
The fix is merged and working in production, but there is no verified record that: (1) the 11 unit tests pass via `/do-test`, (2) the code was properly reviewed via `/do-pr-review`, or (3) the documentation is complete against issue #700's acceptance criteria.

**Desired outcome:**
All three quality gates retroactively verified. Any gaps found are fixed in a follow-up PR.

## Prior Art

- **Issue #700**: Original bug report with full root cause analysis -- closed when PR #703 merged
- **PR #703**: Emergency fix (merged) -- added `status` to `_AGENT_SESSION_FIELDS`, added nudge overwrite guard
- **Issues #706, #709-716**: Duplicate follow-up issues for this same verification work -- all closed as duplicates of #708

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0 (verification-only, no new code expected)

## Prerequisites

No prerequisites -- this work operates on already-merged code in the main branch.

## Solution

### Key Elements

- **Test verification**: Run `/do-test` against `tests/unit/test_session_completion_zombie.py` and the broader unit suite
- **Docs verification**: Audit `docs/features/session-lifecycle.md` against issue #700's acceptance criteria
- **Index verification**: Confirm `docs/features/README.md` has a correct, sorted entry for session-lifecycle

### Technical Approach

This is verification-only. Run the test suite, read the docs, compare against acceptance criteria. If gaps are found, open a small follow-up PR to fix them.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope -- this is a verification task, not implementation.

### Empty/Invalid Input Handling
Not applicable -- no new functions being created or modified.

### Error State Rendering
Not applicable -- no user-visible output changes.

## Test Impact

No existing tests affected -- this work verifies existing tests, it does not modify any code or test files.

## Rabbit Holes

- Re-reviewing the entire PR #703 diff as if it were a new submission -- the code is already merged and working. Focus only on confirming quality gates were met.
- Creating integration tests for the zombie fix -- the 11 unit tests cover the fix. Integration testing is a separate concern.
- Reopening any of the duplicate issues (#706, #709-716) -- they are correctly closed.

## Risks

### Risk 1: Tests may fail due to codebase changes since PR #703
**Impact:** False negative -- tests may fail due to unrelated changes, not the zombie fix itself
**Mitigation:** If tests fail, inspect failures to determine if they are related to the zombie fix or unrelated regressions

## Race Conditions

No race conditions identified -- this is a read-only verification task with no concurrent operations.

## No-Gos (Out of Scope)

- Writing new integration tests for the zombie fix
- Modifying the zombie fix implementation
- Changing any SDLC pipeline tracking code

## Update System

No update system changes required -- this is a verification-only task with no code changes expected.

## Agent Integration

No agent integration required -- this is a retroactive quality gate check with no new tools or capabilities.

## Documentation

- [ ] Verify `docs/features/session-lifecycle.md` covers all 6 acceptance criteria from issue #700
- [ ] Verify `docs/features/README.md` has session-lifecycle entry correctly sorted
- [ ] If gaps found, create a follow-up PR to fix documentation

## Success Criteria

- [ ] `/do-test` confirms `test_session_completion_zombie.py` passes (11 tests)
- [ ] `/do-test` confirms no regressions in the broader unit suite
- [ ] `docs/features/session-lifecycle.md` verified complete against #700 acceptance criteria
- [ ] `docs/features/README.md` index entry verified present and sorted
- [ ] Any gaps found are documented and fixed

## Team Orchestration

### Team Members

- **Tester (zombie-fix)**
  - Name: zombie-tester
  - Role: Run unit tests and verify all 11 zombie fix tests pass
  - Agent Type: test-engineer
  - Resume: true

- **Docs Auditor (session-lifecycle)**
  - Name: docs-auditor
  - Role: Verify session-lifecycle docs against issue #700 acceptance criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Run Unit Tests
- **Task ID**: test-zombie-fix
- **Depends On**: none
- **Validates**: tests/unit/test_session_completion_zombie.py
- **Assigned To**: zombie-tester
- **Agent Type**: test-engineer
- **Parallel**: true
- Run `pytest tests/unit/test_session_completion_zombie.py -v` and confirm 11 tests pass
- Run `pytest tests/unit/ -x -q` to check for broader regressions

### 2. Verify Documentation
- **Task ID**: verify-docs
- **Depends On**: none
- **Validates**: docs/features/session-lifecycle.md, docs/features/README.md
- **Assigned To**: docs-auditor
- **Agent Type**: validator
- **Parallel**: true
- Read `docs/features/session-lifecycle.md` and compare against each of the 6 acceptance criteria from issue #700
- Check `docs/features/README.md` for session-lifecycle entry and verify it is correctly sorted alphabetically
- Report any gaps found

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: test-zombie-fix, verify-docs
- **Assigned To**: docs-auditor
- **Agent Type**: validator
- **Parallel**: false
- Aggregate results from test run and docs audit
- If gaps found, list them for a follow-up PR
- Generate final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Zombie tests pass | `pytest tests/unit/test_session_completion_zombie.py -v` | exit code 0 |
| Unit suite clean | `pytest tests/unit/ -x -q` | exit code 0 |
| Docs exist | `test -f docs/features/session-lifecycle.md` | exit code 0 |
| README entry | `grep -c 'session-lifecycle' docs/features/README.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- this is a well-scoped verification task with clear acceptance criteria already defined in issue #700.
