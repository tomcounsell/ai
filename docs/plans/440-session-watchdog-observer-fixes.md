---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-18
tracking: https://github.com/tomcounsell/ai/issues/440
last_comment_id:
---

# Session Watchdog and Observer Reliability Fixes

## Problem

Three interrelated failure modes are degrading session reliability and producing noisy error logs. They interact in a cascade: SDK timeouts trigger observer decisions, observer failures cascade into stalled sessions, and the watchdog's recovery path crashes on malformed session keys.

**Current behavior:**
1. Stalled `push-*` sessions trigger `AttributeError: 'str' object has no attribute 'redis_key'` every 5 minutes indefinitely — the watchdog never recovers or cleans them up.
2. SDLC jobs hit the 600s SDK timeout (16 occurrences on March 17), killing in-progress work and wasting compute on timeout→steer→timeout cycles. Hard absolute timeouts are the wrong model — the 5-minute heartbeat health checker should detect stalls based on *inactivity* (time since last tool call or log output), not wall-clock duration.
3. Observer import crashes (`load_principal_context` ImportError) go unhandled with no circuit breaker — the observer retries forever with the same error.

**Desired outcome:**
- `push-*` stalled sessions are recovered or cleaned without crashes
- SDK timeout replaced with activity-based stall detection: the existing 5-minute heartbeat checks time since last tool call or log text, not absolute phase duration
- Observer has a circuit breaker with escalating backoff for API/outage errors, retrying automatically when possible — only escalating to human when human action can actually help
- Observer import errors are caught gracefully with fallback behavior

## Prior Art

- **Issue #402**: "Watchdog stall recovery for pending sessions never kills stuck worker" — Fixed the stall recovery to actually kill workers, but didn't handle `push-*` sessions with string `project_key` values.
- **PR #343**: "Fix _compute_stall_backoff TypeError with Popoto Field objects" — Similar type mismatch bug with Popoto fields. Fixed by coercing to `int()`. Same pattern needed here for `project_key`.
- **Issue #127**: "Job queue: detect and recover stuck running jobs" — Early job recovery work. Doesn't address the `push-*` session type.
- **PR #377**: "Fix Observer early return on continuation sessions" — Observer routing fix, but no circuit breaker was added.
- **PR #408**: "Fix observer reason leak and false promise halts" — Observer quality fix, but didn't address import failures or steer→timeout cycles.
- **Issue #426 / PR #427**: "Add zombie process detection and cleanup to bridge watchdog" — Recent watchdog enhancement. Focuses on zombie PIDs, not session-level recovery bugs.

## Data Flow

### Problem 1: Stalled push-* session recovery

1. **Entry**: Push webhook creates `AgentSession` with `session_id="push-13b78316"` and `project_key` as plain string (not a Popoto DB_key object)
2. **Watchdog cycle** (every 5 min): `check_stalled_sessions()` finds sessions pending > 300s threshold
3. **Recovery**: `_recover_stalled_pending()` calls `_kill_stalled_worker(project_key)` at line 378
4. **Crash**: `_kill_stalled_worker()` passes `project_key` to `_active_workers.get()` — if the worker lookup succeeds, downstream code may attempt `.redis_key` on the string value, causing `AttributeError`

### Problem 2: Stall detection via activity heartbeat

1. **Entry**: SDLC job dispatched via `agent/sdk_client.py:run_sdk_query()`
2. **Activity tracking**: SDK client records timestamp of last tool call or log output
3. **Heartbeat check**: The existing 5-minute watchdog cycle checks `time_since_last_activity` instead of absolute wall-clock duration
4. **Stall detection**: If no tool call or log output for a configurable inactivity threshold (e.g., 5 minutes), the session is flagged as stalled
5. **Recovery**: Watchdog can kill the stalled worker — active sessions (still producing tool calls/logs) are never interrupted regardless of total runtime

### Problem 3: Observer import crash

1. **Entry**: Observer calls `_build_observer_system_prompt()` at line 644
2. **Import**: `from agent.sdk_client import load_principal_context` at line 119
3. **Crash**: Import fails (circular import or module not yet loaded), raises `ImportError`
4. **Fallback**: Exception caught at line 702, falls back to `deliver` — but no tracking of consecutive failures

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #343 | Fixed Popoto Field type mismatch in `_compute_stall_backoff` | Only addressed `retry_count` coercion, not `project_key` string handling |
| PR #402/Issue #402 | Made stall recovery actually kill workers | Assumed `project_key` was always a proper object; didn't handle `push-*` sessions with plain string keys |
| PR #408 | Fixed observer reason leak | Focused on output quality, not failure resilience — no circuit breaker added |

