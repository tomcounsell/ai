# Job Queue: Reliability Fixes

**Status**: Complete

## KeyField Index Corruption Fix

Popoto's `KeyField.on_save()` only adds the object key to the new status index set -- it never removes from the old one. This means in-place status mutations like `job.status = "running"; await job.async_save()` leave stale entries in the previous index set, causing ghost jobs and double-processing.

### Delete-and-Recreate Pattern

All status transitions now use delete-and-recreate instead of in-place mutation:

```python
fields = _extract_job_fields(job)
await job.async_delete()        # removes from old index via on_delete
fields["status"] = "running"
new_job = await AgentSession.async_create(**fields)  # adds to new index via on_save
```

This is applied in three functions (plus dependency-related operations):
- `_pop_job()` -- pending to running (also filters out dependency-blocked jobs)
- `_recover_interrupted_jobs()` -- running to pending (sync, startup)
- `_reset_running_jobs()` -- running to pending (async, shutdown)
- `retry_job()` -- failed/cancelled to pending (PM queue management)

The `_extract_job_fields()` helper reads all non-auto fields (56+) from an AgentSession instance for recreation.

## Worker Drain Guard (Event-Based)

The worker loop uses an Event-based drain strategy to reliably pick up pending jobs after completing each job. This replaces the original 100ms sleep-and-retry approach which was insufficient to handle the thread-pool race between Popoto's `async_create` index writes and `async_filter` reads.

### How It Works

1. **asyncio.Event notification**: `enqueue_job()` signals a per-chat `asyncio.Event` after pushing a job. The event is level-triggered (stays set until cleared), so signals during job execution are not lost.

2. **Event-based wait**: After completing a job, `_worker_loop()` clears the event and waits for it with a `DRAIN_TIMEOUT` (configurable constant, default 1.5s). If the event fires, the worker pops the next job via `_pop_job()`.

3. **Sync Popoto fallback**: If the timeout expires without an event, `_pop_job_with_fallback()` runs a synchronous `AgentSession.query.filter()` call that bypasses `to_thread()` scheduling. This eliminates the thread-pool race that caused the original index visibility bug.

4. **Exit-time safety check**: Before exiting with "Queue empty", the worker runs one final `_pop_job_with_fallback()` and logs a WARNING if orphaned pending jobs are found.

### Key Functions

| Function | Purpose |
|----------|---------|
| `_pop_job_with_fallback(chat_id)` | Tries `_pop_job()` first, then sync Popoto query as fallback |
| `DRAIN_TIMEOUT` | Module constant (1.5s) controlling the Event wait timeout |
| `_active_events` | Dict mapping chat_id to asyncio.Event for worker notification |

### Why the Original Drain Guard Failed

The original 100ms sleep relied on `_pop_job()` (which uses `async_filter` via `to_thread()`) finding the job on retry. But the root cause is a thread-pool scheduling race: `async_create` writes the hash and index entries via multiple Redis commands in a thread, and `async_filter` reads the index intersection in a separate thread. The 100ms window was too short, and both calls suffered from the same `to_thread()` race. The sync fallback bypasses this entirely.

## Startup Orphan Recovery

`_recover_orphaned_jobs()` scans for AgentSession objects in the Redis class set that are not present in any status KeyField index. These orphans result from past index corruption or creation races. They are re-created with status `pending` and priority `high`. Called at bridge startup alongside `_recover_interrupted_jobs()`.

## Revival Chat Scoping Fix

`check_revival()` was rewritten to scope revival detection strictly to the originating Telegram chat. The previous implementation listed all `session/*` git branches across the entire repository (via `git branch --list "session/*"`), which caused revival notifications to bleed across unrelated Telegram chats whenever any project had an open session branch.

### New Approach

Instead of scanning git branches globally, `check_revival()` queries Redis (Popoto) for jobs filtered by `project_key` + `status` (pending or running), then filters by `chat_id` in Python:

```python
for status in ("pending", "running"):
    jobs = AgentSession.query.filter(project_key=project_key, status=status)
    for job in jobs:
        if str(job.chat_id) == chat_id_str:
            branch = _session_branch_name(job.session_id)
            branches.append(branch)
```

