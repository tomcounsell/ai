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
`tools/send_message.py`, which flows through the canonical delivery
handler (`bridge/message_drafter.py` validation) and inherits the persona voice.
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
| Delivery guard (#918, epoch-scoped by #1979) | `response_delivered_at >= (started_at or created_at)` (delivery belongs to the current run) → finalize as `completed`, NOT recover. A delivery timestamp from before the current run's `started_at` (e.g. a stale value carried across a resume) no longer trips the guard. | `agent/session_health.py::_delivery_belongs_to_current_run` |

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

`sdk_ever_output` throughout this table is `agent.session_runner.liveness.derive_sdk_ever_output(entry)`
— as of issue #1935 a **third** OR-input, `last_stdout_at` (written by
`SessionRunner._stamp_stdout_liveness` on the headless stream's `init`/stdout
events), joins `last_tool_use_at`/`last_turn_at`. This narrows sub-check B's
active window and the Tier-2 reprieve escalation guard below to genuinely
toolless-AND-non-streaming sessions — a session that streamed at least
`init` now counts as `sdk_ever_output=True` even with zero tool calls. See
[Headless Session Runner § Liveness signals](headless-session-runner.md#liveness-signals-sdk_ever_output-issue-1935).

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
voice-filtered messages. The `AgentSession.self_report_sent_at` field it
gated had no live writer or reader and was deleted by the schema diet
(#1927) — see [AgentSession Model](agent-session-model.md).

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

> **These writers are repo-scoped — do not treat them as universal.** All
> three hooks above live in **this repo's** `.claude/settings.json`, so they
> only fire for a session whose cwd is this repo. A session running against
> any other repo (cyndra-consulting, a client repo) carries `None` in
> `current_tool_name` / `last_tool_use_at` / `last_turn_at` for its entire
> life. Any consumer that reads these fields as a *freshness* signal must
> therefore also consume a repo-independent one — see the deadline clock in
> [Headless Session Runner § Mid-turn tool activity](headless-session-runner.md#mid-turn-tool-activity-the-toolactivity-marker-2026-07-30),
> which is the bug this warning exists to prevent recurring: between #1930
> and 2026-07-30, `_session_progress_ts` read only these fields and silently
> became a hard 30-minute cap on every foreign-repo turn.

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

**Session resolution (issue #2205):** `liveness_writers.py` resolves the
in-flight `AgentSession` via the shared `agent/hooks/session_resolver.py`
helper, which prefers `VALOR_SESSION_ID` (the true `session_id`, looked up
with a direct filter) and falls back to `AGENT_SESSION_ID` (the Popoto
AutoKey hex, looked up via `get_by_id_strict`) when the `VALOR_SESSION_ID`
lookup is absent or empty. Before this fix, the hooks read `AGENT_SESSION_ID`
only; for bridge PM sessions, where `agent_session_id != session_id`, that
lookup silently missed and `current_tool_name`/`last_tool_use_at`/
`last_turn_at`/`recent_thinking_excerpt` never updated. The per-tool budget
backstop hook (`pre_tool_use.py`) shares the same resolver and had the same
gap.

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
that surfaces seven additional keys: `exec_pid` (the fenced execution record's
pid), `process_alive`,
`last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`,
`recovery_attempts`, `reprieve_count`. See [Dashboard — Liveness Signals](dashboard.md#liveness-signals) and [`docs/features/agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md).

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

## PTY-liveness gates — deleted with the granite PTY substrate (issue #1924)

Two kill paths (`tool_timeout` default-tier, and the never-started D0 kill)
used to carry a PTY-liveness gate — `_pty_quiescent_long_enough` (issue
#1784) and `_prime_pty_alive` (issue #1792) — that deferred recovery while
the granite PTY screen was demonstrably still painting. Both helpers, both
kill-site call sites, and the four PTY-liveness `AgentSession` fields they
read (`last_pty_read_loop_at`, `last_pty_activity_at`,
`mid_run_quiescent_since`, `mid_run_pty_snapshot`) were deleted outright with
the granite PTY substrate — a `claude -p` turn has no screen to paint, so
there is nothing for a liveness gate to distinguish. Both kill paths are now
flat age-only kills, applied uniformly to every session. See [Never-Started
Session Recovery](never_started_session_recovery.md#superseded-the-pty-liveness-deferral-and-mid-run-quiescence-detector)
and [Headless Session Runner](headless-session-runner.md#liveness) for the
current design.

## State-layer detection (`sdlc-progress-check`)

The Tier 1 / Tier 2 detectors above watch the **process** layer: they catch wedged PM sessions while the session is technically still running. They do NOT detect a pipeline whose PM session has already gone terminal but whose PR is still open and idle. That state-layer gap is closed by a separate reflection: `sdlc-progress-check` (`reflections/sdlc_progress.py`, registered in `config/reflections.yaml` at a 30-minute interval).

The reflection iterates every local project and applies a **6-gate stall heuristic** to each open PR, then an **action ladder** on the ones that fail all six.

1. **SDLC branch.** Head ref matches `session/sdlc-<N>` (other branches are out of scope).
2. **Not draft.** Draft PRs are intentionally paused and excluded.
3. **Issue open.** `gh issue view <N> --json state` returns `OPEN`. Closed issues mean the work has landed elsewhere.
4. **Last commit age ≥ `SDLC_STALL_THRESHOLD_HOURS`** (default 4h). Resolved via `git log -1 --format=%H\ %ct origin/session/sdlc-<N>` so the orchestrator doesn't need the branch checked out.
5. **Lane liveness, read from the issue lock, not from session rows.** `sdlc_progress.py::_lock_says_live` runs a direct `GET` on `session:issuelock:{N}` and classifies the payload with `models.session_lifecycle._lock_owner_is_live`, the same helper the lock's own owner uses. Key absent means not live. A malformed payload or Redis error means **unknown**, and unknown always declines to act (skip this tick, findings note `gate-unknown: lock-read`).

   This is deliberately a bare `GET`, never a call to `touch_issue_lock`. `touch_issue_lock` fails **open**: every Redis exception it swallows comes back as `IssueLockResult(acquired=True)`, which this gate would read as "lock unheld, not live, act" (exactly backwards during a Redis flap), and it also cannot express "unknown" at all. A bare `GET` can only read, never claim or renew a lease, which is the stronger read-only guarantee this gate needs.

   A secondary signal, `_nonledger_row_live`, ORs in "live" for a *fresh* (within `SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES`) non-ledger `AgentSession` row in a `NON_TERMINAL_STATUSES` state for the issue. It can only ADD liveness, never remove it: a failed row check contributes `False`, never `None`, so a degraded secondary signal can never veto a healthy primary one. This catches a lane running without a lock (a Redis flap during acquire).
6. **This machine owns the project.** `reflections.utilities.machine_owns_project(project_key)` against `projects.<key>.machine`. Two machines sharing a checkout must never both act on the same lane; a non-owning machine skips the whole project.

### The action ladder

A PR that fails all six gates is genuinely stalled. The reflection then acts on it, keyed on `(slug, head-sha)`:

1. **Attempt budget check.** If the `(slug, sha)` attempt counter (`sdlc:stall:resume:attempts:{slug}:{sha}`, TTL `SDLC_STALL_ATTEMPTS_TTL_HOURS`) is at or over `SDLC_STALL_RESUME_MAX_ATTEMPTS`, escalate once and stop: no rung fires.
2. **Action cooldown.** A `SET NX EX` claim on `sdlc:stall:resume:cooldown:{slug}:{sha}` (TTL `SDLC_STALL_RESUME_COOLDOWN_HOURS`) both throttles retries and guards against two overlapping ticks acting on the same lane. Cooldown live means skip this tick, no attempt charged. The claim is deliberately made *before* the rung is picked (that is what makes it the overlapping-tick guard), so any path that then bails without dispatching anything — target query unknown, create brake, a declined rung — releases the key (`DELETE`) before moving on. A tick that took no action must not consume the action window; releasing is safe because there is no action for a concurrent tick to duplicate.
3. **Rung 1: steer.** A live (non-terminal, non-ledger) eng session for the project already exists, so `steer_session(...)` fires with an instruction to invoke `/sdlc` for the issue and route one stage.
4. **Rung 2: resume.** No live session, but the most recently updated eng session in a resumable status carries a `claude_session_uuid`, so `resume_session(...)` fires with the same instruction.
5. **Rung 3: create.** No steerable or resumable target, so `create_session(role="eng", slug="sdlc-{N}", session_type="eng")` fires, subject to the create rung's own guards (below).
6. **Rung 4: escalate.** The dispatched action failed for a non-benign reason, so escalate once and stop.

Escalation sends a single Telegram alert to `Eng: Valor` (`_send_alert`, via the `valor-telegram` CLI) and writes the Redis dedup key `sdlc:stall:escalated:{slug}:{sha}` (`SET NX`, TTL `SDLC_STALL_ESCALATION_TTL_DAYS`) so a human hears about a given head sha at most once, no matter how many ticks keep hitting the exhausted-budget or action-failed rung.

Every rung's outcome is classified and, on success or failure, charges the `(slug, sha)` attempt counter: a steer that lands but doesn't move the pipeline is the steer-storm the budget exists to bound. The one exception is a **benign race**: another actor (typically `reflections.crash_recovery`, or the lane re-taking its own issue lock) got there first. A resume failure whose row re-reads non-terminal, and a create refusal against a lock that is now live, charge no attempt and fire no escalation; they log a `benign-race` finding and move on. The steer rung has no benign-race branch: a live non-ledger target simply succeeds, so every steer failure is a real dead end and always charges an attempt.

### The create rung's guards

Create is the one capability an operator needs to know this reflection has: it can mint a brand-new eng session, unattended, for a stalled issue. Four guards bound that power:

- **Machine ownership** (gate 6, above). A non-owning machine never reaches the ladder at all.
- **Pre-create lock re-read.** Gate 5 ran earlier in the tick; the lock can be re-acquired between the gate and the create call, and a duplicate lane on top of a live one is the worst outcome here. `_attempt_action` re-reads `_lock_says_live` immediately before calling `create_session`: unknown declines (no create); live is a benign race (no attempt charged).
- **Shared attempt budget.** The create rung is charged against the same `(slug, sha)` counter as steer and resume; it does not get its own, larger allowance.
- **Per-tick creation cap.** `SDLC_STALL_CREATE_MAX_PER_TICK` (default 1) bounds how many *new* sessions one project can mint in a single tick, independent of how many stalled PRs it has. A PR that would create past the cap is deferred to the next tick (`create-brake` finding), not dropped — and because the braked lane releases its action-cooldown claim, "next tick" means the next tick, not the next cooldown window.
- **Distinct telemetry.** The per-tick summary counts `steered` / `resumed` / `created` / `escalated` separately, and every dispatched create logs `auto-resume create: {slug}` on success. Creates are never folded into the steer/resume counts, so an operator scanning findings can see exactly how often the reflection is minting new sessions.

### Failure tolerance

Every external boundary (gh CLI, git, `valor-telegram`, Redis, Popoto query, `steer_session`, `resume_session`, `create_session`) is wrapped in a narrow try/except that **logs a warning and continues**; the reflection never raises. Stricter failure semantics:

- **Redis unavailable for the cooldown, attempts, or escalation keys.** The reflection declines to act (skip this tick). Better to under-act during a Redis flap than to double-act or spam during one.
- **`AgentSession` query fails** (gate 5's secondary signal, or the ladder's target query). Reads as `None`/"unknown", logged as a `gate-unknown` finding, no action taken.
- **Branch not present locally.** Silently skipped (debug-logged). Common during transient worktree state.

### Tunables

| Env var | Default | Meaning |
|---|---|---|
| `SDLC_STALL_THRESHOLD_HOURS` | `4` | Minimum age of last commit before a lane is stall-eligible. |
| `SDLC_STALL_RESUME_ENABLED` | `true` | Break-glass switch. `false` disables the whole action ladder; the reflection still escalates once per `(slug, sha)`. |
| `SDLC_STALL_RESUME_MAX_ATTEMPTS` | `3` | Attempt budget per `(slug, sha)` before escalating and stopping. |
| `SDLC_STALL_CREATE_MAX_PER_TICK` | `1` | Cap on new sessions the create rung may mint per project per tick. |
| `SDLC_STALL_RESUME_COOLDOWN_HOURS` | `1` | Action cooldown per `(slug, sha)`: also the overlapping-tick guard. |
| `SDLC_STALL_ESCALATION_TTL_DAYS` | `30` | TTL on the escalation dedup key; bounds how long "already told a human about this sha" is remembered. |
| `SDLC_STALL_ATTEMPTS_TTL_HOURS` | `24` | TTL on the attempt counter. |
| `SDLC_STALL_ROW_LIVENESS_MAX_AGE_MINUTES` | `30` | Freshness bound on gate 5's secondary (non-ledger row) liveness signal. Aligned with the issue lock's own TTL so the two liveness horizons expire together. |

Disable the whole reflection by setting `enabled: false` on the `sdlc-progress-check` entry in `~/Desktop/Valor/reflections.yaml`.

### What this is NOT

- **Never kills anything.** This reflection steers, resumes, and creates eng sessions to restart a stalled lane. It has no kill path of its own; recovery of a wedged-but-still-running process stays with the Tier 1/Tier 2 detectors above.
- **Not a replacement for the Tier 1/Tier 2 detectors above.** The process-layer detectors run every 5 minutes and watch live sessions. The state-layer reflection runs every 30 minutes and watches pipelines whose PM session has already gone terminal.
- **Not draft-PR or non-SDLC-branch aware.** Drafts and ad-hoc branches (`session/<other-slug>`) are intentionally excluded: they have different lifecycles.

## Confirm subprocess dead before requeue AND before worktree cleanup (issue #1938)

When the health check recovers a headless-runner session (`running → pending`,
or `failed` after `MAX_RECOVERY_ATTEMPTS`), the session's detached `claude -p`
process group must be **confirmed dead** before the record is requeued and before
its synthetic-slug worktree is deleted. Two guarantees enforce this:

1. **Requeue gate.** `_apply_recovery_transition` snapshots the fenced
   `exec_pid`/`pid_create_time` (`AgentSession.live_fence`) **before** cancelling
   `SessionHandle.task`. The fence is not cleared between turns, so the snapshot
   stays valid across the unwind; staleness is caught by `fence_is_live`'s
   create_time compare rather than by the field going `None`. It then runs
   `_confirm_subprocess_dead(pid_snapshot)` — now
   **process-group aware**: it derives the group via `os.getpgid` and signals the
   GROUP with `os.killpg` (SIGTERM→SIGKILL + liveness probes), so a detached group
   with grandchildren (MCP servers) is fully reaped. A group that will not die
   escalates the session to `failed` instead of parking an invisible orphan at
   `pending`.
2. **Cleanup ordering (structural, no new gate).** The runner's `_run_one_turn`
   `finally` SYNCHRONOUSLY reaps + confirms its group before `await task._task`
   resolves in the outer executor coroutine, so the executor's synthetic-slug
   cleanup runs strictly after the group is dead. The one residual — a
   pathological unkillable group — leaves a durable `runner_reap_failed` marker
   that the cleanup reads to **skip** deletion (see
   [headless-session-runner.md](headless-session-runner.md#subprocess-lifecycle--teardown-reap-issue-1938)).

**Deliberate no-go: no worker-parented reaper leg.** The orphan reaper's PPID==1
gate is left unchanged. A worker-parented backstop was examined and rejected:
keying it on the fenced `exec_pid` alone would still race a dead session's stale
fence against a live session's recycled PID — `fence_is_live`'s create_time
compare is the guard against exactly this, but the residual TOCTOU window
(no `pidfd` on macOS) means a reaper leg here would carry a live-kill risk. The primary fixes make a terminal-but-live
process unreachable at its creation sites, so a reaper leg here would be a cleanup
path that should never fire while carrying a live-kill risk. The existing PPID==1
reaper still covers genuinely-orphaned (worker-dead) processes.

## One-shot reaper verifies orphanhood before killing (issue #2149)

The two `claude --print` one-shot reapers in `agent/session_health.py` — the
fast-cadence `_fast_reap_stale_print_oneshots()` (every health-loop tick) and
the hourly `_reap_orphan_session_processes()` — no longer treat age alone as
proof of orphanhood. The #1632 premise ("no legitimate `--print` one-shot
survives past 600s") was invalidated by the headless-runner cutover: a single
PM turn IS a `claude -p` process and legitimately runs 14–19 minutes. On
2026-07-17 the age-only rule SIGTERM'd the live harness of a running session
(PID 74819), which the next dead-worker sweep then finalized to `killed`.

**The ownership gate.** Age > `ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS` (600s) is
now only a trigger to *investigate ownership*, never to kill:

- **Fast reaper** calls `_oneshot_owner_is_live(pid)`: resolves
  `AgentSession.find_live_session_by_pid(pid)` (a bounded forward scan over
  the low-cardinality `status` index, building an in-process `{live_pid:
  session}` map from each non-terminal row's fence) inside a **bounded lookup** (a
  module-level single-worker executor awaited with
  `ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS = 2.0`), then requires
  `_session_is_alive(session)` (non-terminal + heartbeat fresher than 30 min).
  Live owner → the PID is protected: any prior SIGTERM stage for its
  `(pid, create_time)` tuple is discarded from `_pending_sigkill_orphans` (no
  staging-ledger leak on PID recycling) and an INFO
  `[fast-oneshot-reap] protected live harness PID N — owning session alive`
  line is emitted. Timeout, lookup exception, or `pid=None` all **fail toward
  reapable** — a wedged Redis degrades to the pre-fix cleanup, never a stalled
  health loop, matching `_session_is_alive`'s conservative-False contract.
- **Hourly reaper**: the former `is_stale_oneshot` fast-kill branch (which
  deliberately bypassed the heartbeat gate) is deleted. A stale one-shot now
  falls through to the same `session is not None and _session_is_alive(session)`
  gate every other signature uses — one `find_live_session_by_pid` lookup, already
  resolved, no redundant second call.

**Cleanup power preserved.** The #1632 rogue-subagent one-shots have no owning
session (`find_live_session_by_pid` → None), so they are still reaped on the same
fast cadence — as are one-shots whose owner is terminal or whose owner's
heartbeat is stale (dead worker).

**Write-side dependency.** The gate reads the fenced execution record
(`exec_pid`/`pid_create_time`), stamped on PM-turn spawn by
`AgentSession.stamp_execution_spawn` (called from
`agent/session_runner/runner.py`'s `_on_turn_spawn`) inside a fail-silent
`try/except`, and not re-stamped again until the next spawn (the heartbeat
writer refreshes only `last_heartbeat_at`). A spawn-time Redis blip that loses
the write would make a live harness look unowned again; hardening that write
is a tracked follow-up (plan Open Question 3), not part of this fix. See
[`docs/features/agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md) for the full fence design.

## See Also

- [`docs/features/agent-session-health-monitor.md`](agent-session-health-monitor.md) — the simplified `_has_progress` + `_tier2_reprieve_signal` detector.
- [`docs/features/headless-session-runner.md`](headless-session-runner.md) — the runner's subprocess-lifecycle contract and teardown reap.
- [`docs/features/bridge-self-healing.md`](bridge-self-healing.md) — the broader recovery model. Inference kills retired in #1172.
- [`docs/features/session-recovery-mechanisms.md`](session-recovery-mechanisms.md) — recovery counters and reprieve telemetry.
- [`docs/features/dashboard.md`](dashboard.md) — the full set of fields exposed on `/dashboard.json`.
- [`docs/features/session-steering.md`](session-steering.md): the sdlc-progress-check action ladder's steer and resume rungs are a consumer of the steering inbox described there (rung 1 calls `steer_session`, the same entry point Telegram reply-thread steering uses).