**Root cause pattern:** Popoto ORM fields sometimes surface as raw Python types (str, int) instead of DB_key objects. Code assumes the ORM wrapper is always present. The observer lacks defensive programming for repeated failures.

## Architectural Impact

- **New dependencies**: None — all fixes use existing stdlib and project patterns
- **Interface changes**: New `SDK_INACTIVITY_TIMEOUT_SECONDS` env var (backward compatible, default 300s)
- **Coupling**: Reduces coupling — observer becomes more resilient to sdk_client import state
- **Data ownership**: No change
- **Reversibility**: Fully reversible — all changes are additive guards and configuration

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1 (scope confirmation on timeout values)
- Review rounds: 1 (code review)

Three distinct bugs in two files, with integration tests. Each fix is small but they need coordinated testing.

## Prerequisites

No prerequisites — this work uses only existing dependencies and infrastructure.

## Solution

### Key Elements

- **Type guard in watchdog**: Defensive handling of `project_key` as plain string in `_recover_stalled_pending()` and `_kill_stalled_worker()`
- **Activity-based stall detection**: Track last tool call / log output timestamp; the 5-minute heartbeat detects inactivity instead of enforcing hard timeouts
- **Observer circuit breaker with escalating backoff**: Retry API/outage errors with exponential backoff; only escalate to human when human action can actually help (not for transient API issues)
- **Observer import guard**: Try/except around `load_principal_context` import with graceful fallback

### Flow

**Watchdog detects stall** → Type-guard project_key → Kill worker or clean up orphan → Session recovered

**SDLC job runs** → SDK client records last_activity timestamp on each tool call/log output → 5-min heartbeat checks inactivity → If idle > threshold, kill worker and recover session

**Observer evaluates** → Import guarded → If failure, classify error type → API/outage: retry with escalating backoff (30s, 60s, 120s, 240s...) → Non-retryable / human-actionable: escalate to Telegram → Counter resets on success

### Technical Approach

1. **Problem 1 (redis_key AttributeError)**:
   - In `_kill_stalled_worker()`: ensure `project_key` is always treated as a plain string for dict lookups
   - In `_recover_stalled_pending()`: add cleanup path for sessions stuck >1 hour with no history — abandon and notify instead of retry
   - Add `str()` coercion wherever `project_key` is used for lookups/comparisons

2. **Problem 2 (Activity-based stall detection)**:
   - Add `_last_activity_timestamp: dict[str, float]` tracker in SDK client, updated on each tool call and log output
   - Expose `get_session_last_activity(session_id) -> float | None` for the watchdog to query
   - In the watchdog's 5-minute heartbeat: check `time.time() - last_activity` instead of `time.time() - session_start`
   - Configurable inactivity threshold via `SDK_INACTIVITY_TIMEOUT_SECONDS` env var (default: 300s / 5 minutes)
   - Active sessions (producing tool calls/logs) are never interrupted regardless of total runtime
   - Remove or relax the hard `asyncio.timeout(600)` — let the heartbeat handle stall detection

