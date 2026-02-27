---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/209
---

# Bridge-AgentSession-SDK Connectivity Gaps

## Problem

The connective logic between the Telegram bridge, AgentSession (Redis), and the Claude Agent SDK has multiple gaps that prevent hook-based stage tracking (PR #205) from ever working. Every SDLC session in production has zero stage data, zero links, and null `task_list_id`.

**Current behavior:**
- `AgentSession.task_list_id` is always `None` because `_execute_job()` computes it but never persists it
- Post-tool-use hooks fire with Claude Code's internal UUID as `session_id`, which doesn't match any `AgentSession.session_id`
- The fallback lookup via `task_list_id` fails because `task_list_id` is `None` on every session
- `complete_transcript()` drops fields (including `task_list_id`) during status changes via delete-and-recreate
- Bridge creates a duplicate `AgentSession` in `start_transcript()` that conflicts with the one from `_push_job()`

**Desired outcome:**
- Hooks can resolve the correct `AgentSession` via `task_list_id` lookup
- Stage progress and links accumulate on the session and render in Telegram summaries
- `complete_transcript()` preserves all fields through status transitions
- Only one `AgentSession` exists per session_id at any given time

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are internal to the bridge, job queue, and session transcript modules.

## Solution

### Key Elements

- **Fix 1: Persist task_list_id** -- Write the computed `task_list_id` to the `AgentSession` during job execution so hooks can find the session
- **Fix 2: Preserve all fields on status change** -- Replace the hardcoded field subset in `complete_transcript()` with comprehensive field extraction
- **Fix 3: Eliminate dual session creation** -- Remove the `AgentSession.create()` from `start_transcript()` since `_push_job()` already creates one
- **Fix 4: Pass VALOR_SESSION_ID env var** -- Give hooks a direct path to find the session without relying on task_list_id matching
- **Fix 5: Integration tests** -- Test the full chain from session creation through hook lookup to summarizer rendering

### Flow

**Job enqueue** (`_push_job`) creates AgentSession with `status=pending`
  -> **Worker picks up** (`_pop_job`) transitions to `status=running`
  -> **Execution starts** (`_execute_job`) persists `task_list_id` + passes `VALOR_SESSION_ID` env var to SDK
  -> **Hook fires** (`post_tool_use.py`) resolves session via `VALOR_SESSION_ID` or `task_list_id`
  -> **Stage progress written** to AgentSession history
  -> **Job completes** (`complete_transcript`) transitions status preserving ALL fields
  -> **Summarizer renders** stage progress + links from session data

### Technical Approach

- **Fix 1** (`agent/job_queue.py:1038`): After finding `agent_session`, set `agent_session.task_list_id = task_list_id` and call `agent_session.save()`. This is a two-line fix.

- **Fix 2** (`bridge/session_transcript.py:244-269`): Replace the hardcoded `old_data` dict with a dynamic approach that copies ALL non-None fields from the old session. Use the model's field names from `AgentSession._fields` or equivalent introspection rather than a hardcoded list.

- **Fix 3** (`bridge/session_transcript.py:71`): Remove the `AgentSession.create()` call. Instead, look up the existing session (created by `_push_job`) and update its transcript-phase fields (`log_path`, `sender_name`, `branch_name`, etc.). If no session exists (defensive), create one.

- **Fix 4** (`agent/sdk_client.py`): Add `VALOR_SESSION_ID` to the env dict passed to Claude Code, set to the `AgentSession.session_id`. Update `tools/session_progress.py._find_session()` to also check this env var. Update `post_tool_use.py:update_stage_progress_for_skill()` to pass `VALOR_SESSION_ID` if available in the environment.

- **Fix 5**: Add integration tests with real Redis (using `redis_test_db` fixture) that verify the complete chain.

## Rabbit Holes

- **Refactoring Popoto KeyField mechanics** -- The delete-and-recreate pattern for status changes is a Popoto limitation. Don't try to fix Popoto; just make the recreate comprehensive.
- **Changing the hook input schema** -- Claude Code hooks receive a fixed schema. Don't try to add `task_list_id` to the hook input; use environment variables instead.
- **Unifying the session_id format** -- The bridge uses `tg_valor_-5051653062_XXXX` while Claude Code uses UUIDs. Don't try to make these match; use `VALOR_SESSION_ID` as the bridge.

## Risks

### Risk 1: Popoto field introspection may not be straightforward
**Impact:** Fix 2 might require manual field enumeration if Popoto doesn't expose a clean field list.
**Mitigation:** Fall back to a comprehensive hardcoded list that covers ALL model fields (not a subset). Add a test that compares the list against the model definition.

### Risk 2: Environment variable not reaching hooks
**Impact:** `VALOR_SESSION_ID` might not propagate through Claude Code's subprocess hierarchy.
**Mitigation:** The SDK already passes `CLAUDE_CODE_TASK_LIST_ID` this way successfully. Add a test that verifies the env var is set. Keep the `task_list_id` fallback as belt-and-suspenders.

## No-Gos (Out of Scope)

- Backfilling stage data on existing sessions -- this only fixes forward
- Changing the Popoto KeyField architecture -- work within its constraints
- Modifying Claude Code's hook input schema -- use env vars instead
- Refactoring the entire job queue lifecycle -- fix the specific gaps only
- Adding real-time progress push to Telegram (stage-by-stage live updates)

## Update System

No update system changes required -- all fixes are internal Python code changes with no new dependencies, config files, or migration steps.

## Agent Integration

No new agent integration required. The existing `tools/session_progress.py` CLI tool is already callable from hooks. The `post_tool_use.py` hook already calls it. The fixes make the existing plumbing actually work by ensuring:
1. `task_list_id` is non-None so the lookup succeeds
2. `VALOR_SESSION_ID` provides a direct lookup path
3. No new MCP servers or `.mcp.json` changes needed

## Documentation

- [ ] Update `docs/features/agent-session-model.md` to document the `VALOR_SESSION_ID` env var and the session lookup chain
- [ ] Add entry to `docs/features/README.md` index table if not already present

## Success Criteria

- [ ] `AgentSession.task_list_id` is non-None for sessions created by the job queue after execution starts
- [ ] `_find_session()` resolves sessions via `VALOR_SESSION_ID` env var
- [ ] `_find_session()` resolves sessions via `task_list_id` when `VALOR_SESSION_ID` is not set
- [ ] `complete_transcript()` preserves `task_list_id` and all other fields through status change
- [ ] Only one `AgentSession` exists per `session_id` at any time (no dual creation)
- [ ] Integration test: hook fires with Claude Code UUID -> session resolved via env var -> stage written
- [ ] Integration test: `complete_transcript()` status change preserves all fields
- [ ] Integration test: summarizer renders stage progress for a session with stage data
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (connectivity-fixes)**
  - Name: connectivity-builder
  - Role: Implement all five fixes across job_queue.py, session_transcript.py, sdk_client.py, session_progress.py, and post_tool_use.py
  - Agent Type: builder
  - Resume: true

- **Test Engineer (integration-tests)**
  - Name: integration-tester
  - Role: Write integration tests for the full session lookup chain, field preservation, and summarizer rendering
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final-check)**
  - Name: final-validator
  - Role: Verify all success criteria are met and no regressions introduced
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix task_list_id persistence in _execute_job
- **Task ID**: build-persist-task-list-id
- **Depends On**: none
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/job_queue.py`, after line 1038 where `agent_session` is found, add `agent_session.task_list_id = task_list_id` and `agent_session.save()`
- Ensure the save is inside the try/except block

### 2. Fix complete_transcript field preservation
- **Task ID**: build-fix-complete-transcript
- **Depends On**: none
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/session_transcript.py`, replace the hardcoded `old_data` dict with dynamic field extraction
- Use Popoto model introspection or a comprehensive field list covering ALL AgentSession fields
- Add `task_list_id` and all currently-dropped fields to the preserved set

