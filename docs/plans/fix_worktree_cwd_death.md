# Fix Shell CWD Death When Worktree Removed

**Issue:** #301
**Slug:** `fix_worktree_cwd_death`
**Branch:** `session/fix_worktree_cwd_death`

## Problem

When a git worktree is removed during a Claude Code session and the shell's persistent CWD was inside that worktree, every subsequent bash command fails with exit code 1. No recovery is possible within the session.

This happens because Claude Code's bash tool maintains a persistent working directory across commands. When that directory is deleted (by `git worktree remove`), the shell can't execute anything.

## Root Cause

The SDLC merge phase runs:
```bash
python scripts/post_merge_cleanup.py {slug}
```

This calls `cleanup_after_merge()` → `remove_worktree()` → `git worktree remove --force`. If the agent previously ran commands inside the worktree (which `do-build` does), the shell CWD is inside the now-deleted directory.

## Solution

### Change 1: Update SDLC skill merge instructions

Add explicit `cd` to repo root before cleanup:

```bash
# Return to repo root BEFORE cleanup (prevents CWD death)
cd /Users/valorengels/src/ai

# Clean up worktree and branch
python scripts/post_merge_cleanup.py {slug}
```

### Change 2: Update do-build PR_AND_CLEANUP.md

Same fix — ensure `cd` to repo root before any worktree removal.

### Change 3: Add CWD safety to post_merge_cleanup.py

Add `os.chdir(REPO_ROOT)` at the start of `main()` so the script always runs from the repo root, and print a warning reminding the caller to `cd` to a safe directory first.

### Change 4: Add CWD guard to remove_worktree()

In `agent/worktree_manager.py`, check if the current process CWD is inside the worktree being removed, and if so, `os.chdir(repo_root)` first.

## Success Criteria

- [ ] SDLC merge phase explicitly `cd`s to repo root before cleanup
- [ ] `do-build` cleanup explicitly `cd`s to repo root before worktree removal
- [ ] `post_merge_cleanup.py` changes CWD to repo root on startup
- [ ] `remove_worktree()` guards against CWD being inside the worktree
- [ ] Tests pass for worktree manager

## No-Gos

- Do NOT change worktree creation or checkout logic
- Do NOT add complex recovery mechanisms — just prevent the problem

## Documentation

- [ ] No new documentation files needed — this is a bug fix in existing code

## Update System

No update system changes required — this is internal code.

## Agent Integration

No agent integration changes required — the fix modifies existing skill instructions and Python code.
