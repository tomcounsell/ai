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
- Call `_ensure_worker()`, `_recover_interrupted_agent_sessions_startup()`, `_agent_session_health_loop()`, or `_cleanup_orphaned_claude_processes()`
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
| 6 | `_agent_session_health_loop()` | Background task: periodic session health checks, orphan detection |

At runtime, the worker processes sessions via `_worker_loop(chat_id)` until the queue is empty, then waits for new enqueue events.

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

The bridge imports from `agent.agent_session_queue`:
- `enqueue_agent_session` — enqueue new sessions
- `register_callbacks` — register output delivery callbacks
- `clear_restart_flag` — clear stale update restart flag

The bridge does **not** import execution functions. If you see `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop`, or `_cleanup_orphaned_claude_processes` imported in `bridge/telegram_bridge.py`, that is a regression.

This boundary is enforced by `tests/unit/test_worker_entry.py::TestImportDecoupling::test_bridge_has_no_execution_function_imports`.

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
