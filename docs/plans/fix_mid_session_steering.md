---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-08
tracking: https://github.com/valorengels/ai/issues/292
---

# Fix Mid-Session Steering: Reply-to Messages Don't Reach Running Agents

## Problem

When a user sends a reply-to message in Telegram to steer a running agent, the message never reaches the agent. Instead it falls through to the job queue as a separate job, getting executed after the original completes -- too late to provide course correction.

**Current behavior:**
1. User sends a message, agent starts processing (status transitions `pending` -> `running`)
2. User sends a reply-to message to steer the agent mid-execution
3. Bridge steering check queries for `AgentSession(session_id=..., status="active")`
4. Session is in `running` status (not `active`) -- steering check finds nothing
5. Message falls through to job queue as a new job
6. Reply executes after original job completes -- useless for steering

**Desired outcome:**
Reply-to messages for threads with running agents are pushed to the Redis steering queue and injected into the running Claude Code session via the PostToolUse hook, allowing real-time course correction.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on status values)
- Review rounds: 1 (code review)

Solo dev work is fast -- the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are within the existing bridge/agent codebase and use existing Redis infrastructure.

## Solution

### Key Elements

- **Status-aware steering check**: Fix the bridge steering check to match sessions in `running` status (the actual status during agent execution), not just `active`
- **Silent exception surfacing**: Replace the `except Exception` fallthrough with explicit error logging that makes steering failures visible
- **Race window mitigation**: Widen the steering check to include both `running` and `active` statuses to cover the transition window

### Flow

**User reply** -> Bridge receives reply-to -> Steering check queries `status="running"` -> Match found -> `push_steering_message()` -> PostToolUse hook fires -> `_handle_steering()` pops message -> `client.interrupt()` + `client.query()` -> Agent receives mid-execution guidance

### Technical Approach

1. **Fix the status filter in the steering check** (line 807 of `bridge/telegram_bridge.py`):
   - Change `status="active"` to query for both `running` and `active` statuses
   - The `running` status is set by `_pop_job()` when the worker picks up a job
   - The `active` status is set later by `_execute_job()` when auto-continue defers reaction
   - Both represent "agent is currently working" and should match for steering

2. **Improve error handling in the steering check** (lines 832-835):
   - Log the full exception with traceback, not just a warning
   - Distinguish between "no session found" (expected for non-running threads) and "Redis error" (unexpected)

3. **Add a defensive status re-check for the race window**:
   - Between `pending` -> `running` transition, a reply could arrive when no status matches
   - Also check `pending` status and log if found (message will be consumed when the job starts and the PostToolUse hook fires)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `except Exception` block at lines 832-835 of `bridge/telegram_bridge.py` currently swallows all errors with a warning log. Fix to log at ERROR level with traceback for Redis/DB failures, keep WARNING for "no session found"
- [ ] Verify the PostToolUse hook's `except Exception` in `_handle_steering` (health_check.py line 250) logs properly and doesn't block

### Empty/Invalid Input Handling
- [ ] Test steering check with empty `session_id` -- should not crash
- [ ] Test steering check when Redis is temporarily unreachable -- should fall through gracefully to queue
- [ ] Test `push_steering_message` with empty `clean_text` -- should still push (empty steering messages are valid)

### Error State Rendering
- [ ] The "Adding to current task" acknowledgment (line 823) must only appear when the message was actually pushed to the steering queue, not when the session lookup succeeded but push failed
- [ ] If steering fails, the fallthrough to job queue should be logged visibly (not just a debug/warning)

## Rabbit Holes

- **Building a real-time WebSocket/SSE channel between bridge and agent** -- The existing Redis queue + PostToolUse hook polling pattern is sufficient. No need for a persistent connection.
- **Implementing priority ordering in the steering queue** -- Messages are consumed FIFO which is correct for chronological steering. Priority would add complexity without value.
- **Fixing the `active` status lifecycle globally** -- The `active` status is used in other contexts (auto-continue deferral). Changing its semantics would be a larger refactor. Just widen the steering check.

## Risks

### Risk 1: Steering messages queued during `pending` -> `running` transition window
**Impact:** Messages pushed during the ~100ms between job creation and worker pickup may sit in the steering queue until the first tool call fires the PostToolUse hook. This is acceptable -- they'll be consumed within seconds once the agent starts.
**Mitigation:** Log when a steering message is pushed for a session in `pending` status so the behavior is observable.

### Risk 2: Breaking the existing `active` status semantics
**Impact:** Other code paths may depend on `active` meaning something specific (auto-continue deferral in `_execute_job`).
**Mitigation:** Only add `running` to the steering check filter -- do not change where `active` is set or what it means elsewhere.

