---
slug: unified-analytics
status: Planning
type: feature
appetite: Large
tracking: https://github.com/tomcounsell/ai/issues/854
created: 2026-04-11
last_comment_id:
---

# Unified Analytics System

## Problem

Observability data is scattered across disconnected silos with no aggregation layer. Token costs are logged at INFO level and lost. Session counts, durations, and outcomes are queryable only as live Redis state with no historical rollups. SDLC stage timing is not tracked. Memory system recall precision is unmeasured. Crash history is a local JSONL file with no API.

**Current behavior:**

- Token cost per query is logged in `agent/sdk_client.py` (line ~1091-1103) but never persisted -- data flows to log files and disappears
- Session lifecycle data exists in Redis via Popoto `AgentSession` model but has no historical rollups -- only live state via `dashboard.json`
- SDLC stage transitions are managed by `PipelineStateMachine` in `bridge/pipeline_state.py` but completion times per stage are not recorded
- Memory system operations (recall attempts, bloom hits, extraction counts) are not measured
- Crash tracker (`monitoring/crash_tracker.py`) writes to a local JSONL file with no programmatic query API
- Health checks (`monitoring/health.py`) produce `HealthCheckResult` objects but only as point-in-time snapshots
- The previously designed telemetry system (Redis-backed counters with keys like `telemetry:daily:{date}`) was deleted as dead code in #753

**Desired outcome:**

A single analytics system that collects metrics from all subsystems, stores them for historical querying, and exposes trends via the dashboard. Any metric the system produces can be queried for the last N days. Token cost per session is tracked and queryable. A CLI command exports benchmark data as JSON.

## Freshness Check

