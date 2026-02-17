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
2. For each session, reads the `tool_use.jsonl` log file for recent activity
3. Applies four detection heuristics (silence, loop, error cascade, duration)
4. Sends a Telegram alert for any session showing issues, respecting cooldowns

The watchdog fixes problems automatically — marking stuck sessions as abandoned and crashed sessions as failed. It creates GitHub issues for problems that can't be auto-fixed. It never modifies tool history or log files.

## Detection Heuristics

### Silence Detection
Fires when `time.time() - session.last_activity > SILENCE_THRESHOLD`. Indicates the agent may have stalled.

| Setting | Value |
|---------|-------|
| Threshold | 600s (10 minutes) |
| Severity | warning |

### Loop Detection
Examines the tail of `tool_use.jsonl`. Creates fingerprints from `(tool_name, sorted(tool_input.items()))` and counts consecutive identical fingerprints from the end. If 5+ match, the agent is stuck.

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

### Unique Constraint Handling (Crash Guard)

When the watchdog encounters a `Unique constraint violated` error while processing a session, it marks that session as `failed` instead of logging the error and retrying every cycle. This prevents infinite retry loops caused by stale sessions left over from SDK crashes. See [Coaching Loop — Error Crash Guard](coaching-loop.md) for the full crash guard mechanism.

## Remediation

Alerts are sent as Telegram messages to the chat where the session originated. Each alert includes session ID, project key, duration, tool call count, and a bulleted list of detected issues.

**Cooldown**: Each session has a 30-minute cooldown (`ALERT_COOLDOWN = 1800s`). After sending an alert for a session, subsequent alerts for that same session are suppressed until the cooldown expires. This prevents the operator from receiving the same alert every 5 minutes.

**Fallback**: If the Telegram client is unavailable or the send fails, the alert is logged at WARNING level and the watchdog continues.

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

No runtime configuration — edit constants directly. Intentionally static to keep the watchdog simple.

## Integration

Started in `bridge/telegram_bridge.py` at line ~3321:

```python
from monitoring.session_watchdog import watchdog_loop
asyncio.create_task(watchdog_loop(telegram_client=client))
```

Runs for the lifetime of the bridge process. No separate service or process management needed. The existing update system restarts the bridge, which automatically restarts the watchdog.

**Relationship to PostToolUse health check**: The existing health check fires every 20 tool calls and can kill sessions. The watchdog is complementary — it catches sessions that go *silent* (no tool calls happening), which the PostToolUse hook cannot detect.

## Files

| File | Purpose |
|------|---------|
| `monitoring/session_watchdog.py` | Watchdog implementation (all detection + alerting) |
| `monitoring/__init__.py` | Module exports |
| `bridge/telegram_bridge.py` | Integration point (launches watchdog task) |
| `tests/unit/test_session_watchdog.py` | 28 unit tests |
| `docs/plans/session-watchdog.md` | Original plan document |
