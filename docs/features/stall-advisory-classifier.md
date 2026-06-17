# Stall Advisory Classifier

Live healthy/suspect/stalled verdict for running sessions from telemetry. Pillar 1 of epic #1536.

## Problem Solved

Before this feature there was no live signal for "is this session wedged or still working?" The dashboard showed session status (running, active, paused) but had no way to distinguish a session that was quietly progressing from one that had silently stalled three turns ago. Diagnosing a stuck session meant manually tailing logs and reconstructing a timeline — after the fact.

The Stall Advisory Classifier closes that gap. It reads each running session's telemetry trace in real time and produces a three-level advisory verdict that surfaces in the dashboard as an unobtrusive badge.

## 3-Level Verdict Model

| Level | Meaning |
|-------|---------|
| `healthy` | Recent turn or tool activity, no concerning patterns |
| `suspect` | Ambiguous — a single multi-minute idle gap, rising tool-timeout count, or weak counter evidence. Absorbs false-positive risk so `stalled` stays high-confidence. |
| `stalled` | Strong evidence of a problem: live never-started session past grace period, sustained idle past 10 min, or kill-bearing status transitions in the event window. |

Healthy sessions produce no badge — no noise for normal operations. Suspect sessions show an amber badge with the reason in a tooltip. Stalled sessions show a red badge.

## Signals

The classifier reads the session's JSONL telemetry trace (see [Session Telemetry](session-telemetry.md)) and optional per-project Redis counters. All reads are read-only — no Redis mutations, no file writes.

### Telemetry Events

| Event type | What the classifier reads |
|---|---|
| `turn_start` | Presence or absence (key for never-started detection and recent-activity check) |
| `turn_end` | Timestamp for last-activity measurement |
| `idle_gap` | `gap_seconds` field (primary); falls back to `data.gap_seconds`, `data.duration_secs`, `data.duration`, `event.duration_secs`, `event.duration` for older event shapes |
| `status_transition` | `to` field — kill-bearing values (`killed`, `failed`, `cancelled`) escalate to `stalled` |

### Per-Project Redis Counters (weak corroboration)

Counters are read from keys of the form `{project_key}:session-health:{metric}`:

| Counter | Threshold | Effect |
|---|---|---|
| `tool_timeouts:*` (summed) | >= 3 | Contributes to `suspect` if no stronger signal present |
| `recoveries:*` (summed) | >= 2 | Contributes to `suspect` if no stronger signal present |

Counter evidence alone is never sufficient to produce `stalled`. It elevates the advisory from `healthy` to `suspect` only when idle and transition signals are below their own thresholds.

## Live Never-Started Detection

A running granite session that has never emitted a `turn_start` event is burning toward its 600-second execution ceiling without doing any work. This pattern is worth flagging early.

The classifier probes for this condition when:

1. The session's `status` is in `_RUNNING_PROBE_STATUSES = {"running", "active", "paused", "paused_circuit"}`
2. The telemetry trace contains zero `turn_start` events
3. The session has been alive longer than `NEVER_STARTED_GRACE_SECS` (120 seconds)

When all three conditions hold, the verdict is `stalled` with reason `never_started`.

