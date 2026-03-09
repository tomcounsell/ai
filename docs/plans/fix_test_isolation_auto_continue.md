---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-09
tracking: https://github.com/tomcounsell/ai/issues/322
---

# Fix Test Isolation: test_auto_continue.py Corrupts sys.modules

## Problem

Seven test files mock `claude_agent_sdk` at module import time using a bare `sys.modules` assignment:

```python
if "claude_agent_sdk" not in sys.modules:
    _mock_sdk = MagicMock()
    sys.modules["claude_agent_sdk"] = _mock_sdk
```

This mock persists in `sys.modules` for the entire pytest session. When `test_cross_wire_fixes.py` runs after any of these files, its `import agent.sdk_client` succeeds (because the mock is present), but `ValorAgent` gets a `MagicMock` instead of the real SDK. Assertions like `options.continue_conversation is False` then fail because they compare against a MagicMock attribute.

**Current behavior:**
Running `pytest tests/test_auto_continue.py tests/test_cross_wire_fixes.py` fails. The `TestSessionIsolation` class gets the mock SDK instead of the real one, and boolean assertions fail against MagicMock attributes.

**Desired outcome:**
All test files can run in any order within a single pytest session without corrupting `sys.modules` for subsequent tests.

## Prior Art

No prior issues found related to this work. This is the first report of cross-test contamination via `sys.modules`.

## Data Flow

1. **Entry point**: pytest collection phase discovers test files
2. **Module-level mock**: Seven test files insert `MagicMock()` into `sys.modules["claude_agent_sdk"]` at import time (before any test function runs)
3. **Contamination**: The mock persists in `sys.modules` for the rest of the pytest session
4. **Victim test**: `test_cross_wire_fixes.py` does `import agent.sdk_client` which internally does `import claude_agent_sdk` -- gets the MagicMock
5. **Failure**: `ValorAgent._create_options()` returns an object backed by MagicMock; boolean assertions fail

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Shared conftest fixture**: A single `autouse` session-scoped fixture in `tests/conftest.py` that manages the `claude_agent_sdk` mock via `unittest.mock.patch.dict`
- **Test file cleanup**: Remove the 7 duplicated module-level `sys.modules` hacks
- **Proper teardown**: The fixture restores `sys.modules` to its original state, preventing cross-test contamination

### Technical Approach

Replace all 7 instances of the module-level `sys.modules` hack with a single `autouse` fixture in `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def mock_claude_agent_sdk_if_missing():
    """Mock claude_agent_sdk if not installed, with proper cleanup.

    Several test files need to import from agent.* which transitively
    imports claude_agent_sdk. When the real SDK isn't installed, we
    provide a MagicMock. The key difference from the old approach:
    this fixture RESTORES sys.modules on teardown, preventing
    contamination of subsequent tests that use the real SDK.
    """
    if "claude_agent_sdk" in sys.modules:
        # Real SDK already loaded -- don't interfere
        yield
        return

    mock_sdk = MagicMock()
    with patch.dict(sys.modules, {"claude_agent_sdk": mock_sdk}):
        yield
```

This approach:
1. Only mocks when the real SDK is absent (preserves real SDK behavior in CI with SDK installed)
2. Uses `patch.dict` context manager which automatically restores `sys.modules` on exit
3. Centralizes the mock in one place instead of 7 scattered copies
4. Is `autouse=True` so no test file needs to explicitly request it

### Flow

**pytest collection** -> conftest.py fixture activates -> mock injected (if SDK missing) -> test file imports agent.* successfully -> test runs -> fixture teardown restores sys.modules -> next test file gets clean sys.modules

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this change is purely about fixture lifecycle

### Empty/Invalid Input Handling
- Not applicable -- no user input processing

### Error State Rendering
- Not applicable -- no user-visible output

## Rabbit Holes

- Trying to make the real SDK importable in all environments -- the mock exists because the SDK is optional
- Auditing every `sys.modules` manipulation across the codebase -- scope is limited to `claude_agent_sdk`
- Session-scoped vs function-scoped fixture debates -- `autouse` with `patch.dict` at function scope is correct here because it ensures cleanup after every test

## Risks

### Risk 1: Tests that depend on the mock persisting across test functions
**Impact:** Tests that import from agent.* at module level (outside test functions) might fail if the mock isn't present during collection
**Mitigation:** The fixture is autouse, so it activates for every test. Module-level imports happen during collection, before fixtures run. We need to verify the 7 files don't have module-level imports that depend on the mock being in sys.modules at collection time. Looking at the files: they DO have module-level imports (e.g., `from agent.job_queue import ...` at line 21 of test_auto_continue.py). The fix must handle this -- either keep a module-level guard that's cleaned up, or move those imports into the test functions.

