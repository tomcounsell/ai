# Agent Session Health Monitor

Automatically detects and recovers stuck running sessions in the Redis-based agent session queue.

## Overview

The agent session health monitor runs as a periodic async task alongside the bridge process. Every 5 minutes, it scans all `running` AND `pending` AgentSessions to check:

1. Whether the associated worker coroutine is still alive
2. Whether the session has any progress evidence (heartbeats, own-progress fields, live children)
3. Whether pending sessions have a worker that can process them

The previous wall-clock per-session timeout was retired by issue #1172 — see
[PM Session Liveness](pm-session-liveness.md) for the evidence-only detector
philosophy. Cost monitoring is the long-run backstop for genuinely runaway
sessions.

This is the **single unified recovery mechanism** — it replaces six competing recovery functions that previously raced against each other. See [Bridge Resilience](bridge-resilience.md) for the full refactoring context.

When a stuck running session is detected, it is automatically recovered by deleting it and re-creating it as `pending`. When an orphaned pending session is found (no live worker), a worker is started for it.

## How It Works

### Detection

- **Dead worker detection**: Checks `_active_workers[worker_key]` asyncio Task liveness via `.done()`. If the task has finished (crashed, cancelled, or completed), the session is considered orphaned.
- **No-progress detection (issue #944, extended by #1036, #1099, #1226, #1356, #1614)**: Even when the worker is alive, a running session past the 300s startup guard is recovered if it shows no progress. `_has_progress(entry)` uses a **two-tier** detector with two sub-checks at Tier 1 — see [Bridge Self-Healing §Two-tier no-progress detector](bridge-self-healing.md#two-tier-no-progress-detector) for the full design. In brief:
  - **Tier 1 sub-check A (per-turn SDK progress, #1226):** `last_tool_use_at` (PreToolUse/PostToolUse hooks) or `last_turn_at` (sdk_client `result` event) fresher than `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s, 30 min) counts as progress. `last_sdk_heartbeat_at` (the BackgroundTask watchdog tick) is intentionally NOT a progress signal — it proves only that the subprocess exists.
  - **Tier 1 sub-check B (startup-window heartbeat fallback, #1036, narrowed by #1226 / #1724 D0 gate / #1905 / #1935):** When `sdk_ever_output` is False — i.e. `agent.session_runner.liveness.derive_sdk_ever_output(entry)` is False because none of `last_tool_use_at`, `last_turn_at`, or `last_stdout_at` has ever been set (issue #1935 added `last_stdout_at`, the headless stream's per-event signal, as the third OR-input) — `last_heartbeat_at` (queue-layer, written by `_heartbeat_loop`) fresh within `HEARTBEAT_FRESHNESS_WINDOW` (90s) counts as progress, **subject to the D0 never-started gate**. **#1724 D0 gate:** `_never_started_past_grace(entry, now=now_utc)` — a session where `sdk_ever_output=False` AND `running_seconds > NEVER_STARTED_GRACE_SECS (120) + NEVER_STARTED_CONFIRM_MARGIN_SECS (30)` (150s) — returns `False` unconditionally from sub-check B, bypassing the fresh-heartbeat fast-path and routing the session to the D0 recovery branch in the 30s tool-timeout sub-loop. For never-started-past-grace sessions, a fresh heartbeat alone does not prove progress. INCRs `{project_key}:session-health:tier1_falloff:never_started_grace_exceeded`. As of issue #1905, the gate is called with the same trusted `now_utc` clock sub-check B's own `running_seconds` computation uses, so the two agree on elapsed time. For gate survivors, the function reads `started_ref = entry.started_at or entry.created_at` (fallback is load-bearing — the recovery path nulls `started_at`) and applies the fast-path while either `started_ref` is None (a phantom record predating this field) OR `running_seconds < STARTUP_GRACE_SECONDS` (300s, aliased to `AGENT_SESSION_HEALTH_MIN_RUNNING`, env-tunable) — unconditionally true for a gate survivor, since surviving `running_seconds` (<= 150s) is always below this 300s window. Issue #1905 pruned the #1356 grace-to-budget band (`STARTUP_GRACE_SECONDS <= running_seconds <= NO_OUTPUT_BUDGET_SECONDS`) and its `tier1_falloff` budget-exceeded telemetry counter as provably unreachable once the D0 gate and this leg share one clock. This bounds the previously-unbounded fresh-heartbeat fast-path that allowed cwd-disappearance and similar wedges to hold Tier 1 open indefinitely (parent investigation #1246).
  - **Own-progress fields and child-activity check (#1614):** `turn_count > 0`, non-empty `log_path`, non-empty `claude_session_uuid` are evaluated only when `sdk_ever_output` is False AND `last_heartbeat_at` is within the last `NO_OUTPUT_BUDGET_SECONDS` (1800s). These fields are sticky once set, but are now **gated on heartbeat freshness** — a stale or absent heartbeat means the executor loop has likely exited, so own-progress fields must not keep the session alive indefinitely (#1614 Branch 2 fix). The child-activity check (`#963`) is unconditional and is evaluated regardless of heartbeat freshness.
  - **#1614-leg evidence-based hang veto (issue #2071):** before honoring any sticky own-progress field above, `_has_progress` probes the recorded subprocess via `subprocess_hang_verdict(entry.claude_pid, session_key, caller="has_progress")`. This leg is only ever reached for an **orphan** — the #944 shared-`worker_key` orphan net in `_agent_session_health_check` consults `_has_progress` solely when no live in-scope handle exists (`in_scope_handle is None`: the owning worker died mid-cold-start and a fresh worker reused its `worker_key`, so `worker_alive=True`). Such an orphaned `claude -p` can be **alive-but-hung** (flat CPU, no children, no established API socket) while `last_heartbeat_at` is still younger than the 1800s budget; without the veto the sticky field (typically `claude_session_uuid`) would hold it alive for the full ~1800s. A positive `hung` verdict makes `_has_progress` skip the sticky-field returns and fall through to recovery on the third flat poll (**~90s vs ~1800s**). It is strictly a stronger *release* condition: any other verdict (`progressing` / `unknown` / no-pid) honors the sticky fields **exactly as before** — the veto never shortens the non-hung hold and never lowers the 1800s gate, and never raises (a malformed/None `claude_pid` coerces to None → `unknown` → honored). The probe is evidence-only (#1172), owner-gated (#2098), and keyed `caller="has_progress"` so its flat-count stays independent of the Tier-2/Fix#3 probers. `_tier2_reprieve_signal` is deliberately **unchanged** — a `claude_pid` fallback there was proven (plan critique) to regress the fast no-`uuid` recovery case from immediate-recover to a spurious reprieve. A confirmed-hung `no_progress` recovery on the zombie profile (`claude_session_uuid` set, `sdk_ever_output=False`) still routes through the existing `zombie_uuid_no_output` counter (see [Recovery](#recovery)).
  - **Tier 2 (reprieve gates, `no_progress` only):** if Tier 1 flags a session, `_tier2_reprieve_signal()` evaluates three gates in order. Any one passing gate reprieves the kill, increments `reprieve_count`, and emits a `tier2_reprieve_total:{compacting|alive|children}` counter. `worker_dead` recoveries skip Tier 2 entirely. The previous fourth `stdout` gate was retired by issue #1172.
  - **Sibling consolidation, not this monitor (issue #2004 Task 2):** `derive_sdk_ever_output` above was already this monitor's own shared leaf. Issue #2004 separately unified the two OTHER "has this session progressed" predicates that used to be hand-forked copies of each other — `agent/session_stall_classifier.py` and `agent/crash_signature.py`'s `_has_demonstrable_progress` — behind a second, narrower leaf in the same `agent/session_runner/liveness.py` module: `has_demonstrable_activity(entry, *, freshness_window=None)`, reading only `{turn_count, last_tool_use_at}`. This monitor's own progress signal and call sites (above) are unchanged. See [Session Recovery Mechanisms](session-recovery-mechanisms.md), [Stall Advisory Classifier](stall-advisory-classifier.md#live-never-started-detection), and [Crash-Signature Auto-Resume](crash-signature-auto-resume.md#progress-fields-ground-truth).
    1. **`compacting`** (issue #1099 Mode 3) — `AgentSession.last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (default 600s). Evaluated first so post-compaction idle periods are never misread as hangs. Companion writer: `agent/hooks/pre_compact.py::pre_compact_hook` (PR #1135) already populates `last_compaction_ts` on every successful backup — no new writer is needed.
    2. **`children`** — `psutil.Process(pid).children()` non-empty. Strongest psutil-based signal.
    3. **`alive`** — `psutil.Process(pid).status()` not in {zombie, dead, stopped}.
  - **Kill path:** cancels `handle.task` from `_active_sessions` registry; captures `pre_bump_attempts = entry.recovery_attempts or 0`, then increments `recovery_attempts`; finalizes as `failed` at `MAX_RECOVERY_ATTEMPTS=2` (history preserved); otherwise transitions `running → pending`. `DISABLE_PROGRESS_KILL=1` suppresses kills while keeping flagging active.
  - **OOM backoff (issue #1099 Mode 4):** when transitioning back to `pending`, if `entry.exit_returncode == -9` AND `pre_bump_attempts == 0` AND `_is_memory_tight()` returns True (available memory < 400MB, cached 5s), the recovery branch sets `entry.scheduled_at = now + 120s` via partial save. The existing pending-scan in `agent/session_pickup.py` already honors `scheduled_at > now` as a "not before" timestamp, so the session is skipped by `_is_eligible` until the 120s elapses — avoiding a thrash loop under sustained memory pressure. The second recovery attempt (`pre_bump_attempts >= 1`) bypasses the defer and proceeds to normal recovery. No new field is introduced for the backoff — `scheduled_at` is reused.
- **Semaphore-wedge forensic log (issue #1808):** When the pending-session branch runs (`worker_alive = True` → `event.set(); continue`) and `_global_session_semaphore._value == 0` with `running_count < MAX_CONCURRENT_SESSIONS`, a `WARNING: PENDING-WEDGE FINGERPRINT` is emitted (always-on, no env flag, fail-silent). This fires only for the leaked-slot condition — it does **not** fire for normal back-pressure (`running_count >= MAX_CONCURRENT_SESSIONS`), which logs at `INFO` instead. The `event.set(); continue` recovery decision is unchanged. See [Worker Wedge Investigation](worker-wedge-investigation.md) for the full root-cause context, and enable `WORKER_ASYNCIO_DEBUG=1` (in `worker/__main__.py`) for asyncio slow-callback detection when the event loop itself is suspected.
- **Orphan Subprocess Reap (issue #1218)**: The two scans above ask "for each row whose Redis status is `running`/`pending`, is the worker still alive?". The orphan reap pass runs the **inverse** scan at the end of each health tick: "for each subprocess in `_active_sessions`, is the owning `AgentSession.status` already in `TERMINAL_STATUSES`?". If yes (and the session is past the 60s grace window), the SDK subprocess is SIGTERM'd and the handle is popped from `_active_sessions`. This catches the failure mode where a `claude -p` subprocess survives indefinitely after its owning row reaches `completed`/`failed`/`killed`/`abandoned`/`cancelled` — typically because the `_execute_agent_session` `finally` block did not fire (asyncio task hang, externally-finalized session, etc.) — and the now-orphaned subprocess holds its `worker_key` "occupied", blocking every subsequent session for that project.
  - **Two-tick SIGTERM → SIGKILL escalation (`_pending_sigkill`):** SIGTERM is sent on tick N; the PID is added to a module-level `_pending_sigkill: set[int]` set. At the **start** of tick N+1 (5 min later), the set is **snapshotted, cleared, and drained**: each PID receives SIGKILL exactly once, then is unconditionally discarded — even if SIGKILL hit `ProcessLookupError` (already dead), `PermissionError`, or any other error. macOS recycles PIDs within ~5 minutes, so retaining a PID across more than one tick risks SIGKILLing an unrelated new process. One-shot drain, no retry, no accumulation.
  - **60s grace window (`ORPHAN_REAP_GRACE_SECONDS`):** sessions whose `updated_at` is within 60s of `now` are skipped this tick — the natural teardown in `_execute_agent_session` is given time to pop its own handle. Under healthy conditions the grace window is never reached because the `finally` block runs first.
  - **`{project_key}:session-health:orphan_subprocess_reaped` Redis counter** is incremented per reap (matching the established `{project_key}:session-health:{metric}` prefix used by `recoveries`, `kill_total`, `tier1_flagged_total`, `tier2_reprieve_total`).
  - **Kill switch:** `DISABLE_ORPHAN_REAP=1` short-circuits the entire pass (parity with `DISABLE_PROGRESS_KILL`).
  - **Distinction from `_cleanup_orphaned_claude_processes()`:** that startup-only function reaps `claude` processes whose **PPID is 1** — i.e., **cross-process** orphans whose worker died and got reparented to init. The orphan-subprocess reap pass covers the **in-process** case: handles whose parent worker is still alive but whose owning session row went terminal without the subprocess exiting. The two are complementary and run on different schedules.
- **Cross-Process Orphan Reap (issue #1271)**: This is the **third** reaper, complementary to the corrupted-record pass and the in-process orphan reap above. It runs hourly inside `cleanup_corrupted_agent_sessions()` (the existing `agent-session-cleanup` reflection) and scans the **OS process table** for processes whose `PPID == 1` AND whose `cmdline` matches `claude_agent_sdk/_bundled/claude` or `mcp_servers/*.py`. Implemented in `agent/session_health.py::_reap_orphan_session_processes`.
  - **Per-PID heartbeat gate (`find_by_claude_pid`):** before killing any candidate, the reaper looks up the owning `AgentSession` via the new indexed `claude_pid` field on `AgentSession`. The PID is set on the `_on_sdk_started` callback (in `agent/session_executor.py`) and cleared in `models/session_lifecycle.py::finalize_session`. If the owning session has `last_heartbeat_at` younger than `ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS` (1800s = 30 min), the kill is skipped — the parent process appearing as PID 1 may be a transient handover artifact and the session is provably alive. For MCP candidates without a direct `claude_pid` mapping, the reaper inherits the parent process's session via `proc.parent().pid`.
  - **Positive-ID self-protection (`worker:registered_pid:*`):** the worker writes its own PID to `worker:registered_pid:{hostname}:{pid}` (TTL 24h) at startup AND on every heartbeat tick (in `register_worker_pid()`). The reaper builds a `skip_pids` set from `os.getpid()` PLUS every `worker:registered_pid:*` Redis value before scanning. This is a **structural** defense: even if a future code change re-adds the worker pattern to the cmdline regex set, the worker is never reaped because its PID is in the skip-set. This is required because on macOS every launchd-respawned worker has `PPID == 1` by design (`launchd` is PID 1 and `com.valor.worker.plist` sets `KeepAlive=true`), so a worker-signature + PPID==1 filter would otherwise match every live worker.
  - **Descendant-tree walk:** before SIGTERM-ing the parent, the reaper captures `proc.children(recursive=True)`. Parent and all descendants are then `terminate()`d via psutil (PID-reuse-safe at construction time); `(pid, create_time)` tuples are staged on the module-level `_pending_sigkill_orphans: set[tuple[int, float]]`.
  - **Two-tick SIGKILL escalation with create-time verification:** at the **start** of every reflection tick, the staged set is snapshotted, cleared, and drained. For each `(pid, staged_create_time)`, the reaper reconstructs `psutil.Process(pid)` and compares `proc.create_time() == staged_create_time` (within `1e-3` epsilon). If they match, `proc.kill()` (SIGKILL); if they differ, the SIGKILL is **skipped** because macOS recycled the PID to an unrelated process. Always clears the staged set after drain regardless of outcome — a PID never lives across more than one tick.
  - **Two-counter scheme:**
    - When the owning session is known via `find_by_claude_pid`: increment `{project_key}:session-health:orphan_process_reaped` (project-scoped, accurate attribution).
    - When the owning session is unknown (the common case for true unowned orphans): increment `session-health:orphan_process_reaped:{worker_hostname}` (hostname-scoped, no false project attribution).
  - **Kill switch:** `DISABLE_ORPHAN_PROCESS_REAP=1` short-circuits the entire pass (parity with `DISABLE_ORPHAN_REAP` for the in-process reaper).
  - **Distinction from the other two orphan reapers:**
    - **vs. `_pending_sigkill` reap (#1218):** the in-process reap iterates `_active_sessions` (handles tracked by THIS worker) and asks "is the owning row terminal?". It cannot detect orphans whose parent worker is gone (because those handles never existed on the new worker). The cross-process reap covers exactly that gap.
    - **vs. `monitoring/bridge_watchdog.py::kill_zombie_processes()`:** the bridge watchdog runs every 60s and kills `claude`/`pyright` processes older than 2h via raw `os.kill`. The cross-process reap runs every 60min, scopes by PPID==1 + heartbeat-stale + signature, walks descendant trees, and uses psutil for PID-reuse safety. Both swallow `ProcessLookupError`/`NoSuchProcess` so double-kill is safe.
  - **Worker process reaping is intentionally OUT OF SCOPE.** See "Solution → Desired outcome" in `docs/plans/sdlc-1271.md` for the full rationale: under launchd `KeepAlive=true`, every live worker has PPID==1, so worker-signature + PPID==1 matching would self-suicide every reflection tick. Stranded sibling workers are reparented by launchd already.
- **Race condition guard**: Jobs must be running for at least 5 minutes (`AGENT_SESSION_HEALTH_MIN_RUNNING`) before they become eligible for recovery. This prevents false positives on jobs that just started processing.
- **Per-Tool Timeout Sub-Loop (issue #1270, parallel to Tier 1/Tier 2)**: A dedicated 30-second sub-loop (`_agent_session_tool_timeout_loop` in `agent/session_health.py`) detects sessions whose `current_tool_name` is non-null but whose `last_tool_use_at` exceeds a tier-specific budget. The PreToolUse hook fired (so we know which tool is in flight) but PostToolUse never returned. Without this check, the session keeps passing Tier 1 sub-check A in `_has_progress` for up to `SDK_PROGRESS_FRESHNESS_WINDOW` (30 min) while making no real progress.
  - **Tier classification (`_classify_tool_tier`):**
    - `mcp__` prefix → `"mcp"` (any Model Context Protocol tool)
    - `{ToolSearch, Read, Glob, Grep, Edit, Write, NotebookEdit}` → `"internal"` (lightweight built-ins that should never legitimately exceed 30s)
    - everything else (`Bash`, `Task`, `Skill`, `WebFetch`, ...) → `"default"`
  - **Budgets:** internal 30s, mcp 120s, default 300s. Each is env-tunable via `TOOL_TIMEOUT_INTERNAL_SEC`, `TOOL_TIMEOUT_MCP_SEC`, `TOOL_TIMEOUT_DEFAULT_SEC`.
  - **Cadence:** dedicated 30s sub-loop in worker startup (parallel to the 5-min main loop), so the 30s internal budget can fire within one tick of expiry. The main loop's psutil/OOM/orphan-reap checks stay on their original cadence — running them at 30s would ~10x the load.
  - **Race mitigation:** before transitioning, the sub-loop re-reads `current_tool_name` and `last_tool_use_at` from a fresh query. If PostToolUse fired between the iterator's read and this point — clearing `current_tool_name` or refreshing `last_tool_use_at` — the recovery is aborted for the tick.
  - **Companion writer cooldown:** `agent/hooks/liveness_writers.py::record_tool_boundary` bypasses its 5s per-session cooldown for `clear=True` (PostToolUse) writes so a fast PreToolUse → PostToolUse pair within the cooldown window does not leave `current_tool_name` populated and produce a false-positive wedge.
  - **Counters:** three `IntField` counters on `AgentSession` (`tool_timeout_count_internal`, `..._mcp`, `..._default`) cumulate per-tier hits for the session's lifetime. A project-scoped Redis counter `{project_key}:session-health:tool_timeouts:{tier}` mirrors the existing `recoveries:{kind}` pattern for dashboards.
  - **Recovery path:** routes through the shared `_apply_recovery_transition` helper with `reason_kind="tool_timeout"`. Tier 2 reprieve is skipped (the wedge condition itself is the evidence), but `MAX_RECOVERY_ATTEMPTS`, the OOM-defer, the response-delivered finalize-instead-of-recover guard, and the `DISABLE_PROGRESS_KILL` kill-switch all still apply uniformly.
  - **Kill switch:** `TOOL_TIMEOUT_TIERS_DISABLED=1` short-circuits the entire sub-loop (parity with `DISABLE_PROGRESS_KILL` for the main loop).
  - **v1 limitations:** the single-slot `current_tool_name` field cannot represent two concurrent tools (Tool A wedged, Tool B fired before A returned would hide A). Per-`tool_use_id` in-flight registries and synthetic `tool_result` injection are explicitly out of scope. Hard recovery (`running → pending`) is the v1 behavior; the recovered session restarts from `pending` without a "your tool wedged" steering message.
  - **Wedge-signal reset on requeue (issue #1762):** `_apply_recovery_transition` clears both `current_tool_name = None` and `last_tool_use_at = None` on the `AgentSession` row before the OOM-defer check, at both save sites (normal requeue and OOM-deferred requeue). Without this reset, `_check_tool_timeout` re-reads the stale durable fields on the very next 30s tick and immediately fires again — exhausting `MAX_RECOVERY_ATTEMPTS=2` and finalizing as `failed` before the resumed session can take a single turn (the symptom was "health check: 2 recovery attempts, never progressed (kind=tool_timeout)"). The tailer (`agent/hooks/liveness_writers.py::record_tool_boundary`) is diff-gated: it only overwrites these fields when a new `tool_use` event appears in the transcript. Because each `run()` call starts a fresh transcript with a new UUID, the tailer cannot clear the wedge signal left by the previous run — the explicit reset in the recovery branch is the correct fix site. Both fields must be cleared together: a partial clear (e.g. only `current_tool_name`) still leaves `last_tool_use_at` stale, which re-trips the budget check on the next tick since the timeout is computed as `now - last_tool_use_at`.

### No wall-clock timeout (issue #1172)

The previous `_get_agent_session_timeout` per-session cap (45 min standard,
2.5 hr build) was retired. A session writing fresh heartbeats is allowed to
run as long as it needs. Cost monitoring (`AgentSession.total_cost_usd`)
is the long-run backstop for genuinely runaway sessions. See
[PM Session Liveness](pm-session-liveness.md) for the full philosophy.

### Post-init hang tradeoff (issue #2071 sub-item 2 — documented, deferred)

The evidence-based subprocess-hang probe (`subprocess_hang_verdict`, both the
Tier-2 `caller="health"`/`"fix3"` probers from #2069 and the #1614-leg
`caller="has_progress"` veto from #2071) only fast-recovers sessions where
`sdk_ever_output` is False — i.e. sessions that have **not yet produced any SDK
output**. A session that HAS produced output and then hangs (a genuine
*post-init* hang) is intentionally NOT fast-recovered by the probe: it may be
legitimately blocked on a first-token network wait to a non-443 endpoint (a
local model, a proxy, or a custom base URL) with flat CPU and no qualifying
socket, and recovering it there would false-kill mid-call. Post-init hangs are
instead bounded by the 1800s `NO_OUTPUT_BUDGET_SECONDS` freshness deadline (and,
for the whole turn, the runner's turn deadline).

The mitigation for the non-443 case is the env-tunable `HANG_PROBE_API_PORTS`
(`agent/session_runner/liveness.py`, default `"443,8443"`): a fleet routing
cold-start traffic through a non-standard port registers it there so an
in-flight model call still counts as `progressing`. A tighter, socket-state-aware
post-init probe that could catch an Anthropic-443 post-init hang *faster* than
the 1800s deadline is deliberately **deferred** — it risks false-killing
legitimate non-443 blocks, and the decision to build it is gated on a
human-reviewed read of real-world `HANG_PROBE_API_PORTS` hang telemetry rather
than being codeable now.

### Recovery

When a stuck session is found:

1. Log a warning with the session ID, project key, and reason (`worker_dead` or `no_progress`)
2. Increment the project-scoped Redis counter `{project_key}:session-health:recoveries:{worker_dead|no_progress}` for observability (non-fatal on failure). For `no_progress` recoveries on sessions matching the zombie profile (`claude_session_uuid` set but `sdk_ever_output=False`), an additional `{project_key}:session-health:recoveries:zombie_uuid_no_output` counter is also incremented (#1614) — this distinguishes normal startup-window recoveries from the stale-zombie case.
3. For `no_progress` recoveries: run Tier 2 reprieve gates — if any gate passes, skip recovery this cycle (reprieve)
4. Cancel the session task via `_active_sessions` registry and wait up to `TASK_CANCEL_TIMEOUT` (0.25s)
5. Increment `recovery_attempts`; if `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (2), finalize as `failed` (history preserved); otherwise transition to `pending` (local sessions finalize as `abandoned`)
6. Call `_ensure_worker()` to restart the processing loop for that project

### Startup Integration

The health check loop starts automatically with the **worker process** (`python -m worker`), alongside the session notify listener and session watchdog. Both the health loop and notify listener run as background asyncio tasks in the worker:

- **Session notify listener** (`_session_notify_listener()` in `agent/agent_session_queue.py`): Subscribes to the `valor:sessions:new` Redis pub/sub channel. Extracts `worker_key` from the payload and calls `_ensure_worker(worker_key, is_project_keyed)` immediately — ~1s pickup latency. This is the fast path for normal operation. Uses a **dedicated** `redis.Redis` connection with `socket_timeout=None` so `pubsub.listen()` blocks indefinitely between messages, instead of inheriting the global `POPOTO_REDIS_DB` pool's `socket_timeout=settings.timeouts.redis_socket_s` (default 5s, `.env`-overridable via `TIMEOUTS__REDIS_SOCKET_S` — see [Config Timeout Catalog](config-timeout-catalog.md)) (which would cause a reconnect cycle and a guaranteed message-loss window — issue #824). After `subscribe()`, the listener verifies `PUBSUB NUMSUB >= 1` on its own connection (up to 3 retries, ~300 ms total). If NUMSUB remains 0, a WARNING is logged and the function returns early so the outer loop re-subscribes after its 5 s backoff. Post-subscribe drift (NUMSUB→0 after a previously-good subscribe) is left to this health monitor's 300 s backstop (issue #1804).
- **Agent session health monitor** (`_agent_session_health_loop()` in `agent/session_health.py`, re-exported from `agent_session_queue.py`): Runs every 5 minutes. Recovers sessions missed by pub/sub (Redis restart, worker not running at publish time, bypass paths). This is the safety net. The task is named `session-health-monitor` and registers a `done_callback` (`_health_task_done`) that logs ERROR if the loop exits unexpectedly with an exception (cancellation during shutdown is ignored). This mirrors the `_notify_task_done` pattern on `notify_task` and prevents silent loss of health monitoring.
- **Session watchdog** (`monitoring/session_watchdog.py`): Monitors `AgentSession` objects at the application level (separate from queue-level monitoring)

### Single-owner actuation (issue #2098)

`_agent_session_health_check()` is registered **both** as the worker's in-process loop (`_agent_session_health_loop`) **and** as the out-of-process `session-liveness-check` reflection (`config/reflections.yaml`), which runs inside `python -m reflections` (`com.valor.reflection-worker`). Every detection branch keys off the **process-local** `_active_workers` / `_active_sessions` registries, which are populated only inside the owning worker. In the reflection process they are empty, so — before the guard — every running session looked `worker_dead` (false `running->pending` recovery) and every pending session looked worker-less (spawning a **competing** queue worker via `_ensure_worker`). This was the confirmed root cause of the #2091 double-owner incident, where the reflection process and the real worker resumed the same Claude conversation concurrently.

The guard: `_agent_session_health_check()` returns immediately when the process is the reflection worker (`VALOR_REFLECTION_WORKER=1`, set in `reflections/__main__.py`) **and** has not marked itself the owning worker. The worker's `_agent_session_health_loop` calls `mark_owning_worker_process()` before its first tick, so the worker is never gated even if it inherited the env marker; direct callers (unit tests) set neither and actuate normally. The worker already runs this exact check in-process every tick, and the read-only orphan-reap tail is a no-op against an empty registry, so the reflection copy loses nothing. Regression coverage: `tests/unit/test_session_liveness_single_owner.py`.

### Done Callback — `_health_task_done`

`health_task` is registered with a `_health_task_done` done_callback (added in #825, mirroring the identical pattern on `notify_task`):

```python
def _health_task_done(t: asyncio.Task) -> None:
    if t.cancelled():
        return  # Normal shutdown path
    exc = t.exception()
    if exc is not None:
        logger.error("Health monitor task exited unexpectedly: %s", exc)

health_task.add_done_callback(_health_task_done)
```

The callback guards against unexpected task exits that bypass the health loop's own `except Exception` handler — specifically `BaseException` subclasses (`SystemExit`, `KeyboardInterrupt`) and asyncio-internal exits. Ordinary exceptions are already caught inside the loop's `while True / try-except` block and cannot escape. On normal `SIGTERM` shutdown, `health_task.cancel()` triggers `CancelledError`, which the `if t.cancelled(): return` guard suppresses so no false ERROR is logged.

## CLI Usage

```bash
# Show current queue state
python -m agent.agent_session_queue --status

# Recover all stuck running sessions (orphaned workers)
python -m agent.agent_session_queue --flush-stuck

# Recover a specific session by ID
python -m agent.agent_session_queue --flush-session <SESSION_ID>
```

### Example `--status` output

Sessions are grouped by `worker_key` (the canonical routing key — `project_key`, `chat_id`, or `slug` depending on session type, slug, and current stage). Each header shows the session's `project_key` followed by the actual `worker_key` in parentheses, so slug-keyed sessions (dev sessions, and PM sessions at worktree stages) are visibly distinct from the project-keyed loop.

```
=== valor (worker: valor) ===
  Worker: alive
  [  running] abc123 (running 5m) - How do I configure...
  [  pending] def456 (queued 2m) - Please review...

=== valor (worker: worker-key-slug-precedence) ===
  Worker: alive
  [  running] xyz789 (running 3m) - Implement worker_key...

Total: 3 sessions (1 pending, 2 running)
```

## Configuration

Constants in `agent/session_health.py` (re-exported from `agent_session_queue.py`):

| Constant | Default | Description |
|----------|---------|-------------|
| `AGENT_SESSION_HEALTH_CHECK_INTERVAL` | 300 (5 min) | How often the health check runs |
| `AGENT_SESSION_HEALTH_MIN_RUNNING` | 300 (5 min) | Min runtime before recovery eligible |
| `HEARTBEAT_FRESHNESS_WINDOW` | 90s | `last_heartbeat_at` within this window = sub-check B progress (subject to the D0 never-started gate, #1724) |
| `COMPACT_REPRIEVE_WINDOW_SEC` | 600s | `last_compaction_ts` within this window = Tier 2 `compacting` reprieve (issue #1099) |
| `HEARTBEAT_WRITE_INTERVAL` | 60s | How often `_heartbeat_loop` writes `last_heartbeat_at` |
| `SDK_PROGRESS_FRESHNESS_WINDOW` | 1800s (30 min) | Per-turn signals (`last_tool_use_at`, `last_turn_at`) within this window = sub-check A progress (env-tunable, #1226) |
| `MAX_NO_OUTPUT_REPRIEVES` | 20 | `SDK_PROGRESS_FRESHNESS_WINDOW // HEARTBEAT_FRESHNESS_WINDOW`. Tier-2 reprieve cap for `sdk_ever_output=False` sessions; also feeds `NO_OUTPUT_BUDGET_SECONDS` (#1356) |
| `NO_OUTPUT_BUDGET_SECONDS` | 1800s (30 min) | `MAX_NO_OUTPUT_REPRIEVES * HEARTBEAT_FRESHNESS_WINDOW`. Outside sub-check B's scope (its grace-to-budget band was pruned in #1905, subsumed by the D0 gate); still used by the #1614 own-progress heartbeat gate and the Tier-2 reprieve cap |
| `STARTUP_GRACE_SECONDS` | 300s (= `AGENT_SESSION_HEALTH_MIN_RUNNING`) | `running_seconds` below this threshold preserves the sub-check B fresh-heartbeat fast-path unconditionally for D0-gate survivors (env-tunable, #1356, gate added by #1724) |
| `MAX_RECOVERY_ATTEMPTS` | 2 | Kills before session is finalized as `failed` |
| `TASK_CANCEL_TIMEOUT` | 0.25s | Grace period after `handle.task.cancel()` |
| `_MEMORY_CACHE_TTL_SEC` | 5s | Cache TTL for `_is_memory_tight()` psutil syscall (issue #1099) |
| `TOOL_TIMEOUT_LOOP_INTERVAL` | 30s | Per-tool timeout sub-loop tick cadence (issue #1270) |
| `TOOL_TIMEOUT_INTERNAL_SEC` | 30s | Budget for internal-tier tools (`Read`, `Glob`, ..., env-tunable) |
| `TOOL_TIMEOUT_MCP_SEC` | 120s | Budget for MCP-tier tools (`mcp__*`, env-tunable) |
| `TOOL_TIMEOUT_DEFAULT_SEC` | 300s | Budget for default-tier tools (`Bash`, `Task`, ..., env-tunable) |
| `NEVER_STARTED_CONFIRM_MARGIN_SECS` | 30s | Additional margin after `NEVER_STARTED_GRACE_SECS` before D0 recovery fires (env-tunable via `NEVER_STARTED_CONFIRM_MARGIN_SECS`; defined in `session_stall_classifier.py`, imported by `session_health.py`) — issue #1724 |

> **Retired by issue #1172:** `STDOUT_FRESHNESS_WINDOW`,
> `FIRST_STDOUT_DEADLINE`, `AGENT_SESSION_TIMEOUT_DEFAULT`,
> `AGENT_SESSION_TIMEOUT_BUILD`, and the `_get_agent_session_timeout`
> helper are gone. The detector no longer infers failure from staleness;
> see [PM Session Liveness](pm-session-liveness.md).

## Related

- [scale-agent-session-queue-with-popoto-and-worktrees.md](scale-agent-session-queue-with-popoto-and-worktrees.md) -- The underlying Redis agent session queue
- [session-watchdog.md](session-watchdog.md) -- Session-level health monitoring (complementary layer)
- [bridge-self-healing.md](bridge-self-healing.md) -- Bridge process-level health monitoring
- [agent-session-model.md](agent-session-model.md) -- AgentSession model fields and lifecycle
- `agent/session_health.py` -- Health monitor and startup recovery implementation
- `agent/agent_session_queue.py` -- Queue entry points (re-exports from session_health and other modules)
- Issue #127 -- Original tracking issue
- Issue #944 -- No-progress recovery for sessions stuck behind a shared-worker-key PM
- Issue #1036 -- Two-tier no-progress detector (dual heartbeat + Tier 2 reprieve gates)
- Issue #1099 -- Harness failure hardening: adds the `compacting` Tier 2 gate and OOM-pressure backoff via `exit_returncode == -9` + `scheduled_at`. See also [session-recovery-mechanisms.md](session-recovery-mechanisms.md) for the companion Mode 1 / Mode 2 changes on the SDK client
