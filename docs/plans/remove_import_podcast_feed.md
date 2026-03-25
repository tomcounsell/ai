---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-17
tracking: https://github.com/yudame/cuttlefish/issues/163
last_comment_id:
---

# Remove import_podcast_feed Management Command

## Problem

The `import_podcast_feed` management command was a one-time migration tool for backfilling episodes from the old research repo's RSS feed. That migration completed months ago and the command is no longer needed.

**Current behavior:**
Dead code sits in the codebase, previously caused test failures (issue #122) by hitting the privacy immutability guard, and references patterns no longer in use.

**Desired outcome:**
The command, its tests, and its dedicated migration doc are removed. The codebase is cleaner with no dead code.

## Prior Art

- **Issue #122**: Fix 4 pre-existing test failures — one failure was caused by `import_podcast_feed` passing `privacy` in `update_or_create` defaults. Fixed by moving to `create_defaults`. Migration was already complete at that time.
- **PR #49**: Add Django podcast app with models, admin, feeds, views, and import — the PR that originally added the command.
- **PR #57**: Add Episode Artifacts and Publish Workflow — superseded the feed import with the `backfill_episodes` command.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Remove command file**: Delete `apps/podcast/management/commands/import_podcast_feed.py`
- **Remove test file**: Delete `apps/podcast/tests/test_import_command.py`
- **Remove migration doc**: Delete `docs/operations/podcast-migration.md` (only covers the one-time import)
- **Keep `_episode_import_utils.py`**: This module is used by `backfill_episodes.py` and `publish_episode.py` — NOT by `import_podcast_feed.py`

### Technical Approach

- Straight deletion of 3 files
- No code modifications needed elsewhere — `import_podcast_feed.py` has no importers outside its own test file
- Plan references in `docs/plans/` are historical and do not need updating (they document past decisions)

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is pure deletion

### Empty/Invalid Input Handling
- Not applicable — no new or modified functions

### Error State Rendering
- Not applicable — no user-visible output changes

## Test Impact

- [ ] `apps/podcast/tests/test_import_command.py::ImportPodcastFeedTestCase` — DELETE: tests the removed command
- [ ] `apps/podcast/tests/test_import_command.py::ImportBlankAudioUrlTestCase` — DELETE: tests the removed command

## Rabbit Holes

- Refactoring `backfill_episodes.py` or other management commands — separate concern
- Updating references in historical plan docs (`django-podcast-setup.md`, `fix_preexisting_test_failures.md`, `relax_podcast_privacy_guard.md`) — these are archived context, not operational docs

## Risks

### Risk 1: Accidentally removing shared utilities
**Impact:** `backfill_episodes` and `publish_episode` commands break
**Mitigation:** `_episode_import_utils.py` is confirmed NOT imported by `import_podcast_feed.py` — only by `backfill_episodes.py` and `publish_episode.py`. Leave it untouched.

## Race Conditions

No race conditions identified — this is a static code deletion with no runtime implications.

## No-Gos (Out of Scope)

- Removing `_episode_import_utils.py` (used by other commands)
- Removing `backfill_episodes.py` (actively used)
- Updating historical plan documents that reference the command

## Update System

No update system changes required — this is purely a code cleanup removing unused files.

## Agent Integration

No agent integration required — this removes a management command that was never exposed through MCP or the bridge.

## Documentation

- [ ] Delete `docs/operations/podcast-migration.md` (covers only the removed command's workflow)
- [ ] No new documentation needed — this is a removal

## Success Criteria

- [ ] `apps/podcast/management/commands/import_podcast_feed.py` no longer exists
- [ ] `apps/podcast/tests/test_import_command.py` no longer exists
- [ ] `docs/operations/podcast-migration.md` no longer exists
- [ ] `_episode_import_utils.py` still exists and is importable
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`ruff check`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete the three files
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: cleanup-validator
  - Role: Verify files removed, remaining commands work, tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove Dead Code
- **Task ID**: build-cleanup
- **Depends On**: none
- **Validates**: Ensure no import errors, `pytest tests/ -x -q` passes
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `apps/podcast/management/commands/import_podcast_feed.py`
- Delete `apps/podcast/tests/test_import_command.py`
- Delete `docs/operations/podcast-migration.md`

### 2. Validate Cleanup
- **Task ID**: validate-cleanup
- **Depends On**: build-cleanup
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the 3 files are gone
- Verify `_episode_import_utils.py` still exists
- Run `python -c "from apps.podcast.management.commands._episode_import_utils import EPISODE_FIELD_FILES"` to confirm shared utils still importable
- Run test suite to confirm no breakage

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-cleanup
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Command removed | `test ! -f apps/podcast/management/commands/import_podcast_feed.py` | exit code 0 |
| Tests removed | `test ! -f apps/podcast/tests/test_import_command.py` | exit code 0 |
| Doc removed | `test ! -f docs/operations/podcast-migration.md` | exit code 0 |
| Shared utils intact | `python -c "from apps.podcast.management.commands._episode_import_utils import EPISODE_FIELD_FILES"` | exit code 0 |
