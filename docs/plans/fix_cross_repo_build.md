---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/249
---

# Fix /do-build Cross-Repo Dispatch

## Problem

When `/do-build` is invoked from the ai repo for a plan document located in a different repo (e.g., psyoptimal), the build runs against the ai repo's branches and worktrees instead of the target repo's.

**Current behavior:**
1. `/sdlc` targets psyoptimal issue #289
2. Plan is correctly created at `/Users/valorengels/src/psyoptimal/docs/plans/foo.md`
3. `/do-build /Users/valorengels/src/psyoptimal/docs/plans/foo.md` is invoked
4. Build creates worktree in the ai repo (`.worktrees/foo/`) and runs against ai repo branches

**Desired outcome:**
When `/do-build` receives a plan path in a different repo, it should detect the target repo from the plan file's location and execute all git/worktree/pipeline operations within that repo context.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work is fast -- the fix is well-scoped and the problem is clearly understood.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Repo root resolver**: Given a plan path, determine which git repo it belongs to by running `git rev-parse --show-toplevel` from the plan file's directory
- **do-build SKILL.md update**: Modify the Plan Resolution section to resolve the target repo root from the plan path, then pass that repo root to all subsequent operations
- **worktree_manager integration**: The worktree_manager already accepts `repo_root` as a parameter -- the skill just needs to pass the correct one
- **pipeline_state repo awareness**: `pipeline_state.py` hardcodes `_REPO_ROOT = Path(__file__).parent.parent` (the ai repo). It needs to accept an explicit repo root for cross-repo builds

### Flow

**Plan path provided** -> Resolve absolute path -> `git rev-parse --show-toplevel` from plan dir -> **target repo root** -> Create worktree in target repo -> All git/PR operations use target repo -> **PR in correct repo**

### Technical Approach

1. **Add `resolve_repo_root()` utility** to `agent/worktree_manager.py` (or a new small module). Given a file path, it runs `git -C <dir> rev-parse --show-toplevel` to find the repo root.

