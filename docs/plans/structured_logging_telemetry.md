---
status: Shipped
type: feature
appetite: Medium
owner: Valor
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/319
---

# Structured Logging and Telemetry for Observer Agent

## Problem

Issue #319 defines three phases of observability for the Observer Agent and SDLC pipeline. PRs #335 (observer logging) and #337 (correlation IDs) shipped Phase 1 partially. Two Phase 1 gaps remain, and Phases 2-3 are untouched.

**Current state:**
- Correlation IDs: end-to-end (bridge → queue → agent → observer → transcripts)
- Stage transition logging: structured in `stage_detector.py`
- Observer decision logging: steer/deliver with reason in `observer.py`
- Observer tool use logging: iteration-level with tool name + result preview
- Link enforcement logging: debug-level only, not structured
- Human interjection logging: not implemented
- Redis telemetry: not implemented
- Alerting: alert definitions exist in `monitoring/alerts.py` but handlers never invoked

**Desired outcome:**
- All five event types from #319 logged with structured, parseable format
- Redis-based metrics for pipeline health dashboard
- Threshold alerting for observer errors and pipeline stalls

## Prior Art

- **PR #337**: Correlation IDs — end-to-end request tracing
- **PR #335**: Observer decision logging — steer/deliver reasoning, enrichment, stage detection
- **Issue #309**: Observer Agent introduction
- **monitoring/alerts.py**: Existing alert threshold definitions
- **monitoring/session_tracker.py**: In-memory session tracking (not Redis)

## Data Flow

```
Observer decisions → structured log lines (existing loggers)
                   → Redis HINCRBY/LPUSH (new telemetry collector)
                   → monitoring/alerts.py threshold checks (existing, newly invoked)
```

Metrics stored in Redis keys:
- `telemetry:observer:decisions` — hash: steer_count, deliver_count, error_count
- `telemetry:pipeline:completions` — hash: per-stage completion counts
- `telemetry:observer:tool_usage` — hash: per-tool invocation counts
- `telemetry:interjections` — list: recent interjection events (capped)
- `telemetry:daily:{date}` — hash: daily rollup for trend tracking

## Architectural Impact

- **New dependencies**: None (Redis already used via Popoto)
- **Interface changes**: None — all additions are internal logging/metrics
- **Coupling**: New `monitoring/telemetry.py` module reads from observer/stage detector events
- **Data ownership**: Telemetry data owned by monitoring layer, written by bridge layer
- **Reversibility**: Fully reversible — telemetry is additive, removing it changes no behavior

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

- PR #337 merged (done)
- PR #335 merged (done)
- Observer Agent stable on main (done)

## Solution

### Key Elements

1. **Fill Phase 1 gaps**: Add structured logging for link enforcement and human interjections
2. **Telemetry collector**: New `monitoring/telemetry.py` with Redis-backed counters
3. **Instrument observer**: Call telemetry collector from observer decision points
4. **Alert integration**: Wire telemetry thresholds to existing alert system
5. **Health endpoint**: Add telemetry summary to existing health check

### Flow

Fill logging gaps → Create telemetry module → Instrument observer + stage detector → Wire alerts → Add health check integration → Test

### Technical Approach

**Phase 1 gaps (structured logging):**
- Upgrade link enforcement logging in `models/agent_session.py:set_link()` from debug to info with structured format: `LINK session={id} correlation={cid} type={kind} action={found|set} url={url}`
- Add human interjection logging in `bridge/observer.py` when reading/clearing queued steering messages: `INTERJECTION session={id} correlation={cid} count={N} action={forwarded|cleared}`

**Phase 2 (telemetry):**
- New `monitoring/telemetry.py` with functions: `record_decision()`, `record_stage_transition()`, `record_tool_use()`, `record_interjection()`, `get_summary()`
- Uses Redis HINCRBY for counters, LPUSH+LTRIM for event lists (capped at 100)
- Daily rollup keys with 7-day TTL for trend data
- `get_summary()` returns dict suitable for dashboard or health check

**Phase 3 (alerting):**
- Add `check_observer_health()` in `monitoring/telemetry.py` that reads counters and compares to thresholds
- Wire into existing `monitoring/alerts.py` framework
- Thresholds: error_rate > 10%, pipeline_completion < 50%, observer_latency > 5s

## Failure Path Test Strategy

### Exception Handling Coverage
- Telemetry writes wrapped in try/except to never break observer flow
- Redis connection failures logged and silently skipped (telemetry is best-effort)

### Empty/Invalid Input Handling
- `get_summary()` returns zero-valued dict when no data exists
- Missing correlation_id falls back to "unknown" in telemetry records

### Error State Rendering
- Health check includes telemetry status (ok/degraded/unavailable)
- Alert messages include specific metric values and thresholds

## Rabbit Holes

- Don't build a web dashboard UI — Redis data + health endpoint is sufficient
- Don't add external services (Datadog, Grafana) — keep it Redis + logs
- Don't refactor existing observer logging — only add to it
- Don't add telemetry that blocks the stop hook — all writes must be async/fire-and-forget

## Risks

### Risk 1: Telemetry Redis writes slow down observer
**Impact:** Observer stop hook exceeds timeout, Claude Code kills it
**Mitigation:** Use Redis pipeline for batched writes. Wrap all telemetry in try/except with timeout. Observer continues regardless of telemetry success.