**Baseline commit:** `db38b4ff`
**Issue filed at:** 2026-04-09T08:34:23Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/sdk_client.py:1091-1101` -- token cost logging block -- still holds, code unchanged
- `monitoring/crash_tracker.py` -- JSONL-based crash history -- still holds
- `monitoring/health.py` -- `HealthCheckResult` dataclass -- still holds
- `monitoring/session_tracker.py` -- in-memory `SessionTracker` -- still holds (not connected to Redis-backed AgentSession)
- `ui/app.py` -- FastAPI dashboard with `dashboard.json` endpoint -- still holds, minor UI commits since issue (slug display, modal fixes) but API structure unchanged
- `bridge/pipeline_state.py:140` -- `PipelineStateMachine` class -- still holds
- `docs/features/structured-logging-telemetry.md` -- design doc with Redis key schema -- still holds, correctly notes implementation was deleted

**Cited sibling issues/PRs re-checked:**
- #753 -- closed 2026-04-07, deleted `monitoring/telemetry.py` and `models/telemetry.py` -- confirms implementation must be built from scratch
- #620 -- still open, broader roadmap issue -- no conflict

**Commits on main since issue was filed (touching referenced files):**
- `230b94da` Dashboard: slug as session name, capture issue/PR links (#879) -- UI change, additive, does not affect analytics plan
- `748cdec8` Dashboard: copy button, 2-col modal details -- UI change, irrelevant
- `d24dd07f` Add CLI harness abstraction for dev sessions -- agent harness change, irrelevant to analytics

**Active plans in `docs/plans/` overlapping this area:** None. The `reflections-dashboard.md` plan exists but focuses on reflections UI, not metrics collection.

**Notes:** The dashboard has had several UI improvements since the issue was filed, but the `dashboard.json` API contract is unchanged. The `monitoring/telemetry.py` module remains deleted. All issue claims are accurate.

## Prior Art

- **Issue #319**: Add structured logging and telemetry for Observer Agent and stage transitions -- closed, established the Redis key schema (`telemetry:observer:decisions`, `telemetry:daily:{date}`, etc.) and structured log format. The design was sound but the implementation was never wired into production code paths, resulting in dead code.
- **Issue #753**: Delete dead ObserverTelemetry model and monitoring/telemetry module -- closed, cleaned up the orphaned implementation. Confirms we start from scratch with implementation but can reuse the design doc's key naming conventions.
- **Issue #488**: Consolidate SDLC stage tracking -- closed, consolidated stage tracking into `PipelineStateMachine`. Relevant because stage transitions are now managed in one place (`bridge/pipeline_state.py`), making them easy to instrument.
- **Issue #552**: Local Claude Code session observability and memory parity -- closed, added Claude Code hooks for memory. Relevant because memory operations already have hook entry points that can emit metrics.
- **Issue #645**: Implicit pipeline stage tracking via observable artifacts -- closed, explored artifact-based stage detection. Relevant because it confirms `stage_states` on `AgentSession` is the canonical stage tracking mechanism.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #319 implementation | Created `monitoring/telemetry.py` and `models/telemetry.py` with Redis counters | Never wired into production code paths. The Observer and pipeline state machine never called the telemetry functions. The implementation existed in isolation with no callers. |

**Root cause pattern:** The telemetry code was built as a standalone module waiting for callers, rather than being instrumented at the point where events naturally occur. The analytics system must instrument existing code paths directly rather than building a separate module that needs explicit wiring.

## Data Flow

### Metric Collection Flow

1. **Entry points**: Existing code paths where observable events occur:
   - `agent/sdk_client.py` -- token cost available in `msg.total_cost_usd` after each query
   - `models/session_lifecycle.py` -- session status transitions (pending -> running -> completed/failed)
   - `bridge/pipeline_state.py` -- SDLC stage starts/completions via `start_stage()`/`complete_stage()`
   - `agent/memory_retrieval.py` -- recall attempts and bloom filter checks
   - `agent/memory_extraction.py` -- post-session extraction counts
   - `monitoring/crash_tracker.py` -- crash events
   - `monitoring/health.py` -- health check results

2. **Collection layer** (`analytics/collector.py`): Each entry point calls `record_metric(name, value, dimensions)` wrapped in try/except. This is a thin function that writes to both SQLite (historical) and Redis (live counters). Failures are caught and logged, never propagated.

3. **Storage layer**:
   - **SQLite** (`data/analytics.db`): Append-only time-series table with columns `(timestamp, metric_name, value, dimensions_json)`. This is the historical store for trend queries.
   - **Redis**: Live counters using HINCRBY on keys like `analytics:live:{metric_name}` and daily rollups on `analytics:daily:{YYYY-MM-DD}` with 30-day TTL. These power the dashboard's real-time views.

4. **Query layer** (`analytics/query.py`): Python API for querying historical data from SQLite (e.g., "sessions per day for last 30 days") and live data from Redis.

5. **Output**:
   - Dashboard: New `dashboard.json` fields (additive) plus new HTMX partial for trend chart
   - CLI: `python -m tools.analytics export --days 30` produces JSON benchmark report

## Architectural Impact

- **New dependencies**: `sqlite3` (stdlib, no external dep). No new pip packages.
- **Interface changes**: `dashboard.json` gets new additive fields (`analytics` key). No existing fields changed or removed.
- **Coupling**: Each instrumented module gets a single `record_metric()` call. The analytics module has no reverse dependencies -- it is a pure sink. Coupling is minimal and unidirectional.
- **Data ownership**: Analytics owns its own SQLite database (`data/analytics.db`) and a set of Redis keys under the `analytics:` prefix. No overlap with existing Popoto models.
- **Reversibility**: High. Removing the `record_metric()` calls at each instrumentation point restores the original behavior. The SQLite file and Redis keys can be deleted without affecting any other system.

## Appetite

**Size:** Large

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (scope alignment on dashboard views, metric naming)
- Review rounds: 1 (code review for instrumentation points)

Large appetite because this touches many subsystems (sdk_client, session lifecycle, pipeline state, memory retrieval/extraction, crash tracker, health checks, dashboard) and introduces a new storage layer (SQLite). However, each individual instrumentation point is small, and the core analytics module is straightforward.

## Prerequisites

No prerequisites -- this work uses only stdlib SQLite and existing Redis. No new API keys or external services.

## Solution

### Key Elements

- **Collector**: A `record_metric()` function that writes a metric event to SQLite and increments a Redis counter. Best-effort: all writes wrapped in try/except.
- **Store**: SQLite database at `data/analytics.db` for historical time-series. Redis keys under `analytics:` prefix for live counters and daily rollups.
- **Instrumentation**: Lightweight calls to `record_metric()` inserted at existing event points (token cost logging, session transitions, stage starts/completions, memory operations).
- **Query API**: Python functions for querying metrics by name, time range, and dimensions.
- **Dashboard integration**: New `analytics` key in `dashboard.json` with summary stats. New HTMX partial showing sessions-per-day trend.
- **CLI export**: `python -m tools.analytics export` command for benchmark data.
- **Rollup job**: Periodic aggregation from raw events into daily summaries, run as part of the reflections daily maintenance cycle.

### Flow

**Agent runs session** -> sdk_client emits token cost metric -> session lifecycle emits start/complete metric -> pipeline state emits stage metrics -> **analytics SQLite + Redis** -> dashboard queries Redis for live view -> CLI queries SQLite for historical export

### Technical Approach

- Use Python's `sqlite3` stdlib module for the historical store. Single WAL-mode database file at `data/analytics.db`. One table: `metrics(id, timestamp, name, value, dimensions)`.
- Redis keys follow the pattern from the deleted telemetry design doc but under a new `analytics:` prefix: `analytics:live:{name}` (hash of dimension->count), `analytics:daily:{YYYY-MM-DD}` (hash of metric->value with 30-day TTL).
- The `record_metric()` function is a module-level function in `analytics/collector.py`. It handles both SQLite writes and Redis increments in a single call. Both storage operations are independently try/excepted so a Redis failure does not prevent SQLite writes and vice versa.
- Instrumentation is done by adding a single function call at each event point. No decorators, no middleware, no monkey-patching.
- The dashboard integration extends the existing `dashboard.json` endpoint with a new top-level `analytics` key containing summary counters and recent daily rollups.
- The HTMX trend view uses a simple ASCII/text-based bar chart in an HTML partial, avoiding any JavaScript charting library. This keeps the dashboard dependency-free.
- The CLI export command outputs JSON with metric summaries suitable for external publication.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `analytics/collector.py::record_metric` -- primary try/except around SQLite write and Redis increment. Test that a SQLite connection failure does not propagate. Test that a Redis connection failure does not propagate. Both must log a warning.
- [ ] `analytics/query.py` -- query functions must handle empty database and return sensible defaults (empty lists, zero counts) rather than raising.

### Empty/Invalid Input Handling
- [ ] `record_metric()` with empty name, None value, missing dimensions -- must not raise, must log and skip
- [ ] Query functions with date ranges that have no data -- must return empty results, not errors
- [ ] CLI export with no data in database -- must produce valid JSON with empty arrays

### Error State Rendering
- [ ] Dashboard `analytics` section with no data -- must render "No data yet" rather than crashing
- [ ] `dashboard.json` `analytics` key with empty database -- must return empty object, not error

## Test Impact

No existing tests affected -- this is a greenfield feature adding a new `analytics/` module and new `tools/analytics.py` CLI. All instrumentation points are additive `record_metric()` calls that do not change existing return values or control flow. The `dashboard.json` endpoint gets new additive fields but existing fields are unchanged, so existing dashboard tests (if any) will not break.

## Rabbit Holes

- **Real-time WebSocket streaming** -- Tempting to push metrics to the dashboard in real-time via WebSocket. Not worth it. HTMX polling at 30-second intervals is sufficient and much simpler.
- **Custom charting library** -- Tempting to add Chart.js or similar for dashboard visualizations. Avoid. Use simple text-based or CSS-only bar charts in the HTMX partial. A charting library is a large dependency for minimal gain at this stage.
- **Migrating crash tracker to SQLite** -- Tempting to replace the JSONL file with the analytics SQLite database. Keep the crash tracker as-is and just add a reader that imports crash events into analytics on the daily rollup. Migration is a separate concern.
- **Per-project cost allocation** -- Tempting to build a full cost allocation system tracking costs per project. Defer. Track `project_key` as a dimension on cost metrics so it is queryable later, but do not build allocation UI now.
- **Prometheus/Grafana integration** -- Explicitly out of scope per issue constraints. SQLite + Redis is the stack.

## Risks

### Risk 1: SQLite contention under concurrent writes
**Impact:** Multiple simultaneous sessions calling `record_metric()` could hit SQLite write locks, causing slow writes or timeouts.
**Mitigation:** Use WAL mode (`PRAGMA journal_mode=WAL`) which allows concurrent reads and serialized writes with minimal contention. The `record_metric()` function uses short-lived connections (open, write, close) to minimize lock duration. If writes fail, they are silently dropped (best-effort pattern).

### Risk 2: SQLite database growth over time
**Impact:** Unbounded raw metric events could grow the database file to multiple GB over months.
**Mitigation:** The daily rollup job aggregates raw events into daily summaries and purges raw events older than 30 days. The rollup runs as part of the existing reflections daily cycle. Add a `data/analytics.db` size check to the health system.

### Risk 3: Instrumentation overhead in hot paths
**Impact:** Adding `record_metric()` calls to `sdk_client.py` and session lifecycle could add latency to the critical path.
**Mitigation:** The `record_metric()` function is designed to be fast: SQLite write is ~1ms with WAL mode, Redis HINCRBY is sub-millisecond. Total overhead per event is < 5ms. For the SDK client path, the metric is recorded after the query completes (in the `msg.total_cost_usd` handler), not before. No blocking on the critical path.

## Race Conditions

### Race 1: Concurrent SQLite writes from parallel sessions
**Location:** `analytics/collector.py::record_metric`
**Trigger:** Two agent sessions complete simultaneously, both calling `record_metric()` with cost data.
**Data prerequisite:** `data/analytics.db` must exist with the metrics table created.
**State prerequisite:** SQLite WAL mode must be enabled.
**Mitigation:** WAL mode serializes writes internally. Each `record_metric()` call uses its own connection with a short timeout (5 seconds). If the write lock is held, the call times out and silently drops the metric (best-effort). No data corruption risk with WAL mode.

### Race 2: Rollup job running while new events are being written
**Location:** `analytics/rollup.py` (daily aggregation)
**Trigger:** Rollup aggregates raw events while `record_metric()` is inserting new rows.
**Data prerequisite:** Raw events table has rows to aggregate.
**State prerequisite:** None beyond database existence.
**Mitigation:** Rollup reads and aggregates events with `timestamp < cutoff_time` where cutoff is the start of the rollup window. New events being written have timestamps >= cutoff and are ignored by the rollup query. Purge step deletes only rows already aggregated (same timestamp filter). No race.

## No-Gos (Out of Scope)

- **Prometheus/Grafana/InfluxDB** -- No heavyweight external dependencies
- **Real-time WebSocket push** -- Polling is sufficient for v1
- **JavaScript charting libraries** -- Keep dashboard dependency-free
- **Crash tracker migration** -- Keep JSONL; import into analytics via reader
- **Cost allocation UI** -- Track project_key dimension but defer allocation views
- **Alerting/thresholds** -- No pager-style alerting; the health system handles that
- **Log file parsing** -- Do not scrape existing log files for historical data; start collecting from deploy time forward

## Update System

The update script needs to handle the new `analytics/` package and ensure `data/analytics.db` is created on first run. Specifically:

- `scripts/remote-update.sh` does not need changes -- it runs `git pull` which will bring in the new `analytics/` package, and the SQLite database is created on first `record_metric()` call (no migration step needed)
- No new dependencies to install (sqlite3 is stdlib)
- No new config files to propagate
- The `data/` directory already exists on all machines and is gitignored

No update system changes required -- the SQLite database auto-creates on first write, and no new external dependencies are introduced.

## Agent Integration

The analytics CLI command (`python -m tools.analytics`) is a standalone tool that does not need MCP exposure. It is a developer/operator tool for exporting benchmark data, not something the agent needs to invoke during conversations.

The `record_metric()` calls are added to bridge-internal and agent-internal code paths. The agent does not call analytics functions directly -- metrics are emitted as side effects of normal operations.

No agent integration required -- this is an infrastructure-internal change. The analytics system is a passive collector that instruments existing code paths. No MCP server changes, no `.mcp.json` changes, no bridge import changes needed.

## Documentation

- [ ] Create `docs/features/unified-analytics.md` describing the analytics system architecture, metric catalog, query API, and CLI usage
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/structured-logging-telemetry.md` to reference the new analytics system as the successor implementation
- [ ] Add analytics CLI usage to `CLAUDE.md` Quick Commands table: `python -m tools.analytics export --days 30`

