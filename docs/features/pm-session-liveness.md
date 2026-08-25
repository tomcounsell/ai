# PM Session Liveness — See Progress or Stay Graceful

Session liveness uses two complementary mechanisms: the detector kills only
on **evidence** of failure (Pillar B), and the agent plus the dashboard
surface live state so operators can see what the agent is doing right now
(Pillar A).

## Detector philosophy

The detector kills only on evidence of failure, never on inference from
past timestamps. Evidence-only signals are:

### What the detector kills on

| Trigger | Evidence | Source |
|---|---|---|
| `worker_dead` | The Python `_active_workers[worker_key]` future is missing or done | `agent/session_health.py::_agent_session_health_check` |
| `no_progress` (after Tier 2) | `_has_progress` returned False AND every Tier 2 reprieve gate failed | `agent/session_health.py::_has_progress` + `_tier2_reprieve_signal` |
| Mode 4 OOM defer | `exit_returncode == -9` AND psutil reports memory tight | `agent/session_health.py` |
| Delivery guard | `response_delivered_at >= (started_at or created_at)` (delivery belongs to the current run) → finalize as `completed`, NOT recover. A delivery timestamp from before the current run's `started_at` (e.g. a stale value carried across a resume) does not trip the guard. | `agent/session_health.py::_delivery_belongs_to_current_run` |

### What the detector explicitly does NOT kill on

- **Stdout silence.** A session writing fresh heartbeats can run as long as
  it needs; silence alone is not a kill signal.
- **Wall-clock duration.** No per-session wall-clock cap applies. A session
  writing fresh heartbeats runs as long as it needs.
- **Absence of stdout within a deadline.** No first-stdout deadline applies.
- **Watchdog-tick heartbeat alone.** `last_sdk_heartbeat_at` (written by
  `BackgroundTask._watchdog` every 60s on subprocess existence) is not a Tier 1
  progress signal. A subprocess that exists but produces no structured SDK
  output is identified as hung, not treated as healthy.

### Tier 1 signal reference

`_has_progress` evaluates two sub-checks. Any one passing → True (progress).

| Sub-check | Field | Writer | Window | When active |
|---|---|---|---|---|
| **A: per-turn SDK activity** | `last_tool_use_at` | `agent/hooks/pre_tool_use.py`, `post_tool_use.py` | `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, env-tunable) | Always |
| **A: per-turn SDK activity** | `last_turn_at` | `agent/sdk_client.py` `result` event | `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, env-tunable) | Always |
| **B: startup-window executor-alive** | `last_heartbeat_at` | `_heartbeat_loop` in `session_executor.py` | `HEARTBEAT_FRESHNESS_WINDOW` (90s) | Only when `sdk_ever_output=False` AND (`started_ref` is None OR `running_seconds < STARTUP_GRACE_SECONDS`); gated by the D0 never-started gate — see below |
| **Watchdog-alive (not Tier 1)** | `last_sdk_heartbeat_at` | `BackgroundTask._watchdog` every 60s | N/A — not a progress signal | Dashboard `last_evidence_at` only |

