# Git State Guard

Detects and resolves dirty git state (in-progress merges, rebases, cherry-picks, uncommitted changes) on the main working tree before SDLC skills perform branch-switching operations. Prevents unrelated work from being blocked by lingering git state from concurrent PR operations.

## Problem

When working on multiple PRs concurrently, an unresolved merge conflict on one branch can block git operations for unrelated work. For example:

1. PR #311 has a merge conflict being resolved on its branch
2. PR #308 needs review, requiring `git checkout` or worktree creation
3. `git checkout main && git pull` fails with "you need to resolve your current index first"
4. The agent gets stuck and cannot proceed with any work

## Solution

A guard function `ensure_clean_git_state()` in `agent/worktree_manager.py` that runs at SDLC skill entry points before any branch-switching operation.

### Detection

The function checks for four types of dirty state by inspecting the `.git/` directory:

| State | Indicator | Resolution |
|-------|-----------|------------|
| In-progress merge | `.git/MERGE_HEAD` exists | `git merge --abort` |
| In-progress rebase | `.git/rebase-merge/` or `.git/rebase-apply/` exists | `git rebase --abort` |
| In-progress cherry-pick | `.git/CHERRY_PICK_HEAD` exists | `git cherry-pick --abort` |
| Uncommitted changes | `git status --porcelain` returns output | `git stash push -m "sdlc-auto-stash"` |

### Safety

- Only operates on the main working tree (`.git` is a directory)
- Refuses to run on worktree directories (`.git` is a file pointing to the main repo)
- This prevents accidentally aborting a legitimate merge in progress on a session branch worktree

### Return Value

Returns a structured dict describing all cleanup actions taken:

```python
{
    "skipped": False,        # True if called on a worktree (no action taken)
    "merge_aborted": False,  # True if a merge was aborted
    "rebase_aborted": False, # True if a rebase was aborted
    "cherry_pick_aborted": False,  # True if a cherry-pick was aborted
    "changes_stashed": False,      # True if changes were stashed
    "stash_name": None,      # Stash message if stashed
    "errors": [],            # Error messages for failed operations
    "was_clean": True,       # True if no dirty state detected
}
```

### Error Handling

- If an abort command fails (e.g., `git merge --abort` returns non-zero), the error is logged and included in the `errors` list
- If any operation fails and the state could not be fully cleaned, raises `ValueError` with a message describing what manual steps are needed
- `subprocess.CalledProcessError` from git commands is caught and logged, not propagated

## Integration Points

The guard is called at three SDLC skill entry points:

1. **`/do-build`** (Step 6): Before creating a worktree for the build
2. **`/do-pr-review`** (Step 1): Before `gh pr checkout` for code review (checkout moved from Step 3 to Step 1 so all file reads see the PR branch)
3. **`/sdlc`** (Step 2): Before assessing current state and branch operations

### Usage

```python
from agent.worktree_manager import ensure_clean_git_state
from pathlib import Path

result = ensure_clean_git_state(Path("/path/to/repo"))
if not result["was_clean"]:
    print(f"Cleaned up: {result}")
```

Or from bash:

```bash
python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; print(ensure_clean_git_state(Path('.')))"
```

## Files

| File | Purpose |
|------|---------|
| `agent/worktree_manager.py` | `ensure_clean_git_state()`, `_resolve_git_dir()`, `_is_worktree()` |
| `tests/unit/test_git_state_guard.py` | 21 unit tests covering all detection/resolution paths |
| `.claude/skills/do-build/SKILL.md` | Guard call at Step 6 |
| `.claude/skills/do-pr-review/SKILL.md` | Guard call in Step 1 before `gh pr checkout` |
| `.claude/skills/sdlc/SKILL.md` | Guard call at Step 2 |

## Tracking

- Issue: [#313](https://github.com/valorengels/ai/issues/313)
