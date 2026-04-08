# Session Lifecycle

How sessions transition between states via the consolidated lifecycle module (`models/session_lifecycle.py`).

## Session States (11 total)

### Non-terminal (use `transition_status()`)

| State | Description |
|-------|-------------|
| `pending` | Queued, waiting to be picked up by `_pop_agent_session()` |
| `running` | Worker picked up, agent executing |
| `active` | Session in progress (transcript tracking) |
| `dormant` | Paused on open question, waiting for human reply |
| `waiting_for_children` | Parent session waiting for child sessions to complete |
| `superseded` | A newer session for the same session_id has taken over |

### Terminal (use `finalize_session()`)

| State | Description |
|-------|-------------|
| `completed` | Work finished successfully |
| `failed` | Work failed (error, crash, or watchdog detection) |
| `killed` | Terminated by user or scheduler |
| `abandoned` | Unfinished, auto-detected by watchdog or health check |
| `cancelled` | Cancelled before execution (pending -> cancelled) |

## Lifecycle Module

All session status mutations go through `models/session_lifecycle.py`. Direct `.status =` mutations outside this module are prohibited.

### `finalize_session(session, status, reason, *, skip_auto_tag=False, skip_checkpoint=False, skip_parent=False)`

For terminal transitions. Executes all completion side effects in order:

1. **Lifecycle log** -- `session.log_lifecycle_transition(status, reason)` (always)
2. **Auto-tag** -- `auto_tag_session(session_id)` (unless `skip_auto_tag=True`)
3. **Branch checkpoint** -- `checkpoint_branch_state(session)` (unless `skip_checkpoint=True`)
4. **Parent finalization** -- `_finalize_parent_sync(parent_id, ...)` (unless `skip_parent=True` or no parent)
5. **Status + timestamp + save** -- sets `session.status`, `session.completed_at`, calls `session.save()`

**Idempotent**: if the session is already in the target terminal state, logs and returns without re-executing side effects.

**Lazy-load safety**: Before saving, `finalize_session()` backfills `session._saved_field_values["status"]` with the current status. Popoto's `_create_lazy_model()` only seeds `_saved_field_values` with KeyFields, so lazy-loaded sessions have no `"status"` entry. Without this backfill, `IndexedFieldMixin.on_save()` skips `srem()` and the session accumulates in both the old and new status index sets simultaneously (ghost sessions).

**Skip flags**: The hooks subprocess paths (`stop.py`, `subagent_stop.py`) use `skip_auto_tag=True, skip_checkpoint=True` to avoid importing heavy dependencies that may not be available in the subprocess context.

### `transition_status(session, new_status, reason, *, reject_from_terminal=True)`

For non-terminal transitions. Logs the lifecycle transition and updates the status.

1. **Terminal guard** -- if `reject_from_terminal=True` (default) and current status is terminal, raises `ValueError`
2. **Lifecycle log** -- `session.log_lifecycle_transition(new_status, reason)` (always)
3. **Status + save** -- sets `session.status`, calls `session.save()`

**Idempotent**: if the session is already in the target state, logs and returns.

**Lazy-load safety**: Before saving, `transition_status()` backfills `session._saved_field_values["status"]` with the current status. This mirrors the same backfill in `finalize_session()` — both functions share the same Popoto lazy-load coupling. See `finalize_session()` above for the full explanation.

**Terminal respawn protection**: By default, `transition_status()` rejects transitions from terminal statuses (`completed`, `failed`, `killed`, `abandoned`, `cancelled`). This prevents accidental respawning of finished sessions. Callers that legitimately need terminal-to-non-terminal transitions must pass `reject_from_terminal=False` explicitly. Currently two callers use this opt-out:
- `_mark_superseded()`: `completed->superseded` (intentional bookkeeping)
- `user_prompt_submit.py` hook: `completed->running` (user reactivates local session)

See [Session Recovery Mechanisms](session-recovery-mechanisms.md) for the full audit of all recovery paths.

## Completion Flow

When a session finishes execution, all paths converge on `finalize_session()`:

| Path | Caller | Skip Flags |
|------|--------|------------|
| Worker completion | `_complete_agent_session()` in `agent_session_queue.py` | None (all side effects run) |
| Transcript completion | `complete_transcript()` in `session_transcript.py` | None (all side effects run) |
| Claude Code hook stop | `.claude/hooks/stop.py` | `skip_auto_tag=True, skip_checkpoint=True` |
| Subagent stop | `agent/hooks/subagent_stop.py` | `skip_parent=True` |
| Bridge acknowledgment | `telegram_bridge.py` dormant->completed | None |
| PM cancel | `agent_session_queue.py` | None |
| Watchdog abandon/fail | `session_watchdog.py` | None |
| Deploy stale cleanup | `_cleanup_stale_sessions()` in `scripts/update/run.py` | `skip_checkpoint=True` |

### Worker Completion — Redis Re-read

`_complete_agent_session()` re-reads the session record from Redis before calling `finalize_session()`. This ensures that any `stage_states` accumulated during execution (e.g., SDLC pipeline transitions written while the worker was running) are captured rather than overwritten by the stale in-memory snapshot.

The re-query is intentionally **status-filter-free** — it queries by `session_id` only, with no `status="running"` constraint. Filtering by status would return an empty list if the session had already transitioned away from `running` (via a concurrent path) before `_complete_agent_session()` fired, causing `finalize_session()` to operate on the stale in-memory object and corrupt the status index (the session would end up indexed under both the old and new status simultaneously). See issue #825.

