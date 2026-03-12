---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/359
---

# Job Hierarchy: Parent-Child Job Decomposition

## Problem

Large SDLC jobs run as monolithic sessions. A complex feature build (multi-file refactor, large feature) runs as one long session that is harder to recover from if it stalls, harder to observe progress on, and cannot be partially re-run.

**Current behavior:**
An agent working on a complex issue runs everything in a single session. If the session stalls at step 3 of 5, the entire job must be retried from scratch. Progress is opaque -- the only visibility is the raw session log. The `schedule_job` tool (#258) can enqueue jobs, but there is no structural link between a parent job and the sub-jobs it spawns.

**Desired outcome:**
An agent mid-job can decompose work into smaller child jobs via `schedule_job`, each linked back to the parent via `parent_job_id`. The parent tracks aggregate progress. If child 3/5 fails, only that child needs re-running. `/queue-status` can show job trees, not just flat lists.

## Prior Art

- **Issue #258**: Job self-scheduling -- established `schedule_job` tool, `scheduled_after`, `scheduling_depth`, depth cap, rate limits. Foundation for this work. Closed/complete.
- **PR #321**: Observer Agent -- stage-aware SDLC steering. Relevant because child job completion events need to flow through the Observer.
- **PR #284**: SDLC session tracking -- `classification_type` propagation. Child jobs should inherit classification from parent.
- **PR #337**: Correlation IDs -- end-to-end tracing. `correlation_id` should propagate from parent to children.

No prior attempts at job hierarchy exist. This is greenfield on top of mature infrastructure.

## Data Flow

### Parent decomposes work into children

1. **Parent job running**: Agent is executing a complex SDLC job (e.g., "implement feature X")
2. **Agent decides to decompose**: Calls `python -m tools.job_scheduler schedule --issue 113 --parent-job $JOB_ID` for each sub-task
3. **Tool creates child AgentSessions**: Each child has `parent_job_id` set to the parent's `job_id`
4. **Parent completes**: Parent session finishes with status `waiting_for_children` (a new status value)
5. **Worker processes children**: Sequential processing, each child runs independently
6. **Completion check**: After each child completes, worker checks if all siblings are done
7. **Parent finalized**: When all children complete, parent status transitions to `completed`. If any child failed, parent gets `failed`.

### Query path for job trees

1. **Request**: `/queue-status` or CLI queries for a parent job ID
2. **Lookup**: Query `AgentSession.query.filter(parent_job_id=job_id)` to find children
3. **Aggregation**: Count completed/failed/pending/running children, compute progress percentage
4. **Display**: Show tree structure with parent summary and per-child status

## Architectural Impact

- **New dependency**: None -- uses existing Popoto ORM and AgentSession model
- **Interface changes**: New `parent_job_id` field on AgentSession; new `waiting_for_children` status; new `--parent-job` flag on `tools/job_scheduler.py`
- **Coupling**: Low -- parent-child link is a simple foreign key pattern on a flat model. No new models.
- **Data ownership**: No change -- AgentSession remains the single source of truth
- **Reversibility**: High -- field can be ignored if unused; status value is additive

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation on parent completion semantics)
- Review rounds: 1

The job queue infrastructure is mature. This adds one field, one status value, query helpers, and CLI/display updates. The hardest part is the completion propagation logic.

## Prerequisites

No prerequisites -- this work uses only existing Redis, Popoto ORM, and internal Python APIs.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Queue storage |
| job_scheduler tool exists | `python -m tools.job_scheduler --help` | Foundation from #258 |

## Solution

### Key Elements

- **`parent_job_id` field**: New `KeyField` on AgentSession linking child to parent
- **`waiting_for_children` status**: Parent transitions here after spawning children, before final completion
- **Child spawning**: Extended `schedule_job` tool accepts `--parent-job` to set the link
- **Completion propagation**: After each child completes/fails, check if all siblings are done. If so, finalize parent.
- **Progress query**: Helper method on AgentSession to fetch children and compute aggregate status
- **Tree display**: `/queue-status` and CLI show hierarchical job trees