`pending` sessions are explicitly excluded. Stall detection for enqueued-but-not-yet-started sessions belongs to the session watchdog (`monitoring/session_watchdog.py`, issue #1313), which has a different ownership model.

## Decision Path

The classifier evaluates signals in priority order:

1. **Never-started probe** — if the session is in a running-probe status, has zero `turn_start` events, and has been alive past the grace period: `stalled/never_started`.
2. **Recent turn activity** — if the last `turn_start` or `turn_end` event was less than `IDLE_SUSPECT_SECS` (300s) ago: `healthy/recent_turn_activity`. This check makes the verdict real-time: a session actively turning is always healthy regardless of historical idle gaps.
3. **Kill-bearing transition** — if any `status_transition` event in the window has `to` in `{"killed", "failed", "cancelled"}`: `stalled/kill_transition`.
4. **Idle gap vs. stall threshold** — if the maximum or most recent idle gap is >= `IDLE_STALL_SECS` (600s): `stalled/idle_gap_exceeded_stall`.
5. **Idle gap vs. suspect threshold** — if the maximum or most recent idle gap is >= `IDLE_SUSPECT_SECS` (300s): `suspect/idle_gap_exceeded_suspect`.
6. **Counter-only suspect** — if project counters alone cross their thresholds: `suspect/project_counter_suspect`.
7. **Default** — `healthy/no_concerning_signals`.

## Tunable Thresholds

All constants are defined at the top of `agent/session_stall_classifier.py` and can be adjusted without touching logic:

| Constant | Default | Meaning |
|---|---|---|
| `NEVER_STARTED_GRACE_SECS` | 120 | Seconds before a zero-turn-start session in a running-probe status is flagged |
| `IDLE_SUSPECT_SECS` | 300 (5 min) | Idle gap that elevates to `suspect` |
| `IDLE_STALL_SECS` | 600 (10 min) | Idle gap that elevates to `stalled` |
| `TOOL_TIMEOUT_SUSPECT_COUNT` | 3 | Cumulative tool timeouts that corroborate `suspect` |
| `RECOVERY_SUSPECT_COUNT` | 2 | Cumulative recovery attempts that corroborate `suspect` |

## Advisory Only: No Kill Path

The classifier is purely informational. It has no write path. This is a hard constraint carried over from #1172 (the decision that retired the stdout-silence-as-kill policy):

- No import from `agent/session_health.py` — the kill and recovery machinery is intentionally unreachable from this module.
- Idle gaps are recorded facts, never kill signals. Recording a gap and triggering a kill action from that same gap are two separate concerns; this classifier handles only the recording side.
- The advisory never auto-kills, auto-pauses, or auto-resumes a session. Human operators or separate automation (such as the health monitor) own those transitions.

This constraint is enforced by the test suite (`tests/unit/test_session_stall_classifier.py`), which asserts that `agent.session_health` is not importable from the classifier's module graph.

## Boundary Against Pillar 2

Pillar 2 (crash-signature auto-resume, #1539) operates on terminal sessions — completed, failed, killed, abandoned. It extracts signatures from the closed telemetry trace and gates automatic resumption behind statistical confidence.

Pillar 1 (this feature) operates on running sessions only. The moment a session transitions to a terminal status it leaves this classifier's scope and becomes Pillar 2's concern.

See [Crash-Signature Auto-Resume](crash-signature-auto-resume.md) for the Pillar 2 design.

## Dashboard Badge

The dashboard renders a small colored badge on session rows:

- No badge for `healthy` sessions.
- Amber badge for `suspect`, tooltip shows the reason slug.
- Red badge for `stalled`, tooltip shows the reason slug.

The badge is computed by the dashboard's JSON API from the latest reflection finding for each session. It has no effect on session routing or lifecycle.

## Periodic Reflection

`reflections/stall_advisory.py` fires every 5 minutes via the reflection scheduler. Each run:

1. Queries sessions in `_RUNNING_PROBE_STATUSES`.
2. Filters out any that have concurrently transitioned to a terminal status.
3. For each surviving session, reads its telemetry timeline and calls `classify_session_stall()`.
4. Collects non-healthy verdicts into a `findings` list.
5. Logs each finding at `WARNING` level; healthy sessions at `DEBUG`.
6. Optionally sends a concise Telegram note when the `stall_advisory_telegram_enabled` flag is set and findings are present.
7. Returns `{"status": "ok"|"warn", "findings": [...], "summary": "..."}`.

### Registering the Reflection

The reflection is not scheduled by default. To activate, add to `~/Desktop/Valor/reflections.yaml`:

```yaml
  - name: stall-advisory
    group: agents
    description: "Classify running sessions as healthy/suspect/stalled from telemetry"
    every: 300s
    priority: normal
    execution_type: function
    callable: "reflections.stall_advisory.run_stall_advisory"
    params:
      stall_advisory_telegram_enabled: false
    enabled: true
```

### Telegram Alert Flag

`stall_advisory_telegram_enabled` defaults to `false` in v1. The reflection computes and logs findings regardless; the flag gates only the Telegram send. It is off by default pending coordination with #1313 (the adjacent session watchdog alert) to avoid double-alerting on the same session. When enabled, the reflection sends only when findings are present — no all-clear spam.

## Fail-Soft Guarantees

- Any exception inside `classify_session_stall` returns `StallVerdict("healthy", "unclassifiable", {})`. The caller is never disrupted by a classification failure.
- Per-session classification errors in the reflection are logged at DEBUG and skipped. The reflection always completes and returns a summary.
- `read_project_health_counters` returns an empty dict on any Redis error. Counter absence is treated as "no corroboration," which keeps the verdict conservative.
- Concurrent telemetry writes during classification produce a best-effort snapshot. `read_session_timeline` already skips malformed or partial JSONL lines, so partial writes at the tail are handled gracefully. Correctness is sufficient for an advisory.

## Source Files

| File | Role |
|---|---|
| `agent/session_stall_classifier.py` | Core classifier: `StallVerdict`, `classify_session_stall`, `read_project_health_counters` |
| `reflections/stall_advisory.py` | Periodic reflection: scan running sessions, collect findings, optional Telegram alert |

## Related

- [Session Telemetry](session-telemetry.md) — the JSONL event trace this classifier reads (Pillar 0 of #1536)
- [Crash-Signature Auto-Resume](crash-signature-auto-resume.md) — Pillar 2: operates on terminal sessions after Pillar 1's scope ends
- [Agent Session Health Monitor](agent-session-health-monitor.md) — the kill/recovery machinery; this classifier intentionally imports none of it
- Epic #1536 — Session Telemetry parent epic
- Issue #1538 — this feature
- Issue #1313 — session watchdog (adjacent alert, pending coordination for Telegram flag)
