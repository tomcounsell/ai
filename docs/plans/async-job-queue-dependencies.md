---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/501
last_comment_id: 4115465261
---

# Async Job Queue with Branch-Session Mapping, Dependency Tracking, and Session Observability

## Problem

The job queue processes work sequentially within a chat but lacks three capabilities that limit orchestration:

1. **No sibling dependency tracking** -- Jobs can only declare parent-child relationships (`parent_job_id`), not sibling dependencies. When ChatSession queues multiple tasks (e.g., "plan auth, then build auth"), ordering is implicit (FIFO) rather than explicit. If priorities shift, dependent jobs may execute before their prerequisites complete.

2. **No automatic branch-session mapping** -- DevSession agents must manually figure out which branch to work on. The system injects `SDLC_SLUG` and `SDLC_PR_BRANCH` as env vars, but there is no deterministic checkout logic. If a session pauses and another picks up the same slug, the new session might land on the wrong branch.

3. **No session state preservation across pause/resume** -- When a DevSession pauses (steering, dependency block), the current branch and commit state are not recorded. A new DevSession picking up the same slug has no way to restore to the exact state where work stopped.

4. **No session-level activity visibility** -- The bridge has five observability systems (bridge heartbeat, SDK heartbeat, health check, subagent stop hook, job health check), but none provides structured per-tool-call activity logging or task-aware health verdicts. A 20-minute, 180+ tool call session that creates a PR looks identical to a stuck session from the bridge logs -- just heartbeats saying "running Xs, communicated=False." The health check produces false positives because it lacks session type and task context.

**Current behavior:**
- Jobs execute in priority + FIFO order with no dependency awareness
- Branch checkout is manual and implicit via skill context
- Session pause loses branch/commit state
- Health check judge has no concept of session type or task context, producing frequent false positives
- No structured activity log -- tool calls are only visible in the JSONL transcript (heavy, not designed for streaming)

**Desired outcome:**
- Jobs can declare `depends_on` to block until dependencies complete
- DevSessions automatically land on the correct branch for their slug + stage
- Pause/resume records and restores branch + commit state
- Health check enriched with session_type and task description for accurate verdicts
- Lightweight per-tool-call activity stream for real-time session monitoring
- Subagent completion summaries logged for outcome visibility

## Prior Art

- **Issue #258 / PR #362**: Job self-scheduling -- agent-initiated queue operations. Merged. Adds `scheduling_depth`, `scheduled_after`. Foundation for deferred execution.
- **Issue #332 / PR #357**: Checkpoint/resume -- stage-aware recovery. Merged. Records stage progress via `stage_states` but not branch/commit state.
- **PR #390**: Parent-child job hierarchy. Merged. Adds `parent_job_id`, `get_children()`, `_finalize_parent()`. Foundation for dependency tracking.
- **PR #466**: SDLC Redesign Phase 2 -- nudge loop, per-chat queue. Merged. Current architecture foundation.
- **PR #485**: Job scheduler kill command. Merged. Job lifecycle management.
- **Issue #493**: ruflo deep dive -- source research identifying this work item.
- **Issue #501 comment (observability findings)**: Live session observed 3 SDLC jobs queued simultaneously. Job 105 waited 28+ minutes unnecessarily while Jobs 103/104 ran -- it had no dependency on them but FIFO ordering blocked it. This validates the dependency tracking need: with `depends_on`, independent jobs could be reordered by the PM. Also identified that the health check flagged sessions as UNHEALTHY 11 times across jobs 103/104, mostly false positives -- the judge lacks session_type and task context. Proposed activity stream, health check enrichment, and subagent summaries.

## Data Flow

