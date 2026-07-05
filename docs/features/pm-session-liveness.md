# PM Session Liveness — See Progress or Stay Graceful

**Issue:** [#1172](https://github.com/tomcounsell/ai/issues/1172) (extended by [#1226](https://github.com/tomcounsell/ai/issues/1226))
**Status:** Active
**Last updated:** 2026-04-30

This feature replaces inferred-from-staleness session kills with two
complementary changes: the detector kills only on **evidence** of failure
(Pillar B), and the agent + dashboard surface live state so operators can see
what the agent is doing right now (Pillar A).

**Note (2026-05-06):** the templated mid-work self-report ("Working on:
{snippet} — Dev session running.") was removed. In production it leaked
internal vocabulary — issue numbers, the literal "Dev session running"
phrase — into supervisor chats and read like system-log noise. The PM
persona already covers when to send Telegram updates via
`tools/send_telegram.py`, which now flows through the message drafter
(`bridge/message_drafter.py`) and inherits the persona voice.
Silence between meaningful events is correct; the dashboard's live-state
surface (Pillar A below) is the canonical "is the agent alive" signal.

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
- **Watchdog-tick heartbeat alone.** `last_sdk_heartbeat_at` (written by
  `BackgroundTask._watchdog` every 60s on subprocess existence) is no
  longer a Tier 1 progress signal (#1226). A subprocess that exists but
  produces no structured SDK output is not indistinguishable from a working
  one — it is now correctly identified as hung.

### Tier 1 signal reference (#1226)

`_has_progress` evaluates two sub-checks. Any one passing → True (progress).

| Sub-check | Field | Writer | Window | When active |
|---|---|---|---|---|
| **A: per-turn SDK activity** | `last_tool_use_at` | `agent/hooks/pre_tool_use.py`, `post_tool_use.py` | `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, env-tunable) | Always |
| **A: per-turn SDK activity** | `last_turn_at` | `agent/sdk_client.py` `result` event | `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, env-tunable) | Always |
| **B: startup-window executor-alive** | `last_heartbeat_at` | `_heartbeat_loop` in `session_executor.py` | `HEARTBEAT_FRESHNESS_WINDOW` (90s) | Only when `sdk_ever_output=False` AND (`started_ref` is None OR `running_seconds < STARTUP_GRACE_SECONDS`); gated by the D0 never-started gate — see below (#1724) |
| **Watchdog-alive (not Tier 1)** | `last_sdk_heartbeat_at` | `BackgroundTask._watchdog` every 60s | N/A — not a progress signal | Dashboard `last_evidence_at` only |

Sub-check B preserves backward compatibility for sessions in their startup
window and for those started before PR #1177 (whose hooks did not write the
per-turn fields). Issue #1724 bounds the previously-unbounded fresh-heartbeat
fast-path with the D0 never-started gate: the function reads
`started_ref = entry.started_at or entry.created_at` (the fallback is
load-bearing — the recovery path nulls `started_at` when re-queuing) and,
when `sdk_ever_output=False` AND `running_seconds > NEVER_STARTED_GRACE_SECS
+ NEVER_STARTED_CONFIRM_MARGIN_SECS` (150s), the D0 gate fires and sub-check
B returns False immediately — it does NOT fall through to a grace-to-budget
band. As of issue #1905 the gate is called with the same trusted `now_utc`
clock sub-check B's own `running_seconds` computation uses, making the
prior #1356 grace-to-budget band (and its `tier1_falloff` budget-exceeded
telemetry counter) provably unreachable; both were removed. Combined with
the Tier-2 reprieve cap below, this
guarantees a session that never emits a first turn is recovered within
~60 minutes worst-case (parent investigation #1246).

### Tier 2 reprieve gates (current)

`_tier2_reprieve_signal` retains:

- **`compacting`** — `last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (600s). Real evidence (the PreCompact hook fired).
- **`children`** — `psutil.Process(pid).children()` non-empty. Strongest signal.
- **`alive`** — process status not in {zombie, dead, stopped}.

The previous **`stdout`** gate was retired with the same rationale.

**Reprieve escalation guard (#1226):** When a session has never produced
any SDK tool or turn event (`sdk_ever_output=False`) and its `reprieve_count`
reaches `MAX_NO_OUTPUT_REPRIEVES` (default 20 ticks ≈ 30 minutes), the
"alive" gate is suppressed and recovery proceeds. Sessions that have
produced output (`sdk_ever_output=True`) are never subject to this cap —
their recovery depends solely on per-turn freshness in sub-check A.

**Startup recovery reprieve reset:** `_recover_interrupted_agent_sessions_startup`
resets `reprieve_count=0` when transitioning sessions back to pending, preventing
the escalation guard from triggering immediately after a worker restart.

## PM self-report behavior — removed

The mid-work self-report (`_emit_pm_self_report` in
`agent/session_completion.py`) was removed on 2026-05-06. Its templated
output ("Working on: {snippet} — Dev session running.") read as
system-log noise to human supervisors and competed with the PM's own
voice-filtered messages. The `AgentSession.self_report_sent_at` field is
retained to avoid a migration but is no longer written or read by any
caller.

If a future replacement is added, route it through the message drafter —
do not template raw `parent.message_text` snippets into the chat.

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
  tight tool loops. The cooldown is bypassed for PostToolUse
  (`record_tool_boundary(clear=True)`) writes so that a fast PreToolUse →
  PostToolUse pair within the cooldown window cannot leave
  `current_tool_name` populated; the per-tool timeout sub-loop (#1270)
  depends on PostToolUse always clearing the field promptly to avoid
  false-positive wedges.
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

Issue [#1269](https://github.com/tomcounsell/ai/issues/1269) extends the dashboard
surface with a row-level freshness chip (age since `last_evidence_at`), a ghost
badge driven by a non-blocking process-alive probe, and a modal Liveness section
that surfaces seven additional keys: `harness_pid`, `process_alive`,
`last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`,
`recovery_attempts`, `reprieve_count`. See [Dashboard — Liveness Signals](dashboard.md#liveness-signals).

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
- `tests/unit/test_health_check_recovery_finalization.py::TestHasProgressPerTurnSignal` — per-turn
  SDK signal tests: sub-check A (`last_tool_use_at`/`last_turn_at`), sub-check B
  (startup-window `last_heartbeat_at`), and `last_sdk_heartbeat_at` exclusion from Tier 1 (#1226).
- `tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveEscalation` — reprieve
  escalation guard: suppresses "alive" when `sdk_ever_output=False` and
  `reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` (#1226).
- `tests/unit/test_health_check_recovery_finalization.py::TestStartupRecoveryReprieveCountReset` —
  startup recovery resets `reprieve_count=0` to prevent immediate escalation (#1226 Risk 4).
- `tests/integration/test_pm_long_run_no_kill.py` — acceptance test for
  a 4+ hour PM with active tool use and no result event.

## PTY-liveness gates for kill paths

Two kill paths have a PTY-liveness gate that defers recovery when the granite
PTY is demonstrably active. They are parallel in purpose but use different PTY
signals and have opposite return-value polarity. SDK/non-granite sessions are
never affected — the helpers fall through to `False` when `last_pty_read_loop_at`
is `None` (branch 2 of each helper).

### Gate 1 — tool_timeout default-tier (issue #1784)

**Location:** `agent/session_health.py`, tool-timeout kill-site

**Helper:** `_pty_quiescent_long_enough(entry, now) -> bool`

**PTY signal consulted:** `mid_run_quiescent_since` — a mid-run field set when
the granite PTY screen stops repainting. It is `None` during priming (before
any transcript entry exists) and while the screen is actively painting.

**Polarity:** True = wedge-eligible / OK to kill, False = PTY still active /
defer. This is the "OK to proceed with kill" predicate — a caller that wants to
kill checks `if _pty_quiescent_long_enough(...)`.

**Behavior:** The default-tier kill (for `Bash`/`Skill`/`Task` tools, 300s
budget) is suppressed when `mid_run_quiescent_since` is `None` (PTY still
painting) or when quiescence has not yet lasted `MID_RUN_QUIESCENCE_SECS`
(180s, env-tunable). The kill fires only when the screen has been consistently
static for ≥ 180s.

**Counter:** `{project_key}:session-health:tool_timeouts:default_deferred`

### Gate 2 — never-started D0 kill (issue #1792)

**Location:** `agent/session_health.py`, D0 never-started kill-site (~line 3208)

**Helper:** `_prime_pty_alive(entry, now) -> bool`

**PTY signal consulted:** `last_pty_activity_at` — set whenever the PTY screen
repaints. This field is available during priming (before any transcript entry
exists), making it the correct signal for the never-started kill path where
`mid_run_quiescent_since` is always `None`.

**Polarity:** True = PTY alive / defer the kill, False = kill-eligible. This
is the OPPOSITE of `_pty_quiescent_long_enough`. A caller defers when
`_prime_pty_alive(...)` returns True.

**Behavior:** When the D0 never-started grace window expires and the session
still has no SDK progress, the kill is deferred if the granite PTY read loop
is fresh (`last_pty_read_loop_at` within `HEARTBEAT_FRESHNESS_WINDOW` = 90s)
AND the PTY screen has shown activity within `NEVER_STARTED_PTY_LIVENESS_SECS`
(default 90s, override via `NEVER_STARTED_PTY_LIVENESS_SECS` env var). The
kill proceeds if the read loop is stale, `last_pty_activity_at` is absent, or
activity is older than the window. Setting `NEVER_STARTED_PTY_LIVENESS_SECS=0`
disables deferral for all sessions (kill-switch, branch 1 of the helper).

**Counter:** `{project_key}:session-health:never_started_pty_deferred`

### Branch logic of `_prime_pty_alive`

The helper evaluates four ordered branches; first match wins:

| Branch | Condition | Returns |
|--------|-----------|---------|
| 1 (kill-switch) | `NEVER_STARTED_PTY_LIVENESS_SECS <= 0` | `False` (kill-eligible) |
| 2 (non-PTY) | `last_pty_read_loop_at is None` | `False` (SDK sessions unaffected) |
| 3 (stale read loop) | `last_pty_read_loop_at` older than 90s | `False` (dead loop cannot prove liveness) |
| 4 (alive) | `last_pty_activity_at` within `NEVER_STARTED_PTY_LIVENESS_SECS` | `True` (defer) |

The helper never raises — all unexpected exceptions return `False` (kill-eligible).

### Side-by-side comparison

| Aspect | `tool_timeout` gate (#1784) | `never_started` gate (#1792) |
|--------|----------------------------|------------------------------|
| Kill path | Default-tier tool timeout | D0 never-started grace exceeded |
| Helper | `_pty_quiescent_long_enough` | `_prime_pty_alive` |
| PTY signal | `mid_run_quiescent_since` | `last_pty_activity_at` |
| Why that signal | Available mid-run; None means actively painting | Available during priming; `mid_run_quiescent_since` is always None at prime |
| Return polarity | True = kill-eligible | True = defer (alive) |
| Tuning env var | `MID_RUN_QUIESCENCE_SECS` (default 180s) | `NEVER_STARTED_PTY_LIVENESS_SECS` (default 90s) |
| Deferred counter | `tool_timeouts:default_deferred` | `never_started_pty_deferred` |

## State-layer detection (`sdlc-progress-check`)

The Tier 1 / Tier 2 detectors above watch the **process** layer — they catch wedged PM sessions while the session is technically still running. They do NOT detect a pipeline whose PM session has already gone terminal but whose PR is still open and idle. That state-layer gap is closed by a separate reflection: `sdlc-progress-check` (`reflections/sdlc_progress.py`, registered in `config/reflections.yaml` at a 30-minute interval).

The reflection iterates every local project and applies a **5-gate stall heuristic** to each open PR:

1. **SDLC branch** — head ref matches `session/sdlc-<N>` (other branches are out of scope).
2. **Not draft** — draft PRs are intentionally paused and excluded.
3. **Issue open** — `gh issue view <N> --json state` returns `OPEN`. Closed issues mean the work has landed elsewhere.
4. **Last commit age ≥ `SDLC_STALL_THRESHOLD_HOURS`** (default 4h). Resolved via `git log -1 --format=%H\ %ct origin/session/sdlc-<N>` so the orchestrator doesn't need the branch checked out.
5. **No non-terminal `AgentSession` for the slug** — checked via `AgentSession.query.filter(slug=...)` and `NON_TERMINAL_STATUSES` from `models.session_lifecycle`. If ANY session for the slug is `running`, `pending`, `dormant`, `paused`, `paused_circuit`, or `waiting_for_children`, the alert is suppressed.

When all five gates pass, the reflection sends a single Telegram alert to `Dev: Valor` and writes a Redis dedup key `sdlc:stall:alert:<slug>:<last-commit-sha>` with TTL `SDLC_STALL_COOLDOWN_HOURS` (default 6h). The dedup is keyed on the **last-commit SHA**, not just the slug — a new commit clears the cooldown so a re-stall after partial activity is still surfaced.

### Failure tolerance

Every external boundary (gh CLI, git, `valor-telegram`, Redis, Popoto query) is wrapped in a narrow try/except that **logs a warning and continues**. Stricter failure semantics:

- **Redis unavailable for the dedup write** — the alert is **suppressed** (not sent). Better to under-alert during a Redis flap than to spam during one.
- **`AgentSession` query fails** — the active-session gate returns `None`, treated as "unknown", and the alert is suppressed. The 4-hour threshold gives plenty of time for the next reflection tick to retry.
- **Branch not present locally** — silently skipped (debug-logged). Common during transient worktree state.

### Tunables

| Env var | Default | Meaning |
|---|---|---|
| `SDLC_STALL_THRESHOLD_HOURS` | `4` | Minimum age of last commit before a stall is reportable. |
| `SDLC_STALL_COOLDOWN_HOURS` | `6` | Dedup window for the same `(slug, last-commit-sha)` pair. |

Disable the whole reflection by setting `enabled: false` on the `sdlc-progress-check` entry in `~/Desktop/Valor/reflections.yaml`.

### What this is NOT

- **Not auto-recovery.** v1 is notification-only. The reflection never creates, resumes, or restarts a PM session. Recovery is a human decision after seeing the alert.
- **Not a replacement for the Tier 1/Tier 2 detectors above.** The process-layer detectors run every 5 minutes and watch live sessions. The state-layer reflection runs every 30 minutes and watches dead pipelines.
- **Not draft-PR or non-SDLC-branch aware.** Drafts and ad-hoc branches (`session/<other-slug>`) are intentionally excluded — they have different lifecycles.

## See Also

- [`docs/features/agent-session-health-monitor.md`](agent-session-health-monitor.md) — the simplified `_has_progress` + `_tier2_reprieve_signal` detector.
- [`docs/features/bridge-self-healing.md`](bridge-self-healing.md) — the broader recovery model. Inference kills retired in #1172.
- [`docs/features/session-recovery-mechanisms.md`](session-recovery-mechanisms.md) — recovery counters and reprieve telemetry.
- [`docs/features/dashboard.md`](dashboard.md) — the full set of fields exposed on `/dashboard.json`.
