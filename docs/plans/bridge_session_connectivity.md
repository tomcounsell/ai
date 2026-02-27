---
status: Complete
type: bug
appetite: Medium
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/209
---

# Bridge ↔ AgentSession ↔ SDK Connectivity Gaps

## Problem

SDLC stage progress lines and link footers never appear in Telegram summaries. Every AgentSession in Redis has zero stage history, zero links, and `task_list_id=None`. The hook-based stage tracking from PR #205 has been non-functional since merge.

**Current behavior:**
1. Hooks fire `tools/session_progress.py` with Claude Code's internal UUID — no AgentSession match
2. `_find_session()` falls back to `task_list_id` match — all sessions have `None`
3. `complete_transcript()` drops fields (including `task_list_id`) during status change
4. Two AgentSession objects are created per message (one by transcript, one by job queue)

**Desired outcome:**
- Hooks can locate the correct AgentSession via `task_list_id`
- Stage progress and links render in every SDLC Telegram summary
- All AgentSession fields survive status transitions
- One AgentSession per message, not two

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Four targeted fixes to four files, plus integration tests that prove each fix works.

## Prerequisites

No prerequisites — all files already exist.

## Solution

### Key Elements

- **task_list_id persistence**: Write computed `task_list_id` to AgentSession during job execution
- **Field-safe status transitions**: Replace hardcoded field subset with comprehensive extraction
- **Single session creation**: Remove duplicate AgentSession.create from `start_transcript()`
- **Integration tests**: Prove the full chain works with realistic session IDs

### Technical Approach

#### Fix 1: Persist task_list_id on AgentSession (`agent/job_queue.py`)

In `_execute_job()`, after finding `agent_session` at line 1038, write the computed `task_list_id`:

```python
# After line 1040 (agent_session.branch_name = branch_name)
if agent_session:
    agent_session.task_list_id = task_list_id
    agent_session.save()
```

This is the critical fix — it enables `_find_session()` to resolve Claude Code's hook session ID to the correct AgentSession via the `task_list_id` fallback path.

#### Fix 2: complete_transcript field preservation (`bridge/session_transcript.py`)

Replace the hardcoded `old_data` dict (lines 245-267) with `_extract_job_fields()` or an equivalent that copies ALL model fields. The current code loses `task_list_id`, `message_text`, `priority`, `working_dir`, `workflow_id`, `sender_id`, `message_id`, `chat_title`, media fields, and thread context.

Approach: extract all field names from the model class dynamically:

```python
old_data = {}
for field_name in AgentSession._meta.fields:
    if field_name == "job_id":  # AutoKeyField — skip
        continue
    if field_name == "status":  # KeyField being changed — skip
        continue
    old_data[field_name] = getattr(s, field_name)
# Override specific fields for the transition
old_data["completed_at"] = time.time()
old_data["last_activity"] = time.time()
if summary:
    old_data["summary"] = summary
```

#### Fix 3: Eliminate dual session creation (`bridge/session_transcript.py`)

`start_transcript()` at line 71 creates an AgentSession with `status="active"`. `_push_job()` at line 255 creates another with `status="pending"`. The transcript one is orphaned.

Fix: Remove the `AgentSession.create()` call from `start_transcript()`. Keep only the transcript file operations there. The job queue is the single source of truth for AgentSession creation.

Update `start_transcript()` to find the existing session and set `log_path` on it instead of creating a new one:

```python
try:
    sessions = list(AgentSession.query.filter(session_id=session_id))
    if sessions:
        s = sessions[0]
        s.log_path = log_path
        s.started_at = time.time()
        s.last_activity = time.time()
        s.save()
    # If no session exists yet (race condition), the job queue will create one
except Exception as e:
    logger.warning(f"Failed to update AgentSession for {session_id}: {e}")
```

#### Fix 4: Integration tests

Write tests that exercise the real flow without mocking `_find_session()`:

1. **test_find_session_via_task_list_id**: Create an AgentSession with a Telegram-style ID and `task_list_id` set. Call `_find_session()` with a UUID (simulating hook behavior). Verify it resolves via `task_list_id`.

2. **test_complete_transcript_preserves_all_fields**: Create an AgentSession with all fields populated. Call `complete_transcript()`. Verify every field survives the status change.

3. **test_single_session_per_message**: Call the enqueue + start_transcript flow. Verify only one AgentSession exists for the session_id.

## Rabbit Holes

