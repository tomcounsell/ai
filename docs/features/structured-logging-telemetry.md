# Structured Logging and Telemetry

## Overview

Redis-backed telemetry system for the Observer Agent and SDLC pipeline. Records decisions, stage transitions, tool usage, and human interjections with structured log entries and queryable counters.

Built on top of [Correlation IDs](correlation-ids.md) for end-to-end request tracing.

## Components

### Structured Log Events

All log events use a parseable `KEY=VALUE` format with correlation ID prefix:

| Event | Format | Source |
|-------|--------|--------|
| Observer decision | `[{cid}] Decision: steer\|deliver (reason: ...)` | `bridge/observer.py` |
| Stage transition | `[stage-detector] Applied {stage} -> {status}: {reason}` | `bridge/stage_detector.py` |
| Link enforcement | `LINK session={id} correlation={cid} type={kind} action=set url={url}` | `models/agent_session.py` |
| Human interjection | `[{cid}] INTERJECTION session={id} correlation={cid} count={N} action=read\|cleared` | `bridge/observer.py` |
| Tool use | `[{cid}] Iteration {N}/{MAX}: tool={name}, result={preview}` | `bridge/observer.py` |

### Redis Telemetry (`monitoring/telemetry.py`)

Counters and event lists stored in Redis:

| Key | Type | Description |
|-----|------|-------------|
| `telemetry:observer:decisions` | Hash | `steer_count`, `deliver_count`, `error_count` |
| `telemetry:pipeline:completions` | Hash | `{STAGE}_completed`, `{STAGE}_started` per stage |
| `telemetry:observer:tool_usage` | Hash | Per-tool invocation counts |
| `telemetry:interjections` | List | Last 100 interjection events (JSON) |
| `telemetry:daily:{YYYY-MM-DD}` | Hash | Daily rollup of all counters (7-day TTL) |

### Health Check Integration

`monitoring/health.py` includes an observer telemetry check in the overall health assessment:

- **ok**: Error rate < 10%
- **degraded**: Error rate 10-25%
- **unhealthy**: Error rate > 25%

## API

```python
from monitoring.telemetry import (
    record_decision,
    record_stage_transition,
    record_tool_use,
    record_interjection,
    get_summary,
    check_observer_health,
)

# Record events (all non-blocking, never raise)
record_decision(session_id, correlation_id, "steer", "reason")
record_stage_transition(session_id, correlation_id, "BUILD", "pending", "completed")
record_tool_use(session_id, correlation_id, "read_session")
record_interjection(session_id, correlation_id, message_count=2, action="cleared")

# Query
summary = get_summary()  # All counters + recent interjections
health = check_observer_health()  # Status + error_rate + violations
```

## Design Decisions

- **Best-effort writes**: All telemetry calls are wrapped in try/except. The observer never fails due to telemetry. Redis unavailability silently degrades to no metrics.
- **Atomic counters**: Uses Redis HINCRBY for thread-safe concurrent updates.
- **Bounded storage**: Interjection list capped at 100 entries via LTRIM. Daily keys expire after 7 days.
- **No external services**: Metrics stay in Redis — no Datadog, Grafana, or external telemetry services.

## Querying Metrics

```bash
# Quick summary
python -c "from monitoring.telemetry import get_summary; import json; print(json.dumps(get_summary(), indent=2))"

# Health check
python -c "from monitoring.telemetry import check_observer_health; print(check_observer_health())"

# Raw Redis inspection
redis-cli hgetall telemetry:observer:decisions
redis-cli hgetall telemetry:pipeline:completions
redis-cli lrange telemetry:interjections 0 9
```
