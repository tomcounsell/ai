---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-03
tracking:
---

# Fix Stale Worktrees Blocking Branch Checkout

## Problem

When running `/do-build`, the worktree creation step (`git worktree add .worktrees/{slug} -b session/{slug} main`) fails if a previous session left a stale worktree referencing the same branch. This happens because:

1. A prior `/do-plan` or `/do-build` invocation created a worktree but didn't clean it up (session crashed, agent timed out, etc.)
2. The worktree directory may or may not still exist on disk, but git's internal worktree tracking still references the branch
3. Git refuses to create a new worktree for a branch already associated with an existing worktree

**Current behavior:**
```
fatal: 'session/fix-chat-cross-wire' is already used by worktree at '.worktrees/fix-chat-cross-wire'
```
The error is opaque and requires manual `git worktree remove --force` to recover.

**Desired outcome:**
The `create_worktree` function in `agent/worktree_manager.py` handles stale worktrees automatically: detect, clean up, and retry -- so the SDLC pipeline never blocks on stale worktree state.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a focused fix to a single module with clear behavior and existing test infrastructure.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Stale worktree detection**: Before creating a worktree, check if git already tracks a worktree for the target branch (even if the directory is missing)
- **Automatic cleanup**: If a stale worktree is detected, prune it and/or force-remove it before retrying creation
- **Resilient create_worktree**: The function should handle all stale states transparently, making it idempotent

### Flow

**Build starts** -> `create_worktree(slug)` -> Detects stale worktree for `session/{slug}` -> Prunes/removes stale reference -> Creates fresh worktree -> **Build proceeds**

### Technical Approach

Modify `create_worktree()` in `agent/worktree_manager.py` to:

1. **Before creating**: Run `git worktree list --porcelain` and check if the target branch (`session/{slug}`) is already associated with any worktree
2. **If stale worktree found** (directory missing or branch locked):
   - Run `git worktree prune` to clean up references to missing directories
   - If the worktree directory exists but is stale, run `git worktree remove --force` on it
   - Retry creation after cleanup
3. **If worktree directory exists and is valid**: Return the existing path (current behavior, already works)

Also update the `/do-build` SKILL.md to use `worktree_manager.create_worktree()` instead of raw `git worktree add`, ensuring the resilient logic is always invoked.

## Rabbit Holes

- Implementing worktree health checks or periodic background cleanup -- separate concern, not needed for this fix
- Refactoring the entire worktree lifecycle (session start to finish) -- too broad, the targeted fix to `create_worktree` is sufficient
- Adding worktree state to pipeline_state tracking -- unnecessary complexity for this bug

## Risks

### Risk 1: Force-removing a worktree with uncommitted work
**Impact:** Data loss if an active session's worktree is force-removed
**Mitigation:** Only force-remove if the worktree directory is genuinely stale (no git lock files, no active process). Add a check for `.git` lock files before force-removing. Log a warning when force-removing.

### Risk 2: Race condition between concurrent sessions
**Impact:** Two sessions targeting the same slug could conflict during cleanup
**Mitigation:** Acceptable risk -- the SDLC pipeline is single-threaded per slug. Document that concurrent sessions for the same slug are not supported.

## No-Gos (Out of Scope)

- Background worktree garbage collection daemon
- Worktree state persistence in Redis or database
- Changes to `post_merge_cleanup.py` (already works correctly)
- Multi-machine worktree synchronization

## Update System

No update system changes required -- this is an internal fix to the worktree manager module. No new dependencies or config files.

## Agent Integration

No agent integration required -- this is a fix to the build pipeline's internal worktree management. The agent does not directly invoke worktree operations; they are called by the `/do-build` skill.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md` with stale worktree recovery behavior
- [ ] Add entry to `docs/features/README.md` index table if not already present

### Inline Documentation
- [ ] Code comments on the stale worktree detection logic
- [ ] Updated docstrings for `create_worktree` reflecting recovery behavior

## Success Criteria

- [ ] `create_worktree()` succeeds even when a stale worktree exists for the target branch
- [ ] `create_worktree()` succeeds when worktree directory is gone but git still tracks it
- [ ] `create_worktree()` preserves existing valid worktrees (no regression)
- [ ] Stale worktree cleanup is logged with clear messages
- [ ] `/do-build` SKILL.md updated to use `worktree_manager.create_worktree()` instead of raw git commands
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (worktree-fix)**
  - Name: worktree-builder
  - Role: Implement stale worktree detection and recovery in worktree_manager.py
  - Agent Type: builder
  - Resume: true

- **Validator (worktree-fix)**
  - Name: worktree-validator
  - Role: Verify stale worktree scenarios are handled correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add stale worktree detection and recovery to create_worktree
- **Task ID**: build-worktree-recovery
- **Depends On**: none
- **Assigned To**: worktree-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a helper function `_find_worktree_for_branch(repo_root, branch_name)` that parses `git worktree list --porcelain` output to find if a branch is already associated with a worktree
- Modify `create_worktree()` to call this helper before creation
- If a stale worktree is found: prune, force-remove if directory exists, then retry creation
- Add logging for all recovery actions
- Update docstring to document recovery behavior

### 2. Update do-build SKILL.md to use worktree_manager
- **Task ID**: build-skill-update
- **Depends On**: build-worktree-recovery
- **Assigned To**: worktree-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace raw `git worktree add` command in SKILL.md step 5 with a Python invocation of `create_worktree()`
- Ensure the settings.local.json copy is still handled (it's already in `create_worktree`)

### 3. Add unit tests for stale worktree scenarios
- **Task ID**: build-tests
- **Depends On**: build-worktree-recovery
- **Assigned To**: worktree-builder
- **Agent Type**: builder
- **Parallel**: false
- Add tests to `tests/unit/test_worktree_manager.py` covering:
  - Stale worktree with missing directory is auto-pruned and recreation succeeds
  - Stale worktree with existing directory is force-removed and recreation succeeds
  - Valid existing worktree is returned as-is (no regression)
  - Branch locked by worktree at different path is handled

### 4. Validate all scenarios
- **Task ID**: validate-worktree-fix
- **Depends On**: build-tests
- **Assigned To**: worktree-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worktree_manager.py -v`
- Verify all new and existing tests pass
- Check that `create_worktree` docstring reflects new behavior

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-worktree-fix
- **Assigned To**: worktree-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with stale worktree recovery section
- Verify docs build if applicable

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: worktree-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/test_worktree_manager.py -v`
- Run linting: `black --check . && ruff check .`
- Verify all success criteria met

## Validation Commands

- `pytest tests/unit/test_worktree_manager.py -v` - All worktree tests pass
- `black --check agent/worktree_manager.py` - Code formatting
- `ruff check agent/worktree_manager.py` - Linting
- `python -c "from agent.worktree_manager import create_worktree; print('import ok')"` - Module imports cleanly

---

## Open Questions

1. **Should we also update `/do-build` to invoke `create_worktree()` via Python instead of raw git commands?** The SKILL.md currently uses `git worktree add` directly, bypassing the worktree manager's recovery logic. Updating this seems essential for the fix to actually take effect, but it changes the build skill's documented workflow.

2. **Should force-removal check for uncommitted work?** If a stale worktree directory has uncommitted changes, should we attempt to commit them as a safety net before force-removing, or just log a warning and proceed? The uncommitted work is likely from a crashed session and may be incomplete/broken.
