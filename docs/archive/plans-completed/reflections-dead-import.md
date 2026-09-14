---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/857
last_comment_id:
---

# Fix reflections import of removed module bridge.session_logs

## Problem

The reflections maintenance script crashes on deployed instances where the backward-compatibility shim `bridge/session_logs.py` is missing.

**Current behavior:**
`scripts/reflections.py` line 3017 imports `cleanup_old_snapshots` from `bridge.session_logs` -- a shim that re-exports from `agent.session_logs`. On machines where the shim file was not propagated during updates (incomplete `git pull`, partial deploy), the entire reflections run fails with `ModuleNotFoundError` at the cleanup step, after all the valuable analysis work has already completed. Observed in production as Sentry event VALOR-12.

**Desired outcome:**
The reflections script imports directly from the canonical location `agent.session_logs`, eliminating the dependency on the shim. The script runs to completion on every deployed instance regardless of whether the shim exists.

## Prior Art

- **PR #737**: Extract standalone worker service from bridge monolith -- moved `session_logs` from `bridge/` to `agent/`, left a backward-compat shim at `bridge/session_logs.py`. This is the refactor that created the stale import.
- **Plan `docs/plans/done/log-rotation-fix.md`**: Originally wired `cleanup_old_snapshots()` into the reflections script. The plan specified importing from `bridge.session_logs`, which was correct at the time but became stale after PR #737 moved the canonical module.

## Architectural Impact

- **New dependencies**: None -- `agent.session_logs` already exists and is the canonical location.
- **Interface changes**: None -- `cleanup_old_snapshots` has the same signature in both locations.
- **Coupling**: This change reduces coupling. The reflections script currently depends on a shim in `bridge/`; after the fix it depends directly on the canonical module in `agent/`.
- **Data ownership**: No change -- `agent/session_logs.py` already owns this function.
- **Reversibility**: Trivial -- single import path change.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (code review)

This is a one-line import fix plus a documentation update. No design decisions, no scope ambiguity.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Import fix**: Update `scripts/reflections.py` line 3017 to import from `agent.session_logs` instead of `bridge.session_logs`
- **Documentation fix**: Update `docs/features/reflections.md` line 458 to reference `agent.session_logs` instead of `bridge.session_logs`

### Flow

Reflections `main()` completes analysis pipeline -> imports `cleanup_old_snapshots` from `agent.session_logs` -> calls cleanup -> logs result

### Technical Approach

- Change the import at `scripts/reflections.py:3017` from `from bridge.session_logs import cleanup_old_snapshots` to `from agent.session_logs import cleanup_old_snapshots`
- Update the prose in `docs/features/reflections.md:458` that references `bridge.session_logs.cleanup_old_snapshots()` to say `agent.session_logs.cleanup_old_snapshots()`
- Do NOT remove the backward-compat shim `bridge/session_logs.py` -- other code or external consumers may still use it, and removing it is a separate concern

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `try/except Exception` block at lines 3016-3023 already catches and logs failures as non-fatal -- no change needed. The builder should verify this block still wraps the updated import.

### Empty/Invalid Input Handling
- [ ] No new functions are added. `cleanup_old_snapshots()` already handles the case where `logs/sessions/` does not exist (returns 0).

### Error State Rendering
- [ ] Not applicable -- this is a background script with no user-visible output. Errors are logged.

## Test Impact

- [ ] `tests/unit/test_worker_entry.py::test_session_logs_re_exports_from_bridge` -- no change needed. This test validates the shim still works, which remains true. The shim is not being removed.

No existing tests are directly affected by this change. The import path update in `scripts/reflections.py` is not covered by any existing test that imports from `bridge.session_logs` via reflections. The shim re-export test at `tests/unit/test_worker_entry.py:255-259` tests the shim itself, not the reflections script's usage of it.

## Rabbit Holes

- **Removing the shim entirely**: Tempting, but the shim `bridge/session_logs.py` may be used by other deployed scripts or external tooling. Removing it is a separate audit and should be its own issue.
- **Auditing all shim consumers**: The test at `tests/unit/test_worker_entry.py:255` explicitly validates the shim still works. A full shim removal audit is out of scope for this bug fix.
- **Adding a test for the reflections import specifically**: The import is inside a try/except, so a test that imports `scripts.reflections` will not catch a bad import path (the exception is swallowed). Testing this properly would require mocking or integration setup disproportionate to the fix.

