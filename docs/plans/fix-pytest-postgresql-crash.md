---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-03-06
tracking: https://github.com/valorengels/ai/issues/265
---

# Fix pytest-postgresql Plugin Crash in Worktrees

## Problem

Running `pytest` in a git worktree (e.g., `.worktrees/fix-hook-infinite-loop/`) crashes the entire test runner before any tests execute:

```
ImportError: no pq wrapper available.
Attempts made:
- couldn't import psycopg 'c' implementation: No module named 'psycopg_c'
- couldn't import psycopg 'binary' implementation: No module named 'psycopg_binary'
- couldn't import psycopg 'python' implementation: libpq library not found
```

The `pytest_postgresql` plugin is installed at the system level (not in this project's `.venv`) but gets picked up by pytest anyway. It tries to import `psycopg` which cannot find `libpq`, crashing the test runner on startup -- not just postgresql-related tests, but ALL tests.

**Current behavior:**
The test runner crashes immediately with an `ImportError` when `pytest_postgresql` plugin auto-loads and its `psycopg` dependency cannot find `libpq`. Developers must manually pass `-p no:postgresql` to work around it.

**Desired outcome:**
`pytest` runs cleanly without needing manual flags. The `pytest_postgresql` plugin is disabled by default in this project since no tests use it.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a one-line config fix with clear validation criteria.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **pytest addopts config**: Add `-p no:postgresql` to the existing `addopts` in `pyproject.toml` to disable the plugin at startup
- **Test file cleanup**: Remove redundant `-p no:postgresql` instructions from test file docstrings since the config handles it globally

### Flow

**Developer runs `pytest`** -> pytest reads `pyproject.toml` addopts -> `-p no:postgresql` prevents plugin load -> tests run normally

### Technical Approach

- Add `-p no:postgresql` to the `addopts` field in `[tool.pytest.ini_options]` section of `pyproject.toml`
- This is the safest approach (option 4 from the issue): it prevents the plugin from loading unless explicitly overridden with `-p postgresql`
- Remove any workaround documentation in test files that reference `-p no:postgresql` manually

## Rabbit Holes

- **Installing psycopg-binary**: Would fix the import but adds an unnecessary dependency and may cause other side effects
- **Uninstalling pytest-postgresql globally**: Would fix this project but might break other projects that need it
- **Creating a conftest.py plugin guard**: Over-engineering for what is a simple config flag

## Risks

### Risk 1: Breaks future postgresql tests
**Impact:** If someone adds postgresql tests later, they won't work by default
**Mitigation:** The fix is clearly documented. Adding `-p postgresql` to a specific test run or removing the flag from addopts is trivial. This project has no postgresql tests and no plans for them.

## No-Gos (Out of Scope)

- Uninstalling system-level packages
- Adding psycopg or psycopg-binary as project dependencies
- Modifying worktree setup scripts

## Update System

No update system changes required -- this is a `pyproject.toml` config change that propagates automatically via git pull during the normal update process.

## Agent Integration

No agent integration required -- this is a developer tooling configuration change. No MCP servers, bridge code, or tool wrappers are affected.

## Documentation

- [ ] Update `docs/features/README.md` index if a testing-related feature doc exists
- [ ] Add inline comment in `pyproject.toml` explaining why `-p no:postgresql` is needed

Note: This is a minor config fix. No standalone feature doc is warranted. The inline comment serves as the documentation.

## Success Criteria

- [ ] `pytest tests/` runs without `-p no:postgresql` flag and does not crash with ImportError
- [ ] `pyproject.toml` contains `-p no:postgresql` in addopts
- [ ] Existing tests still pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (config-fix)**
  - Name: config-builder
  - Role: Update pyproject.toml pytest configuration
  - Agent Type: builder
  - Resume: true

- **Validator (test-runner)**
  - Name: test-validator
  - Role: Verify pytest runs without crashes
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update pyproject.toml addopts
- **Task ID**: build-config
- **Depends On**: none
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `-p no:postgresql` to the `addopts` field in `[tool.pytest.ini_options]`
- Add inline comment explaining the reason

### 2. Clean up workaround references
- **Task ID**: build-cleanup
- **Depends On**: build-config
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove manual `-p no:postgresql` instructions from test file docstrings (e.g., `tests/test_sdlc_mode.py`)

### 3. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-cleanup
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/` without manual flags
- Verify no ImportError from psycopg/postgresql
- Verify all existing tests pass

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-fix
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Validation Commands

- `pytest tests/ -v --tb=short` - Verify tests run without crash and pass
- `grep 'no:postgresql' pyproject.toml` - Verify config change is present
