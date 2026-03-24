---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/495
last_comment_id: 4115184066
---

# Bridge Resilience: Graceful Degradation & Recovery Pipeline Simplification

## Problem

Two incidents on 2026-03-23 and 2026-03-24 exposed compounding failures in the bridge's resilience and recovery mechanisms.

**Incident 1 (2026-03-23):** Bridge crash-looped 10 times in 2 minutes due to Telegram session lock conflicts. Watchdog killed active SDK processes. SDK retries had no backoff.

**Incident 2 (2026-03-24):** Two messages in PM: Valor were lost. Session 99 ran for 26 minutes (normal, just slow). Session 100 was stuck pending behind it. The stall recovery system tried to help but used `project_key` instead of `chat_id` to start a worker — the worker found an empty queue and exited. The job was deleted-and-recreated during retry, breaking correlation. Six overlapping recovery mechanisms competed without coordination, and the "recovery" is what actually lost the message.

**Current behavior:**
- Startup retry only covers SQLite lock errors (3 attempts at 2s/5s/10s). Other failures crash immediately.
- SDK client has max 2 retries on error but no backoff and no circuit breaker.
- Six overlapping recovery mechanisms (`_recover_interrupted_jobs`, `_recover_orphaned_jobs`, `_reset_running_jobs`, `_job_health_check`, `check_stalled_sessions`/`_recover_stalled_pending`, `_enqueue_stall_retry`) race against each other and use delete-and-recreate which loses jobs.
- Stall recovery uses `project_key` to look up workers that are keyed by `chat_id` — every stall retry starts a worker for the wrong queue.
- Pending stall detector doesn't know another job is running on the same chat — treats normal queue wait as a stall.
- No way to distinguish slow SDK calls from hung ones (watchdog logs once at 180s, then silence).
- Reflections crash with tracebacks when prerequisites are missing.
- No degraded mode when Anthropic is down — messages are silently lost.

**Desired outcome:**
- One correct recovery loop replaces six competing mechanisms (net code deletion)
- Bridge survives temporary outages of any single dependency without crash-looping
- Recovery is simple: "is there a live worker for this job's chat_id? If not, start one."
- PM handles dependency failures silently (reschedule/retry), alerts human only for auth failures
- Observability: structured logging with correlation IDs, periodic SDK heartbeats, job status CLI

## Prior Art

Relevant recent commits inform the solution:
- `6ef1117f`: Fix zombie cleanup killing active SDK processes (exit code 143)
- `44996159`: Fix watchdog killing healthy bridge sessions when zombies detected
- `949e9a31`: Fix zombie Claude Code process accumulation
- `44ad4569`: Comprehensive resilience overhaul (activity tracking, circuit breaker, stall detection)
- `c4b70812`: Observer circuit breaker with exponential backoff

The observer circuit breaker pattern will be generalized. The stall recovery functions will be deleted entirely.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `44ad4569` | Added stall detection + recovery | Recovery built around `project_key` but workers refactored to `chat_id` — every recovery starts the wrong worker |
| `6ef1117f` | Fixed watchdog killing active SDK | Only addressed zombie cleanup, not the pending stall false-positive that treats queue wait as a stall |
| Stall retry mechanism | Delete-and-recreate job on stall | Race-prone: deletes job a legitimate worker is about to pop. New job_id breaks correlation. Create failure = permanent job loss |

**Root cause pattern:** Recovery mechanisms were added incrementally without revisiting whether earlier ones were still correct after the worker-keying refactor from project_key to chat_id.

## Data Flow

**Current (broken) recovery flow:**
1. Job enqueued for `chat_id=-1003449100931`, worker started for that chat
2. Worker busy with job N, job N+1 sits pending
3. Session watchdog detects pending stall (300s threshold, unaware of running job)
4. `_recover_stalled_pending()` calls `_kill_stalled_worker(project_key="valor")` → finds nothing (workers keyed by chat_id)
5. `_enqueue_stall_retry()` deletes original job, creates new one, calls `_ensure_worker("valor")` → starts worker for wrong key
6. Worker for `chat:valor` finds empty queue, exits. Job orphaned in Redis forever.
7. When job N completes, original worker checks queue — job N+1 is gone (deleted by step 5).

**Proposed (simple) recovery flow:**
1. Job enqueued for `chat_id=-1003449100931`, worker started for that chat
2. Worker busy with job N, job N+1 sits pending — this is normal queue behavior
3. Single health check scans ALL jobs (pending + running)
4. For each job: is there a live worker for `job.chat_id`? If yes, skip. If no, start one.
5. Recovery of truly orphaned jobs (worker dead) uses Popoto's delete-and-recreate (required by KeyField). But the health check never deletes jobs that have a live worker — no more destroying jobs a legitimate worker is about to pop.

