---
status: Review
type: feature
appetite: Small
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/306
last_comment_id:
---

# Workspace Safety Invariants

## Problem

Agent sessions launch Claude Code subprocesses with a `cwd` parameter, but there's no pre-launch validation that the directory exists, is contained within the expected repo root, or has a sanitized name. This has caused real failures — issue #301 documented CWD death when a worktree was removed under a running process.

**Current behavior:**
- `ValorAgent` accepts any path as `working_dir` without validation
- `_execute_job()` in job_queue.py passes `job.working_dir` directly to agent launch
- No path containment check prevents operations outside the repo root
- Slug sanitization exists in `worktree_manager.py` but isn't enforced at agent launch

**Desired outcome:**
- Every agent subprocess launch validates CWD exists and is within the allowed root
- Path containment prevents directory traversal or writes to system paths
- Slug sanitization is enforced consistently across all path-generating code
- Violations are logged as warnings (defensive, not crashy)

## Prior Art

- **PR #304**: "Prevent shell CWD death when worktree is removed" — Added CWD guard to `remove_worktree()` and `post_merge_cleanup.py`. Fixed the symptom (CWD death after removal) but didn't add pre-launch validation.
- **Issue #301**: The original CWD death bug. PR #304 closed it with a reactive fix; this issue adds the proactive/systemic fix.

## Data Flow

1. **Entry point**: Telegram message → bridge → `enqueue_job()` sets `working_dir` from project config
2. **Job queue**: `_execute_job()` reads `job.working_dir`, passes to `get_agent_response_sdk()`
3. **SDK client**: `ValorAgent.__init__()` stores `working_dir` as `Path`, `_create_options()` passes `cwd=str(self.working_dir)` to `ClaudeAgentOptions`
4. **Subprocess**: Claude Code CLI spawns with the given `cwd`

The validation should happen at step 3 (before `ClaudeAgentOptions` is created) and optionally at step 2 (early fail).

## Architectural Impact

- **New dependencies**: None — pure stdlib (pathlib, os, re)
- **Interface changes**: New `validate_workspace()` function in `worktree_manager.py`, called from `sdk_client.py` and `job_queue.py`
- **Coupling**: Minimal — adds a validation call at existing integration points
- **Data ownership**: No change
- **Reversibility**: Trivially reversible — remove the validation calls

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

- **`validate_workspace()`**: Single function that checks CWD exists, is within allowed root, and has valid path components
- **Pre-launch hook**: Called in `ValorAgent._create_options()` before building `ClaudeAgentOptions`
- **Job-level check**: Called in `_execute_job()` before agent invocation

### Flow

**Job enqueued** → `_execute_job()` validates workspace → `ValorAgent.__init__()` stores path → `_create_options()` re-validates before subprocess → **Claude Code launches with verified CWD**

### Technical Approach

- Add `validate_workspace(path: Path, allowed_root: Path) -> Path` to `agent/worktree_manager.py`
  - Resolves path to absolute
  - Checks `path.exists()` and `path.is_dir()`
  - Verifies `allowed_root` is a parent of `path` (path containment)
  - Validates all path components match `VALID_SLUG_RE` (for worktree paths only)
  - Returns the resolved path on success
  - Logs warning and falls back to `allowed_root` on failure (don't crash)
- Call from `ValorAgent.__init__()` to validate `working_dir`
- Call from `_execute_job()` to validate `job.working_dir` before agent launch
- Use `/Users/valorengels/src` as the allowed root (covers all projects)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `validate_workspace()` catches `OSError` from `Path.resolve()` on broken symlinks
- [ ] Invalid CWD logs warning and falls back to allowed_root (no crash)

### Empty/Invalid Input Handling
- [ ] `validate_workspace(None, root)` falls back gracefully
- [ ] `validate_workspace(Path(""), root)` falls back gracefully
- [ ] Path with `..` traversal is rejected

### Error State Rendering
- [ ] Validation failures log structured warnings with the invalid path and reason

## Rabbit Holes

- **Sandboxing agent file operations at runtime**: This is about pre-launch validation, not runtime containment. Runtime sandboxing is a separate, much larger project.
- **Validating every path component against VALID_SLUG_RE for non-worktree paths**: Regular project paths (like `/Users/valorengels/src/ai`) contain characters not in slug format. Only validate slug components for worktree paths.
- **Making validation blocking/crashy**: Log and fallback, don't crash the agent session.

## Risks

### Risk 1: False positives on valid project paths
**Impact:** Agent fails to launch for legitimate working directories
**Mitigation:** Path containment uses a generous root (`/Users/valorengels/src`), and validation falls back to allowed_root rather than crashing.

### Risk 2: Symlink resolution changes path identity
**Impact:** A valid symlinked path could resolve outside the allowed root
**Mitigation:** Use `Path.resolve()` to follow symlinks before containment check. Log the original and resolved paths for debugging.

## Race Conditions

No race conditions identified — validation is synchronous and runs in the same thread as the agent launch. The CWD could theoretically be removed between validation and subprocess spawn, but this is already handled by the existing CWD death guard in PR #304.

## No-Gos (Out of Scope)

- Runtime file operation sandboxing (separate project)
- Restricting which files the agent can read/write after launch
- Changing the worktree directory structure
- Adding new dependencies

## Update System

No update system changes required — this adds internal validation functions with no new dependencies or config files.

## Agent Integration

No agent integration required — this is internal infrastructure that runs before the agent launches. The agent itself doesn't need to invoke these functions.

## Documentation

- [ ] Create `docs/features/workspace-safety-invariants.md` describing the three invariants
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Code comments on `validate_workspace()` explaining each check

## Success Criteria

- [ ] CWD is validated before every agent subprocess launch
- [ ] Path containment prevents operations outside `/Users/valorengels/src`
- [ ] Slug sanitization is enforced consistently for worktree paths
- [ ] Violations are logged as warnings with fallback behavior
- [ ] Existing tests continue to pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (safety-invariants)**
  - Name: safety-builder
  - Role: Implement validate_workspace() and integrate into sdk_client.py and job_queue.py
  - Agent Type: builder
  - Resume: true

- **Validator (safety-invariants)**
  - Name: safety-validator
  - Role: Verify all three invariants are enforced, test edge cases
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement validate_workspace()
- **Task ID**: build-validate-workspace
- **Depends On**: none
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `validate_workspace(path, allowed_root)` to `agent/worktree_manager.py`
- Implement CWD existence check, path containment, and slug validation for worktree paths
- Log warnings on violations, fall back to allowed_root
- Add unit tests in `tests/test_worktree_manager.py`

### 2. Integrate into agent launch path
- **Task ID**: build-integrate
- **Depends On**: build-validate-workspace
- **Assigned To**: safety-builder
- **Agent Type**: builder
- **Parallel**: false
- Call `validate_workspace()` in `ValorAgent.__init__()` before storing working_dir
- Call `validate_workspace()` in `_execute_job()` before agent launch
- Use `/Users/valorengels/src` as the allowed root

### 3. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-integrate
- **Assigned To**: safety-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all three invariants are enforced
- Run existing tests to confirm no regressions
- Check that violations produce log warnings

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: safety-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/workspace-safety-invariants.md`
- Add entry to `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| validate_workspace exists | `grep -c 'def validate_workspace' agent/worktree_manager.py` | output contains 1 |
| SDK client calls validate | `grep -c 'validate_workspace' agent/sdk_client.py` | output contains 1 |
| Job queue calls validate | `grep -c 'validate_workspace' agent/job_queue.py` | output contains 1 |
