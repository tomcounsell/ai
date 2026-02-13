---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-02-13
tracking: https://github.com/yudame/valor-agent/issues/88
---

# Fix job queue losing enqueued jobs

## Problem

Two distinct bugs cause the job queue to lose or misroute jobs:

**Bug 1: Creation race condition** — Queued jobs are silently lost when enqueued while a worker is processing another job. The worker finishes, calls `_pop_job()`, gets `None`, and exits — even though jobs were successfully created in Redis moments earlier.

Evidence from logs:
1. Worker processes job A
2. Jobs B and C are enqueued while A runs (logs confirm `depth=1`, `depth=2`)
3. Job A completes, worker calls `_pop_job()`
4. `_pop_job()` queries `RedisJob.query.async_filter(status="pending")` and finds nothing
5. Worker logs "Queue empty, worker exiting" — jobs B and C are never processed

Root cause: Popoto's `async_create()` uses `to_thread(cls.create)` which executes three separate Redis commands without pipelining: HSET (object data), SADD to class set, SADD to KeyField index set (last). If `async_filter` queries the index between steps 2 and 3, it returns empty.

**Bug 2: KeyField index corruption on status transitions** — `KeyField.on_save()` only ADDs the object key to the new status index set — it never REMOVES from the old one. Every in-place status mutation (e.g. `job.status = "running"; await job.async_save()`) leaves a stale entry in the previous index set, causing ghost jobs and double-processing.

Three functions use this broken pattern:
- `_pop_job()`: `chosen.status = "running"` + `async_save()` — leaves stale entry in `pending` index
- `_recover_interrupted_jobs()`: `job.status = "pending"` + `save()` — leaves stale entry in `running` index
- `_reset_running_jobs()`: `job.status = "pending"` + `async_save()` — same corruption

**Desired outcome:**
All enqueued jobs are eventually processed. Status transitions are clean with no index corruption. The worker never exits while jobs exist in Redis.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0 (ship it)

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Delete-and-recreate pattern**: Safe KeyField status transitions that avoid index corruption
- **`_extract_job_fields()` helper**: Extracts all fields from a RedisJob for recreation
- **Worker drain guard**: Re-check for pending jobs before exiting the worker loop
- **Startup orphan recovery**: Scan for RedisJob objects stranded by past index corruption
- **Regression tests**: Prove both bugs are fixed

### Technical Approach

#### Fix 1: Delete-and-recreate for all status transitions (index corruption fix)

Popoto's `KeyField.on_save()` only adds to the new index — never removes from the old one. The only safe way to change a KeyField value is to delete the old object and create a new one with the desired status. This ensures the old index entry is removed (via `on_delete`) and the new one is added (via `on_save`).

Add `_extract_job_fields(redis_job)` helper that reads all field values from a RedisJob instance and returns them as a dict suitable for `RedisJob.async_create(**fields)`.

Apply delete-and-recreate to all three status-mutating functions:

**`_pop_job()`** (partially edited — complete the edit):
```python
fields = _extract_job_fields(chosen)
await chosen.async_delete()
fields["status"] = "running"
new_job = await RedisJob.async_create(**fields)
return Job(new_job)
```

**`_recover_interrupted_jobs()`** (sync, called at startup):
```python
fields = _extract_job_fields(job)
job.delete()
fields["status"] = "pending"
fields["priority"] = "high"
RedisJob.create(**fields)
```

**`_reset_running_jobs()`** (async, called during graceful shutdown):
```python
fields = _extract_job_fields(job)
await job.async_delete()
fields["status"] = "pending"
fields["priority"] = "high"
await RedisJob.async_create(**fields)
```

#### Fix 2: Worker drain guard (creation race defense)

In `_worker_loop()`, before breaking on `job is None`, yield to the event loop (`await asyncio.sleep(0.1)`) and re-query. This gives any in-flight `async_create` calls time to complete their index writes. Only exit after two consecutive empty reads.

```python
job = await _pop_job(project_key)
if job is None:
    await asyncio.sleep(0.1)  # yield — let pending creates finish
    job = await _pop_job(project_key)  # second check
    if job is None:
        logger.info(f"[{project_key}] Queue empty, worker exiting")
        break
    logger.info(f"[{project_key}] Drain guard caught job that would have been lost")
```

#### Fix 3: Startup orphan recovery

At startup (in `_recover_interrupted_jobs` or a new function), scan for RedisJob objects in the class set that are NOT present in any KeyField status index. These are orphans from past index corruption or creation races. Re-create them with status `pending`.

### Flow

**Message arrives** → `enqueue_job()` creates RedisJob → `_ensure_worker()` starts/reuses worker → Worker pops job (delete-and-recreate to "running") → Executes → Pops next → **Drain guard**: sleep + re-check → truly empty → Worker exits

**Startup** → `_recover_interrupted_jobs()` finds "running" jobs (delete-and-recreate to "pending") → orphan scan finds stranded objects → re-index → `_ensure_worker()`

## Rabbit Holes

- **Don't rewrite popoto's async layer** — The ORM works; we work around the KeyField limitation with delete-and-recreate
- **Don't add a persistent worker that polls** — The current "start on enqueue, exit when empty" pattern is correct; we just need a safer exit condition
- **Don't add Redis pub/sub notification** — Over-engineering for a timing window that's ~1ms wide
- **Don't switch to a different queue library** — The Redis-backed queue is the right architecture; the bugs are in status transitions and exit conditions
- **Don't try to pipeline async_create** — The `to_thread` wrapper makes pipeline execution semantics unreliable; delete-and-recreate is the cleaner fix

