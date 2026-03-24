---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/501
last_comment_id:
---

# Async Job Queue with Branch-Session Mapping and Dependency Tracking

## Problem

The job queue processes work sequentially within a chat but lacks three capabilities that limit orchestration:

1. **No sibling dependency tracking** -- Jobs can only declare parent-child relationships (`parent_job_id`), not sibling dependencies. When ChatSession queues multiple tasks (e.g., "plan auth, then build auth"), ordering is implicit (FIFO) rather than explicit. If priorities shift, dependent jobs may execute before their prerequisites complete.

2. **No automatic branch-session mapping** -- DevSession agents must manually figure out which branch to work on. The system injects `SDLC_SLUG` and `SDLC_PR_BRANCH` as env vars, but there is no deterministic checkout logic. If a session pauses and another picks up the same slug, the new session might land on the wrong branch.

3. **No session state preservation across pause/resume** -- When a DevSession pauses (steering, dependency block), the current branch and commit state are not recorded. A new DevSession picking up the same slug has no way to restore to the exact state where work stopped.

**Current behavior:**
- Jobs execute in priority + FIFO order with no dependency awareness
- Branch checkout is manual and implicit via skill context
- Session pause loses branch/commit state

**Desired outcome:**
- Jobs can declare `depends_on` to block until dependencies complete
- DevSessions automatically land on the correct branch for their slug + stage
- Pause/resume records and restores branch + commit state

## Prior Art

- **Issue #258 / PR #362**: Job self-scheduling -- agent-initiated queue operations. Merged. Adds `scheduling_depth`, `scheduled_after`. Foundation for deferred execution.
- **Issue #332 / PR #357**: Checkpoint/resume -- stage-aware recovery. Merged. Records stage progress via `stage_states` but not branch/commit state.
- **PR #390**: Parent-child job hierarchy. Merged. Adds `parent_job_id`, `get_children()`, `_finalize_parent()`. Foundation for dependency tracking.
- **PR #466**: SDLC Redesign Phase 2 -- nudge loop, per-chat queue. Merged. Current architecture foundation.
- **PR #485**: Job scheduler kill command. Merged. Job lifecycle management.
- **Issue #493**: ruflo deep dive -- source research identifying this work item.

## Data Flow

1. **Entry point**: ChatSession queues multiple jobs via `_push_job()`, specifying `depends_on` job IDs
2. **Queue filtering**: `_pop_job()` checks each pending job's `depends_on` list against completed jobs. Jobs with unmet dependencies are skipped.
3. **Branch resolution**: When `_execute_job()` starts a DevSession, `resolve_branch_for_stage()` maps slug + stage to the correct branch (main for PLAN, `session/{slug}` for BUILD/TEST/REVIEW)
4. **Worktree setup**: For `session/{slug}` branches, `get_or_create_worktree()` ensures the worktree exists and sets the working directory
5. **Pause checkpoint**: When a job pauses (steering, dependency), `checkpoint_branch_state()` records current branch + HEAD commit SHA on the AgentSession
6. **Resume restore**: When a job resumes, `restore_branch_state()` verifies the branch + commit match, checking out if needed
7. **Completion cascade**: When Job A completes, `_pop_job()` re-evaluates pending jobs -- Job B (depends_on A) becomes eligible

## Architectural Impact

- **New dependencies**: None -- uses existing Popoto ORM and git subprocess calls
- **Interface changes**: `_push_job()` gains `depends_on` parameter; `_pop_job()` gains dependency filtering; new helper functions for branch resolution
- **Coupling**: Moderate increase -- job_queue.py gains awareness of branch/worktree state via new helper module. Kept modular by isolating branch resolution into a separate function.
- **Data ownership**: AgentSession gains `depends_on` (ListField), `commit_sha` (Field) fields. Branch resolution logic owned by new functions in `agent/job_queue.py`.
- **Reversibility**: Medium -- new fields can be made nullable and ignored; `_pop_job()` dependency check is a simple filter that can be removed

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (scope alignment on Phase 2 branch mapping rules, dependency failure semantics)
- Review rounds: 2+ (core queue changes require careful review)

## Prerequisites

No prerequisites -- this work has no external dependencies. All foundational work (parent-child hierarchy, worktree manager, pipeline graph) is already merged.

## Solution

### Key Elements

