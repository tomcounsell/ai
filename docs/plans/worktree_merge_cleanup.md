---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/199
---

# Worktree Merge Cleanup

## Problem

When merging a PR with `gh pr merge --squash --delete-branch`, the remote branch is deleted but local branch deletion fails because a git worktree still references it:

```
failed to delete local branch session/auto-continue-audit: failed to run git: error: cannot delete branch 'session/auto-continue-audit' used by worktree at '/Users/valorengels/src/ai/.worktrees/auto-continue-audit'
```

**Current behavior:**
After a PR merge, stale worktrees and local branches accumulate. Each requires manual cleanup:
```bash
git worktree remove .worktrees/<slug>
git branch -d session/<slug>
```

**Desired outcome:**
The SDLC merge phase automatically cleans up the worktree and local branch associated with the merged PR's slug, so no manual intervention is needed.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work is fast -- this is a focused bug fix in existing infrastructure with clear scope.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **`cleanup_after_merge()` function**: New function in `agent/worktree_manager.py` that removes the worktree and deletes the local branch for a given slug
- **`scripts/post_merge_cleanup.py` script**: CLI entry point that accepts a slug argument and calls the cleanup function, suitable for use in SDLC merge steps and manual invocation
- **SDLC merge stage integration**: Update the SDLC skill docs to include the cleanup step as part of the merge phase

### Flow

**PR approved** -> Human says "merge" -> SDLC merge step -> `gh pr merge --squash --delete-branch` -> `post_merge_cleanup.py {slug}` removes worktree + local branch -> Clean state

### Technical Approach

- Add `cleanup_after_merge(repo_root, slug)` to `agent/worktree_manager.py` that:
  1. Calls `remove_worktree(repo_root, slug, delete_branch=True)` if the worktree exists
  2. Calls `prune_worktrees(repo_root)` to clean stale references
  3. Falls back to force-deleting the local branch if the worktree was already removed but the branch lingers
  4. Returns a status dict with what was cleaned up
- Create `scripts/post_merge_cleanup.py` as a thin CLI wrapper around `cleanup_after_merge()`
- Update `.claude/skills/sdlc/SKILL.md` to document calling the cleanup script in the MERGE stage
- Add unit tests covering the cleanup scenarios

## Rabbit Holes

- Automatic merge triggering -- this plan only adds cleanup, not auto-merge capability. The human gate remains.
- Remote branch cleanup -- `gh pr merge --delete-branch` already handles the remote. We only handle local.
- Worktree state detection via `git worktree list --porcelain` parsing -- the existing `list_worktrees()` already does this. Reuse it, don't reinvent.

## Risks

### Risk 1: Worktree has uncommitted changes
**Impact:** Force-removing a worktree with uncommitted changes would lose work.
**Mitigation:** The cleanup only runs after a successful merge, meaning all changes are already in the squashed commit. The existing `remove_worktree` uses `--force` which is appropriate here since work is merged.

### Risk 2: Branch already deleted
**Impact:** `git branch -D` fails if the branch doesn't exist.
**Mitigation:** `cleanup_after_merge` will check branch existence before attempting deletion, and handle the "already gone" case gracefully.

## No-Gos (Out of Scope)

- Auto-merge functionality (human gate is preserved)
- Cleaning up worktrees for non-merged PRs
- Modifying the `gh pr merge` command itself
- Cleaning up remote branches (already handled by `--delete-branch`)

## Update System

No update system changes required -- this feature adds a new Python function and script that are purely internal to the development workflow. No new dependencies or config files.

## Agent Integration

No agent integration required -- this is a developer-workflow tool used by the SDLC skill during the merge phase. It does not need MCP server exposure or bridge integration. The agent invokes it through the SDLC skill documentation, not through tool calls.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md` to document post-merge cleanup behavior
- [ ] Add entry to `docs/features/README.md` index table if not already present

### Inline Documentation
- [ ] Docstrings on `cleanup_after_merge()` function
- [ ] Usage comment in `scripts/post_merge_cleanup.py`

## Success Criteria

- [ ] `cleanup_after_merge()` function exists in `agent/worktree_manager.py`
- [ ] `scripts/post_merge_cleanup.py` runs successfully given a slug argument
- [ ] Running cleanup on a slug with an active worktree removes the worktree and branch
- [ ] Running cleanup on a slug with no worktree (already removed) still cleans up the branch
- [ ] Running cleanup on a slug with nothing to clean is a no-op (no errors)
- [ ] SDLC skill docs reference the cleanup step in the merge phase
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (worktree-cleanup)**
  - Name: cleanup-builder
  - Role: Implement `cleanup_after_merge()` function, CLI script, and SDLC doc updates
  - Agent Type: builder
  - Resume: true

- **Validator (worktree-cleanup)**
  - Name: cleanup-validator
  - Role: Verify cleanup function works in all scenarios
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add cleanup_after_merge to worktree_manager.py
- **Task ID**: build-cleanup-function
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `cleanup_after_merge(repo_root: Path, slug: str) -> dict` to `agent/worktree_manager.py`
- Function removes worktree (if exists), prunes stale refs, deletes local branch (if exists)
- Returns dict with keys: `worktree_removed`, `branch_deleted`, `slug`

### 2. Create CLI script
- **Task ID**: build-cli-script
- **Depends On**: build-cleanup-function
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/post_merge_cleanup.py` that accepts a slug as CLI argument
- Calls `cleanup_after_merge()` and prints results
- Exit code 0 on success, 1 on error

### 3. Add unit tests
- **Task ID**: build-tests
- **Depends On**: build-cleanup-function
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add tests in `tests/unit/test_worktree_manager.py`
- Test cases: worktree exists, worktree already removed, branch already deleted, invalid slug

### 4. Update SDLC skill docs
- **Task ID**: build-docs
- **Depends On**: build-cleanup-function
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/sdlc/SKILL.md` merge stage to include cleanup step
- Update `.claude/skills/do-build/PR_AND_CLEANUP.md` to reference post-merge cleanup

### 5. Validate all scenarios
- **Task ID**: validate-all
- **Depends On**: build-cleanup-function, build-cli-script, build-tests, build-docs
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run unit tests
- Verify SDLC docs mention cleanup
- Verify script is executable and has correct usage

## Validation Commands

- `pytest tests/unit/test_worktree_manager.py -v` -- unit tests pass
- `python scripts/post_merge_cleanup.py --help` -- CLI script is functional
- `grep -q "cleanup_after_merge" agent/worktree_manager.py` -- function exists
- `grep -q "post_merge_cleanup" .claude/skills/sdlc/SKILL.md` -- SDLC docs updated