### Flow

**Agent running parent job** -> decides to decompose -> calls `schedule_job --parent-job $JOB_ID` per sub-task -> parent status becomes `waiting_for_children` -> worker processes children sequentially -> last child completes -> parent auto-transitions to `completed` (or `failed` if any child failed)

### Technical Approach

- **`parent_job_id` as KeyField**: Indexed for efficient queries. `AgentSession.query.filter(parent_job_id=X)` returns all children. `null` for non-child jobs (the common case).
- **Inheritance**: Child jobs inherit `correlation_id`, `project_key`, `classification_type`, `chat_id`, and `working_dir` from the parent. `scheduling_depth` is incremented. Priority is independent (children default to parent's priority but can be overridden).
- **Completion propagation in worker loop**: After `_execute_job()` finishes for a child job, check `parent_job_id`. If set, query siblings. If all siblings are terminal (completed/failed), transition parent to final status. This is a small addition to the existing worker loop -- not a new process.
- **No polling or events**: The worker already processes jobs sequentially. After completing a child, it can synchronously check sibling status and finalize the parent inline. No async coordination needed.
- **Parent does not block the worker**: When the parent spawns children and transitions to `waiting_for_children`, it releases the worker. The parent sits in Redis with that status until the last child completes and transitions it.
- **Partial re-run**: A failed child can be manually re-enqueued (via `schedule_job` with the same `parent_job_id`). The completion check re-evaluates all siblings.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_finalize_parent()`: test with parent already deleted from Redis -> log warning, no crash
- [ ] `_finalize_parent()`: test with parent already in `completed` status -> idempotent, no-op
- [ ] Child scheduling with invalid parent_job_id -> clear error message from tool

### Empty/Invalid Input Handling
- [ ] `--parent-job ""` (empty string) -> validation error
- [ ] Parent with zero children transitions to `waiting_for_children` -> immediately re-transitions to `completed` (edge case guard)
- [ ] Child of a `failed` parent gets scheduled -> allowed (supports retry)

### Error State Rendering
- [ ] `/queue-status` with job trees renders cleanly for 0, 1, and many children
- [ ] Failed child shows failure reason in parent's progress summary

## Rabbit Holes

- **DAG scheduler**: Ordering children by dependency ("run B after A") is a DAG engine. Out of scope. Children run in FIFO order like any other jobs. If ordering matters, the agent can set `scheduled_after` or priorities.
- **Recursive decomposition**: Children spawning grandchildren. The existing `scheduling_depth` cap (3) already prevents unbounded recursion. Allow it but don't build special UI for deep trees.
- **Real-time progress updates**: Pushing progress events to Telegram as each child completes. Not needed -- `/queue-status` provides on-demand visibility.
- **Parallel child execution**: Running children concurrently. The system is sequential-only by design (#258 No-Gos). Don't revisit.
- **Automatic decomposition**: Having the system automatically decide when to decompose. This is agent intelligence, not infrastructure. The agent decides when to call `schedule_job --parent-job`.

## Risks

### Risk 1: Orphaned children
**Impact:** Parent deleted or lost, children complete but no one finalizes the parent
**Mitigation:** Job health monitor already runs every 5 minutes. Add a check: if a child's parent_job_id points to a non-existent session, log a warning and clear the field. The child still completes normally.

### Risk 2: Parent stuck in waiting_for_children
**Impact:** All children completed but parent never transitions (bug in propagation logic)
**Mitigation:** Job health monitor checks: if a parent has status `waiting_for_children` and all children are terminal, force-transition to `completed`/`failed`. This is a self-healing backstop.

## Race Conditions

### Race 1: Two children complete near-simultaneously
**Location:** `_finalize_parent()` in `agent/job_queue.py`
**Trigger:** Worker completes child A, checks siblings, all done. But child B was in its final save() at the same moment.
**Data prerequisite:** Child B must have its terminal status persisted before the sibling check reads it
**State prerequisite:** Worker processes jobs sequentially (concurrency=1)
**Mitigation:** Not a real race. With concurrency=1, only one child executes at a time. The worker finishes child A, then starts child B. The sibling check after child B sees child A as completed. Sequential processing eliminates this class of race.

### Race 2: Parent finalized while a new child is being enqueued
**Location:** `enqueue_job()` vs `_finalize_parent()` in `agent/job_queue.py`
**Trigger:** Agent's last `schedule_job` call is in-flight while the worker finalizes the parent after earlier children complete
**Data prerequisite:** All children must be enqueued before parent transitions to `waiting_for_children`
**State prerequisite:** Parent must be in `waiting_for_children` status
**Mitigation:** The parent agent controls when it transitions to `waiting_for_children`. It should only do so after all `schedule_job` calls have returned. Since the agent is single-threaded, this is naturally ordered. Document this contract: "transition to waiting_for_children only after all children are enqueued."

## No-Gos (Out of Scope)

- No DAG/dependency ordering between children -- FIFO + priority is sufficient
- No parallel child execution -- sequential-only by design
- No real-time progress push to Telegram -- on-demand via `/queue-status`
- No automatic decomposition -- agent decides when to decompose
- No deep tree UI -- flat parent/children display only (depth cap at 3 handles the rest)
- No child cancellation cascade -- cancelling a parent does not auto-cancel children (can be added later)

## Update System

No update system changes required -- new field on AgentSession and tool updates propagate via `git pull`. No new system dependencies or config files.

## Agent Integration

- **Updated tool**: `tools/job_scheduler.py` gains `--parent-job` flag on the `schedule` subcommand
- **No MCP changes** -- agent calls the tool via Bash, which is already permitted
- **Bridge env vars**: `JOB_ID` must be available in the agent subprocess environment so the agent can reference its own job when spawning children. Verify `agent/sdk_client.py` injects this.
- **Integration test**: Agent calls `schedule_job --parent-job X`, child appears in Redis with correct `parent_job_id`, child completion triggers parent finalization

## Documentation

- [ ] Update `docs/features/job-scheduling.md` with parent-child job hierarchy section
- [ ] Update `docs/features/job-queue.md` with `waiting_for_children` status and `parent_job_id` field
- [ ] Add entry to `docs/features/README.md` index table for job hierarchy
- [ ] Code comments on completion propagation logic in `agent/job_queue.py`

## Success Criteria

- [ ] `parent_job_id` field exists on AgentSession as a KeyField
- [ ] `waiting_for_children` is a valid status in the session lifecycle
- [ ] `schedule_job --parent-job X` creates a child linked to parent X
- [ ] Child jobs inherit `correlation_id`, `project_key`, `classification_type`, `chat_id`, `working_dir` from parent
- [ ] After last child completes, parent auto-transitions to `completed`
- [ ] If any child fails, parent auto-transitions to `failed`
- [ ] Job health monitor detects orphaned children and stuck parents
- [ ] `/queue-status` shows job trees (parent with children listed beneath)
- [ ] `AgentSession.get_children()` returns all child sessions
- [ ] `AgentSession.get_completion_progress()` returns `(completed_count, total_count, failed_count)`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (model-and-queue)**
  - Name: queue-builder
  - Role: Add `parent_job_id` field, `waiting_for_children` status, completion propagation logic, health monitor updates
  - Agent Type: builder
  - Resume: true

- **Builder (tool-and-display)**
  - Name: tool-builder
  - Role: Update `tools/job_scheduler.py` with `--parent-job` flag, update `/queue-status` for tree display
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end: parent spawns children, children complete, parent finalized
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using: builder (2), validator (1)

## Step by Step Tasks

### 1. Add `parent_job_id` field and `waiting_for_children` status
- **Task ID**: build-parent-field
- **Depends On**: none
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `parent_job_id = KeyField(null=True)` to AgentSession in `models/agent_session.py`
- Add `parent_job_id` to `_JOB_FIELDS` list in `agent/job_queue.py`
- Update `_extract_job_fields()` to preserve `parent_job_id`
- Add `parent_job_id` parameter to `enqueue_job()` and `_push_job()`
- Document `waiting_for_children` as a valid status in the AgentSession docstring
- Add helper methods: `get_children()`, `get_completion_progress()`, `get_parent()`

### 2. Implement completion propagation
- **Task ID**: build-completion-propagation
- **Depends On**: build-parent-field
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_finalize_parent()` function in `agent/job_queue.py`
- After `_execute_job()` completes a child (check `parent_job_id`), call `_finalize_parent()`
- `_finalize_parent()`: query siblings via `parent_job_id`, if all terminal, transition parent to `completed` (or `failed` if any child failed)
- Use delete-and-recreate pattern for parent status transition (same as existing `_pop_job`)
- Handle edge cases: parent already deleted, parent already finalized (idempotent)

### 3. Update `tools/job_scheduler.py` with `--parent-job` flag
- **Task ID**: build-scheduler-update
- **Depends On**: build-parent-field
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--parent-job` argument to the `schedule` subcommand
- When `--parent-job` is provided, look up the parent AgentSession and inherit fields: `correlation_id`, `project_key`, `classification_type`, `chat_id`, `working_dir`
- Pass `parent_job_id` through to `enqueue_job()`
- Add `children` subcommand: list children of a given job ID with their statuses

### 4. Update job health monitor
- **Task ID**: build-health-monitor
- **Depends On**: build-completion-propagation
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Add orphan detection: children whose `parent_job_id` points to non-existent session
- Add stuck parent detection: `waiting_for_children` status with all children terminal
- Self-heal both cases (clear orphan field, finalize stuck parent)

### 5. Update `/queue-status` for tree display
- **Task ID**: build-tree-display
- **Depends On**: build-scheduler-update
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify queue status output to group children under their parent
- Show parent with progress bar: `[3/5 done, 1 failed]`
- Show each child with its status on an indented line

### 6. Verify env vars and integration
- **Task ID**: verify-integration
- **Depends On**: build-completion-propagation, build-scheduler-update
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `JOB_ID` is available in agent subprocess env (check `agent/sdk_client.py`)
- Add missing env var injection if needed
- Test: `schedule_job --parent-job X` creates child with correct `parent_job_id`
- Test: child completion triggers parent finalization
- Test: orphan and stuck parent health checks work

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: verify-integration
- **Assigned To**: tool-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/job-scheduling.md` with hierarchy section
- Update `docs/features/job-queue.md` with new status and field
- Add entry to `docs/features/README.md` index table

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-health-monitor, build-tree-display
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify documentation created

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| parent_job_id field exists | `grep -q 'parent_job_id' models/agent_session.py` | exit code 0 |
| Job fields updated | `grep -q 'parent_job_id' agent/job_queue.py` | exit code 0 |
| Scheduler updated | `python -m tools.job_scheduler schedule --help 2>&1 \| grep -q 'parent-job'` | exit code 0 |
| Feature docs | `test -f docs/features/job-scheduling.md` | exit code 0 |

---

## Open Questions

1. **Parent completion semantics**: Should the parent auto-transition to `failed` if *any* child fails, or only if *all* children fail? Current proposal: any child failure -> parent fails. This is the safer default (surfaces problems early). Should we offer a `--allow-partial-failure` flag on the parent?

2. **Child priority inheritance**: Should children default to the parent's priority, or always default to `normal`? Current proposal: inherit parent's priority. This means an `urgent` parent spawns `urgent` children, which may not always be desired.

3. **JOB_ID env var**: Is `JOB_ID` currently injected into the agent subprocess by `agent/sdk_client.py`? If not, this is a prerequisite. Need to verify before build.
