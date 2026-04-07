---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/230
---

# Fix Daydream Report Duplicate Issue Creation

## Problem

The daydream system creates duplicate GitHub issues because deduplication is broken in two ways.

**Current behavior:**
1. `issue_exists_for_date(date)` checks the default repo (no `cwd` parameter) while `create_daydream_issue(findings, date, cwd)` creates issues in a project-specific repo via `cwd`. The dedup check runs against the wrong repo.
2. When daydream processes multiple projects rapidly, `gh issue create` calls complete before GitHub's search index updates. The next project's `issue_exists_for_date` check (even if `cwd` were correct) may not find the just-created issue. Evidence: issues #219/#220 created the same second, #140/#141/#142 triple on 2026-02-18.

**Desired outcome:**
- `issue_exists_for_date` checks the correct repo by accepting and using a `cwd` parameter
- A dedup guard prevents rapid sequential creates from racing the search index

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

- **cwd passthrough**: Add `cwd` parameter to `issue_exists_for_date()` and pass it to `subprocess.run`
- **Caller fix**: Update `create_daydream_issue()` to forward its `cwd` to `issue_exists_for_date()`
- **Race condition guard**: Track created issue titles in-memory during a single daydream run and check before calling `gh issue create`

### Flow

**Daydream run starts** -> per-project loop -> `create_daydream_issue(findings, date, cwd)` -> `issue_exists_for_date(date, cwd)` checks correct repo -> in-memory set check -> create if not duplicate -> add title to in-memory set -> next project

### Technical Approach

1. Add `cwd: str | None = None` parameter to `issue_exists_for_date()` and pass it as `cwd=cwd` to the `subprocess.run` call on line 59-74
2. In `create_daydream_issue()`, change line 106 from `issue_exists_for_date(date)` to `issue_exists_for_date(date, cwd=cwd)`
3. Add a module-level `_created_this_run: set[str]` that tracks `(date, cwd)` tuples created during the current process. Check it before calling `gh issue create` and add to it after successful creation. Provide a `reset_dedup_guard()` function for testing.
4. In `DaydreamRunner.step_create_github_issue()`, call `reset_dedup_guard()` at the start of each daydream run to clear state from any previous run in the same process.

## Rabbit Holes

- Do not add delays/sleeps between project issue creation -- the in-memory guard is sufficient and faster
- Do not try to fix GitHub's search index lag -- that is external infrastructure
- Do not redesign the multi-repo iteration architecture

## Risks

### Risk 1: Backward compatibility
**Impact:** Callers of `issue_exists_for_date` without `cwd` would break
**Mitigation:** `cwd` defaults to `None`, preserving existing behavior

### Risk 2: In-memory guard doesn't persist across runs
**Impact:** If daydream crashes and restarts within the same minute, could create duplicates
**Mitigation:** The existing `issue_exists_for_date` GitHub search check handles cross-run dedup; the in-memory guard only handles the within-run race condition

## No-Gos (Out of Scope)

- Cleaning up existing duplicate issues (#219/#220, #140/#141/#142)
- Adding persistent (file/Redis) dedup tracking
- Changing the daydream step execution order

## Update System

No update system changes required -- this is a bug fix to existing internal scripts with no new dependencies or config files.

## Agent Integration

No agent integration required -- this is a fix to the daydream maintenance scripts which run independently of the agent/bridge system.

## Documentation

- [ ] Update `docs/features/daydream.md` to mention the dedup guard behavior
- [ ] Code comments on the in-memory dedup guard explaining why it exists

## Success Criteria

- [ ] `issue_exists_for_date(date, cwd="/path/to/project")` passes `cwd` to `subprocess.run`
- [ ] `create_daydream_issue()` forwards `cwd` to `issue_exists_for_date()`
- [ ] In-memory dedup guard prevents duplicate creation within a single run
- [ ] `reset_dedup_guard()` clears the guard (for testing and per-run reset)
- [ ] All existing tests pass
- [ ] New tests cover: cwd passthrough, in-memory guard, guard reset
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (dedup-fix)**
  - Name: dedup-builder
  - Role: Implement cwd passthrough and dedup guard
  - Agent Type: builder
  - Resume: true

- **Validator (dedup-fix)**
  - Name: dedup-validator
  - Role: Verify fix correctness and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix cwd passthrough in daydream_report.py
- **Task ID**: build-cwd-fix
- **Depends On**: none
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `cwd` parameter to `issue_exists_for_date()` signature
- Pass `cwd` to `subprocess.run` in `issue_exists_for_date()`
- Update `create_daydream_issue()` to pass `cwd` to `issue_exists_for_date()`

### 2. Add in-memory dedup guard
- **Task ID**: build-dedup-guard
- **Depends On**: build-cwd-fix
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add module-level `_created_this_run` set
- Add `reset_dedup_guard()` function
- Check guard before `gh issue create` in `create_daydream_issue()`
- Add to guard after successful creation
- Call `reset_dedup_guard()` at start of `step_create_github_issue()`

### 3. Update tests
- **Task ID**: build-tests
- **Depends On**: build-dedup-guard
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add tests for `issue_exists_for_date` with `cwd` parameter
- Add tests for in-memory dedup guard
- Add test for `reset_dedup_guard()`
- Verify existing tests still pass

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: dedup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Validation Commands

- `pytest tests/test_daydream_report.py -v` - Verify report tests pass
- `pytest tests/test_daydream_multi_repo.py -v` - Verify multi-repo tests pass
- `ruff check scripts/daydream_report.py scripts/daydream.py` - Lint check
- `black --check scripts/daydream_report.py scripts/daydream.py` - Format check
