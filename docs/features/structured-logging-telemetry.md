# Structured Logging and Telemetry

## Overview

The structured logging and telemetry system provides observability for the Observer Agent and SDLC pipeline. It covers three layers: structured log lines for grep-based debugging, Redis-backed counters for metrics, and health checks for alerting.

## Architecture

```
Observer decisions    ---> structured log lines (grep-friendly)
                      ---> Redis HINCRBY counters (metrics)
                      ---> monitoring/alerts.py threshold checks (alerting)

Stage transitions     ---> structured log lines
                      ---> Redis HINCRBY counters

Link enforcement      ---> structured LINK log lines
Human interjections   ---> structured INTERJECTION log lines
                      ---> Redis LPUSH event list (capped at 100)
```

## Structured Log Formats

All structured log lines follow the pattern: `EVENT_TYPE key=value key=value`

### LINK (set_link in AgentSession)

```
LINK session={session_id} correlation={cid} type={issue|plan|pr} action={set|update} url={url}
```

Emitted at INFO level when `AgentSession.set_link()` stores an issue, plan, or PR URL.

### INTERJECTION (Observer)

```
[{correlation_id}] INTERJECTION session={session_id} correlation={cid} count={N} action={read|cleared}
```

Emitted at INFO level when the Observer reads or clears queued human steering messages.

### Decision Logging (Observer)

```
[{correlation_id}] Decision: steer|deliver (reason: {reason_preview})
```

Emitted at INFO level after the Observer makes a routing decision.

### Stage Transition Logging (Stage Detector)

```
[stage-detector] Applied {STAGE} -> {status}: {reason}
```

Emitted at INFO level when a stage transition is applied to a session.

## Redis Telemetry Keys

| Key | Type | Description |
|-----|------|-------------|
| `telemetry:observer:decisions` | Hash | `steer_count`, `deliver_count`, `error_count` |
| `telemetry:pipeline:completions` | Hash | `{stage}_started`, `{stage}_completed` per stage |
| `telemetry:observer:tool_usage` | Hash | Per-tool invocation counts |
| `telemetry:interjections` | List | Last 100 interjection events as JSON (LPUSH + LTRIM) |
| `telemetry:daily:{YYYY-MM-DD}` | Hash | Daily rollup of all counters, 7-day TTL |

### Querying Telemetry

```bash
# View decision counters
redis-cli HGETALL telemetry:observer:decisions

# View pipeline stage completions
redis-cli HGETALL telemetry:pipeline:completions

# View tool usage
redis-cli HGETALL telemetry:observer:tool_usage

# View recent interjections
redis-cli LRANGE telemetry:interjections 0 9

# View daily rollup
redis-cli HGETALL telemetry:daily:2026-03-10
```

### Python API

**Note:** The `monitoring/telemetry.py` module was removed as part of issue #488 (SDLC stage consolidation). The Observer was removed in SDLC Redesign Phase 2, making the telemetry module orphaned. Redis telemetry keys listed above may still exist in Redis but are no longer written to or read by any code.

## Design Decisions

- **Best-effort telemetry**: All Redis writes are wrapped in try/except. Telemetry failures never break the Observer or pipeline.
- **Daily rollup with TTL**: Daily keys expire after 7 days to prevent unbounded Redis key growth.
- **Event list capping**: Interjection events are capped at 100 entries via LTRIM.
- **Inline imports**: Modules use inline imports to avoid circular imports and keep imports lightweight.
- **No external services**: Telemetry stays in Redis + logs. No Datadog, Grafana, or other external dependencies.

## Files

| File | Role |
|------|------|
| `agent/agent_session_queue.py` | Calls record_decision for routing (delegates to `agent/output_router.py`) |
| `bridge/pipeline_state.py` | State machine logs stage transitions |
| `models/agent_session.py` | Structured LINK logging in set_link() |
| `monitoring/health.py` | Health checks (observer telemetry check removed) |

## Related

- Issue: [#319](https://github.com/tomcounsell/ai/issues/319)
- Plan: `docs/plans/structured_logging_telemetry.md`
- [Correlation IDs](correlation-ids.md) -- the tracing layer that telemetry builds on