- **Job dependency graph**: `depends_on` field on AgentSession + dependency checking in `_pop_job()`
- **Branch resolver**: Deterministic function mapping (slug, stage) to correct branch name and working directory
- **State checkpoint/restore**: Record and restore branch + commit SHA on job pause/resume
- **PM queue management**: ChatSession can reorder, cancel, and inspect job queue with dependency visibility

### Flow

**ChatSession queues work** → `_push_job(depends_on=[job_a_id])` → **Worker loop** → `_pop_job()` checks dependencies → **Skip blocked jobs** → **Pick eligible job** → `resolve_branch_for_stage()` → **DevSession starts on correct branch** → **Work** → **Pause** → `checkpoint_branch_state()` → **Resume** → `restore_branch_state()` → **Complete** → **Dependent jobs become eligible**

### Technical Approach

#### Phase 1: Job Dependencies

- Add `depends_on` as a `ListField` on AgentSession (list of job_ids, nullable)
- In `_pop_job()`, after filtering by `scheduled_after`, filter out jobs whose `depends_on` contains any job_id that is not in a terminal state (`completed` or `failed`)
- Add `dependency_status` helper to check if all dependencies are met
- Failed dependency handling: when a depended-on job fails, mark dependent jobs as `blocked` and notify PM via the parent ChatSession
- Add `reorder_job()` function for PM to change priority of pending jobs
- Add `cancel_job()` function for PM to cancel pending jobs without affecting running ones

#### Phase 2: Branch-Session Mapping

- Add `resolve_branch_for_stage(slug, stage)` function:
  - PLAN, ISSUE stages -> `main` branch (plans committed to main)
  - BUILD, TEST, PATCH stages -> `session/{slug}` branch in worktree
  - REVIEW, DOCS stages -> `session/{slug}` branch
  - Q&A / non-SDLC -> `main` branch
- Integrate into `_execute_job()`: before starting the agent, resolve and checkout the correct branch
- Use `get_or_create_worktree()` for stages that need worktree isolation

#### Phase 3: State Checkpoint/Restore

