---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-16
tracking: https://github.com/yudame/valor-ai/issues/127
---

# Job Health Monitor: Detect and Recover Stuck Running Jobs

## Problem

Jobs can get permanently stuck in `running` status when the underlying Claude Code process dies silently. The RedisJob stays `running` forever with no process behind it. Since workers are serialized per project, this blocks the entire project queue.

**Current behavior:**
On 2026-02-16, a job ran for 3+ hours with no process behind it. All subsequent messages queued as `pending` and were never processed. Required manual Redis intervention to discover and flush.

The root cause: `_recover_interrupted_jobs()` only runs at startup. There is zero runtime monitoring of job liveness. The session watchdog monitors `AgentSession` objects but has no visibility into `RedisJob` status or process liveness.

**Desired outcome:**
Stuck `running` jobs are automatically detected within 5 minutes, recovered (re-queued or failed), and logged clearly. A CLI tool exists for manual inspection and flushing.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on timeout thresholds and recovery behavior)
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing Redis infrastructure and asyncio patterns already in the codebase.

## Solution

### Key Elements

- **Job health checker**: Periodic async task that scans `running` RedisJobs, checks if their worker coroutine is still alive, and recovers dead ones
- **`started_at` timestamp on RedisJob**: Enables timeout detection (currently only `created_at` exists via SortedField)
- **CLI tool**: `python -m agent.job_queue --status` and `--flush-stuck` for manual inspection

### Flow

**Timer fires (every 5 min)** → Query all `running` RedisJobs → For each: check if `_active_workers[project_key]` task is alive AND job hasn't exceeded max duration → If dead or timed out → log warning, reset to `pending` (or mark `failed` if retried too many times) → `_ensure_worker()` to restart processing

### Technical Approach

- Add `started_at = Field(type=float, null=True)` to `RedisJob` — set when status transitions to `running` in `_pop_job()`
- Create `_job_health_check()` async function in `job_queue.py` that:
  1. Queries all `running` RedisJobs across all project keys
  2. For each job, checks: is `_active_workers[project_key]` present and `.done() == False`?
  3. If worker is dead: reuse `_recover_interrupted_jobs()` logic (delete-and-recreate as `pending`)
  4. If job exceeds max duration (45 min standard, 2.5h for builds): same recovery. Timeout is measured from `started_at` (processing start), not `created_at` (enqueue time)
  5. Logs each recovery action clearly
- Start `_job_health_loop()` as an asyncio task alongside the existing session watchdog in bridge startup
- Add `__main__.py` in `agent/` for CLI: parse `--status`, `--flush-stuck`, `--flush-job <id>` args

**Why extend session_watchdog vs. new loop?** Keep it separate. Session watchdog monitors `AgentSession` (application-level). Job health monitors `RedisJob` (queue-level). Different concerns, different recovery actions. But they run at the same interval (5 min) and follow the same pattern.

## Rabbit Holes

- **PID tracking**: Tempting to store the OS PID of the Claude subprocess and check `/proc/{pid}`. Not worth it — asyncio Task liveness is sufficient since the worker owns the subprocess. If the Task is done, the job is dead.
- **Graceful job cancellation**: Don't try to `SIGTERM` stuck Claude processes. Just mark the job dead and move on. Claude processes clean themselves up or get reaped by the OS.
- **Retry limits with backoff**: Over-engineering for this appetite. Simple "recover once, fail on second stuck" is enough.

## Risks

### Risk 1: Race condition between health check and normal completion
**Impact:** Health check resets a job that's about to complete normally, causing duplicate execution.
**Mitigation:** Only recover jobs where the worker asyncio Task is `.done()` AND the job has been running for > 5 minutes (measured from `started_at`). Normal jobs complete in seconds to minutes; a 5-min running job with a dead worker is definitively stuck.

### Risk 2: Health check runs during bridge shutdown
**Impact:** Could interfere with graceful shutdown's `_reset_running_jobs()`.
**Mitigation:** Check for a shutdown flag before taking recovery action. The existing `_check_restart_flag()` pattern works here.

