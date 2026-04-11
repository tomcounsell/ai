# Unified Analytics

## Overview

The unified analytics system collects metrics from all subsystems and stores them for historical querying. It replaces the deleted telemetry module (#753) with a dual-write architecture: SQLite for historical time-series and Redis for live counters. All metric recording is best-effort -- failures are caught and logged, never propagated to callers.

## Architecture

```
Instrumentation points          Storage                    Output
---------------------          -------                    ------

sdk_client.py           --->   SQLite (data/analytics.db) ---> CLI export (JSON)
session_lifecycle.py    --->     WAL mode, indexed         ---> Query API (Python)
pipeline_state.py       --->                               ---> Dashboard (HTMX)
memory_retrieval.py     --->   Redis (analytics:* keys)    ---> dashboard.json
memory_extraction.py    --->     Live counters              ---> Reflections rollup
crash_tracker.py        --->     Daily rollups (30d TTL)
health.py               --->
```

Each instrumentation point calls `record_metric(name, value, dimensions)` which writes to both backends independently. A failure in one backend does not affect the other.

## Metric Catalog

All metric names use dotted notation.

| Metric | Source | Description |
|--------|--------|-------------|
| `session.cost_usd` | `agent/sdk_client.py` | Token cost per session from `msg.total_cost_usd` |
| `session.turns` | `agent/sdk_client.py` | Number of turns in a session |
| `session.started` | `models/session_lifecycle.py` | Session start event (dimensions: session_type, project_key) |
| `session.completed` | `models/session_lifecycle.py` | Session completion event (dimensions: session_type, status) |
| `sdlc.stage_started` | `bridge/pipeline_state.py` | SDLC stage start (dimensions: stage) |
| `sdlc.stage_completed` | `bridge/pipeline_state.py` | SDLC stage completion (dimensions: stage) |
| `memory.recall_attempt` | `agent/memory_retrieval.py` | Memory recall attempt (dimensions: hits, project_key) |
| `memory.extraction` | `agent/memory_extraction.py` | Post-session memory extraction (dimensions: count, project_key) |
| `crash.recorded` | `monitoring/crash_tracker.py` | Crash event recorded (dimensions: service, exit_code) |
| `health.check` | `monitoring/health.py` | Health check result (dimensions: component, status) |

## Storage

### SQLite (`data/analytics.db`)

Single append-only table for historical time-series data:

```sql
CREATE TABLE metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    dimensions TEXT  -- JSON string or NULL
);

CREATE INDEX idx_metrics_name_ts ON metrics (name, timestamp);
```

WAL mode is enabled for concurrent read/write support. Each write uses a 5-second timeout. The database auto-creates on first write -- no migration step needed.

### Redis

Two key patterns under the `analytics:` prefix:

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `analytics:live:{metric_name}` | Hash | None | Live counters keyed by dimensions JSON |
| `analytics:daily:{YYYY-MM-DD}` | Hash | 30 days | Daily rollup totals per metric |

Live counters use `HINCRBYFLOAT` for atomic increments. Daily keys store both totals and counts (`{metric}` and `{metric}:count` hash fields).

## API Reference

### Collector (`analytics/collector.py`)

```python
from analytics.collector import record_metric

# Record a metric with dimensions
record_metric("session.cost_usd", 0.05, {"session_id": "abc123"})

# Record a simple counter
record_metric("session.started", 1)
```

The function validates inputs (non-empty name, numeric value) and silently skips invalid calls with a warning log.

### Query (`analytics/query.py`)

```python
from analytics.query import (
    query_metrics,
    query_daily_summary,
    query_metric_total,
    query_metric_count,
    list_metric_names,
)

# Raw events for a metric in a time range
events = query_metrics("session.cost_usd", start_time=t0, end_time=t1)

# Daily aggregates (count, total, avg per day)
daily = query_daily_summary("session.started", days=30)

# Scalar aggregates
total_cost = query_metric_total("session.cost_usd", days=7)
session_count = query_metric_count("session.started", days=1)

# List all known metrics
names = list_metric_names()
```

All query functions return sensible defaults (empty lists, zero) when the database is missing or empty.

### Rollup (`analytics/rollup.py`)

```python
from analytics.rollup import rollup_daily

result = rollup_daily()
# {"aggregated_days": 5, "purged_rows": 142, "errors": []}
```

Aggregates raw events into Redis daily summary keys and purges SQLite events older than 30 days. Runs automatically as reflections unit 15 (`analytics_rollup`).

## CLI Usage

```bash
# Export all metrics as JSON (last 30 days by default)
python -m tools.analytics export --days 30

# Print human-readable summary
python -m tools.analytics summary

# Run daily rollup manually
python -m tools.analytics rollup
```

The `export` command produces JSON with the structure:

```json
{
  "exported_at": "2026-04-11T12:00:00Z",
  "days": 30,
  "metrics": {
    "session.cost_usd": {
      "total": 12.5,
      "count": 250,
      "daily": [
        {"date": "2026-04-11", "count": 8, "total": 0.42, "avg": 0.0525}
      ]
    }
  }
}
```

## Dashboard Integration

The `dashboard.json` endpoint includes an additive `analytics` key:

```json
{
  "analytics": {
    "sessions_today": 5,
    "sessions_7d": 32,
    "cost_today_usd": 0.25,
    "cost_7d_usd": 1.80,
    "daily_sessions": [
      {"date": "2026-04-11", "count": 5, "total": 5.0, "avg": 1.0}
    ]
  }
}
```

An HTMX partial (`ui/templates/_partials/analytics_trend.html`) renders a CSS-only sessions-per-day bar chart on the main dashboard page with 30-second polling refresh. When no data is available, it displays "No analytics data yet" gracefully.

## Reflections Integration

The daily rollup runs as reflections step 15 (`analytics_rollup`) in `scripts/reflections.py`. It:

1. Reads raw events from SQLite within the retention window
2. Aggregates totals and counts per day per metric into Redis daily keys
3. Purges raw SQLite events older than 30 days to bound database growth

No separate launchd job is needed -- the existing reflections schedule handles it.

## Design Decisions

- **SQLite over Redis for history**: Redis is ephemeral; SQLite provides durable, queryable time-series without external dependencies (stdlib only).
- **Dual-write over single store**: Redis provides instant live counters for the dashboard; SQLite provides historical queries for export and trends.
- **Best-effort everywhere**: Every `record_metric()` call is independently try/excepted at both the call site and within the collector. A Redis outage does not prevent SQLite writes and vice versa.
- **Module-level connection reuse**: The SQLite connection is reused across writes within a process to minimize connection overhead. If a write fails, the connection is reset so the next call retries.
- **No external dependencies**: Uses only Python stdlib `sqlite3` and existing Redis (via Popoto). No new pip packages.
- **Lazy imports at call sites**: Instrumentation points use `from analytics.collector import record_metric` inside try/except blocks to avoid import-time failures.

## Files

| File | Role |
|------|------|
| `analytics/__init__.py` | Package init, re-exports `record_metric` |
| `analytics/collector.py` | Dual-write collector (SQLite + Redis) |
| `analytics/query.py` | Query API for historical and aggregate data |
| `analytics/rollup.py` | Daily aggregation and purge job |
| `tools/analytics.py` | CLI entry point (`python -m tools.analytics`) |
| `ui/data/analytics.py` | Dashboard data provider |
| `ui/templates/_partials/analytics_trend.html` | HTMX sessions-per-day bar chart |
| `data/analytics.db` | SQLite database (auto-created, gitignored) |

## Related

- Issue: [#854](https://github.com/tomcounsell/ai/issues/854)
- Plan: `docs/plans/unified-analytics.md`
- Predecessor: [Structured Logging & Telemetry](structured-logging-telemetry.md) (design doc; implementation was deleted in #753)