`sdk_ever_output` throughout this table is `agent.session_runner.liveness.derive_sdk_ever_output(entry)`.
It is a three-way OR-input: `last_stdout_at` (written by
`SessionRunner._stamp_stdout_liveness` on the headless stream's `init`/stdout
events), `last_tool_use_at`, and `last_turn_at`. This narrows sub-check B's
active window and the Tier-2 reprieve escalation guard below to genuinely
toolless-AND-non-streaming sessions — a session that streamed at least
`init` counts as `sdk_ever_output=True` even with zero tool calls. See
[Headless Session Runner § Liveness signals](headless-session-runner.md#liveness-signals).

Sub-check B preserves backward compatibility for sessions in their startup
window and for those started before the per-turn fields were written. The D0
never-started gate bounds the fresh-heartbeat fast-path: the function reads
`started_ref = entry.started_at or entry.created_at` (the fallback is
load-bearing — the recovery path nulls `started_at` when re-queuing) and,
when `sdk_ever_output=False` AND `running_seconds > NEVER_STARTED_GRACE_SECS
+ NEVER_STARTED_CONFIRM_MARGIN_SECS` (150s), the D0 gate fires and sub-check
B returns False immediately — it does NOT fall through to a grace-to-budget
band. The gate is called with the same trusted `now_utc` clock sub-check B's
own `running_seconds` computation uses. Combined with the Tier-2 reprieve cap
below, this guarantees a session that never emits a first turn is recovered
within ~60 minutes worst-case.

### Tier 2 reprieve gates (current)

`_tier2_reprieve_signal` retains:

- **`compacting`** — `last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (600s). Real evidence (the PreCompact hook fired).
- **`children`** — `psutil.Process(pid).children()` non-empty. Strongest signal.
- **`alive`** — process status not in {zombie, dead, stopped}.

**Reprieve escalation guard:** When a session has never produced
any SDK tool or turn event (`sdk_ever_output=False`) and its `reprieve_count`
reaches `MAX_NO_OUTPUT_REPRIEVES` (default 20 ticks ≈ 30 minutes), the
"alive" gate is suppressed and recovery proceeds. Sessions that have
produced output (`sdk_ever_output=True`) are never subject to this cap —
their recovery depends solely on per-turn freshness in sub-check A.

**Startup recovery reprieve reset:** `_recover_interrupted_agent_sessions_startup`
resets `reprieve_count=0` when transitioning sessions back to pending, preventing
the escalation guard from triggering immediately after a worker restart.

## Pillar A — In-flight visibility

Four `AgentSession` fields surface the agent's own state so operators
can read what's happening live, no inference required.

| Field | Writer | Notes |
|---|---|---|
| `current_tool_name` | `agent/hooks/pre_tool_use.py` (set), `post_tool_use.py` (clear) | Name of the tool currently in flight, or None between tools. |
| `last_tool_use_at` | both hooks | Bumped on every tool boundary. |
| `last_turn_at` | `agent/sdk_client.py` `result` event | Most recent SDK turn boundary. |
| `recent_thinking_excerpt` | `agent/sdk_client.py` `thinking_delta` | Last 280 chars of extended-thinking content (tweet length). |

> **These writers are repo-scoped — do not treat them as universal.** All
> three hooks live in **this repo's** `.claude/settings.json`, so they
> only fire for a session whose cwd is this repo. A session running against
> any other repo (cyndra-consulting, a client repo) carries `None` in
> `current_tool_name` / `last_tool_use_at` / `last_turn_at` for its entire
> life. Any consumer that reads these fields as a *freshness* signal must
> therefore also consume a repo-independent one — see the deadline clock in
> [Headless Session Runner § Mid-turn tool activity](headless-session-runner.md#mid-turn-tool-activity).

All writes go through `agent/hooks/liveness_writers.py`, which enforces:

- **Per-session 5s in-memory cooldown** to bound Redis write rate under
  tight tool loops. The cooldown is bypassed for PostToolUse
  (`record_tool_boundary(clear=True)`) writes so that a fast PreToolUse →
  PostToolUse pair within the cooldown window cannot leave
  `current_tool_name` populated; the per-tool timeout sub-loop depends on
  PostToolUse always clearing the field promptly to avoid false-positive
  wedges.
- **Best-effort fail-closed.** Every write is wrapped in try/except;
  Redis or Popoto failures log at DEBUG and return False. The hook return
  value is unaffected — the agent never crashes because liveness writes
  failed.
- **No backfill.** Sessions started before this commit lands keep `None`
  on the new fields until their next tool / turn boundary fires.

**Session resolution:** `liveness_writers.py` resolves the in-flight
`AgentSession` via the shared `agent/hooks/session_resolver.py` helper,
which prefers `VALOR_SESSION_ID` (the true `session_id`, looked up with a
direct filter) and falls back to `AGENT_SESSION_ID` (the Popoto AutoKey hex,
looked up via `get_by_id_strict`) when the `VALOR_SESSION_ID` lookup is
absent or empty. The per-tool budget backstop hook (`pre_tool_use.py`) shares
the same resolver.

### Dashboard surfaces

`/dashboard.json`'s `sessions[]` entries carry five keys:

- `current_tool_name` (string | null)
- `last_tool_use_at` (float epoch | null)
- `last_turn_at` (float epoch | null)
- `recent_thinking_excerpt` (string | null)
- `last_evidence_at` (float epoch | null) — derived as `max(last_heartbeat_at,
  last_sdk_heartbeat_at, last_stdout_at, last_tool_use_at, last_turn_at,
  last_compaction_ts)`. None when every contributing field is None.

The dashboard surface also carries a row-level freshness chip (age since
`last_evidence_at`), a ghost badge driven by a non-blocking process-alive
probe, and a modal Liveness section that surfaces seven additional keys:
`exec_pid` (the fenced execution record's pid), `process_alive`,
`last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`,
`recovery_attempts`, `reprieve_count`. See [Dashboard — Liveness Signals](dashboard.md#liveness-signals)
and [`docs/features/agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md).