3. **Problem 3 (Observer circuit breaker with escalating backoff)**:
   - Add `_observer_failure_counts: dict[str, int]` and `_observer_last_retry: dict[str, float]` trackers
   - Classify errors: API/outage errors → retryable; import errors, logic bugs → non-retryable
   - Retryable errors: exponential backoff (30s, 60s, 120s, 240s, max 480s), retry automatically
   - Non-retryable errors or errors where human action can help (e.g., missing credentials, config issues): escalate to Telegram immediately
   - After max backoff retries exhausted (e.g., 5 consecutive retryable failures): escalate as likely sustained outage
   - Reset counters on success
   - Wrap `_build_observer_system_prompt()` import in try/except with fallback to prompt without principal context

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_recover_stalled_pending()` except block at line 412 — test that string project_key doesn't raise AttributeError
- [ ] `_build_observer_system_prompt()` import — test that ImportError is caught and fallback prompt is returned
- [ ] `_run_llm_observer()` except block at line 702 — test that consecutive failures trigger escalation

### Empty/Invalid Input Handling
- [ ] `_kill_stalled_worker("")` — empty string project_key should return False gracefully
- [ ] `_kill_stalled_worker(None)` — None project_key should return False gracefully
- [ ] Observer with empty worker_output — should still make a valid decision

### Error State Rendering
- [ ] Circuit breaker escalation produces a clear Telegram message identifying the stuck session
- [ ] Timeout errors surface the phase and duration in the error summary

## Test Impact

- [ ] `tests/unit/test_session_watchdog.py` — UPDATE: add test cases for string project_key handling and orphan push-* cleanup
- [ ] `tests/unit/test_observer.py` — UPDATE: add circuit breaker tests and import fallback tests
- [ ] `tests/unit/test_pending_recovery.py` — UPDATE: add push-* session recovery scenarios
- [ ] `tests/unit/test_sdk_client_sdlc.py` — UPDATE: add activity tracking and inactivity detection tests

## Rabbit Holes

- **Redesigning the Popoto ORM layer** — The root cause is Popoto returning raw types, but fixing the ORM is a separate, large project. Just add type guards.
- **Per-phase hard timeout limits** — Hard wall-clock timeouts are the wrong model. Active sessions producing tool calls should never be killed. Activity-based detection is the correct approach.
- **Complex retry orchestration frameworks** — Keep backoff logic simple (exponential with cap). Don't add retry queues, dead-letter patterns, or separate retry services.
- **Rewriting the entire stall detection system** — The current system works; just fix the edge cases.

## Risks

### Risk 1: Activity tracking misses silent stalls
**Impact:** If an SDK session stalls without producing tool calls or log output (e.g., hanging network request), the activity tracker won't see updates, but the session is technically "doing something."
**Mitigation:** The inactivity threshold (default 5 min) is generous enough that even slow operations will produce some output. If a network call hangs for 5+ minutes with zero output, it's genuinely stalled.

### Risk 2: Escalating backoff delays recovery too long
**Impact:** 5 retries with exponential backoff (30s→60s→120s→240s→480s) means ~15 minutes before escalation on sustained outage.
**Mitigation:** This is acceptable — Anthropic API outages typically resolve within minutes, and blind escalation during outages just creates noise. The backoff schedule can be tuned via env vars if needed.

## Race Conditions

### Race 1: Concurrent watchdog cycles processing same stalled session
**Location:** `monitoring/session_watchdog.py` lines 327-418
**Trigger:** Watchdog cycle takes longer than 5 minutes, next cycle starts while first is still processing the same session
**Data prerequisite:** Session must exist in Redis with status=pending
**State prerequisite:** Worker must still be alive when `_kill_stalled_worker()` runs
**Mitigation:** The existing `retry_count` increment acts as an optimistic lock. Even if two cycles race, the retry count prevents double-retry. Add an explicit check: if session status changed since stall detection, skip recovery.

## No-Gos (Out of Scope)

- Fixing the Popoto ORM to always return proper DB_key objects (separate issue)
- Building a separate retry queue or scheduling service
- Adding per-session timeout tuning UI
- Refactoring the entire watchdog architecture
- Addressing why `push-*` webhook sessions are created with plain string project_keys (separate issue — fix the symptom here)

## Update System

No update system changes required — all fixes are internal to the bridge and monitoring code. No new dependencies, config files, or migration steps needed. New `SDK_INACTIVITY_TIMEOUT_SECONDS` env var is optional with a sensible default (300s).

## Agent Integration

No agent integration required — this is a bridge-internal change affecting the session watchdog, SDK client timeout configuration, and observer error handling. No new MCP tools or bridge imports are needed.

## Documentation

- [ ] Create `docs/features/session-watchdog-reliability.md` describing the watchdog recovery system, circuit breaker, and per-phase timeouts
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings in `monitoring/session_watchdog.py` for `_recover_stalled_pending()` and `_kill_stalled_worker()`
- [ ] Update inline docstrings in `bridge/observer.py` for circuit breaker behavior
- [ ] Document `SDK_INACTIVITY_TIMEOUT_SECONDS` env var in `.env.example` or inline comments

## Success Criteria

- [ ] `push-*` stalled sessions are recovered or cleaned up without AttributeError
- [ ] Stall detection is activity-based: sessions are only killed when idle (no tool calls/logs) for > inactivity threshold
- [ ] Active sessions (producing output) are never killed regardless of total runtime
- [ ] Observer circuit breaker retries API/outage errors with escalating backoff (30s→480s)
- [ ] Observer only escalates to human when human action can actually help
- [ ] Observer import errors are caught gracefully with fallback behavior (prompt without principal context)
- [ ] Unit test: simulate stalled push-* session with string project_key, verify recovery without crash
- [ ] Unit test: simulate observer failure cascade, verify circuit breaker fires at threshold
- [ ] Unit test: activity tracking updates on tool call and log output
- [ ] Unit test: inactivity detection correctly identifies stalled vs active sessions
- [ ] All existing tests continue to pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog-fix)**
  - Name: watchdog-builder
  - Role: Fix redis_key AttributeError and push-* session cleanup in session_watchdog.py
  - Agent Type: builder
  - Resume: true

- **Builder (sdk-timeout)**
  - Name: timeout-builder
  - Role: Implement per-phase SDK timeout configuration in sdk_client.py
  - Agent Type: builder
  - Resume: true

- **Builder (observer-circuit-breaker)**
  - Name: observer-builder
  - Role: Add circuit breaker and import guard to observer.py
  - Agent Type: builder
  - Resume: true

- **Validator (all-fixes)**
  - Name: reliability-validator
  - Role: Verify all three fixes work together without regression
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation and update docstrings
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Fix watchdog redis_key AttributeError
- **Task ID**: build-watchdog
- **Depends On**: none
- **Validates**: tests/unit/test_session_watchdog.py, tests/unit/test_pending_recovery.py
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `str()` coercion for `project_key` in `_kill_stalled_worker()` to handle plain string values
- Add type guard in `_recover_stalled_pending()` — if project_key is a plain string, use it directly for dict lookups
- Add cleanup path for push-* sessions stuck >1 hour with no history: abandon and notify
- Add guard: if session status changed since stall detection, skip recovery
- Add unit tests for string project_key, None project_key, and orphan push-* cleanup

### 2. Implement activity-based stall detection
- **Task ID**: build-activity-detection
- **Depends On**: none
- **Validates**: tests/unit/test_sdk_client_sdlc.py
- **Assigned To**: timeout-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_last_activity_timestamp: dict[str, float]` tracker in `sdk_client.py`, updated on each tool call callback and log output
- Expose `get_session_last_activity(session_id) -> float | None` function for watchdog consumption
- Update watchdog heartbeat to check `time_since_last_activity` instead of `time_since_session_start`
- Add `SDK_INACTIVITY_TIMEOUT_SECONDS` env var (default: 300s) for configurable inactivity threshold
- Remove or relax the hard `asyncio.timeout(600)` — active sessions should never be killed
- Add unit tests for activity tracking, inactivity detection, and threshold configuration

