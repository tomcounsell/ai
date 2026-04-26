# Sustainable Self-Healing

Queue governance layer that keeps the Valor AI system running reliably under API outages, runaway session storms, and repeated failure loops. Implemented as five reflection functions registered in `config/reflections.yaml`.

## What It Does

### 1. Circuit-Gated Queue Pause and Hibernation (`circuit_health_gate`)

When the Anthropic circuit breaker transitions to OPEN or HALF_OPEN state, the session queue is paused immediately and the worker enters hibernation. Both `queue_paused` and `worker:hibernating` flags are managed atomically in a single reflection. On recovery, both `recovery:active` and `worker:recovering` flags are set to trigger the drip resume.

- **Interval:** 60s
- **Redis keys written:** `{project_key}:sustainability:queue_paused`, `{project_key}:worker:hibernating`, `{project_key}:recovery:active`, `{project_key}:worker:recovering`
- **Queue guard:** `_pop_agent_session()` in `agent/agent_session_queue.py` checks both `queue_paused` and `worker:hibernating` with OR logic
- **Notification:** Sends Telegram notification on first transition into/out of hibernation

### 2. Unified Drip Resume (`session_recovery_drip`)

When either `recovery:active` or `worker:recovering` is set (circuit just closed), resumes paused sessions at a controlled rate — one session every 30 seconds. `paused_circuit` sessions (blocked before dequeue) are dripped first; `paused` sessions (interrupted mid-execution) are dripped second. Clears both recovery flags when both queues drain.

- **Interval:** 30s
- **Session statuses:** `paused_circuit` (priority), then `paused` (non-terminal, restorable)

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

- **Interval:** 3600s (60 minutes)
- **Redis key:** `{project_key}:sustainability:seen_fingerprints` (TTL 7 days)
- **Skips:** during active API outage (`queue_paused` set)

### 5. Daily Health Digest (`sustainability_digest` / `system-health-digest`)

Enqueues a dev-role AgentSession that generates and sends a daily Telegram health summary to the `Dev: Valor` chat. The digest includes circuit state, throttle level, session counts, and active failure cluster count.

Circuit states are translated to plain-language labels in the agent session prompt — the LLM is instructed to report `OK` (closed), `DOWN` (open), and `RECOVERING` (half-open) rather than the raw internal enum values. This covers both the lowercase `.value` strings (`closed`, `open`, `half_open`) and the uppercase enum names (`CLOSED`, `OPEN`, `HALF_OPEN`).

- **Interval:** 86400s (daily)

## Redis Key Schema

All keys are scoped under `{project_key}:sustainability:*` except the recovery flag:

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `{pk}:sustainability:queue_paused` | string | 3600s | Circuit is OPEN — queue frozen |
| `{pk}:sustainability:throttle_level` | string | 7200s | `none` / `moderate` / `suspended` |
| `{pk}:sustainability:seen_fingerprints` | set | 7 days | Deduplicate failure-loop issues |
| `{pk}:recovery:active` | string | 3600s | Drip resume in progress |

`{pk}` = `$VALOR_PROJECT_KEY` (default: `"valor"`).

The env var is injected into the worker and bridge launchd plists by the install scripts (`scripts/install_worker.sh`, `scripts/update/service.py::install_worker`, and `scripts/remote-update.sh`). Empty/whitespace values fall back to `"valor"` (the canonical AgentSession writer default at `tools/agent_session_scheduler.DEFAULT_PROJECT_KEY`). See issue #1171.

## Session Lifecycle

A new non-terminal status `paused_circuit` was added to `models/session_lifecycle.py`:

- Listed in `NON_TERMINAL_STATUSES` — session can be transitioned back to `pending`
- Set by `api_health_gate` when the circuit opens (future: not yet auto-set; the gate currently only blocks new dequeues)
- Cleared by `recovery_drip` → transitions to `pending`

## Registered Reflections

In `config/reflections.yaml`:

```yaml
- name: circuit-health-gate
  callable: agent.sustainability.circuit_health_gate
  interval: 60

- name: session-count-throttle
  callable: agent.sustainability.session_count_throttle
  interval: 3600

- name: failure-loop-detector
  callable: agent.sustainability.failure_loop_detector
  interval: 3600

- name: session-recovery-drip
  callable: agent.sustainability.session_recovery_drip
  interval: 30

- name: system-health-digest
  execution_type: agent
  interval: 86400
```

## Verifying It Works

**Check queue pause state:**
```bash
redis-cli GET "${VALOR_PROJECT_KEY:-valor}:sustainability:queue_paused"
```

**Check throttle level:**
```bash
redis-cli GET "${VALOR_PROJECT_KEY:-valor}:sustainability:throttle_level"
```

**Check seen failure fingerprints:**
```bash
redis-cli SMEMBERS "${VALOR_PROJECT_KEY:-valor}:sustainability:seen_fingerprints"
```

**Check recovery flag:**
```bash
redis-cli EXISTS "${VALOR_PROJECT_KEY:-valor}:recovery:active"
```

**Run reflections manually:**
```bash
python scripts/reflections.py  # runs all registered reflections
```

**Run a specific reflection:**
```python
from agent.sustainability import circuit_health_gate
circuit_health_gate()
```

## Test Coverage

Unit tests in `tests/unit/test_sustainability.py`:
- `circuit_health_gate`: OPEN/HALF_OPEN → both flags set; CLOSED → both flags cleared + recovery keys + notification; neither was set → no-op; unregistered circuit → no-op; exception guard
- `session_recovery_drip`: paused_circuit-first priority; paused fallback; both queues empty → both flags cleared; neither flag set → no-op; one-per-tick; exception guard
- `_pop_agent_session`: queue_paused set → returns None
- `failure_loop_detector`: 3+ same-fingerprint failures → issue filed; already seen → no duplicate; < 3 → no issue
- `sustainability_digest`: anomaly string uses plain language (no raw enum names); agent prompt maps all six circuit state forms to `OK`/`DOWN`/`RECOVERING`
