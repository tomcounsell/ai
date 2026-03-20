---
status: Complete
type: chore
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/447
last_comment_id:
---

# Move projects.json to ~/Desktop/Valor/ (private, iCloud-synced)

## Problem

`config/projects.json` contained private data (Telegram chat IDs, machine names, project descriptions) and was historically checked into a public repo. The file has already been moved to `~/Desktop/Valor/projects.json` and the code updated to read from there, but many stale references remain across documentation and code comments pointing to the old `config/projects.json` location.

**Current behavior:**
The actual file lives at `~/Desktop/Valor/projects.json` and the runtime code reads from there correctly. However, ~15 documentation files and 3 code files still reference `config/projects.json`, creating confusion for anyone reading the docs or onboarding a new machine.

**Desired outcome:**
All references updated to `~/Desktop/Valor/projects.json` (or the `PROJECTS_CONFIG_PATH` env var pattern). No stale `config/projects.json` references remain outside of legacy fallback code paths that explicitly document the fallback.

## Prior Art

- **PR #448 (fb6b9129)**: "Make persona name configurable via layered soul files" -- deleted `config/projects.json` from the repo
- **Commit fbf254d2**: "Move projects.json and persona overlays to ~/Desktop/Valor/" -- the core move
- **Commit 87e336d4**: "Fix stale references in docs for persona system and projects.json move" -- partial cleanup, but many references still remain
- **Issue #416**: Config consolidation (merged) -- established the `PROJECTS_CONFIG_PATH` env var pattern

The core move is complete. This issue tracks the remaining cleanup.

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

- **Doc reference cleanup**: Update all `config/projects.json` references in docs to `~/Desktop/Valor/projects.json`
- **Code comment cleanup**: Fix stale comments in Python files that still reference the old path
- **Worktree cleanup**: Remove stale worktree `.claude/worktrees/agent-a1f22e42/` if it references old paths

### Technical Approach

Batch find-and-replace across documentation and code comments. The runtime code in `bridge/routing.py` already has the correct resolution order with fallback -- only comments/docstrings need updating.

**Files requiring changes (excluding worktrees):**

Python code (comments/fallback references):
1. `scripts/migrate_model_relationships.py` -- comment on line 8 says "config/projects.json", update
2. `scripts/reflections.py` -- comment on line 166 references old path
3. `bridge/routing.py` -- comments on lines 50 and 71 mention "config/projects.json" as fallback (acceptable as legacy fallback docs)
4. `tests/conftest.py` -- comment on line 249 references old path

Documentation:
5. `docs/features/telegram.md`
6. `docs/features/reflections.md`
7. `docs/features/issue-poller.md`
8. `docs/features/redis-models.md`
9. `docs/features/bridge-module-architecture.md`
10. `docs/features/bridge-response-improvements.md`
11. `docs/features/sdlc-first-routing.md`
12. `docs/features/workspace-safety-invariants.md`
13. `docs/features/documentation-audit.md`
14. `docs/references/valor-name-references.md`
15. `docs/guides/cursor-lessons.md`
16. `.claude/agents/baseline-verifier.md`

### Flow

**Grep for stale refs** → **Update each file** → **Verify no stale refs remain** → **Commit**

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope -- this is a documentation/comment-only change.

### Empty/Invalid Input Handling
Not applicable -- no runtime behavior changes.

### Error State Rendering
Not applicable -- no user-visible output changes.

## Test Impact

No existing tests affected -- this change only updates documentation and code comments. No runtime behavior or interfaces are modified. The test files that reference `projects.json` (`tests/unit/test_reflections_multi_repo.py`, `tests/conftest.py`, etc.) use `PROJECTS_CONFIG_PATH` env var overrides which are already correct.

## Rabbit Holes

- Updating the stale worktree at `.claude/worktrees/agent-a1f22e42/` -- worktrees are ephemeral copies and will be recreated; don't waste time updating them
- Refactoring the legacy fallback in `bridge/routing.py` -- the 3-tier resolution (env var -> Desktop/Valor -> config/) is correct and intentional for backwards compatibility
- Adding `PROJECTS_CONFIG_PATH` to `config/settings.py` as a pydantic field -- the env var is already read directly in `bridge/routing.py` which is the correct approach since it's needed before Settings is fully initialized

## Risks

### Risk 1: Missing a reference
**Impact:** Confusion for developers reading stale docs
**Mitigation:** Automated grep verification in the final step confirms zero remaining stale references

## Race Conditions

No race conditions identified -- all operations are file edits with no concurrent access concerns.

## No-Gos (Out of Scope)

- Not changing any runtime behavior or config resolution logic
- Not modifying the `bridge/routing.py` fallback chain
- Not updating files inside `.claude/worktrees/` (ephemeral)
- Not adding `PROJECTS_CONFIG_PATH` to `config/settings.py` (already works via direct env var read)

## Update System

No update system changes required -- this is purely a documentation cleanup. The actual file move and code changes were already shipped in prior PRs.

## Agent Integration

No agent integration required -- this is a documentation/comment-only change with no new functionality.

## Documentation

### Feature Documentation
- [ ] Update all docs listed in the Solution section to reference `~/Desktop/Valor/projects.json`
- [ ] Verify `config/README.md` already documents the new location correctly

### Inline Documentation
- [ ] Update code comments in `scripts/migrate_model_relationships.py`, `scripts/reflections.py`, `tests/conftest.py`

## Success Criteria

- [ ] `grep -r "config/projects\.json" --include="*.md" --include="*.py" .` returns zero matches outside of: (a) `bridge/routing.py` legacy fallback comment, (b) `.claude/worktrees/`, (c) `config/projects.example.json` references
- [ ] All documentation references point to `~/Desktop/Valor/projects.json` or mention the `PROJECTS_CONFIG_PATH` env var
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`ruff check .`)

## Team Orchestration

### Team Members

- **Builder (doc-cleanup)**
  - Name: doc-cleanup-builder
  - Role: Update all stale config/projects.json references across docs and code comments
  - Agent Type: builder
  - Resume: true

- **Validator (ref-check)**
  - Name: ref-check-validator
  - Role: Verify no stale references remain
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update all stale references
- **Task ID**: build-doc-cleanup
- **Depends On**: none
- **Validates**: grep for "config/projects.json" returns no unexpected matches
- **Assigned To**: doc-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Update all 16 files listed in the Solution section
- Replace `config/projects.json` with `~/Desktop/Valor/projects.json` in docs
- Update code comments to reference the correct path

### 2. Validate no stale references
- **Task ID**: validate-refs
- **Depends On**: build-doc-cleanup
- **Assigned To**: ref-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "config/projects\.json" --include="*.md" --include="*.py" .` excluding worktrees
- Verify only acceptable matches remain (routing.py fallback comment)
- Run `ruff check .` and `ruff format --check .`

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-refs
- **Assigned To**: ref-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale refs in docs | `grep -r "config/projects\.json" --include="*.md" . \| grep -v worktrees \| grep -v CHANGELOG \| wc -l` | output contains 0 |
| No stale refs in code | `grep -r "config/projects\.json" --include="*.py" . \| grep -v worktrees \| grep -v routing.py \| wc -l` | output contains 0 |

---

## Open Questions

No open questions -- the scope is well-defined and the technical approach is straightforward.
