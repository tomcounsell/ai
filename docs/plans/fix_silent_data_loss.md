---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/336
---

# Fix Silent Data Loss Risks

## Problem

Several places in the pipeline silently lose data or operate with incomplete state, with no warning to operators or the agent. These affect every long-running SDLC session.

**Current behavior:**
1. Session history is silently truncated to 20 entries -- long SDLC runs lose early history with no log
2. Redis job status changes use delete-then-create -- a crash between the two orphans the job silently
3. Dropped steering messages are truncated to 120 chars in logs -- intent is lost
4. Auto-continue count is only logged when the cap is reached, not per increment
5. Popoto `save()` failures log a generic warning without specifying what failed to persist
6. Corrupted UTF-8 in Redis keys is handled but not logged with enough detail for forensics

**Desired outcome:**
All six data loss/visibility gaps have appropriate WARNING-level logging so operators can diagnose issues without needing to reproduce them.

## Prior Art

- **Issue #292 / PR #308**: Fixed mid-session steering -- messages now reach running agents. Related to item 3 (steering message handling) but didn't address the truncation of dropped messages.
- **Issue #309 / PR #321**: Observer Agent replaced auto-continue/summarizer. Changed auto-continue flow but didn't add per-increment logging.
- **PR #185**: Stage-aware auto-continue for SDLC jobs. Added the SDLC cap logic but not per-increment visibility.
- **Issue #211**: Dual AgentSession creation. Fixed the duplication but didn't address save failure logging.

No prior attempt specifically addressed the logging gaps identified in this issue.

## Data Flow

1. **Entry point**: Human message arrives via Telegram bridge
2. **Job queue**: Message is enqueued as AgentSession with status=pending
3. **Worker pickup**: delete-and-recreate transitions to running (item 2 risk)
4. **Agent execution**: History is appended via `append_history()` (item 1 risk)
5. **Completion**: Observer decides to auto-continue (item 4 visibility gap) or deliver
6. **Steering cleanup**: Unconsumed steering messages are popped and logged (item 3 truncation)
7. **Session finalization**: `save()` persists final state (item 5 risk)
8. **Orphan recovery**: Periodic scan finds stranded sessions (item 6 forensics gap)

## Architectural Impact

- **No new dependencies**: Pure logging additions
- **No interface changes**: All changes are internal logging improvements
- **Coupling**: No change -- these are isolated logging enhancements within existing code paths
- **Reversibility**: Trivially reversible -- removing log statements has zero functional impact

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

All six items are logging additions with no behavioral changes. Each is a 5-10 line edit.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **History truncation warning**: Log when entries are dropped from session history
- **Delete-and-recreate logging**: Log both sides of the Redis status change operation
- **Steering message full logging**: Increase truncation limit from 120 to 500 chars
- **Auto-continue per-increment logging**: Log count at every auto-continue, not just at cap
- **Save failure detail logging**: Include field context in save failure messages
- **UTF-8 corruption logging**: Log hex bytes when corrupted keys are detected

### Technical Approach

1. In `models/agent_session.py` `append_history()`: Add `logger.warning()` before truncation with old length and dropped count
2. In `agent/job_queue.py` `_pick_next_job()` and `_reset_running_to_pending()`: Add `logger.info()` before delete and after recreate
3. In `agent/job_queue.py` steering cleanup: Change `[:120]` to `[:500]`
4. In `agent/job_queue.py` auto-continue steer block: Add `logger.info()` at each increment (line ~1199)
5. In `models/agent_session.py` save failure handlers: Add field context to the warning
6. In `agent/job_queue.py` orphan recovery: Add `logger.warning()` with hex bytes when `errors="replace"` substitutes characters

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `append_history` save failure (line 147): already logs warning -- enhance with field context
- [ ] `set_link` save failure (line 168): already logs warning -- enhance with field context
- [ ] Steering cleanup (line 1357): already catches exception -- no change needed
- [ ] AgentSession update (line 1327): already catches -- enhance with field context

### Empty/Invalid Input Handling
- [ ] `append_history` with empty history list: already handled by `_get_history_list()` returning `[]`
- [ ] Steering messages with empty text: already handled -- the `[:500]` truncation is safe on empty strings

### Error State Rendering
- No user-visible output changes -- all changes are server-side logging

## Rabbit Holes

- Increasing history cap to 50 or adding overflow files -- that's a behavioral change, not a logging fix. Defer to a separate issue.
- Switching from delete-then-create to create-then-delete for Redis status changes -- that's a transactional safety improvement requiring careful testing. Out of scope.
- Re-queuing dropped steering messages instead of dropping them -- behavioral change, not logging.

