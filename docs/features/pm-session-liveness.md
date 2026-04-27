# PM Session Liveness — See Progress or Stay Graceful

**Issue:** [#1172](https://github.com/tomcounsell/ai/issues/1172)
**Status:** Active
**Last updated:** 2026-04-26

This feature replaces inferred-from-staleness session kills with two
complementary changes: the detector kills only on **evidence** of failure
(Pillar B), and the agent + dashboard surface live state so operators can see
what the agent is doing right now (Pillar A). PM sessions also emit one
short mid-work self-report so the chat is neither silent nor spammy
("goldilocks" mode).

## Detector philosophy

The previous detector tried to **infer** liveness from past timestamps.
Each new tweak (`STDOUT_FRESHNESS_WINDOW`, `FIRST_STDOUT_DEADLINE`,
per-session wall-clock cap) added another inference layer; none replaced
the asymmetric error model where false-kills (lose real work) are treated
symmetrically with false-positives-on-stuck (cost almost nothing — cost
monitoring catches the runaway case).

Issue #1172 retires every inference path. Evidence-only signals stay:

### What the detector kills on

| Trigger | Evidence | Source |
|---|---|---|
| `worker_dead` | The Python `_active_workers[worker_key]` future is missing or done | `agent/session_health.py::_agent_session_health_check` |
| `no_progress` (after Tier 2) | `_has_progress` returned False AND every Tier 2 reprieve gate failed | `agent/session_health.py::_has_progress` + `_tier2_reprieve_signal` |
| Mode 4 OOM defer (#1099) | `exit_returncode == -9` AND psutil reports memory tight | `agent/session_health.py:1017-1036` |
| Delivery guard (#918) | `response_delivered_at` is set → finalize as `completed`, NOT recover | `agent/session_health.py:798-822` |

### What the detector explicitly does NOT kill on

- **Stdout silence.** The deleted `STDOUT_FRESHNESS_WINDOW` path (#1046)
  killed alive-but-silent sessions; this misfired on long-thinking turns
  and large tool outputs.
- **Wall-clock duration.** The deleted `_get_agent_session_timeout` and
  the `AGENT_SESSION_TIMEOUT_DEFAULT` / `AGENT_SESSION_TIMEOUT_BUILD`
  constants enforced a 45-min / 2.5-hour cap. That cap killed working
  sessions that simply needed more time. A session writing fresh
  heartbeats can run as long as it needs.
- **Absence of stdout within a deadline.** The deleted
  `FIRST_STDOUT_DEADLINE` killed sessions that had not yet produced
  stdout within 5 min — false-positive on long warmups.

### Tier 2 reprieve gates (current)

`_tier2_reprieve_signal` retains:

- **`compacting`** — `last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (600s). Real evidence (the PreCompact hook fired).
- **`children`** — `psutil.Process(pid).children()` non-empty. Strongest signal.
- **`alive`** — process status not in {zombie, dead, stopped}.

The previous **`stdout`** gate was retired with the same rationale.

## PM self-report behavior

PM sessions emit at most one short status message via `valor-telegram send`
between the first dev-child completion and the final delivery — and only one.

### Trigger conditions (all must hold)

1. `parent.session_type == "pm"` — only PM sessions self-report.
2. `parent.self_report_sent_at is None` — frequency cap state.
3. `project_name` resolved from `parent.project_config["name"]` is a
   non-empty string. Without it, the send is skipped (the wrong channel
   is worse than no message).

### Channel and content

- **Channel:** `PM: {project_name}` via the `valor-telegram send` CLI.
  Reuses the subprocess pattern from `agent/sustainability.py:_send_telegram`.
- **Content:** Short templated string composed from `parent.message_text[:200]`.
  Templated, NOT LLM-generated — past experiments drift into spam mode.
  Example: `"Working on: Run the build for issue #1172 — Dev session running."`

### Failure handling

- Subprocess raise OR `returncode != 0` → log WARNING and leave
  `self_report_sent_at = None` so the next dev-child completion can retry.
- The helper itself never raises; PM final delivery is unaffected.

## Pillar A — In-flight visibility

Four new `AgentSession` fields surface the agent's own state so operators
can read what's happening live, no inference required.

| Field | Writer | Notes |
|---|---|---|
| `current_tool_name` | `agent/hooks/pre_tool_use.py` (set), `post_tool_use.py` (clear) | Name of the tool currently in flight, or None between tools. |
| `last_tool_use_at` | both hooks | Bumped on every tool boundary. |
| `last_turn_at` | `agent/sdk_client.py` `result` event | Most recent SDK turn boundary. |
| `recent_thinking_excerpt` | `agent/sdk_client.py` `thinking_delta` | Last 280 chars of extended-thinking content (tweet length). |

All writes go through `agent/hooks/liveness_writers.py`, which enforces:

- **Per-session 5s in-memory cooldown** to bound Redis write rate under
  tight tool loops.
- **Best-effort fail-closed.** Every write is wrapped in try/except;
  Redis or Popoto failures log at DEBUG and return False. The hook return
  value is unaffected — the agent never crashes because liveness writes
  failed.
- **No backfill.** Sessions started before this commit lands keep `None`
  on the new fields until their next tool / turn boundary fires.

### Dashboard surfaces

`/dashboard.json`'s `sessions[]` entries gain five new keys:

- `current_tool_name` (string | null)
- `last_tool_use_at` (float epoch | null)
- `last_turn_at` (float epoch | null)
- `recent_thinking_excerpt` (string | null)
- `last_evidence_at` (float epoch | null) — derived as `max(last_heartbeat_at,
  last_sdk_heartbeat_at, last_stdout_at, last_tool_use_at, last_turn_at,
  last_compaction_ts)`. None when every contributing field is None.

Backwards-compatible JSON addition — extra keys are ignored by typical
consumers.

## Cost backstop

The detector intentionally has no wall-clock kill. The long-run backstop
for genuinely runaway sessions is cost monitoring:
`AgentSession.total_cost_usd` (issue #1128) accumulates per-session
spend from the SDK `ResultMessage.usage` and the harness `result` event.
The dashboard surfaces it; an operator-driven alarm can be added if a
specific cost ceiling becomes operationally necessary.

## Migration / rollout

- **No new dependencies.** `valor-telegram` CLI is already installed;
  `subprocess.run(...)` is already used by `agent/sustainability.py`.
- **No data migration.** New `AgentSession` fields are nullable;
  pre-existing rows keep `None` until their next write.
- **No update-script changes.** Standard `git pull` + restart picks
  up the new code.
- **Env vars retired.** `STDOUT_FRESHNESS_WINDOW_SECS` and
  `FIRST_STDOUT_DEADLINE_SECS` are no-ops post-deploy. Operators who
  set them in `.env` will see no effect (intended).

## Test coverage

- `tests/unit/test_session_health_inference_removed.py` — structural
  guards on the deleted constants and helpers.
- `tests/unit/test_agent_session_liveness_fields.py` — model-level
  field roundtrip + default guards.
- `tests/unit/test_pre_tool_use_liveness_writes.py` — hook writer
  behavior + fail-closed + cooldown.
- `tests/unit/test_dashboard_pillar_a_fields.py` — dashboard JSON shape.
- `tests/unit/test_pm_self_report.py` — frequency cap + failure paths.
- `tests/integration/test_pm_long_run_no_kill.py` — acceptance test for
  a 4+ hour PM with active tool use and no result event.
- `tests/integration/test_pm_goldilocks_messaging.py` — acceptance test
  for the one-mid-work-message-then-final-delivery cadence.

## See Also

- [`docs/features/agent-session-health-monitor.md`](agent-session-health-monitor.md) — the simplified `_has_progress` + `_tier2_reprieve_signal` detector.
- [`docs/features/bridge-self-healing.md`](bridge-self-healing.md) — the broader recovery model. Inference kills retired in #1172.
- [`docs/features/session-recovery-mechanisms.md`](session-recovery-mechanisms.md) — recovery counters and reprieve telemetry.
- [`docs/features/dashboard.md`](dashboard.md) — the full set of fields exposed on `/dashboard.json`.