- Add `commit_sha` Field to AgentSession (nullable string)
- `checkpoint_branch_state(job)`: reads current branch + HEAD and stores on the session
- `restore_branch_state(job)`: on resume, verifies branch matches and commit exists, checks out if needed
- Integrate with existing `_complete_job()` and steering pause flows

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_pop_job()` dependency check: test that a malformed `depends_on` (non-existent job_id) does not crash the queue -- job should be treated as unblocked
- [ ] `resolve_branch_for_stage()`: test that an invalid slug or missing stage falls back to main branch with a warning log
- [ ] `restore_branch_state()`: test that a missing commit SHA or detached HEAD logs a warning and proceeds on current branch

### Empty/Invalid Input Handling
- [ ] `depends_on=[]` (empty list) should be treated as no dependencies
- [ ] `depends_on=None` should be treated as no dependencies
- [ ] `commit_sha=""` or `commit_sha=None` should skip restore

### Error State Rendering
- [ ] When dependency fails, PM receives a clear message about which job failed and which jobs are blocked
- [ ] Job status dashboard shows dependency graph with blocked/unblocked states

## Test Impact

- [ ] `tests/unit/test_job_queue_async.py::test_pop_job_priority` -- UPDATE: add dependency filtering assertions
- [ ] `tests/unit/test_job_queue_async.py::test_push_and_pop` -- UPDATE: verify depends_on field preserved
- [ ] `tests/unit/test_job_queue_async.py::test_complete_job` -- UPDATE: verify dependency cascade (dependent jobs become eligible)
- [ ] `tests/unit/test_worktree_manager.py` -- no changes expected (worktree API unchanged)
- [ ] `tests/unit/test_branch_manager.py` -- no changes expected (existing functions unchanged)

## Rabbit Holes

- **Graph cycle detection in dependencies** -- Dependencies are acyclic by construction (Job B depends on Job A which was created first). Building a full DAG validator is overkill for sequential queue processing. If cycles somehow occur, `_pop_job()` will simply never pick up the blocked jobs, and health checks will eventually flag them.
- **Distributed locking for concurrent dependency resolution** -- We process one job at a time per chat. No concurrent workers means no lock contention.
- **Automatic dependency inference from SDLC stages** -- Tempting to auto-generate "build depends on plan" dependencies, but this conflates pipeline stage ordering (handled by ChatSession orchestration) with explicit job dependencies. Keep them separate.

## Risks

### Risk 1: Dependency deadlock (circular or permanently blocked jobs)
**Impact:** Jobs stuck in pending forever, queue appears frozen
**Mitigation:** Health check (`_job_health_check`) already detects stuck pending jobs. Add specific check: if a pending job has `depends_on` pointing to a failed/deleted job, auto-unblock it and notify PM.

### Risk 2: Delete-and-recreate changes job_id, breaking depends_on references
**Impact:** When `_pop_job()` does delete-and-recreate for status change, the new job gets a new `job_id`. Any other job's `depends_on` list pointing to the old ID becomes stale.
**Mitigation:** After delete-and-recreate in `_pop_job()`, scan pending jobs for `depends_on` references to the old ID and update them to the new ID. Same pattern as `_transition_parent()` does for `parent_job_id`.

### Risk 3: Branch state divergence between checkpoint and restore
**Impact:** Resume lands on wrong commit, work conflicts
**Mitigation:** `restore_branch_state()` verifies commit exists before checkout. If commit is unreachable (e.g., force-pushed), log warning and proceed on latest branch HEAD.

## Race Conditions

### Race 1: Dependency resolution during concurrent child completions
**Location:** `_pop_job()` and `_complete_job()` in `agent/job_queue.py`
**Trigger:** Two child jobs completing nearly simultaneously, both triggering `_finalize_parent()` and dependency re-evaluation
**Data prerequisite:** Both children's terminal statuses must be committed to Redis before dependency check runs
**State prerequisite:** `_pop_job()` must see consistent completed statuses for all dependencies
**Mitigation:** Workers are per-chat and sequential. `_complete_job()` writes terminal status before `_pop_job()` runs for the next job. No concurrent access within a single chat's worker loop.

### Race 2: Branch state change between checkpoint and restore
**Location:** `checkpoint_branch_state()` and `restore_branch_state()`
**Trigger:** Another process (manual git, another worktree) pushes to the same branch between pause and resume
**Data prerequisite:** Commit SHA recorded at checkpoint must be reachable from the branch HEAD at restore time
**State prerequisite:** Branch must not have been force-pushed or rebased since checkpoint
**Mitigation:** `restore_branch_state()` checks if the recorded commit is an ancestor of current HEAD. If yes, proceed on HEAD (newer commits are fine). If not, warn and proceed on HEAD -- the PM can steer if needed.

## No-Gos (Out of Scope)

- Parallel DevSession execution on the same machine
- Automatic dependency inference from SDLC pipeline stages
- Full DAG visualization in Telegram messages
- Job queue persistence across Redis restarts (already handled by Popoto)
- Cross-chat job dependencies (dependencies are within a single chat's queue)

## Update System

No update system changes required -- this feature is purely internal to the bridge and agent components. No new dependencies, config files, or migration steps.

## Agent Integration

No new MCP server needed. The dependency tracking is internal to the job queue. ChatSession already orchestrates via `_push_job()` -- the new `depends_on` parameter is passed through the existing interface. Branch resolution happens automatically in `_execute_job()` without agent awareness.

- The agent does NOT need to call dependency APIs -- ChatSession sets `depends_on` when queuing jobs
- Branch resolution is transparent to the agent -- it starts in the correct working directory
- No changes to `.mcp.json` or `mcp_servers/`

## Documentation

### Feature Documentation
- [ ] Create `docs/features/job-dependency-tracking.md` describing dependency graph, branch mapping, and checkpoint/restore
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings on `resolve_branch_for_stage()`, `checkpoint_branch_state()`, `restore_branch_state()`
- [ ] Updated docstring on `_pop_job()` documenting dependency filtering
- [ ] Updated docstring on `_push_job()` documenting `depends_on` parameter

## Success Criteria

- [ ] Jobs can declare dependencies via `depends_on` and queue respects them
- [ ] `_pop_job()` skips jobs with unmet dependencies
- [ ] DevSessions automatically land on the correct branch for their slug + stage
- [ ] Session pause records branch + commit SHA; resume restores it
- [ ] PM can reorder and cancel pending jobs
- [ ] Failed dependency handling: PM notified, dependent jobs marked blocked
- [ ] No regression in single-job execution path (jobs without `depends_on` work as before)
- [ ] Health check detects and handles stuck dependency chains
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (queue-deps)**
  - Name: queue-builder
  - Role: Implement depends_on field, _pop_job dependency filtering, dependency health checks
  - Agent Type: builder
  - Resume: true

- **Builder (branch-mapping)**
  - Name: branch-builder
  - Role: Implement resolve_branch_for_stage(), integrate into _execute_job()
  - Agent Type: builder
  - Resume: true

- **Builder (checkpoint)**
  - Name: checkpoint-builder
  - Role: Implement commit_sha field, checkpoint/restore functions, steering integration
  - Agent Type: builder
  - Resume: true

- **Builder (pm-controls)**
  - Name: pm-controls-builder
  - Role: Implement reorder_job(), cancel_job(), dependency visibility for ChatSession
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all components work together end-to-end
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add depends_on field and dependency filtering
- **Task ID**: build-deps
- **Depends On**: none
- **Validates**: tests/unit/test_job_queue_async.py (update existing + new dependency tests)
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `depends_on = ListField(null=True)` to AgentSession model
- Add `depends_on` to `_JOB_FIELDS` list and `_push_job()` parameters
- In `_pop_job()`, filter eligible jobs: skip if any `depends_on` job_id is not in terminal state
- After delete-and-recreate in `_pop_job()`, update any `depends_on` references to old job_id -> new job_id
- Add `_dependency_health_check()` to detect and handle stuck dependency chains
- Write unit tests for dependency filtering, failed deps, empty deps

### 2. Implement branch-session mapping
- **Task ID**: build-branch-mapping
- **Depends On**: none
- **Validates**: tests/unit/test_job_queue_async.py (new branch resolution tests)
- **Assigned To**: branch-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `resolve_branch_for_stage(slug: str, stage: str) -> tuple[str, bool]` returning (branch_name, needs_worktree)
- Integrate into `_execute_job()`: resolve branch before starting agent, set working_dir to worktree if needed
- Handle edge cases: no slug (Q&A), no stage (non-SDLC), invalid slug
- Write unit tests for stage-to-branch mapping

### 3. Implement state checkpoint/restore
- **Task ID**: build-checkpoint
- **Depends On**: build-branch-mapping
- **Validates**: tests/unit/test_job_queue_async.py (new checkpoint tests)
- **Assigned To**: checkpoint-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `commit_sha = Field(null=True)` to AgentSession model
- Add `commit_sha` to `_JOB_FIELDS` list
- Implement `checkpoint_branch_state(job)`: read current branch + HEAD commit, store on session
- Implement `restore_branch_state(job)`: verify and checkout recorded state
- Integrate checkpoint into steering pause flow and `_complete_job()`
- Integrate restore into `_execute_job()` for resumed jobs
- Write unit tests for checkpoint/restore flows

### 4. Implement PM queue management
- **Task ID**: build-pm-controls
- **Depends On**: build-deps
- **Validates**: tests/unit/test_job_queue_async.py (new queue management tests)
- **Assigned To**: pm-controls-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `reorder_job(job_id, new_priority)` -- changes priority of a pending job
- Add `cancel_job(job_id)` -- cancels a pending job, handles dependency cascade
- Add `get_queue_status(chat_id)` -- returns full queue state with dependency graph
- Wire into ChatSession orchestration
- Write unit tests for reorder, cancel, status functions

### 5. Integration validation
- **Task ID**: validate-integration
- **Depends On**: build-deps, build-branch-mapping, build-checkpoint, build-pm-controls
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify dependency chain: A -> B -> C executes in order
- Verify branch mapping: PLAN job runs on main, BUILD job runs in worktree
- Verify checkpoint/restore: pause and resume lands on correct branch
- Verify PM controls: reorder and cancel work correctly
- Verify no regression: single jobs without depends_on work as before

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/job-dependency-tracking.md`
- Add entry to `docs/features/README.md` index table
- Update inline docstrings

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify lint and format pass
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Dependency field exists | `grep -c 'depends_on' models/agent_session.py` | output > 0 |
| Branch resolver exists | `grep -c 'resolve_branch_for_stage' agent/job_queue.py` | output > 0 |
| Checkpoint function exists | `grep -c 'checkpoint_branch_state' agent/job_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Failed dependency semantics**: When Job A fails, should dependent Job B be auto-cancelled, auto-failed, or held in a `blocked` state for PM decision? The plan proposes `blocked` + PM notification, but auto-cancellation would be simpler.
2. **Cross-chat dependencies**: Should jobs in different Telegram chats be able to depend on each other? The current scope says no, but multi-project orchestration might need this eventually.
3. **Dependency on delete-and-recreate ID stability**: The delete-and-recreate pattern means job_ids change on status transitions. The plan proposes scanning and updating `depends_on` references, but an alternative is using `session_id` (stable) as the dependency key instead of `job_id`. Which is preferred?
