---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-08
tracking: https://github.com/valorengels/ai/issues/313
---

# SDLC Git State Guard

## Problem

When working on multiple PRs concurrently, an unresolved merge conflict on one branch blocks git operations for unrelated work on another branch. The SDLC skills do not check for dirty git state before performing branch-switching operations, causing cascading failures.

**Current behavior:**
1. A merge conflict is in progress on one branch (e.g., PR #311 resolving `docs/features/README.md` conflict)
2. Another PR needs work (e.g., PR #308 review)
3. `git checkout main && git pull` fails with "you need to resolve your current index first"
4. The agent gets stuck between branches with dirty git state and cannot proceed with any work

**Desired outcome:**
All SDLC skills that perform branch-switching or git operations gracefully detect and resolve dirty git state before proceeding, preventing unrelated work from being blocked by in-progress merge/rebase operations.

## Prior Art

No prior issues found related to git state guarding in SDLC skills. The worktree system (issue #237, `agent/worktree_manager.py`) handles stale worktree references but does not address dirty working tree state (in-progress merges, rebases, or uncommitted changes on the main working tree).

## Data Flow

1. **Entry point**: User triggers an SDLC skill (e.g., `/sdlc issue 308`) while another branch has an in-progress merge
2. **SDLC dispatcher** (`SKILL.md`): Runs `git branch -a`, `gh pr list` -- these read-only git commands succeed even with dirty state
3. **Sub-skill invocation**: Dispatches to `/do-build`, `/do-pr-review`, or `/do-patch`
4. **Branch operation**: The sub-skill attempts `gh pr checkout`, `git checkout main`, or `git pull` -- this FAILS because the working tree has unresolved conflicts
5. **Failure cascade**: The agent reports an error and cannot proceed; unrelated work is blocked

The fix intercepts at step 2-3: before any sub-skill performs a branch-switching operation, a git state guard checks for and resolves dirty state.

## Architectural Impact

- **New dependencies**: None -- uses only git CLI commands already available
- **Interface changes**: New utility function `ensure_clean_git_state()` in `agent/worktree_manager.py` (or a new `agent/git_state.py` module)
- **Coupling**: Minimal -- the guard function is called at skill entry points, not deeply coupled
- **Data ownership**: No change -- git state is managed by git itself
- **Reversibility**: Fully reversible -- removing the guard function and its call sites returns to current behavior

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

- **Git state detection utility**: A function that checks for in-progress merges, rebases, cherry-picks, and uncommitted changes
- **Automatic resolution**: Aborts in-progress merge/rebase operations when they're on the main working tree and we're about to use a worktree anyway
- **SDLC skill guards**: Entry-point checks in the skills that perform branch-switching operations

### Flow

**SDLC skill invoked** -> Check git state -> [Clean?] -> Proceed normally
**SDLC skill invoked** -> Check git state -> [In-progress merge/rebase?] -> Abort merge/rebase -> Proceed
**SDLC skill invoked** -> Check git state -> [Uncommitted changes?] -> Stash changes -> Proceed

### Technical Approach

- Add `ensure_clean_git_state(repo_root)` function to `agent/worktree_manager.py` that:
  1. Detects in-progress merge (`git merge HEAD` returns non-zero while `.git/MERGE_HEAD` exists)
  2. Detects in-progress rebase (`.git/rebase-merge/` or `.git/rebase-apply/` exists)
  3. Detects in-progress cherry-pick (`.git/CHERRY_PICK_HEAD` exists)
  4. For each detected state: aborts the operation (`git merge --abort`, `git rebase --abort`, `git cherry-pick --abort`)
  5. If uncommitted changes remain after abort: stashes them (`git stash push -m "sdlc-auto-stash"`)
  6. Returns a dict describing what was cleaned up (for logging)
- Update `/do-build` SKILL.md to call the guard before worktree creation
- Update `/do-pr-review` SKILL.md to call the guard before `gh pr checkout`
- The `/do-pr-review` skill should use a temporary worktree instead of `gh pr checkout` to avoid polluting the main working tree entirely (stretch goal -- documented as future improvement if not done in this appetite)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `ensure_clean_git_state()` function catches `subprocess.CalledProcessError` from `git merge --abort` etc. and logs warnings rather than crashing
- [ ] If abort fails (e.g., no merge in progress despite MERGE_HEAD existing), the function logs the error and returns a partial result

### Empty/Invalid Input Handling
- [ ] Function handles repos without `.git` directory gracefully (raises `ValueError`)
- [ ] Function handles repos where `.git` is a file (worktree pointer) by resolving the actual git dir

### Error State Rendering
- [ ] The guard function returns a structured dict with what was cleaned, enabling callers to log it clearly
- [ ] If the guard cannot fully clean the state, it raises an exception with a clear message about what manual steps are needed

## Rabbit Holes

- Building a full git state machine that tracks all possible git states -- we only need to detect and abort the common dirty states
- Auto-resolving merge conflicts -- we should abort, not try to resolve. Conflict resolution is a separate concern
- Adding undo/redo for the abort operations -- if someone was actively resolving a conflict, the abort is lossy. This is acceptable because SDLC skills should use worktrees, not branch-switch

## Risks

### Risk 1: Data loss from aborting an in-progress merge resolution
**Impact:** If someone was mid-conflict-resolution, aborting loses their work
**Mitigation:** Log a clear warning message before aborting. The issue description itself notes this is the expected behavior -- the worktree system should prevent this scenario in the first place. The stash provides a recovery path for uncommitted changes.

### Risk 2: Guard runs on a worktree instead of the main working tree
**Impact:** Could abort a merge that's legitimately in progress on a session branch
**Mitigation:** The guard should only run on the repo root, not on worktree directories. Check if `repo_root/.git` is a file (worktree) vs directory (main repo) to distinguish.

## Race Conditions

No race conditions identified. The git state guard runs synchronously at skill entry points, and git's file-based locking prevents concurrent modifications to the same repository. The worktree system already provides filesystem isolation for concurrent work.

## No-Gos (Out of Scope)

- Auto-resolving merge conflicts (only abort)
- Refactoring `/do-pr-review` to use worktrees instead of `gh pr checkout` (future improvement)
- Adding git state monitoring as a continuous background check
- Handling dirty state in worktree directories (only the main working tree)

## Update System

No update system changes required -- this feature adds a utility function and updates skill documentation. No new dependencies or config files.

## Agent Integration

No agent integration required -- this is a skill-internal change. The guard function is called within SDLC skills, not exposed as an MCP tool. The bridge does not need to call this code directly.

## Documentation

- [ ] Create `docs/features/git-state-guard.md` describing the guard mechanism and when it fires
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `ensure_clean_git_state()` detects in-progress merge, rebase, and cherry-pick states
- [ ] `ensure_clean_git_state()` aborts detected operations and returns a description of what was cleaned
- [ ] `ensure_clean_git_state()` stashes uncommitted changes after aborting
- [ ] Unit tests cover: clean state (no-op), merge in progress, rebase in progress, uncommitted changes, combined states
- [ ] `/do-build` SKILL.md includes guard call before worktree creation
- [ ] `/do-pr-review` SKILL.md includes guard call before `gh pr checkout`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (git-state-guard)**
  - Name: guard-builder
  - Role: Implement `ensure_clean_git_state()` and update skill docs
  - Agent Type: builder
  - Resume: true

- **Validator (git-state-guard)**
  - Name: guard-validator
  - Role: Verify implementation meets success criteria
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using core Tier 1 agents only (builder + validator).

## Step by Step Tasks

### 1. Implement git state guard function
- **Task ID**: build-guard
- **Depends On**: none
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `ensure_clean_git_state(repo_root: Path)` function to `agent/worktree_manager.py`
- Detect: MERGE_HEAD, rebase-merge/, rebase-apply/, CHERRY_PICK_HEAD
- Abort detected operations with appropriate git commands
- Stash uncommitted changes after abort
- Return structured dict with cleanup actions taken
- Add unit tests in `tests/unit/test_git_state_guard.py`

### 2. Update SDLC skill documentation
- **Task ID**: build-skill-docs
- **Depends On**: build-guard
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/do-build/SKILL.md` to include guard call before worktree creation (Step 6)
- Update `.claude/skills/do-pr-review/SKILL.md` to include guard call before `gh pr checkout` (Step 3)
- Update `.claude/skills/sdlc/SKILL.md` Step 2 to include guard call

### 3. Validate implementation
- **Task ID**: validate-guard
- **Depends On**: build-skill-docs
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `ensure_clean_git_state()` exists and handles all documented states
- Verify unit tests exist and cover clean, merge, rebase, cherry-pick, and uncommitted states
- Verify skill docs reference the guard function
- Run `python -m pytest tests/unit/test_git_state_guard.py -v`
- Run `python -m ruff check agent/worktree_manager.py`

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: guard-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/git-state-guard.md`
- Add entry to `docs/features/README.md` index table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Validation Commands

- `python -m pytest tests/unit/test_git_state_guard.py -v` - Validates unit tests pass
- `python -m ruff check agent/worktree_manager.py` - Validates code quality
- `python -m ruff format --check agent/worktree_manager.py` - Validates formatting
- `grep -c "ensure_clean_git_state" .claude/skills/do-build/SKILL.md` - Confirms guard in build skill
- `grep -c "ensure_clean_git_state" .claude/skills/do-pr-review/SKILL.md` - Confirms guard in review skill

---

## Open Questions

1. **Should the guard also handle the case where the main working tree is on a session branch instead of main?** The `branch_manager.py` already has `return_to_main()`, but it does not handle dirty state before switching. Should we integrate with that, or keep the guard focused on merge/rebase/cherry-pick only?

2. **Should `/do-pr-review` be updated to use a temporary worktree (like `/do-build` does) instead of `gh pr checkout`?** This would eliminate the root cause entirely for reviews -- they would never touch the main working tree. However, this is a larger refactor and may exceed the Small appetite. Should it be a separate follow-up issue?
