# Bridge Resilience: Graceful Degradation for Dependency Outages

The bridge resilience system prevents crash-loops and resource waste during dependency outages. It uses circuit breakers to detect sustained failures, exponential backoff for startup retries, and degraded mode to preserve messages when the AI backend is unavailable.

## Problem

When an external dependency (Telegram API, Anthropic API, Redis) becomes temporarily unavailable, the bridge would crash immediately and rely on launchd to restart -- creating tight crash-loops that waste resources and delay recovery. SDK retries had no backoff. Reflections tasks crashed with full tracebacks on missing prerequisites.

## Architecture

### Circuit Breaker Pattern (`bridge/resilience.py`)

The `CircuitBreaker` class tracks failures per dependency using a rolling time window. When failures exceed a threshold, the circuit opens and calls are short-circuited immediately, preventing resource waste.

**States:**

| State | Behavior |
|-------|----------|
| CLOSED | Normal operation. Failures are counted within the rolling window. |
| OPEN | Dependency is down. Calls fail immediately without attempting the operation. |
| HALF_OPEN | Probing recovery. One call is allowed through to test if the dependency has recovered. |

**Transitions:**

- CLOSED -> OPEN: Failure count reaches `failure_threshold` within `window_seconds`
- OPEN -> HALF_OPEN: `probe_interval_seconds` has elapsed since the circuit opened
- HALF_OPEN -> CLOSED: A probe call succeeds (clears failure history)
- HALF_OPEN -> OPEN: A probe call fails (resets the probe timer)

**Configuration:**

```python
from bridge.resilience import CircuitBreaker

cb = CircuitBreaker(
    name="anthropic",
    failure_threshold=5,      # 5 failures to trip
    window_seconds=60.0,      # within a 60-second window
    probe_interval_seconds=30.0,  # probe every 30s when open
    on_open=lambda: logger.warning("Anthropic circuit opened"),
    on_close=lambda: logger.info("Anthropic circuit recovered"),
)
```

**Thread safety:** State transitions use `asyncio.Lock`. State reads (`is_closed()`, `is_open()`) are lock-free since Python's GIL ensures atomic reads of enum values.

### Dependency Health Tracking (`bridge/health.py`)

The `DependencyHealth` class aggregates circuit breaker states into a unified health summary. A module-level singleton (`get_health()`) ensures all parts of the bridge share one instance.

**Overall status derivation:**

| Condition | Status |
|-----------|--------|
| All circuits closed | `healthy` |
| Some circuits open | `degraded` |
| All circuits open | `down` |

**Usage:**

```python
from bridge.health import get_health

health = get_health()
health.register(anthropic_cb)
health.register(telegram_cb)

# Structured summary for programmatic consumption
summary = health.summary()
# {"overall": "degraded", "circuits": {"anthropic": {...}, "telegram": {...}}}

# Human-readable string for logging
status = health.formatted_status()
# "degraded | anthropic:open(5 failures) telegram:closed"
```

**Cross-process note:** The watchdog runs as a separate process and cannot access in-memory circuit state. If cross-process health sharing is needed, the `summary()` output can be written to a file (e.g., `data/health.json`) for the watchdog to read.

### Startup Retry with Exponential Backoff (`bridge/telegram_bridge.py`)

The bridge startup replaces the previous SQLite-only retry with a general connection retry that covers all Telethon error types. The backoff schedule uses exponential delays with jitter to prevent thundering herd:

- **8 attempts** with delays: 2s, 4s, 8s, 16s, 32s, 64s, 128s, 256s (capped)
- **Jitter:** Random component added to each delay to prevent synchronized retries
- **Clean exit:** After all attempts exhaust, the bridge exits cleanly for launchd restart at its normal interval

### SDK Circuit Breaker (`agent/sdk_client.py`)

The SDK client wraps Anthropic API calls with a circuit breaker instance. When the Anthropic circuit opens due to sustained failures, the bridge enters degraded mode rather than continuing to hammer a failing API.

### Degraded Mode (`bridge/telegram_bridge.py`)

When the Anthropic circuit breaker is open:

1. Incoming Telegram messages are acknowledged with a brief status message
2. Messages are persisted to the dead-letter queue for later replay
3. When the circuit closes (Anthropic recovers), queued messages are replayed automatically

This ensures no messages are lost during an Anthropic outage.

### Reflections Pre-Flight Checks (`scripts/reflections.py`)

Each reflection task validates prerequisites before attempting work:

- Missing config files, GitHub labels, or unreachable APIs produce a single warning log line
- No full tracebacks for expected missing prerequisites
- The reflections runner continues to the next task instead of aborting

## Watchdog Integration

The watchdog (`monitoring/bridge_watchdog.py`) can read per-dependency circuit states from the health module. This extends the existing 5-level recovery escalation with visibility into which specific dependencies are degraded, enabling more targeted recovery actions.

## Files

| File | Purpose |
|------|---------|
| `bridge/resilience.py` | Reusable `CircuitBreaker` class with rolling window failure tracking |
| `bridge/health.py` | `DependencyHealth` aggregator and module-level singleton |
| `bridge/telegram_bridge.py` | Startup retry with exponential backoff, degraded mode handler |
| `agent/sdk_client.py` | Anthropic circuit breaker integration |
| `scripts/reflections.py` | Pre-flight prerequisite validation |
| `monitoring/bridge_watchdog.py` | Health status consumption for watchdog checks |
| `tests/unit/test_circuit_breaker.py` | Circuit breaker state transition tests |
| `tests/unit/test_health.py` | Health aggregation and formatting tests |
| `tests/unit/test_reflections_preflight.py` | Pre-flight validation tests |

## Configuration

All circuit breaker thresholds are set via constructor arguments -- no environment variables or config files required. Defaults:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `failure_threshold` | 5 | Failures needed to open circuit |
| `window_seconds` | 60.0 | Rolling window for counting failures |
| `probe_interval_seconds` | 30.0 | Time between recovery probes when open |

Set `failure_threshold=0` to make a circuit always open (useful for testing).

## Related

- [Bridge Self-Healing](bridge-self-healing.md) -- crash recovery, watchdog escalation, and auto-revert (complements resilience with post-crash recovery)
- [Session Watchdog Reliability](session-watchdog-reliability.md) -- observer circuit breaker with escalating backoff (prior art for the generalized pattern)
- [Stall Retry](stall-retry.md) -- automatic retry of stalled agent sessions