## Risks

### Risk 1: Verbose logging in high-throughput scenarios
**Impact:** Log files grow faster if many sessions hit truncation
**Mitigation:** Use WARNING level (not INFO) for truncation so it can be filtered. These events should be rare in practice.

## Race Conditions

No race conditions identified -- all changes are adding log statements to existing synchronous code paths. No new shared state or concurrent access patterns.

## No-Gos (Out of Scope)

- Changing the history cap value (currently 20)
- Changing delete-then-create to create-then-delete pattern
- Re-queuing dropped steering messages
- Adding `last_persisted_at` timestamp field
- Adding counter metrics for orphan recoveries

## Update System

No update system changes required -- this is purely internal logging improvements with no new dependencies or configuration.

## Agent Integration

No agent integration required -- this is a bridge-internal change affecting only server-side logging.

## Documentation

- [ ] Add inline code comments explaining the logging rationale at each change point
- [ ] Update `docs/features/session-isolation.md` to mention history truncation warning behavior

## Success Criteria

- [ ] `append_history()` logs WARNING when history entries are dropped, including old length and count dropped
- [ ] `_pick_next_job()` logs both delete and recreate steps
- [ ] `_reset_running_to_pending()` logs both delete and recreate steps
- [ ] Steering message cleanup uses `[:500]` instead of `[:120]`
- [ ] Auto-continue logs count at each increment: `"Auto-continue {n}/{max} for session {id}"`
- [ ] Save failure warnings include which operation was being attempted
- [ ] Corrupted UTF-8 keys log hex bytes at WARNING level
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (logging-fixes)**
  - Name: logging-builder
  - Role: Add all six logging improvements
  - Agent Type: builder
  - Resume: true

- **Validator (logging-fixes)**
  - Name: logging-validator
  - Role: Verify all logging additions are correct
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add history truncation warning
- **Task ID**: build-history-warning
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `models/agent_session.py` `append_history()`, before `current = current[-HISTORY_MAX_ENTRIES:]`, add `logger.warning(f"Session {self.session_id} history truncated from {len(current)} to {HISTORY_MAX_ENTRIES}, {len(current) - HISTORY_MAX_ENTRIES} oldest entries lost")`

### 2. Add delete-and-recreate logging
- **Task ID**: build-redis-logging
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_pick_next_job()` (line ~333): add `logger.info(f"[{project_key}] Deleting job {chosen.job_id} for status change to running")`
- In `_reset_running_to_pending()`: already has logging -- verify both sides are covered

### 3. Increase steering message truncation
- **Task ID**: build-steering-truncation
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In steering cleanup (line ~1352): change `[:120]` to `[:500]`

### 4. Add per-increment auto-continue logging
- **Task ID**: build-autocontinue-logging
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- After `chat_state.auto_continue_count += 1` (line ~1199): add `logger.info(f"[{job.project_key}] Auto-continue {chat_state.auto_continue_count}/{effective_max} for session {job.session_id}")`

### 5. Enhance save failure logging
- **Task ID**: build-save-failure-logging
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent_session.py` save failure handlers: add context about which operation failed
- In `job_queue.py` line ~1328: enhance the generic "AgentSession update failed" message

### 6. Add UTF-8 corruption logging
- **Task ID**: build-utf8-logging
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In orphan recovery (line ~470): after `key.decode(errors="replace")`, check if replacement occurred and log `logger.warning(f"[{project_key}] Corrupted UTF-8 in Redis key: {key!r} (hex: {key.hex()})")`

### 7. Validation
- **Task ID**: validate-all
- **Depends On**: build-history-warning, build-redis-logging, build-steering-truncation, build-autocontinue-logging, build-save-failure-logging, build-utf8-logging
- **Assigned To**: logging-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff check models/agent_session.py agent/job_queue.py`
- Verify all six logging additions are present
- Run test suite

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: logging-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with history truncation warning info
- Add inline code comments at each change point

### 9. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: logging-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Validation Commands

- `python -m ruff check models/agent_session.py agent/job_queue.py` - lint passes
- `grep -n "history truncated" models/agent_session.py` - history warning exists
- `grep -n "Deleting job" agent/job_queue.py` - delete-side logging exists
- `grep -n "500" agent/job_queue.py` - steering truncation increased
- `grep -n "Auto-continue" agent/job_queue.py` - per-increment logging exists
- `grep -n "hex" agent/job_queue.py` - UTF-8 hex logging exists
- `pytest tests/` - all tests pass
