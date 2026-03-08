---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-08
tracking: https://github.com/tomcounsell/ai/issues/305
---

# Stall Detection and Automatic Retry for Agent Sessions

## Problem

When a Claude Code agent session hangs or stalls mid-execution (e.g., the SDK subprocess stops producing output), the system currently detects the stall but only abandons the session or creates a GitHub issue. The user's work is lost and they must manually re-trigger it.

**Current behavior:**
- `session_watchdog.py` detects stalled sessions (pending >5m, running >45m, active >10m) and logs `LIFECYCLE_STALL` warnings
- `fix_unhealthy_session()` marks stalled sessions as `abandoned` -- but does not retry them
- `_job_health_check()` in `job_queue.py` recovers jobs with dead workers by resetting to `pending`, but this is a crash recovery mechanism, not a retry with context
- The user sees no output and must notice the silence, then re-send their request

**Desired outcome:**
- Stalled sessions are automatically retried with exponential backoff
- Each retry carries context about what was attempted and why it stalled
- After max retries, the human is notified with diagnostics
- Stall timeout is configurable via `.env`
- The stalled process is killed before retry to prevent resource leaks

## Prior Art

- **Issue #216 / Plan `session_stall_diagnostics.md`**: Implemented lifecycle logging and stall detection (status: Complete). Added `check_stalled_sessions()`, `LIFECYCLE_STALL` log entries, and the `session_status.py` CLI. This is the foundation -- issue #305 extends detection into action.
- **`_job_health_check()` in `job_queue.py`**: Existing liveness monitor that recovers jobs with dead workers or exceeded timeouts. Uses delete-and-recreate as pending. Does not carry retry context or implement backoff.
- **`_recover_interrupted_jobs()` in `job_queue.py`**: Startup-time recovery for jobs stuck in `running` status after a crash. Resets to pending with high priority.
- **OpenAI Symphony**: Uses 5-minute default stall timeout with event-timestamp tracking and automatic retry. Inspiration cited in the issue.

## Data Flow

1. **Entry point**: Agent session is executing via `_execute_job()` in `job_queue.py`
2. **Activity tracking**: `ValorAgent.query()` updates `AgentSession.last_activity` via hooks
3. **Stall detection**: `session_watchdog.check_stalled_sessions()` runs every 5 minutes, identifies sessions exceeding status-specific thresholds
4. **Current termination**: `fix_unhealthy_session()` marks session as `abandoned`
5. **New retry path**: Instead of abandoning, the stall handler will:
   a. Kill the stalled SDK subprocess (via the pid tracked on the AgentSession)
   b. Compute backoff delay: `min(10s * 2^(attempt-1), max_backoff)`
   c. Re-enqueue the job as pending with retry context and incremented retry count
   d. After max retries exhausted, notify human via Telegram and mark as `failed`

## Architectural Impact

- **New fields on AgentSession**: `retry_count` (int), `stall_pid` (int, for process kill), `last_stall_reason` (str)
- **New dependency**: None -- uses existing `os.kill()` and `asyncio.sleep()` for backoff
- **Interface changes**: `fix_unhealthy_session()` gains retry path alongside abandon path
- **Coupling**: Extends existing watchdog → job_queue coupling (already present via recovery)
- **Reversibility**: Easy -- remove the retry logic and the watchdog falls back to abandonment

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on retry vs. abandon decision)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work extends existing internal monitoring code with no new external dependencies.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| None | N/A | No external dependencies |

## Solution

### Key Elements

- **Stall retry handler**: Extension to `fix_unhealthy_session()` that retries stalled sessions instead of abandoning them immediately
- **Process killer**: Kills the stalled SDK subprocess by PID before retrying, preventing zombie processes
- **Backoff calculator**: `min(10s * 2^(attempt-1), max_backoff)` with configurable max_backoff (default 5 minutes)
- **Retry context builder**: Carries forward what was attempted and why it stalled into the retry message
- **Failure notifier**: After max retries, sends Telegram notification with diagnostics

### Flow

**Session stalls** → Watchdog detects stall → Kill stalled process → Check retry count → (under max) Compute backoff → Sleep → Re-enqueue with context → Worker picks up retry job → Agent continues

