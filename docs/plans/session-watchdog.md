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

Agent sessions can become unhealthy without being detected or terminated. The current health check system only triggers when sessions actively make tool calls, missing two critical failure modes:

**Current behavior:**
- Health check fires every 20 tool calls via PostToolUse hook
- Sessions that go silent (stop making tool calls) are never checked
- When unhealthy is detected, the session is immediately killed
- No visibility into session health across the system
- Sessions can run for 9+ hours stuck in loops before manual intervention

**Real example from Feb 4-5:**
- Popoto session ran for 9+ hours stuck exploring without converging
- Health check eventually flagged it as "stuck in a loop" but only after 40+ tool calls
- User had to manually ask "PR?" to discover nothing had shipped

**Desired outcome:**
- All active sessions monitored every 5 minutes regardless of tool activity
- Silent sessions detected (no activity for N minutes)
- Loop patterns detected without killing the session
- Supervisor notified of concerning patterns
- Sessions continue running (non-intrusive monitoring)

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Solution

### Key Elements

- **Background Watchdog Task**: Async loop running every 5 minutes alongside the bridge
- **Session Health Assessment**: Pattern-based detection (silence, loops, errors) without AI judge calls
- **Alert System**: Log warnings and optionally notify supervisor via Telegram
- **Health Dashboard Data**: Queryable health status for each session

### Flow

**Bridge starts** → Watchdog task spawned → **Every 5 minutes** → Query active sessions → Assess each session → **Log health status** → (If unhealthy) Send alert → **Continue monitoring**

### Technical Approach

- New module: `monitoring/session_watchdog.py`
- Runs as `asyncio.create_task()` during bridge startup
- Queries `AgentSession` model in Redis for `status="active"` sessions
- Reads `tool_use.jsonl` for recent activity patterns
- Pattern detection via simple heuristics (no Haiku calls = faster, cheaper)
- Integrates with existing Telegram client for supervisor alerts

**Detection heuristics:**
1. **Silence**: `time.time() - last_activity > SILENCE_THRESHOLD`
2. **Looping**: Repeated tool+input patterns in last N calls
3. **Error cascade**: >N errors in last M calls
4. **Runaway duration**: Session running longer than appetite threshold

## Risks

### Risk 1: False positives overwhelming supervisor
**Impact:** Alert fatigue, supervisor ignores real problems
**Mitigation:** Conservative thresholds, cooldown between alerts per session, severity levels

### Risk 2: Watchdog interfering with sessions
**Impact:** Race conditions, corrupted state
**Mitigation:** Read-only access only - never write to session files or interrupt SDK client

### Risk 3: Redis query overhead
**Impact:** Slowing down job queue operations
**Mitigation:** Single bulk query every 5 minutes is negligible; avoid per-session queries

### Risk 4: Missing transcript/tool_use files
**Impact:** Can't assess session health
**Mitigation:** Graceful degradation - skip sessions without readable logs, log warning

## No-Gos (Out of Scope)

- Automatic session termination (monitoring only, not enforcement)
- AI-based health judgment (using pattern matching, not Haiku calls)
- Historical health analytics or dashboards
- Changing existing PostToolUse health check behavior
- Integration with external monitoring systems (Sentry, etc.)

## Update System

No update system changes required — this feature is purely internal to the bridge process.

