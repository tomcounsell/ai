---
status: Complete
type: bug
appetite: Medium
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/216
---

# Agent Session Stall Detection and Lifecycle Diagnostics

## Problem

When agent sessions get stuck or stalled, there's no structured log trail showing what state the session was in, when it transitioned, or where it got stuck. The existing monitoring (session watchdog, job health monitor, bridge watchdog) detects symptoms after the fact but doesn't capture the lifecycle breadcrumbs needed to diagnose *why* a session stalled.

**Current behavior:**
- State transitions happen silently — `start_transcript()`, `complete_transcript()`, and job status changes don't emit structured lifecycle logs
- The session watchdog alerts on silence/loops/errors but the alert lacks context about the last known state transition
- The job health monitor recovers stuck jobs but doesn't log what state they were in when they stalled
- Diagnosing "why was this session stuck?" requires manual Redis inspection and log archaeology

**Desired outcome:**
- Every session state transition emits a structured log entry with session ID, old state, new state, timestamp, and context
- `tail -f logs/bridge.log` shows the full lifecycle of any session at a glance
- A health check can report stalled sessions with their last known state transition
- Stall detection integrates with the existing watchdog infrastructure

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1
- Review rounds: 1

## Prerequisites

No prerequisites — all changes are internal to existing bridge/monitoring code.

## Solution

### Key Elements

- **Lifecycle logger**: A thin logging layer that emits structured log entries at every AgentSession state transition
- **Stall detector**: Extension to the existing session watchdog that checks for sessions stuck in transitional states (pending, running) beyond configurable thresholds
- **Diagnostic report**: CLI command to show current session states and time-in-state

### Flow

Message arrives → `start_transcript()` logs `LIFECYCLE: pending→active` → job queue picks up → logs `LIFECYCLE: active→running` → agent executes → logs `LIFECYCLE: running→completed` (or `→failed`/`→dormant`)

If any transition doesn't happen within the expected window, the stall detector flags it.

### Technical Approach

#### Part 1: Structured Lifecycle Logging

Add a `log_lifecycle_transition()` function in `models/agent_session.py` that emits structured log entries. Call it from every place that changes `AgentSession.status`:

1. `bridge/session_transcript.py` — `start_transcript()` (→active), `complete_transcript()` (→completed/failed/dormant)
2. `agent/job_queue.py` — `_push_job()` (→pending), `_execute_job()` (→running), job completion (→completed/failed)
3. `monitoring/session_watchdog.py` — watchdog remediation (→failed/abandoned)

Log format:
```
LIFECYCLE session=tg_valor_123_456 transition=pending→running job_id=abc123 project=valor duration_in_prev_state=12.3s context="worker picked up job"
```

Also append to `AgentSession.history` for each transition, so the data is queryable from Redis.

#### Part 2: Stall Detection

Extend `monitoring/session_watchdog.py` with a new check:

- Query sessions with `status` in (`pending`, `running`, `active`)
- For each, check `time.time() - last_transition_time > STALL_THRESHOLD`
- Thresholds: pending > 5min, running > 45min (matches job health), active with no `last_activity` update > 10min
- When stalled: log warning with full diagnostic (session_id, status, duration, last history entry)
- Alert via existing Telegram mechanism with cooldown

#### Part 3: CLI Diagnostic

Add a `--sessions` flag to `agent/job_queue.py` CLI (or new `monitoring/session_status.py`) that shows:

```
SESSION STATUS REPORT
=====================
tg_valor_123_456  running  12m  project=valor  last_transition=running@10:45
tg_dm_789_012     pending   3m  project=dm     last_transition=pending@10:54
tg_valor_345_678  active   45m  project=valor  last_transition=active@10:12  ⚠️ STALLED
```

### Files to Modify

1. `models/agent_session.py` — Add `log_lifecycle_transition()` helper and `last_transition_at` field
2. `bridge/session_transcript.py` — Call lifecycle logger in `start_transcript()` and `complete_transcript()`
3. `agent/job_queue.py` — Call lifecycle logger in `_push_job()`, `_execute_job()`, and job completion
4. `monitoring/session_watchdog.py` — Add stall detection check to existing watchdog loop
5. `monitoring/session_status.py` — New CLI tool for session status report
6. `tests/test_session_lifecycle.py` — Integration tests for lifecycle logging and stall detection

## Rabbit Holes

- Building a separate monitoring dashboard — the CLI report is sufficient
- Replacing the existing session watchdog — extend it, don't rewrite it
- Adding metrics/Prometheus — structured logs are enough for now
- Persisting lifecycle logs to a separate store — bridge.log and AgentSession.history are sufficient

## Risks

### Risk 1: Log noise
**Impact:** Lifecycle logs could dominate bridge.log output
**Mitigation:** Use INFO level for transitions, DEBUG for details. Lifecycle entries are infrequent (a few per session).

### Risk 2: Performance impact of extra Redis writes
**Impact:** Each transition writes to AgentSession.history
**Mitigation:** History is already append-only with cap at 20 entries. One extra save per transition is negligible.

