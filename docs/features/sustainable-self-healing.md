# Sustainable Self-Healing

Queue governance layer that keeps the Valor AI system running reliably under API outages, runaway session storms, and repeated failure loops. Implemented as five reflection functions registered in `config/reflections.yaml`.

## What It Does

### 1. Circuit-Gated Queue Pause (`api_health_gate`)

When the Anthropic circuit breaker transitions to OPEN or HALF_OPEN state, the session queue is paused immediately. No new sessions are dequeued until the circuit closes. On recovery, a `recovery:active` flag is set to trigger the drip resume.

- **Interval:** 60s
- **Redis keys written:** `{project_key}:sustainability:queue_paused`, `{project_key}:recovery:active`
- **Queue guard:** `_pop_agent_session()` in `agent/agent_session_queue.py` checks `queue_paused` on every dequeue attempt

### 2. Drip Resume (`recovery_drip`)

When `recovery:active` is set (circuit just closed), resumes sessions paused with status `paused_circuit` at a controlled rate — one session every 30 seconds. Clears the `recovery:active` flag when the queue drains.

- **Interval:** 30s
- **Session status:** `paused_circuit` (non-terminal, restorable)

### 3. Session-Count Throttle (`session_count_throttle`)

Counts sessions started in the last hour. Writes a `throttle_level` to Redis:

| Level | Condition | Effect |
|-------|-----------|--------|
| `none` | < MODERATE threshold | Normal operation |
| `moderate` | >= MODERATE (default 20/hr) | Low-priority sessions blocked |
| `suspended` | >= SUSPENDED (default 40/hr) | Normal + low-priority sessions blocked |

- **Interval:** 3600s (hourly)
- **Redis key:** `{project_key}:sustainability:throttle_level` (TTL 2hr)
- **Env vars:** `SUSTAINABILITY_THROTTLE_MODERATE`, `SUSTAINABILITY_THROTTLE_SUSPENDED`

### 4. Failure-Loop Deduplication (`failure_loop_detector`)

Scans failed/abandoned sessions from the last 4 hours. Groups them by error fingerprint (HTTP status code or exception type + error message prefix). When a cluster reaches 3+ sessions:

1. Checks `{project_key}:sustainability:seen_fingerprints` Redis set via `SADD` (atomic check-and-set)
2. If new: files one GitHub issue via `gh issue create --label bug`
3. Marks affected sessions with `extra_context["loop_detected"] = True`

- **Interval:** 1800s (30 minutes)
- **Redis key:** `{project_key}:sustainability:seen_fingerprints` (TTL 7 days)
- **Skips:** during active API outage (`queue_paused` set)

### 5. Daily Health Digest (`sustainability_digest`)

Enqueues a dev-role AgentSession that generates and sends a daily Telegram health summary to the `Dev: Valor` chat. The digest includes circuit state, throttle level, session counts, and active failure cluster count.

- **Interval:** 86400s (daily)

## Redis Key Schema

All keys are scoped under `{project_key}:sustainability:*` except the recovery flag:

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `{pk}:sustainability:queue_paused` | string | 3600s | Circuit is OPEN — queue frozen |
| `{pk}:sustainability:throttle_level` | string | 7200s | `none` / `moderate` / `suspended` |
| `{pk}:sustainability:seen_fingerprints` | set | 7 days | Deduplicate failure-loop issues |
| `{pk}:recovery:active` | string | 3600s | Drip resume in progress |

`{pk}` = `$VALOR_PROJECT_KEY` (default: `"default"`)

## Session Lifecycle

A new non-terminal status `paused_circuit` was added to `models/session_lifecycle.py`:

- Listed in `NON_TERMINAL_STATUSES` — session can be transitioned back to `pending`
- Set by `api_health_gate` when the circuit opens (future: not yet auto-set; the gate currently only blocks new dequeues)
- Cleared by `recovery_drip` → transitions to `pending`

## Registered Reflections

In `config/reflections.yaml`:

```yaml
- name: api-health-gate
  function: agent.sustainability:api_health_gate
  interval: 60

- name: session-count-throttle
  function: agent.sustainability:session_count_throttle
  interval: 3600

- name: failure-loop-detector
  function: agent.sustainability:failure_loop_detector
  interval: 1800

- name: recovery-drip
  function: agent.sustainability:recovery_drip
  interval: 30

- name: sustainability-digest
  function: agent.sustainability:sustainability_digest
  interval: 86400
```

## Verifying It Works

**Check queue pause state:**
```bash
redis-cli GET "${VALOR_PROJECT_KEY:-default}:sustainability:queue_paused"
```

**Check throttle level:**
```bash
redis-cli GET "${VALOR_PROJECT_KEY:-default}:sustainability:throttle_level"
```

**Check seen failure fingerprints:**
```bash
redis-cli SMEMBERS "${VALOR_PROJECT_KEY:-default}:sustainability:seen_fingerprints"
```

**Check recovery flag:**
```bash
redis-cli EXISTS "${VALOR_PROJECT_KEY:-default}:recovery:active"
```

**Run reflections manually:**
```bash
python scripts/reflections.py  # runs all registered reflections
```

**Run a specific reflection:**
```python
from agent.sustainability import api_health_gate
api_health_gate()
```

## Test Coverage

Unit tests in `tests/unit/test_sustainability.py`:
- `api_health_gate`: OPEN → pause, CLOSED → clear + recovery, unregistered circuit → no-op
- `_pop_agent_session`: queue_paused set → returns None
- `recovery_drip`: transitions session, clears flag when empty, no-op when flag absent
- `failure_loop_detector`: 3+ same-fingerprint failures → issue filed; already seen → no duplicate; < 3 → no issue
