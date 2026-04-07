---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/yudame/ai/issues/753
last_comment_id:
---

# Delete Dead ObserverTelemetry Model and Module

## Problem

`models/telemetry.py` (the `ObserverTelemetry` Popoto model) and `monitoring/telemetry.py` (its helper functions) were introduced in PR #348 to track observer agent decisions but were never wired into runtime code. They are dead code: not imported by `agent/`, `bridge/`, or `worker/`. The only references are two test files that exist solely to test the dead code, plus a re-export in `models/__init__.py` and stale doc references.

**Current behavior:** Dead model occupies the registry, dead helpers occupy disk, dedicated tests run against unused code.

**Desired outcome:** All traces of `ObserverTelemetry` and `monitoring/telemetry.py` removed from the codebase, including model export and stale doc references. Future telemetry, if desired, would be re-introduced alongside actual wiring (separate issue).

## Prior Art

- **#348**: Introduced this code; the model shipped but was never connected
- **#325**: Earlier observer dead-code cleanup
- **#467**: Pipeline dead-code cleanup that missed this module
- **#488**: SDLC stage tracking cruft removal

## Appetite

**Size:** Small
**Team:** Solo dev
**Interactions:** PM check-ins: 0; Review rounds: 1

## Prerequisites

No prerequisites — this is a pure deletion task with no external dependencies.

## Solution

### Key Elements

- Delete two source files
- Delete two test files
- Remove one export line from `models/__init__.py`
- Strip stale references from two doc files

### Technical Approach

1. `git rm` the four files
2. Edit `models/__init__.py` to remove the `ObserverTelemetry` import and `__all__` entry
3. Edit `docs/features/structured-logging-telemetry.md` and `docs/plans/redis-popoto-migration.md` to remove or update telemetry references
4. Verify no broken imports remain

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope (deletion only)

### Empty/Invalid Input Handling
- N/A — no functions added or modified

### Error State Rendering
- N/A — no user-visible output

## Test Impact

- [ ] `tests/unit/test_observer_telemetry.py` — DELETE: tests removed model
- [ ] `tests/unit/test_monitoring_telemetry.py` — DELETE: tests removed module

## Rabbit Holes

- Re-implementing observer telemetry properly — out of scope, separate issue if desired
- Auditing other potentially-dead Popoto models — separate cleanup pass

## Risks

### Risk 1: Hidden runtime import
**Impact:** A dynamic import or lazy reference could break at runtime.
**Mitigation:** `grep -rn ObserverTelemetry` and `grep -rn monitoring.telemetry` across the entire repo before deletion. Run `python -c "import models"` and full unit test suite after deletion.

## Race Conditions

No race conditions identified — pure deletion of unused files.

## No-Gos (Out of Scope)

- Re-wiring telemetry into the observer
- Removing or refactoring other telemetry-adjacent code (`monitoring/crash_tracker.py`, structured logging infrastructure)
- Auditing other dead Popoto models

## Update System

No update system changes required — pure code deletion, no new deps, no migration needed. Existing Redis keys for `ObserverTelemetry` will TTL out within 7 days.

## Agent Integration

No agent integration required — `ObserverTelemetry` was never exposed via MCP or called by the bridge.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/structured-logging-telemetry.md` to remove `ObserverTelemetry` references (or delete the section if it was the doc's main subject)
- [ ] Update `docs/plans/redis-popoto-migration.md` to remove `ObserverTelemetry` from any model lists

### Inline Documentation
- [ ] No inline doc changes needed

## Success Criteria

- [ ] `models/telemetry.py` no longer exists
- [ ] `monitoring/telemetry.py` no longer exists
- [ ] `tests/unit/test_observer_telemetry.py` and `tests/unit/test_monitoring_telemetry.py` no longer exist
- [ ] `ObserverTelemetry` is not referenced in `models/__init__.py`
- [ ] `python -c "import models"` succeeds
- [ ] `pytest tests/unit/ -x --co` succeeds (no collection errors)
- [ ] `grep -rn ObserverTelemetry` returns zero results in source/tests/docs
- [ ] `docs/features/structured-logging-telemetry.md` and `docs/plans/redis-popoto-migration.md` no longer reference the deleted symbols

## Team Orchestration

### Team Members

- **Builder (deletion)**
  - Name: telemetry-deleter
  - Role: Delete files, remove export, update docs
  - Agent Type: builder
  - Resume: true

- **Validator (deletion)**
  - Name: telemetry-validator
  - Role: Verify no broken imports or stale references remain
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Pre-deletion grep audit
- **Task ID**: build-audit
- **Depends On**: none
- **Validates**: grep results captured before deletion
- **Assigned To**: telemetry-deleter
- **Agent Type**: builder
- **Parallel**: false
- Run `grep -rn ObserverTelemetry .` and `grep -rn 'monitoring.telemetry' .` across the repo
- Confirm only expected references exist (the four files, `models/__init__.py`, two doc files)
- If unexpected references found, surface them before proceeding

### 2. Delete dead code
- **Task ID**: build-delete
- **Depends On**: build-audit
- **Validates**: files no longer exist
- **Assigned To**: telemetry-deleter
- **Agent Type**: builder
- **Parallel**: false
- `git rm models/telemetry.py monitoring/telemetry.py tests/unit/test_observer_telemetry.py tests/unit/test_monitoring_telemetry.py`
- Edit `models/__init__.py` to remove the `ObserverTelemetry` import and any `__all__` entry
- Edit `docs/features/structured-logging-telemetry.md` to remove stale references
- Edit `docs/plans/redis-popoto-migration.md` to remove stale references

### 3. Validate
- **Task ID**: validate-deletion
- **Depends On**: build-delete
- **Assigned To**: telemetry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -c "import models"` and confirm exit code 0
- Run `pytest tests/unit/ -x --co -q` and confirm no collection errors
- Run `grep -rn ObserverTelemetry .` and confirm zero matches
- Run `grep -rn 'monitoring\.telemetry\|monitoring/telemetry' .` and confirm zero matches
- Report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Models import clean | `python -c "import models"` | exit code 0 |
| Test collection clean | `pytest tests/unit/ -x --co -q` | exit code 0 |
| No ObserverTelemetry refs | `grep -rn ObserverTelemetry .` | exit code 1 |
| No monitoring.telemetry refs | `grep -rn 'monitoring\.telemetry' .` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. -->
