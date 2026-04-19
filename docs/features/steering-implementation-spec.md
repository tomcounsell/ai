---
status: Implemented
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-02
tracking: https://github.com/tomcounsell/ai/issues/23
---

# Steering Queue: Historical Design Specification

> **Historical Design Specification** — This document records the original design decisions, Redis key structure, watchdog hook design, and SDK client registry rationale from the initial implementation of session steering. For the current operational reference, see [Session Steering](session-steering.md). For the end-to-end Telegram reply-thread flow, see [Mid-Session Steering](mid-session-steering.md).

**Scope:** Redis list steering queue design and bridge coalescing. SDK-harness mid-turn injection (secondary path; the current canonical path is the turn-boundary inbox in `session-steering.md`). For PM→child steering, see `session-steering.md`.

## Problem

When Valor is executing a long task (10-30+ minutes), the supervisor cannot course-correct mid-execution. Messages sent during a running session either:
1. Start a duplicate session (wasting compute and causing git conflicts)
2. Get queued as a completely new task (losing the thread context)
3. Are silently ignored

**Current behavior:**
- User sends "fix the auth bug" → Valor starts working
- 5 minutes in, user replies "actually, focus on the OAuth provider specifically"
- That reply starts a *new* session or queues as a separate task
- Valor finishes the original task without the course correction, wasting 20+ minutes

**Desired outcome:**
- Reply-thread messages to a running session get injected into that session in real-time
- Non-reply messages during a running session get queued with an acknowledgment
- The supervisor can abort a running session with "stop" or "cancel"

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Solution

### Key Elements

- **Steering Redis Queue**: Per-session Redis list (`steering:{session_id}`) that accumulates reply-thread messages while a session runs
- **Watchdog Queue Check**: The existing `watchdog_hook` (fires every tool call) checks the steering queue and injects messages or aborts
- **Bridge Routing Logic**: `handle_new_message` detects reply-to-running-session and routes to the steering queue instead of creating a new session
- **Receipt Acknowledgments**: Brief ack messages telling the supervisor what happened with their message

### Flow

**Steering (reply to running session):**

User replies to Valor's "acknowledged" message → Bridge checks if session is active → Push to `steering:{session_id}` Redis list → Ack: "Adding to current task" → Watchdog picks up on next tool call → SDK `client.interrupt()` + `client.query(steering_message)` → Agent continues with new context

**Follow-up (new message while session running):**

User sends new mention (not a reply) → Bridge sees active session for project → Enqueue as normal session → Ack: "Queued — will start after current task finishes"

