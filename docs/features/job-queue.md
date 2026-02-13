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
new_job = await RedisJob.async_create(**fields)  # adds to new index via on_save
```

This is applied in three functions:
- `_pop_job()` -- pending to running
- `_recover_interrupted_jobs()` -- running to pending (sync, startup)
- `_reset_running_jobs()` -- running to pending (async, shutdown)

The `_extract_job_fields()` helper reads all 24 non-auto fields from a RedisJob instance for recreation.

## Worker Drain Guard

The worker loop now includes a drain guard before exiting. When `_pop_job()` returns `None`, the worker sleeps 100ms (yielding to the event loop) then re-checks. This catches jobs whose `async_create` index writes were still in-flight due to popoto's three-step creation process (`HSET`, `SADD` class set, `SADD` KeyField index).

## Startup Orphan Recovery

`_recover_orphaned_jobs()` scans for RedisJob objects in the Redis class set that are not present in any status KeyField index. These orphans result from past index corruption or creation races. They are re-created with status `pending` and priority `high`. Called at bridge startup alongside `_recover_interrupted_jobs()`.

## See Also

- `docs/features/scale-job-queue-with-popoto-and-worktrees.md` -- Original job queue architecture
- `agent/job_queue.py` -- Implementation