**Session stalls** → Watchdog detects stall → Kill stalled process → Check retry count → (at max) Notify human → Mark as failed

### Technical Approach

#### 1. New AgentSession fields

Add to `models/agent_session.py`:
- `retry_count` (Field, type=int, default=0) -- how many times this session has been retried
- `last_stall_reason` (Field, type=str, null=True) -- diagnostic context from last stall

#### 2. Stall retry in session watchdog

Modify `fix_unhealthy_session()` in `monitoring/session_watchdog.py`:
- Before abandoning, check `session.retry_count < MAX_STALL_RETRIES` (default 3)
- If retries remain: kill the stalled process, compute backoff, re-enqueue via `_enqueue_stall_retry()`
- If retries exhausted: abandon as before, plus send Telegram notification

New function `_enqueue_stall_retry()`:
- Uses the existing delete-and-recreate pattern from `_enqueue_continuation()` in `job_queue.py`
- Increments `retry_count` on the session
- Sets `message_text` to a retry context message explaining what happened
- Sets `priority` to "high"
- Calls `_ensure_worker()` to restart the worker

#### 3. Process cleanup

Add `_kill_stalled_process()` function:
- Look up the worker task for the session's project from `_active_workers`
- Cancel the asyncio task (which should kill the SDK subprocess)
- Wait briefly for cleanup
- If the subprocess PID is known, send SIGKILL as fallback

#### 4. Backoff delay

Implement in `_compute_stall_backoff()`:
- Formula: `delay = min(STALL_BACKOFF_BASE * 2^(retry_count), STALL_BACKOFF_MAX)`
- `STALL_BACKOFF_BASE` = 10 seconds (configurable via `STALL_BACKOFF_BASE_SECONDS` env var)
- `STALL_BACKOFF_MAX` = 300 seconds / 5 minutes (configurable via `STALL_BACKOFF_MAX_SECONDS` env var)
- Progression: 10s, 20s, 40s (then capped at 5m for any further attempts)

#### 5. Configuration via .env

New environment variables:
- `STALL_MAX_RETRIES` (default: 3)
- `STALL_BACKOFF_BASE_SECONDS` (default: 10)
- `STALL_BACKOFF_MAX_SECONDS` (default: 300)
- `STALL_TIMEOUT_SECONDS` (default: 600 for active sessions -- overrides existing hardcoded thresholds)

#### 6. Telegram notification on final failure

Use the existing `send_telegram_alert()` pattern from `bridge_watchdog.py` or route through the bridge's send callback to notify when retries are exhausted.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_kill_stalled_process()` handles `ProcessLookupError` (process already dead) gracefully
- [ ] `_enqueue_stall_retry()` handles Redis connection failures -- falls back to abandon
- [ ] Backoff computation handles retry_count=0 and very large values without overflow

### Empty/Invalid Input Handling
- [ ] Sessions with `retry_count=None` (legacy) are treated as `retry_count=0`
- [ ] Sessions with missing `last_activity` use `created_at` as fallback (already handled in existing code)
- [ ] Empty `last_stall_reason` does not break retry context message

### Error State Rendering
- [ ] Final failure Telegram notification includes session ID, retry count, and last stall reason
- [ ] Stall retry log messages are clearly distinguishable from crash recovery messages

## Rabbit Holes

- **Building a separate retry queue**: The existing job queue is sufficient -- just re-enqueue with retry context
- **Tracking individual subprocess PIDs**: The asyncio task cancellation is enough; tracking PIDs adds complexity with minimal benefit
- **Implementing jitter in backoff**: Pure exponential is fine for the expected retry counts (max 3); jitter matters at scale
- **Making the retry message carry full conversation history**: The session continuation mechanism already handles this via `continue_conversation=True`
- **Modifying the SDK wrapper to detect stalls in real-time**: The 5-minute watchdog interval is sufficient; real-time detection would require substantial refactoring

## Risks

### Risk 1: Infinite retry loops
**Impact:** A session that always stalls (e.g., hitting a deterministic SDK bug) would retry endlessly
**Mitigation:** Hard cap at `MAX_STALL_RETRIES` (default 3). After exhaustion, abandon + notify human.