## Architectural Impact

- **Deleted modules/functions:** `_recover_stalled_pending()`, `_kill_stalled_worker()`, `_enqueue_stall_retry()` from `session_watchdog.py`; `_recover_orphaned_jobs()`, `_reset_running_jobs()` from `job_queue.py`
- **New module:** `bridge/resilience.py` — reusable CircuitBreaker class extracted from observer pattern
- **New module:** `bridge/health.py` — dependency health tracking
- **Modified:** `_job_health_check()` in `job_queue.py` expanded to cover pending jobs and become the single recovery mechanism
- **Interface changes:** None external. `sdk_client.query()` wraps calls with circuit breaker internally.
- **Coupling:** Reduces coupling — centralizes retry/backoff instead of per-call ad hoc handling
- **Reversibility:** High — circuit breakers can be disabled by setting thresholds high. Recovery refactor is independently revertable.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on degraded mode UX)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — uses only stdlib and existing packages.

## Solution

### Key Elements

- **Unified recovery loop** (`agent/job_queue.py`): Expand `_job_health_check()` to scan both `running` and `pending` jobs. For each: check if a live worker exists for `job.chat_id`. If worker is missing and job has exceeded a threshold, start a worker for that chat_id. Delete the six competing mechanisms (~400 lines) and replace with this one (~100 lines). No delete-and-recreate.
- **CircuitBreaker class** (`bridge/resilience.py`): Extract observer circuit breaker into reusable class with configurable failure threshold, backoff schedule, half-open probe. States: closed/open/half-open.
- **Startup retry** (`telegram_bridge.py`): Replace SQLite-only retry with general connection retry covering all Telethon errors. Exponential backoff with jitter, capped at ~5 min.
- **SDK circuit breaker** (`sdk_client.py`): Wrap query calls with CircuitBreaker. On sustained Anthropic failures, open circuit and fail fast.
- **Degraded mode** (`bridge/telegram_bridge.py`): When Anthropic circuit is open, acknowledge on Telegram and queue to dead-letter for replay.
- **Auth failure alerts**: When Claude Code OAuth or Telegram session auth fails (requires manual re-login), send a Telegram alert to the stakeholder. All other dependency failures are handled silently by circuit breakers. PM error routing (intelligent reschedule/retry/cancel decisions) deferred to follow-up issue.
- **Reflections pre-flight** (`scripts/reflections.py`): Validate prerequisites before each task. Single warning log on failure, not tracebacks.
- **Observability basics**: Structured JSON logging with `job_id`/`session_id`/`correlation_id` fields. Periodic SDK heartbeat (log every 60s during running jobs). Job status CLI (`python -m agent.job_queue --status`).

### Flow

**Message arrives** → Check Anthropic circuit → If closed: process normally → If open: acknowledge on Telegram, persist to dead-letter → When circuit closes: replay

**Job recovery** → Periodic scan of all jobs → For each: worker alive for chat_id? → Yes: skip → No + exceeded threshold: `_ensure_worker(chat_id)` → Worker pops and processes job naturally

**Startup** → Attempt Telethon connect → On failure: backoff with jitter (2s, 4s, 8s... 256s cap) → After max attempts: exit for launchd restart

### Technical Approach

- `_job_health_check()` becomes the single recovery point: queries all `running` and `pending` jobs, checks `_active_workers.get(job.chat_id)`, starts worker if missing. For truly orphaned jobs (worker dead + exceeded threshold), uses Popoto's delete-and-recreate to reset status (required by KeyField architecture). Key distinction: never touches jobs that have a live worker on the same chat_id.
- Delete: `_recover_orphaned_jobs`, `_reset_running_jobs`, `_recover_stalled_pending`, `_kill_stalled_worker`, `_enqueue_stall_retry`
- Fold `_recover_interrupted_jobs` into `_job_health_check` with a startup mode: runs synchronously at startup before the event loop processes messages, resets all `running` jobs to `pending` unconditionally (at startup, all running jobs are by definition orphaned from previous crash). Periodic mode applies the threshold check.
- CircuitBreaker: parameterized with `failure_threshold`, `backoff_schedule`, `half_open_interval`, `on_open`/`on_close` callbacks
- Degraded mode: when Anthropic circuit is open, incoming messages stay in the job queue as `pending` (not dead-lettered). The health check will start a worker when the circuit closes. Dead-letter queue is only for Telegram delivery failures, not API outages.
- Structured logging: JSON formatter with `job_id`, `session_id`, `correlation_id`, `chat_id` fields on every log line
- SDK heartbeat: `BackgroundTask._watchdog` emits periodic logs (every 60s) with subprocess liveness check, not just a single 180s check
- CancelledError: catch explicitly in `_worker_loop` and `sdk_client.query` — log and complete job properly

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit and fix `except Exception: pass` blocks in touched files — each must log or change state
- [ ] `_worker_loop` catches `CancelledError` explicitly, logs it, completes job
- [ ] Circuit breaker `on_open` logs at WARNING with dependency name and failure count