- **Dependencies**: No new pip dependencies. Uses only standard library modules (`asyncio`, `json`, `logging`, `time`, `pathlib`) plus the existing `models.sessions.AgentSession` model.
- **Config files**: No new configuration files. Thresholds are module-level constants in `monitoring/session_watchdog.py`.
- **Service restarts**: The watchdog starts automatically as an `asyncio.create_task()` when the bridge boots. The existing update system already restarts the bridge via `scripts/update/service.py`, so the watchdog starts on the next restart with no additional changes.
- **Migration**: No database changes, no new environment variables, no symlinks. Existing installations pick up the watchdog on their next `git pull` + bridge restart.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/session-watchdog.md` describing the watchdog system, thresholds, alert behavior, and how to tune constants
- [ ] Add entry to documentation index

### Inline Documentation
- [ ] Module docstring in `monitoring/session_watchdog.py` covering purpose, detection heuristics, and configuration
- [ ] Code comments on non-obvious logic (loop detection algorithm, alert cooldown)

No external documentation site changes needed — this repo does not use Sphinx/Read the Docs.

## Success Criteria

- [ ] Watchdog runs every 5 minutes when bridge is active
- [ ] All sessions with `status="active"` are checked
- [ ] Silent sessions (>10 min no activity) logged as warning
- [ ] Looping patterns (>5 identical tool calls) detected and logged
- [ ] Supervisor receives Telegram alert for unhealthy sessions
- [ ] No impact on normal session execution (read-only monitoring)
- [ ] Graceful handling of missing/corrupted log files
- [ ] Documentation updated and indexed

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (watchdog-core)**
  - Name: watchdog-builder
  - Role: Implement the session watchdog module and pattern detection
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-integration)**
  - Name: bridge-integrator
  - Role: Integrate watchdog startup into bridge lifecycle
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog)**
  - Name: watchdog-validator
  - Role: Verify watchdog detects test scenarios correctly
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-writer
  - Role: Write unit tests for pattern detection functions
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: watchdog-docs
  - Role: Create feature documentation and verify inline docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create Watchdog Module
- **Task ID**: build-watchdog
- **Depends On**: none
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `monitoring/session_watchdog.py`
- Implement `watchdog_loop()` async function with 5-minute interval
- Implement `check_all_sessions()` to query active sessions
- Implement `assess_session_health()` with pattern detection
- Implement `read_recent_tool_calls()` to parse tool_use.jsonl
- Implement `detect_repetition()` for loop detection
- Add configurable thresholds as module constants

### 2. Implement Alert System
- **Task ID**: build-alerts
- **Depends On**: build-watchdog
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `send_health_alert()` function using Telegram client
- Implement alert cooldown per session (don't spam)
- Format alerts with session ID, project, duration, and reason

### 3. Integrate with Bridge
- **Task ID**: build-integration
- **Depends On**: build-watchdog
- **Assigned To**: bridge-integrator
- **Agent Type**: builder
- **Parallel**: false
- Add watchdog startup in `telegram_bridge.py` main()
- Pass Telegram client reference for alerts
- Ensure watchdog stops cleanly on bridge shutdown

### 4. Write Unit Tests
- **Task ID**: build-tests
- **Depends On**: build-watchdog
- **Assigned To**: test-writer
- **Agent Type**: test-engineer
- **Parallel**: true
- Test silence detection with mock timestamps
- Test loop detection with sample tool_use patterns
- Test error cascade detection
- Test graceful handling of missing files

### 5. Validate Implementation
- **Task ID**: validate-watchdog
- **Depends On**: build-watchdog, build-alerts, build-integration, build-tests
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify watchdog starts with bridge
- Verify active sessions are queried
- Verify logs show health check results
- Run unit tests and confirm passing

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-watchdog
- **Assigned To**: watchdog-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/session-watchdog.md` covering purpose, thresholds, alert behavior, tuning
- Add entry to documentation index
- Verify inline docs in `monitoring/session_watchdog.py` are complete

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-watchdog, document-feature
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/unit/test_session_watchdog.py -v` - Unit tests pass
- `grep "watchdog" logs/bridge.log | tail -20` - Watchdog logs present
- `python -c "from monitoring.session_watchdog import watchdog_loop; print('OK')"` - Module imports

---

## Open Questions

1. **Alert destination**: Should alerts go to a specific chat (e.g., DM to Tom) or to the same chat where the session originated?

2. **Silence threshold**: 10 minutes seems reasonable - is this too aggressive or too lenient?

3. **Loop detection sensitivity**: Should we detect 3 identical calls, 5, or more? Lower = more false positives, higher = slower detection.

4. **Supervisor intervention**: When an unhealthy session is detected, should there be a way for the supervisor to force-stop it via reply? (Would add complexity but increases control.)

5. **Duration threshold**: Should we alert if a session runs longer than a certain time (e.g., 2 hours) regardless of activity pattern?
