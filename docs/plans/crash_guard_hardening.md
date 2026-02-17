---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-02-17
tracking: https://github.com/tomcounsell/ai/issues/133
---

# Harden Crash Guard: Fix Weak Test + Fragile String Matching

## Problem

PR #132 shipped a crash guard that prevents auto-continue loops after SDK crashes. The fix works, but has two hardening gaps identified during review:

**Current behavior:**
1. `test_error_sends_to_chat` (test_auto_continue.py:169) recreates the if/elif routing logic inline instead of testing the actual `send_to_chat` closure. It proves `ERROR != STATUS_UPDATE` but doesn't verify the explicit `OutputType.ERROR` guard. If someone removes the ERROR check, the test still passes because ERROR falls to `else`.
2. Watchdog unique constraint handling (`session_watchdog.py:93`) uses `"Unique constraint violated" in str(e)` — if popoto ever changes the message, the guard silently breaks and the infinite loop returns.

**Desired outcome:**
- A test that would fail if the explicit `OutputType.ERROR` guard is removed from `job_queue.py`
- Watchdog catches `popoto.exceptions.ModelException` instead of string-matching

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

Two surgical changes — one test rewrite, one exception class swap.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Stronger error bypass test**: Replace inline logic recreation with a test that patches `classify_output` and verifies the actual code path logs the error-skip message and doesn't push a steering message
- **Typed exception catch**: Replace `"Unique constraint violated" in str(e)` with `except ModelException` from `popoto.exceptions`

### Technical Approach

**Fix 1 — Rewrite `test_error_sends_to_chat`** (`tests/test_auto_continue.py:169-187`):

The existing tests all duplicate the routing logic inline. The ERROR test should instead:
1. Patch `classify_output` to return `OutputType.ERROR`
2. Simulate the routing decision the same way other tests do, but with the actual if/elif/else structure from `job_queue.py` that includes the explicit ERROR check
3. Assert that `auto_continue_count` stays at 0 (no auto-continue)
4. Assert that `send_cb` is called (error reaches chat)
5. Assert no steering message was pushed

The key difference: the test must include the `OutputType.ERROR` check as a distinct branch, not rely on it falling to `else`. This way, removing the ERROR guard would cause a behavior change detectable by the test.

**Fix 2 — Catch `ModelException` in watchdog** (`monitoring/session_watchdog.py:92-114`):

```python
from popoto.exceptions import ModelException

# Replace:
except Exception as e:
    if "Unique constraint violated" in str(e):
        ...

# With:
except ModelException as e:
    # Unique constraint or other popoto model errors from stale sessions
    try:
        session.status = "failed"
        session.save()
        ...
    except Exception:
        pass
except Exception as e:
    logger.error(...)
```

This catches all `ModelException` variants (unique constraint, query errors) from stale sessions and marks them failed, while letting non-popoto exceptions flow to the general handler.

## Rabbit Holes

- Don't refactor all the other inline-logic tests (QUESTION, COMPLETION, BLOCKER, STATUS_UPDATE) — they work fine for their cases since they only need to verify `!= STATUS_UPDATE`
- Don't add integration tests that spin up real Redis sessions — unit tests with mocks are sufficient here
- Don't try to make the watchdog catch more specific sub-exceptions beyond `ModelException` — popoto doesn't have finer-grained exception types

## Risks

### Risk 1: ModelException catches more than unique constraint violations
**Impact:** Other popoto errors (query failures, serialization) would also mark sessions as failed
**Mitigation:** This is actually desirable — any popoto error on a session means it's in a bad state. Marking it failed is the safe default. The warning log distinguishes the cases.

## No-Gos (Out of Scope)

- Not refactoring the test pattern for all auto-continue tests (just fixing the ERROR one)
- Not adding new exception classes to popoto
- Not changing the watchdog's overall error handling strategy

## Update System

No update system changes required — this is purely internal test and error handling hardening.

## Agent Integration

No agent integration required — this is test code and internal watchdog error handling.

## Documentation

- [ ] Update inline comments in `session_watchdog.py` to reference `ModelException` instead of string matching
- [ ] No feature doc changes needed — the behavior is unchanged, only the implementation is more robust

## Success Criteria

- [ ] `test_error_sends_to_chat` would fail if the `OutputType.ERROR` guard in `job_queue.py` is removed
- [ ] Watchdog catches `popoto.exceptions.ModelException` instead of string-matching
- [ ] All existing tests still pass
- [ ] Linting passes (`ruff`, `black`)

## Team Orchestration

### Team Members

- **Builder (hardening)**
  - Name: hardening-builder
  - Role: Rewrite error test and swap exception class
  - Agent Type: builder
  - Resume: true

- **Validator (hardening)**
  - Name: hardening-validator
  - Role: Verify test catches regressions and exception handling is correct
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite error bypass test
- **Task ID**: build-error-test
- **Depends On**: none
- **Assigned To**: hardening-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `test_error_sends_to_chat` in `tests/test_auto_continue.py` to include the explicit `OutputType.ERROR` branch
- Verify the test fails if the ERROR guard is commented out in `job_queue.py`

### 2. Replace string matching with ModelException
- **Task ID**: build-exception-class
- **Depends On**: none
- **Assigned To**: hardening-builder
- **Agent Type**: builder
- **Parallel**: true
- In `monitoring/session_watchdog.py`, import `ModelException` from `popoto.exceptions`
- Replace the `if "Unique constraint violated" in str(e)` block with a dedicated `except ModelException` clause
- Update the inline comment to explain why we catch ModelException

### 3. Validate changes
- **Task ID**: validate-hardening
- **Depends On**: build-error-test, build-exception-class
- **Assigned To**: hardening-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_auto_continue.py tests/test_session_watchdog.py -x`
- Run `ruff check tests/test_auto_continue.py monitoring/session_watchdog.py`
- Run `black --check tests/test_auto_continue.py monitoring/session_watchdog.py`
- Verify the error test structure includes explicit ERROR branch

## Validation Commands

- `pytest tests/test_auto_continue.py -x -v` — Auto-continue tests pass
- `pytest tests/test_session_watchdog.py -x -v` — Watchdog tests pass
- `ruff check tests/test_auto_continue.py monitoring/session_watchdog.py` — Lint clean
- `black --check tests/test_auto_continue.py monitoring/session_watchdog.py` — Format clean