### Empty/Invalid Input Handling
- [ ] Health check handles zero active jobs gracefully
- [ ] CircuitBreaker handles zero-threshold (always open) and infinite-threshold (never opens)
- [ ] Startup retry handles immediate success (no unnecessary sleep)
- [ ] Recovery handles jobs with missing `chat_id` (legacy data)

### Error State Rendering
- [ ] Degraded mode acknowledgment includes human-readable status
- [ ] Job status CLI outputs readable table even with zero jobs

## Test Impact

- [ ] `tests/unit/test_pending_recovery.py` — DELETE: tests the stall retry mechanism being removed
- [ ] `tests/unit/test_stall_detection.py` — UPDATE: remove tests for `_recover_stalled_pending`, `_kill_stalled_worker`, `_enqueue_stall_retry`; add tests for unified health check covering pending jobs
- [ ] `tests/unit/test_transcript_liveness.py` — UPDATE: remove references to deleted recovery functions if any
- [ ] `tests/unit/test_bridge_logic.py` — UPDATE: add tests for new startup retry covering non-SQLite errors
- [ ] `tests/unit/test_sdk_client.py` — UPDATE: add tests for circuit breaker wrapping and CancelledError handling

## Rabbit Holes

- **Distributed circuit breakers via Redis** — Bridge is single process. In-memory state is sufficient.
- **HTTP health endpoint** — Bridge doesn't serve HTTP. Use file/in-process reporting.
- **Per-request retry strategies** — Circuit breaker at dependency level is the right granularity.
- **Multi-LLM failover** — Out of scope. Bridge is coupled to Anthropic SDK.
- **Popoto KeyField migration** — The delete-and-recreate pattern exists because Popoto KeyFields can't be updated in place. Fixing Popoto is out of scope; instead, avoid status changes that require delete-and-recreate.

## Risks

### Risk 1: Removing recovery mechanisms exposes edge cases they handled
**Impact:** Some obscure failure mode that one of the six mechanisms caught is now unhandled
**Mitigation:** The unified health check covers the same two conditions (worker dead + job exceeded threshold) that all six mechanisms ultimately check. Write tests for each known failure scenario before deleting old code.

### Risk 2: Circuit breaker thresholds too aggressive or lenient
**Impact:** Too aggressive = normal transients trigger degraded mode. Too lenient = hammers failing API.
**Mitigation:** Conservative defaults (5 failures in 60s to open, 30s half-open probe). Configurable via env vars. Log all state transitions.

### Risk 3: Structured logging breaks log parsers
**Impact:** Existing grep-based log analysis and watchdog health checks break
**Mitigation:** JSON format still greppable. Update watchdog to parse JSON. Run both formats in parallel during transition if needed.

## Race Conditions

### Race 1: Health check starts worker while job is being completed
**Location:** `agent/job_queue.py` — health check vs `_complete_job`
**Trigger:** Health check sees no worker for a chat_id at the exact moment the worker is exiting after completing its last job
**Data prerequisite:** Job must be in terminal state before worker exits
**State prerequisite:** `_active_workers` entry must be removed atomically with worker exit
**Mitigation:** Health check uses a minimum-age threshold — only recovers jobs pending/running longer than N seconds. Fresh jobs are not touched.

### Race 2: Circuit state read during transition
**Location:** `bridge/resilience.py` — CircuitBreaker state check vs state update
**Trigger:** Concurrent coroutines check circuit while another records a failure
**Mitigation:** `asyncio.Lock` for state transitions. Single-value reads are GIL-atomic.

## No-Gos (Out of Scope)

- HTTP health check endpoint
- Distributed circuit breaker state
- Multi-LLM provider failover
- Popoto ORM changes (work around KeyField limitations)
- Redis connection pooling (existing per-operation try/except is adequate)
- Telegram reconnection logic (Telethon handles this internally)
- OpenTelemetry adoption (premature — structured logging is the right step now)

## Update System

No update system changes required. No new dependencies, no new config files. New modules (`bridge/resilience.py`, `bridge/health.py`) are picked up by git pull. Deleted functions reduce code surface.

## Agent Integration

