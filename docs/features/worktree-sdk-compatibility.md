# Experiment: Git Worktree SDK Compatibility

**Date:** 2026-02-10
**Claude Code version:** 2.1.38
**Branch:** build/session-isolation
**Issue:** #62 (Experiments 1-3)
**Plan:** docs/plans/session-isolation.md

## Objective

Determine whether the Claude Agent SDK can run in git worktree directories, and which approach (if any) is required to make it work. The original concern (issue #62) was that the SDK previously crashed with exit code 1 when `.claude/` config was missing in worktree directories.

## Background

Git worktrees create a secondary working directory that shares the same `.git` repository. In a worktree:
- `.git` is a **file** (not a directory) containing `gitdir: /path/to/main/.git/worktrees/<name>`
- Tracked files are checked out normally (including `.claude/` if tracked)
- Untracked/gitignored files (like `.claude/settings.local.json`) are NOT present

In this repository, `.claude/` is tracked in git, so worktrees automatically receive the directory contents on checkout.

## Baseline Test

Before running worktree experiments, confirmed the SDK works from the main repo:

```bash
claude --print "say the word hello"
```

**Output:** `hello`
**Exit code:** 0
**Result:** PASS

## Experiment 1: Copy .claude to Worktree

**Approach:** Create worktree, copy the full `.claude/` directory from main repo (overwriting the git-tracked version to include `settings.local.json` and any untracked files).

**Commands:**
```bash
git worktree add .worktrees/test-exp1 -b session/test-exp1
cp -r .claude .worktrees/test-exp1/
cd .worktrees/test-exp1 && claude --print "say the word hello"
```

**Note:** The `cp -r .claude` over the existing `.claude/` directory created a nested `.claude/.claude/` because the target already existed. This required cleanup. A better approach would be `cp -r .claude/* .worktrees/test-exp1/.claude/` or `rsync`.

**Output:** `hello`
**Exit code:** 0
**Result:** PASS

## Experiment 2: Symlink .claude

**Approach:** Create worktree, remove the git-tracked `.claude/`, replace with a symlink to the main repo's `.claude/`.

**Commands:**
```bash
git worktree add .worktrees/test-exp2 -b session/test-exp2
rm -rf .worktrees/test-exp2/.claude
ln -s /Users/valorengels/src/ai/.claude .worktrees/test-exp2/.claude
cd .worktrees/test-exp2 && claude --print "say the word hello"
```

**Output:** `hello`
**Exit code:** 0
**Result:** PASS

**Note:** Symlink uses absolute path. The SDK follows symlinks correctly and reads all config files.

## Experiment 3: --cwd Flag

**Approach:** Check if the Claude CLI supports a `--cwd` flag to specify working directory without actually changing directories.

**Commands:**
```bash
claude --help 2>&1 | grep -i cwd
claude --cwd .worktrees/test-exp3 --print "say the word hello"
```

**Output:** `error: unknown option '--cwd'`
**Exit code:** 1
**Result:** FAIL (flag does not exist)

**Note:** The `--cwd` flag is not supported in Claude Code v2.1.38. The available alternative is `--add-dir` which adds directories for tool access but does not change the working directory. Using `--add-dir` from the main repo to point to a worktree was tested and works (exit code 0), but it runs in the main repo's context, not the worktree's context.

## Experiment 4: Bare Worktree (No Modifications)

**Approach:** Create worktree, make no modifications to `.claude/`, and run the SDK directly. This tests whether the git-tracked `.claude/` is sufficient.

**Commands:**
```bash
git worktree add .worktrees/test-exp3 -b session/test-exp3
cd .worktrees/test-exp3 && claude --print "say the word hello"
```

**Output:** `hello`
**Exit code:** 0
**Result:** PASS

## Experiment 5: Worktree with .claude Removed

**Approach:** Create worktree, completely delete `.claude/`, and run the SDK. This simulates a repo where `.claude/` is NOT tracked in git.

**Commands:**
```bash
git worktree add .worktrees/test-exp4 -b session/test-exp4
rm -rf .worktrees/test-exp4/.claude
cd .worktrees/test-exp4 && claude --print "say the word hello"
```

**Output:** `hello`
**Exit code:** 0
**Result:** PASS

**Key finding:** The SDK no longer crashes even when `.claude/` is completely absent. This means the original crash from issue #62 has been fixed in Claude Code v2.1.38.

## Summary Table

| # | Approach | Exit Code | Result | Notes |
|---|----------|-----------|--------|-------|
| Baseline | Main repo (control) | 0 | PASS | Expected behavior |
| 1 | Copy .claude to worktree | 0 | PASS | Works but `cp -r` creates nested dirs; use rsync |
| 2 | Symlink .claude | 0 | PASS | Clean approach, shares config |
| 3 | --cwd flag | 1 | FAIL | Flag does not exist in v2.1.38 |
| 4 | Bare worktree (no modifications) | 0 | PASS | Git-tracked .claude suffices |
| 5 | .claude completely removed | 0 | PASS | SDK tolerates missing .claude |

## Recommendation

**Use the bare worktree approach (Experiment 4) -- no special handling needed.**

The Claude Agent SDK v2.1.38 works correctly in git worktrees with no modifications required. The `.claude/` directory, when tracked in git, is automatically present in worktrees. Even if it were absent, the SDK no longer crashes.

For the session-isolation feature in issue #62, the implementation should:

1. **Create worktrees with `git worktree add`** -- `.claude/` will be present from the git checkout
2. **Optionally copy `settings.local.json`** into the worktree's `.claude/` if local settings (not tracked in git) are needed for the session
3. **No symlinks required** -- symlinks work but add unnecessary complexity and can cause issues with git status showing modifications
4. **Use `cd` to change into the worktree** before invoking `claude` -- there is no `--cwd` flag

### Caveats and Edge Cases

- **`.claude/settings.local.json`**: This file is gitignored and will NOT be present in worktrees. If sessions need local settings (like permission overrides), they must be explicitly copied.
- **`cp -r` gotcha**: Copying `.claude` over an existing `.claude` directory creates nested `.claude/.claude/`. Use `rsync -a` or `cp -r .claude/* target/.claude/` instead.
- **Worktree cleanup**: Always use `git worktree remove --force` for cleanup, as worktrees with untracked files (like `settings.local.json`) will block regular removal.
- **`.git` file**: In worktrees, `.git` is a file (not a directory) containing a `gitdir:` pointer. The SDK handles this correctly.
- **Branch isolation**: Each worktree requires its own branch. The session-isolation feature should use branch naming like `session/{slug}` to avoid conflicts.
