---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/198
---

# Continue Summarizer Bullet Format + AgentSession Migration

## Problem

The summarizer bullet-point format and AgentSession model unification were largely completed and landed on main (originating from the `session/summarizer_bullet_format` branch, issue #177). However, the branch stalled before reaching PR stage, and the migration was completed piecemeal across multiple PRs. An audit (`docs/audits/agent_session_migration_audit.md`, grade A-) identified residual cleanup:

1. **Stale imports in 7 files** -- Test files and `scripts/daydream.py` still import `RedisJob` or `SessionLog` via backward-compat shims instead of `AgentSession` directly
2. **Telegram bridge missing markdown sends** -- 3 `client.send_message()` calls in `bridge/telegram_bridge.py` should use `send_markdown()` for consistent formatting with fallback
3. **Stale plan doc** -- `docs/plans/summarizer_bullet_format.md` references issue #177 (closed) and describes work that's already done

**Current behavior:**
- Test files use `from agent.job_queue import RedisJob` and `from models.session_log import SessionLog` -- functional but misleading
- Bridge sends plain text for steering acks, revival prompts, and queue depth messages instead of markdown
- `scripts/daydream.py` aliases `AgentSession as SessionLog` in 4 places

**Desired outcome:**
- All Python code imports `AgentSession` from `models.agent_session` directly
- All Telegram message sends use `send_markdown()` with plain-text fallback
- Old plan doc marked complete, stale branch cleaned up

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Purely mechanical cleanup. No architectural decisions needed.

## Prerequisites

No prerequisites -- this work only changes imports and function calls in existing code.

## Solution

### Key Elements

- **Import migration**: Replace all `RedisJob` and `SessionLog` imports in test files and daydream.py with direct `AgentSession` imports
- **Markdown sends**: Replace 3 `client.send_message()` calls in telegram_bridge.py with `send_markdown()` calls
- **Cleanup**: Mark old plan as Complete, delete stale branch

### Flow

Grep for stale imports -> Update each file -> Run tests -> Update docs -> Clean up

### Technical Approach

**Phase A: Stale Import Migration**

Files to update (from audit):

| File | Current Import | Target Import |
|------|---------------|---------------|
| `tests/test_job_health_monitor.py` | `from agent.job_queue import RedisJob` | `from models.agent_session import AgentSession` |
| `tests/test_job_queue_race.py` | `from agent.job_queue import RedisJob` | `from models.agent_session import AgentSession` |
| `tests/test_reply_delivery.py` | `from agent.job_queue import RedisJob` | `from models.agent_session import AgentSession` |
| `tests/test_daydream_redis.py` | `from models.session_log import SessionLog` | `from models.agent_session import AgentSession` |
| `tests/unit/test_session_tags.py` | `from models.session_log import SessionLog` | `from models.agent_session import AgentSession` |
| `tests/test_redis_models.py` | `from models.session_log import SessionLog as AgentSession` | `from models.agent_session import AgentSession` |
| `scripts/daydream.py` | `from models.agent_session import AgentSession as SessionLog` | `from models.agent_session import AgentSession` (update all references) |

In each file, also rename all variable/class references from `RedisJob`/`SessionLog` to `AgentSession`.

**Phase B: Telegram Markdown Sends**

In `bridge/telegram_bridge.py`, replace these 3 `client.send_message()` calls with `send_markdown()`:

1. **Line ~826** -- Steering ack message ("Adding to current task" / abort ack)
2. **Line ~856** -- Revival prompt message
3. **Line ~909** -- Queue depth message ("Queued (position N)")

Import `send_markdown` from `bridge.markdown` at the top of the file or inline.

**Phase C: Cleanup**

- Update `docs/plans/summarizer_bullet_format.md` frontmatter: `status: Complete`
- Delete stale branch: `git branch -d session/summarizer_bullet_format`

## Rabbit Holes

- **Removing the backward-compat shims entirely** -- `models/session_log.py` and `RedisJob = AgentSession` in `agent/job_queue.py` should stay. External code or future tests may depend on them. Only clean up the import sites, not the shims themselves.
- **Refactoring test structure** -- Just change imports, don't reorganize test files or rename test classes
- **Changing daydream.py logic** -- Only update the import aliases, don't touch any business logic

## Risks

### Risk 1: Test breakage from import changes
**Impact:** Tests fail after renaming
**Mitigation:** Each file is a simple find-and-replace. Run `pytest tests/ -x` after changes. The shims guarantee the old import paths still work if any are missed.

### Risk 2: Telegram markdown parse errors on new send paths
**Impact:** Messages fail to deliver
**Mitigation:** `send_markdown()` already has a built-in plain-text fallback (try markdown, catch exception, retry without parse_mode). The steering acks, revival prompts, and queue depth messages are simple text that won't trigger markdown parse errors.

## No-Gos (Out of Scope)

- Removing backward-compat shims (`session_log.py`, `RedisJob` alias)
- Removing `RedisJob` from `agent/__init__.py` exports
- Modifying summarizer logic or formatting
- Changing AgentSession model fields
- Migrating any doc-only references to RedisJob/SessionLog (docs can mention the history)

## Update System

No update system changes required -- this is purely internal import cleanup and a minor bridge change. The update script pulls code and restarts, which is sufficient.

## Agent Integration

No agent integration required -- this is a bridge-internal and test cleanup change. No new tools or MCP servers needed.

## Documentation

- [ ] Update `docs/audits/agent_session_migration_audit.md` to mark stale items as resolved
- [ ] Mark `docs/plans/summarizer_bullet_format.md` as Complete
- [ ] No new feature docs needed -- existing docs are already correct

## Success Criteria

- [ ] No Python file imports `RedisJob` except `agent/job_queue.py` (backward-compat alias) and `agent/__init__.py` (re-export)
- [ ] No Python file imports `from models.session_log import SessionLog` except `tests/test_agent_session_lifecycle.py` (which explicitly tests the shim)
- [ ] `scripts/daydream.py` uses `AgentSession` directly (no `as SessionLog` alias)
- [ ] All 3 `client.send_message()` calls in `bridge/telegram_bridge.py` replaced with `send_markdown()`
- [ ] `docs/plans/summarizer_bullet_format.md` status is `Complete`
- [ ] Stale branch `session/summarizer_bullet_format` deleted
- [ ] Tests pass (`pytest tests/ -x`)
- [ ] Lint passes (`ruff check . && black --check .`)

## Team Orchestration

### Team Members

- **Builder (migration)**
  - Name: import-migrator
  - Role: Update all stale imports and telegram bridge sends
  - Agent Type: builder
  - Resume: true

- **Validator (migration)**
  - Name: migration-validator
  - Role: Verify all imports migrated, tests pass, lint clean
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 -- Core (default choices):**
- `builder` - General implementation
- `validator` - Read-only verification

## Step by Step Tasks

### 1. Migrate stale imports in test files
- **Task ID**: build-test-imports
- **Depends On**: none
- **Assigned To**: import-migrator
- **Agent Type**: builder
- **Parallel**: true
- Replace `RedisJob` imports and references in `tests/test_job_health_monitor.py`, `tests/test_job_queue_race.py`, `tests/test_reply_delivery.py`
- Replace `SessionLog` imports and references in `tests/test_daydream_redis.py`, `tests/unit/test_session_tags.py`, `tests/test_redis_models.py`

### 2. Migrate stale imports in daydream.py
- **Task ID**: build-daydream-imports
- **Depends On**: none
- **Assigned To**: import-migrator
- **Agent Type**: builder
- **Parallel**: true
- Replace `AgentSession as SessionLog` with `AgentSession` in `scripts/daydream.py`
- Update all `SessionLog` variable references to `AgentSession`
- Update docstrings/comments mentioning SessionLog

### 3. Replace client.send_message with send_markdown in telegram_bridge.py
- **Task ID**: build-markdown-sends
- **Depends On**: none
- **Assigned To**: import-migrator
- **Agent Type**: builder
- **Parallel**: true
- Replace 3 `client.send_message()` calls with `send_markdown()` calls
- Add `from bridge.markdown import send_markdown` import

### 4. Cleanup old plan and stale branch
- **Task ID**: build-cleanup
- **Depends On**: none
- **Assigned To**: import-migrator
- **Agent Type**: builder
- **Parallel**: true
- Update `docs/plans/summarizer_bullet_format.md` frontmatter to `status: Complete`
- Delete local branch `session/summarizer_bullet_format`
- Update `docs/audits/agent_session_migration_audit.md` to mark stale items resolved

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-test-imports, build-daydream-imports, build-markdown-sends, build-cleanup
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x`
- Run `ruff check . && black --check .`
- Verify no stale imports remain (grep for `from models.session_log import` and `from agent.job_queue import RedisJob` in Python files excluding expected locations)
- Verify `send_markdown` used in telegram_bridge.py
- Verify all success criteria met

## Validation Commands

- `grep -rn "from agent.job_queue import.*RedisJob" tests/` - Should return no results
- `grep -rn "from models.session_log import" tests/ scripts/` - Should return no results (except test_agent_session_lifecycle.py which tests the shim)
- `grep -n "client.send_message" bridge/telegram_bridge.py` - Should return no results
- `grep -n "send_markdown" bridge/telegram_bridge.py` - Should show 3+ matches
- `pytest tests/ -x` - All tests pass
- `ruff check models/ bridge/ agent/ tools/ tests/ scripts/daydream.py` - No lint errors
- `black --check models/ bridge/ agent/ tools/ tests/ scripts/daydream.py` - Formatting OK
