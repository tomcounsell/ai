---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-10
tracking: https://github.com/valorengels/ai/issues/341
---

# Fix _compute_stall_backoff TypeError

## Problem

`_compute_stall_backoff` in `monitoring/session_watchdog.py` crashes with:

```
TypeError: unsupported operand type(s) for ** or pow(): 'int' and 'Field'
```

**Current behavior:**
The watchdog error handler crashes on every stalled zombie session on every watchdog cycle, preventing stall retry logic from executing. Popoto `Field` objects are passed directly into arithmetic operations that expect plain `int` values.

**Desired outcome:**
`retry_count` is always coerced to a plain `int` before use in arithmetic, comparisons, and logging throughout the watchdog module.

## Prior Art

No prior issues found related to this work.

## Data Flow

1. **Entry point**: Watchdog cycle calls `_check_session_health()` for each active session
2. **Session model**: `session.retry_count` is a Popoto `Field(type=int, default=0)` — reading it may return a `Field` object rather than a plain `int`
3. **`_compute_stall_backoff(retry_count)`**: Receives the raw field value and uses it in `2**retry_count` — crashes with `TypeError`
4. **Output**: Backoff delay (float) used in `asyncio.sleep()`

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

- **Defensive casting in `_compute_stall_backoff`**: Cast `retry_count` to `int()` inside the function to handle any non-int input
- **Defensive casting at read sites**: Where `session.retry_count` is read from the model (lines 594, 663, 726), ensure the value is coerced to `int`

### Technical Approach

- Add `retry_count = int(retry_count)` in `_compute_stall_backoff` after the None check, before arithmetic
- At each call site where `session.retry_count` is read, wrap with `int(...)` to ensure plain int before passing to functions or using in comparisons
- Add a unit test that passes a mock `Field`-like object to `_compute_stall_backoff` to verify it handles non-int types

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_compute_stall_backoff` function has no exception handlers — the fix is to prevent the TypeError from occurring at all

### Empty/Invalid Input Handling
- [ ] Test `_compute_stall_backoff` with None (already handled), negative values (already handled), and non-int types (the bug)

### Error State Rendering
- [ ] Not applicable — this is internal watchdog logic with no user-visible output

## Rabbit Holes

- Refactoring all Popoto Field reads across the entire codebase — scope to this module only
- Adding a generic Popoto field-coercion decorator or wrapper — overkill for this fix

## Risks

### Risk 1: Other Popoto Field arithmetic bugs elsewhere
**Impact:** Similar crashes in other modules
**Mitigation:** Out of scope for this fix; file a separate issue if found

## Race Conditions

No race conditions identified — `_compute_stall_backoff` is a pure function, and the retry_count reads are within a single async context.

## No-Gos (Out of Scope)

- Fixing Popoto's Field dereferencing behavior globally
- Auditing all Popoto model field reads across the codebase
- Changing the session model schema

## Update System

No update system changes required — this is a pure bug fix in monitoring logic.

## Agent Integration

No agent integration required — this is a watchdog-internal change.

## Documentation

No documentation changes needed. This is a one-line bug fix casting a Popoto Field to int before arithmetic. The fix is self-explanatory from the inline comment added in the code change. No feature docs, API changes, or external documentation are affected.

## Success Criteria

- [ ] `_compute_stall_backoff` accepts Field-like objects without crashing
- [ ] All `session.retry_count` reads in `session_watchdog.py` are coerced to `int`
- [ ] Unit test validates non-int input handling
- [ ] Tests pass (`/do-test`)
- [ ] Ruff lint and format pass

## Team Orchestration

### Team Members

- **Builder (watchdog-fix)**
  - Name: watchdog-fixer
  - Role: Apply int() casts and add test
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-fix)**
  - Name: watchdog-validator
  - Role: Verify fix and run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix _compute_stall_backoff and call sites
- **Task ID**: build-fix
- **Depends On**: none
- **Assigned To**: watchdog-fixer
- **Agent Type**: builder
- **Parallel**: false
- Add `retry_count = int(retry_count)` in `_compute_stall_backoff` after None/negative guards
- Cast `session.retry_count` to `int()` at lines 594, 663, 726

### 2. Add unit test for non-int retry_count
- **Task ID**: build-test
- **Depends On**: build-fix
- **Assigned To**: watchdog-fixer
- **Agent Type**: builder
- **Parallel**: false
- Add test that passes a mock Field-like object to `_compute_stall_backoff`
- Verify it returns the expected backoff value

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-test
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff check monitoring/session_watchdog.py`
- Run `python -m pytest tests/unit/test_session_watchdog.py -x`
- Verify all success criteria met

## Validation Commands

- `python -m ruff check monitoring/session_watchdog.py` - lint passes
- `python -m ruff format --check monitoring/session_watchdog.py` - format passes
- `python -m pytest tests/unit/test_session_watchdog.py -x` - tests pass