### 3. Eliminate dual session creation in start_transcript
- **Task ID**: build-fix-dual-creation
- **Depends On**: none
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/session_transcript.py:start_transcript()`, replace `AgentSession.create()` with a lookup-and-update pattern
- Find existing session by `session_id`, update transcript fields (`log_path`, `sender_name`, `branch_name`, etc.)
- Keep a defensive create as fallback if no session exists (edge case: standalone transcript without job queue)

### 4. Add VALOR_SESSION_ID env var to SDK client
- **Task ID**: build-env-var
- **Depends On**: none
- **Assigned To**: connectivity-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py`, add `VALOR_SESSION_ID` to the env dict alongside `CLAUDE_CODE_TASK_LIST_ID`
- In `tools/session_progress.py:_find_session()`, add a lookup path: check `os.environ.get('VALOR_SESSION_ID')` and try that as session_id first
- In `.claude/hooks/post_tool_use.py:update_stage_progress_for_skill()`, pass `VALOR_SESSION_ID` from env to the session_progress CLI call if available

### 5. Write integration tests
- **Task ID**: build-integration-tests
- **Depends On**: build-persist-task-list-id, build-fix-complete-transcript, build-fix-dual-creation, build-env-var
- **Assigned To**: integration-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test: session created by `_push_job` has `task_list_id` set after `_execute_job` persists it
- Test: `_find_session()` resolves via `VALOR_SESSION_ID` env var
- Test: `_find_session()` resolves via `task_list_id` fallback
- Test: `complete_transcript()` preserves ALL fields (including `task_list_id`, `message_text`, `sender_id`, etc.)
- Test: `start_transcript()` updates existing session instead of creating a duplicate
- Test: summarizer renders stage progress for a session with non-empty history

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-integration-tests
- **Assigned To**: connectivity-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` with VALOR_SESSION_ID lookup chain
- Update `docs/features/README.md` index if needed

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/` and verify all pass
- Run `black . && ruff check .` and verify clean
- Verify all success criteria checkboxes
- Generate final report

## Validation Commands

- `pytest tests/test_session_progress.py -v` -- session lookup tests
- `pytest tests/test_agent_session_lifecycle.py -v` -- lifecycle and summarizer tests
- `pytest tests/ -v` -- full test suite
- `black --check .` -- code formatting
- `ruff check .` -- linting
- `grep -r "task_list_id = task_list_id" agent/job_queue.py` -- verify Fix 1 is present
- `grep -r "VALOR_SESSION_ID" agent/sdk_client.py` -- verify Fix 4 is present
