---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-08
tracking: https://github.com/valorengels/ai/issues/301
---

# Fix Shell CWD Death on Worktree Removal

## Problem

When a git worktree is removed during a Claude Code session (e.g., after merging a PR and running `post_merge_cleanup.py`), if the shell's persistent working directory was inside that worktree, every subsequent bash command fails with exit code 1 because the CWD no longer exists on the filesystem.

**Current behavior:**
After `cleanup_after_merge()` removes `.worktrees/{slug}/`, the shell's CWD points to a deleted directory. All subsequent bash commands fail silently. Only non-bash tools (Read, Glob, Grep) continue to work. The session is partially broken with no recovery path.

**Desired outcome:**
Before removing a worktree directory, the cleanup code changes the shell's CWD to the repository root. This prevents the CWD-death scenario entirely.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (code review)

Solo dev work is fast -- this is a targeted two-function fix with clear test cases.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Safe CWD guard in `remove_worktree()`**: Before calling `git worktree remove`, change the subprocess CWD to the repo root (already the case -- subprocess calls use `cwd=repo_root`). The real fix is to also emit an `os.chdir()` if the *process* CWD is inside the worktree being removed.
- **Safe CWD guard in `cleanup_after_merge()`**: Same protection at the higher-level cleanup entry point, ensuring the process CWD is safe before any removal begins.
- **`post_merge_cleanup.py` script guard**: Add an `os.chdir(REPO_ROOT)` at script entry to ensure the Python process CWD is always safe before cleanup begins.

### Flow

**Merge PR** -> `post_merge_cleanup.py {slug}` -> `os.chdir(repo_root)` -> `cleanup_after_merge()` -> checks if `os.getcwd()` is inside worktree dir -> if yes, `os.chdir(repo_root)` -> removes worktree safely -> shell CWD remains valid

### Technical Approach

- Add a helper function `_ensure_safe_cwd(repo_root, worktree_dir)` in `worktree_manager.py` that:
  1. Gets the current CWD via `os.getcwd()` (wrapped in try/except for already-deleted CWD)
  2. Checks if the CWD is inside or equal to the worktree directory being removed
  3. If yes, calls `os.chdir(repo_root)` and logs a warning
- Call `_ensure_safe_cwd()` at the top of `remove_worktree()` before any removal operations
- Call `_ensure_safe_cwd()` at the top of `cleanup_after_merge()` as a belt-and-suspenders guard
- Add `os.chdir(REPO_ROOT)` to `post_merge_cleanup.py` main() as an early safety measure
- Update the SDLC skill doc and do-build PR_AND_CLEANUP.md to include a `cd` to repo root before calling cleanup

**Important nuance:** The subprocess `cwd=repo_root` parameter already ensures git commands themselves run from a valid directory. The bug is that the *parent process* (or the persistent shell in Claude Code) has its CWD inside the worktree. The `os.chdir()` fix addresses the Python process CWD. For the Claude Code shell (which is a separate process), the SDLC skill instructions must include an explicit `cd /path/to/repo` before calling the cleanup script.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `os.getcwd()` can raise `FileNotFoundError` when the CWD is already deleted -- the `_ensure_safe_cwd()` function must handle this by calling `os.chdir(repo_root)` in the except block
- [ ] No `except Exception: pass` blocks in scope

### Empty/Invalid Input Handling
- [ ] `_ensure_safe_cwd()` handles the case where `worktree_dir` does not exist (already removed)
- [ ] `_ensure_safe_cwd()` handles `repo_root` being the same as `worktree_dir` (degenerate case -- should not chdir)

### Error State Rendering
- [ ] When CWD is rescued, a warning log message is emitted so the behavior is observable
- [ ] The function returns a boolean indicating whether a chdir was performed (useful for tests)

## Rabbit Holes

- **Fixing the Claude Code shell CWD directly**: The Claude Code CLI spawns its own bash shell subprocess. We cannot control that shell's CWD from Python. The only thing we can do is ensure the SDLC skill instructions include an explicit `cd` command. Do not attempt to hack the SDK client to reset shell CWD.
- **Making `os.chdir()` thread-safe**: `os.chdir()` is process-wide. In theory, concurrent sessions in the same process could conflict. In practice, the bridge runs one Claude Code subprocess per session, so this is not a real concern. Do not add threading locks.

## Risks

### Risk 1: `os.chdir()` affects the entire process
**Impact:** If the bridge or another component relies on a specific CWD, changing it could cause side effects.
**Mitigation:** The bridge already uses absolute paths everywhere. The `os.chdir()` only happens inside `post_merge_cleanup.py` (a standalone script) or inside the worktree manager functions (which use absolute paths via `repo_root`). No side effects expected.

