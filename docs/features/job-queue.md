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

This is applied in three functions:
- `_pop_job()` -- pending to running
- `_recover_interrupted_jobs()` -- running to pending (sync, startup)
- `_reset_running_jobs()` -- running to pending (async, shutdown)

The `_extract_job_fields()` helper reads all non-auto fields (56+) from an AgentSession instance for recreation.

## Worker Drain Guard

The worker loop now includes a drain guard before exiting. When `_pop_job()` returns `None`, the worker sleeps 100ms (yielding to the event loop) then re-checks. This catches jobs whose `async_create` index writes were still in-flight due to popoto's three-step creation process (`HSET`, `SADD` class set, `SADD` KeyField index).

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

## See Also

- `docs/features/scale-job-queue-with-popoto-and-worktrees.md` -- Original job queue architecture
- `docs/features/job-scheduling.md` -- Agent-initiated scheduling tool
- `agent/job_queue.py` -- Implementation
