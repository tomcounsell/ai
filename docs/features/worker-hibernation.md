# Worker Hibernation

Worker hibernation is a mid-execution pause-and-resume mechanism that prevents tight failure loops when the Anthropic API goes down. When a `CircuitOpenError` interrupts an in-flight session, the worker transitions the session to a preserved `paused` state and sets a Redis flag that blocks further session dequeuing until the circuit recovers.

Tracking issue: [#839](https://github.com/tomcounsell/ai/issues/839)

## Problem

Before this feature, an Anthropic API outage mid-execution caused the worker to leave the in-flight session in `running` state and exit the loop. Startup recovery would re-queue it to `pending`, the worker would pick it up again, and the session would fail immediately — a tight failure loop with no backoff.

## Solution

Two reflections plus a catch-block patch create a coordinated hibernate-and-drip cycle:

1. `_worker_loop()` catches `CircuitOpenError`, transitions the session to `paused`, and writes `{project_key}:worker:hibernating` (TTL 600s)
2. `_pop_agent_session()` checks the flag before acquiring the pop lock — returns `None` if set
3. `worker-health-gate` (60s reflection) checks the Anthropic circuit; when CLOSED, clears `hibernating` and writes `worker:recovering`
4. `session-resume-drip` (30s reflection) transitions one `paused` session to `pending` per tick until the queue drains

## Hibernation Flow

```
Anthropic API down
  → CircuitOpenError raised in sdk_client.py
  → _worker_loop catch: transition_status(session, "paused") + write worker:hibernating (TTL 600s)
  → Telegram notification enqueued: "Worker hibernating"
  → _pop_agent_session reads hibernating flag → returns None
  → worker waits (no sessions consumed)

Anthropic API recovers
  → worker-health-gate (60s): circuit CLOSED → delete hibernating, write recovering (TTL 3600s)
  → Telegram notification enqueued: "Worker waking"
  → session-resume-drip (30s): one paused → pending per tick (~2 sessions/min)
  → when paused list empty: delete recovering flag
```

## Redis Key Schema

| Key | TTL | Writer | Reader |
|-----|-----|--------|--------|
| `{project_key}:worker:hibernating` | 600s | `_worker_loop()` catch, `worker-health-gate` | `_pop_agent_session()`, `worker-health-gate` |
| `{project_key}:worker:recovering` | 3600s | `worker-health-gate` | `session-resume-drip` |

The 600s TTL on `worker:hibernating` is a safety valve: if the reflection scheduler stops running, the flag expires automatically and the worker resumes popping sessions rather than staying blocked indefinitely.

## `paused` Status Semantics

`paused` is a non-terminal session status meaning: the session was interrupted mid-execution by a transient external failure. Full session context is preserved in Redis. The session is not terminal — it will be re-queued when the failure clears.

Contrast with `paused_circuit` (added by issue #773): `paused_circuit` means the session was blocked before it ever started executing (blocked at the dequeue gate). `paused` means execution started and was interrupted.

| Status | Meaning | Written By | Restored By |
|--------|---------|-----------|-------------|
| `paused_circuit` | Blocked before dequeue (circuit gate) | `api_health_gate` (sustainability.py) | `recovery_drip` (sustainability.py) |
| `paused` | Interrupted mid-execution (circuit open) | `_worker_loop()` catch block | `session_resume_drip` (hibernation.py) |

## Relationship to Sustainable Self-Healing (#773)

Issue #773 adds a separate but complementary queue governance system:

| Concern | #773 (sustainability.py) | #839 (hibernation.py) |
|---------|--------------------------|----------------------|
| Status | `paused_circuit` | `paused` |
| Redis gate key | `{pk}:sustainability:queue_paused` (TTL 3600s) | `{pk}:worker:hibernating` (TTL 600s) |
| Gate written by | `api_health_gate` reflection | `_worker_loop()` catch + `worker_health_gate` reflection |
| Drip recovery | `recovery_drip` (30s) | `session_resume_drip` (30s) |
| Recovery flag | `{pk}:recovery:active` | `{pk}:worker:recovering` |

`_pop_agent_session()` checks both flags with OR logic — if either is set, the pop returns `None`. The two recovery drips are independent: each only transitions its own status type.

## Reflections

Both reflections are registered in `config/reflections.yaml`:

```yaml
- name: worker-health-gate
  interval: 60  # 1 minute
  priority: high
  callable: "agent.hibernation.worker_health_gate"

- name: session-resume-drip
  interval: 30  # 30 seconds
  priority: high
  callable: "agent.hibernation.session_resume_drip"
```

## Telegram Notifications

On hibernation entry and on wake, a lightweight `teammate` session is enqueued to send a notification to the `Dev: Valor` chat. Notification enqueue is best-effort: if it fails, the flag write and status transition still complete.

## Implementation

- `agent/hibernation.py` — `worker_health_gate()`, `session_resume_drip()`, `send_hibernation_notification()`
- `agent/agent_session_queue.py` — `_pop_agent_session()` hibernation guard, `_worker_loop()` catch block update
- `models/session_lifecycle.py` — `"paused"` added to `NON_TERMINAL_STATUSES`
- `config/reflections.yaml` — two new reflection entries

## Verification

```bash
# Check paused status is registered
python -c "from models.session_lifecycle import NON_TERMINAL_STATUSES; assert 'paused' in NON_TERMINAL_STATUSES"

# Check module imports cleanly
python -c "from agent.hibernation import worker_health_gate, session_resume_drip"

# Check reflections are registered
python -c "from agent.reflection_scheduler import load_registry; r=load_registry(); names=[e.name for e in r]; assert 'worker-health-gate' in names; assert 'session-resume-drip' in names"

# List paused sessions
python -m tools.valor_session list --status paused
```