**Tie-breaking** when multiple records share the same `session_id`: prefer any record currently in `running` status (ensures the live session is finalized), then fall back to most-recent by `created_at` only if no running records exist. If no records are found at all, `finalize_session()` is called on the original in-memory object.

## Side Effect Consolidation

Before consolidation, completion side effects were scattered across 4 paths, each performing different subsets:

| Side Effect | Before (which paths) | After |
|-------------|---------------------|-------|
| Lifecycle log | Path A only | All paths via `finalize_session()` |
| Auto-tag | Path A only | All paths (unless `skip_auto_tag`) |
| Branch checkpoint | Path B only | All paths (unless `skip_checkpoint`) |
| Parent finalization | Path B only | All paths (unless `skip_parent`) |

## Parent Finalization

When a child session completes, `finalize_session()` checks if the parent should also be finalized:

1. Look up parent by `parent_agent_session_id`
2. If parent is already terminal, skip
3. Set parent to `waiting_for_children` if not already
4. Check all children's statuses
5. If all children terminal: finalize parent as `completed` (all succeeded) or `failed` (any failed)
6. Uses `skip_parent=True` internally to prevent infinite recursion

## Field Extraction (`_extract_agent_session_fields`)

The `_AGENT_SESSION_FIELDS` list defines which fields are preserved during delete-and-recreate operations. The `status` field is included for defense-in-depth: any delete-and-recreate path preserves the original status instead of defaulting to `"pending"`.

## Zombie Loop Prevention

### Health Check Orphan-Fixing

The `_agent_session_hierarchy_health_check()` function detects orphaned children. Because `status` is in the field extraction list, a completed orphaned session stays `completed` after recreation. Without this, the recreated session would default to `pending` and be re-executed indefinitely.

### Nudge Overwrite Guard

When a nudge (auto-continue) is enqueued during session execution, the session status is set to `pending` via `transition_status()`. The worker finally block re-reads the session from Redis before completing:
- If `status = "pending"`: a nudge was enqueued, skip completion
- If session no longer exists: nudge fallback recreated it, skip completion
- Otherwise: proceed with normal completion via `finalize_session()`

## Stale Session Cleanup

`_cleanup_stale_sessions()` in `scripts/update/run.py` runs during every `/update` deploy and terminates `running` or `pending` sessions that have no live process. It is a safety net for sessions that were never finalized due to a crash or abrupt restart.

**Primary liveness check — `updated_at` recency (30-minute window):** The function first checks each session's `updated_at` timestamp. If `updated_at` is within the last 30 minutes, the session is considered live and unconditionally skipped. The worker writes a periodic `updated_at` heartbeat every 25 minutes via `_heartbeat_loop` in `agent/agent_session_queue.py`, so even sessions blocked on a long Claude API call stay fresh in Redis. Sessions skipped for recent activity are counted and reported in the `/update` log as "Skipped N live session(s) (recent heartbeat)".

**Fallback liveness check — `created_at` age (120-minute threshold):** When `updated_at` is `None` (sessions created before the heartbeat feature was added), the function falls back to checking `created_at` age. Sessions younger than 120 minutes are skipped. This preserves the original safety margin for legacy sessions.

**Secondary defense — `_active_workers` registry:** Before either timestamp check, any session whose `worker_key` maps to a not-done asyncio Task in `_active_workers` is unconditionally skipped. Workers are keyed by `worker_key` (either `project_key` for PM/unslugged-dev sessions, or `chat_id` for teammate/slugged-dev sessions). This registry is only populated during in-process invocations and is always empty when the update script runs as a CLI subprocess.

**Return value:** The function returns `(killed_count, skipped_live)` — both the number of sessions killed and the number skipped due to recent heartbeat activity.

**Lifecycle routing:** All terminal transitions go through `finalize_session(session, "killed", reason="stale cleanup (no live process)", skip_checkpoint=True)`. This fires all lifecycle hooks (lifecycle log, auto-tag, parent finalization) while skipping the branch checkpoint, which is unavailable outside the normal worker context.

**In-process vs. standalone:** When the update script runs inside the same process as the queue (bridge in-process update), `_active_workers` is populated and fully authoritative. When it runs as a CLI subprocess, `_active_workers` will always be empty and the function logs a warning before relying on the `updated_at` recency check.

## Design Constraints

- **Import safety**: The module uses lazy imports for `tools.session_tags` and `agent.agent_session_queue` so it can be imported from `.claude/hooks/stop.py` subprocess context where those modules may not be on `sys.path`.
- **Fail-safe side effects**: Each side effect (auto-tag, checkpoint, parent finalization) is wrapped in a try/except that logs and continues. A failure in any side effect never blocks the status save.
- **Synchronous only**: The module provides sync functions. Callers in async contexts use `asyncio.to_thread()` as needed (matching existing patterns).

## Related

- [Agent Session Queue Reliability](agent-session-queue.md) -- KeyField index fixes and delete-and-recreate pattern
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- Stuck session detection
- [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md) -- Structured LIFECYCLE logging at every state transition
- [Agent Session Hierarchy](agent-session-scheduling.md#parent-child-session-hierarchy) -- Parent-child relationships and orphan handling
