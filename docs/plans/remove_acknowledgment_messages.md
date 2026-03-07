---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-03-07
tracking: https://github.com/tomcounsell/ai/issues/283
---

# Remove Acknowledgment Messages

## Problem

The `BackgroundTask` watchdog sends "I'm working on this." to chat after 3 minutes of silence. This is unnecessary noise -- a senior developer doesn't ping their PM to say they're doing their job.

**Current behavior:**
After 180 seconds of silence, the watchdog in `BackgroundTask._watchdog()` calls `messenger.send_acknowledgment()`, which sends "I'm working on this." to the Telegram chat. The Telegram reaction emoji (hourglass) already signals work is in progress.

**Desired outcome:**
The watchdog still runs for internal health logging (so we know the task is alive), but it no longer sends any chat message. All acknowledgment-related code, constants, and methods are removed as dead code.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work -- straightforward code removal with clear blast radius.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Watchdog conversion**: Change `_watchdog()` to log internally instead of sending a chat message
- **Dead code removal**: Remove `send_acknowledgment()`, `acknowledgment_sent` flag, `ACKNOWLEDGMENT_MESSAGE`, `ACKNOWLEDGMENT_TIMEOUT_SECONDS` constants, and backward-compatible imports
- **Test updates**: Update tests to reflect new behavior (watchdog logs, doesn't message)

### Flow

**Message arrives** -> BackgroundTask starts -> Watchdog starts -> 180s elapses -> Watchdog logs health check internally -> Task completes -> Result sent to chat

### Technical Approach

Option B from the issue: full cleanup of dead code.

1. **`agent/messenger.py`**:
   - Remove `send_acknowledgment()` method from `BossMessenger`
   - Remove `acknowledgment_sent` field from `BossMessenger`
   - Remove `"acknowledgment"` from `MessageRecord.message_type` comment
   - Change `BackgroundTask._watchdog()` to log instead of calling `send_acknowledgment()`
   - Remove `acknowledgment_message` parameter from `BackgroundTask.__init__`

2. **`bridge/agents.py`**:
   - Remove `ACKNOWLEDGMENT_TIMEOUT_SECONDS` constant
   - Remove `ACKNOWLEDGMENT_MESSAGE` constant

3. **`bridge/telegram_bridge.py`**:
   - Remove backward-compatible imports of `ACKNOWLEDGMENT_MESSAGE` and `ACKNOWLEDGMENT_TIMEOUT_SECONDS`

4. **`agent/job_queue.py`**:
   - Remove `acknowledgment_timeout=180.0` kwarg from `BackgroundTask()` constructor (use default)

5. **`tests/test_messenger.py`**:
   - Remove `test_acknowledgment_sent_once`
   - Remove `test_acknowledgment_skipped_if_already_communicated`
   - Update `test_slow_task_sends_acknowledgment` to verify NO acknowledgment is sent (watchdog logs only)
   - Update `TestIntegration.test_multiple_messages_scenario` to remove acknowledgment assertion
   - Update `TestIntegration.test_concurrent_tasks` to not expect acknowledgment message from slow task

## Race Conditions

No race conditions identified -- this change removes functionality from async code but does not alter any concurrency patterns, shared state, or timing dependencies. The watchdog still runs on the same asyncio task and still gets cancelled when the main task completes.

## Rabbit Holes

- **Removing the watchdog entirely** -- The watchdog has value as an internal health check. Keep it for logging, just stop it from sending messages.
- **Making the timeout configurable** -- The timeout is only used for logging now. No reason to make it configurable.
- **Changing reaction behavior** -- Reactions are a separate concern and work correctly. Don't touch them.

## Risks

### Risk 1: Something depends on `has_communicated()` counting acknowledgments
**Impact:** If acknowledgments contributed to `has_communicated()` being True, removing them could change reaction selection logic.
**Mitigation:** Reviewed all callers of `has_communicated()`. It checks `len(self.messages_sent) > 0`. Since acknowledgments were only sent when NO other messages had been sent yet, and result messages are always sent afterward, the removal doesn't change the final `has_communicated()` state at reaction-selection time. The reaction logic in `job_queue.py` runs after the task completes, at which point the result message has already been sent.

## No-Gos (Out of Scope)

- Changing reaction semantics or the hourglass reaction
- Modifying the auto-continue system
- Adding new notification mechanisms to replace acknowledgments
- Changing the watchdog timeout value

## Update System

No update system changes required -- this is purely internal code removal. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this is a bridge-internal change. No MCP servers, tools, or `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/message-pipeline.md` to remove acknowledgment references
- [ ] Update `docs/features/reaction-semantics.md` to remove acknowledgment references

### Inline Documentation
- [ ] Update docstrings on `BackgroundTask` and `BossMessenger` to remove acknowledgment references

## Success Criteria

- [ ] `send_acknowledgment()` method removed from `BossMessenger`
- [ ] `acknowledgment_sent` field removed from `BossMessenger`
- [ ] `ACKNOWLEDGMENT_MESSAGE` and `ACKNOWLEDGMENT_TIMEOUT_SECONDS` removed from `bridge/agents.py`
- [ ] Backward-compatible imports removed from `bridge/telegram_bridge.py`
- [ ] `BackgroundTask._watchdog()` logs internally instead of sending a chat message
- [ ] `acknowledgment_message` parameter removed from `BackgroundTask.__init__`
- [ ] Tests updated to reflect new behavior
- [ ] No remaining references to "acknowledgment" in code (except test history/docs)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove acknowledgment code and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: cleanup-validator
  - Role: Verify all acknowledgment code removed and tests pass
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using core tier: builder + validator.

## Step by Step Tasks

### 1. Remove Acknowledgment Code
- **Task ID**: build-cleanup
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `send_acknowledgment()` and `acknowledgment_sent` from `agent/messenger.py`
- Change `BackgroundTask._watchdog()` to log instead of send
- Remove `acknowledgment_message` param from `BackgroundTask.__init__`
- Remove constants from `bridge/agents.py`
- Remove backward-compatible imports from `bridge/telegram_bridge.py`
- Remove `acknowledgment_timeout=180.0` kwarg from `agent/job_queue.py`
- Update tests in `tests/test_messenger.py`

### 2. Validate Changes
- **Task ID**: validate-cleanup
- **Depends On**: build-cleanup
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no remaining `send_acknowledgment` references in code
- Verify no remaining `ACKNOWLEDGMENT_MESSAGE` or `ACKNOWLEDGMENT_TIMEOUT_SECONDS` references
- Run all tests
- Verify `ruff check` and `ruff format` pass

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-cleanup
- **Assigned To**: cleanup-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/message-pipeline.md` to remove acknowledgment references
- Update `docs/features/reaction-semantics.md` to remove acknowledgment references

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)

## Validation Commands

- `grep -r 'send_acknowledgment' agent/ bridge/ --include='*.py'` - No acknowledgment method references (expect 0)
- `grep -r 'ACKNOWLEDGMENT_MESSAGE\|ACKNOWLEDGMENT_TIMEOUT' agent/ bridge/ --include='*.py'` - No constant references (expect 0)
- `pytest tests/test_messenger.py -v` - Messenger tests pass
- `python -m ruff check . && python -m ruff format --check .` - Lint and format pass
