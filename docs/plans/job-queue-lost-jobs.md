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

Queued jobs are silently lost when enqueued while a worker is processing another job. The worker finishes, calls `_pop_job()`, gets `None`, and exits — even though jobs were successfully created in Redis moments earlier.

**Current behavior:**
1. Worker processes job A
2. Jobs B and C are enqueued while A runs (logs confirm `depth=1`, `depth=2`)
3. Job A completes, worker calls `_pop_job()`
4. `_pop_job()` queries `RedisJob.query.async_filter(status="pending")` and finds nothing
5. Worker logs "Queue empty, worker exiting" — jobs B and C are never processed

**Root cause:**
Popoto's `async_create()` uses `to_thread(cls.create)` which executes three separate Redis commands without pipelining:
1. `HSET` — object data saved
2. `SADD` to class set — object registered
3. `SADD` to KeyField index set — `$KeyF:RedisJob:status:pending` updated (happens LAST)

`async_filter(status="pending")` queries the KeyField index SET via `SMEMBERS`. If the filter runs between steps 2 and 3, the index is empty and `_pop_job()` returns `None`. The worker exits, and those jobs are orphaned in Redis forever (data exists but index doesn't point to them).

**Desired outcome:**
All enqueued jobs are eventually processed. The worker never exits while jobs exist in Redis.

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

- **Worker drain guard**: Re-check for pending jobs before exiting the worker loop, with a brief yield to allow in-flight index updates to complete
- **Atomic job creation**: Use Redis pipelines in `_push_job()` so object data and KeyField indexes are written in a single round-trip
- **Regression test**: Simulate the race condition to prove the fix works

### Technical Approach

#### Fix 1: Worker drain guard (defense in depth)

In `_worker_loop()`, before breaking on `job is None`, yield to the event loop (`await asyncio.sleep(0.1)`) and re-query. This gives any in-flight `async_create` calls time to complete their index writes. Only exit after two consecutive empty reads.

```
# Pseudocode for the guard
job = await _pop_job(project_key)
if job is None:
    await asyncio.sleep(0.1)  # yield — let pending creates finish
    job = await _pop_job(project_key)  # second check
    if job is None:
        break  # truly empty
```

This is the primary fix. It's simple, low-risk, and addresses the symptom directly regardless of ORM internals.

#### Fix 2: Pipelined job creation (root cause fix)

Modify `_push_job()` to pass a Redis pipeline to `RedisJob.async_create()` (popoto supports a `pipeline` kwarg on `create()`). This makes the HSET + SADD operations atomic, eliminating the race window entirely.

However, `async_create` wraps `create` via `to_thread` and pipeline execution semantics need testing — the pipeline must be executed within the same thread. If popoto's pipeline support doesn't work cleanly with `async_create`, fall back to calling `RedisJob.create(pipeline=pipe)` synchronously within `to_thread` manually.

#### Fix 3: Startup orphan recovery

Add a startup check that scans for RedisJob objects whose data exists in Redis but whose `status:pending` index entry is missing. This recovers any jobs orphaned by past race conditions.

### Flow

**Message arrives** → `enqueue_job()` creates RedisJob (pipelined) → `_ensure_worker()` starts/reuses worker → Worker pops job → Executes → Pops next → **Drain guard**: sleep + re-check → truly empty → Worker exits

## Rabbit Holes

- **Don't rewrite popoto's async layer** — The ORM works; we just need to use pipelines or guard against the timing gap
- **Don't add a persistent worker that polls** — The current "start on enqueue, exit when empty" pattern is correct; we just need a safer exit condition
- **Don't add Redis pub/sub notification** — Over-engineering for a timing window that's ~1ms wide
- **Don't switch to a different queue library** — The Redis-backed queue is the right architecture; the bug is in the exit condition

## Risks

### Risk 1: Pipeline kwarg may not work with async_create
**Impact:** Fix 2 (atomic creation) can't be applied via the simple `pipeline=` path
**Mitigation:** Fall back to wrapping sync `create(pipeline=pipe)` + `pipe.execute()` in `asyncio.to_thread()` manually. Fix 1 (drain guard) works regardless.

### Risk 2: Sleep duration too short or too long
**Impact:** Too short = still misses jobs; too long = unnecessary latency before worker exit
**Mitigation:** 100ms is generous (Redis commands take <1ms). Can be tuned. The double-check pattern is correct regardless of duration.

## No-Gos (Out of Scope)

- Changing the FILO queue ordering
- Adding queue persistence beyond Redis
- Modifying popoto library internals
- Adding worker health monitoring or heartbeats
- Changing the per-project sequential worker model

## Update System

No update system changes required — this is an internal bug fix to the job queue module. `uv sync` handles any dependency changes.

## Agent Integration

No agent integration required — this is a bridge-internal change to the worker loop and job creation. No new tools or MCP changes.

## Documentation

- [ ] Update `docs/features/job-queue.md` with drain guard behavior (if file exists) or add a note to the job queue docstring
- [ ] Add inline comments explaining the race condition and why the drain guard exists

## Success Criteria

- [ ] Worker never exits while pending jobs exist in Redis
- [ ] `_push_job()` uses pipelined Redis operations (or documents why not)
- [ ] Regression test proves the race condition is fixed
- [ ] Existing job queue tests still pass
- [ ] Bridge starts and processes queued messages correctly
- [ ] No orphaned jobs after 24h of production use

## Team Orchestration

### Team Members

- **Builder (queue-fix)**
  - Name: queue-builder
  - Role: Implement drain guard, pipelined creation, and regression test
  - Agent Type: builder
  - Resume: true

- **Validator (queue-fix)**
  - Name: queue-validator
  - Role: Verify fixes, run tests, check edge cases
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Implement worker drain guard
- **Task ID**: build-drain-guard
- **Depends On**: none
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `_worker_loop()` in `agent/job_queue.py` to add double-check before exiting
- Add `await asyncio.sleep(0.1)` + second `_pop_job()` call before the `break`
- Log when the drain guard catches a job that would have been lost

### 2. Implement pipelined job creation
- **Task ID**: build-pipeline-create
- **Depends On**: build-drain-guard
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `_push_job()` to use `RedisJob.create(pipeline=...)` with pipeline execution
- Test that KeyField indexes are written atomically with object data
- If pipeline approach doesn't work with async, document why and keep drain guard as primary fix

### 3. Add regression test
- **Task ID**: build-regression-test
- **Depends On**: build-pipeline-create
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Create test in `tests/test_job_queue_race.py` that:
  - Creates a job while a worker is running
  - Verifies the job is processed (not lost)
  - Tests the drain guard catches the edge case
- Ensure test uses the `redis_test_db` fixture for isolation

### 4. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-regression-test
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/`
- Verify drain guard logging works
- Review code for edge cases (shutdown during drain guard, multiple workers)
- Confirm no regressions in existing queue behavior

## Validation Commands

- `pytest tests/test_job_queue_race.py -v` — regression test passes
- `pytest tests/test_redis_models.py -v` — existing model tests pass
- `pytest tests/ -v` — full suite passes
- `ruff check agent/job_queue.py` — no lint issues
- `black --check agent/job_queue.py` — formatting correct