## Success Criteria

- [ ] `analytics/collector.py` exists with `record_metric(name, value, dimensions)` API
- [ ] Session metrics (count, duration, success/failure) are recorded for every agent session
- [ ] Token cost per session is captured from `sdk_client.py` and persisted to `data/analytics.db`
- [ ] SDLC stage transitions (start/complete per stage) are recorded
- [ ] Memory system operations (recall attempts, hits, extractions) are recorded
- [ ] Historical data is queryable via Python API (e.g., "sessions in the last 7 days")
- [ ] The web dashboard at `localhost:8500` includes at least one historical trend view (sessions per day)
- [ ] `python -m tools.analytics export --days 30` exports benchmark data as JSON
- [ ] `dashboard.json` remains backward-compatible (new `analytics` key is additive only)
- [ ] All metric recording is best-effort -- failures are caught and logged, never crash the caller
- [ ] Existing tests continue to pass; new tests cover analytics collection and query paths
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (analytics-core)**
  - Name: analytics-builder
  - Role: Build the analytics module (collector, store, query, rollup) and CLI export command
  - Agent Type: builder
  - Resume: true

- **Builder (instrumentation)**
  - Name: instrumentation-builder
  - Role: Add `record_metric()` calls to existing code paths (sdk_client, session lifecycle, pipeline state, memory ops, crash tracker, health)
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: dashboard-builder
  - Role: Extend dashboard.json with analytics data and add HTMX trend partial
  - Agent Type: builder
  - Resume: true