These keys are an additive JSON addition — extra keys are ignored by typical
consumers.

## Cost backstop

The detector intentionally has no wall-clock kill. The long-run backstop
for genuinely runaway sessions is cost monitoring:
`AgentSession.total_cost_usd` accumulates per-session spend from the SDK
`ResultMessage.usage` and the harness `result` event. The dashboard surfaces
it; an operator-driven alarm can be added if a specific cost ceiling becomes
operationally necessary.

## Test coverage

- `tests/unit/test_session_health_inference_removed.py` — structural
  guards on the removed inference paths.
- `tests/unit/test_agent_session_liveness_fields.py` — model-level
  field roundtrip + default guards.
- `tests/unit/test_pre_tool_use_liveness_writes.py` — hook writer
  behavior + fail-closed + cooldown.
- `tests/unit/test_dashboard_pillar_a_fields.py` — dashboard JSON shape.
- `tests/unit/test_health_check_recovery_finalization.py::TestHasProgressPerTurnSignal` — per-turn
  SDK signal tests: sub-check A (`last_tool_use_at`/`last_turn_at`), sub-check B
  (startup-window `last_heartbeat_at`), and `last_sdk_heartbeat_at` exclusion from Tier 1.
- `tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveEscalation` — reprieve
  escalation guard: suppresses "alive" when `sdk_ever_output=False` and
  `reprieve_count >= MAX_NO_OUTPUT_REPRIEVES`.
- `tests/unit/test_health_check_recovery_finalization.py::TestStartupRecoveryReprieveCountReset` —
  startup recovery resets `reprieve_count=0` to prevent immediate escalation.
- `tests/integration/test_pm_long_run_no_kill.py` — acceptance test for
  a 4+ hour PM with active tool use and no result event.

## State-layer detection (`sdlc-progress-check`)

The Tier 1 / Tier 2 detectors above watch the **process** layer: they catch
wedged PM sessions while the session is technically still running. They do
NOT detect a pipeline whose PM session has already gone terminal but whose PR
is still open and idle. That state-layer gap is closed by a separate
reflection: `sdlc-progress-check` (`reflections/sdlc_progress.py`, registered
in `config/reflections.yaml` at a 30-minute interval).

The reflection iterates every local project and applies a **6-gate stall
heuristic** to each open PR, then an **action ladder** on the ones that fail
all six.

