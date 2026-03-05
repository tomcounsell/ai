---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/tomcounsell/ai/issues/267
---

# Fix Worktree Resume on Interrupted Sessions

## Problem

When a session is interrupted and resumed, the agent may try to `git checkout session/{slug}` instead of working in the existing `.worktrees/{slug}/` directory. Git rejects the checkout because the branch is already locked by a worktree:

```
fatal: 'session/fix-hook-infinite-loop' is already used by worktree at '/Users/valorengels/src/ai/.worktrees/fix-hook-infinite-loop'
```

And `git checkout -b session/{slug}` also fails:
```
fatal: a branch named 'session/fix-hook-infinite-loop' already exists
```

**Current behavior:**
The `create_worktree()` function in `agent/worktree_manager.py` already handles the case where the worktree directory exists (returns early on line 211-213). However, the `/do-build` skill and builder agent prompts do not expose a simple `get_or_create_worktree()` convenience function. More importantly, the issue describes agents attempting raw `git checkout` commands instead of using the worktree manager at all. The builder agent prompt tells agents to `cd` into the worktree, but if the agent tries to checkout the branch first (as a "navigate to branch" reflex), it fails.

**Desired outcome:**
1. A `get_or_create_worktree()` convenience function that agents and scripts can call to always get the right worktree path, whether it already exists or needs to be created.
2. The `/do-build` skill's builder agent prompt explicitly warns against using `git checkout` and reinforces that the worktree path IS the correct way to work on the branch.

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

- **`get_or_create_worktree()` function**: A thin wrapper around `create_worktree()` that makes the idempotent "give me a worktree path" pattern explicit and self-documenting.
- **Builder agent prompt hardening**: Add explicit warnings to the `/do-build` agent deployment prompts against using `git checkout` on session branches.

### Flow

**Resumed session** -> `/do-build` calls `get_or_create_worktree(slug)` -> returns existing worktree path -> agents work in `.worktrees/{slug}/` -> no checkout errors

### Technical Approach

- Add `get_or_create_worktree(repo_root, slug, base_branch)` to `agent/worktree_manager.py` that delegates to `create_worktree()` -- since `create_worktree()` already returns early when the worktree exists, this is a thin alias with a more intention-revealing name.
- Update the `/do-build` SKILL.md to use `get_or_create_worktree` instead of `create_worktree` in its worktree creation step (step 6).
- Add a `NEVER use git checkout on session/ branches` warning to the agent deployment prompt template in WORKFLOW.md and SKILL.md.
- Add unit tests for `get_or_create_worktree()` covering both the "create new" and "resume existing" paths.

## Rabbit Holes

- Refactoring the entire `create_worktree()` function -- it already handles the edge cases well; we just need a convenience alias.
- Adding automatic worktree detection to the bridge or SDK client -- that is a larger architectural change.
- Building a worktree registry or state machine -- overkill for this fix.

## Risks

### Risk 1: Agents still use `git checkout` despite prompt warnings
**Impact:** Same error as before on interrupted sessions.
**Mitigation:** The prompt warning is defense-in-depth. The real fix is the `/do-build` orchestrator calling `get_or_create_worktree()` which always returns a valid path. Individual agents never need to checkout branches.

## No-Gos (Out of Scope)

- Automatic detection of "agent tried git checkout and failed" with recovery logic
- Changes to the bridge or SDK client worktree handling
- Worktree registry or persistent state tracking beyond what `pipeline_state.py` already does

## Update System

No update system changes required -- this is a code-only change to `agent/worktree_manager.py` and skill documentation files that deploy automatically.

## Agent Integration

No agent integration required -- this is an internal change to the worktree manager and build skill prompts. The agent already uses the worktree manager indirectly through the `/do-build` skill. The new `get_or_create_worktree()` function is called by the orchestrator, not exposed as an MCP tool.

## Documentation

- [ ] Update `docs/features/session-isolation.md` to mention `get_or_create_worktree()` in the worktree operations list
- [ ] Inline docstrings on `get_or_create_worktree()` function

## Success Criteria

- [ ] `get_or_create_worktree()` function exists in `agent/worktree_manager.py`
- [ ] Calling `get_or_create_worktree()` when worktree already exists returns the existing path without error
- [ ] Calling `get_or_create_worktree()` when worktree does not exist creates it and returns the path
- [ ] `/do-build` SKILL.md uses `get_or_create_worktree` in its worktree creation step
- [ ] Builder agent prompt includes warning against `git checkout` on session branches
- [ ] Unit tests pass for both "create new" and "resume existing" scenarios
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (worktree-manager)**
  - Name: worktree-builder
  - Role: Add `get_or_create_worktree()` function and update tests
  - Agent Type: builder
  - Resume: true

- **Builder (skill-docs)**
  - Name: skill-docs-builder
  - Role: Update `/do-build` skill prompts with worktree resume awareness and checkout warnings
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Verify all changes work together
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 -- Core (default choices):**
- `builder` - General implementation
- `validator` - Read-only verification

## Step by Step Tasks

### 1. Add `get_or_create_worktree()` function and tests
- **Task ID**: build-worktree-manager
- **Depends On**: none
- **Assigned To**: worktree-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `get_or_create_worktree(repo_root, slug, base_branch="main")` to `agent/worktree_manager.py`
- Function delegates to `create_worktree()` -- thin wrapper with intention-revealing name
- Add unit tests in `tests/unit/test_worktree_manager.py` for both create-new and resume-existing paths
- Export `get_or_create_worktree` from the module

### 2. Update `/do-build` skill prompts
- **Task ID**: build-skill-docs
- **Depends On**: none
- **Assigned To**: skill-docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/do-build/SKILL.md` step 6 to use `get_or_create_worktree` instead of `create_worktree`
- Add `NEVER use git checkout on session/ branches` warning to agent deployment prompt in SKILL.md and WORKFLOW.md
- Ensure the warning explains that the worktree IS the checkout -- agents should `cd` into it

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-worktree-manager, build-skill-docs
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worktree_manager.py -v`
- Verify `get_or_create_worktree` is importable from `agent.worktree_manager`
- Verify `/do-build` SKILL.md references `get_or_create_worktree`
- Verify agent prompt includes checkout warning
- Run `ruff check agent/worktree_manager.py`

## Validation Commands

- `pytest tests/unit/test_worktree_manager.py -v` -- unit tests pass
- `python -c "from agent.worktree_manager import get_or_create_worktree; print('OK')"` -- function is importable
- `grep -q "get_or_create_worktree" .claude/skills/do-build/SKILL.md` -- skill references new function
- `grep -q "NEVER.*git checkout" .claude/skills/do-build/WORKFLOW.md` -- checkout warning exists
- `ruff check agent/worktree_manager.py` -- no lint errors

---

## Open Questions

None -- the scope is clear and narrow. The `create_worktree()` function already handles the resume case; this is about making that behavior explicit and hardening agent prompts.