- **Validator (analytics)**
  - Name: analytics-validator
  - Role: Verify all success criteria, run tests, check best-effort pattern
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: analytics-tester
  - Role: Write unit and integration tests for collection, query, and dashboard endpoints
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: analytics-docs
  - Role: Create feature documentation and update references
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build Analytics Core Module
- **Task ID**: build-analytics-core
- **Depends On**: none
- **Validates**: `tests/unit/test_analytics_collector.py`, `tests/unit/test_analytics_query.py` (create)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `analytics/__init__.py`, `analytics/collector.py`, `analytics/query.py`, `analytics/rollup.py`
- Implement `record_metric(name: str, value: float, dimensions: dict | None = None)` with SQLite + Redis dual-write
- Implement SQLite schema: `metrics(id INTEGER PRIMARY KEY, timestamp REAL, name TEXT, value REAL, dimensions TEXT)`
- Use WAL mode, short-lived connections, 5-second write timeout
- Implement `query_metrics(name, start_time, end_time, dimensions_filter)` returning list of dicts
- Implement `query_daily_summary(name, days)` for aggregated daily data
- Implement `rollup_daily()` that aggregates raw events into `analytics:daily:{date}` Redis keys and purges raw events older than 30 days
- All public functions wrapped in try/except with `logger.warning()` on failure
- Redis keys: `analytics:live:{name}` (hash), `analytics:daily:{YYYY-MM-DD}` (hash, 30-day TTL)

