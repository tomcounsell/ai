---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-11
tracking: https://github.com/tomcounsell/ai/issues/360
last_comment_id:
---

# Health Check Reflection: Self-Healing Job Monitor

## Problem

The current job health check (`_job_health_check` in `agent/job_queue.py`) detects stuck jobs and recovers them by re-queuing as pending. But it operates mechanically -- it has no understanding of **why** a job stalled and no ability to fix the underlying cause.

**Current behavior:**
- Health check runs every 5 minutes, scans running jobs for dead workers or timeouts
- Dead/timed-out jobs get delete-and-recreated as pending with high priority
- The same bug that stalled the first job stalls the retry, creating an infinite stall-recover-stall loop
- No log inspection, no diagnosis, no self-repair

**Desired outcome:**
- Health check inspects logs and session history to classify failures
- Transient failures get retried (as today)
- Code bugs get diagnosed, an issue gets filed, SDLC runs autonomously to fix the bug, the bridge restarts with the fix, and the original job retries
- Infrastructure issues generate an alert to the human
- The system is demonstrably self-policing for scheduling and agent bugs

## Prior Art

- **Issue #127** (closed): Job queue stuck job detection and recovery. Implemented the current `_job_health_check()` with periodic scanning, timeout enforcement, and CLI tools (`--flush-stuck`, `--flush-job`). This established the mechanical recovery layer that this feature builds on.
- **Issue #216** (closed): Agent session stall detection and lifecycle diagnostics. Added structured lifecycle logging (`log_lifecycle_transition`), stall detection via `retry_count`/`last_stall_reason` fields on AgentSession, and health check integration. This provides the diagnostic data this feature needs to classify failures.
- **Issue #258** (closed): Job self-scheduling, batch dispatch, deferred execution. Added the `schedule_job` MCP tool and `scheduled_after` field. The self-scheduling capability is a prerequisite for health check reflection to spawn SDLC jobs for bugfixes.
- **Issue #361** (open): Reflections as first-class objects. Sibling issue that proposes a unified model for recurring non-issue work. The health check reflection is a specific instance of this pattern -- it's a recurring job that should be modeled as a first-class reflection.

## Data Flow

1. **Entry point**: `_job_health_loop()` runs every 5 minutes in the bridge event loop
2. **Detection**: `_job_health_check()` scans all `status="running"` AgentSessions, checks worker liveness and timeout
3. **Current recovery**: Dead/timed-out jobs get delete-and-recreated as `status="pending"`, `priority="high"`
4. **Proposed extension**: Before recovery, the health check reflection runs a diagnostic pipeline:
   a. Reads recent `logs/bridge.log` entries around the stall timestamp
   b. Reads the AgentSession's `history`, `last_stall_reason`, `retry_count`
   c. Calls Claude Haiku to classify failure: `transient | code_bug | infrastructure | unknown`
   d. For `code_bug`: creates a GitHub issue, enqueues an SDLC job at `high` priority
   e. For `infrastructure`: sends Telegram alert via existing watchdog mechanism
   f. For `transient`/`unknown`: retries as today (delete-and-recreate as pending)
5. **Fix deployment**: SDLC job runs, creates PR, auto-merges (auto_merge=true for valor project), bridge restarts via `data/restart-requested` flag
6. **Retry**: Original job is re-enqueued only after the fix is deployed (tracked via a `blocked_by_fix` field on the job or a simple deferred scheduling with `scheduled_after`)

## Architectural Impact

