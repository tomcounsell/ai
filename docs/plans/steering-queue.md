---
status: Planning
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-02
tracking: https://github.com/tomcounsell/ai/issues/23
---

# Steering Queue: Mid-Execution Course Correction via Reply Threads

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
- **Bridge Routing Logic**: `handle_new_message` detects reply-to-running-session and routes to the steering queue instead of creating a new job
- **Receipt Acknowledgments**: Brief ack messages telling the supervisor what happened with their message

### Flow

**Steering (reply to running session):**

User replies to Valor's "acknowledged" message → Bridge checks if session is active → Push to `steering:{session_id}` Redis list → Ack: "Adding to current task" → Watchdog picks up on next tool call → SDK `client.interrupt()` + `client.query(steering_message)` → Agent continues with new context

**Follow-up (new message while session running):**

User sends new mention (not a reply) → Bridge sees active session for project → Enqueue as normal job → Ack: "Queued — will start after current task finishes"

**Abort:**

User replies "stop" or "cancel" → Bridge pushes abort signal to steering queue → Watchdog picks up → SDK `client.interrupt()` → Session marked as aborted → Ack: "Stopped"

### Technical Approach

#### 1. Steering Queue Model (new Redis structure)

Use Redis lists via `popoto.redis_db.POPOTO_REDIS_DB` directly (not a Model — these are transient queues, not queryable entities):

```
Key:    steering:{session_id}
Type:   Redis List (RPUSH to add, LPOP to consume)
Values: JSON strings: {"text": "...", "sender": "...", "timestamp": ..., "is_abort": false}
TTL:    1 hour (auto-cleanup for orphaned queues)
```

Why not a popoto Model: steering messages are ephemeral, consumed once, and don't need indexing or querying beyond FIFO consumption. A Redis list is the right primitive.

#### 2. Bridge Changes (`bridge/telegram_bridge.py`)

In `handle_new_message`, after detecting `is_reply_to_valor` and building the `session_id`:

```
if is_reply_to_valor and message.reply_to_msg_id:
    session_id = f"tg_{project_key}_{event.chat_id}_{message.reply_to_msg_id}"

    # NEW: Check if this session is currently running
    active_sessions = AgentSession.query.filter(session_id=session_id, status="active")
    if active_sessions:
        # Route to steering queue instead of job queue
        push_steering_message(session_id, clean_text, sender_name)
        await client.send_message(event.chat_id, "Adding to current task", reply_to=message.id)
        return

    # Otherwise fall through to normal job queue (session resume)
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

#### 4. SDK Client Changes (`agent/sdk_client.py`)

Store the active client reference so the watchdog can access it:

```python
# Module-level registry of active SDK clients
_active_clients: dict[str, ClaudeSDKClient] = {}

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
client = _active_clients.get(session_id)
if client:
    await client.interrupt()
    await client.query(steering_text)
```

#### 5. Steering Queue Functions (new module: `agent/steering.py`)

```python
def push_steering_message(session_id: str, text: str, sender: str, is_abort: bool = False) -> None:
    """Push a message to a session's steering queue."""

def pop_steering_message(session_id: str) -> dict | None:
    """Pop the next steering message (FIFO). Returns None if empty."""

def clear_steering_queue(session_id: str) -> int:
    """Clear all pending steering messages. Returns count cleared."""
```

All use `POPOTO_REDIS_DB` directly with `RPUSH`, `LPOP`, and `DEL` on key `steering:{session_id}`. Set TTL of 1 hour on first push.

## Rabbit Holes & Risks

### Risk 1: SDK interrupt + re-query behavior
**Impact:** If `client.interrupt()` followed by `client.query()` doesn't cleanly resume the session, the agent could lose context or crash.
**Mitigation:** Test this flow in isolation first. If interrupt + query doesn't work cleanly, fall back to Option C (file-based signaling via system prompt instruction). The agent already reads files as part of its normal operation.

### Risk 2: Race condition between watchdog and agent response
**Impact:** The watchdog fires after a tool call. If the agent finishes between the steering push and the next tool call, the steering message is never consumed.
**Mitigation:** When a session completes, check its steering queue. If messages remain, either auto-queue them as a new follow-up job or log them. Add `clear_steering_queue(session_id)` to `_execute_job` completion path.

### Risk 3: Watchdog hook doesn't have async access to Redis
**Impact:** The hook is async but runs in the SDK's event loop. Redis calls via popoto are synchronous.
**Mitigation:** Use `POPOTO_REDIS_DB.lpop()` directly — it's a sync Redis call but completes in <1ms (local Redis). Wrapping in `asyncio.to_thread()` is an option if needed but likely unnecessary for a single LPOP.

### Risk 4: Multiple steering messages accumulate
**Impact:** If the user sends several corrections in quick succession, they all queue up. The agent processes them one at a time (one per tool call), which could be confusing.
**Mitigation:** Pop ALL messages from the queue in one check and concatenate them into a single injection. Use `LPOP` in a loop until empty, then combine.

## No-Gos (Out of Scope)

- **Message classification AI** — Existing reply-to handling is sufficient for routing. No LLM-based classification of "steering vs new task" needed.
- **Progress streaming** — No play-by-play updates to Telegram. Only meaningful communication.
- **Multi-session steering** — Only one session runs per project at a time (enforced by job queue). No need to handle concurrent session steering.
- **Non-reply steering** — Only reply-thread messages count as steering. A new mention always creates a new job.
- **Legacy mode support** — Steering only applies to SDK mode (`USE_CLAUDE_SDK=true`). Legacy clawdbot mode is not modified.

## Success Criteria

- [ ] Reply to a running session's message injects the reply into the active agent session
- [ ] Agent acknowledges and acts on the steering message within its current execution
- [ ] "stop" / "cancel" reply aborts the running session within one tool call
- [ ] Non-reply messages during a running session queue normally with a position ack
- [ ] Reply to a completed session resumes it (existing behavior preserved)
- [ ] Steering queue cleans up after session completes (no orphaned Redis keys)
- [ ] No production Redis pollution (steering keys use TTL)
- [ ] Tests cover: push/pop, abort signal, bridge routing, watchdog injection, cleanup

## Files to Modify

| File | Change |
|------|--------|
| `agent/steering.py` | **NEW** — Steering queue functions (push, pop, clear) |
| `agent/health_check.py` | Add steering queue check to `watchdog_hook` |
| `agent/sdk_client.py` | Store active client reference for interrupt access |
| `bridge/telegram_bridge.py` | Route reply-to-active-session to steering queue |
| `agent/job_queue.py` | Clear steering queue on job completion; handle leftover messages |
| `tests/test_steering.py` | **NEW** — Tests for steering queue, routing, and cleanup |