**Auth failure alerts:** When Claude Code OAuth or Telegram session auth fails, send a Telegram alert to the stakeholder (these require manual re-login). All other dependency failures handled silently by circuit breakers. PM error routing (intelligent reschedule/retry/cancel) deferred to a follow-up issue.

**Job status CLI:** Add `python -m agent.job_queue --status` as a diagnostic tool. No MCP exposure needed — this is for human operators, not the agent.

No other agent integration needed. The resilience module operates below the agent layer.

## Documentation

- [ ] Create `docs/features/bridge-resilience.md` describing: circuit breaker pattern, unified recovery loop, degraded mode, startup retry, health reporting, structured logging
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/bridge-self-healing.md` to reference resilience module and health status
- [ ] Update `docs/features/session-watchdog.md` to reflect removed recovery functions
- [ ] Update `docs/features/job-health-monitor.md` to reflect expanded health check scope

## Success Criteria

- [ ] Recovery pipeline simplified: six mechanisms collapsed into one. Net lines removed > lines added.
- [ ] Bridge survives 60s simulated Telegram API outage without crash-looping
- [ ] Bridge survives 60s simulated Anthropic API outage: queues messages, acknowledges, replays
- [ ] Startup retries cover all connection error types with exponential backoff + jitter
- [ ] CircuitBreaker class reusable: used by at least 2 dependencies
- [ ] Stall retry `project_key` vs `chat_id` bug eliminated (no more `_ensure_worker(project_key)`)
- [ ] Pending jobs with a live worker on same chat are NOT treated as stalls
- [ ] CancelledError caught explicitly in worker loop — no silent deaths
- [ ] Reflections log single warning (not traceback) for missing prerequisites
- [ ] Structured JSON logging with correlation_id on job lifecycle events
- [ ] Job status CLI prints human-readable table of active/pending jobs
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (recovery-refactor)**
  - Name: recovery-builder
  - Role: Delete six recovery mechanisms, expand _job_health_check to cover pending jobs, fix CancelledError handling
  - Agent Type: async-specialist
  - Resume: true

- **Builder (resilience-core)**
  - Name: resilience-builder
  - Role: Implement CircuitBreaker class, health module, startup retry, SDK circuit breaker, degraded mode
  - Agent Type: builder
  - Resume: true

- **Builder (observability)**
  - Name: observability-builder
  - Role: Structured JSON logging, SDK heartbeat, job status CLI, correlation ID threading
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: resilience-validator
  - Role: Verify all components under simulated failures, confirm net code deletion
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs, update existing docs for removed functions
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Recovery pipeline refactoring (net deletion)
- **Task ID**: build-recovery-refactor
- **Depends On**: none
- **Validates**: tests/unit/test_stall_detection.py (update), tests/unit/test_pending_recovery.py (delete)
- **Assigned To**: recovery-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Delete from `monitoring/session_watchdog.py`: `_recover_stalled_pending()`, `_kill_stalled_worker()`, `_enqueue_stall_retry()`, and the pending stall path in `check_stalled_sessions()`
- Delete from `agent/job_queue.py`: `_recover_orphaned_jobs()`, `_reset_running_jobs()`
- Expand `_job_health_check()` to scan both `status="running"` and `status="pending"` jobs
- Add startup mode: `_job_health_check(startup=True)` runs synchronously at bridge startup before event loop, resets ALL running jobs to pending unconditionally (all running jobs at startup are orphaned). Periodic mode (default) applies threshold check.
- For each job: check `_active_workers.get(job.chat_id)` — if no live worker and job exceeded threshold, call `_ensure_worker(job.chat_id)`
- Fix `_worker_loop` to catch `asyncio.CancelledError` explicitly — log and complete job
- Fix `sdk_client.query()` to catch `CancelledError` — log and re-raise
- Update/delete affected tests
- Verify: count lines removed vs added — must be net negative

### 2. Build CircuitBreaker class and health module
- **Task ID**: build-resilience-core
- **Depends On**: none
- **Validates**: tests/unit/test_circuit_breaker.py (create)
- **Assigned To**: resilience-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/resilience.py` with CircuitBreaker: states (closed/open/half-open), configurable failure threshold, backoff schedule, half-open probe, on_open/on_close callbacks, asyncio.Lock
- Create `bridge/health.py` with DependencyHealth: register circuit breakers, expose summary dict
- Write unit tests

