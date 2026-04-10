# Worker Service

Standalone worker process for processing AgentSession records from Redis without requiring a Telegram connection.

## Overview

The worker extracts the session execution engine from the bridge monolith into an independently runnable service. Developer workstations run just the worker. Bridge machines run both bridge and worker: the bridge handles Telegram I/O only, and the worker processes all sessions. Future platform bridges (email, Slack) become thin I/O adapters that enqueue work to the same shared worker.

## Architecture

```
                    ┌─────────────────────┐
                    │   Redis (sessions)   │
                    └──────┬──────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
    ┌─────────▼──┐  ┌──────▼─────┐  ┌──▼──────────┐
    │   Bridge    │  │  Standalone │  │  Future      │
    │  (Telegram  │  │   Worker   │  │  Bridge      │
    │   I/O only) │  │            │  │  (email/etc) │
    └─────────────┘  └────────────┘  └──────────────┘
```

### Entry Points

- **Standalone worker**: `python -m worker` -- processes sessions, delivers output via `TelegramRelayOutputHandler` (Redis outbox for Telegram delivery) with `FileOutputHandler` dual-write for audit logs
- **Bridge** (I/O only): `python bridge/telegram_bridge.py` -- handles Telegram I/O, registers output callbacks, enqueues sessions; requires a running worker to process them
- **Both processes share**: `agent/agent_session_queue.py` for queue logic, `agent/output_handler.py` for output routing

## OutputHandler Protocol

All output destinations implement the `OutputHandler` protocol defined in `agent/output_handler.py`:

```python
class OutputHandler(Protocol):
    async def send(self, chat_id: str, text: str, reply_to_msg_id: int, session: Any = None) -> None: ...
    async def react(self, chat_id: str, msg_id: int, emoji: str | None = None) -> None: ...
```

### Built-in Implementations

| Handler | Location | Purpose |
|---------|----------|---------|
| `TelegramRelayOutputHandler` | `agent/output_handler.py` | Writes JSON to Redis outbox (`telegram:outbox:{session_id}`) for bridge relay delivery; wraps `FileOutputHandler` for dual-write |
| `FileOutputHandler` | `agent/output_handler.py` | Writes output to `logs/worker/{session_id}.log` |
| `LoggingOutputHandler` | `agent/output_handler.py` | Logs output via Python logging (stderr) |
| Telegram callbacks | `bridge/telegram_bridge.py` | Sends output via Telegram (registered at bridge startup) |

### Registration

```python
from agent.agent_session_queue import register_callbacks
from agent.output_handler import FileOutputHandler, TelegramRelayOutputHandler

# Telegram-connected projects: relay to Redis outbox (dual-write to file log)
register_callbacks("my-project", handler=TelegramRelayOutputHandler(file_handler=FileOutputHandler()))

# Non-Telegram / dev projects: file log only
register_callbacks("my-project", handler=FileOutputHandler())

# Old style: pass raw callables (backward compatible)
register_callbacks("my-project", send_callback=my_send, reaction_callback=my_react)
```

## Worker Modes

The worker supports two modes controlled by the `VALOR_WORKER_MODE` environment variable:

| Mode | Env Var | Behavior | Use Case |
|------|---------|----------|----------|
| **Standalone** | `VALOR_WORKER_MODE=standalone` | Waits indefinitely for new work; never exits on empty queue | launchd service, persistent daemon |
| **Bridge** | Not set (default) | Exits after drain timeout (1.5s) when queue is empty | Worker on bridge machines (short-lived drain after enqueue) |

Standalone mode is set automatically by `python -m worker`. In this mode, nudge re-enqueues are processed within milliseconds (no 10s launchd restart gap), enabling full SDLC pipeline execution end-to-end.

### Graceful Shutdown

On SIGTERM (e.g., `./scripts/valor-service.sh worker-restart` or `/update` restart):

1. `request_shutdown()` sets a flag and wakes all waiting workers
2. Workers finish their current session (no mid-session kills)
3. `_run_worker()` awaits all active worker loops with a 60s timeout
4. If timeout expires, remaining tasks are cancelled (triggers cleanup)
5. Sessions that were pending but not started remain as "pending" in Redis for next startup
6. `main()` calls `sys.exit(1)` — signals launchd to apply `ThrottleInterval` (10s) rather than the default ~10-minute throttle

SIGINT (developer Ctrl-C) exits with code 0 — a voluntary developer stop should not trigger a forced restart. Only SIGTERM sets the `_shutdown_via_signal` flag that causes the non-zero exit.

## CLI Usage

```bash
# Process all projects from projects.json
python -m worker

# Process a specific project only
python -m worker --project valor

# Validate config and exit (no processing)
python -m worker --dry-run
```

## Service Management

### Installation

```bash
./scripts/install_worker.sh
```

This installs `com.valor.worker` as a launchd service that auto-starts on boot.

### Commands

```bash
./scripts/valor-service.sh worker-start     # Start the worker
./scripts/valor-service.sh worker-stop      # Stop the worker
./scripts/valor-service.sh worker-restart   # Restart the worker
./scripts/valor-service.sh worker-status    # Check worker status
./scripts/valor-service.sh worker-logs      # Tail worker logs
```

### Update Integration

The `/update` command (`scripts/update/run.py`) automatically manages the worker service alongside the bridge:

- **Full update** (`--full`): Installs worker plist, verifies worker starts (polls up to 10s)
- **Cron update** (`scripts/remote-update.sh`): Bootout old worker, substitute paths, bootstrap new
- **Python API**: `scripts/update/service.py` exposes `install_worker()`, `restart_worker()`, `get_worker_status()`, `is_worker_running()`

The worker is installed after reflections and before stale session cleanup in the update sequence.

### Logs

| Log | Path | Content |
|-----|------|---------|
| Worker log | `logs/worker.log` | Worker lifecycle events, startup, shutdown |
| Worker errors | `logs/worker_error.log` | Unhandled exceptions |
| Session output | `logs/worker/{session_id}.log` | Per-session agent output (timestamps, reactions) |

## Deployment Topology

| Machine Type | Services | Output |
|-------------|----------|--------|
| Dev workstation | Worker only | File logs |
| Bridge machine | Bridge + Worker (both required) | Telegram |
| Both | Health monitor runs in worker process | Session recovery |

### Requirement

Bridge machines MUST run both the bridge and the standalone worker. The bridge is I/O only and does not process sessions on its own. Without the worker running alongside it, sessions will be enqueued but never executed.

## Import Decoupling

The session execution engine (`agent/agent_session_queue.py`) has zero module-level imports from `bridge/`. Constants and utilities were moved to shared locations:

| What | Canonical Location | Re-export Location |
|------|-------------------|-------------------|
| `REACTION_SUCCESS/COMPLETE/ERROR` | `agent/constants.py` | `bridge/response.py` |
| `save_session_snapshot()` | `agent/session_logs.py` | `bridge/session_logs.py` |

All 7 lazy `bridge/` imports inside functions are guarded with `try/except` for graceful degradation when running without Telegram.

## Related

- [Agent Session Queue Reliability](agent-session-queue.md) -- queue architecture
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- health check loop
- [Bridge Self-Healing](bridge-self-healing.md) -- crash recovery patterns
- [Deployment](deployment.md) -- multi-instance configuration