## Risks

### Risk 1: Typo in new import path
**Impact:** Same crash, different module name.
**Mitigation:** The builder will verify with `python -c "from agent.session_logs import cleanup_old_snapshots"` after making the change.

## Race Conditions

No race conditions identified -- this is a single-threaded import statement change with no concurrency implications.

## No-Gos (Out of Scope)

- Removing the `bridge/session_logs.py` backward-compat shim
- Auditing all other consumers of the shim
- Adding integration tests for the reflections cleanup step
- Modifying the `cleanup_old_snapshots` function itself

## Update System

No update system changes required -- this is a standard code change that propagates via `git pull`. The fix actually makes the reflections script more resilient to incomplete updates (the whole point of the bug fix).

## Agent Integration

No agent integration required -- `scripts/reflections.py` is a standalone scheduled script. It is not invoked through MCP servers or the bridge. No changes to `.mcp.json` or `mcp_servers/` needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` line 458: change the Session Log Cleanup paragraph to reference `agent.session_logs.cleanup_old_snapshots()` instead of `bridge.session_logs.cleanup_old_snapshots()`, reflecting the canonical module location after the worker extraction refactor

### Inline Documentation
- [ ] Verify the inline comment at `scripts/reflections.py` line 3015 ("Clean up old session log directories") remains accurate after the import change -- no update expected but confirm during build

No new documentation files needed. No `docs/features/README.md` index changes needed -- the existing `docs/features/reflections.md` entry already covers this area and only requires a path correction within its content.

## Success Criteria

- [ ] `scripts/reflections.py` imports `cleanup_old_snapshots` from `agent.session_logs` (not `bridge.session_logs`)
- [ ] `python -c "from agent.session_logs import cleanup_old_snapshots; print('OK')"` exits 0
- [ ] `grep -n 'from agent.session_logs import cleanup_old_snapshots' scripts/reflections.py` matches line ~3017
- [ ] `grep -c 'bridge.session_logs' scripts/reflections.py` outputs 0
- [ ] `docs/features/reflections.md` references `agent.session_logs` (not `bridge.session_logs`)
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (import-fix)**
  - Name: import-fixer
  - Role: Update the import path and documentation reference
  - Agent Type: builder
  - Resume: true

- **Validator (verify-fix)**
  - Name: fix-validator
  - Role: Verify the import works and no stale references remain
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix the import in reflections.py
- **Task ID**: build-import-fix
- **Depends On**: none
- **Validates**: `python -c "from agent.session_logs import cleanup_old_snapshots"`, `grep 'from agent.session_logs' scripts/reflections.py`
- **Assigned To**: import-fixer
- **Agent Type**: builder
- **Parallel**: true
- Change line 3017 of `scripts/reflections.py` from `from bridge.session_logs import cleanup_old_snapshots` to `from agent.session_logs import cleanup_old_snapshots`

### 2. Update documentation reference
- **Task ID**: build-docs-update
- **Depends On**: none
- **Validates**: `grep 'agent.session_logs' docs/features/reflections.md`
- **Assigned To**: import-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `docs/features/reflections.md` line 458: change `bridge.session_logs.cleanup_old_snapshots()` to `agent.session_logs.cleanup_old_snapshots()`

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-import-fix, build-docs-update
- **Assigned To**: fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `grep -c 'bridge.session_logs' scripts/reflections.py` outputs 0
- Verify `grep 'from agent.session_logs import cleanup_old_snapshots' scripts/reflections.py` matches
- Verify `grep 'agent.session_logs' docs/features/reflections.md` matches
- Run `python -m ruff check scripts/reflections.py`
- Run `pytest tests/unit/test_worker_entry.py -x -q` to confirm shim test still passes

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Import works | `python -c "from agent.session_logs import cleanup_old_snapshots; print('OK')"` | exit code 0 |
| Canonical import in reflections | `grep -c 'from agent.session_logs import cleanup_old_snapshots' scripts/reflections.py` | output > 0 |
| No stale bridge import in reflections | `grep -c 'from bridge.session_logs' scripts/reflections.py` | exit code 1 |
| Docs updated | `grep -c 'agent.session_logs' docs/features/reflections.md` | output > 0 |
| Tests pass | `pytest tests/unit/test_worker_entry.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/reflections.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None -- this is a straightforward one-line bug fix with clear root cause and solution.
