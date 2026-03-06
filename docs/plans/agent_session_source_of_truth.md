---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/tomcounsell/ai/issues/285
---

# AgentSession as Single Source of Truth

## Problem

When auto-continue fires, `_enqueue_continuation()` creates a **new** AgentSession record via `enqueue_job()` → `AgentSession.async_create()`. The old record — containing `classification_type`, `[stage]` history entries, and link URLs — is orphaned. The new record starts blank.

Additionally, the `send_to_chat` closure captures the `agent_session` object once at job start. During execution, `session_progress.py` writes `[stage]` entries to Redis on a different object. The closure never sees these writes, so `is_sdlc_job()` evaluates stale data.

PR #284 patched this by passing `classification_type` as a parameter through `_enqueue_continuation`. Tom's architectural direction: **stop passing variables around. The AgentSession IS the source of truth. All systems reference it by session_id and read from Redis.**

**Current behavior:**
- Auto-continue creates duplicate AgentSession records for the same `session_id`
- Metadata (classification, history, links) must be manually propagated as function parameters
- `send_to_chat` reads stale in-memory session instead of fresh Redis data
- `AgentSession.query.filter(session_id=...)` may return the wrong record when duplicates exist

**Desired outcome:**
- Exactly one AgentSession record per `session_id` at any time
- Auto-continue reuses the existing record (reset status to pending, update message_text)
- `send_to_chat` re-reads from Redis before making routing decisions
- No metadata propagation needed — everything lives on the canonical session

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Session reuse in `_enqueue_continuation`**: Look up existing AgentSession by `session_id`, reset to pending, update `message_text` and `auto_continue_count`
- **Fresh session reads in `send_to_chat`**: Re-read AgentSession from Redis before evaluating `is_sdlc_job()`, `has_remaining_stages()`, `has_failed_stage()`
- **Remove parameter propagation**: Stop passing `classification_type` through `_enqueue_continuation` — it's already on the session

### Flow

**Agent completes turn** → `send_to_chat` re-reads session from Redis → evaluates `is_sdlc_job()` on fresh data → auto-continue decision → `_enqueue_continuation` reuses existing session (status → pending, new message_text) → worker picks up → reads same session with all history intact

### Technical Approach

#### 1. `_enqueue_continuation`: Reuse existing session

Replace `enqueue_job()` call (which creates new record) with direct session update:

```python
async def _enqueue_continuation(job, branch_name, task_list_id, auto_continue_count, ...):
    # Look up the existing session — it has all the state
    sessions = list(AgentSession.query.filter(session_id=job.session_id))
    if not sessions:
        logger.error(f"No session found for {job.session_id}")
        return

    session = sessions[0]

    # Popoto KeyField index workaround: delete-and-recreate
    fields = _extract_job_fields(session)
    await session.async_delete()
    fields["status"] = "pending"
    fields["message_text"] = coaching_message
    fields["auto_continue_count"] = auto_continue_count
    fields["priority"] = "high"
    await AgentSession.async_create(**fields)
```

This preserves `classification_type`, `history`, `issue_url`, `plan_url`, `pr_url`, `context_summary`, and `expectations` — everything that was on the original session.

**Critical Popoto constraint**: `_pop_job` already uses delete-and-recreate for status transitions (line 346-350) because Popoto's `KeyField.on_save()` only ADDs to the new index set but never REMOVEs from the old one. We must follow the same pattern.

#### 2. `send_to_chat`: Re-read session from Redis

The `agent_session` captured by the closure at job start (line 1065) goes stale. Before the routing decision block, re-read:

```python
# Re-read session from Redis for fresh stage data
if agent_session and agent_session.session_id:
    fresh = list(AgentSession.query.filter(session_id=agent_session.session_id))
    if fresh:
        agent_session = fresh[0]
```

Place this before line 1144 (`_is_sdlc = False`).

#### 3. Clean up parameter propagation

