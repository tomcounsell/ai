---
status: Planning
type: feature
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/44
---

# Background Session Watchdog

## Problem

Agent sessions can become unhealthy without being detected or recovered. The current health check system only triggers when sessions actively make tool calls, and when it does detect a problem it kills the session with no recovery attempt.

**Current behavior:**
- Health check fires every 20 tool calls via PostToolUse hook
- Sessions that go silent (stop making tool calls) are never checked
- When unhealthy is detected, the session is immediately killed
- No structured logging for session health events (daydream system has nothing to review)
- No automatic recovery - failed sessions stay failed
- Sessions can run for 9+ hours stuck in loops before manual intervention

**Real examples from Feb 4-5:**
- Popoto session ran for 9+ hours stuck exploring without converging. User had to manually ask "PR?" to discover nothing shipped.
- Valor AI group: 3 jobs queued but workers never started. No response, no error, no recovery.
- Yudame Research: Project not in ACTIVE_PROJECTS - messages stored but never processed. No alert.

**Desired outcome:**
- All active sessions monitored every 5 minutes regardless of tool activity
- Silent sessions detected and revived automatically
- Unhealthy patterns logged to structured critical log (consumed by daydream system)
- GitHub issues created automatically for investigation
- Sessions revived where possible; Telegram failure message sent when unrecoverable

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Solution

### Key Elements

- **Background Watchdog Task**: Async loop running every 5 minutes alongside the bridge
- **Session Health Assessment**: Pattern-based detection for silence and error cascades
- **Critical Event Log**: Structured JSONL log for session health events, shared with daydream system
- **Self-Healing Actions**: Automatic revival of stuck/failed sessions, GitHub issue creation for investigation
- **Graceful Failure**: Telegram message to originating chat when recovery fails

### Flow

**Bridge starts** → Watchdog task spawned → **Every 5 minutes** → Query active sessions from Redis → Assess each session health → **If healthy** → Log OK, continue → **If unhealthy** → Log to critical events → Create GitHub issue → Attempt revival → (If revival fails) Send failure message to chat → **Continue monitoring**

### Technical Approach

- New module: `monitoring/session_watchdog.py`
- New structured log: `logs/critical_events.jsonl` (shared with daydream)
- Runs as `asyncio.create_task()` during bridge startup
- Queries `AgentSession` model in Redis for `status="active"` sessions
- Reads `tool_use.jsonl` timestamps for silence detection
- Revival uses existing job queue `enqueue_job()` mechanism

**Detection heuristics:**

1. **Silence detection**: Check `tool_use.jsonl` timestamps. Healthy sessions have median gap of ~2s between tool calls with occasional gaps up to 6 min. A gap >10 minutes with no new tool call entries = silent/stuck. Based on analysis of 22 sessions:
   - Healthy session: median 0.5s gap, max 6 min
   - Stuck session: 10+ minute gaps mid-execution

2. **Error cascade**: >5 consecutive error results in tool_use.jsonl

3. **Runaway duration**: Session active for >2 hours with no completion signal

4. **Loop detection**: Deferred to future issue (insufficient session log history for reliable pattern analysis - see Open Questions)

**Critical event log format (`logs/critical_events.jsonl`):**
```json
{
  "timestamp": "2026-02-05T10:00:00Z",
  "event": "session_unhealthy",
  "session_id": "tg_popoto_-5294327191_4469",
  "project_key": "popoto",
  "reason": "silent_for_15_minutes",
  "action_taken": "revival_attempted",
  "github_issue": "https://github.com/tomcounsell/ai/issues/45",
  "details": {"last_tool_call": "2026-02-05T09:45:00Z", "tool_count": 40}
}
```

This log is the primary input for the daydream system's "Review Previous Day's Logs" step.

### Self-Healing Actions

When an unhealthy session is detected:

1. **Log critical event** to `logs/critical_events.jsonl`
2. **Create GitHub issue** via `gh issue create` with:
   - Session ID, project, duration
   - Last N tool calls from tool_use.jsonl
   - Specific health failure reason