### Risk 2: Race between watchdog and job health monitor
**Impact:** Both `_job_health_check()` and the stall retry handler could try to recover the same session simultaneously
**Mitigation:** The watchdog operates on `active` sessions; the job health monitor operates on `running` sessions with dead workers. Different session statuses prevent overlap. Add a `retry_in_progress` guard as defensive safety.

### Risk 3: Killing a subprocess that's actually making progress
**Impact:** A long-running but healthy agent session gets killed because its `last_activity` wasn't updated recently enough
**Mitigation:** Use the existing `last_activity` timestamp which is updated on every tool use. The 10-minute threshold for active sessions is generous. Build jobs already have a 2.5-hour timeout.

## Race Conditions

### Race 1: Concurrent watchdog and worker access to session
**Location:** `monitoring/session_watchdog.py` and `agent/job_queue.py`
**Trigger:** Watchdog reads session status while worker is in the middle of transitioning it
**Data prerequisite:** Session must be in a transitional state (active/running)
**State prerequisite:** Both watchdog and worker must be accessing the same session
**Mitigation:** The watchdog uses the existing `_safe_abandon_session()` pattern with ModelException catch for concurrent modification. Retry re-enqueue uses delete-and-recreate which is atomic at the Redis level.

### Race 2: Backoff sleep vs. bridge restart
**Location:** `monitoring/session_watchdog.py` backoff sleep
**Trigger:** Bridge restarts during backoff delay
**Data prerequisite:** A retry is pending with backoff delay
**State prerequisite:** Bridge is restarting while backoff sleep is active
**Mitigation:** The backoff delay is applied via `asyncio.sleep()` in the watchdog loop. If the bridge restarts, `_recover_interrupted_jobs()` at startup will pick up any session left in `running` status. The retry context is persisted on the AgentSession before the sleep.

## No-Gos (Out of Scope)

- No changes to the auto-continue or coaching loop logic
- No new external monitoring dependencies (Prometheus, Datadog, etc.)
- No changes to the bridge watchdog's process-level monitoring
- No retry of user-initiated cancellations (only stall-detected retries)
- No modification to how `ValorAgent.query()` works internally
- No real-time stall detection within the SDK query loop -- watchdog interval is sufficient

## Update System

No update system changes required -- this feature adds internal monitoring behavior with no new dependencies or config files that need to be propagated. The `.env` variables are optional and have safe defaults.

## Agent Integration

