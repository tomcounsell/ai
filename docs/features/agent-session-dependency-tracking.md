# Agent Session Dependency Tracking

Extends the async agent session queue with sibling dependency tracking, deterministic branch-session mapping, checkpoint/restore for session pause/resume, PM queue management controls, and structured session observability.

## Problem

The agent session queue previously processed work sequentially within a chat but lacked:

1. **Sibling dependency tracking** -- Jobs could only declare parent-child relationships, not sibling dependencies. Ordering was implicit (FIFO) rather than explicit.
2. **Automatic branch-session mapping** -- DevSession agents had to manually figure out which branch to work on.
3. **Session state preservation** -- When a DevSession paused, branch and commit state were not recorded.
4. **Session-level visibility** -- Health checks lacked session context, producing false positives.

## Solution

### Job Dependencies (Phase 1)

Each job gets a `stable_agent_session_id` (UUID, set once at creation, never changes on delete-and-recreate) and an optional `depends_on` list of `stable_agent_session_id` values.

- `_pop_agent_session()` filters out jobs whose dependencies are not all `completed`
- Only `completed` is considered "met" -- `failed` and `cancelled` block dependents
- Missing `stable_agent_session_id` (deleted from Redis) is treated as blocked (conservative)
- A `_dependency_health_check()` detects stuck chains and logs warnings

**Key functions:**
- `_dependencies_met(job)` -- Check if all deps are completed
- `dependency_status(job)` -- Return status of each dependency

### Branch-Session Mapping (Phase 2)

`resolve_branch_for_stage(slug, stage)` returns `(branch_name, needs_worktree)`:

| Stage | Branch | Worktree |
|-------|--------|----------|
| PLAN, ISSUE, CRITIQUE | `main` | No |
| BUILD, TEST, PATCH, REVIEW, DOCS | `session/{slug}` | Yes |
| MERGE | `session/{slug}` | No |
| Q&A / no slug | `main` | No |

Integrated into `_execute_agent_session()` -- the agent automatically starts on the correct branch.

### State Checkpoint/Restore (Phase 3)

- `checkpoint_branch_state(job)` -- Records current branch + HEAD commit SHA on the AgentSession
- `restore_branch_state(job)` -- Verifies and restores state on job resume
- Called automatically at job completion (audit trail) and job start (restore)
- Handles force-pushed branches and missing commits gracefully

### PM Queue Management (Phase 4)

Four functions for ChatSession orchestration:

- `reorder_agent_session(agent_session_id, new_priority)` -- Change priority of pending jobs
- `cancel_agent_session(agent_session_id)` -- Set explicit `cancelled` terminal status (blocks dependents)
- `retry_agent_session(stable_agent_session_id)` -- Re-queue failed/cancelled jobs
- `get_queue_status(chat_id)` -- Full queue state with dependency graph

### Session Observability (Phase 5)

**Activity stream**: Every tool call appends one JSONL line to `logs/sessions/{session_id}/activity.jsonl`. Fields: timestamp, tool name, key args, call count. Zero API calls, zero cost.

**Health check enrichment**: The judge prompt now includes `session_type` (chat/dev) and `message_text` from the AgentSession, plus extracted `gh` CLI commands. This eliminates false positives where PM research is misdiagnosed as "stuck."

**Subagent outcome summaries**: When a subagent completes, its outcome is extracted and logged alongside agent_type and agent_id. The outcome is included in the SDLC pipeline state returned to the PM.

## Data Model Changes

### AgentSession (new fields)

| Field | Type | Description |
|-------|------|-------------|
| `stable_agent_session_id` | `KeyField(null=True)` | UUID set once at creation, dependency reference key |
| `depends_on` | `ListField(null=True)` | List of stable_agent_session_id values this job waits for |
| `commit_sha` | `Field(null=True)` | HEAD commit SHA for checkpoint/restore |

### Terminal Statuses

`cancelled` added alongside `completed` and `failed`. Cancelled jobs block their dependents (same as failed).

## Files

| File | Changes |
|------|---------|
| `models/agent_session.py` | Added `stable_agent_session_id`, `depends_on`, `commit_sha` fields |
| `agent/agent_session_queue.py` | Dependencies, branch mapping, checkpoint/restore, PM controls |
| `agent/health_check.py` | Activity stream, session context enrichment |
| `agent/hooks/subagent_stop.py` | Outcome summary extraction |
| `tests/unit/test_job_dependencies.py` | 43 tests for deps, branch mapping, checkpoint, PM controls |
| `tests/unit/test_health_check.py` | Added tests for activity stream, session context, gh commands |
| `tests/unit/test_subagent_stop_hook.py` | Added tests for outcome summary extraction |

## Observability Clean Separation

| System | Scope | Role |
|--------|-------|------|
| Bridge heartbeat | Process health | "Am I alive, how many workers" |
| SDK heartbeat | Session liveness | "Is the session still running" |
| **Activity stream** (new) | Every tool call | Structured log: tool name, args, timestamp |
| Health check | Every 20 calls | AI verdict + gh extract + task-aware context |
| Subagent stop | On completion | Agent type + outcome summary |