- **New dependencies**: None new. Uses existing Claude API (Haiku), `gh` CLI, `_push_job()`, and `enqueue_job()`.
- **Interface changes**: `_job_health_check()` gains a diagnostic sub-pipeline. New function `_diagnose_stalled_job(job, logs)` returns a classification enum.
- **Coupling**: Increases coupling between `agent/job_queue.py` and `scripts/reflections.py` patterns. The diagnostic LLM call pattern mirrors `run_llm_reflection()` in reflections.py.
- **Data ownership**: AgentSession gains new fields (`last_diagnosis`, `blocked_by_issue`) to track diagnostic state.
- **Reversibility**: High. The diagnostic pipeline is additive -- current mechanical recovery remains the fallback if diagnosis fails or is disabled.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on blast radius limits, auto-merge safety)
- Review rounds: 2+ (security review for autonomous code changes, correctness review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Claude API for LLM diagnosis |
| `gh` CLI authenticated | `gh auth status` | GitHub issue creation and SDLC |
| Job self-scheduling (#258) | `python -c "from agent.job_queue import enqueue_job; print('ok')"` | Spawn SDLC jobs from health check |
| Bridge auto-merge enabled | `python -c "import json; c=json.load(open('config/projects.json')); assert c['projects']['valor']['auto_merge']"` | Merge fixes without human |

## Solution

### Key Elements

- **Failure Classifier**: LLM-powered (Haiku) classifier that reads logs and session state to categorize failures as transient, code bug, infrastructure, or unknown
- **SDLC Spawner**: Enqueues a high-priority SDLC job when a code bug is diagnosed, targeting the specific file and error pattern
- **Fix-Then-Retry Gate**: Holds the original failed job in a deferred state until the fix PR is merged and bridge restarts
- **Dedup Guard**: Prevents filing duplicate issues for the same bug pattern (reuses `has_existing_github_work()` from reflections)
- **Blast Radius Limiter**: Restricts auto-fix to files in `agent/`, `bridge/`, and `models/` directories. Changes outside this scope get an issue filed but no auto-fix.

### Flow

**Stalled job detected** -> Classify failure (LLM) -> *If code bug:* Create issue + Spawn SDLC job -> *Fix merged + bridge restarted* -> Retry original job

**Stalled job detected** -> Classify failure (LLM) -> *If transient:* Re-enqueue as pending (existing behavior)

**Stalled job detected** -> Classify failure (LLM) -> *If infrastructure:* Alert human via Telegram

### Technical Approach

- Add `_diagnose_stalled_job()` function in `agent/job_queue.py` that accepts a stalled AgentSession and recent log lines, calls Haiku for classification
- Extend `_job_health_check()` to call diagnostic before recovery, gated by a `HEALTH_CHECK_DIAGNOSIS_ENABLED` env var (default: true)
- For code bugs: call `enqueue_job()` with `classification_type="sdlc"` and a message that invokes `/sdlc issue {new_issue_number}`
- Track pending fix with `scheduled_after` on the original job (set to now + 30 minutes to give SDLC time)
- After SDLC completes, the bridge restart clears `data/restart-requested`, and the deferred job becomes eligible on next `_pop_job()` cycle
- Add a `retry_count` ceiling (3) to prevent infinite diagnosis-retry loops

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_diagnose_stalled_job()` wraps the LLM call in try/except -- if diagnosis fails, fall back to mechanical recovery (current behavior)
- [ ] `_job_health_check()` already has a top-level try/except in `_job_health_loop()` -- verify it catches diagnosis errors

### Empty/Invalid Input Handling
- [ ] Test with empty log content (no bridge.log entries around stall time)
- [ ] Test with AgentSession missing `history` or `last_stall_reason` fields
- [ ] Test with Haiku returning malformed JSON or unrecognized category

### Error State Rendering
- [ ] When diagnosis returns `infrastructure`, verify the Telegram alert includes actionable diagnostics
- [ ] When diagnosis fails (LLM error), verify the user is not bothered -- silent fallback to mechanical recovery

## Rabbit Holes

- **Full root cause analysis with code context**: It's tempting to feed the LLM the actual source code of the failing function for root cause analysis. This balloons token usage and complexity. The SDLC job handles the actual fix -- diagnosis just needs enough signal to file a good issue.
- **Cross-project health check reflection**: The health check currently operates on the `valor` project. Extending to all projects in `projects.json` is a separate scope item -- each project has different auto_merge policies and SDLC capabilities.
- **Real-time process monitoring**: Monitoring CPU/memory of Claude Code subprocesses per job. The existing timeout mechanism is sufficient; process-level monitoring is a different problem.
- **Custom LLM fine-tuning for failure classification**: Haiku with a well-crafted prompt is sufficient. Fine-tuning is premature optimization.

## Risks

### Risk 1: Infinite diagnosis-retry loops
**Impact:** A code bug that the SDLC can't fix leads to: diagnose -> file issue -> SDLC fails -> retry original job -> stall -> diagnose -> file duplicate issue -> loop forever
**Mitigation:** Dedup guard (`has_existing_github_work()`) prevents duplicate issues. `retry_count` ceiling (3) on the original job prevents infinite retries. After 3 retries with the same diagnosis, the job is marked `failed` and the human is alerted.

### Risk 2: SDLC fix introduces new bugs
**Impact:** The autonomous fix for a scheduling bug could break something else, causing a cascade of new stalls.
**Mitigation:** Blast radius limiter restricts auto-fixes to `agent/`, `bridge/`, `models/` only. All fixes go through the standard SDLC pipeline (tests must pass). The bridge watchdog (level 4: auto-revert) catches crashes caused by bad commits. Confidence threshold: only auto-merge if the fix is < 50 changed lines and all tests pass.

### Risk 3: High API cost from frequent LLM diagnosis calls
**Impact:** If many jobs stall simultaneously, each triggers a Haiku call for diagnosis.
**Mitigation:** Rate limit diagnosis to 1 call per 5-minute health check cycle (already natural since health check runs every 5 min). Cache recent diagnoses by error pattern hash to avoid re-diagnosing the same failure.

## Race Conditions

### Race 1: Health check diagnoses while SDLC fix is in progress
**Location:** `agent/job_queue.py` `_job_health_check()`, concurrent with SDLC worker
**Trigger:** Health check runs, finds the same stalled job, diagnoses again while a fix SDLC job is already running
**Data prerequisite:** The diagnosis result (issue URL) must be recorded on the stalled job before the next health check cycle
**State prerequisite:** The SDLC job must be visible in the queue before the health check re-scans
**Mitigation:** After filing an issue and spawning SDLC, set `blocked_by_issue` on the stalled job's AgentSession. Health check skips jobs with `blocked_by_issue` set. Clear it after SDLC completes or after timeout.

### Race 2: Bridge restart during SDLC fix execution
**Location:** `data/restart-requested` flag, checked in `_worker_loop()` after each job
**Trigger:** The SDLC fix merges and triggers restart while the SDLC job itself is still cleaning up
**Data prerequisite:** SDLC job must complete its final commit and push before restart
**State prerequisite:** `restart-requested` flag must not be set until after the fix PR merges
**Mitigation:** The existing `_check_restart_flag()` already waits for no running jobs. The SDLC job's completion naturally precedes the restart. No additional mitigation needed.

## No-Gos (Out of Scope)

- **Fixing bugs outside `agent/`, `bridge/`, `models/`**: Auto-fix is scoped to core system code only. Issues in `tools/`, `scripts/`, `mcp_servers/` get an issue filed but not auto-fixed.
- **Multi-project health check reflection**: This feature targets the `valor` project only. Extending to other projects requires per-project SDLC capability validation.
- **Replacing the bridge watchdog**: The watchdog (level 1-5 escalation) handles bridge-level crashes. This feature handles job-level stalls. They are complementary, not replacements.
- **Fixing non-code issues**: If the diagnosis says "infrastructure" (e.g., Redis down, API rate limited), the system alerts but does not attempt repair.
- **Human-in-the-loop approval for fixes**: Auto-merge is already enabled for the valor project. Adding an approval gate would defeat the purpose of autonomous self-healing.

## Update System

No update system changes required. The health check reflection runs inside the bridge process, which is already managed by launchd. New fields on AgentSession are backward-compatible (default to None). The `HEALTH_CHECK_DIAGNOSIS_ENABLED` env var defaults to true but can be disabled on machines where autonomous fixes are not desired.

## Agent Integration

No new MCP server needed. The health check reflection runs inside the bridge's event loop, not as an agent tool. It uses existing infrastructure:
- `enqueue_job()` to spawn SDLC jobs (already exposed)
- `gh` CLI for issue creation (subprocess call, same pattern as reflections.py)
- Claude API (Haiku) for diagnosis (direct API call, same pattern as reflections.py)

The bridge (`bridge/telegram_bridge.py`) does not need modification -- `_job_health_loop()` already runs as an asyncio task in the bridge. The changes are entirely within `agent/job_queue.py`.

Integration test: verify that `_diagnose_stalled_job()` returns a valid classification and that `enqueue_job()` is called with correct parameters for SDLC jobs.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/health-check-reflection.md` describing the self-healing pipeline
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/bridge-self-healing.md` to reference the new diagnostic layer

### Inline Documentation
- [ ] Docstrings on `_diagnose_stalled_job()`, `_classify_failure()`, and modified `_job_health_check()`
- [ ] Update module docstring in `agent/job_queue.py` to mention diagnostic capability

## Success Criteria

- [ ] `_diagnose_stalled_job()` correctly classifies test fixtures: transient failure, code bug, infrastructure issue
- [ ] Code bug diagnosis creates a GitHub issue with the error pattern and affected file
- [ ] SDLC job is enqueued at `high` priority after code bug diagnosis
- [ ] Original stalled job is deferred (not retried immediately) when a fix is in progress
- [ ] Dedup guard prevents duplicate issues for the same bug pattern
- [ ] Retry ceiling (3) prevents infinite diagnosis loops
- [ ] Blast radius limiter restricts auto-fixes to `agent/`, `bridge/`, `models/`
- [ ] Feature is gated behind `HEALTH_CHECK_DIAGNOSIS_ENABLED` env var
- [ ] Graceful degradation: if LLM diagnosis fails, falls back to current mechanical recovery
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (health-check-diagnosis)**
  - Name: diagnosis-builder
  - Role: Implement failure classifier and diagnostic pipeline
  - Agent Type: builder
  - Resume: true

- **Builder (sdlc-spawner)**
  - Name: sdlc-spawner-builder
  - Role: Implement SDLC job spawning and fix-then-retry gate
  - Agent Type: builder
  - Resume: true

- **Validator (health-check)**
  - Name: health-check-validator
  - Role: Verify diagnostic pipeline produces correct classifications
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: health-check-tester
  - Role: Write tests for classification, dedup, retry ceiling, blast radius
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update existing self-healing docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement failure classifier
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: diagnosis-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_diagnose_stalled_job(job: AgentSession, log_lines: list[str]) -> str` to `agent/job_queue.py`
- Add `_extract_log_context(job: AgentSession) -> list[str]` to read relevant log lines around stall time
- Haiku prompt classifies failure as: `transient`, `code_bug`, `infrastructure`, `unknown`
- Return classification with confidence score and relevant log excerpt

### 2. Implement SDLC spawner and fix-then-retry gate
- **Task ID**: build-spawner
- **Depends On**: none
- **Assigned To**: sdlc-spawner-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_spawn_bugfix_sdlc(job: AgentSession, diagnosis: dict) -> str` that creates a GitHub issue and enqueues SDLC
- Add `blocked_by_issue` field to AgentSession model
- Modify `_job_health_check()` to call diagnostic pipeline before recovery
- Implement retry ceiling check (skip diagnosis if `retry_count >= 3`)
- Gate behind `HEALTH_CHECK_DIAGNOSIS_ENABLED` env var

### 3. Validate diagnostic pipeline
- **Task ID**: validate-diagnosis
- **Depends On**: build-classifier, build-spawner
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify classification returns valid enum values for test fixtures
- Verify dedup guard prevents duplicate issues
- Verify blast radius limiter rejects fixes outside scope
- Verify retry ceiling triggers after 3 attempts

### 4. Write tests
- **Task ID**: test-health-check
- **Depends On**: validate-diagnosis
- **Assigned To**: health-check-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for `_diagnose_stalled_job()` with mocked Haiku responses
- Unit tests for `_spawn_bugfix_sdlc()` with mocked `gh` CLI
- Integration test: stall a job, verify diagnosis runs, verify SDLC job is enqueued
- Edge cases: empty logs, missing session fields, LLM errors

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: test-health-check
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/health-check-reflection.md`
- Update `docs/features/README.md` index
- Update `docs/features/bridge-self-healing.md`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Classifier importable | `python -c "from agent.job_queue import _diagnose_stalled_job"` | exit code 0 |
| Feature doc exists | `test -f docs/features/health-check-reflection.md` | exit code 0 |

---

## Open Questions

1. **Blast radius scope**: Should auto-fix be limited to `agent/`, `bridge/`, `models/` only, or should `tools/` and `mcp_servers/` also be in scope? The current proposal restricts to core system code to minimize risk.

2. **Confidence threshold for auto-merge**: The issue suggests "only merge if tests pass AND the fix is < 20 lines". Should we use 20 lines or 50 lines as the threshold? Lower is safer but may reject valid multi-file fixes.

3. **Dependency on #361 (Reflections as first-class objects)**: Should this feature wait for a unified reflection model, or should it be implemented independently and migrated later? The issue lists #361 as a sibling, not a blocker.

4. **Diagnosis frequency**: Currently health check runs every 5 minutes. Should diagnosis run on every cycle, or only on the first detection of a stalled job (to avoid redundant LLM calls)?

5. **Fix deployment verification**: After the SDLC fix merges and bridge restarts, how does the system verify the fix actually resolved the issue? Should there be a post-fix validation step before retrying the original job?
