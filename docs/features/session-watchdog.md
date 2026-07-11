---
status: Implemented
type: feature
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/44
---

# Session Watchdog

Background health monitor that checks active agent sessions every 5 minutes for signs of distress and alerts the operator via Telegram.

## Problem

Agent sessions can silently fail: the agent stops producing output, enters a loop repeating the same tool call, accumulates errors without recovering, or runs far longer than any reasonable task should take. Without monitoring, these sessions waste API credits and block progress until the operator happens to notice. Real example: a Popoto session ran for 9+ hours stuck exploring without converging.

## How It Works

The watchdog runs as an `asyncio.create_task()` in the bridge's `main()` function. Every 5 minutes it:

1. Queries all sessions with `status="active"` via `AgentSession.query.filter()`
2. For each session, reads the session log for recent activity
3. Applies four detection heuristics (silence, loop, error cascade, duration)
4. Sends a Telegram alert for any session showing issues, respecting cooldowns

The watchdog fixes problems automatically — marking stuck sessions as abandoned and crashed sessions as failed. It creates GitHub issues for problems that can't be auto-fixed. It never modifies tool history or log files.

## Detection Heuristics

### Silence Detection
Fires when `time.time() - session.updated_at > SILENCE_THRESHOLD`. Indicates the agent may have stalled. The `updated_at` field (a `DatetimeField` with `auto_now=True`, renamed from `last_activity`) is compared using `_to_timestamp()` which handles both datetime and float values. Naive datetimes (as deserialized from Popoto `SortedField`) are assumed to represent UTC — this prevents a false-stall inflation of one full UTC offset on non-UTC machines (fix: issue #777).

| Setting | Value |
|---------|-------|
| Threshold | 600s (10 minutes) |
| Severity | warning |

### Transcript Liveness Check (Smart Stall Detection)
Before killing an active session for silence, the watchdog checks the transcript file's mtime (`logs/sessions/{session_id}/transcript.txt`). Sub-agents continuously write to this file even when `updated_at` isn't updated. If the transcript was modified within the threshold, the session is left alone — it's doing productive sub-agent work.

| Setting | Value |
|---------|-------|
| Constant | `TRANSCRIPT_STALE_THRESHOLD_MIN` |
| Default | 15 minutes |
| Fallback | If transcript file is missing, assumes stale (existing logic proceeds) |

### Loop Detection
Examines recent tool use events. Creates fingerprints from `(tool_name, sorted(tool_input.items()))` and counts consecutive identical fingerprints from the end. If 5+ match, the agent is stuck.

| Setting | Value |
|---------|-------|
| Threshold | 5 consecutive identical tool calls |
| Severity | critical (when combined with other issues) |

### Error Cascade Detection
Counts `post_tool_use` events with error indicators (`error`, `exception`, `failed`, `traceback`, etc.) in the output preview. Checks the last 20 calls.

| Setting | Value |
|---------|-------|
| Threshold | 5 errors in last 20 calls |
| Window | 20 most recent post_tool_use events |
| Severity | critical (when combined with other issues) |

### Duration Detection
Fires when `time.time() - session.started_at > DURATION_THRESHOLD`. Most tasks should complete well within 2 hours.

| Setting | Value |
|---------|-------|
| Threshold | 7200s (2 hours) |
| Severity | warning |

### Severity Logic
- 1 issue detected: `warning`
- 2+ issues detected: `critical`

### ModelException Handling (Crash Guard)

When the watchdog encounters a `popoto.exceptions.ModelException` while processing a session (e.g. unique constraint violations from duplicate Redis keys, or other ORM errors from corrupted state), it marks that session as `failed` instead of logging the error and retrying every cycle. This prevents infinite retry loops caused by stale sessions left over from SDK crashes. See [Bridge Workflow Gaps](bridge-workflow-gaps.md) for the full crash guard mechanism.

## Remediation

Alerts are sent as Telegram messages to the chat where the session originated. Each alert includes session ID, project key, duration, tool call count, and a bulleted list of detected issues.

**Cooldown**: Each session has a 30-minute cooldown (`ALERT_COOLDOWN = 1800s`). After sending an alert for a session, subsequent alerts for that same session are suppressed until the cooldown expires. This prevents the operator from receiving the same alert every 5 minutes.

**Fallback**: If the Telegram client is unavailable or the send fails, the alert is logged at WARNING level and the watchdog continues.

## Automatic Loop-Break Steering (issue #1128)

When `detect_repetition` or `detect_error_cascade` fires, the watchdog no
longer just logs the finding — it automatically enqueues a targeted
steering message via `agent/steering.py::push_steering_message` tagged
`sender="watchdog"`. The message is drained at the next tool-call
boundary by the existing PostToolUse hook, so the agent receives the
correction before its next repetition of the stuck tool. A token-spend
soft-threshold alert uses the same helper to nudge sessions whose
cumulative `total_input_tokens + total_output_tokens` crosses
`TOKEN_ALERT_THRESHOLD` (default 5M) while `status == "running"`.

**Atomic per-reason cooldown.** Each trigger reason has its own Redis
cooldown key (`watchdog:steer_cooldown:<reason>:<session_id>`). The
cooldown is enforced with a single atomic `SET key "1" NX EX <ttl>` —
never a separate GET/SET — so concurrent ticks cannot double-fire.
Because the keys are reason-scoped, a repetition steer does not
suppress a parallel error-cascade or token-alert steer.

| Reason | Cooldown env var | Default TTL |
|--------|------------------|-------------|
| `repetition` | `WATCHDOG_STEER_COOLDOWN` | 900s (3 ticks) |
| `error_cascade` | `WATCHDOG_STEER_COOLDOWN` | 900s (3 ticks) |
| `token_alert` | `WATCHDOG_TOKEN_ALERT_COOLDOWN` | 3600s (1 hour) |

**Sender attribution.** Every watchdog-authored steer passes
`sender="watchdog"` so the dashboard, `valor-session status`, and the PM
steering-drain log can distinguish automated nudges from human steers.

**Delivery timing.** Steers drain at tool-call boundaries via the
existing PostToolUse hook. Operators should expect a one-tool-call delay
between detection and correction; this is acceptable because stuck
loops emit many tool calls per minute.

**Feature gate.** `WATCHDOG_AUTO_STEER_ENABLED=false` disables loop-break
steering without disabling detection (still logged at WARNING).

## User-Visible Stall Reaction (issue #1313)

When `check_stalled_sessions()` observes an active session past its stall
threshold, the watchdog also calls `_apply_stall_reaction(session)` to queue
a ⏳ reaction emoji on the user's originating Telegram message. Until #1313,
stalls were `LIFECYCLE_STALL`-logged only — the user saw silence and
assumed "agent is thinking," compounding short outages into long ones. The
existing warning log is preserved unchanged; the reaction is an *additional*
user-visible channel.

**How it's wired.** The watchdog stays Telethon-free. It writes a reaction
payload (`type: "reaction"`, `chat_id`, `reply_to`, `emoji: "⏳"`,
`session_id`, `timestamp`) directly to `telegram:outbox:{session_id}` via
`RPUSH` + `EXPIRE STALL_REACTION_OUTBOX_TTL` (3600s, matches
`OutputHandler.OUTBOX_TTL`). The payload schema is byte-for-byte identical
to `agent/output_handler.py::_build_reaction_payload`; a unit test
(`test_payload_matches_build_reaction_payload`) enforces parity. The
bridge's existing `bridge/telegram_relay.py::_send_queued_reaction` drain
delivers the reaction on its next poll.

**Idempotency.** A single atomic `SET NX EX` on
`watchdog:stall_reaction_applied:{session_id}` (TTL =
`STALL_REACTION_DEDUP_TTL` = 1 day) ensures exactly one reaction per stall
period. When the next watchdog tick observes the session in a healthy
(non-stall) state, `_clear_stall_reaction_dedup()` `DELETE`s the dedup key
so a re-stall queues a fresh reaction. There is a ≤5-minute window
(one watchdog tick) where ⏳ can briefly persist after recovery before the
next tick clears the dedup; the recovery message lands first, so this is
acceptable.

**Skip conditions** (return False, no Redis writes, no log spam): feature
flag falsy, session has no `chat_id`, no `telegram_message_id`, or no
resolvable `session_id`/`agent_session_id`.

**Failure modes.** Redis exception → fail-quiet `logger.warning`, watchdog
loop continues. Bridge relay down >`OUTBOX_TTL` → outbox key expires,
reaction lost; the warning log still fires, and the next tick re-queues
once the dedup TTL expires.

**Feature gate.** `WATCHDOG_STALL_REACTION_ENABLED=false` disables the
reaction without disabling stall detection (still logged at WARNING).
Mirrors `WATCHDOG_AUTO_STEER_ENABLED`.

## Configuration

All thresholds are module-level constants in `monitoring/session_watchdog.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `WATCHDOG_INTERVAL` | 300 (5 min) | Seconds between check cycles |
| `SILENCE_THRESHOLD` | 600 (10 min) | Inactivity before silence alert |
| `LOOP_THRESHOLD` | 5 | Consecutive identical calls to trigger |
| `ERROR_CASCADE_THRESHOLD` | 5 | Errors in window to trigger |
| `ERROR_CASCADE_WINDOW` | 20 | Number of recent calls to examine |
| `DURATION_THRESHOLD` | 7200 (2 hr) | Session age before duration alert |
| `ALERT_COOLDOWN` | 1800 (30 min) | Minimum gap between alerts per session |
| `TRANSCRIPT_STALE_THRESHOLD_MIN` | 15 | Minutes before transcript is considered stale |
| `STEER_COOLDOWN` | 900 (15 min) | Per-reason cooldown for repetition/cascade steers |
| `TOKEN_ALERT_THRESHOLD` | 5,000,000 | Soft-threshold on `input + output` tokens |
| `TOKEN_ALERT_COOLDOWN` | 3600 (1 hr) | Cooldown for token-alert steers |
| `STALL_REACTION_EMOJI` | `⏳` | Emoji queued on the user's message when a session stalls (issue #1313) |
| `STALL_REACTION_DEDUP_TTL` | 86400 (1 day) | Dedup key TTL for one-reaction-per-stall-period |
| `STALL_REACTION_OUTBOX_TTL` | 3600 | Outbox key TTL; matches `OutputHandler.OUTBOX_TTL` |

**Environment variables (issue #1128):** every constant above that
participates in loop-break / token-alert behavior is env-tunable and the
behavior itself is toggleable:

| Env var | Purpose | Default |
|---------|---------|---------|
| `WATCHDOG_AUTO_STEER_ENABLED` | Toggle auto-steer on/off | on |
| `WATCHDOG_STALL_REACTION_ENABLED` | Toggle user-visible ⏳ reaction on stall (issue #1313) | on |
| `WATCHDOG_TOKEN_TRACKING_ENABLED` | Toggle per-session token accumulation | on |
| `WATCHDOG_TOKEN_ALERT_THRESHOLD` | Soft-threshold tokens | 5000000 |
| `WATCHDOG_TOKEN_ALERT_COOLDOWN` | Token-alert cooldown (s) | 3600 |
| `WATCHDOG_STEER_COOLDOWN` | Repetition/cascade cooldown (s) | 900 |

Falsy values (case-insensitive `"0"`, `"false"`, `"no"`) disable the
gated feature. Any other value — including unset — means enabled.

**Retired in #2000:** `WATCHDOG_IDLE_TEARDOWN_ENABLED`, `WATCHDOG_IDLE_TEARDOWN_THRESHOLD_SECONDS`, and `WATCHDOG_IDLE_SWEEP_INTERVAL` gated the worker idle-sweeper, which was deleted wholesale along with the rest of the dead Claude Agent SDK path — see [HarnessAdapter Seam](harness-adapter.md).

## Integration

Started in `bridge/telegram_bridge.py` at line ~3321:

```python
from monitoring.session_watchdog import watchdog_loop
asyncio.create_task(watchdog_loop(telegram_client=client))
```

Runs for the lifetime of the bridge process. No separate service or process management needed. The existing update system restarts the bridge, which automatically restarts the watchdog.

**Relationship to PostToolUse health check**: The PostToolUse health check (`agent/health_check.py`) runs a deterministic consecutive-failure circuit breaker on every tool call (5 back-to-back failures trip it — issue #1413) plus a Haiku judge every 20 tool calls, and uses a two-pronged kill mechanism when it detects an unhealthy session:

1. **`watchdog_unhealthy` flag**: Sets a reason string on the AgentSession model in Redis. The nudge loop in `agent/agent_session_queue.py` checks this flag via `is_session_unhealthy()` before auto-continuing. When flagged, the nudge loop delivers output to Telegram instead of sending "Keep working", breaking the auto-continue cycle.
2. **`additionalContext` injection**: Returns a PostToolUse hook result with `additionalContext` telling Claude to stop immediately and summarize what blocked it.

The session watchdog is complementary — it catches sessions that go *silent* (no tool calls happening), which the PostToolUse hook cannot detect.

**Stall detection**: The watchdog also runs `check_stalled_sessions()` each cycle, which flags sessions stuck in transitional states (pending >5min, running >45min, active with no recent activity). For active sessions, stall detection is activity-based: the watchdog checks both the Redis `updated_at` field and in-memory timestamps from `sdk_client.get_session_last_activity()`, using whichever is more recent. Sessions producing tool calls or log output are never interrupted regardless of total runtime. See [Session Watchdog Reliability](session-watchdog-reliability.md) for the activity-based detection system and [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md) for logging details.

## Process-Locality Contract (issue #1128) — idle-sweeper half retired in #2000

The session-watchdog process (`monitoring/session_watchdog.py`) owns repetition / error-cascade
/ token-threshold detection and their steering actuation, reading `AgentSession` tokens (never
writing them).

This section previously also described a **worker-internal idle sweeper** (`worker/idle_sweeper.py`)
that owned a process-local `_active_clients` registry and proactively tore down persistent SDK
clients on dormant / paused / paused_circuit sessions. That sweeper and the registry it
inspected were deleted wholesale in #2000 along with the rest of the dead Claude Agent SDK path
— every production session now runs a short-lived `claude -p` subprocess per turn, which never
populates a persistent-client registry. See [HarnessAdapter Seam](harness-adapter.md) and
[bridge-worker-architecture.md](bridge-worker-architecture.md) for the current topology.

## Files

| File | Purpose |
|------|---------|
| `monitoring/session_watchdog.py` | Watchdog implementation (detection + loop-break steer + token alert) |
| `monitoring/__init__.py` | Module exports |
| `agent/health_check.py` | PostToolUse health check with watchdog_unhealthy flag and additionalContext injection |
| `agent/agent_session_queue.py` | Nudge loop checks `is_session_unhealthy()` before auto-continuing |
| `agent/sdk_client.py` | `accumulate_session_tokens` helper (SDK + harness path writers) |
| `agent/steering.py` | `push_steering_message` with `sender="watchdog"` attribution |
| `models/agent_session.py` | `watchdog_unhealthy`, token fields, `sdk_connection_torn_down_at` |
| `bridge/telegram_bridge.py` | Integration point (launches watchdog task) |
| `tests/unit/test_session_watchdog.py` | Detection + steer-actuator assertions |
| `tests/unit/test_watchdog_loop_break_steer.py` | `_inject_watchdog_steer` cooldown + sender attribution |
| `tests/unit/test_watchdog_token_alert.py` | Token threshold → steer wiring |
| `tests/unit/test_stall_detection.py` | `_apply_stall_reaction` payload, dedup, skip conditions (issue #1313) |
| `tests/integration/test_watchdog_to_bridge.py` | End-to-end: watchdog outbox write → bridge relay drain (issue #1313) |
| `tests/unit/test_session_token_accumulator.py` | `accumulate_session_tokens` end-to-end |
| `tests/unit/test_harness_token_capture.py` | Harness-path B3 fix (usage + cost from `result` event) |
| `tests/unit/test_health_check.py` | PostToolUse health check |
| `tests/unit/test_transcript_liveness.py` | Transcript mtime check |
| `docs/plans/session-watchdog.md` | Original plan document |
| `docs/plans/watchdog-hardening.md` | issue #1128 plan |
