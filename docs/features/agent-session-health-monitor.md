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
- **No-progress detection (issue #944, extended by #1036 and #1099)**: Even when the worker is alive, a running session past the 300s startup guard is recovered if it shows no progress. `_has_progress(entry)` now uses a **two-tier** detector — see [Bridge Self-Healing §Two-tier no-progress detector](bridge-self-healing.md#two-tier-no-progress-detector) for the full design. In brief:
  - **Tier 1 (dual heartbeat):** either `last_heartbeat_at` (queue-layer) or `last_sdk_heartbeat_at` (messenger-layer) fresh within 90s counts as progress. Both must be stale for the session to be flagged. The original three own-progress signals (`turn_count > 0`, non-empty `log_path`, non-empty `claude_session_uuid`) and the #963 child-activity check are preserved.
  - **Tier 2 (reprieve gates, `no_progress` only):** if Tier 1 flags a session, `_tier2_reprieve_signal()` evaluates three gates in order. Any one passing gate reprieves the kill, increments `reprieve_count`, and emits a `tier2_reprieve_total:{compacting|alive|children}` counter. `worker_dead` recoveries skip Tier 2 entirely. The previous fourth `stdout` gate was retired by issue #1172.
    1. **`compacting`** (issue #1099 Mode 3) — `AgentSession.last_compaction_ts` within `COMPACT_REPRIEVE_WINDOW_SEC` (default 600s). Evaluated first so post-compaction idle periods are never misread as hangs. Companion writer: `agent/hooks/pre_compact.py::pre_compact_hook` (PR #1135) already populates `last_compaction_ts` on every successful backup — no new writer is needed.
    2. **`children`** — `psutil.Process(pid).children()` non-empty. Strongest psutil-based signal.
    3. **`alive`** — `psutil.Process(pid).status()` not in {zombie, dead, stopped}.
  - **Kill path:** cancels `handle.task` from `_active_sessions` registry; captures `pre_bump_attempts = entry.recovery_attempts or 0`, then increments `recovery_attempts`; finalizes as `failed` at `MAX_RECOVERY_ATTEMPTS=2` (history preserved); otherwise transitions `running → pending`. `DISABLE_PROGRESS_KILL=1` suppresses kills while keeping flagging active.
  - **OOM backoff (issue #1099 Mode 4):** when transitioning back to `pending`, if `entry.exit_returncode == -9` AND `pre_bump_attempts == 0` AND `_is_memory_tight()` returns True (available memory < 400MB, cached 5s), the recovery branch sets `entry.scheduled_at = now + 120s` via partial save. The existing pending-scan in `agent/session_pickup.py` already honors `scheduled_at > now` as a "not before" timestamp, so the session is skipped by `_is_eligible` until the 120s elapses — avoiding a thrash loop under sustained memory pressure. The second recovery attempt (`pre_bump_attempts >= 1`) bypasses the defer and proceeds to normal recovery. No new field is introduced for the backoff — `scheduled_at` is reused.
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

### No wall-clock timeout (issue #1172)

The previous `_get_agent_session_timeout` per-session cap (45 min standard,
2.5 hr build) was retired. A session writing fresh heartbeats is allowed to
run as long as it needs. Cost monitoring (`AgentSession.total_cost_usd`)
is the long-run backstop for genuinely runaway sessions. See
[PM Session Liveness](pm-session-liveness.md) for the full philosophy.

### Recovery

When a stuck session is found:

1. Log a warning with the session ID, project key, and reason (`worker_dead` or `no_progress`)
2. Increment the project-scoped Redis counter `{project_key}:session-health:recoveries:{worker_dead|no_progress}` for observability (non-fatal on failure)
3. For `no_progress` recoveries: run Tier 2 reprieve gates — if any gate passes, skip recovery this cycle (reprieve)
4. Cancel the session task via `_active_sessions` registry and wait up to `TASK_CANCEL_TIMEOUT` (0.25s)
5. Increment `recovery_attempts`; if `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (2), finalize as `failed` (history preserved); otherwise transition to `pending` (local sessions finalize as `abandoned`)
6. Call `_ensure_worker()` to restart the processing loop for that project

### Startup Integration

The health check loop starts automatically with the **worker process** (`python -m worker`), alongside the session notify listener and session watchdog. Both the health loop and notify listener run as background asyncio tasks in the worker:

- **Session notify listener** (`_session_notify_listener()` in `agent/agent_session_queue.py`): Subscribes to the `valor:sessions:new` Redis pub/sub channel. Extracts `worker_key` from the payload and calls `_ensure_worker(worker_key, is_project_keyed)` immediately — ~1s pickup latency. This is the fast path for normal operation. Uses a **dedicated** `redis.Redis` connection with `socket_timeout=None` so `pubsub.listen()` blocks indefinitely between messages, instead of inheriting the global `POPOTO_REDIS_DB` pool's `socket_timeout=5` (which would cause a reconnect cycle and a guaranteed message-loss window — issue #824).
- **Agent session health monitor** (`_agent_session_health_loop()` in `agent/session_health.py`, re-exported from `agent_session_queue.py`): Runs every 5 minutes. Recovers sessions missed by pub/sub (Redis restart, worker not running at publish time, bypass paths). This is the safety net. The task is named `session-health-monitor` and registers a `done_callback` (`_health_task_done`) that logs ERROR if the loop exits unexpectedly with an exception (cancellation during shutdown is ignored). This mirrors the `_notify_task_done` pattern on `notify_task` and prevents silent loss of health monitoring.
- **Session watchdog** (`monitoring/session_watchdog.py`): Monitors `AgentSession` objects at the application level (separate from queue-level monitoring)

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
| `HEARTBEAT_FRESHNESS_WINDOW` | 90s | Either heartbeat within this window = progress |
| `COMPACT_REPRIEVE_WINDOW_SEC` | 600s | `last_compaction_ts` within this window = Tier 2 `compacting` reprieve (issue #1099) |
| `HEARTBEAT_WRITE_INTERVAL` | 60s | How often `_heartbeat_loop` writes `last_heartbeat_at` |
| `MAX_RECOVERY_ATTEMPTS` | 2 | Kills before session is finalized as `failed` |
| `TASK_CANCEL_TIMEOUT` | 0.25s | Grace period after `handle.task.cancel()` |
| `_MEMORY_CACHE_TTL_SEC` | 5s | Cache TTL for `_is_memory_tight()` psutil syscall (issue #1099) |

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