**Pending session merge (#619):**

User sends two messages in quick succession (< 8s) → First message enqueues as pending job → Second message arrives before worker pops the first → Bridge detects pending session within `PENDING_MERGE_WINDOW_SECONDS` (8s) → Push to `steering:{session_id}` Redis list → Ack: "Adding to current task" → When worker pops the job, drain-on-start logic calls `pop_all_steering_messages()` and prepends follow-up text to `message_text` → Agent sees the combined message on first run

For messages arriving within ~200ms (before the first session is written to Redis), an in-memory coalescing guard (`_recent_session_by_chat`) bridges the Redis visibility gap. See `docs/features/semantic-session-routing.md` for details on the coalescing guard.

This covers both the reply-to fast path (direct Telegram replies to pending sessions) and the intake classifier path (non-reply follow-ups detected by Haiku). The 8s window prevents stale pending sessions from absorbing unrelated messages.

**Abort:**

User replies "stop" or "cancel" → Bridge pushes abort signal to steering queue → Watchdog picks up → SDK `client.interrupt()` → Session marked as aborted → Ack: "Stopped"

### Technical Approach

#### 1. Steering Queue Model (new Redis structure)

Use Redis lists via `popoto.redis_db.POPOTO_REDIS_DB` directly (not a Model — these are transient queues, not queryable entities):

```
Key:    steering:{session_id}
Type:   Redis List (RPUSH to add, LPOP to consume)
Values: JSON strings: {"text": "...", "sender": "...", "timestamp": ..., "is_abort": false}
TTL:    None (no expiry — messages persist until consumed or explicitly cleared)
```

Why not a popoto Model: steering messages are ephemeral, consumed once, and don't need indexing or querying beyond FIFO consumption. A Redis list is the right primitive.

#### 2. Bridge Changes (`bridge/telegram_bridge.py`)

In `handle_new_message`, after detecting `is_reply_to_valor`, the bridge resolves the canonical root session_id via `resolve_root_session_id()` (see `docs/features/session-management.md`):

```python
if is_reply_to_valor and message.reply_to_msg_id:
    # Walk the reply chain to find the original human message's session_id.
    # This handles replies to Valor's responses correctly — uses Popoto cache
    # first, falls back to Telegram API, falls back to reply_to_msg_id directly.
    session_id = await resolve_root_session_id(
        client, event.chat_id, message.reply_to_msg_id, project_key
    )

    # Check if this session is currently running
    active_sessions = AgentSession.query.filter(session_id=session_id, status="active")
    if active_sessions:
        # Route to steering queue instead of session queue
        push_steering_message(session_id, clean_text, sender_name)
        await client.send_message(event.chat_id, "Adding to current task", reply_to=message.id)
        return

    # Otherwise fall through to normal session queue (session resume)
```

Abort detection: check if `clean_text.strip().lower()` is in `{"stop", "cancel", "abort", "nevermind"}` and set `is_abort=True` in the steering message.

#### 3. Watchdog Hook Changes (`agent/health_check.py`)

The watchdog already fires after every tool call and has the `session_id`. Add steering queue check **before** the periodic health check (which only fires every 20 tool calls):

```python
async def watchdog_hook(input_data, tool_use_id, context):
    session_id = input_data.get("session_id", "unknown")

    # Check steering queue EVERY tool call (lightweight Redis LPOP)
    steering_msg = pop_steering_message(session_id)
    if steering_msg:
        if steering_msg["is_abort"]:
            return {"decision": "block", "continue_": False, "stopReason": "User requested abort"}

        # Inject the steering message — this is the key mechanism
        # Return a special response that tells the SDK to process this as new user input
        return {
            "continue_": True,
            "inject_message": steering_msg["text"],  # See implementation note below
        }

    # ... existing health check logic (every CHECK_INTERVAL calls)
```

**Critical implementation question: How to inject the steering message into the SDK session.**

The SDK's `PostToolUse` hook returns a dict that controls flow. The hook doesn't have direct access to the `ClaudeSDKClient` instance to call `client.query()`. Two approaches:

**Option A — Shared client reference**: Store the `ClaudeSDKClient` instance in a module-level dict keyed by session_id when `ValorAgent.query()` starts. The watchdog hook reads it and calls `client.interrupt()` + `client.query(steering_text)`. This is the most direct path.

**Option B — Hook return value**: If the SDK supports a return value from PostToolUse that injects a user message (e.g. `{"user_message": "..."}` or similar), use that. Need to verify SDK capabilities. If supported, this is cleaner.

**Option C — File-based signaling**: Write steering messages to a known file path. Add a tool or system prompt instruction that tells the agent to check for steering messages periodically. Least reliable — depends on agent compliance.

**Recommended: Option A.** It's explicit, doesn't depend on undocumented SDK features, and gives us full control over interrupt + re-query.

#### 4. SDK Client Registry (`agent/sdk_client.py`)

Store the active `ClaudeSDKClient` instance so the watchdog hook (and other subsystems) can access it:

```python
# Module-level registry of active SDK clients
_active_clients: dict[str, ClaudeSDKClient] = {}

def get_active_client(session_id: str) -> ClaudeSDKClient | None:
    """Get the live SDK client for a running session, if any."""
    return _active_clients.get(session_id)

class ValorAgent:
    async def query(self, message, session_id=None):
        options = self._create_options(session_id)
        async with ClaudeSDKClient(options) as client:
            _active_clients[session_id] = client
            try:
                # ... existing query logic
            finally:
                _active_clients.pop(session_id, None)
```

Watchdog hook steers by calling:
```python
from agent.sdk_client import get_active_client

client = get_active_client(session_id)
if client:
    await client.interrupt()
    await client.query(steering_text)
```

**Crash/reboot safety**: The `_active_clients` dict is in-process memory only. It is NOT persisted. This is intentional and correct:

- **On crash/reboot**: The dict is empty. The SDK subprocess (`claude` CLI) that was running is also dead. There is nothing to steer into — the session is gone.
- **Recovery path**: On startup, `_recover_interrupted_sessions()` resets any `status="running"` AgentSessions back to `status="pending"`, and `_ensure_worker()` restarts them. The recovered session creates a **new** `ClaudeSDKClient` instance, which gets registered in `_active_clients` fresh. The `ClaudeAgentOptions.resume` field (set to the session_id) tells the SDK CLI to resume from its own persistent conversation history on disk (`~/.claude/`).
- **Steering queue on crash**: Any messages in `steering:{session_id}` survive in Redis (no TTL — they persist indefinitely). When the session resumes after crash, the watchdog will find and inject them on the first tool call of the new session. No messages are lost.
- **No stale references**: The `finally` block in `ValorAgent.query()` guarantees cleanup even on exceptions. The only way to get a stale entry is if the process is killed (SIGKILL). On next startup, `_active_clients` is empty (fresh import), so there's zero risk of referencing a dead client.

**What could go wrong with shared client references:**

1. **Async context mismatch**: The SDK docs warn that `ClaudeSDKClient` cannot be used across different async runtime contexts (anyio task groups). The watchdog hook runs inside the SDK's own event loop (it's a PostToolUse callback), so it shares the same async context as the client. This is safe. However, calling `get_active_client()` from a *different* asyncio task (e.g., the bridge's Telethon event handler) would be unsafe. The bridge must only push to the Redis queue, never call the client directly.

2. **Concurrent access**: Only one session runs per project (enforced by `_worker_loop`). The watchdog fires synchronously between tool calls (the agent is paused). There's no concurrent access to the client — the hook has exclusive access during its execution window.

3. **Interrupt during tool execution**: `client.interrupt()` sends a signal to the CLI subprocess. If called while a tool is mid-execution (e.g., a long `git push`), the CLI should handle the interrupt gracefully. Need to verify this doesn't corrupt in-progress operations.

**Additional benefits of the client registry:**

The `_active_clients` registry opens up capabilities beyond steering:

- **Direct health inspection**: Instead of reading transcript files and asking Haiku to judge health (current approach), we could inspect the client's state directly — checking message counts, elapsed time, or the last tool name. This makes the health check simpler and eliminates the Haiku API call for routine checks.
- **Parallel session inspection**: A monitoring endpoint or diagnostic tool could list all running sessions with their client state (connected, message count, duration) without parsing log files.
- **Cost tracking in real-time**: `ResultMessage.total_cost_usd` is available on the client's response stream. The registry makes it possible to query accumulated cost for a running session from outside for observability.
- **Graceful shutdown improvement**: `_graceful_shutdown()` currently resets sessions to pending via Redis. With the registry, it could call `client.interrupt()` on each active client first, giving the agent a chance to save state before the process exits.

#### 5. Steering Queue Functions (new module: `agent/steering.py`)

```python
def push_steering_message(session_id: str, text: str, sender: str, is_abort: bool = False) -> None:
    """Push a message to a session's steering queue."""

def pop_steering_message(session_id: str) -> dict | None:
    """Pop the next steering message (FIFO). Returns None if empty."""

def clear_steering_queue(session_id: str) -> int:
    """Clear all pending steering messages. Returns count cleared."""
```

All use `POPOTO_REDIS_DB` directly with `RPUSH`, `LPOP`, and `DEL` on key `steering:{session_id}`. No TTL — messages persist until consumed or explicitly cleared by session completion.

## No-Gos (Out of Scope)

- **Message classification AI** — Existing reply-to handling is sufficient for routing. No LLM-based classification of "steering vs new task" needed.
- **Progress streaming** — No play-by-play updates to Telegram. Only meaningful communication.
- **Multi-session steering** — Only one session runs per project at a time (enforced by session queue). No need to handle concurrent session steering.
- **Non-reply steering** — Only reply-thread messages count as steering. A new mention always creates a new session.
- **Automatic fan-out** — Parent-child steering is always explicit, per-child. No broadcasting to all children.
