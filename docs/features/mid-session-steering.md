# Mid-Session Steering: Real-Time Course Correction for Running Agents

## Overview

Mid-session steering allows a user to send a reply-to message in Telegram that gets injected into a currently running agent session, enabling real-time course correction without waiting for the agent to finish. This is distinct from creating a new job or resuming a completed session.

## How It Works

### End-to-End Flow

1. **User sends a message** that triggers agent work (e.g., "fix the auth bug").
2. **Bridge creates a job**, worker picks it up, session status transitions `pending` -> `running`.
3. **Agent starts executing** -- the session is now in `running` status.
4. **User sends a reply-to message** to steer the agent (e.g., "actually, focus on OAuth specifically").
5. **Bridge steering check** queries for sessions with matching `session_id` in `running` or `active` status.
6. **Match found** -- message is pushed to the Redis steering queue (`steering:{session_id}`).
7. **Acknowledgment sent** -- bridge replies "Adding to current task" (or "Stopping current task." for abort keywords).
8. **PostToolUse hook fires** on the agent's next tool call, pops the steering message from Redis.
9. **Agent receives the steering message** via `client.interrupt()` + `client.query()` and adjusts its behavior.

### Session Status During Steering

The agent session goes through these statuses during execution:

| Status | Set By | Meaning | Steerable? |
|--------|--------|---------|------------|
| `pending` | Job creation | Queued, waiting for worker | No (race window logged) |
| `running` | `_pop_job()` | Worker picked up job, agent executing | **Yes** |
| `active` | `_execute_job()` | Auto-continue deferred | **Yes** |
| `dormant` | Summarizer | Paused on open question | No |
| `completed` | Job completion | Work finished | No |
| `failed` | Error handler | Work failed | No |

The steering check queries for both `running` (primary) and `active` (fallback) statuses, since both represent "agent is currently working."

### Abort Flow

When the user sends a reply containing an abort keyword (`stop`, `cancel`, `abort`, `nevermind`):

1. Bridge detects the abort keyword and sets `is_abort=True` on the steering message.
2. PostToolUse hook pops the message, sees the abort flag.
3. Hook returns `{"continue_": False, "stopReason": "Aborted by user"}`.
4. Agent session terminates.

## Architecture

```
Telegram Reply
    |
    v
Bridge (telegram_bridge.py)
    |-- Query AgentSession: status in ("running", "active")
    |-- Match found? --> push_steering_message() --> Redis steering:{session_id}
    |-- No match? --> Fall through to job queue
    |
Agent (health_check.py - PostToolUse hook)
    |-- Every tool call: pop_steering_message()
    |-- Message found? --> get_active_client() --> client.interrupt() + client.query()
    |-- Abort? --> return {continue_: false}
```

### Key Components

| Component | File | Role |
|-----------|------|------|
| Steering check | `bridge/telegram_bridge.py` | Routes reply-to messages to steering queue |
| Steering queue | `agent/steering.py` | Redis list push/pop/clear operations |
| PostToolUse hook | `agent/health_check.py` | Consumes steering messages on each tool call |
| Client registry | `agent/sdk_client.py` | Stores active SDK clients for interrupt access |

## Error Handling

The steering check in the bridge uses differentiated error handling:

- **`ConnectionError` / `OSError`**: Logged at ERROR level with full traceback. These indicate Redis or database connectivity issues.
- **Other exceptions**: Also logged at ERROR level with traceback for visibility. Previously these were silently swallowed with a WARNING log.
- **No matching session**: Not logged (expected case for non-running threads). The message falls through to the normal job queue.
- **Pending session detected**: Logged at INFO level for observability during the `pending` -> `running` race window.

## Race Conditions

### Race 1: Reply during `pending` -> `running` transition

Between job creation and worker pickup (~100ms), a reply could arrive when the session is in `pending` status. The steering check detects this and logs it. The message falls through to the job queue and will be processed normally.

### Race 2: Reply during session delete/recreate in `_pop_job`

The `_pop_job()` method briefly deletes and recreates the session (two Redis commands). This window is sub-millisecond. If the steering check hits this window, the message falls through to the job queue -- not lost, just slightly delayed.

### Race 3: Reply after job completion but before cleanup

If a steering message arrives just as the agent finishes, `pop_all_steering_messages` cleanup runs and logs any unconsumed messages. The message was too late to affect the agent -- correct behavior.

## Related Documentation

- [Steering Queue](steering-queue.md) -- Original implementation plan and design decisions
- [Coaching Loop](coaching-loop.md) -- Auto-continue and output classification that interacts with session status
- [Agent Session Model](agent-session-model.md) -- Session lifecycle and status transitions