1. **Lane branch.** Head ref is in the `session/` namespace — the repo's
   single lane-branch prefix (`tools/lane_identity.py::lane_branch_name`,
   used by `agent/worktree_manager.py`). The shape of the rest of the name is
   not consulted, so this admits both an issue-derived lane
   (`session/sdlc-2755`) and a human-named one (`session/dev-41a59eee`) into
   the corpus. Which issue a given PR belongs to is answered separately, by
   the issue resolution ladder — see [SDLC Lane Identity](sdlc-lane-identity.md#discovery-reads-identity).
2. **Not draft.** Draft PRs are intentionally paused and excluded.
3. **Last commit age ≥ `SDLC_STALL_THRESHOLD_HOURS`** (default 4h). Resolved
   via `git log -1 --format=%H\ %ct origin/<branch>`, using the PR's actual
   head branch whatever its shape, so the orchestrator doesn't need the
   branch checked out. A branch with no local remote-tracking ref leaves a
   `gate-unknown: branch-not-fetched` finding rather than being dropped in
   silence.
4. **Issue open.** `gh issue view <N> --repo <repo> --json state` returns
   `OPEN`, where `<N>` is the number the issue resolution ladder resolved and
   `<repo>` is the project's resolved GitHub slug — the same `target_repo` the
   ladder itself was scoped by. The `--repo` scoping is deliberate: a bare
   `gh issue view` resolves `GH_REPO` from the environment before cwd, so
   under a foreign `GH_REPO` (or from a checkout that is not the issue's own)
   it would answer about a *different* repository's issue #N and exit 0.
   Closed issues mean the work has landed elsewhere. This gate consumes the
   resolved issue number, so it necessarily runs after the ladder, which is
   why the cheap branch read above it comes first: a fresh, healthy lane is
   discarded on age alone and never pays for identity resolution or a
   `gh issue view`.
5. **Lane liveness, read from the issue lock, not from session rows.**
   `sdlc_progress.py::_lock_says_live` runs a direct `GET` on
   `session:issuelock:{N}` and classifies the payload with
   `models.session_lifecycle._lock_owner_is_live`, the same helper the lock's
   own owner uses. Key absent means not live. A malformed payload or Redis
   error means **unknown**, and unknown always declines to act (skip this
   tick, findings note `gate-unknown: lock-read`).

   This is deliberately a bare `GET`, never a call to `touch_issue_lock`.
   `touch_issue_lock` fails **open**: every Redis exception it swallows comes
   back as `IssueLockResult(acquired=True)`, which this gate would read as
   "lock unheld, not live, act" (exactly backwards during a Redis flap), and
   it also cannot express "unknown" at all. A bare `GET` can only read, never
   claim or renew a lease, which is the stronger read-only guarantee this
   gate needs.

   The lock is the **sole** liveness signal here.
6. **This machine owns the project.**
   `reflections.utilities.machine_owns_project(project_key)` against
   `projects.<key>.machine`. Two machines sharing a checkout must never both
   act on the same lane; a non-owning machine skips the whole project.

### The action ladder

A PR that fails all six gates is genuinely stalled. The reflection then acts
on it, keyed on `(slug, head-sha)`:

1. **Already-escalated check.** A read of `sdlc:stall:escalated:{slug}:{sha}`
   runs *before* the lane can claim an action window. If the key exists, a
   human has already been paged about this head sha and the lane is skipped
   entirely (`already-escalated` finding) — no rung fires, nothing is sent.
   This is what makes "escalate once and stop acting" literal rather than
   "page once while acting forever". An unreadable key is unknown and also
   declines (`gate-unknown: escalation-read`).
2. **Break-glass check.** If `SDLC_STALL_RESUME_ENABLED=false`, the whole
   action ladder is disabled: no rung ever fires, and the lane escalates once
   (`"auto-resume disabled (SDLC_STALL_RESUME_ENABLED=false)"`) and stops,
   same as any other terminal ladder outcome.
3. **Attempt budget check.** If the `(slug, sha)` attempt counter
   (`sdlc:stall:resume:attempts:{slug}:{sha}`, TTL
   `SDLC_STALL_ATTEMPTS_TTL_DAYS`, floored at the escalation TTL) is at or
   over `SDLC_STALL_RESUME_MAX_ATTEMPTS`, escalate once and stop: no rung
   fires. A counter payload that is not an integer reads as *unknown*, not as
   zero.
4. **Rung selection.** `_pick_steer_target` picks `steer`, `resume`, or
   `create` *before* anything claims the action window — the per-tick brakes
   below need to know the target session and the rung kind before they can
   brake on either. Within both the live and the resumable bucket, a session
   whose `slug` **matches the stalled lane** wins over a merely-more-recent
   one, falling back to most-recently-updated only when no same-lane
   candidate exists. `resume_session` transitions the row in place and the
   worker runs it in that row's own `working_dir`, so resuming another lane's
   session would make this issue's work land as commits on that lane's
   branch. A session-query failure reads as `unknown` and declines.
5. **Same-tick brakes**, checked in order, every one of them *before* the
   cooldown claim:
   - **Per-tick target dedupe** (the primary burst guard). `_pick_steer_target`
     queries project-wide with same-lane only a ranking preference, so several
     newly-visible lanes with no same-lane session can all resolve to the
     *same* eng session. Each dispatched target's `session_id` is recorded for
     the tick; a later lane resolving to an already-used target defers
     (`action-cap: ... deferred to next tick (target already dispatched)`).
   - **Per-tick action cap.** `SDLC_STALL_ACTIONS_MAX_PER_TICK` (default 3)
     bounds steer + resume + create combined, for one project, for one tick.
     Secondary to the target dedupe. A lane past the cap defers
     (`action-cap: ... deferred to next tick`).
   - **Create brake.** `SDLC_STALL_CREATE_MAX_PER_TICK` (default 1) bounds
     only the create rung; see "The create rung's guards" below.
   - **Degraded-create suppression.** If the ledger enumeration errored this
     tick, the create rung is suppressed regardless of budget; see "Failure
     tolerance" below.
6. **Action cooldown.** Only a lane that survived every brake above claims
   the window: a `SET NX EX` on
   `sdlc:stall:resume:cooldown:{slug}:{sha}` (TTL
   `SDLC_STALL_RESUME_COOLDOWN_HOURS`), taken immediately before dispatch.
   Because rung selection and every brake now run first, the claim is taken
   exclusively by a lane about to dispatch — a lane deferred by a brake never
   claims it and so never needs to release it. Cooldown live (a concurrent
   tick already claimed it) means skip this tick, no attempt charged; this is
   also the overlapping-tick guard. The narrow window that survives the claim
   — the create rung's pre-create lock re-read finding a benign race, or a
   rung declining after all — still releases the key (`DELETE`), because that
   tick dispatched nothing and owes the hour back.
7. **Rung 1: steer.** A live (non-terminal, non-ledger) eng session for the
   project already exists, so `steer_session(...)` fires with an instruction
   to invoke `/sdlc` for the issue and route one stage.
8. **Rung 2: resume.** No live session, but an eng session in a resumable
   status carries a `claude_session_uuid`, so `resume_session(...)` fires with
   the same instruction.
9. **Rung 3: create.** No steerable or resumable target, so
   `create_session(role="eng", slug=<lane slug>, session_type="eng")` fires,
   subject to the create rung's own guards (below). The slug is the lane's own
   branch-derived slug, passed through unchanged — including a human-named one
   — never re-derived.
10. **Rung 4: escalate.** The dispatched action failed for a non-benign
    reason, so escalate once and stop.

Escalation sends a single Telegram alert to `Eng: Valor` (`_send_alert`, via
the `valor-telegram` CLI) and writes the Redis dedup key
`sdlc:stall:escalated:{slug}:{sha}` (`SET NX`, TTL
`SDLC_STALL_ESCALATION_TTL_DAYS`) so a human hears about a given head sha at
most once, no matter how many ticks keep hitting the exhausted-budget or
action-failed rung.

Every rung's outcome is classified and, on success or failure, charges the
`(slug, sha)` attempt counter: a steer that lands but doesn't move the
pipeline is the steer-storm the budget exists to bound. The one exception is
a **benign race**: another actor (typically `reflections.crash_recovery`, or
the lane re-taking its own issue lock) got there first. A resume failure whose
row re-reads non-terminal, and a create refusal against a lock that is now
live, charge no attempt and fire no escalation; they log a `benign-race`
finding and move on. The steer rung has no benign-race branch: a live
non-ledger target simply succeeds, so every steer failure is a real dead end
and always charges an attempt.

### The create rung's guards

Create is the one capability an operator needs to know this reflection has:
it can mint a brand-new eng session, unattended, for a stalled issue. Five
guards bound that power:

- **Machine ownership** (gate 6, above). A non-owning machine never reaches
  the ladder at all.
- **Pre-create lock re-read.** Gate 5 ran earlier in the tick; the lock can
  be re-acquired between the gate and the create call, and a duplicate lane on
  top of a live one is the worst outcome here. `_attempt_action` re-reads
  `_lock_says_live` immediately before calling `create_session`: unknown
  declines (no create); live is a benign race (no attempt charged).
- **Shared attempt budget.** The create rung is charged against the same
  `(slug, sha)` counter as steer and resume; it does not get its own, larger
  allowance.
- **Per-tick creation cap.** `SDLC_STALL_CREATE_MAX_PER_TICK` (default 1)
  bounds how many *new* sessions one project can mint in a single tick,
  independent of how many stalled PRs it has. A PR that would create past the
  cap is deferred to the next tick (`create-brake` finding), not dropped — and
  because the cooldown claim is only taken after this brake, a braked lane
  never claims it in the first place, so "next tick" means the next tick, not
  the next cooldown window.
- **Degraded-create suppression.** Create is the only rung that writes
  permanent identity, via `adopt_lane_slug`, and that write is no-overwrite —
  an uncorrectable wrong record is a worse outcome than deferring. If the
  per-tick `PipelineLedger` enumeration that feeds the issue resolution ladder
  errored, the create rung is suppressed for every lane in that project's tick
  (`create-suppressed: ... (ledger degraded)` finding) even though steer and
  resume may still proceed. See [SDLC Lane Identity](sdlc-lane-identity.md#discovery-reads-identity)
  for the resolution ladder this guards.
- **Distinct telemetry.** The per-tick summary counts `steered` / `resumed` /
  `created` / `escalated` separately, and every dispatched create logs
  `auto-resume create: {slug}` on success. Creates are never folded into the
  steer/resume counts, so an operator scanning findings can see exactly how
  often the reflection is minting new sessions.

### Failure tolerance

Every external boundary (gh CLI, git, `valor-telegram`, Redis, Popoto query,
`steer_session`, `resume_session`, `create_session`) is wrapped in a narrow
try/except that **logs a warning and continues**; the reflection never raises.
Stricter failure semantics:

- **Redis unavailable for the cooldown, attempts, or escalation keys.** The
  reflection declines to act (skip this tick). Better to under-act during a
  Redis flap than to double-act or spam during one.
- **`AgentSession` query fails** (the ladder's target query). Reads as
  `None`/"unknown", logged as a `gate-unknown` finding, no action taken. Rung
  selection runs before the cooldown claim, so a failed target query never
  claimed the window in the first place; the lane is actionable again on the
  next tick with nothing to release.
- **Branch not present locally.** Skipped with a
  `gate-unknown: branch-not-fetched` finding (the underlying git failure is
  debug-logged). Common during transient worktree state. The finding matters
  because the corpus is now every `session/` branch: an unfetched ref is the
  one condition that could quietly return the detector to seeing nothing, so
  it reports rather than disappears.

### Tunables

| Env var | Default | Meaning |
|---|---|---|
| `SDLC_STALL_THRESHOLD_HOURS` | `4` | Minimum age of last commit before a lane is stall-eligible. |
| `SDLC_STALL_RESUME_ENABLED` | `true` | Break-glass switch. `false` disables the whole action ladder; the reflection still escalates once per `(slug, sha)`. |
| `SDLC_STALL_RESUME_MAX_ATTEMPTS` | `3` | Attempt budget per `(slug, sha)` before escalating and stopping. |
| `SDLC_STALL_CREATE_MAX_PER_TICK` | `1` | Cap on new sessions the create rung may mint per project per tick. |
| `SDLC_STALL_ACTIONS_MAX_PER_TICK` | `3` | Cap on steer + resume + create combined per project per tick. Secondary to the per-tick target dedupe, which is the primary burst guard. |
| `SDLC_STALL_RESUME_COOLDOWN_HOURS` | `1` | Action cooldown per `(slug, sha)`: also the overlapping-tick guard. |
| `SDLC_STALL_ESCALATION_TTL_DAYS` | `30` | TTL on the escalation dedup key; bounds how long "already told a human about this sha" is remembered. |
| `SDLC_STALL_ATTEMPTS_TTL_DAYS` | `30` | TTL on the attempt counter. Read through a floor at `SDLC_STALL_ESCALATION_TTL_DAYS`: **attempts TTL >= escalation TTL** is an invariant, because a budget that re-arms while the escalation key still suppresses the page turns "escalate once and stop" into "act forever, silently". Both keys are sha-scoped, so a new commit re-arms everything anyway. |

Disable the whole reflection by setting `enabled: false` on the
`sdlc-progress-check` entry in `~/Desktop/Valor/reflections.yaml`.

### What this is NOT

- **Never kills anything.** This reflection steers, resumes, and creates eng
  sessions to restart a stalled lane. It has no kill path of its own; recovery
  of a wedged-but-still-running process stays with the Tier 1/Tier 2 detectors
  above.
- **Not a replacement for the Tier 1/Tier 2 detectors above.** The
  process-layer detectors run every 5 minutes and watch live sessions. The
  state-layer reflection runs every 30 minutes and watches pipelines whose PM
  session has already gone terminal.
- **Draft-PR aware, branch-shape agnostic.** Draft PRs are still intentionally
  excluded — they have a different lifecycle. Every other PR whose head branch
  is in the `session/` namespace is in scope, including a human-named lane
  (`session/dev-41a59eee`) that a shape-matching filter could not see at all.

## Confirm subprocess dead before requeue AND before worktree cleanup

When the health check recovers a headless-runner session (`running → pending`,
or `failed` after `MAX_RECOVERY_ATTEMPTS`), the session's detached `claude -p`
process group must be **confirmed dead** before the record is requeued and
before its synthetic-slug worktree is deleted. Two guarantees enforce this:

1. **Requeue gate.** `_apply_recovery_transition` snapshots the fenced
   `exec_pid`/`pid_create_time` (`AgentSession.live_fence`) **before** cancelling
   `SessionHandle.task`. The fence is not cleared between turns, so the snapshot
   stays valid across the unwind; staleness is caught by `fence_is_live`'s
   create_time compare rather than by the field going `None`. It then runs
   `_confirm_subprocess_dead(pid_snapshot)` — **process-group aware**: it
   derives the group via `os.getpgid` and signals the GROUP with `os.killpg`
   (SIGTERM→SIGKILL + liveness probes), so a detached group with grandchildren
   (MCP servers) is fully reaped. A group that will not die escalates the
   session to `failed` instead of parking an invisible orphan at `pending`.
2. **Cleanup ordering (structural, no new gate).** The runner's `_run_one_turn`
   `finally` SYNCHRONOUSLY reaps + confirms its group before `await task._task`
   resolves in the outer executor coroutine, so the executor's synthetic-slug
   cleanup runs strictly after the group is dead. The one residual — a
   pathological unkillable group — leaves a durable `runner_reap_failed` marker
   that the cleanup reads to **skip** deletion (see
   [headless-session-runner.md](headless-session-runner.md#subprocess-lifecycle--teardown-reap)).

**Deliberate no-go: no worker-parented reaper leg.** The orphan reaper's
PPID==1 gate is left unchanged. A worker-parented backstop would still race a
dead session's stale fence against a live session's recycled PID — a cleanup
path that should never fire while carrying a live-kill risk. The existing
PPID==1 reaper still covers genuinely-orphaned (worker-dead) processes.

## One-shot reaper verifies orphanhood before killing

The two `claude --print` one-shot reapers in `agent/session_health.py` — the
fast-cadence `_fast_reap_stale_print_oneshots()` (every health-loop tick) and
the hourly `_reap_orphan_session_processes()` — do not treat age alone as
proof of orphanhood. A single PM turn IS a `claude -p` process and legitimately
runs 14–19 minutes.

**The ownership gate.** Age > `ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS` (600s) is
only a trigger to *investigate ownership*, never to kill:

- **Fast reaper** calls `_oneshot_owner_is_live(pid)`: resolves
  `AgentSession.find_live_session_by_pid(pid)` (a bounded forward scan over
  the low-cardinality `status` index, building an in-process `{live_pid:
  session}` map from each non-terminal row's fence) inside a **bounded lookup**
  (a module-level single-worker executor awaited with
  `ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS = 2.0`), then requires
  `_session_is_alive(session)` (non-terminal + heartbeat fresher than 30 min).
  Live owner → the PID is protected: any prior SIGTERM stage for its
  `(pid, create_time)` tuple is discarded from `_pending_sigkill_orphans` (no
  staging-ledger leak on PID recycling) and an INFO
  `[fast-oneshot-reap] protected live harness PID N — owning session alive`
  line is emitted. Timeout, lookup exception, or `pid=None` all **fail toward
  reapable** — a wedged Redis degrades to cleanup, never a stalled health loop,
  matching `_session_is_alive`'s conservative-False contract.
- **Hourly reaper**: a stale one-shot falls through to the same
  `session is not None and _session_is_alive(session)` gate every other
  signature uses — one `find_live_session_by_pid` lookup, already resolved, no
  redundant second call.

**Cleanup power preserved.** Rogue-subagent one-shots with no owning session
(`find_live_session_by_pid` → None) are still reaped on the same fast cadence —
as are one-shots whose owner is terminal or whose owner's heartbeat is stale
(dead worker).

**Write-side dependency.** The gate reads the fenced execution record
(`exec_pid`/`pid_create_time`), stamped on PM-turn spawn by
`AgentSession.stamp_execution_spawn` (called from
`agent/session_runner/runner.py`'s `_on_turn_spawn`) inside a fail-silent
`try/except`, and not re-stamped again until the next spawn (the heartbeat
writer refreshes only `last_heartbeat_at`). A spawn-time Redis blip that loses
the write would make a live harness look unowned again; hardening that write
is a tracked follow-up. See
[`docs/features/agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md)
for the full fence design.

## See Also

- [`docs/features/agent-session-health-monitor.md`](agent-session-health-monitor.md) — the `_has_progress` + `_tier2_reprieve_signal` detector.
- [`docs/features/headless-session-runner.md`](headless-session-runner.md) — the runner's subprocess-lifecycle contract and teardown reap.
- [`docs/features/bridge-self-healing.md`](bridge-self-healing.md) — the broader recovery model.
- [`docs/features/session-recovery-mechanisms.md`](session-recovery-mechanisms.md) — recovery counters and reprieve telemetry.
- [`docs/features/dashboard.md`](dashboard.md) — the full set of fields exposed on `/dashboard.json`.
- [`docs/features/session-steering.md`](session-steering.md): the sdlc-progress-check action ladder's steer and resume rungs are a consumer of the steering inbox described there (rung 1 calls `steer_session`, the same entry point Telegram reply-thread steering uses).
