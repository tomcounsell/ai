# Bridge/Worker Architecture

**Status**: Shipped (issue #750)

## Overview

The system separates Telegram I/O from session execution into two independent processes:

- **Bridge** (`bridge/telegram_bridge.py`): Receives Telegram messages, routes them, enqueues `AgentSession` records to Redis. Delivers replies via registered output callbacks.
- **Worker** (`python -m worker`): Polls Redis for pending sessions, executes them via the Claude Agent SDK, handles all session lifecycle functions.

Communication between the two processes happens exclusively through Redis. The bridge never calls worker execution functions; the worker never touches Telegram.

## Data Flow

```
Telegram → Bridge (Telethon)
              ↓ enqueue_agent_session()
           Redis (AgentSession record, status=pending)
              ↓ worker health loop / event
           Worker (_ensure_worker → _worker_loop)
              ↓ Claude Agent SDK
           Output (FileOutputHandler writes session output)
              ↓ registered callbacks
           Bridge (delivers reply to Telegram)
```

## Bridge Responsibilities (only)

1. Authenticate with Telegram and receive messages
2. Route messages to projects via `find_project_for_chat()`
3. Call `enqueue_agent_session()` — writes `AgentSession` to Redis
4. Register output callbacks via `register_callbacks(project_key, handler=...)`
5. Deliver replies via Telegram when callbacks fire
6. Run `ReflectionScheduler` for background maintenance tasks
7. Run `KnowledgeWatcher` for work-vault file change monitoring
8. Run message catchup scan and reconciler on startup

The bridge does **not**:
- Call `_ensure_worker()`, `_recover_interrupted_agent_sessions_startup()`, `_agent_session_health_loop()`, `_session_notify_listener()`, or `_cleanup_orphaned_claude_processes()`
- Call `AgentSession.rebuild_indexes()`
- Poll Redis for orphaned sessions
- Kill or manage Claude SDK subprocesses

## Worker Responsibilities (only)

The worker's startup sequence is deterministic:

| Step | Function | Purpose |
|------|----------|---------|
| 1 | `AgentSession.rebuild_indexes()` | Repair stale/corrupt Redis index entries |
| 2 | `cleanup_corrupted_agent_sessions()` | Remove malformed session records |
| 3 | `_recover_interrupted_agent_sessions_startup()` | Reset running sessions to pending (orphaned from prior process) |
| 4 | `_cleanup_orphaned_claude_processes()` | Kill orphaned Claude SDK subprocesses (PPID=1) |
| 5 | `_ensure_worker(chat_id)` for each pending session | Kick per-chat worker loops for queued sessions |
| 6 | `_agent_session_health_loop()` | Background task: periodic session health checks, orphan detection (safety net) |
| 7 | `_session_notify_listener()` | Background task: subscribe to `valor:sessions:new` pub/sub, wake worker on new session (~1s pickup) |

At runtime, the worker processes sessions via `_worker_loop(chat_id)` until the queue is empty, then waits for new enqueue events.

## Chat Serialization and Worker Deduplication

Each `chat_id` has at most one active `_worker_loop` task at any time. This is the **chat serialization invariant**: all sessions belonging to the same chat are processed strictly in FIFO order, never concurrently. The invariant is enforced by `_ensure_worker()` through a dual-guard mechanism:

| Guard | What it covers |
|-------|---------------|
| `_active_workers[chat_id]` | **Steady-state**: task exists and `.done()` is False — already running, do nothing. |
| `_starting_workers` (set) | **Startup race**: `create_task()` has been called but the task has not yet registered itself in `_active_workers`. A second call that arrives in the same event-loop turn sees this flag and returns without spawning another task. |

Because `_ensure_worker()` is a plain synchronous function (no `await`), the check-and-set of both guards is atomic within the cooperative asyncio event loop. This is particularly important during the health-check loop, which may iterate many pending sessions sharing the same `chat_id` and call `_ensure_worker()` for each one before any task is live in `_active_workers`.

**Lifecycle of `_starting_workers`:**

1. Added immediately before `asyncio.create_task()`.
2. Removed synchronously right after the task is registered in `_active_workers` (fast path — clears it before any re-entrant call can see it).
3. Also removed via a `done_callback` as a safety net in case the task finishes before the synchronous removal runs (degenerate edge case).
4. Removed in the `except` block if `create_task()` itself raises, so the set never leaks.

The `_worker_loop` removes itself from `_active_workers` in its `finally` block. After it exits, the next call to `_ensure_worker()` (triggered by the next enqueue or the health check) starts a fresh task.

## Session Pickup: Fast Path vs Safety Net

The worker uses two mechanisms to discover new sessions:

| Mechanism | Latency | How It Works |
|-----------|---------|-------------|
| **Redis pub/sub** (fast path) | ~1 second | `_push_agent_session()` publishes `{"chat_id", "session_id"}` to `valor:sessions:new`. `_session_notify_listener()` subscribes and calls `_ensure_worker(chat_id)` immediately. |
| **Health check loop** (safety net) | Up to 10 minutes | `_agent_session_health_loop()` fires every 300s. Sessions pending longer than 300s trigger `_ensure_worker(chat_id)` recovery. |

The fast path covers normal operation. The health check catches edge cases: missed pub/sub messages (network blip, worker restart during publish), sessions created by paths that bypass `_push_agent_session()`, and sessions orphaned from a prior worker process.

**Bridge path**: `enqueue_agent_session()` → `_push_agent_session()` publishes notification → worker receives within ~1s.

**CLI path** (`python -m tools.valor_session create`): Same — `_push_agent_session()` publishes to `valor:sessions:new` → worker receives within ~1s. Prior to issue #778, CLI-created sessions relied solely on the health check (worst case: 10 minutes).
## Redis Communication Contract

The bridge and worker share a single contract: the `AgentSession` Popoto model in Redis.

| Field | Bridge writes | Worker reads |
|-------|--------------|-------------|
| `status` | `pending` (on enqueue) | Transitions: pending → running → complete/failed |
| `project_key` | Yes | Yes (routes to registered callbacks) |
| `chat_id` | Yes | Yes (per-chat worker isolation) |
| `message_text` | Yes | Yes (passed to Claude) |
| `session_type` | Yes | Yes (PM/dev/teammate persona selection) |
| `queued_steering_messages` | Any process | Worker injects at turn boundary |

The bridge also reads `AgentSession.status` to determine if a session is already active (dedup logic).

## Import Boundary

The bridge imports from `agent.agent_session_queue` are allowlisted to these functions only:
- `enqueue_agent_session` — enqueue new sessions
- `maybe_send_revival_prompt` — send a revival prompt to a dormant session
- `queue_revival_agent_session` — enqueue a revival session from a reply
- `cleanup_stale_branches` — clean up stale git branches on startup
- `register_callbacks` — register output delivery callbacks
- `clear_restart_flag` — clear stale update restart flag

Any function imported by the bridge that is not on this list is a violation of the boundary. The bridge does **not** import execution functions. If you see `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop`, `_session_notify_listener`, or `_cleanup_orphaned_claude_processes` imported in `bridge/telegram_bridge.py`, that is a regression.

This boundary is enforced by `tests/unit/test_worker_entry.py::TestImportDecoupling::test_bridge_has_no_execution_function_imports`, which uses an allowlist to catch any unauthorized additions.

## Operator CLI

### Queue Status

```bash
python -m tools.agent_session_scheduler status
python -m tools.agent_session_scheduler list --status pending
python -m tools.agent_session_scheduler list --status pending --sort priority
python -m tools.agent_session_scheduler list --status pending,running --sort fifo
```

The `--sort` flag accepts: `priority` (by priority tier then FIFO), `fifo` (creation order), `status`. When sorting by `priority` or `fifo`, each pending session includes a `fifo_position` field showing its rank within its priority band.

### Session Management

```bash
# Bump a session to urgent priority and reset FIFO position
python -m tools.agent_session_scheduler bump --agent-session-id <ID>
python -m tools.agent_session_scheduler bump --agent-session-id <ID> --priority high

# Cancel a pending session
python -m tools.agent_session_scheduler cancel --agent-session-id <ID>

# Kill a running session
python -m tools.agent_session_scheduler kill --agent-session-id <ID>
python -m tools.agent_session_scheduler kill --all

# Clean up old terminal sessions
python -m tools.agent_session_scheduler cleanup --age 30 --dry-run
python -m tools.agent_session_scheduler cleanup --age 30
```

### Session Inspection (valor_session)

```bash
python -m tools.valor_session list                          # All sessions (shows priority column)
python -m tools.valor_session list --status pending         # Filter by status
python -m tools.valor_session status --id <ID>              # Full session details
python -m tools.valor_session steer --id <ID> --message "..." # Inject steering message
python -m tools.valor_session kill --id <ID>                # Kill a session
```

### Service Management

```bash
./scripts/valor-service.sh status          # Check both bridge and worker
./scripts/valor-service.sh restart         # Restart bridge, watchdog, and worker
./scripts/valor-service.sh worker-restart  # Restart worker only
./scripts/valor-service.sh worker-status   # Worker-specific status
```

## Worker Exit Code and launchd Restart Behavior

The worker exits with **code 1** when shut down via SIGTERM (e.g., by `./scripts/valor-service.sh worker-restart`). This is intentional.

launchd's `ThrottleInterval` (configured at 10 seconds in `com.valor.worker.plist`) only applies to **non-zero exits**. A zero exit is treated as voluntary success and triggers launchd's internal ~10-minute default throttle, causing the worker to be unavailable for up to 10 minutes after a normal restart.

**How it works:**
- A module-level flag `_shutdown_via_signal` in `worker/__main__.py` is set to `True` only on SIGTERM.
- After `asyncio.run(_run_worker(...))` returns, `main()` checks the flag and calls `sys.exit(1)` if it is set.
- SIGINT (developer Ctrl-C) leaves the flag unset and exits 0 — a voluntary stop during development should not be penalized with a forced restart.
- `stop_worker()` in `scripts/valor-service.sh` uses `launchctl bootout` (the modern macOS API) to remove the worker from the launchd domain, consistent with `scripts/install_worker.sh`.

**Result:** Worker killed via SIGTERM restarts within 15 seconds (10s `ThrottleInterval` + margin) rather than the ~10-minute default.

## Deployment Notes

Both the bridge and worker must run simultaneously for sessions to be executed. If only the bridge is running, sessions will queue in Redis but not be processed until the worker starts. The existing launchd watchdog (`com.valor.bridge-watchdog`) auto-restarts the bridge; a separate launchd service (`com.valor.worker`) auto-restarts the worker.

To verify both are running:

```bash
launchctl list | grep "valor"
```

Expected output:
```
<PID>  0  com.valor.bridge
<PID>  0  com.valor.worker
<PID>  0  com.valor.bridge-watchdog
```

## Background: Prior Separation Efforts

| Effort | What It Did | Why It Was Incomplete |
|--------|-------------|----------------------|
| PR #737 | Created `worker/__main__.py`, moved session execution there | Did not remove execution imports from bridge; bridge still called `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `rebuild_indexes` at startup |
| Issue #741 | Added graceful shutdown and persistent event loop to worker | Addressed worker robustness only; bridge coupling was out of scope |
| Issue #750 | Enforced the import boundary: removed all execution calls from bridge, consolidated full startup sequence in worker | Complete separation achieved |

The root cause of prior incompleteness: each effort treated the worker as additive — creating worker capability without stripping bridge capability. This issue enforced the boundary at the import level.