### 2. Build CLI Export Command
- **Task ID**: build-cli-export
- **Depends On**: build-analytics-core
- **Validates**: `tests/unit/test_analytics_cli.py` (create)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/analytics.py` as CLI entry point (`python -m tools.analytics`)
- Subcommands: `export --days N --format json`, `summary`, `rollup`
- Export produces JSON with metric summaries, daily breakdowns, and metadata
- Summary prints a human-readable overview to stdout

### 3. Instrument Existing Code Paths
- **Task ID**: build-instrumentation
- **Depends On**: build-analytics-core
- **Validates**: `tests/unit/test_analytics_instrumentation.py` (create)
- **Assigned To**: instrumentation-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-cli-export)
- Add `record_metric("session.cost_usd", cost, {"session_id": session_id})` to `agent/sdk_client.py` after the `msg.total_cost_usd` check (line ~1092)
- Add `record_metric("session.turns", turns, {"session_id": session_id})` alongside cost
- Add `record_metric("session.started", 1, {"session_type": ..., "project_key": ...})` to session lifecycle start
- Add `record_metric("session.completed", 1, {"session_type": ..., "status": ...})` to session lifecycle completion
- Add `record_metric("sdlc.stage_started", 1, {"stage": stage_name})` to `PipelineStateMachine.start_stage()`
- Add `record_metric("sdlc.stage_completed", 1, {"stage": stage_name})` to `PipelineStateMachine.complete_stage()`
- Add `record_metric("memory.recall_attempt", 1, ...)` to memory retrieval path
- Add `record_metric("memory.extraction", count, ...)` to memory extraction path
- Add `record_metric("health.check", 1, {"component": ..., "status": ...})` to health checker
- Every call site must import lazily (`from analytics.collector import record_metric`) and wrap in try/except

### 4. Extend Dashboard with Analytics
- **Task ID**: build-dashboard
- **Depends On**: build-analytics-core
- **Validates**: manual verification of `curl localhost:8500/dashboard.json | jq .analytics`
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-instrumentation)
- Add `analytics` key to `dashboard.json` response in `ui/app.py`: `{"sessions_today": N, "sessions_7d": N, "cost_today_usd": N, "cost_7d_usd": N, "daily_sessions": [{date, count}, ...]}`
- Create `ui/data/analytics.py` module that queries the analytics store for dashboard data
- Create `ui/templates/_partials/analytics_trend.html` HTMX partial showing sessions-per-day as a simple CSS bar chart
- Add the trend partial to the main `index.html` template with HTMX polling (30-second refresh)
- Ensure dashboard renders gracefully when analytics database is empty or missing

### 5. Write Tests
- **Task ID**: build-tests
- **Depends On**: build-analytics-core, build-instrumentation, build-dashboard
- **Assigned To**: analytics-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_analytics_collector.py`: test `record_metric()` writes to SQLite, test best-effort pattern (mock SQLite failure, verify no exception), test invalid inputs
- Create `tests/unit/test_analytics_query.py`: test query functions return correct data, test empty database returns empty results
- Create `tests/unit/test_analytics_cli.py`: test export command produces valid JSON, test with empty database
- Create `tests/integration/test_analytics_dashboard.py`: test `dashboard.json` includes `analytics` key, test backward compatibility (existing keys unchanged)
- Mark integration tests with `@pytest.mark.analytics` marker