## No-Gos (Out of Scope)

- No distributed locking or multi-instance awareness (single machine)
- No job retry limits or exponential backoff (recover once, fail on second)
- No alerting/notifications on stuck jobs (just logging — bridge watchdog handles alerting)
- No changes to the session watchdog (separate concern)

## Update System

No update system changes required — this is a bridge-internal change. The health monitor starts automatically with the bridge. No new config files or dependencies.

## Agent Integration

No agent integration required — this is bridge-internal infrastructure. The health monitor runs as an asyncio background task within the bridge process. The CLI is for human operators, not the agent.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/job-health-monitor.md` describing the monitoring and recovery behavior
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings on `_job_health_check()`, `_job_health_loop()`, and CLI functions
- [ ] Update the "Existing Infrastructure" table in issue #127 body when complete

## Success Criteria

- [ ] `started_at` field added to `RedisJob`, populated when job transitions to `running`
- [ ] Health check loop runs every 5 minutes, detects `running` jobs with dead workers
- [ ] Dead jobs are automatically recovered (reset to `pending`) and logged
- [ ] Jobs exceeding timeout are detected and recovered (45 min standard, 2.5h builds, measured from `started_at`)
- [ ] `python -m agent.job_queue --status` shows current queue state (running, pending, counts)
- [ ] `python -m agent.job_queue --flush-stuck` recovers all orphaned running jobs
- [ ] Health check integrates into bridge startup alongside session watchdog
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (health-monitor)**
  - Name: health-builder
  - Role: Implement health check loop, RedisJob changes, and CLI
  - Agent Type: builder
  - Resume: true

- **Validator (health-monitor)**
  - Name: health-validator
  - Role: Verify health check detects stuck jobs and CLI works
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add `started_at` field and health check core
- **Task ID**: build-health-monitor
- **Depends On**: none
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `started_at = Field(type=float, null=True)` to `RedisJob`
- Set `started_at = time.time()` in `_pop_job()` when status transitions to `running`
- Create `_job_health_check()` async function:
  - Query all `running` RedisJobs
  - For each: check `_active_workers[project_key]` is alive (exists and not `.done()`)
  - If worker dead or missing: log warning, call recovery (delete-recreate as `pending`)
  - If `started_at` exists and job exceeds max duration: same recovery
  - `JOB_TIMEOUT_DEFAULT = 2700` (45 min for standard jobs)
  - `JOB_TIMEOUT_BUILD = 9000` (2.5 hours for build jobs — detected by `/do-build` in message_text)
  - Timeouts measured from `started_at` (processing start), not `created_at` (enqueue time)
- Create `_job_health_loop()`: run `_job_health_check()` every 300 seconds (5 min)
- Wire `_job_health_loop()` into bridge startup (same place session watchdog starts)

### 2. Add CLI entry point
- **Task ID**: build-cli
- **Depends On**: build-health-monitor
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/__main__.py` with argparse
- `--status`: List all RedisJobs grouped by project_key and status, show started_at/created_at
- `--flush-stuck`: Find all `running` jobs with no live worker, recover them
- `--flush-job <job_id>`: Recover a specific job by ID

### 3. Validate implementation
- **Task ID**: validate-health-monitor
- **Depends On**: build-cli
- **Assigned To**: health-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `started_at` is set on running jobs
- Verify health check loop is started in bridge startup
- Verify CLI `--status` produces readable output
- Verify recovery logic matches existing `_recover_interrupted_jobs()` pattern
- Run `black . && ruff check .` passes

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-health-monitor
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/job-health-monitor.md`
- Add entry to `docs/features/README.md` index table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: health-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Check documentation exists and is indexed

## Validation Commands

- `python -c "from agent.job_queue import RedisJob; assert hasattr(RedisJob, 'started_at')"` — field exists
- `python -m agent.job_queue --status` — CLI works
- `ruff check agent/job_queue.py agent/__main__.py` — lint passes
- `black --check agent/job_queue.py agent/__main__.py` — format passes
- `pytest tests/ -x` — all tests pass
