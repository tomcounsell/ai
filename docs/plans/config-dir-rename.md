---
status: Complete
type: chore
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/452
last_comment_id:
---

# Rename Desktop Config Dir from claude_code to Valor

## Problem

The project identity is "Valor" but one source file and one external config file still reference the legacy `~/Desktop/claude_code/` directory. A stale git-tracked worktree also contains old references. The old directory still exists on the primary machine alongside the new one.

**Current behavior:**
- `scripts/update/verify.py` has 2 hardcoded references to `~/Desktop/claude_code/`
- `~/.claude/settings.json` statusline command points to `/Users/valorengels/Desktop/claude_code/statusline-command.sh`
- `.claude/worktrees/agent-a1f22e42/` is a stale git-tracked worktree with old references throughout
- `~/Desktop/claude_code/` still exists on the primary machine with legacy files

**Desired outcome:**
Zero references to `Desktop/claude_code` in the codebase or external configs. Old directory deleted after verification. Regression test prevents future drift.

## Prior Art

- **PR #438**: "Config consolidation: eliminate hardcoded paths, unify settings" -- Merged 2026-03-20. This PR migrated most files from `Desktop/claude_code` to `Desktop/Valor` but missed `scripts/update/verify.py`, the statusline path in `settings.json`, and did not clean up the stale worktree.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Straightforward find-and-replace plus cleanup. No architectural decisions needed.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **verify.py fix**: Replace 2 `claude_code` references with `Valor`
- **Stale worktree removal**: Delete the git-tracked `.claude/worktrees/agent-a1f22e42/` directory
- **Regression test**: Unit test that greps the codebase for any remaining `claude_code` references
- **Update skill**: Add `settings.json` statusline path migration to the update/verify flow

### Flow

**Build** -> Update verify.py -> Remove stale worktree -> Add regression test -> Update settings.json handling -> **Verify** -> Deploy via /update -> Delete old directory on all machines

### Technical Approach

- Direct string replacement in `scripts/update/verify.py`: `Desktop/claude_code` -> `Desktop/Valor`
- `git rm -rf .claude/worktrees/agent-a1f22e42/` to remove the stale worktree from tracking
- New test in `tests/unit/test_no_legacy_paths.py` that runs `git grep 'Desktop/claude_code'` and asserts zero matches
- Add a `settings.json` migration step to `scripts/update/verify.py` or `scripts/update/run.py` that rewrites the statusline path

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope -- this is a string replacement and file deletion task

### Empty/Invalid Input Handling
- [ ] The `settings.json` migration should handle the case where `statusLine` key doesn't exist or already points to Valor
- [ ] The `verify.py` sync function should handle missing `~/Desktop/Valor/claude_oauth_config.json` gracefully (it already does via the existing `if not source.exists()` check)

### Error State Rendering
- [ ] If `settings.json` migration fails, it should log a warning rather than blocking the update

## Test Impact

- [ ] `tests/unit/test_config_consolidation.py` -- no changes needed; these tests already reference `Desktop/Valor/`
- [ ] `tests/unit/test_persona_loading.py` -- no changes needed; already references `Desktop/Valor/`

No existing tests are affected -- the only file being modified (`verify.py`) has no direct unit test coverage for the `sync_claude_oauth` function, and the stale worktree removal has no test implications.

## Rabbit Holes

- Do not attempt to audit or consolidate the contents of `~/Desktop/claude_code/` vs `~/Desktop/Valor/` -- the issue states files are already copied over
- Do not refactor `verify.py` beyond the path rename -- that is a separate concern
- Do not add migration logic for the old directory deletion to the update script -- that is a manual post-verification step

## Risks

### Risk 1: Settings.json path change breaks statusline on machines not yet updated
**Impact:** Statusline shows blank/error until machine runs /update
**Mitigation:** The /update skill handles `settings.json` -- the migration step runs during update, so each machine gets the fix when it updates. No service disruption.

### Risk 2: Stale worktree removal might affect active worktree operations
**Impact:** If a worktree operation is using `agent-a1f22e42`, removing it breaks that operation
**Mitigation:** Check `git worktree list` before removal. The worktree dates from March 10 and is stale.