### 3. Startup retry and SDK circuit breaker
- **Task ID**: build-startup-sdk
- **Depends On**: build-resilience-core
- **Validates**: tests/unit/test_bridge_logic.py (update), tests/unit/test_sdk_client.py (update)
- **Assigned To**: resilience-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace SQLite-only retry in `telegram_bridge.py` with general connection retry: all exceptions from `client.start()`, exponential backoff with jitter, max 8 attempts
- Create Anthropic circuit breaker in `sdk_client.py` (threshold=5, backoff=[30,60,120,240,480])
- Wrap `query()`: check circuit before calling SDK, record success/failure after
- Implement degraded mode in message handler: when Anthropic circuit open, acknowledge on Telegram ("Processing delayed — will respond when service recovers"), leave job as pending in queue. Health check will start worker when circuit closes.

### 4. Reflections pre-flight checks
- **Task ID**: build-preflight
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_preflight.py (create)
- **Assigned To**: resilience-builder
- **Agent Type**: builder
- **Parallel**: true
- Create pre-flight validation utility for reflections tasks
- Apply to `scripts/reflections.py`: wrap each task, catch failures as single-line warnings

### 5. Observability improvements
- **Task ID**: build-observability
- **Depends On**: build-recovery-refactor
- **Validates**: tests/unit/test_structured_logging.py (create), tests/unit/test_job_status_cli.py (create)
- **Assigned To**: observability-builder
- **Agent Type**: builder
- **Parallel**: false
- Switch to JSON log formatter with `job_id`, `session_id`, `correlation_id`, `chat_id` fields
- Make `correlation_id` mandatory at message intake, thread through all function calls
- Expand `BackgroundTask._watchdog` to emit periodic heartbeat logs (every 60s) with subprocess liveness
- Add `python -m agent.job_queue --status` CLI that prints table of all jobs with session, chat, duration, worker status
- Update `monitoring/bridge_watchdog.py` to parse JSON logs instead of plain text
- Write tests for JSON log format and job status CLI output

### 6. Validate all components
- **Task ID**: validate-all
- **Depends On**: build-recovery-refactor, build-startup-sdk, build-preflight, build-observability
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify net lines removed > lines added (count with `git diff --stat`)
- Verify circuit breaker state transitions under simulated failures
- Verify degraded mode acknowledgment
- Verify startup retry covers non-SQLite errors
- Verify reflections pre-flight logs warnings not tracebacks
- Verify job status CLI output
- Verify CancelledError handling in worker loop

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/bridge-resilience.md`
- Update `docs/features/README.md` index
- Update `docs/features/bridge-self-healing.md`, `session-watchdog.md`, `job-health-monitor.md`

### 8. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- `pytest tests/ -x -q`
- `python -m ruff check .`
- `python -m ruff format --check .`
- All success criteria met including net code deletion

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resilience module | `python -c "from bridge.resilience import CircuitBreaker"` | exit code 0 |
| Health module | `python -c "from bridge.health import DependencyHealth"` | exit code 0 |
| Job status CLI | `python -m agent.job_queue --status` | exit code 0 |
| Net code deletion | `git diff --stat main | tail -1` | output contains "deletions" |
| No new dependencies | `git diff HEAD -- requirements.txt pyproject.toml` | exit code 0 |

## Critique Results

| Severity | Critic | Concern | Resolution |
|----------|--------|---------|------------|
| BLOCKER | Skeptic, Archaeologist | Plan claims "no delete-and-recreate" but Popoto KeyField requires it for status changes | FIXED: Clarified that orphaned job recovery retains delete-and-recreate (Popoto constraint). "No delete-and-recreate" refers to not destroying jobs with live workers. |
| CONCERN | Adversary, Operator | Dead letter replay on circuit close may duplicate messages; dead_letters stores outbound, not inbound | FIXED: Removed dead-letter replay. Degraded mode keeps jobs pending in queue; health check starts worker when circuit closes. |
| CONCERN | Skeptic, Operator | Task 5 (observability) has no test validation — "manual verification" unacceptable | FIXED: Added test files for structured logging and job status CLI. Added watchdog update subtask. |
| CONCERN | Skeptic | `_recover_interrupted_jobs` fold into health check startup mode underspecified | FIXED: Added explicit startup mode spec — runs synchronously before event loop, unconditional reset. |
| CONCERN | Simplifier, Operator | PM error routing vague, no implementation path, no task in step-by-step | FIXED: Descoped PM error routing to follow-up issue. Kept auth failure alerts only. |
| NIT | Simplifier | Reflections pre-flight loosely coupled to core problem | Kept — low effort, high value, no dependencies. Can be split if needed. |
| NIT | User | "async-specialist" agent type undefined | Kept — it is defined in the agent type list in the template. |

---

## Open Questions

None — all questions from comments have been incorporated into the plan. PM alert philosophy, auth failure alerts, and recovery architecture decisions are resolved.