3. **Attempt revival**:
   - Mark the current session as failed in Redis
   - Re-enqueue the original message via `enqueue_job()` with revival context
   - Include the GitHub issue URL in the revival context
4. **On revival failure** (second failure for same message):
   - Send Telegram message to originating chat: "Sorry, I ran into an issue and couldn't recover. The error has been logged for investigation."
   - Uses existing pattern from `agent/sdk_client.py:655`

## Risks

### Risk 1: Revival loops
**Impact:** Watchdog detects unhealthy session, revives it, new session also fails, infinite revival loop
**Mitigation:** Track revival attempts per message. Max 1 revival. Second failure = send failure message, stop.

### Risk 2: Watchdog interfering with sessions
**Impact:** Race conditions with session file I/O
**Mitigation:** Read-only access to tool_use.jsonl. Never write to session files or interrupt SDK client.

### Risk 3: GitHub issue spam
**Impact:** Dozens of issues for transient problems
**Mitigation:** Cooldown per project (max 1 issue per project per hour). De-duplicate by checking existing open issues with `[Watchdog]` prefix.

### Risk 4: Missing tool_use.jsonl files
**Impact:** Can't assess session health for sessions without log files
**Mitigation:** Fall back to Redis `last_activity` timestamp. Skip sessions without any readable data source.

### Risk 5: Stale AgentSession records in Redis
**Impact:** Watchdog tries to check sessions that already completed but weren't cleaned up
**Mitigation:** Cross-reference with running job queue. If no running job matches the session, mark it as stale and skip.

## No-Gos (Out of Scope)

- AI-based health judgment (using pattern matching, not Haiku calls)
- Supervisor alerts via Telegram DM (system handles internally)
- Loop detection heuristics (deferred - need more session log data)
- Changing existing PostToolUse health check behavior
- Force-stop capability (monitor and revive only)
- Historical health analytics dashboards

## Update System

No update system changes required — this feature is purely internal to the bridge process.

- **Dependencies**: No new pip dependencies. The watchdog uses only standard library modules (`asyncio`, `json`, `logging`, `time`, `pathlib`) plus the existing `models.sessions.AgentSession` model.
- **Config files**: No new configuration files. Thresholds are module-level constants in `monitoring/session_watchdog.py`.
- **Service restarts**: The watchdog starts automatically as an `asyncio.create_task()` when the bridge boots. The existing update system already restarts the bridge via `scripts/update/service.py`, so the watchdog will start on the next restart with no additional changes.
- **Migration**: No database changes, no new environment variables, no symlinks. Existing installations will pick up the watchdog on their next `git pull` + bridge restart.

## Success Criteria

- [ ] Watchdog runs every 5 minutes when bridge is active
- [ ] All sessions with `status="active"` in Redis are checked
- [ ] Silent sessions (>10 min no tool activity) detected
- [ ] Critical events written to `logs/critical_events.jsonl` in structured format
- [ ] GitHub issue created automatically for unhealthy sessions with investigation details
- [ ] Failed sessions revived (re-enqueued) with max 1 retry
- [ ] Unrecoverable failures send existing failure message to originating chat
- [ ] No impact on normal session execution (read-only monitoring)
- [ ] Graceful handling of missing/corrupted log files
- [ ] Daydream system can consume critical_events.jsonl for daily review

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (watchdog-core)**
  - Name: watchdog-builder
  - Role: Implement watchdog module, health assessment, and critical event logging
  - Agent Type: builder
  - Resume: true

- **Builder (self-healing)**
  - Name: healing-builder
  - Role: Implement revival logic, GitHub issue creation, and failure messaging
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-integration)**
  - Name: bridge-integrator
  - Role: Integrate watchdog startup/shutdown into bridge lifecycle
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog)**
  - Name: watchdog-validator
  - Role: Verify watchdog detects scenarios and self-heals correctly
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-writer
  - Role: Write unit tests for detection and recovery functions
  - Agent Type: test-engineer
  - Resume: true

## Step by Step Tasks