## Race Conditions

No race conditions identified -- all operations are synchronous file edits and git operations.

## No-Gos (Out of Scope)

- Deleting `~/Desktop/claude_code/` on remote machines -- that is a manual post-deploy verification step per the issue
- Refactoring the OAuth sync function in `verify.py`
- Moving `settings.json` management into the repo

## Update System

The `/update` skill needs a one-line change: after pulling code, update `~/.claude/settings.json` to replace any `Desktop/claude_code` path with `Desktop/Valor` in the statusline command. This should be added to `scripts/update/run.py` or `scripts/update/verify.py` as a post-update migration step.

## Agent Integration

No agent integration required -- this is a codebase hygiene chore with no new tools or MCP changes.

## Documentation

- [ ] No new feature docs needed -- this is a rename, not a new feature
- [ ] Verify `CLAUDE.md` already references `~/Desktop/Valor/` (confirmed: it does)
- [ ] Docstrings in `scripts/update/verify.py::sync_claude_oauth` updated to reference new path

## Success Criteria

- [ ] Zero references to `Desktop/claude_code` in git-tracked files (`git grep 'Desktop/claude_code'` returns nothing)
- [ ] `~/.claude/settings.json` statusline path points to `~/Desktop/Valor/statusline-command.sh`
- [ ] Regression test exists and passes: `pytest tests/unit/test_no_legacy_paths.py -x`
- [ ] Stale worktree `.claude/worktrees/agent-a1f22e42/` removed from git tracking
- [ ] `scripts/update/verify.py` references `~/Desktop/Valor/` for OAuth config
- [ ] Tests pass (`/do-test`)
- [ ] Lint and format clean

## Team Orchestration

### Team Members

- **Builder (rename)**
  - Name: rename-builder
  - Role: Execute path renames, remove stale worktree, add regression test, update settings.json migration
  - Agent Type: builder
  - Resume: true

- **Validator (rename)**
  - Name: rename-validator
  - Role: Verify zero legacy references, test passes, statusline works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix verify.py and remove stale worktree
- **Task ID**: build-rename
- **Depends On**: none
- **Validates**: tests/unit/test_no_legacy_paths.py (create)
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `Desktop/claude_code` with `Desktop/Valor` in `scripts/update/verify.py` (2 occurrences: line 335 docstring, line 346 Path construction, line 356 error message)
- Update the function docstring on line 332 to say "Desktop Valor dir" instead of "Desktop claude_code dir"
- Run `git rm -rf .claude/worktrees/agent-a1f22e42/`
- Create `tests/unit/test_no_legacy_paths.py` with a test that runs `git grep 'Desktop/claude_code'` and asserts zero matches (excluding the test file itself)

### 2. Add settings.json migration to update flow
- **Task ID**: build-settings-migration
- **Depends On**: none
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a function to `scripts/update/run.py` or `scripts/update/verify.py` that reads `~/.claude/settings.json`, replaces `Desktop/claude_code` with `Desktop/Valor` in the statusline command path, and writes it back
- Call this function during the update verify/run flow

### 3. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-rename, build-settings-migration
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `git grep 'Desktop/claude_code'` and confirm zero results
- Run `pytest tests/unit/test_no_legacy_paths.py -x`
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify `~/.claude/settings.json` statusline path is correct

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: rename-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update docstring in `verify.py::sync_claude_oauth`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No legacy paths | `git grep 'Desktop/claude_code'` | exit code 1 |
| Tests pass | `pytest tests/unit/test_no_legacy_paths.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Full tests | `pytest tests/unit/ -x -q` | exit code 0 |

---

## Open Questions

1. Should the `settings.json` statusline migration be a standalone function in `verify.py`, or should it be integrated into the existing `sync_claude_oauth` function? (Leaning toward standalone -- they are different concerns.)
2. The old `~/Desktop/claude_code/` directory contains files like `check_sdk_jobs.py`, `output-styles/`, `tool-hooks/`, and `README.md` that may not have equivalents in `~/Desktop/Valor/`. Should we verify parity before recommending deletion, or are those known-obsolete files?
