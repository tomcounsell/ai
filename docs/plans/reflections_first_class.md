---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/361
last_comment_id:
---

# Reflections as First-Class Objects

## Problem

The system has several recurring tasks implemented via completely different mechanisms:

- `scripts/reflections.py` -- a 14-step daily pipeline triggered by launchd (`com.valor.reflections.plist`) at 6 AM
- `_job_health_check()` -- an asyncio loop in `agent/job_queue.py` running every 5 minutes
- `_recover_orphaned_jobs()` -- called during startup in job queue
- `cleanup_stale_branches()` -- called ad-hoc from job queue
- Bridge watchdog (`monitoring/bridge_watchdog.py`) -- a separate launchd service (`com.valor.bridge-watchdog`) running every 60s

**Current behavior:**
Each recurring task has its own scheduling mechanism (launchd plist, asyncio loop, startup hook), its own error handling, and no shared observability. Adding a new reflection requires choosing and configuring a scheduling mechanism from scratch. There is no way to see "what reflections are running/due/overdue" from a single view.

**Desired outcome:**
A unified `Reflection` model and registry where all recurring non-issue work is declared in one place, scheduled by a single lightweight scheduler, and observable through `/queue-status`. Adding a new reflection is a one-line registry entry.

## Prior Art

- **PR #245**: "Refactor daydream to reflections" -- Renamed the daily daydream system to reflections. Established the current 14-step pipeline in `scripts/reflections.py`.
- **PR #136**: "Reactivate Daydream with self-reflection and institutional memory" -- Original implementation of the daily self-reflection system.
- **PR #259**: "Remove LessonLearned, add branch & plan cleanup step" -- Added branch/plan cleanup as step 14 of reflections.
- **Issue #258**: "Job self-scheduling" (CLOSED) -- Established the job queue infrastructure that reflections will integrate with. Dependency is satisfied.

## Data Flow

1. **Entry point**: Reflection scheduler (new asyncio loop in bridge worker) ticks every 60 seconds
2. **Registry check**: Reads `config/reflections.yaml` to find all declared reflections and their schedules
3. **Schedule evaluation**: For each reflection, checks if it's due (last_run + interval < now) by querying Redis state
4. **Enqueue**: If due, creates an `AgentSession` with `classification_type="reflection"` and enqueues it as a job
5. **Execution**: The existing job queue worker picks up the reflection job and executes it (either as a lightweight Python function or a full agent session)
6. **Completion**: Reflection result is logged; `last_run` is updated in Redis; observable via `/queue-status`

## Architectural Impact

- **New dependencies**: `croniter` or similar for cron expression parsing (or stick to interval-based scheduling to avoid the dependency)
- **Interface changes**: `AgentSession.classification_type` gains a new value `"reflection"`. `/queue-status` output format expands to include a reflections section.
- **Coupling**: Increases coupling between reflections and the job queue, but this is intentional -- the job queue is the right execution layer. Decreases coupling with launchd (external scheduler dependency reduced).
- **Data ownership**: Reflection schedule state moves from launchd plists + in-process asyncio loops to a centralized Redis-backed registry.
- **Reversibility**: Medium -- the old mechanisms (launchd plists, asyncio loops) can coexist during migration. Full rollback requires re-enabling the old scheduling mechanisms.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on which reflections to migrate first)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work has no external dependencies. Issue #258 (job self-scheduling) is already closed.

## Solution

### Key Elements

- **Reflection registry** (`config/reflections.yaml`): Declares all reflections with name, schedule, priority, and execution type
- **Reflection model**: Redis-backed state tracking (last_run, next_due, run_count, last_status) via Popoto
- **Reflection scheduler**: Lightweight asyncio loop that checks schedules and enqueues due reflections as jobs
- **Execution adapters**: Two modes -- `function` (direct Python callable, lightweight) and `agent` (full Claude session, for intelligence-requiring tasks)
- **Observability**: `/queue-status` shows reflections separately from issue-driven work

### Flow

**Scheduler tick** → Check registry → Find due reflections → Enqueue as `AgentSession(classification_type="reflection")` → Worker executes → Update last_run in Redis → Observable in `/queue-status`

### Technical Approach

