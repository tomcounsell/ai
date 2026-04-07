---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/565
last_comment_id:
---

# Remove Issue Poller

## Problem

The issue poller is a deprecated automation feature that actively causes harm. It recently corrupted the ai repo twice (#564) by checking out foreign-repo branches into the ai working directory. Rather than fix the underlying cross-repo dispatch bug, the feature is being removed entirely because PM agent orchestration replaces its purpose.

**Current behavior:**
- Launchd runs `scripts/issue_poller.py` every 5 minutes across 11 configured projects
- Cross-repo dispatch is broken (#564), corrupting the ai repo working directory
- Dead code accumulates: `SeenIssue` model, dedup helper, install script, tests, docs all reference a feature that should not run

**Desired outcome:**
- Every file, reference, config entry, test, doc section, and launchd service related to the issue poller is gone
- The codebase reads as if the feature never existed
- The launchd service is unloaded and plist removed
- Related issue #564 closed as resolved by removal

## Prior Art

- **Issue #564**: Issue poller dispatches plan creation in wrong working directory -- the critical cross-repo bug that motivated this removal. PR #575 merged a fix, but the feature itself is still deprecated.
- **Issue #307**: Poll GitHub issues for automatic SDLC kickoff and deduplication -- the original feature request. Shipped via PR #384.
- **PR #507**: Migrate raw Redis anti-patterns to Popoto models -- created the `SeenIssue` Popoto model that replaced raw Redis keys.

## Architectural Impact

- **Removed dependencies**: `SeenIssue` Popoto model, issue poller scripts, launchd service
- **Interface changes**: None -- the poller ran independently with no inbound API
- **Coupling**: Decreases coupling -- removes a scheduled process that interacted with GitHub, Redis, and Claude CLI
- **Data ownership**: `SeenIssue` Redis records become orphaned and should be flushed
- **Reversibility**: Easy -- git revert would restore all files; launchd plist would need re-installation

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a pure deletion task with clear boundaries. The issue already enumerates every file and reference.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **File deletion**: Remove 8 files (3 scripts, 1 plist, 1 model, 2 test files, 1 feature doc)
- **Reference surgery**: Edit 8 files to remove issue poller mentions
- **Runtime cleanup**: Unload launchd service, remove installed plist, flush Redis records

### Flow

**Start** → Delete files → Edit references → Unload launchd → Flush Redis → Verify grep returns zero hits → **Done**

### Technical Approach

- Delete files first, then surgically edit referencing files
- Unload launchd service before removing the plist from LaunchAgents
- Flush `SeenIssue` records from Redis
- Close #564 as resolved by removal

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is purely a deletion task

### Empty/Invalid Input Handling
- Not applicable -- no new functions or modified functions

### Error State Rendering
- Not applicable -- no user-visible output changes

## Test Impact

- [ ] `tests/test_issue_poller.py` (entire file) -- DELETE: tests the removed feature
- [ ] `tests/unit/test_seen_issue.py` (entire file) -- DELETE: tests the removed model
- [ ] `tests/conftest.py` -- UPDATE: remove issue_poller reflection name mapping
- [ ] `tests/README.md` -- UPDATE: remove test_issue_poller reference

## Rabbit Holes

- Editing worktree copies of deleted files -- worktrees are branch-scoped and will be cleaned up when pruned
- Editing `docs/plans/redis-popoto-migration.md` -- historical plan document; editing it would rewrite history
- Adding replacement automation -- this is a pure removal, replacement is a separate future issue

## Risks

### Risk 1: Launchd service still running after removal
**Impact:** Cron job errors every 5 minutes when script is missing
**Mitigation:** Unload the service with `launchctl bootout` before deleting files

### Risk 2: Import errors from removed model
**Impact:** Other code importing `SeenIssue` would crash
**Mitigation:** Grep confirms no imports outside the files being deleted and `models/__init__.py` (which will be edited)

## Race Conditions

No race conditions identified -- this is a synchronous deletion task with no concurrent access patterns.

## No-Gos (Out of Scope)

- No replacement automation -- PM agent orchestration is a separate concern
- No worktree cleanup -- those are branch-scoped
- No editing of historical plan documents

## Update System

The update skill (`scripts/remote-update.sh`) and setup skill (`.claude/skills/setup/SKILL.md`) both reference `install_issue_poller.sh`. These references must be removed as part of the reference surgery. After this ships, running `/update` on any machine will no longer attempt to install the issue poller service.

## Agent Integration

No agent integration required -- the issue poller was a standalone scheduled script, not an agent-accessible tool. No MCP servers, `.mcp.json` entries, or bridge imports reference it.

## Documentation

- [ ] Delete `docs/features/issue-poller.md` (the feature doc itself)
- [ ] Remove entry from `docs/features/README.md` index table
- [ ] Remove issue poller entries from `CLAUDE.md` quick commands table
- [ ] Remove references from `docs/guides/valor-name-references.md`

## Success Criteria

- [ ] All 8 issue poller files deleted from the repo
- [ ] All referencing files edited to remove issue poller mentions
- [ ] `grep -r "issue_poller\|issue_dedup\|SeenIssue\|seen_issue" --include="*.py" --include="*.md" --include="*.sh"` returns zero hits (excluding worktrees, git history, and `docs/plans/redis-popoto-migration.md`)
- [ ] Launchd service unloaded and plist removed from `~/Library/LaunchAgents/`
- [ ] `python -c "from models.seen_issue import SeenIssue"` fails with ImportError
- [ ] `pytest tests/unit/ -x` passes
- [ ] #564 closed as resolved by removal
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (removal)**
  - Name: poller-remover
  - Role: Delete files, edit references, unload launchd, flush Redis
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: removal-validator
  - Role: Verify all traces are gone
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using builder + validator from Tier 1 core types.

## Step by Step Tasks

### 1. Unload launchd and delete files
- **Task ID**: build-delete
- **Depends On**: none
- **Assigned To**: poller-remover
- **Agent Type**: builder
- **Parallel**: true
- Unload launchd service: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.valor.issue-poller.plist`
- Remove plist from LaunchAgents: `rm ~/Library/LaunchAgents/com.valor.issue-poller.plist`
- Delete logs: `rm -f logs/issue_poller.log logs/issue_poller_error.log`
- Delete files: `scripts/issue_poller.py`, `scripts/issue_dedup.py`, `scripts/install_issue_poller.sh`, `com.valor.issue-poller.plist`, `models/seen_issue.py`, `tests/test_issue_poller.py`, `tests/unit/test_seen_issue.py`, `docs/features/issue-poller.md`

### 2. Edit references
- **Task ID**: build-references
- **Depends On**: build-delete
- **Assigned To**: poller-remover
- **Agent Type**: builder
- **Parallel**: false
- Remove `SeenIssue` export from `models/__init__.py`
- Remove issue poller entries from `CLAUDE.md` quick commands table
- Remove issue_poller reflection name mapping from `tests/conftest.py`
- Remove test_issue_poller reference from `tests/README.md`
- Remove install script references from `docs/guides/valor-name-references.md`
- Remove `install_issue_poller.sh` reference from `.claude/skills/update/SKILL.md`
- Remove issue poller setup section from `.claude/skills/setup/SKILL.md`
- Remove issue poller entry from `docs/features/README.md`

### 3. Flush Redis records
- **Task ID**: build-redis
- **Depends On**: build-delete
- **Assigned To**: poller-remover
- **Agent Type**: builder
- **Parallel**: true
- Flush `SeenIssue` records from Redis

### 4. Close related issue
- **Task ID**: build-close-564
- **Depends On**: build-delete
- **Assigned To**: poller-remover
- **Agent Type**: builder
- **Parallel**: true
- Close issue #564 with comment: "Resolved by removing the issue poller entirely (#565)"

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-delete, build-references, build-redis, build-close-564
- **Assigned To**: removal-validator
- **Agent Type**: validator
- **Parallel**: false
- Run grep verification: `grep -r "issue_poller\|issue_dedup\|SeenIssue\|seen_issue" --include="*.py" --include="*.md" --include="*.sh"` (excluding worktrees and redis-popoto-migration.md)
- Verify ImportError: `python -c "from models.seen_issue import SeenIssue"`
- Run unit tests: `pytest tests/unit/ -x`
- Verify launchd service is gone: `launchctl list | grep issue-poller`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No poller refs | `grep -r "issue_poller\|issue_dedup\|SeenIssue\|seen_issue" --include="*.py" --include="*.md" --include="*.sh" . \| grep -v ".worktrees/" \| grep -v "redis-popoto-migration"` | exit code 1 |
| Model removed | `python -c "from models.seen_issue import SeenIssue"` | exit code 1 |
| Launchd gone | `launchctl list 2>/dev/null \| grep issue-poller` | exit code 1 |

## Critique Results

**Plan**: docs/plans/remove_issue_poller.md
**Issue**: #565
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 3 total (0 blockers, 3 concerns, 0 nits)

### Concerns

#### 1. Task 2 missing `scripts/remote-update.sh` edit
- **Severity**: CONCERN
- **Critics**: Operator, Skeptic
- **Location**: Step by Step Tasks > Task 2 / Update System section
- **Finding**: The Update System section explicitly states `scripts/remote-update.sh` references `install_issue_poller.sh`, but grep confirms the script does NOT contain that reference -- only `.claude/skills/update/SKILL.md` does. The prose is misleading but the task list is actually correct. No action required, but the Update System section text should be tightened to avoid confusing the builder.
- **Suggestion**: Edit the Update System section to say "The update skill (`.claude/skills/update/SKILL.md`) and setup skill..." instead of referencing `scripts/remote-update.sh`.

#### 2. Success criterion references `/do-docs` with no corresponding task
- **Severity**: CONCERN
- **Critics**: Skeptic, Operator
- **Location**: Success Criteria
- **Finding**: Success criterion "Documentation updated (`/do-docs`)" implies running the `/do-docs` skill as a separate step, but no task in the Step by Step Tasks section invokes `/do-docs`. Task 2 handles manual doc edits, which may be sufficient, but the criterion is ambiguous about whether `/do-docs` needs to run separately.
- **Suggestion**: Either remove the `/do-docs` reference from success criteria (since Task 2 covers all doc edits manually) or add an explicit task to run `/do-docs` after Task 2.

#### 3. No explicit task for log file cleanup verification
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Step by Step Tasks > Task 1 / Task 5
- **Finding**: Task 1 deletes log files (`logs/issue_poller.log`, `logs/issue_poller_error.log`) but Task 5 (validation) does not verify they were removed. This is low-risk since `rm -f` is idempotent, but the validation step is incomplete relative to the deletion step.
- **Suggestion**: Add a quick check in Task 5: verify `logs/issue_poller.log` does not exist.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-5, sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` references point to valid task IDs |
| File paths exist | PASS | 16 of 16 referenced files exist (8 to delete, 8 to edit) |
| Prerequisites met | PASS | No prerequisites declared |
| Cross-references | PASS | All success criteria map to tasks (with minor `/do-docs` ambiguity noted above); no-gos and rabbit holes do not appear in solution |

### Verdict

**READY TO BUILD** -- No blockers. The three concerns are minor documentation/ambiguity issues that do not affect execution correctness. The plan is thorough, well-scoped, and all referenced files exist as expected.

---

## Open Questions

No open questions -- the issue is fully specified with concrete file lists and acceptance criteria verified by recon.