No agent integration required -- stall detection and retry is bridge-internal infrastructure. The agent does not need to invoke stall detection tools or be aware of retry mechanics.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/stall-retry.md` describing the stall detection, retry flow, backoff formula, and configuration
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on backoff calculation and retry decision logic
- [ ] Updated docstrings for modified functions (`fix_unhealthy_session`, new retry functions)

## Success Criteria

- [ ] Stalled sessions are detected within the configured timeout
- [ ] Stalled sessions are automatically retried up to `MAX_STALL_RETRIES` times
- [ ] Each retry waits with exponential backoff before re-enqueueing
- [ ] Retry context (what was attempted, why it stalled) is carried forward
- [ ] The stalled process is killed before retry to prevent resource leaks
- [ ] After max retries, human is notified via Telegram with session diagnostics
- [ ] Stall timeout is configurable via `.env` (`STALL_TIMEOUT_SECONDS`)
- [ ] Max retries configurable via `.env` (`STALL_MAX_RETRIES`)
- [ ] Backoff base and max configurable via `.env`
- [ ] Existing session watchdog and job health monitor continue to work unchanged
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (stall-retry)**
  - Name: stall-retry-builder
  - Role: Implement stall retry logic in watchdog and job queue
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write unit and integration tests for stall retry
  - Agent Type: test-engineer
  - Resume: true

- **Validator (stall-retry-validator)**
  - Name: stall-retry-validator
  - Role: Verify retry behavior, backoff timing, and failure notification
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add retry fields to AgentSession model
- **Task ID**: build-model-fields
- **Depends On**: none
- **Assigned To**: stall-retry-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `retry_count` field (Field, type=int, default=0) to `models/agent_session.py`
- Add `last_stall_reason` field (Field, type=str, null=True)
- Add fields to `_JOB_FIELDS` list in `agent/job_queue.py` for delete-and-recreate preservation

### 2. Implement backoff calculator and configuration
- **Task ID**: build-backoff
- **Depends On**: none
- **Assigned To**: stall-retry-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `_compute_stall_backoff(retry_count)` function in `monitoring/session_watchdog.py`
- Load configuration from env vars with defaults: `STALL_MAX_RETRIES=3`, `STALL_BACKOFF_BASE_SECONDS=10`, `STALL_BACKOFF_MAX_SECONDS=300`
- Add `STALL_TIMEOUT_SECONDS` env var to override `STALL_THRESHOLD_ACTIVE`

### 3. Implement stall retry in session watchdog
- **Task ID**: build-stall-retry
- **Depends On**: build-model-fields, build-backoff
- **Assigned To**: stall-retry-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `fix_unhealthy_session()` to check `retry_count < MAX_STALL_RETRIES` before abandoning
- Add `_enqueue_stall_retry()` function using delete-and-recreate pattern
- Build retry context message with stall reason and attempt number
- Kill the worker task for the stalled session's project before re-enqueueing
- After max retries: abandon + send Telegram notification via existing alert mechanism

### 4. Implement process cleanup
- **Task ID**: build-process-cleanup
- **Depends On**: build-stall-retry
- **Assigned To**: stall-retry-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_kill_stalled_worker(project_key)` function
- Cancel the asyncio task from `_active_workers` dict in `job_queue.py`
- Handle `ProcessLookupError` and other cleanup errors gracefully
- Wait briefly for cleanup before re-enqueueing

### 5. Write tests
- **Task ID**: build-tests
- **Depends On**: build-stall-retry, build-process-cleanup
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test backoff calculation: `_compute_stall_backoff(0)=10`, `_compute_stall_backoff(1)=20`, `_compute_stall_backoff(2)=40`, capped at max
- Test retry decision: `retry_count < max` retries, `retry_count >= max` abandons
- Test retry context message includes attempt number and stall reason
- Test configuration from env vars with defaults
- Test that `retry_count=None` (legacy session) is treated as 0
- Test final failure notification is triggered after max retries
- Test existing stall detection tests still pass unchanged

### 6. Validate existing behavior preserved
- **Task ID**: validate-existing
- **Depends On**: build-tests
- **Assigned To**: stall-retry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite to verify no regressions
- Verify existing `check_stalled_sessions()` tests pass
- Verify existing `_job_health_check()` tests pass
- Verify session watchdog loop still functions correctly
- Check that non-stall session abandonment still works (looping, error cascade)

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-existing
- **Assigned To**: stall-retry-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/stall-retry.md`
- Add entry to `docs/features/README.md` index table
- Update inline docstrings for modified functions

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: stall-retry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep "retry_count" models/agent_session.py` - Model fields added
- `grep "_compute_stall_backoff" monitoring/session_watchdog.py` - Backoff implemented
- `grep "_enqueue_stall_retry" monitoring/session_watchdog.py` - Retry logic implemented
- `grep "STALL_MAX_RETRIES" monitoring/session_watchdog.py` - Configuration loaded
- `grep "retry" tests/unit/test_stall_detection.py` - Tests cover retry behavior
- `pytest tests/unit/test_stall_detection.py -v --tb=short` - Stall tests pass
- `pytest tests/ -v --tb=short` - Full test suite passes

---

## Open Questions

1. Should retried sessions continue the same Claude Code conversation (via `continue_conversation=True`), or start fresh? Continuing preserves context but may reproduce the same stall condition. Starting fresh loses context but avoids deterministic hangs.
2. Should the backoff delay block the watchdog loop (preventing other health checks during the wait), or should we schedule the retry via a separate asyncio task?
3. The existing `_job_health_check()` already recovers jobs with dead workers. Should stall retry integrate with that mechanism (extend it) or operate independently via the session watchdog? They monitor different status categories (running vs. active) but the recovery actions overlap.
