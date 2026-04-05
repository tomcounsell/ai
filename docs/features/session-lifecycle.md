# Session Lifecycle

How sessions transition between states and the safeguards that prevent zombie loops.

## Session States

| State | Description |
|-------|-------------|
| `pending` | Queued, waiting to be picked up by `_pop_agent_session()` |
| `running` | Currently being executed by a worker |
| `completed` | Finished successfully, retained in Redis for reply-to revival |
| `failed` | Execution failed, retained for retry |
| `waiting_for_children` | Parent session waiting for child sessions to finish |
| `cancelled` | Manually cancelled via PM controls |

## Completion Flow

When a session finishes execution:

1. `_complete_agent_session()` sets `session.status = "completed"` and calls `session.save()`
2. The session remains in Redis (not deleted) to support reply-to revival
3. `_pop_agent_session()` only picks up `status="pending"` sessions, so completed sessions are never re-executed

## Field Extraction (`_extract_agent_session_fields`)

The `_AGENT_SESSION_FIELDS` list defines which fields are preserved during delete-and-recreate operations. This is needed because some fields (KeyFields like `parent_agent_session_id`) cannot be mutated directly in Popoto without corrupting the Redis index.

**Status preservation**: The `status` field is included in `_AGENT_SESSION_FIELDS` for defense-in-depth. Any delete-and-recreate path (e.g., health check orphan-fixing) preserves the original status instead of defaulting to `"pending"`.

Callers that intentionally override status after extraction:
- **Retry** (`retry_agent_session`): Sets `status = "pending"` to re-queue the session
- **Nudge fallback** (`_enqueue_nudge`): Sets `status = "pending"` to continue the session

## Zombie Loop Prevention

### Health Check Orphan-Fixing

The `_agent_session_hierarchy_health_check()` function detects orphaned children (sessions whose parent no longer exists). It clears the parent reference via delete-and-recreate.

Because `status` is in the field extraction list, a completed orphaned session stays `completed` after recreation. Without this, the recreated session would default to `pending` and be re-executed indefinitely (the zombie loop).

### Nudge Overwrite Guard

When a nudge (auto-continue) is enqueued during session execution, `_enqueue_nudge()` sets the Redis session status to `pending`. The worker finally block must not overwrite this back to `completed`.

The guard re-reads the session from Redis before completing:
- If `status = "pending"`: a nudge was enqueued, skip completion
- If session no longer exists: nudge fallback recreated it, skip completion
- If `status = "running"` or other: no nudge, proceed with normal completion

This prevents the finally block from clobbering the nudge's `pending` status.

Implementation note: nudge enqueue paths set `chat_state.defer_reaction` elsewhere in the worker, but the completion guard uses **Redis as the source of truth** (pending or missing session) so it also covers delete-and-recreate nudge fallbacks without relying on in-memory state alone.

## Related

- [Agent Session Queue Reliability](agent-session-queue.md) -- KeyField index fixes and delete-and-recreate pattern
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- Stuck session detection
- [Agent Session Hierarchy](agent-session-scheduling.md#parent-child-session-hierarchy) -- Parent-child relationships and orphan recovery