## No-Gos (Out of Scope)

- No new external dependencies
- No dashboard or web UI
- No changes to the bridge watchdog (process-level monitoring) — only session-level
- No changes to auto-continue or coaching loop logic
- Don't modify how sessions are created/completed — only add logging around those events

## Update System

No update system changes required — this is internal monitoring code with no new dependencies or config files.

## Agent Integration

No agent integration required — this is bridge-internal monitoring. The agent doesn't need to invoke stall detection tools.

## Documentation

- [ ] Create `docs/features/session-lifecycle-diagnostics.md` describing the lifecycle logging format, stall detection, and CLI usage
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/session-watchdog.md` to reference the new stall detection capability

## Success Criteria

- [ ] Every status transition on AgentSession emits a structured LIFECYCLE log entry
- [ ] `grep LIFECYCLE logs/bridge.log` shows the full lifecycle of any session
- [ ] Stall detection fires for sessions stuck > threshold in pending/running/active states
- [ ] CLI command shows current session states with time-in-state and stall warnings
- [ ] Existing session watchdog and job health monitor continue to work unchanged
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-logger)**
  - Name: lifecycle-logger
  - Role: Implement lifecycle logging in agent_session model and all callers
  - Agent Type: builder
  - Resume: true

- **Builder (stall-detector)**
  - Name: stall-detector
  - Role: Implement stall detection in session watchdog and CLI diagnostic
  - Agent Type: builder
  - Resume: true

- **Validator (monitoring-validator)**
  - Name: monitoring-validator
  - Role: Verify lifecycle logs appear correctly and stall detection works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add lifecycle logging to AgentSession model
- **Task ID**: build-lifecycle-model
- **Depends On**: none
- **Assigned To**: lifecycle-logger
- **Agent Type**: builder
- **Parallel**: false
- Add `last_transition_at` field to `AgentSession` (Field, type=float, null=True)
- Add `log_lifecycle_transition(old_status, new_status, context)` method
- Method should: (a) emit structured log at INFO level, (b) append to history, (c) update `last_transition_at`
- Update `complete_transcript()` delete-and-recreate to preserve `last_transition_at`

### 2. Instrument session transcript with lifecycle logging
- **Task ID**: build-instrument-transcript
- **Depends On**: build-lifecycle-model
- **Assigned To**: lifecycle-logger
- **Agent Type**: builder
- **Parallel**: false
- Call `log_lifecycle_transition()` in `start_transcript()` (None→active)
- Call `log_lifecycle_transition()` in `complete_transcript()` (current→final status)

### 3. Instrument job queue with lifecycle logging
- **Task ID**: build-instrument-jobqueue
- **Depends On**: build-lifecycle-model
- **Assigned To**: lifecycle-logger
- **Agent Type**: builder
- **Parallel**: true (with build-instrument-transcript)
- Call `log_lifecycle_transition()` in `_push_job()` (None→pending)
- Call `log_lifecycle_transition()` in `_execute_job()` (pending→running)
- Call `log_lifecycle_transition()` at job completion (running→completed/failed)

### 4. Add stall detection to session watchdog
- **Task ID**: build-stall-detector
- **Depends On**: build-lifecycle-model
- **Assigned To**: stall-detector
- **Agent Type**: builder
- **Parallel**: true (with build-instrument-transcript, build-instrument-jobqueue)
- Add `check_stalled_sessions()` function to `monitoring/session_watchdog.py`
- Query sessions in transitional states, compare `last_transition_at` against thresholds
- Integrate into existing `watchdog_loop()` cycle
- Use existing Telegram alert mechanism with cooldown

### 5. Create CLI session status report
- **Task ID**: build-cli-report
- **Depends On**: build-lifecycle-model
- **Assigned To**: stall-detector
- **Agent Type**: builder
- **Parallel**: false
- Create `monitoring/session_status.py` with `--report` flag
- Show all non-completed sessions with status, duration, last transition, stall warnings
- Also add `--sessions` flag to existing `agent/job_queue.py --status` output

### 6. Write integration tests
- **Task ID**: build-tests
- **Depends On**: build-instrument-transcript, build-instrument-jobqueue, build-stall-detector
- **Assigned To**: lifecycle-logger
- **Agent Type**: builder
- **Parallel**: false
- Test lifecycle log emission at each transition point
- Test stall detection with mocked timestamps
- Test CLI output format
- Test that existing watchdog and job health checks still work

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-cli-report
- **Assigned To**: monitoring-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify lifecycle logs appear in structured format
- Verify stall detection thresholds
- Check documentation completeness

## Validation Commands

- `grep "LIFECYCLE" tests/test_session_lifecycle.py` - Tests cover lifecycle logging
- `grep "log_lifecycle_transition" models/agent_session.py bridge/session_transcript.py agent/job_queue.py` - All callers instrumented
- `grep "check_stalled" monitoring/session_watchdog.py` - Stall detection integrated
- `python monitoring/session_status.py --report` - CLI report works
- `pytest tests/ -v --tb=short` - All tests pass