## Race Conditions

### Race 1: Steering message pushed between session delete and recreate
**Location:** `agent/job_queue.py`, `_pop_job()` (lines 400-436)
**Trigger:** User sends reply while `_pop_job` is between `async_delete()` (line 425) and `async_create()` (line 428). During this window, the session doesn't exist in either status.
**Data prerequisite:** The session must exist in Redis for the steering check to find it.
**State prerequisite:** Session must be in `running` or `active` status.
**Mitigation:** The steering check runs in the bridge (Telethon event handler), not in the job queue worker. The delete-and-recreate in `_pop_job` is nearly instantaneous (two Redis commands). The window is sub-millisecond. If the check hits this window, it falls through to the job queue -- the message is not lost, just slightly delayed. No additional mitigation needed.

### Race 2: Steering message pushed after job completion but before cleanup
**Location:** `agent/job_queue.py`, `_execute_job()` (lines 1647-1659)
**Trigger:** User sends reply just as the agent finishes work but before `pop_all_steering_messages` cleanup runs.
**Data prerequisite:** Steering queue must have messages when cleanup runs.
**State prerequisite:** Job is transitioning from `running` to `completed`.
**Mitigation:** The cleanup at line 1647-1659 already logs unconsumed messages. The message was too late to affect the agent -- this is correct behavior. No fix needed.

## No-Gos (Out of Scope)

- Changing the overall status lifecycle (pending/running/active/dormant/completed/failed)
- Adding new Redis data structures (the existing steering queue pattern is correct)
- Modifying the PostToolUse hook's steering consumption logic (it works correctly already)
- Building integration tests that require a running Telegram client
- Implementing guaranteed delivery / retry for steering messages

## Update System

No update system changes required -- this is a bridge-internal bug fix. The fix modifies existing code in `bridge/telegram_bridge.py` with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The steering queue (`agent/steering.py`), PostToolUse hook (`agent/health_check.py`), and SDK client injection (`agent/sdk_client.py`) already work correctly. The bug is solely in the bridge's steering check that filters for the wrong status value.

## Documentation

- [ ] Update `docs/features/mid-session-steering.md` describing the steering flow end-to-end (new doc)
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] Bridge steering check matches sessions in `running` status (not just `active`)
- [ ] Reply-to messages for running sessions are pushed to the Redis steering queue
- [ ] Steering messages are consumed by the PostToolUse hook and injected into the running agent
- [ ] Silent exception fallthrough is replaced with visible error logging
- [ ] Acknowledgment ("Adding to current task") is sent only when message is successfully pushed
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (steering-fix)**
  - Name: steering-builder
  - Role: Fix the status filter and error handling in the bridge steering check
  - Agent Type: builder
  - Resume: true

- **Validator (steering-fix)**
  - Name: steering-validator
  - Role: Verify the fix matches running sessions and handles edge cases
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using Tier 1 core types only -- this is a focused bug fix.

## Step by Step Tasks

### 1. Fix status filter in steering check
- **Task ID**: build-status-filter
- **Depends On**: none
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/telegram_bridge.py` line 807, change `status="active"` to query for both `running` and `active` statuses
- Use `AgentSession.query.filter(session_id=session_id, status="running")` as primary, fall back to `status="active"` if not found
- Alternatively, query without status filter and check `status in ("running", "active")` in Python

### 2. Improve error handling in steering check
- **Task ID**: build-error-handling
- **Depends On**: build-status-filter
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the broad `except Exception` at lines 832-835 with differentiated logging
- Log Redis/DB errors at ERROR level with traceback
- Log "no matching session" at DEBUG level (expected case for non-running threads)

### 3. Validate steering flow
- **Task ID**: validate-steering
- **Depends On**: build-error-handling
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the steering check code matches sessions in `running` status
- Verify `push_steering_message` is called with correct arguments
- Verify the PostToolUse hook in `agent/health_check.py` correctly consumes from the steering queue
- Verify the acknowledgment message is only sent after successful push

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-error-handling
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: false
- Add test for steering check matching `running` status sessions
- Add test for steering check matching `active` status sessions
- Add test for steering check falling through when no matching session
- Add test for error handling differentiation

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-steering
- **Assigned To**: steering-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/mid-session-steering.md` documenting the end-to-end steering flow
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python -m pytest tests/ -x -q` - Run all tests
- `python -m ruff check bridge/telegram_bridge.py agent/steering.py agent/health_check.py` - Lint changed files
- `grep -n 'status="active"' bridge/telegram_bridge.py` - Verify old status filter is gone from steering check
- `grep -n 'status="running"' bridge/telegram_bridge.py` - Verify new status filter exists