Remove `classification_type=job.classification_type` from the `_enqueue_continuation` → `enqueue_job` call (PR #284 line 1012). It's no longer needed since the session is reused.

## Race Conditions

### Race 1: Concurrent session reads during delete-and-recreate

**Location:** `_enqueue_continuation` and `_pop_job`
**Trigger:** If `send_to_chat` re-reads the session at the exact moment between `async_delete()` and `async_create()`, the session is temporarily absent.
**Data prerequisite:** The session record must exist in Redis when `send_to_chat` reads it.
**State prerequisite:** The delete-and-recreate must be atomic from the reader's perspective.
**Mitigation:** The window is sub-millisecond (two sequential Redis commands). `send_to_chat` only reads once before making its decision. If the session is temporarily missing, fall back to the in-memory `agent_session` (which is stale but non-null). This is the same pattern `_pop_job` already uses successfully.

### Race 2: `send_to_chat` reads session before `session_progress.py` writes

**Location:** `send_to_chat` closure, `tools/session_progress.py`
**Trigger:** Agent output arrives before the last `session_progress` write completes.
**Data prerequisite:** `[stage]` entries must be written to Redis before `send_to_chat` evaluates them.
**State prerequisite:** `session_progress.py` runs inside the agent (Claude Code subprocess), while `send_to_chat` runs in the bridge (parent process).
**Mitigation:** Acceptable. The worst case is that `send_to_chat` sees one fewer stage than expected, which triggers auto-continue (correct behavior — the stage will be written on the next turn). This is the current behavior and is not a regression.

## Rabbit Holes

- **Popoto ORM rewrite**: Tempting to fix the KeyField index corruption bug in Popoto itself. That's a separate project — use the delete-and-recreate workaround like `_pop_job` does.
- **Session merging**: Don't try to merge multiple AgentSession records into one. Prevent duplicates from being created in the first place.
- **Locking/transactions**: Redis doesn't have multi-key transactions that would help here. The sub-millisecond delete-recreate window is acceptable.

## Risks

### Risk 1: `_extract_job_fields` drops new fields
**Impact:** If `_extract_job_fields` doesn't include fields like `context_summary` or `expectations`, they'll be lost during delete-and-recreate.
**Mitigation:** Audit `_JOB_FIELDS` list to ensure all AgentSession fields are included. Add any missing ones.

### Risk 2: Existing duplicate records in Redis
**Impact:** Old orphaned records from before this fix may confuse `filter(session_id=...)` queries.
**Mitigation:** On startup cleanup, deduplicate sessions: for each `session_id` with multiple records, keep the one with the most history entries and delete the rest.

## No-Gos (Out of Scope)

- Fixing Popoto's KeyField index corruption (use existing workaround)
- Changing the `_pop_job` pattern (it works, keep it)
- Addressing the classification race in `telegram_bridge.py` (line 745, `asyncio.create_task` not awaited) — that's a separate issue
- Session revival across bridge restarts — that's existing functionality, not broken

## Update System

No update system changes required — this is a bridge-internal change.

## Agent Integration

No agent integration required — this modifies the job queue internals that are invisible to the agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-model.md` with single-source-of-truth architecture
- [ ] Update `docs/features/coaching-loop.md` to reflect session reuse instead of parameter propagation

### Inline Documentation
- [ ] Code comments on the delete-and-recreate pattern in `_enqueue_continuation`
- [ ] Updated docstring for `_enqueue_continuation` explaining session reuse

## Success Criteria

- [ ] Auto-continue reuses existing AgentSession (no new record created)
- [ ] `classification_type`, `history`, and links survive auto-continue
- [ ] `send_to_chat` reads fresh session data from Redis before routing
- [ ] `_enqueue_continuation` no longer passes `classification_type` as parameter
- [ ] Existing tests still pass
- [ ] New test: auto-continue preserves all session metadata (history, links, classification)
- [ ] New test: `send_to_chat` evaluates `is_sdlc_job()` on fresh Redis data
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-reuse)**
  - Name: session-builder
  - Role: Implement session reuse in `_enqueue_continuation` and fresh reads in `send_to_chat`
  - Agent Type: builder
  - Resume: true

- **Validator (session-reuse)**
  - Name: session-validator
  - Role: Verify no duplicate records created, metadata preserved
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: session-tester
  - Role: Write tests for session reuse and fresh reads
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: session-docs
  - Role: Update agent-session and coaching-loop docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement session reuse in `_enqueue_continuation`
- **Task ID**: build-session-reuse
- **Depends On**: none
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `enqueue_job()` call with direct session lookup + delete-and-recreate
- Audit `_JOB_FIELDS` to ensure `context_summary`, `expectations`, and all new fields are included
- Remove `classification_type=job.classification_type` parameter propagation
- Add fresh Redis read in `send_to_chat` before line 1144

### 2. Validate session reuse
- **Task ID**: validate-session-reuse
- **Depends On**: build-session-reuse
- **Assigned To**: session-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_enqueue_continuation` no longer calls `enqueue_job`
- Verify `send_to_chat` re-reads session before `is_sdlc_job()` check
- Verify `_JOB_FIELDS` includes all AgentSession fields

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-session-reuse
- **Assigned To**: session-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test: auto-continue preserves classification_type, history, links
- Test: `send_to_chat` uses fresh session data (mock Redis read)
- Test: no duplicate AgentSession records after auto-continue

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-session-reuse, build-tests
- **Assigned To**: session-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md`
- Update `docs/features/coaching-loop.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: session-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify lint passes

## Validation Commands

- `python -m pytest tests/test_enqueue_continuation.py tests/test_agent_session_lifecycle.py tests/test_stage_aware_auto_continue.py tests/test_auto_continue.py -x -v` — core session and auto-continue tests
- `python -m ruff check agent/job_queue.py models/agent_session.py` — lint changed files
- `python -m pytest tests/ -x --timeout=120` — full test suite