2. **Update `pipeline_state.py`** to accept an optional `repo_root` parameter instead of always using the hardcoded `_REPO_ROOT`. Functions that compute paths (`_state_path`) should accept or derive repo root contextually. The simplest approach: always store pipeline state in the **ai repo** (since it's the orchestrator) but include the target repo path in the state dict for reference.

3. **Update do-build SKILL.md** to:
   - After resolving `PLAN_PATH`, resolve `TARGET_REPO` via `git -C $(dirname PLAN_PATH) rev-parse --show-toplevel`
   - Pass `TARGET_REPO` to `create_worktree()` instead of `Path('.')`
   - Use `git -C <target_repo>/.worktrees/{slug}` for all git commands
   - Use `--repo` flag or `GH_REPO` env var with `gh pr create` to target the correct GitHub repo

4. **Update WORKFLOW.md** to use `TARGET_REPO` in agent deployment prompts so builder agents work in the correct worktree path.

## Rabbit Holes

- **Generic cross-repo orchestration framework**: We only need to handle the case where the plan path determines the target. Don't build a full multi-repo orchestration system.
- **Pipeline state migration**: Don't move pipeline state storage to per-repo locations. Keep it centralized in the ai repo -- just add a `target_repo` field.
- **Changing `config/projects.json` integration**: The projects config could theoretically be used to resolve repos, but `git rev-parse` from the plan path is simpler, more reliable, and doesn't require config maintenance.

## Risks

### Risk 1: `gh pr create` targeting wrong repo
**Impact:** PR gets created in the ai repo instead of the target repo
**Mitigation:** Use `gh pr create --repo owner/repo` with the org/repo derived from `gh repo view --json nameWithOwner` run from the target repo root

### Risk 2: Agent deployment paths break
**Impact:** Builder agents work in wrong directory
**Mitigation:** The WORKFLOW.md agent deployment template already uses `{absolute_path_to}/.worktrees/{slug}/` -- we just need to ensure `{absolute_path_to}` is the target repo, not the ai repo

## No-Gos (Out of Scope)

- Cross-repo test execution (tests run in whatever repo the worktree is in -- no special handling needed)
- Multi-repo builds (one plan always maps to one repo)
- Changing how `/do-plan` creates plans in external repos (that already works correctly)

## Update System

No update system changes required -- this is a fix to skill prompt files and a small Python utility addition. No new dependencies or config propagation needed.

## Agent Integration

No agent integration required -- this is a fix to the SDLC skill files (`do-build/SKILL.md`, `do-build/WORKFLOW.md`) and a small utility addition to `agent/worktree_manager.py`. The bridge and MCP servers are not affected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md` to document cross-repo worktree behavior
- [ ] Add entry to `docs/features/README.md` index table if cross-repo support is a new entry

### Inline Documentation
- [ ] Code comments on `resolve_repo_root()` explaining when and why it's used
- [ ] Updated docstrings for modified `pipeline_state.py` functions

## Success Criteria

- [ ] `resolve_repo_root("/Users/valorengels/src/psyoptimal/docs/plans/foo.md")` returns `/Users/valorengels/src/psyoptimal`
- [ ] `pipeline_state.initialize()` stores `target_repo` in state when provided
- [ ] do-build SKILL.md resolves `TARGET_REPO` from plan path before creating worktree
- [ ] do-build WORKFLOW.md agent deployment uses correct absolute path for cross-repo worktrees
- [ ] `gh pr create` in SKILL.md uses `--repo` flag derived from target repo
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cross-repo-fix)**
  - Name: cross-repo-builder
  - Role: Implement resolve_repo_root utility, update pipeline_state, update SKILL.md and WORKFLOW.md
  - Agent Type: builder
  - Resume: true

- **Validator (cross-repo-fix)**
  - Name: cross-repo-validator
  - Role: Verify resolve_repo_root works, verify SKILL.md references TARGET_REPO correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add resolve_repo_root utility
- **Task ID**: build-resolver
- **Depends On**: none
- **Assigned To**: cross-repo-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `resolve_repo_root(file_path: str | Path) -> Path` to `agent/worktree_manager.py`
- Function runs `git -C <parent_dir> rev-parse --show-toplevel` and returns the Path
- Add unit test for this function in `tests/test_worktree_manager.py`

### 2. Update pipeline_state for cross-repo
- **Task ID**: build-pipeline-state
- **Depends On**: build-resolver
- **Assigned To**: cross-repo-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `initialize()` to accept optional `target_repo` parameter and store it in state dict
- Keep state storage in the ai repo (no path changes to `_STATE_ROOT`)
- Add test for initialize with target_repo parameter

### 3. Update do-build SKILL.md
- **Task ID**: build-skill-update
- **Depends On**: build-pipeline-state
- **Assigned To**: cross-repo-builder
- **Agent Type**: builder
- **Parallel**: false
- After Plan Resolution section, add TARGET_REPO resolution step using `resolve_repo_root(PLAN_PATH)`
- Update worktree creation to use `TARGET_REPO` instead of `Path('.')`
- Update `gh pr create` to use `--repo` flag from target repo
- Update all `git -C .worktrees/{slug}` references to use `TARGET_REPO/.worktrees/{slug}`

### 4. Update do-build WORKFLOW.md
- **Task ID**: build-workflow-update
- **Depends On**: build-skill-update
- **Assigned To**: cross-repo-builder
- **Agent Type**: builder
- **Parallel**: false
- Update agent deployment template to use `TARGET_REPO` for absolute path references
- Update git commands to reference target repo worktree paths

### 5. Validate cross-repo changes
- **Task ID**: validate-all
- **Depends On**: build-workflow-update
- **Assigned To**: cross-repo-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `resolve_repo_root` function exists and has tests
- Verify SKILL.md contains TARGET_REPO resolution logic
- Verify WORKFLOW.md agent deployment uses TARGET_REPO
- Run all validation commands

## Validation Commands

- `python -c "from agent.worktree_manager import resolve_repo_root; print(resolve_repo_root('.'))"` - verify utility exists and works
- `grep -q 'TARGET_REPO' .claude/skills/do-build/SKILL.md` - verify SKILL.md has cross-repo logic
- `grep -q 'TARGET_REPO' .claude/skills/do-build/WORKFLOW.md` - verify WORKFLOW.md has cross-repo logic
- `pytest tests/test_worktree_manager.py -v` - verify tests pass

---

## Open Questions

1. Should `resolve_repo_root()` fall back to the ai repo when a plan path is not inside any git repo, or should it error? (Recommended: error, since a plan outside a git repo is always a mistake)
2. Should the SDLC dispatcher (`sdlc/SKILL.md`) also be updated to pass repo context when invoking `/do-build`, or is resolving from the plan path sufficient? (Recommended: plan path is sufficient -- that's the source of truth)