Branch existence is then verified individually in git (`git branch --list <specific-branch>`), rather than enumerating all branches. The `state.work_status` legacy fallback was also removed.

### Behavioral Change

| Before | After |
|--------|-------|
| All `session/*` branches visible to any chat | Only branches belonging to the calling `chat_id` |
| Revival could notify wrong chat | Revival only notifies the chat that owns the session |
| `state.work_status` checked as fallback | Redis is the sole source of truth |

## Deferred Execution (`scheduled_after`)

The `AgentSession` model has a `scheduled_after` field (UTC float timestamp). `_pop_job()` skips jobs where `scheduled_after > now()`, enabling deferred execution. Jobs with `scheduled_after` in the past or `None` are eligible immediately.

## Priority Model

Four-tier priority system: `urgent > high > normal > low`. Default priority is `normal` for all new jobs. Within the same tier, FIFO ordering (oldest first). Recovery jobs use `high`; catchup/revival use `low`.

Priority ranking constant: `PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}`

## Self-Scheduling

The agent can enqueue jobs mid-conversation via `tools/job_scheduler.py`. See [Job Scheduling](job-scheduling.md) for details.

## Sibling Job Dependencies

Jobs can declare dependencies on other jobs via `depends_on` (a list of `stable_job_id` values). See [Job Dependency Tracking](job-dependency-tracking.md) for the full design including branch-session mapping, checkpoint/restore, and PM queue management.

Key behaviors:
- `_pop_job()` skips jobs whose dependencies have not all reached `completed` status
- `cancelled` and `failed` jobs block their dependents
- A periodic `_dependency_health_check()` detects stuck chains and logs warnings
- PM can reorder, cancel, and retry jobs via `reorder_job()`, `cancel_job()`, `retry_job()`

## Parent-Child Job Hierarchy

Jobs support parent-child decomposition via the `parent_job_id` field on `AgentSession`.

### Hierarchy Fields

| Field | Type | Description |
|-------|------|-------------|
| `parent_job_id` | `KeyField(null=True)` | Links child to parent job. Indexed for efficient queries. |
| `stable_job_id` | `KeyField(null=True)` | UUID set once at creation, never changes on delete-and-recreate. Used as dependency reference key. |
| `depends_on` | `ListField(null=True)` | List of `stable_job_id` values this job must wait for. |
| `commit_sha` | `Field(null=True)` | HEAD commit SHA for checkpoint/restore across session pause/resume. |

### Status Values

| Status | Description |
|--------|-------------|
| `waiting_for_children` | Parent has spawned children and is waiting for them to complete |
| `cancelled` | Explicitly cancelled by PM; blocks dependents (same as `failed`) |

### Completion Propagation

When a child job completes, `_complete_job()` calls `_finalize_parent()` **before** deleting the child from Redis. The completing child's intended terminal status is passed as a parameter (since its Redis status is still "running" at that point). `_finalize_parent()` queries all siblings and uses the override status for the completing child. When all siblings are terminal (`completed` or `failed`), the parent transitions to `completed` (all succeeded) or `failed` (any failed). Only after finalization does `_complete_job()` delete the child.

The transition uses the same delete-and-recreate pattern as `_pop_job()` to avoid KeyField index corruption.

### Health Monitor Extensions

The periodic health check (`_job_hierarchy_health_check()`) detects and self-heals:

- **Orphaned children**: `parent_job_id` points to non-existent session -- cleared
- **Stuck parents**: `waiting_for_children` with all children terminal -- auto-finalized

See [Job Scheduling](job-scheduling.md) for usage details and CLI commands.

## See Also

- `docs/features/scale-job-queue-with-popoto-and-worktrees.md` -- Original job queue architecture
- `docs/features/job-scheduling.md` -- Agent-initiated scheduling tool
- `docs/features/job-dependency-tracking.md` -- Sibling dependencies, branch mapping, checkpoint/restore, PM controls
- `agent/job_queue.py` -- Implementation
