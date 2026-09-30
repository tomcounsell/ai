# Session Telemetry

Per-event JSONL telemetry trace for agent sessions. Part of epic #1536 (v1 delivery).

## What It Is and Why It Exists

Session telemetry is an append-only, structured event log written to disk for every agent session. It provides durable, per-event visibility into what happened inside a session: when turns started and ended, which tools were called, how many tokens were consumed, when the session changed state, and where time was lost to idle gaps.

Before this feature, diagnosing a stuck or failed session meant reading unstructured logs and reconstructing a timeline manually. Telemetry makes that reconstruction automatic and machine-readable. It is the foundation for the learning, classification, and crash-resume sub-issues (#1538, #1539, #1540) in the parent epic.

## JSONL Sink

Each session gets its own file:

```
logs/session_telemetry/{session_id}.jsonl
```

Events are appended one JSON line at a time. The file is flushed after every write. Files are retained for 14 days; the `agent-session-cleanup` reflection deletes older files.

## Event Schema (v1)

Every event carries two universal fields in addition to its type-specific payload:

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | The session this event belongs to |
| `ts` | string | ISO-8601 UTC timestamp (e.g. `"2026-06-16T10:00:00.123456Z"`) |

### `turn_start`

Marks the beginning of a new turn.

```json
{"session_id": "...", "ts": "...", "type": "turn_start"}
```

### `turn_end`

Emitted from the stream-json result event at the end of a turn.

```json
{"session_id": "...", "ts": "...", "type": "turn_end"}
```

### `tool_use`

Records a tool invocation. Duration is best-effort (derived from surrounding timestamps; may be absent if timing context is unavailable).

```json
{"session_id": "...", "ts": "...", "type": "tool_use", "name": "Bash", "duration_seconds": 1.234}
```

### `token_usage`

Raw per-turn token consumption plus a derived total cost estimate.

```json
{
  "session_id": "...",
  "ts": "...",
  "type": "token_usage",
  "usage": {
    "input_tokens": 1024,
    "output_tokens": 256,
    "cache_read_input_tokens": 512
  },
  "total_cost_usd": 0.0042
}
```

### `idle_gap`

Synthetic event. Emitted automatically when the inter-event gap exceeds 60 seconds. Written immediately before the next real event so that periods of inactivity are visible in the timeline without altering the real event sequence.

```json
{"session_id": "...", "ts": "...", "type": "idle_gap", "gap_seconds": 127.4}
```

**Important:** idle gaps are recorded facts for diagnosability. They are NEVER used as kill signals or stall indicators. Session termination decisions belong to the health monitor (see issue #1172).

### `status_transition`

Emitted on every session state machine transition.

```json
{
  "session_id": "...",
  "ts": "...",
  "type": "status_transition",
  "from": "running",
  "to": "killed",
  "reason": "operator request",
  "kill": {
    "confirmed_dead": true,
    "signal_sent": true,
    "pid": 12345
  }
}
```

The `kill` field is `null` for non-kill transitions.

### `telemetry_truncated`

Emitted once when the 10,000-event per-session cap is reached. No further events are written for that session after this marker appears.

```json
{"session_id": "...", "ts": "...", "type": "telemetry_truncated"}
```

### `unknown`

Emitted when an event arrives with an absent or empty `type` field. The original payload is preserved verbatim under `raw`.

```json
{"session_id": "...", "ts": "...", "type": "unknown", "raw": {"original": "payload"}}
```

## CLI Consumer

Read a session's telemetry timeline:

```bash
python -m tools.valor_session telemetry --id <ID>
python -m tools.valor_session telemetry --id <ID> --json
python -m tools.valor_session telemetry --id <ID> --tail 50
```

`--json` emits raw JSONL. `--tail N` returns only the last N events (useful for long-running sessions near the cap).

## Retention

Files older than 14 days are deleted by the `agent-session-cleanup` hourly reflection. This is a hard delete — there is no archive step.

## Limits and Guarantees

**Per-session cap:** 10,000 events. When the cap is reached, a `telemetry_truncated` marker is written and the file is closed to further writes. The cap includes the truncation marker itself.

**Fail-silent:** `record_telemetry_event` never raises. Any internal exception is caught and logged at DEBUG. The telemetry system never crashes the session executor or the parse loop.

**Idle gap insertion:** when the gap between two consecutive events exceeds 60 seconds, a synthetic `idle_gap` event is prepended automatically. This happens inside the write lock, before the real event is written.

## Concurrency Model

A per-session `threading.Lock` governs all state mutations for that session. The lock dict (`_locks`) uses `dict.setdefault`, which is GIL-atomic in CPython — two threads racing on the same `session_id` both get the same lock object. All file writes, monotonic timestamp updates, and handle-cache evictions happen inside the lock.

Up to 50 JSONL file handles are kept open simultaneously (LRU eviction when the limit is reached). Eviction flushes and closes the least-recently-used handle.

## Explicit Non-Goal

Idle gaps are purely observational. They identify where time was spent; they do not trigger any action. Kill decisions belong to the session health monitor (`agent/session_health.py`). This separation was established in issue #1172 and is enforced at the design level — the telemetry recorder has no write path to session status.

## Related

The live telemetry trace is read by the [Stall Advisory Classifier](stall-advisory-classifier.md) (Pillar 1 of #1536) to derive a per-session advisory verdict for running sessions. The classifier's `classify_session_stall()` function reads `turn_start`, `idle_gap`, and `status_transition` events from this JSONL file.

The recorder also carries two interaction event types written by the [TUI Interaction Capture](tui-interaction-capture.md) feature (Pillar 3 of #1536): `slash_command` and `human_steering`. These are appended by `agent/tui_interaction_capture.py::capture_prompt_event()` via the existing `record_telemetry_event` path. At session end, `summarize_and_store()` reads the full timeline with `read_session_timeline()` and tallies `tool_use` events alongside the two new types to produce one `pattern` Memory per session.

## Related Issues

- **Epic #1536** — Session Telemetry (this is v1)
- **#1538** — Stall Advisory Classifier (Pillar 1) — reads this trace for live verdicts
- **#1539** — Crash-Signature Auto-Resume (Pillar 2) — reads this trace for terminal session signatures
- **#1540** — TUI Interaction Capture (Pillar 3) — writes `slash_command` / `human_steering` events; reads `tool_use` events at summarize time