### Risk 2: Redis key bloat
**Impact:** Memory pressure from unbounded telemetry data
**Mitigation:** Daily keys expire after 7 days. Event lists capped at 100 entries via LTRIM. Total key count bounded by design.

## Race Conditions

- Multiple observer instances may increment counters concurrently — Redis HINCRBY is atomic, so this is safe
- Daily rollup key creation is idempotent (HINCRBY creates if missing)

## No-Gos (Out of Scope)

- Web dashboard UI
- External telemetry services
- Modifying existing log formats (only adding new ones)
- Historical backfill of metrics
- Real-time streaming/websocket dashboard

## Update System

No update system changes required — telemetry uses existing Redis infrastructure and adds no new dependencies or config files.

## Agent Integration

No agent integration required — this is bridge/monitoring-internal. The telemetry module is called from observer code, not exposed via MCP tools. The agent does not need to invoke telemetry directly.

## Documentation

- [ ] Create `docs/features/structured-logging-telemetry.md` describing the telemetry system, Redis key schema, and how to query metrics
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/correlation-ids.md` to reference telemetry as the next layer

## Success Criteria

- [ ] Link enforcement logged with structured format at INFO level
- [ ] Human interjections logged with structured format at INFO level
- [ ] `monitoring/telemetry.py` exists with Redis-backed counters
- [ ] Observer decisions recorded in Redis (steer/deliver/error counts)
- [ ] Stage transitions recorded in Redis (per-stage completion counts)
- [ ] Daily rollup keys created with 7-day TTL
- [ ] `get_summary()` returns complete telemetry snapshot
- [ ] Alert thresholds checked on observer health queries
- [ ] All telemetry writes are non-blocking (observer never fails due to telemetry)
- [ ] Existing tests pass
- [ ] New tests for telemetry module (record + read back)

## Team Orchestration

### Team Members

- **Builder (telemetry)**
  - Name: telemetry-builder
  - Role: Implement telemetry module and instrument observer
  - Agent Type: builder
  - Resume: true

- **Validator (telemetry)**
  - Name: telemetry-validator
  - Role: Verify metrics collection and alert integration
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fill Phase 1 logging gaps
- **Task ID**: phase1-gaps
- **Depends On**: none
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: false
- Upgrade `models/agent_session.py:set_link()` to structured INFO logging
- Add human interjection structured logging in `bridge/observer.py` (when reading queued_steering_messages)
- Follow existing log format: `[{correlation_id}] EVENT_TYPE key=value key=value`

### 2. Create telemetry module
- **Task ID**: telemetry-module
- **Depends On**: none
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: true (with task 1)
- Create `monitoring/telemetry.py` with:
  - `record_decision(session_id, correlation_id, action, reason)` — HINCRBY on decision counters
  - `record_stage_transition(session_id, correlation_id, stage, old_status, new_status)` — HINCRBY on stage counters
  - `record_tool_use(session_id, correlation_id, tool_name, duration_ms)` — HINCRBY on tool counters
  - `record_interjection(session_id, correlation_id, message_count, action)` — LPUSH to event list
  - `get_summary()` — read all counters into dict
  - `check_observer_health()` — compare counters to thresholds, return health status
- All writes wrapped in try/except, non-blocking
- Daily rollup keys with 7-day TTL

### 3. Instrument observer with telemetry
- **Task ID**: instrument-observer
- **Depends On**: telemetry-module
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: false
- Call `record_decision()` after observer makes steer/deliver/error decision (~line 462-478)
- Call `record_interjection()` when processing queued steering messages
- Call `record_tool_use()` in the tool iteration loop (~line 421)

### 4. Instrument stage detector with telemetry
- **Task ID**: instrument-stages
- **Depends On**: telemetry-module
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: true (with task 3)
- Call `record_stage_transition()` from `stage_detector.py` when transitions are applied (~line 217)

### 5. Wire alerting
- **Task ID**: wire-alerts
- **Depends On**: telemetry-module
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `check_observer_health()` call to existing health check in `monitoring/health.py`
- Define thresholds: error_rate > 10%, completion_rate < 50%
- Log alerts via existing `monitoring/alerts.py` framework

### 6. Write tests
- **Task ID**: write-tests
- **Depends On**: instrument-observer, instrument-stages, wire-alerts
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: false
- Test `record_decision()` + `get_summary()` round-trip
- Test `record_stage_transition()` increments correct counters
- Test `check_observer_health()` returns correct status based on thresholds
- Test TTL is set on daily keys
- Test telemetry failure doesn't propagate (mock Redis failure)

### 7. Create documentation
- **Task ID**: write-docs
- **Depends On**: write-tests
- **Assigned To**: telemetry-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/structured-logging-telemetry.md`
- Update `docs/features/README.md` index
- Update `docs/features/correlation-ids.md` with telemetry reference

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: write-tests, write-docs
- **Assigned To**: telemetry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff check . && python -m ruff format --check .`
- Run `pytest tests/`
- Verify all success criteria met
- Verify Redis keys created correctly (manual check)

## Validation Commands

- `python -m ruff check monitoring/telemetry.py` — no lint errors
- `python -m ruff format --check monitoring/telemetry.py` — properly formatted
- `pytest tests/` — all tests pass
- `grep -n "LINK session=" models/agent_session.py` — structured link logging present
- `grep -n "INTERJECTION session=" bridge/observer.py` — structured interjection logging present
- `python -c "from monitoring.telemetry import get_summary; print(get_summary())"` — telemetry module importable