**Revised approach:** Since module-level imports happen during collection (before fixtures run), we cannot rely solely on a conftest fixture. Instead:
1. Keep a minimal module-level mock that ensures collection succeeds
2. Add a conftest fixture that properly cleans up after each test by saving/restoring the original sys.modules state
3. The fixture saves `sys.modules.get("claude_agent_sdk")` before each test, and restores it after

## Race Conditions

No race conditions identified -- all operations are synchronous and single-threaded (pytest test execution).

## No-Gos (Out of Scope)

- Refactoring how agent.* modules import the SDK
- Making claude_agent_sdk a required dependency
- Changing test collection order or pytest configuration (e.g., pytest-randomly)

## Update System

No update system changes required -- this is a test-only fix with no runtime impact.

## Agent Integration

No agent integration required -- this is a test infrastructure fix.

## Documentation

- [ ] Add inline comments in `tests/conftest.py` explaining the SDK mock fixture
- [ ] No feature doc needed -- this is a test infrastructure fix
- [ ] If no documentation changes are needed beyond inline comments, state: "No feature documentation needed -- bug fix in test infrastructure only"

## Success Criteria

- [ ] `pytest tests/test_auto_continue.py tests/test_cross_wire_fixes.py` passes (the exact reproduction case from the issue)
- [ ] `pytest tests/` passes with all tests running in a single session
- [ ] The 7 test files no longer contain bare `sys.modules["claude_agent_sdk"] = ...` assignments
- [ ] `sys.modules` cleanup is handled by a conftest fixture with proper teardown
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (test-infra)**
  - Name: test-isolation-builder
  - Role: Implement conftest fixture and clean up test files
  - Agent Type: builder
  - Resume: true

- **Validator (test-infra)**
  - Name: test-isolation-validator
  - Role: Verify test ordering doesn't matter
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add SDK mock cleanup fixture to conftest.py
- **Task ID**: build-conftest-fixture
- **Depends On**: none
- **Assigned To**: test-isolation-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a fixture to `tests/conftest.py` that saves and restores `sys.modules["claude_agent_sdk"]` around each test
- Ensure the fixture is autouse so all tests get the cleanup automatically

### 2. Clean up test_auto_continue.py
- **Task ID**: build-cleanup-auto-continue
- **Depends On**: build-conftest-fixture
- **Assigned To**: test-isolation-builder
- **Agent Type**: builder
- **Parallel**: true (with other cleanup tasks)
- Remove the module-level `sys.modules` hack (lines 15-19)
- Replace with a localized import guard or rely on the conftest fixture

### 3. Clean up remaining 6 test files
- **Task ID**: build-cleanup-remaining
- **Depends On**: build-conftest-fixture
- **Assigned To**: test-isolation-builder
- **Agent Type**: builder
- **Parallel**: true (with task 2)
- Remove the module-level `sys.modules` hack from:
  - `tests/test_silent_failures.py`
  - `tests/unit/test_sdk_client_sdlc.py`
  - `tests/test_lifecycle_transition.py`
  - `tests/test_stage_aware_auto_continue.py`
  - `tests/test_enqueue_continuation.py`
  - `tests/test_session_progress.py`
  - `tests/test_build_validation.py`

### 4. Validate all tests pass in any order
- **Task ID**: validate-test-ordering
- **Depends On**: build-cleanup-auto-continue, build-cleanup-remaining
- **Assigned To**: test-isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_auto_continue.py tests/test_cross_wire_fixes.py -v`
- Run `pytest tests/test_cross_wire_fixes.py tests/test_auto_continue.py -v`
- Run `pytest tests/ -v` (full suite)
- Verify no test depends on mock SDK bleeding between files

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-test-ordering
- **Assigned To**: test-isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- `python -m ruff check tests/`
- `python -m ruff format --check tests/`
- Confirm no remaining bare `sys.modules["claude_agent_sdk"]` assignments

## Validation Commands

- `pytest tests/test_auto_continue.py tests/test_cross_wire_fixes.py -v` - reproduction case passes
- `pytest tests/test_cross_wire_fixes.py tests/test_auto_continue.py -v` - reverse order passes
- `pytest tests/ -v` - full suite passes
- `grep -rn 'sys.modules\["claude_agent_sdk"\] =' tests/` - no bare assignments remain (only in conftest fixture)
- `python -m ruff check tests/ && python -m ruff format --check tests/` - lint passes