### 3. Add observer circuit breaker with escalating backoff and import guard
- **Task ID**: build-observer
- **Depends On**: none
- **Validates**: tests/unit/test_observer.py
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add module-level `_observer_failure_counts: dict[str, int]` and `_observer_last_retry: dict[str, float]` trackers
- Classify errors: API/outage → retryable, import/config/logic → non-retryable
- Retryable errors: exponential backoff (30s, 60s, 120s, 240s, max 480s), auto-retry
- Non-retryable errors (human can help): escalate to Telegram immediately
- After 5 consecutive retryable failures (backoff exhausted): escalate as sustained outage
- Reset counters on success
- Wrap `from agent.sdk_client import load_principal_context` in try/except ImportError
- On ImportError: log warning, build prompt without principal context section
- Add unit tests for backoff schedule, error classification, escalation conditions, reset on success, and import fallback

### 4. Handle escalation and backoff retry in bridge
- **Task ID**: build-escalation-handler
- **Depends On**: build-observer
- **Validates**: tests/unit/test_observer.py
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Ensure the observer's "escalate" action is handled wherever observer results are consumed
- Implement backoff retry scheduler: when observer returns "retry_after" with delay, schedule re-evaluation
- Send Telegram notification with session ID, failure count, error type, and last error ONLY when human action can help
- Error message should clearly state what the human can do (e.g., "API key expired", "config missing X")
- Add integration-style unit test verifying escalation flow and backoff retry scheduling

### 5. Validate all fixes
- **Task ID**: validate-all-fixes
- **Depends On**: build-watchdog, build-timeout, build-observer, build-escalation-handler
- **Assigned To**: reliability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify no regressions in existing watchdog, observer, and SDK tests
- Verify all new tests pass
- Run `python -m ruff check . && python -m ruff format --check .`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all-fixes
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/session-watchdog-reliability.md`
- Add entry to `docs/features/README.md` index table
- Update docstrings in modified functions

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: reliability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Watchdog tests | `pytest tests/unit/test_session_watchdog.py tests/unit/test_pending_recovery.py -v` | exit code 0 |
| Observer tests | `pytest tests/unit/test_observer.py -v` | exit code 0 |
| Activity detection tests | `pytest tests/unit/test_sdk_client_sdlc.py -v` | exit code 0 |
| Feature docs exist | `test -f docs/features/session-watchdog-reliability.md` | exit code 0 |

---

## Open Questions

*All resolved by PM feedback (2026-03-18):*

1. ~~**SDK timeout values**~~: **Resolved** — No hard timeouts. Activity-based stall detection via 5-minute heartbeat. Inactivity threshold default: 300s.
2. ~~**Circuit breaker escalation channel**~~: **Resolved** — Retry with escalating backoff for API/outage errors. Only escalate to chat when human action can actually help (not for transient API issues).