- Keep the registry declarative (YAML) so adding a reflection is a config change, not a code change
- Use simple interval-based scheduling (seconds/minutes/hours) rather than full cron expressions to avoid `croniter` dependency
- Reflections that are still running when their next schedule fires should be skipped (not queued again)
- Health check reflection retains `high` priority; all others default to `low`
- The existing `scripts/reflections.py` 14-step pipeline becomes a single `agent`-type reflection called `daily-maintenance` that runs once daily
- Bridge watchdog remains as a separate launchd service (it must run externally to detect bridge crashes -- it cannot run inside the process it monitors)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Scheduler loop must catch and log exceptions per-reflection without crashing the loop
- [ ] Function-type reflections must have try/except with logging (no silent swallowing)
- [ ] Test that a failing reflection does not block other reflections from being scheduled

### Empty/Invalid Input Handling
- [ ] Registry with empty/missing fields should log warnings and skip invalid entries
- [ ] Reflection function returning None/empty should be treated as success (cleanup tasks may have nothing to do)

### Error State Rendering
- [ ] `/queue-status` must show failed reflections with their error message
- [ ] Reflection failures should be visible in bridge logs

## Rabbit Holes

- **Full cron expression support**: Tempting but unnecessary. Simple intervals (every N seconds/minutes/hours, daily at H:M) cover all current use cases. Cron adds parsing complexity and a dependency.
- **Migrating bridge watchdog into the reflection system**: The watchdog MUST run as an external process -- it monitors the bridge. Moving it inside the bridge defeats its purpose. Keep it as launchd.
- **Reflection chains/dependencies**: Some reflections logically follow others (e.g., report after scan), but building a dependency DAG is overkill. Sequential steps within a single reflection (like the current 14-step pipeline) handle this already.
- **Distributed scheduling**: This is a single-machine system. No need for distributed locks or consensus.

## Risks

### Risk 1: Migration disruption
**Impact:** Existing scheduled tasks stop running during the migration window.
**Mitigation:** Run old and new mechanisms in parallel during migration. Only remove launchd plists after the new scheduler proves reliable over 1 week.

### Risk 2: Job queue congestion
**Impact:** Low-priority reflections could pile up and delay human-initiated work.
**Mitigation:** All reflections default to `low` priority (except health check at `high`). The job queue already prioritizes by priority level. Additionally, skip-if-running logic prevents reflection pile-up.

### Risk 3: Health check latency increase
**Impact:** Moving health check from a tight asyncio loop (5 min) to the job queue could add latency if the queue is busy.
**Mitigation:** Health check keeps `high` priority. Alternatively, keep health check as a direct asyncio loop (not routed through the queue) since it needs guaranteed timing.

## Race Conditions

### Race 1: Reflection enqueued while still running
**Location:** Reflection scheduler loop
**Trigger:** Scheduler ticks, finds a reflection is due, but a previous run is still executing
**Data prerequisite:** `last_run` timestamp must be set before the scheduler evaluates the next schedule
**State prerequisite:** A running reflection job must be detectable via `AgentSession.query.filter(classification_type="reflection", reflection_name=X, status="running")`
**Mitigation:** Before enqueuing, check if a reflection with the same name is already `pending` or `running`. Skip if so.

### Race 2: Scheduler tick overlaps with worker completing a reflection
**Location:** Scheduler reading `last_run` while worker is updating it
**Trigger:** Scheduler reads `last_run` as stale right before worker updates it
**Data prerequisite:** `last_run` must be updated atomically after reflection completion
**State prerequisite:** Redis single-threaded execution model prevents true concurrent writes
**Mitigation:** Redis operations are atomic. The worst case is one extra tick where the reflection is skipped (already-running check catches it). No data corruption possible.

## No-Gos (Out of Scope)

- **Bridge watchdog migration**: Stays as external launchd service (must monitor from outside the process)
- **Distributed scheduling**: Single-machine only
- **Reflection chains/DAGs**: Use sequential steps within a single reflection instead
- **Full cron syntax**: Interval-based scheduling only
- **Reflection UI**: No web dashboard; `/queue-status` in Telegram is sufficient
- **Auto-retry with backoff**: Failed reflections just run on the next scheduled tick

## Update System

The update script needs a small change to install the new `config/reflections.yaml` file. No new Python dependencies are required (avoiding `croniter`). The reflections scheduler starts automatically as part of the bridge worker loop, so no new launchd plists are needed. Existing `com.valor.reflections.plist` can be removed after migration is verified stable (tracked as a follow-up task).

## Agent Integration

No agent integration required for the scheduler itself -- it runs inside the bridge worker loop. The `/queue-status` skill (if it exists as an MCP tool) should be updated to include reflections in its output. Function-type reflections are pure Python and don't need MCP exposure. Agent-type reflections use the existing Claude agent session infrastructure.

## Documentation

- [ ] Update `docs/features/reflections.md` to document the new unified model, registry format, and scheduler
- [ ] Add entry to `docs/features/README.md` index table for reflection scheduler
- [ ] Update `CLAUDE.md` quick commands section with reflection management commands