### 6. Validate All
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: analytics-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_analytics*.py -v` -- all pass
- Run `python -m ruff check analytics/ tools/analytics.py ui/data/analytics.py`
- Run `python -m ruff format --check analytics/ tools/analytics.py ui/data/analytics.py`
- Verify `python -m tools.analytics export --days 1` produces valid JSON
- Verify `dashboard.json` backward compatibility: all existing keys still present
- Verify `record_metric()` with simulated Redis failure does not raise
- Verify `record_metric()` with simulated SQLite failure does not raise

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: analytics-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/unified-analytics.md` with architecture, metric catalog, query API, CLI usage
- Add entry to `docs/features/README.md` index table
- Update `docs/features/structured-logging-telemetry.md` to note the new analytics system replaces the deleted telemetry implementation
- Add CLI command to `CLAUDE.md` Quick Commands table

### 8. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: analytics-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite `pytest tests/ -x -q`
- Verify all success criteria are met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Analytics module exists | `python -c "from analytics.collector import record_metric"` | exit code 0 |
| CLI export works | `python -m tools.analytics export --days 1` | exit code 0 |
| Dashboard backward-compatible | `python -c "import json; d=json.loads(open('/dev/stdin').read()); assert 'sessions' in d and 'health' in d" < <(curl -s localhost:8500/dashboard.json)` | exit code 0 |
| Best-effort pattern | `python -c "from analytics.collector import record_metric; record_metric('test', 1.0)"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Rollup frequency**: Should the daily rollup run as a new reflections unit (extending the existing 17 units to 18), or as a separate scheduled job via launchd? Running it within reflections keeps scheduling simple but couples analytics to the reflections cycle.

2. **Metric naming convention**: The plan uses dotted names (`session.cost_usd`, `sdlc.stage_started`). Should we follow the deleted telemetry's colon-separated convention (`telemetry:observer:decisions`) for the Redis keys only, or use dotted names consistently across both SQLite and Redis?

3. **Dashboard trend view scope**: The plan proposes sessions-per-day as the first trend chart. Should the initial dashboard view also include cost-per-day, or is sessions-per-day sufficient for v1?
