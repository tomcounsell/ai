# Worker Service

Standalone worker process for processing AgentSession records from Redis without requiring a Telegram connection.

## Overview

The worker extracts the session execution engine from the bridge monolith into an independently runnable service. Developer workstations run just the worker. Bridge machines run bridge + embedded worker (backward compatible). Future platform bridges (email, Slack) become thin I/O adapters that enqueue work to the same shared worker.

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
    │   + worker) │  │            │  │  (email/etc) │
    └─────────────┘  └────────────┘  └──────────────┘
```

### Entry Points

- **Standalone worker**: `python -m worker` -- processes sessions, writes output to log files
- **Bridge + embedded worker**: `python bridge/telegram_bridge.py` -- processes sessions, sends output via Telegram
- **Both paths share**: `agent/agent_session_queue.py` for queue logic, `agent/output_handler.py` for output routing

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
| `FileOutputHandler` | `agent/output_handler.py` | Writes output to `logs/worker/{session_id}.log` |
| `LoggingOutputHandler` | `agent/output_handler.py` | Logs output via Python logging (stderr) |
| Telegram callbacks | `bridge/telegram_bridge.py` | Sends output via Telegram (registered at bridge startup) |

### Registration

```python
from agent.agent_session_queue import register_callbacks
from agent.output_handler import FileOutputHandler

# New style: pass an OutputHandler instance
register_callbacks("my-project", handler=FileOutputHandler())

# Old style: pass raw callables (backward compatible)
register_callbacks("my-project", send_callback=my_send, reaction_callback=my_react)
```

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
| Bridge machine | Bridge + embedded worker | Telegram |
| Both | Health monitor runs in worker process | Session recovery |

### Constraint

Do not run both the standalone worker and the bridge on the same machine for the same project. They would both start worker loops for the same chat_id without coordination (the `_active_workers` dict is process-local). Bridge machines should use the bridge's embedded worker.

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