## Success Criteria

- [ ] `config/reflections.yaml` declares all system reflections in one place
- [ ] Reflection scheduler runs as an asyncio task in the bridge worker loop
- [ ] Health check runs via reflection scheduler at `high` priority every 5 minutes
- [ ] Daily maintenance (current `scripts/reflections.py`) runs via reflection scheduler
- [ ] Orphan recovery runs via reflection scheduler every 30 minutes
- [ ] Stale branch cleanup runs via reflection scheduler daily
- [ ] `/queue-status` shows reflections separately (due, running, last_run)
- [ ] Skip-if-running prevents duplicate reflection enqueuing
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (reflection-model)**
  - Name: model-builder
  - Role: Create Reflection model, registry loader, and scheduler loop
  - Agent Type: builder
  - Resume: true

- **Builder (migration)**
  - Name: migration-builder
  - Role: Migrate existing recurring tasks to reflection registry entries
  - Agent Type: builder
  - Resume: true

- **Validator (reflection-system)**
  - Name: reflection-validator
  - Role: Verify scheduler, registry, and observability
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: reflection-docs
  - Role: Update reflections docs and README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create Reflection Model and Registry
- **Task ID**: build-model
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/reflection.py` with Popoto model: `name`, `last_run`, `next_due`, `run_count`, `last_status`, `last_error`
- Create `config/reflections.yaml` with all reflection declarations (name, interval, priority, execution_type, callable/command)
- Create `agent/reflection_scheduler.py` with:
  - Registry loader (reads YAML, validates entries)
  - Schedule evaluator (interval-based: last_run + interval < now)
  - Enqueue logic (creates AgentSession with classification_type="reflection")
  - Skip-if-running guard
- Add `classification_type="reflection"` support to AgentSession

### 2. Integrate Scheduler into Worker Loop
- **Task ID**: build-integration
- **Depends On**: build-model
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Start reflection scheduler as asyncio task alongside health check loop in job queue startup
- Add execution adapters: `function` type (direct callable) and `agent` type (full session)
- Wire up `/queue-status` to show reflection state

### 3. Migrate Existing Recurring Tasks
- **Task ID**: build-migration
- **Depends On**: build-integration
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Convert `_job_health_check` from direct asyncio loop to reflection registry entry
- Convert `_recover_orphaned_jobs` to reflection registry entry
- Convert `cleanup_stale_branches` to reflection registry entry
- Convert `scripts/reflections.py` daily pipeline to a single `daily-maintenance` reflection entry
- Keep bridge watchdog as external launchd (document why in registry comments)

### 4. Validate Reflection System
- **Task ID**: validate-reflections
- **Depends On**: build-migration
- **Assigned To**: reflection-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all reflections appear in registry
- Verify scheduler enqueues due reflections correctly
- Verify skip-if-running prevents duplicates
- Verify `/queue-status` includes reflections section
- Run `pytest tests/ -x -q`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-reflections
- **Assigned To**: reflection-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` with unified model documentation
- Add reflection scheduler entry to `docs/features/README.md`
- Update `CLAUDE.md` quick commands

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: reflection-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Registry valid | `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"` | exit code 0 |
| Model importable | `python -c "from models.reflection import Reflection"` | exit code 0 |
| Scheduler importable | `python -c "from agent.reflection_scheduler import ReflectionScheduler"` | exit code 0 |

---

## Open Questions

1. **Health check scheduling strategy**: Should the health check remain as a direct asyncio loop for guaranteed timing, or go through the job queue like other reflections? Going through the queue adds latency when the queue is busy, but keeps the architecture uniform. Recommendation: keep as direct asyncio loop but register it in the reflections registry for observability.

2. **Registry format**: YAML (`config/reflections.yaml`) vs Python (`config/reflections.py` with dataclass definitions)? YAML is more declarative and easier to edit; Python allows type checking and default validation. Recommendation: YAML with a validation step on load.

3. **Daily maintenance granularity**: Should the current 14-step `scripts/reflections.py` become one `daily-maintenance` reflection, or should each step become its own reflection? One reflection preserves the existing checkpoint/resume behavior. Separate reflections allow independent scheduling but require reworking the checkpoint system. Recommendation: one reflection initially, split later if needed.

4. **Issue #258 integration depth**: The closed #258 issue mentioned `schedule_job` MCP tool and `scheduled_after` field. How much of that infrastructure is already built vs. needs to be created for this feature? Need to audit what landed from #258 before scoping the build.