### 1. Create Critical Event Logging
- **Task ID**: build-critical-log
- **Depends On**: none
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `monitoring/critical_events.py`
- Implement `log_critical_event()` that appends structured JSON to `logs/critical_events.jsonl`
- Include fields: timestamp, event type, session_id, project_key, reason, action_taken, details
- Ensure atomic writes (write to temp, rename)
- Implement `read_recent_events()` for daydream consumption

### 2. Create Watchdog Module
- **Task ID**: build-watchdog
- **Depends On**: build-critical-log
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `monitoring/session_watchdog.py`
- Implement `watchdog_loop()` async function with 5-minute interval (WATCHDOG_INTERVAL = 300)
- Implement `check_all_sessions()` to query `AgentSession.query.filter(status="active")`
- Implement `assess_session_health()` with silence detection (SILENCE_THRESHOLD = 600s)
- Implement `read_recent_tool_calls()` to parse tool_use.jsonl timestamps
- Implement error cascade detection (>5 consecutive errors)
- Implement runaway duration detection (>2 hours)
- Add configurable thresholds as module constants

### 3. Implement Self-Healing
- **Task ID**: build-healing
- **Depends On**: build-watchdog, build-critical-log
- **Assigned To**: healing-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `create_investigation_issue()` using `gh issue create` with `[Watchdog]` prefix
- Implement issue de-duplication (check for existing open `[Watchdog]` issues per project)
- Implement per-project cooldown for issue creation (1 per hour max)
- Implement `attempt_revival()` that re-enqueues original message via `enqueue_job()`
- Track revival attempts per message_id (max 1 revival per message)
- On second failure, use existing failure message pattern from `sdk_client.py:655`
- Store revival tracking in Redis (simple key with TTL)

### 4. Integrate with Bridge
- **Task ID**: build-integration
- **Depends On**: build-watchdog, build-healing
- **Assigned To**: bridge-integrator
- **Agent Type**: builder
- **Parallel**: false
- Add `asyncio.create_task(watchdog_loop(...))` in bridge startup after all project queues registered
- Pass references: Telegram client (for failure messages), send callbacks
- Cancel watchdog task in `_graceful_shutdown()`
- Ensure watchdog survives individual session failures (catch + log per-session)

### 5. Write Unit Tests
- **Task ID**: build-tests
- **Depends On**: build-watchdog, build-healing, build-critical-log
- **Assigned To**: test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Test silence detection with mock timestamps at various gaps
- Test error cascade detection with sample tool_use patterns
- Test runaway duration detection
- Test critical event log writing and reading
- Test revival attempt tracking (max 1 retry)
- Test graceful handling of missing files
- Test issue de-duplication logic

### 6. Validate Implementation
- **Task ID**: validate-all
- **Depends On**: build-integration, build-tests
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify module imports cleanly
- Run full test suite
- Check that critical_events.jsonl format matches spec
- Verify watchdog integrates with bridge startup/shutdown
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/unit/test_session_watchdog.py -v` - Unit tests pass
- `pytest tests/unit/test_critical_events.py -v` - Critical event logging tests pass
- `python -c "from monitoring.session_watchdog import watchdog_loop; print('OK')"` - Watchdog module imports
- `python -c "from monitoring.critical_events import log_critical_event; print('OK')"` - Critical events module imports
- `grep "watchdog" logs/bridge.log | tail -20` - Watchdog logs present after startup

---

## Open Questions

1. **Loop detection data gap**: We have 22 session logs. Subagent-heavy sessions (Task tool spawning many similar agents) can look like loops. We need more data to build reliable heuristics. **Proposed**: Create a separate GitHub issue to build a session log analysis tool that runs against accumulated logs in the future and produces loop detection rules.

2. **Critical event retention**: How long should `critical_events.jsonl` be retained? Should daydream rotate/archive after processing? Propose: 30-day retention with daydream archiving processed events.

3. **Revival context depth**: When re-enqueuing a failed message, how much context to include? Just the original message, or also the error details and GitHub issue link?