### Risk 2: CWD already deleted before cleanup runs
**Impact:** `os.getcwd()` raises `FileNotFoundError` before we can check it.
**Mitigation:** `_ensure_safe_cwd()` wraps `os.getcwd()` in a try/except and unconditionally does `os.chdir(repo_root)` on error.

## Race Conditions

No race conditions identified -- all operations are synchronous and single-threaded. The `post_merge_cleanup.py` script runs as a standalone process, and `cleanup_after_merge()` is called synchronously within a single session's flow.

## No-Gos (Out of Scope)

- Modifying the Claude Code SDK or CLI to handle CWD recovery
- Adding automatic CWD recovery to the bridge process itself
- Handling CWD death for worktrees in other repos (cross-repo builds manage their own cleanup)

## Update System

No update system changes required -- this fix modifies existing files (`agent/worktree_manager.py`, `scripts/post_merge_cleanup.py`) and skill docs. The update script will pull these changes automatically.

## Agent Integration

No agent integration required -- this is a fix to internal cleanup code. No MCP servers, tools, or bridge changes needed.

## Documentation

- [ ] Update `docs/features/session-isolation.md` to mention CWD safety in the cleanup section
- [ ] Add entry to `docs/features/README.md` if not already present for session isolation

## Success Criteria

- [ ] `_ensure_safe_cwd()` function exists in `agent/worktree_manager.py`
- [ ] `_ensure_safe_cwd()` handles `FileNotFoundError` from `os.getcwd()`
- [ ] `_ensure_safe_cwd()` returns True when CWD was inside worktree (and was changed)
- [ ] `remove_worktree()` calls `_ensure_safe_cwd()` before removal
- [ ] `cleanup_after_merge()` calls `_ensure_safe_cwd()` before removal
- [ ] `post_merge_cleanup.py` calls `os.chdir(REPO_ROOT)` at entry
- [ ] SDLC skill doc includes `cd` before cleanup commands
- [ ] Unit tests cover: CWD inside worktree, CWD outside worktree, CWD already deleted
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (worktree-cwd-fix)**
  - Name: cwd-fixer
  - Role: Implement `_ensure_safe_cwd()` and integrate into cleanup functions
  - Agent Type: builder
  - Resume: true

- **Validator (worktree-cwd-fix)**
  - Name: cwd-validator
  - Role: Verify CWD safety across all cleanup paths
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `_ensure_safe_cwd()` to worktree_manager.py
- **Task ID**: build-ensure-safe-cwd
- **Depends On**: none
- **Assigned To**: cwd-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `_ensure_safe_cwd(repo_root: Path, worktree_dir: Path) -> bool` function
- Handle `FileNotFoundError` from `os.getcwd()` (CWD already deleted)
- Check if CWD is inside worktree_dir using path prefix matching
- Call `os.chdir(repo_root)` when needed, log a warning, return True
- Integrate call into `remove_worktree()` before `git worktree remove`
- Integrate call into `cleanup_after_merge()` before removal steps

### 2. Update `post_merge_cleanup.py`
- **Task ID**: build-script-guard
- **Depends On**: none
- **Assigned To**: cwd-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `os.chdir(REPO_ROOT)` early in `main()` before calling `cleanup_after_merge()`

### 3. Update SDLC skill docs
- **Task ID**: build-skill-docs
- **Depends On**: none
- **Assigned To**: cwd-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/sdlc/SKILL.md` merge section to include `cd` before cleanup
- Update `.claude/skills/do-build/PR_AND_CLEANUP.md` cleanup section to include `cd`

### 4. Add unit tests
- **Task ID**: build-tests
- **Depends On**: build-ensure-safe-cwd
- **Assigned To**: cwd-fixer
- **Agent Type**: builder
- **Parallel**: false
- Test: CWD inside worktree -> chdir performed, returns True
- Test: CWD outside worktree -> no chdir, returns False
- Test: CWD already deleted (FileNotFoundError) -> chdir to repo_root, returns True
- Test: `remove_worktree()` calls `_ensure_safe_cwd()`
- Test: `cleanup_after_merge()` calls `_ensure_safe_cwd()`

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-tests, build-script-guard, build-skill-docs
- **Assigned To**: cwd-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worktree_manager.py -v`
- Run `python -m ruff check agent/worktree_manager.py scripts/post_merge_cleanup.py`
- Verify `_ensure_safe_cwd` is called in both `remove_worktree()` and `cleanup_after_merge()`
- Verify `post_merge_cleanup.py` has `os.chdir()` call

## Validation Commands

- `pytest tests/unit/test_worktree_manager.py -v` - unit tests pass
- `python -m ruff check agent/worktree_manager.py scripts/post_merge_cleanup.py` - no lint errors
- `grep -q "_ensure_safe_cwd" agent/worktree_manager.py` - function exists
- `grep -q "os.chdir" scripts/post_merge_cleanup.py` - script has CWD guard
