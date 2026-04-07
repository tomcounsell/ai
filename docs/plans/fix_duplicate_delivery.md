# Fix Duplicate Delivery: Missed-Message Scan Re-enqueues In-Flight Messages

**Issue**: #193
**Status**: ACTIVE

## Problem

A single "Merge" message produced 3 responses because:

1. **Live handler** enqueued the job and started processing
2. **Catchup scanner** (`scan_for_missed_messages`) ran during the same window, saw no Telegram reply yet, and enqueued the same message as a second job
3. **Auto-continue** fired on one of the duplicate completions, producing a third response

## Root Cause Analysis

Two independent dedup systems don't share state:

| Checkpoint | Location | Storage | Problem |
|-----------|----------|---------|---------|
| Live handler | `telegram_bridge.py:554` | Redis set `bridge:dedup:{chat_id}` | Works correctly |
| Catchup scanner | `catchup.py:126` | Telegram reply chain | Doesn't check Redis dedup |
| Job queue | `job_queue.py:enqueue_job()` | None | No duplicate prevention at all |

## Solution

### Fix 1: Catchup scanner checks Redis dedup (PRIMARY FIX)

In `bridge/catchup.py`, before the `_check_if_handled()` Telegram reply check, add a Redis dedup check:

```python
# After line 112 (skip messages without text), before line 114 (get sender info):
from bridge.dedup import is_duplicate_message
if await is_duplicate_message(dialog.entity.id, message.id):
    logger.info(f"[catchup] {chat_title}: msg {message.id} already processed (Redis dedup) - skip")
    continue
```

This is the minimal fix that prevents the exact scenario from #193. The catchup scanner already has access to `dialog.entity.id` (chat_id) and `message.id`.

### Fix 2: Record dedup in catchup enqueue

After the catchup scanner enqueues a job (line 185), record the message as processed:

```python
from bridge.dedup import record_message_processed
await record_message_processed(dialog.entity.id, message.id)
```

This prevents double-enqueue if the catchup scanner runs multiple times before the agent replies.

### Fix 3: Auto-continue respects session completion state

In `send_to_chat()` inside `_execute_job()`, before the auto-continue logic, check if this session was already completed by a prior execution:

```python
# After the _completion_sent check (line 1052), add:
if agent_session and agent_session.status == "completed":
    logger.info(f"[{job.project_key}] Session already completed — delivering without auto-continue")
    await send_cb(job.chat_id, msg, job.message_id, agent_session)
    _completion_sent = True
    return
```

This prevents the chain reaction where a duplicate job's completion gets auto-continued.

## No-Gos

- Do NOT add a job-queue-level dedup (checking for existing jobs with same message_id). The session_id already encodes the message_id, and continuation jobs intentionally reuse the same session_id. Adding queue-level dedup would break auto-continue.
- Do NOT remove the `_check_if_handled()` Telegram reply check. It's still valuable as a fallback when Redis data has expired (TTL: 2 hours).

## Success Criteria

- [ ] Single message produces exactly one response (no duplicates)
- [ ] Catchup scanner skips messages already in Redis dedup set
- [ ] Auto-continue does not fire on sessions already marked completed
- [ ] All existing tests pass
- [ ] New regression tests for: catchup dedup check, completed-session guard
- [ ] Bridge restart during active job does not produce duplicate responses

## Implementation Order

1. Fix 1 + Fix 2 (catchup.py) — primary fix
2. Fix 3 (job_queue.py) — defense in depth
3. Tests
4. Manual verification: restart bridge during active job, confirm no duplicate

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` with dedup architecture
- [ ] Add entry to `docs/features/README.md` index

## Update System

No update system changes required — this is a bridge-internal fix.

## Agent Integration

No agent integration required — this is bridge infrastructure. The agent doesn't need to know about dedup mechanics.