- Changing the hook protocol to pass custom session IDs — Claude Code's hook protocol is fixed, we work within it
- Adding a Redis index on `task_list_id` — the scan in `_find_session()` is fine for our session count
- Refactoring AgentSession to avoid delete-and-recreate entirely — Popoto KeyField limitation requires this pattern; just copy all fields
- Making hooks write directly to Redis — keep the CLI tool layer for testability

## Risks

### Risk 1: Removing start_transcript() AgentSession.create breaks transcript operations
**Impact:** Transcript operations that expect a session to exist may fail
**Mitigation:** The `append_turn` and `complete_transcript` functions already handle missing sessions gracefully (try/except). The job queue creates the session before `start_transcript` is called.

### Risk 2: _find_session() task_list_id scan is O(n) on all sessions
**Impact:** Slow lookups if session count grows large
**Mitigation:** Current session count is ~20. `cleanup_expired()` runs in daydream. Not a problem at our scale. Add an index later if needed.

## No-Gos (Out of Scope)

- Refactoring Popoto model to avoid delete-and-recreate for KeyField changes
- Adding Redis indexes on `task_list_id`
- Changing the Claude Code hook protocol
- Modifying how `tools/session_progress.py` writes data (it's correct, just can't find the session)

## Update System

No update system changes required — all changes are to bridge-internal code (`agent/job_queue.py`, `bridge/session_transcript.py`) and tests.

## Agent Integration

No agent integration required — this fixes internal plumbing between the bridge, Redis model, and SDK hooks. No new MCP tools or bridge imports needed.

## Documentation

- [ ] Update `docs/features/agent-session-model.md` to document `task_list_id` lifecycle (set during execution, preserved through status changes)
- [ ] Update `docs/features/sdlc-enforcement.md` to document that stage tracking now works via `task_list_id` resolution

## Success Criteria

- [ ] `_find_session()` resolves a Claude Code UUID to the correct AgentSession via `task_list_id`
- [ ] `complete_transcript()` preserves all fields through status change (including `task_list_id`, `message_text`, `working_dir`)
- [ ] Only one AgentSession exists per `session_id` after enqueue + transcript start
- [ ] Integration tests pass without mocking `_find_session()`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (connectivity)**
  - Name: connectivity-builder
  - Role: Apply all 4 fixes to job_queue.py and session_transcript.py
  - Agent Type: builder
  - Resume: true

- **Test Writer (integration)**
  - Name: integration-test-writer
  - Role: Write integration tests for session ID resolution and field preservation
  - Agent Type: test-writer
  - Resume: true

- **Validator (connectivity)**
  - Name: connectivity-validator
  - Role: Verify all fixes work end-to-end
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Write failing integration tests first
- **Task ID**: write-tests
- **Depends On**: none
- **Assigned To**: integration-test-writer
- **Agent Type**: test-writer
- **Parallel**: false
- Write `tests/test_bridge_session_connectivity.py` with 3 integration tests
- Test 1: `_find_session()` resolves via `task_list_id` (should fail — task_list_id is never set)
- Test 2: `complete_transcript()` preserves all fields (should fail — fields are dropped)
- Test 3: Single session per message after enqueue (should fail — dual creation)
- Verify all 3 tests fail as expected

### 2. Apply Fix 1 — Persist task_list_id
- **Task ID**: fix-task-list-id
- **Depends On**: write-tests
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_execute_job()`, write `task_list_id` to `agent_session` after line 1040
- Run test 1 — should now pass

### 3. Apply Fix 2 — Field-safe status transitions
- **Task ID**: fix-field-preservation
- **Depends On**: fix-task-list-id
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace hardcoded `old_data` dict in `complete_transcript()` with dynamic field extraction
- Run test 2 — should now pass

### 4. Apply Fix 3 — Eliminate dual creation
- **Task ID**: fix-dual-creation
- **Depends On**: fix-field-preservation
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `AgentSession.create()` from `start_transcript()`, replace with find-and-update
- Run test 3 — should now pass

### 5. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: fix-dual-creation
- **Assigned To**: connectivity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all 3 new integration tests pass
- Verify no regressions in existing tests

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: connectivity-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md`
- Update `docs/features/sdlc-enforcement.md`

## Validation Commands

- `pytest tests/test_bridge_session_connectivity.py -v` — all 3 integration tests pass
- `pytest tests/ -q --ignore=tests/e2e --ignore=tests/performance` — no regressions
- `python -c "from models.agent_session import AgentSession; [print(f.name) for f in AgentSession._meta.fields.values()]"` — verify field names for dynamic extraction
