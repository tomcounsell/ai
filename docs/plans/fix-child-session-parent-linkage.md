---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/808
last_comment_id: null
---

# Fix Child Session Parent Linkage

## Problem

When a PM AgentSession spawns a child Dev/Teammate session via the Claude Agent SDK's Agent tool, the dashboard shows the child session with `parent_agent_session_id: null`. The parent→child hierarchy is never established, making it impossible to trace which PM session owns which child, or to aggregate child status back to the parent.

**Current behavior:**

Two separate `AgentSession` records are created for each child subprocess:

1. `pre_tool_use.py` fires in the **parent process** and calls `AgentSession.create_child()`, creating a `dev-{parent_id}` record with `parent_agent_session_id` set correctly. This record is never updated by the child.
2. The child Claude Code CLI starts, `user_prompt_submit.py` fires in the **child process** and calls `AgentSession.create_local()` — creating a second `local-{uuid}` record with `parent_agent_session_id=None`.
3. The child process updates the `local-*` record throughout its lifetime; the `dev-*` record sits orphaned.
4. Dashboard shows `local-*` sessions with no parent link.
5. `subagent_stop.py` queries `parent_agent_session_id=parent_session_id` to find children — it finds the `dev-*` record (correct parent) but NOT the `local-*` record (null parent), so completion tracking is also broken.

**Desired outcome:**

- One `AgentSession` record per child subprocess, with `parent_agent_session_id` correctly set.
- `python -m tools.valor_session list` shows correct parent→child hierarchy.
- Dashboard reflects child sessions under their parent PM session.

## Prior Art

- **Issue #597**: "Fix hook session ID resolution — hooks run in parent process but read subprocess env" — Established `session_registry.py` to map Claude Code UUIDs to bridge session IDs for **parent-process hooks**. This fixed hook→session resolution but did not address child subprocess session creation. The subprocess still has no way to know its parent.
- **Issue #638**: "Document and test parent-child session hook lifecycle" — Added integration tests for the `dev-*` record path. Tests pass but they test the orphaned `dev-*` path, not the actual `local-*` path the child uses.
- **Issue #757**: "AgentSession dual parent fields (parent_session_id vs parent_agent_session_id) are never synced" — Fixed the model to have a single canonical parent field. Pre-condition for this fix.
- **Issue #634**: "Generalize AgentSession parent-child model and add role field" — Established `create_child()` factory, which is what `pre_tool_use.py` calls. Still operates in parent process only.

## Data Flow

Current broken flow:

1. **PM process**: `sdk_client.py` calls `client.query()` → SDK spawns child Claude Code CLI subprocess
2. **PM process**: `pre_tool_use.py` fires on Agent tool call → creates `dev-{parent_id}` AgentSession (has parent link, but orphaned)
3. **Child subprocess**: Claude Code CLI starts → `user_prompt_submit.py` fires on first prompt
4. **Child subprocess**: `user_prompt_submit.py` calls `AgentSession.create_local()` with no parent ID → creates `local-{uuid}` AgentSession (parent_agent_session_id=None)
5. **Child subprocess**: All subsequent activity updates the `local-*` record
6. **PM process**: `subagent_stop.py` fires → queries for children by `parent_agent_session_id` → finds `dev-*` (not `local-*`) → marks `dev-*` completed but `local-*` stays orphaned

Fixed flow:

1. **PM process**: `sdk_client.py` resolves the parent's `agent_session_id` from the `AgentSession` record
2. **PM process**: `sdk_client.py._create_options()` injects `VALOR_PARENT_SESSION_ID={agent_session_id}` into child subprocess env
3. **Child subprocess**: `user_prompt_submit.py` reads `os.environ.get("VALOR_PARENT_SESSION_ID")` → passes to `create_local(parent_agent_session_id=...)`
4. **PM process**: `pre_tool_use.py` skips `create_child()` when env var will handle linkage (or is eliminated for the dev-session case)
5. **PM process**: `subagent_stop.py` queries children by `parent_agent_session_id` → finds `local-*` record → marks it completed

## Architectural Impact

