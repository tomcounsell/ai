# Agent Session Queue: Reliability Fixes

**Status**: Complete

## KeyField Index Corruption Fix

Popoto's `KeyField.on_save()` only adds the object key to the new status index set -- it never removes from the old one. This means in-place status mutations like `job.status = "running"; await job.async_save()` leave stale entries in the previous index set, causing ghost sessions and double-processing.

### Delete-and-Recreate Pattern

All status transitions now use delete-and-recreate instead of in-place mutation:

```python
fields = _extract_agent_session_fields(job)
await job.async_delete()        # removes from old index via on_delete
fields["status"] = "running"
new_job = await AgentSession.async_create(**fields)  # adds to new index via on_save
```

This is applied in three functions (plus dependency-related operations):
- `_pop_agent_session()` -- pending to running (also filters out dependency-blocked jobs)
- `_recover_interrupted_sessions()` -- running to pending (sync, startup)
- `_reset_running_jobs()` -- running to pending (async, shutdown)
- `retry_agent_session()` -- failed/cancelled to pending (PM queue management)

The `_extract_agent_session_fields()` helper reads all non-auto fields (56+) from an AgentSession instance for recreation. The `status` field is included for defense-in-depth: any delete-and-recreate path (e.g., health check orphan-fixing) preserves the original status instead of defaulting to `"pending"`. Callers that intentionally override status (retry, nudge fallback) set `fields["status"]` explicitly after extraction.

## Worker Drain Guard (Event-Based)

The worker loop uses an Event-based drain strategy to reliably pick up pending sessions after completing each job. This replaces the original 100ms sleep-and-retry approach which was insufficient to handle the thread-pool race between Popoto's `async_create` index writes and `async_filter` reads.

### How It Works

1. **asyncio.Event notification**: `enqueue_agent_session()` signals a per-chat `asyncio.Event` after pushing a session. The event is level-triggered (stays set until cleared), so signals during job execution are not lost.

2. **Event-based wait**: After completing a session, `_worker_loop()` clears the event and waits for it with a `DRAIN_TIMEOUT` (configurable constant, default 1.5s). If the event fires, the worker pops the next session via `_pop_agent_session()`.

3. **Sync Popoto fallback**: If the timeout expires without an event, `_pop_agent_session_with_fallback()` runs a synchronous `AgentSession.query.filter()` call that bypasses `to_thread()` scheduling. This eliminates the thread-pool race that caused the original index visibility bug.

4. **Exit-time safety check**: Before exiting with "Queue empty", the worker runs one final `_pop_agent_session_with_fallback()` and logs a WARNING if orphaned pending jobs are found.

### Key Functions

| Function | Purpose |
|----------|---------|
| `_pop_agent_session_with_fallback(worker_key, is_project_keyed)` | Tries `_pop_agent_session()` first, then sync Popoto query as fallback |
| `DRAIN_TIMEOUT` | Module constant (1.5s) controlling the Event wait timeout |
| `_active_events` | Dict mapping worker_key to asyncio.Event for worker notification |

### Why the Original Drain Guard Failed

The original 100ms sleep relied on `_pop_agent_session()` (which uses `async_filter` via `to_thread()`) finding the session on retry. But the root cause is a thread-pool scheduling race: `async_create` writes the hash and index entries via multiple Redis commands in a thread, and `async_filter` reads the index intersection in a separate thread. The 100ms window was too short, and both calls suffered from the same `to_thread()` race. The sync fallback bypasses this entirely.

## Startup Session Cleanup and Recovery

At startup, two cleanup passes run before session processing begins. These are called exclusively from `worker/__main__.py` — the bridge does not call them.

1. **Corrupted session cleanup** (`cleanup_corrupted_agent_sessions()`): Detects sessions with invalid IDs (e.g., length 60 instead of expected 32 for uuid4) or sessions whose `.save()` raises `ModelException`. These are deleted directly (with fallback to raw Redis key deletion), then `AgentSession.rebuild_indexes()` clears orphaned index entries. Also runs hourly as the `agent-session-cleanup` reflection and during `/update`.

