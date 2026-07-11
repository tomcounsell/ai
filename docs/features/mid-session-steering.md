# Mid-Session Steering: Real-Time Course Correction for Running Agents

**Scope:** Telegram bridge reply-thread steering via Redis list (`steering:{session_id}`). For PM→child steering, see `session-steering.md`.

**See also:** [Session Steering](session-steering.md) — canonical reference for the turn-boundary inbox model and parent-child steering

> **Note:** The `client.interrupt()` / `client.query()` / `get_active_client()` mechanism described in the Architecture and Key Components sections below belonged to the persistent-`ClaudeSDKClient` SDK path, which was deleted wholesale in #2000 (see [HarnessAdapter Seam](harness-adapter.md)). Steering delivery today is exclusively the Redis-list turn-boundary drain described in [Session Steering](session-steering.md) — the worker pops the steering list and injects it as the next turn's input; there is no mid-turn `interrupt()` call. The high-level end-to-end flow (bridge → steering queue → agent adjusts) still holds; only the delivery mechanics below are out of date.

## Overview

Mid-session steering allows a user to send a reply-to message in Telegram that gets injected into a currently running agent session, enabling real-time course correction without waiting for the agent to finish. This is distinct from creating a new session or resuming a completed session.

## How It Works

### End-to-End Flow

1. **User sends a message** that triggers agent work (e.g., "fix the auth bug").
2. **Bridge enqueues a session**, worker picks it up, session status transitions `pending` -> `running`.
3. **Agent starts executing** -- the session is now in `running` status.
4. **User sends a reply-to message** to steer the agent (e.g., "actually, focus on OAuth specifically").
5. **Bridge steering check** queries for sessions with matching `session_id` in `running` or `active` status.
6. **Match found** -- message is pushed to the Redis steering queue (`steering:{session_id}`).
7. **Acknowledgment sent** -- bridge attaches a 👀 emoji reaction to the user's message (or 🫡 for abort keywords). No inline text reply is emitted; the eventual real reply lands in the thread as a normal PM-authored message.
8. **PostToolUse hook fires** on the agent's next tool call, pops the steering message from Redis.
9. **Agent receives the steering message** via `client.interrupt()` + `client.query()` and adjusts its behavior.

### Session Status During Steering

The agent session goes through these statuses during execution:

| Status | Set By | Meaning | Steerable? |
|--------|--------|---------|------------|
| `pending` | Session creation | Queued, waiting for worker | No (race window logged) |
| `running` | `_pop_agent_session()` | Worker picked up session, agent executing | **Yes** |
| `active` | `_execute_agent_session()` | Auto-continue deferred | **Yes** |
| `dormant` | Summarizer | Paused on open question | No |
| `completed` | Session completion | Work finished | No |
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
    |-- No match? --> Fall through to session queue
    |
Worker (turn-boundary drain — see session-steering.md)
    |-- pop_all_steering_messages() at the start of the next turn
    |-- Message found? --> injected as the turn's input (get_response_via_harness)
    |-- Abort keyword? --> session terminates
```

### Key Components

| Component | File | Role |
|-----------|------|------|
| Steering check | `bridge/telegram_bridge.py` | Routes reply-to messages to steering queue |
| Steering queue | `agent/steering.py` | Redis list push/pop/clear operations |
| Turn-boundary drain | `agent/session_runner/` | Worker pops the steering list at the start of the next turn (retired the PostToolUse-hook/`get_active_client()` SDK-era path, deleted in #2000) |

## Error Handling

The steering check in the bridge uses differentiated error handling:

- **`ConnectionError` / `OSError`**: Logged at ERROR level with full traceback. These indicate Redis or database connectivity issues.
- **Other exceptions**: Also logged at ERROR level with traceback for visibility. Previously these were silently swallowed with a WARNING log.
- **No matching session**: Not logged (expected case for non-running threads). The message falls through to the normal session queue.
- **Pending session detected**: Logged at INFO level for observability during the `pending` -> `running` race window.

## Race Conditions

### Race 1: Reply during `pending` -> `running` transition

Between session creation and worker pickup (~100ms), a reply could arrive when the session is in `pending` status. The steering check detects this and logs it. The message falls through to the session queue and will be processed normally.

### Race 2: Reply during status transition in `_pop_agent_session`

The `_pop_agent_session()` method transitions the session from `pending` to `running` via in-place mutation (`transition_status()`), since `status` is an IndexedField and no longer a KeyField. The window is sub-millisecond. If the steering check hits this window, the message falls through to the session queue -- not lost, just slightly delayed.

### Race 3: Reply after session completion but before cleanup

If a steering message arrives just as the agent finishes, `pop_all_steering_messages` cleanup runs and logs any unconsumed messages. The message was too late to affect the agent -- correct behavior.

## Related Documentation

- [Steering Queue: Historical Spec](steering-implementation-spec.md) -- Original implementation plan and design decisions
- [Bridge Workflow Gaps](bridge-workflow-gaps.md) -- Auto-continue and output classification that interacts with session status
- [Agent Session Model](agent-session-model.md) -- Session lifecycle and status transitions
- [Telegram Integration → Inbound attachments](telegram.md#inbound-attachments--steering-enrichment--auto-ingest) -- Files (documents, photos, voice notes) sent as reply-to interjections are enriched and auto-ingested before reaching the steering queue (issue #1215)