- **New env var**: `VALOR_PARENT_SESSION_ID` — injected by `sdk_client.py` into child subprocess env. Follows the same pattern as `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, `SESSION_TYPE`.
- **Interface changes**: `create_local()` signature unchanged (uses `**kwargs`). `sdk_client.py._create_options()` gains one conditional env injection. `user_prompt_submit.py` gains one env var read.
- **Coupling**: Reduces coupling by eliminating the need for `pre_tool_use.py` to pre-create child records. The child is now self-registering with parent context.
- **Reversibility**: Easy — removing the env var injection reverts to the current state.

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

- **`sdk_client.py`**: When creating options for a session that has a known `agent_session_id`, inject it as `VALOR_PARENT_SESSION_ID` into the child subprocess env — but only when the current session is a PM/Teammate type spawning a child (i.e., `self.agent_session_id` is set).
- **`user_prompt_submit.py`**: On first prompt, read `VALOR_PARENT_SESSION_ID` from env and pass as `parent_agent_session_id` kwarg to `create_local()`.
- **`pre_tool_use.py`**: Remove the `create_child()` call for Agent tool dev-session spawning (the orphaned `dev-*` record). Retain the `start_stage()` pipeline tracking — that still runs in the parent and is correct.
- **`subagent_stop.py`**: Update the child session query to find `local-*` records by `parent_agent_session_id` (the env var approach makes this work correctly).

### Flow

PM session starts → `sdk_client.py` injects `VALOR_PARENT_SESSION_ID` → Child subprocess starts → `user_prompt_submit.py` reads env var → `create_local()` called with parent ID → One linked `AgentSession` created → Dashboard shows hierarchy

### Technical Approach

1. In `sdk_client.py._create_options()`: after the `AGENT_SESSION_ID` injection block, add:
   ```python
   # Inject parent session ID so child user_prompt_submit.py can link the local session
   if self.agent_session_id and self.session_type in (SessionType.PM, SessionType.TEAMMATE):
       env["VALOR_PARENT_SESSION_ID"] = self.agent_session_id
   ```
   Note: `self.agent_session_id` is the `AgentSession.agent_session_id` UUID of the **current** session (PM), not the bridge session ID. This is the canonical FK.

2. In `user_prompt_submit.py`, in the first-prompt branch before calling `create_local()`:
   ```python
   parent_agent_session_id = os.environ.get("VALOR_PARENT_SESSION_ID")
   agent_session = AgentSession.create_local(
       session_id=local_session_id,
       project_key=project_key,
       working_dir=cwd,
       status="running",
       message_text=prompt[:500] if prompt else "",
       **({"parent_agent_session_id": parent_agent_session_id} if parent_agent_session_id else {}),
   )
   ```

3. In `pre_tool_use.py._maybe_register_dev_session()`: Remove the `AgentSession.create_child()` call entirely. Keep the `start_stage()` call (pipeline tracking is still needed). The function should only start the pipeline stage now — rename to clarify.

4. In `subagent_stop.py._register_dev_session_completion()`: The existing query `AgentSession.query.filter(parent_agent_session_id=parent_session_id)` will now find the `local-*` record (because it now has the parent ID set). No query change needed, but verify the parent_session_id passed is the `agent_session_id` UUID, not the bridge session ID. The current code passes the bridge session ID via `resolve(claude_uuid)` — this may need alignment.

   **Key subtlety**: `VALOR_PARENT_SESSION_ID` carries the `agent_session_id` (UUID like `agt_xxx`), while `session_registry.resolve()` returns the bridge session ID (like `tg_valor_...`). The `parent_agent_session_id` field on `AgentSession` stores the `agent_session_id` FK — so the env var must carry the `agent_session_id`, not the bridge `session_id`.

   This means `sdk_client.py` must look up the parent's `agent_session_id` from the `AgentSession` record before injecting it. The `agent_session_id` is already available via `self.agent_session_id` (passed as a constructor argument).

## Failure Path Test Strategy

### Exception Handling Coverage

The `user_prompt_submit.py` changes occur inside a `try/except Exception: pass` block — all failures are already silent. No new exception handlers needed. The existing test suite has `test_creates_session_with_correct_fields` that should be updated to also verify the parent ID kwarg is passed through.

### Empty/Invalid Input Handling

- `VALOR_PARENT_SESSION_ID` absent (non-child sessions): `os.environ.get()` returns `None`, the conditional kwarg dict is empty, `create_local()` behaves identically to today.
- `VALOR_PARENT_SESSION_ID` present but malformed UUID: `create_local()` accepts any string — stored as-is. Not a crash risk.
- `VALOR_PARENT_SESSION_ID` set but parent record deleted: Child session saves fine, `parent_agent_session_id` points to a non-existent record. This is a pre-existing risk in the system (same as today).

### Error State Rendering

No user-visible output. Failures are silent (existing behavior). The only observable failure is `parent_agent_session_id` remaining null — testable via `valor_session list`.

## Test Impact

- [ ] `tests/unit/test_dev_session_registration.py::TestCreateLocalFactory::test_telegram_fields_are_null` — UPDATE: `parent_session_id` is now set when `VALOR_PARENT_SESSION_ID` env var is present; rename test or add a variant for the child-session case
- [ ] `tests/unit/test_dev_session_registration.py::TestPreToolUseDevDetection::test_detects_agent_tool_with_dev_session_type` — UPDATE: `create_child()` call is removed from `_maybe_register_dev_session()`; this test mocks `create_child` — update to verify it is NOT called (or removed)
- [ ] `tests/integration/test_parent_child_round_trip.py::TestSuccessRoundTrip::test_pretooluse_creates_child_and_starts_stage` — UPDATE: PreToolUse no longer creates a `dev-*` child record; test verifies stage starts but should not assert on `AgentSession.query.filter(parent_agent_session_id=...)` returning a dev-* record
- [ ] New test: `tests/unit/test_dev_session_registration.py::TestCreateLocalFactory::test_accepts_parent_session_id_from_env` — CREATE: verify that when `VALOR_PARENT_SESSION_ID` is set, `create_local()` stores it as `parent_agent_session_id`
- [ ] New test: `tests/integration/test_parent_child_round_trip.py::TestEnvVarLinkage` — CREATE: end-to-end test simulating child subprocess env var → `local-*` record with parent link

## Rabbit Holes

- **Propagating the env var through all Agent tool types** — Only PM/Teammate sessions spawn tracked children. Dev sessions spawning sub-agents (e.g., Task tool) use a different mechanism. Keep the injection scoped to `SESSION_TYPE in (PM, TEAMMATE)`.
- **Merging `dev-*` + `local-*` records** — Don't merge. Just stop creating `dev-*` records. Merging would require reading from the sidecar file across process boundaries, which is complex and fragile.
- **Fixing `subagent_stop.py` bridge-session-ID vs agent-session-ID mismatch** — The query currently passes the bridge `session_id` (from `session_registry.resolve()`) as `parent_agent_session_id`, but the field stores the `agent_session_id` UUID. This is a pre-existing mismatch that should be tracked separately.

## Risks

### Risk 1: `self.agent_session_id` may not be set on the `ValorAgent`
**Impact:** `VALOR_PARENT_SESSION_ID` is not injected; child sessions remain unlinked (current behavior, no regression).
**Mitigation:** Add a defensive check — only inject when `self.agent_session_id` is non-empty. Log a warning when missing for a PM session.

### Risk 2: `subagent_stop.py` parent ID mismatch (bridge ID vs agent_session_id)
**Impact:** `subagent_stop.py` queries `parent_agent_session_id=bridge_session_id` which won't match `parent_agent_session_id=agent_session_id_uuid` set by the env var approach. Completion tracking breaks.
**Mitigation:** Fix `subagent_stop.py` to look up the parent's `agent_session_id` from the bridge session ID before querying. This is part of this fix scope.

### Risk 3: Existing tests expect `dev-*` records to be created by PreToolUse
**Impact:** Test failures after removing `create_child()` from `pre_tool_use.py`.
**Mitigation:** Identified in Test Impact section — update tests as part of this work.

## Race Conditions

### Race 1: Child process reads env before parent finishes writing agent_session_id
**Location:** `sdk_client.py._create_options()` and the `ValorAgent` constructor
**Trigger:** `self.agent_session_id` is populated from the `AgentSession` record created by the worker before spawning the SDK client. If the record isn't saved yet when the SDK client is instantiated, the env var is empty.
**Data prerequisite:** `AgentSession.agent_session_id` must be saved to Redis before `ValorAgent` is instantiated.
**State prerequisite:** The parent PM session must be in `running` state with its `agent_session_id` populated.
**Mitigation:** The worker creates the `AgentSession` record (and saves it) before spawning `ValorAgent`. This is synchronous and already correct. No new race introduced.

## No-Gos (Out of Scope)

- Fixing the `subagent_stop.py` bridge-session-ID vs agent-session-ID mismatch beyond what's needed for this fix (tracked as a follow-up)
- Propagating parent linkage to Task tool sub-agents (different mechanism, separate concern)
- Retroactively linking existing orphaned `local-*` records (one-time data migration not worth the complexity)
- Changing how `session_registry.py` works (it solves the parent-process hook problem; this fix solves the child-process self-registration problem)

## Update System

No update system changes required — this feature is purely internal and involves no new dependencies, config files, or deployment steps.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. The fix operates at the process-spawning layer, not at the MCP tool layer.

## Documentation

- [ ] Update `docs/features/pm-dev-session-architecture.md` to document the `VALOR_PARENT_SESSION_ID` env var and the child self-registration flow
- [ ] Add inline comments to `sdk_client.py` and `user_prompt_submit.py` explaining the parent-linkage mechanism

## Success Criteria

- [ ] Child sessions spawned by PM sessions show correct `parent_agent_session_id` on the dashboard
- [ ] Only one `AgentSession` record exists per child subprocess (no duplicate `dev-*` + `local-*` pair)
- [ ] `python -m tools.valor_session list` shows correct parent→child hierarchy
- [ ] `tests/unit/test_dev_session_registration.py` passes with updated assertions
- [ ] `tests/integration/test_parent_child_round_trip.py` passes with updated assertions
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-linkage)**
  - Name: linkage-builder
  - Role: Implement env var injection in sdk_client.py, read in user_prompt_submit.py, remove create_child() from pre_tool_use.py, fix subagent_stop.py query
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: linkage-test-engineer
  - Role: Update existing tests and write new tests for the env-var-based parent linkage
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: linkage-validator
  - Role: Verify implementation meets all acceptance criteria, run valor_session list
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Implement env var injection and child session linkage
- **Task ID**: build-linkage
- **Depends On**: none
- **Validates**: `tests/unit/test_dev_session_registration.py`, `tests/integration/test_parent_child_round_trip.py`
- **Assigned To**: linkage-builder
- **Agent Type**: builder
- **Parallel**: true
- In `sdk_client.py._create_options()`: inject `VALOR_PARENT_SESSION_ID = self.agent_session_id` when `self.session_type in (SessionType.PM, SessionType.TEAMMATE)` and `self.agent_session_id` is set
- In `user_prompt_submit.py`: read `VALOR_PARENT_SESSION_ID` from env; pass as `parent_agent_session_id` kwarg to `create_local()` when present
- In `pre_tool_use.py._maybe_register_dev_session()`: remove `AgentSession.create_child()` call; keep `start_stage()` call; rename function to `_maybe_start_pipeline_stage()` or similar
- In `subagent_stop.py`: ensure the child session query looks up the parent `AgentSession` by bridge session ID first, then queries children by `parent_agent_session_id` using the `agent_session_id` UUID (not the bridge ID)

### 2. Update and write tests
- **Task ID**: build-tests
- **Depends On**: build-linkage
- **Validates**: `tests/unit/test_dev_session_registration.py`, `tests/integration/test_parent_child_round_trip.py`
- **Assigned To**: linkage-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `TestCreateLocalFactory::test_telegram_fields_are_null` to reflect that `parent_agent_session_id` is set when env var present
- Update `TestPreToolUseDevDetection::test_detects_agent_tool_with_dev_session_type` — `create_child` should NOT be called; stage start should still happen
- Update `TestSuccessRoundTrip::test_pretooluse_creates_child_and_starts_stage` — verify stage starts but no `dev-*` record created
- Add `TestCreateLocalFactory::test_accepts_parent_session_id_from_env` — env var present → `create_local()` stores parent ID
- Add `TestEnvVarLinkage` integration test class simulating full child env var → linked session flow

### 3. Validate
- **Task ID**: validate-linkage
- **Depends On**: build-tests
- **Assigned To**: linkage-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_dev_session_registration.py tests/integration/test_parent_child_round_trip.py -v`
- Verify no duplicate `dev-*` + `local-*` records exist in any test
- Verify `parent_agent_session_id` is set on `local-*` records when env var is present
- Report pass/fail

### 4. Documentation
- **Task ID**: document-linkage
- **Depends On**: validate-linkage
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-dev-session-architecture.md` to document `VALOR_PARENT_SESSION_ID` env var
- Add inline comments explaining the parent-linkage mechanism

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-linkage
- **Assigned To**: linkage-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_dev_session_registration.py -v` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_parent_child_round_trip.py -v` | exit code 0 |
| No orphaned dev-* records in tests | `pytest tests/ -k "parent" -v` | exit code 0 |
| No duplicate session records | `pytest tests/unit/test_dev_session_registration.py -v` | exit code 0 |
| Format clean | `python -m black --check agent/sdk_client.py .claude/hooks/user_prompt_submit.py agent/hooks/pre_tool_use.py agent/hooks/subagent_stop.py` | exit code 0 |

## Critique Results

| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

None — root cause and solution are fully validated through code-read. Ready for implementation.