2. **Interrupted session recovery** (`_recover_interrupted_agent_sessions_startup()`): Resets stale running sessions to pending with high priority. Sessions started within the last `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds (300s) are skipped — they may have been picked up by a worker in the current process before startup recovery fired. Sessions with `started_at=None` (missing or corrupt) are always recovered. Uses the same timing guard as the periodic health check to prevent orphaning SDK subprocesses (issue #727).

3. **Orphaned process cleanup** (`_cleanup_orphaned_claude_processes()`): Kills Claude Agent SDK subprocesses from prior worker runs whose PPID is 1 (orphaned by parent death). Called from `worker/__main__.py` at startup after session recovery. Defined in `agent/agent_session_queue.py` so it is available to the worker without importing from the bridge.

### Caller: Worker Only

The following execution functions are called exclusively from `worker/__main__.py`:
- `_ensure_worker(worker_key, is_project_keyed)` — spawns per-worker-key worker loops
- `_recover_interrupted_agent_sessions_startup()` — startup session recovery
- `_agent_session_health_loop()` — background health monitor (safety net: every 5 min)
- `_session_notify_listener()` — pub/sub subscriber for immediate session pickup (~1s latency); creates a dedicated `redis.Redis` connection with `socket_timeout=None` to avoid the global pool's 5s timeout (issue #824)
- `_cleanup_orphaned_claude_processes()` — kill orphaned SDK subprocesses
- `AgentSession.rebuild_indexes()` — repair Redis index entries

The bridge only calls `enqueue_agent_session()` and `register_callbacks()`. See [Bridge/Worker Architecture](bridge-worker-architecture.md).

## Revival Chat Scoping Fix

`check_revival()` was rewritten to scope revival detection strictly to the originating Telegram chat. The previous implementation listed all `session/*` git branches across the entire repository (via `git branch --list "session/*"`), which caused revival notifications to bleed across unrelated Telegram chats whenever any project had an open session branch.

### New Approach

Instead of scanning git branches globally, `check_revival()` queries Redis (Popoto) for jobs filtered by `project_key` + `status` (pending or running), then filters by `chat_id` in Python:

```python
for status in ("pending", "running"):
    jobs = AgentSession.query.filter(project_key=project_key, status=status)
    for session in sessions:
        if str(session.chat_id) == chat_id_str:
            branch = _session_branch_name(session.session_id)
            branches.append(branch)
```

Branch existence is then verified individually in git (`git branch --list <specific-branch>`), rather than enumerating all branches. Redis is the sole source of truth for session state.

### Behavioral Change

| Before | After |
|--------|-------|
| All `session/*` branches visible to any chat | Only branches belonging to the calling `chat_id` |
| Revival could notify wrong chat | Revival only notifies the chat that owns the session |
| File-based state checked as fallback | Redis is the sole source of truth |

## Deferred Execution (`scheduled_at`)

The `AgentSession` model has a `scheduled_at` field (UTC datetime). `_pop_agent_session()` skips jobs where `scheduled_at > now()`, enabling deferred execution. Jobs with `scheduled_at` in the past or `None` are eligible immediately.

## Priority Model

Four-tier priority system: `urgent > high > normal > low`. Default priority is `normal` for all new jobs. Within the same tier, FIFO ordering (oldest first). Recovery jobs use `high`; catchup/revival use `low`.

Priority ranking constant: `PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}`

## Self-Scheduling

The agent can enqueue jobs mid-conversation via `tools/agent_session_scheduler.py`. See [Agent Session Scheduling](agent-session-scheduling.md) for details.

## Parent-Child Job Hierarchy

Jobs support parent-child decomposition via the `parent_agent_session_id` field on `AgentSession`.

### Hierarchy Fields

| Field | Type | Description |
|-------|------|-------------|
| `parent_agent_session_id` | `KeyField(null=True)` | Links child to parent session. Indexed for efficient queries. |
| `stable_agent_session_id` | `KeyField(null=True)` | UUID set once at creation, never changes on delete-and-recreate. Used as dependency reference key. |
| ~~`depends_on`~~ | ~~`ListField(null=True)`~~ | Removed. Dependency tracking was removed from the model. |
| `commit_sha` | `Field(null=True)` | HEAD commit SHA for checkpoint/restore across session pause/resume. |

### Status Values

| Status | Description |
|--------|-------------|
| `waiting_for_children` | Parent has spawned children and is waiting for them to complete |
| `cancelled` | Explicitly cancelled by PM; blocks dependents (same as `failed`) |

### Completion Propagation

When a child session completes, `_complete_agent_session()` calls `_finalize_parent()` **before** deleting the child from Redis. The completing child's intended terminal status is passed as a parameter (since its Redis status is still "running" at that point). `_finalize_parent()` queries all siblings and uses the override status for the completing child. When all siblings are terminal (`completed` or `failed`), the parent transitions to `completed` (all succeeded) or `failed` (any failed). Only after finalization does `_complete_agent_session()` delete the child.

Parent finalization mutates the parent's status in place via `transition_status()` (since `status` is an IndexedField, not a KeyField). `_pop_agent_session()` likewise uses in-place mutation -- delete-and-recreate is only required for callers that change a KeyField (e.g., `parent_agent_session_id` reparenting in retry/orphan-fix paths).

### Health Monitor Extensions

The periodic health check (`_job_hierarchy_health_check()`) detects and self-heals:

- **Orphaned children**: `parent_agent_session_id` points to non-existent session -- cleared (status preserved via `_extract_agent_session_fields()` to prevent zombie loops)
- **Stuck parents**: `waiting_for_children` with all children terminal -- auto-finalized

See [Agent Session Scheduling](agent-session-scheduling.md) for usage details and CLI commands.

## See Also

- `docs/features/scale-agent-session-queue-with-popoto-and-worktrees.md` -- Original agent session queue architecture
- `docs/features/agent-session-scheduling.md` -- Agent-initiated scheduling tool
- `docs/features/agent-session-model.md` -- AgentSession model fields and lifecycle
- `docs/features/session-lifecycle.md` -- Session state machine, zombie loop prevention
- `agent/agent_session_queue.py` -- Implementation