## Risks

### Risk 1: Delete-and-recreate changes the job_id (AutoKeyField)
**Impact:** Any code referencing the old `job_id` after a status transition will point to a deleted object
**Mitigation:** `_pop_job` returns a `Job` wrapper — callers never see the old ID. `_recover_interrupted_jobs` and `_reset_running_jobs` don't return IDs. Log both old and new IDs for traceability.

### Risk 2: Race between delete and recreate in _pop_job
**Impact:** If the process crashes between `async_delete()` and `async_create()`, the job is lost
**Mitigation:** This window is sub-millisecond (two Redis commands). The existing `_recover_interrupted_jobs` already handles crash recovery for "running" jobs — extend it to also recover from this edge case via orphan scan.

### Risk 3: Sleep duration in drain guard too short or too long
**Impact:** Too short = still misses jobs; too long = unnecessary latency before worker exit
**Mitigation:** 100ms is generous (Redis commands take <1ms). Can be tuned. The double-check pattern is correct regardless of duration.

## No-Gos (Out of Scope)

- Changing the FILO queue ordering
- Adding queue persistence beyond Redis
- Modifying popoto library internals
- Adding worker health monitoring or heartbeats
- Changing the per-project sequential worker model
- Pipelined `async_create` (unreliable with `to_thread`)

## Update System

No update system changes required — this is an internal bug fix to the job queue module. `uv sync` handles any dependency changes.

## Agent Integration

No agent integration required — this is a bridge-internal change to the worker loop and job creation. No new tools or MCP changes.

## Documentation

- [ ] Update `docs/features/job-queue.md` with drain guard and delete-and-recreate patterns (if file exists), or add a note to the job queue module docstring
- [ ] Add inline comments explaining the KeyField index corruption and why delete-and-recreate is necessary

## Success Criteria

- [ ] `_extract_job_fields()` helper exists and extracts all RedisJob fields
- [ ] `_pop_job()` uses delete-and-recreate (complete the partial edit)
- [ ] `_recover_interrupted_jobs()` uses delete-and-recreate (sync)
- [ ] `_reset_running_jobs()` uses delete-and-recreate (async)
- [ ] Worker drain guard prevents premature exit
- [ ] Startup orphan recovery detects and re-indexes stranded jobs
- [ ] Regression tests prove both bugs are fixed
- [ ] Existing job queue tests still pass
- [ ] Bridge starts and processes queued messages correctly

## Team Orchestration

### Team Members

- **Builder (queue-fix)**
  - Name: queue-builder
  - Role: Implement all fixes and regression tests
  - Agent Type: builder
  - Resume: true

- **Validator (queue-fix)**
  - Name: queue-validator
  - Role: Verify fixes, run tests, check edge cases
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Implement `_extract_job_fields` helper and fix `_pop_job`
- **Task ID**: build-extract-and-pop
- **Depends On**: none
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_extract_job_fields(redis_job: RedisJob) -> dict` that reads all non-auto fields from a RedisJob instance
- Complete the partially-edited `_pop_job()` which already calls `_extract_job_fields` but the function doesn't exist yet
- Verify `_pop_job` correctly removes from pending index and adds to running index

### 2. Fix `_recover_interrupted_jobs` and `_reset_running_jobs`
- **Task ID**: build-fix-status-transitions
- **Depends On**: build-extract-and-pop
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Convert `_recover_interrupted_jobs()` (sync) to use delete-and-recreate via `_extract_job_fields`
- Convert `_reset_running_jobs()` (async) to use delete-and-recreate via `_extract_job_fields`
- Log old and new job IDs for traceability

### 3. Implement worker drain guard
- **Task ID**: build-drain-guard
- **Depends On**: build-fix-status-transitions
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `_worker_loop()` to add `await asyncio.sleep(0.1)` + second `_pop_job()` call before the `break`
- Log when the drain guard catches a job that would have been lost

### 4. Add startup orphan recovery
- **Task ID**: build-orphan-recovery
- **Depends On**: build-drain-guard
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add function to scan for RedisJob objects in the class set not present in any status KeyField index
- Re-create orphaned jobs with status `pending` and priority `high`
- Call during startup alongside `_recover_interrupted_jobs`

### 5. Add regression tests
- **Task ID**: build-regression-tests
- **Depends On**: build-orphan-recovery
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/test_job_queue_race.py` that:
  - Tests `_extract_job_fields` returns complete field set
  - Tests `_pop_job` delete-and-recreate doesn't leave stale pending entries
  - Tests `_recover_interrupted_jobs` delete-and-recreate doesn't leave stale running entries
  - Tests `_reset_running_jobs` delete-and-recreate doesn't leave stale running entries
  - Tests worker drain guard catches jobs created during processing
- Ensure tests use the `redis_test_db` fixture for isolation

### 6. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-regression-tests
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/`
- Verify drain guard logging works
- Review code for edge cases (shutdown during drain guard, crash between delete and recreate)
- Confirm KeyField indexes are clean after all status transitions
- Confirm no regressions in existing queue behavior

## Validation Commands

- `pytest tests/test_job_queue_race.py -v` — regression tests pass
- `pytest tests/test_redis_models.py -v` — existing model tests pass
- `pytest tests/ -v` — full suite passes
- `ruff check agent/job_queue.py` — no lint issues
- `black --check agent/job_queue.py` — formatting correct