1. **Entry point**: ChatSession queues multiple jobs via `_push_job()`, specifying `depends_on` stable_job_ids (UUIDs, immutable across delete-and-recreate)
2. **Queue filtering**: `_pop_job()` checks each pending job's `depends_on` list of stable_job_ids against their terminal status. Jobs with unmet dependencies are skipped.
3. **Branch resolution**: When `_execute_job()` starts a DevSession, `resolve_branch_for_stage()` maps slug + stage to the correct branch (main for PLAN, `session/{slug}` for BUILD/TEST/REVIEW)
4. **Worktree setup**: For `session/{slug}` branches, `get_or_create_worktree()` ensures the worktree exists and sets the working directory
5. **Pause checkpoint**: When a job pauses (steering, dependency), `checkpoint_branch_state()` records current branch + HEAD commit SHA on the AgentSession
6. **Resume restore**: When a job resumes, `restore_branch_state()` verifies the branch + commit match, checking out if needed
7. **Completion cascade**: When Job A completes, `_pop_job()` re-evaluates pending jobs -- Job B (depends_on A) becomes eligible
8. **Activity stream**: Every tool call in `watchdog_hook()` appends one JSONL line to `logs/sessions/{session_id}/activity.jsonl` (timestamp, tool name, key args). Zero API calls, zero cost -- pure file I/O. Designed as a feed that SubconsciousMemory can consume later.
9. **Health check enrichment**: When the health check fires (every 20 calls), the judge prompt includes `session_type` (chat/dev) and task description from the AgentSession, plus extracted `gh` CLI commands from tool call history. This eliminates false positives where PM research or targeted grepping is misdiagnosed as "stuck."
10. **Subagent outcome**: When `subagent_stop_hook` fires for a dev-session, it logs a brief outcome summary alongside agent_type and agent_id (extracted from the subagent's return value)

## Architectural Impact

- **New dependencies**: None -- uses existing Popoto ORM and git subprocess calls
- **Interface changes**: `_push_job()` gains `depends_on` parameter (list of stable_job_ids); `_pop_job()` gains dependency filtering; `retry_job()` for re-queuing failed children; new helper functions for branch resolution
- **Coupling**: Moderate increase -- job_queue.py gains awareness of branch/worktree state via new helper module. Kept modular by isolating branch resolution into a separate function.
- **Data ownership**: AgentSession gains `stable_job_id` (KeyField, indexed), `depends_on` (ListField), `commit_sha` (Field) fields. Branch resolution logic owned by new functions in `agent/job_queue.py`. Activity stream writes to `logs/sessions/` (filesystem, not Redis).
- **Reversibility**: Medium -- new fields can be made nullable and ignored; `_pop_job()` dependency check is a simple filter that can be removed; observability additions are purely additive (new log lines, enriched prompt, activity file) and can be removed without affecting core queue behavior

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

**ChatSession queues work** → `_push_job(depends_on=[stable_job_id_a])` → **Worker loop** → `_pop_job()` checks dependencies by stable_job_id → **Skip blocked jobs** → **Pick eligible job** → `resolve_branch_for_stage()` → **DevSession starts on correct branch** → **Work** → **Pause** → `checkpoint_branch_state()` → **Resume** → `restore_branch_state()` → **Complete** → **Dependent jobs become eligible** / **Fail** → **PM notified, decides: retry or cancel**

### Technical Approach

#### Phase 1: Job Dependencies

- Add `stable_job_id = KeyField(null=True)` to AgentSession — a UUID set once at creation via `uuid.uuid4().hex`, never changes on delete-and-recreate. `job_id` (AutoKeyField) changes on status transitions; `stable_job_id` does not. This is the dependency reference key. KeyField provides indexed lookup for dependency resolution. Nullable: pre-existing jobs in Redis will have `stable_job_id=None` and cannot be depended upon (which is fine since they predate the feature).
- Add `depends_on` as a `ListField` on AgentSession (list of `stable_job_id` values, nullable). Many-to-one: multiple jobs can depend on the same stable_job_id, and a single job can depend on multiple stable_job_ids.
- In `_pop_job()`, after filtering by `scheduled_after`, filter out jobs whose `depends_on` contains any `stable_job_id` where the dependency is not `completed`. Only `completed` is considered "met." Dependencies in `failed` or `cancelled` state (both terminal) block dependents and trigger PM notification — they are never silently unblocked. Missing `stable_job_id` (deleted from Redis) is treated as blocked (conservative — notify PM) to prevent silently unblocking dependents of cleaned-up jobs.
- Add `dependency_status` helper to check if all dependencies are met (looks up AgentSession by stable_job_id)
- Failed dependency handling: notify parent ChatSession (PM) with full visibility. PM decides: cancel, retry, or unblock. No auto-cancellation — parent has full decision-making power over child jobs.
- Add `retry_job(stable_job_id)` function for PM to re-queue a failed child job
- Add `reorder_job(job_id, new_priority)` function for PM to change priority of pending jobs
- Add `cancel_job(job_id)` function for PM to cancel pending jobs without affecting running ones. Sets status to `cancelled` — a new explicit terminal status. `cancelled` is added to all `terminal_statuses` sets throughout `job_queue.py` (currently `{"completed", "failed"}` becomes `{"completed", "failed", "cancelled"}`). In dependency checking, `cancelled` blocks dependents the same as `failed` — dependents are NOT silently unblocked. PM is notified and decides whether to cancel or unblock dependent jobs.

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

#### Phase 4: Session Observability

**4a. PostToolUse activity stream** (in `agent/health_check.py` `watchdog_hook()`)
- On every tool call, append one JSONL line to `logs/sessions/{session_id}/activity.jsonl`
- Fields: `timestamp`, `tool_name`, `key_args` (file path, command summary, pattern -- reuse existing `_summarize_input()`), `tool_call_count`
- Create session directory lazily on first write
- Zero API calls, zero cost -- just file I/O
- Design as a feed that SubconsciousMemory (Popoto recipe) can consume later

**4b. Health check enrichment** (in `agent/health_check.py`)
- Read `session_type` (chat/dev) and `message_text` (the original task request) from AgentSession model and inject into `JUDGE_PROMPT`. No new fields needed — `session_type` and `message_text` both already exist on the AgentSession model (`session_type` is a Field, `message_text` is a Field with max_length=MSG_MAX_CHARS).
- Extract `gh` CLI commands from the tool call activity (high-signal for PM sessions doing GitHub orchestration)
- Add session context preamble to the prompt: "This is a {session_type} session working on: {message_text[:200]}"
- Log extracted `gh` commands alongside the health verdict

**4c. Subagent completion summaries** (in `agent/hooks/subagent_stop.py`)
- Extract outcome summary from the subagent's return value (the `input_data` dict)
- Log a structured outcome line: agent_type, agent_id, outcome summary (truncated)
- Include outcome in the `reason` field returned to the PM alongside pipeline state

**Observability clean separation of concerns:**

| System | Scope | Observability Role |
|--------|-------|-------------------|
| Bridge heartbeat | Process health | "Am I alive, how many workers" |
| SDK heartbeat | Session liveness | "Is the session still running, has it communicated" |
| **Activity stream** (new) | Every tool call | Structured log: tool name, args, timestamp |
| Health check | Every 20 calls | AI verdict + gh command extract + task-aware context |
| Subagent stop | On completion | Agent type + outcome summary |

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

### Observability Edge Cases
- [ ] Activity stream: test that `logs/sessions/` directory is created lazily on first tool call
- [ ] Activity stream: test that JSONL append survives concurrent writes (file append is atomic on POSIX for small writes)
- [ ] Health check enrichment: test that missing `session_type` or `message_text` on AgentSession falls back gracefully (empty string, not crash)
- [ ] Subagent summary: test that missing or empty return value produces a sensible default summary

## Test Impact

- [ ] `tests/unit/test_job_queue_async.py::test_pop_job_priority` -- UPDATE: add dependency filtering assertions
- [ ] `tests/unit/test_job_queue_async.py::test_push_and_pop` -- UPDATE: verify depends_on field preserved
- [ ] `tests/unit/test_job_queue_async.py::test_complete_job` -- UPDATE: verify dependency cascade (dependent jobs become eligible)
- [ ] `tests/unit/test_worktree_manager.py` -- no changes expected (worktree API unchanged)
- [ ] `tests/unit/test_branch_manager.py` -- no changes expected (existing functions unchanged)
- [ ] `tests/unit/test_health_check.py` (if exists) -- UPDATE: verify enriched prompt includes session_type and message_text
- [ ] `tests/unit/test_hooks.py` -- CREATE: new test file for subagent_stop outcome summary

## Rabbit Holes

- **Graph cycle detection in dependencies** -- Dependencies are acyclic by construction (Job B depends on Job A which was created first). Building a full DAG validator is overkill for sequential queue processing. If cycles somehow occur, `_pop_job()` will simply never pick up the blocked jobs, and health checks will eventually flag them.
- **Distributed locking for concurrent dependency resolution** -- We process one job at a time per chat. No concurrent workers means no lock contention.
- **Automatic dependency inference from SDLC stages** -- Tempting to auto-generate "build depends on plan" dependencies, but this conflates pipeline stage ordering (handled by ChatSession orchestration) with explicit job dependencies. Keep them separate.

## Risks

### Risk 1: Dependency deadlock (circular or permanently blocked jobs)
**Impact:** Jobs stuck in pending forever, queue appears frozen
**Mitigation:** Health check (`_job_health_check`) already detects stuck pending jobs. Add specific check: if a pending job has `depends_on` pointing to a failed/deleted job, auto-unblock it and notify PM.

### Risk 2: Delete-and-recreate changes job_id — ~~breaking depends_on references~~
**Impact:** Eliminated. `depends_on` uses `stable_job_id` (a UUID set once at creation, preserved across delete-and-recreate) instead of `job_id`. The delete-and-recreate pattern in `_pop_job()` must copy `stable_job_id` to the new record. No reference scanning or updating needed.
**Residual risk:** If a record is deleted from Redis before dependents check it, the dependency lookup will find no match. Mitigation: treat missing `stable_job_id` as "blocked" (conservative — notify PM). This avoids silently unblocking dependents of cleaned-up jobs. The explicit `cancelled` terminal status ensures that intentionally cancelled jobs block their dependents with clear semantics rather than disappearing from Redis.

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
- SDK heartbeat changes (activity extraction belongs in the PostToolUse hook, not the SDK heartbeat timer which has no access to tool call history)
- SubconsciousMemory integration (activity stream is designed as a consumable feed, but the Popoto recipe integration is a separate issue)

## Update System

No update system changes required -- this feature is purely internal to the bridge and agent components. No new dependencies, config files, or migration steps.

## Agent Integration

No new MCP server needed. The dependency tracking is internal to the job queue. ChatSession already orchestrates via `_push_job()` -- the new `depends_on` parameter is passed through the existing interface. Branch resolution happens automatically in `_execute_job()` without agent awareness.

- The agent does NOT need to call dependency APIs -- ChatSession sets `depends_on` when queuing jobs
- Branch resolution is transparent to the agent -- it starts in the correct working directory
- No changes to `.mcp.json` or `mcp_servers/`

## Documentation

### Feature Documentation
- [ ] Create `docs/features/job-dependency-tracking.md` describing dependency graph, branch mapping, checkpoint/restore, and session observability
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
- [ ] PM can reorder, cancel, and retry child jobs
- [ ] Failed dependency handling: PM notified with full visibility, decides cancel/retry (no auto-cancellation)
- [ ] No regression in single-job execution path (jobs without `depends_on` work as before)
- [ ] Health check detects and handles stuck dependency chains
- [ ] Activity stream writes JSONL per tool call to `logs/sessions/{session_id}/activity.jsonl`
- [ ] Health check prompt includes session_type and task description, reducing false positives
- [ ] `gh` CLI commands extracted and logged alongside health verdicts
- [ ] Subagent stop hook logs outcome summary alongside agent_type and agent_id
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

- **Builder (observability)**
  - Name: observability-builder
  - Role: Implement activity stream, health check enrichment, subagent outcome summaries
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
- Add `stable_job_id = KeyField(null=True)` to AgentSession — UUID set once at creation via `uuid.uuid4().hex`, never changes. KeyField for indexed lookup.
- Add `depends_on = ListField(null=True)` to AgentSession model (stores `stable_job_id` values)
- Add both fields to `_JOB_FIELDS` list; ensure `stable_job_id` is copied in delete-and-recreate pattern
- Add `depends_on` parameter to `_push_job()`
- In `_pop_job()`, filter eligible jobs: skip if any `depends_on` stable_job_id is not in terminal state
- No reference scanning needed — stable_job_id is preserved across delete-and-recreate
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
- Add `cancel_job(job_id)` -- sets status to `cancelled` (a new explicit terminal status added to all `terminal_statuses` sets in `job_queue.py`). Dependents of a cancelled job are blocked and PM is notified, same as `failed` — never silently unblocked.
- Add `retry_job(stable_job_id)` -- re-queues a failed child job with same parameters
- Add `get_queue_status(chat_id)` -- returns full queue state with dependency graph and child statuses
- Wire into ChatSession orchestration: PM gets full visibility over child jobs (cancel, retry, inspect)
- Write unit tests for reorder, cancel, retry, status functions

### 5. Implement session observability
- **Task ID**: build-observability
- **Depends On**: none
- **Validates**: tests/unit/test_health_check.py (new + updated), tests/unit/test_hooks.py (new + updated)
- **Assigned To**: observability-builder
- **Agent Type**: builder
- **Parallel**: true (independent of phases 1-4)
- **5a. Activity stream** -- In `watchdog_hook()` in `agent/health_check.py`, on every tool call, append one JSONL line to `logs/sessions/{session_id}/activity.jsonl`. Fields: timestamp, tool_name, key_args (reuse `_summarize_input()`), tool_call_count. Create directory lazily. Zero API cost.
- **5b. Health check enrichment** -- In `agent/health_check.py`, read `session_type` and `message_text` from AgentSession model (both already exist on the model). Inject into `JUDGE_PROMPT` as context preamble. Extract `gh` CLI commands from tool call history and log alongside verdict.
- **5c. Subagent completion summaries** -- In `agent/hooks/subagent_stop.py`, extract outcome summary from `input_data` and log structured outcome line (agent_type, agent_id, summary). Include in returned `reason` field.
- Write tests for activity stream file creation, health check prompt enrichment, subagent summary extraction

### 6. Integration validation
- **Task ID**: validate-integration
- **Depends On**: build-deps, build-branch-mapping, build-checkpoint, build-pm-controls, build-observability
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify dependency chain: A -> B -> C executes in order
- Verify branch mapping: PLAN job runs on main, BUILD job runs in worktree
- Verify checkpoint/restore: pause and resume lands on correct branch
- Verify PM controls: reorder and cancel work correctly
- Verify no regression: single jobs without depends_on work as before
- Verify activity stream: tool calls produce JSONL in `logs/sessions/`
- Verify health check enrichment: judge prompt includes session_type and task context
- Verify subagent summaries: dev-session completion logs outcome

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/job-dependency-tracking.md`
- Add entry to `docs/features/README.md` index table
- Update inline docstrings

### 8. Final Validation
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
| stable_job_id field exists | `grep -c 'stable_job_id' models/agent_session.py` | output > 0 |
| Dependency field exists | `grep -c 'depends_on' models/agent_session.py` | output > 0 |
| Branch resolver exists | `grep -c 'resolve_branch_for_stage' agent/job_queue.py` | output > 0 |
| Checkpoint function exists | `grep -c 'checkpoint_branch_state' agent/job_queue.py` | output > 0 |
| Activity stream write | `grep -c 'activity.jsonl' agent/health_check.py` | output > 0 |
| Health check enrichment | `grep -c 'session_type' agent/health_check.py` | output > 0 |
| Subagent outcome summary | `grep -c 'outcome' agent/hooks/subagent_stop.py` | output > 0 |

## Critique Results

**Critique run**: 2026-03-24
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 8 total (2 blockers, 4 concerns, 2 nits)

### Blockers (RESOLVED)

#### 1. `task_description` field does not exist on AgentSession — RESOLVED
- **Severity**: BLOCKER
- **Critics**: Skeptic
- **Location**: Phase 4b (Health check enrichment), line 138-140
- **Finding**: The plan says to read `task_description` from AgentSession and inject into the judge prompt, but AgentSession has no `task_description` field. The plan never specifies adding this field to the model or to `_JOB_FIELDS`.
- **Suggestion**: Either add `task_description = Field(null=True)` to AgentSession (and to `_JOB_FIELDS`), or derive the description from an existing field like `message_text` or `context_summary`.
- **Resolution**: Plan updated to use `message_text` (already exists on AgentSession as `Field(max_length=MSG_MAX_CHARS)`) instead of the non-existent `task_description`. No new fields needed.

#### 2. `cancel_job()` semantics create unsafe optimistic unblocking — RESOLVED
- **Severity**: BLOCKER
- **Critics**: Adversary, Skeptic
- **Location**: Risk 2 (residual risk) + Task 4 (`cancel_job`)
- **Finding**: The plan says "treat missing stable_job_id as completed" (optimistic unblocking), and `cancel_job()` cancels pending jobs. But the plan defines no "cancelled" terminal status. If cancellation deletes the job from Redis, dependents will treat the missing dependency as completed and proceed -- potentially executing work whose prerequisite was intentionally cancelled. This directly contradicts the "PM decides" design principle.
- **Suggestion**: Define an explicit `cancelled` terminal status. In dependency checking, treat `cancelled` the same as `failed` (block dependents, notify PM) rather than optimistically unblocking.
- **Resolution**: Plan updated with explicit `cancelled` terminal status added to all `terminal_statuses` sets. Dependency checking treats `cancelled` same as `failed` (blocks dependents, notifies PM). Missing `stable_job_id` treated as blocked (conservative). No optimistic unblocking anywhere.

### Concerns

#### 3. `stable_job_id` lookup performance -- no KeyField index — RESOLVED
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Phase 1, `_pop_job()` dependency filtering
- **Finding**: `stable_job_id` is defined as `Field(null=True)`, not a `KeyField`. Dependency checking in `_pop_job()` must scan all chat jobs to find records matching each `stable_job_id` in a job's `depends_on` list. With many jobs this becomes O(N*M) per pop.
- **Suggestion**: Consider making `stable_job_id` a `KeyField` for indexed lookup, or document why scan performance is acceptable (e.g., jobs per chat are always small).
- **Resolution**: The plan already specifies `stable_job_id = KeyField(null=True)` in Phase 1 and Task 1. The critique misread the plan — KeyField is already specified for indexed lookup.

#### 4. `_transition_parent()` not mentioned in plan but needs `stable_job_id` and `depends_on` awareness
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Task 1 (build-deps)
- **Finding**: `_transition_parent()` (line 575 of job_queue.py) does its own delete-and-recreate for parent jobs and updates children's `parent_job_id`. If a parent has `stable_job_id` or `depends_on`, these must also be preserved. The plan mentions updating `_JOB_FIELDS` but doesn't call out `_transition_parent` specifically.
- **Suggestion**: Add explicit mention that `_transition_parent` must preserve `stable_job_id` via `_JOB_FIELDS` (which it already uses via `_extract_job_fields`). Confirm this is sufficient by tracing the code path.

#### 5. Activity stream has no rotation or size limits
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Phase 4a (Activity stream)
- **Finding**: Activity stream appends JSONL indefinitely to `logs/sessions/{session_id}/activity.jsonl`. Long-running or resumed sessions could accumulate large files. No cleanup, rotation, or size cap is specified.
- **Suggestion**: Add a max file size or max line count after which older entries are truncated, or document that session cleanup (already existing for transcripts) will handle these files.

#### 6. Existing jobs in Redis lack `stable_job_id` -- no migration plan
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Phase 1, AgentSession model changes
- **Finding**: Jobs already in Redis when this deploys will have `stable_job_id = None`. If any code path creates a job with `depends_on` referencing a pre-migration job, the lookup will fail. The plan doesn't address backward compatibility or migration.
- **Suggestion**: Add a note that `stable_job_id` is nullable and only set on new jobs. Dependency features only apply to newly created jobs. Pre-existing jobs cannot be depended upon (which is fine since they predate the feature).

### Nits

#### 7. `tests/unit/test_hooks.py` referenced but does not exist
- **Severity**: NIT
- **Critics**: Structural check
- **Location**: Test Impact section, Task 5
- **Finding**: The plan references `tests/unit/test_hooks.py` in both the Test Impact section and Task 5 validation, but this file does not exist. The plan hedges with "(if exists)" but the Test Impact section doesn't note this.
- **Suggestion**: Create the test file as part of Task 5, or update the Test Impact entry to say "CREATE" instead of "UPDATE".

#### 8. PM queue management functions expand scope beyond stated problem
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Task 4 (build-pm-controls)
- **Finding**: The problem statement identifies three pain points (no dependency tracking, no branch mapping, no state preservation) plus observability. Task 4 adds four new PM functions (`reorder_job`, `cancel_job`, `retry_job`, `get_queue_status`) that weren't in the problem statement. While useful, this is scope expansion.
- **Suggestion**: Acknowledge this as intentional scope expansion in the plan, or defer Task 4 to a follow-up issue if appetite is tight.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All four required sections present and non-empty |
| Task numbering | PASS | Tasks 1-8 sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` references point to valid task IDs, no cycles |
| File paths exist | WARN | 9 of 11 referenced source files exist; `tests/unit/test_hooks.py` and `docs/features/job-dependency-tracking.md` do not (latter is intentionally new) |
| Prerequisites met | PASS | No prerequisites declared |
| Cross-references | PASS | All referenced fields (`session_type`, `message_text`, `stable_job_id`, `depends_on`, `commit_sha`) either exist or are explicitly listed as new fields to add |

### Verdict

**READY TO BUILD** -- Both blockers resolved:
1. ~~Add `task_description` field to AgentSession or specify which existing field to use for health check enrichment~~ — RESOLVED: uses existing `message_text` field
2. ~~Define `cancelled` status semantics so `cancel_job()` doesn't silently unblock dependents~~ — RESOLVED: explicit `cancelled` terminal status, blocks dependents same as `failed`

---

## Open Questions

All resolved:

1. **Failed dependency semantics** — RESOLVED: Parent job gets full visibility and decision-making power. Parent can cancel and retry child jobs. No auto-cancellation — the PM (parent ChatSession) decides what to do with failed dependencies.
2. **Cross-chat dependencies** — RESOLVED: No cross-chat dependencies allowed. Cross-project dependencies are tracked via GitHub issues only (e.g., AI repo issue waiting on a new Popoto feature). The job queue is strictly within a single chat's scope.
3. **Dependency key** — RESOLVED: Use a new `stable_job_id` field (UUID, set once at creation, immutable) instead of `job_id` (changes on delete-and-recreate). `depends_on` stores a list of `stable_job_id` values. Many-to-one: multiple jobs can depend on the same stable_job_id, and a single job can depend on multiple stable_job_ids. Note: `session_id` was considered but rejected — it identifies the conversation thread, not the individual job (multiple jobs share one session_id).
