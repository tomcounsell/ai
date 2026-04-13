---
status: docs_complete
type: bug
appetite: Medium
owner: valorengels
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/934
last_comment_id:
revision_applied: true
---

# PM Session Scope + Wait: PM Exits Before Dev Session Completes

## Problem

When a PM session spawns a dev session for an SDLC stage, the PM exits before the dev session finishes. The dev session's completion handler (`_handle_dev_session_completion`) tries to steer the parent PM, but `steer_session()` silently rejects the message because the parent is already in a terminal status. The pipeline stalls with no active PM to advance it.

**Current behavior:**
1. PM spawns dev session for CRITIQUE, then completes within seconds (before the dev session even starts).
2. Dev session finishes, `_handle_dev_session_completion` calls `steer_session(parent.session_id, ...)`.
3. `steer_session` returns `{"success": False, "error": "Session is in terminal status 'completed' — steering rejected"}`.
4. The return value is not checked — line 2889 logs "Steered parent PM session" regardless of success.
5. Pipeline stalls permanently.

Compounding this: concurrent PM sessions assess global state (`gh issue list`) rather than being scoped to their assigned issue, causing one PM to dispatch BUILD for another PM's issue before CRITIQUE completes.

**Desired outcome:**
A PM session that spawns a dev session stays alive until that dev session completes and delivers its steering message. If the PM is already terminal when a dev session completes, a continuation PM is created to carry the pipeline forward. PM sessions are scoped to a single issue.

## Freshness Check

**Baseline commit:** `cb03ed4b`
**Issue filed at:** 2026-04-13T07:04:05Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:2696` — `steer_session()` definition — confirmed at line 2696
- `agent/agent_session_queue.py:2729-2733` — terminal status guard in `steer_session()` — confirmed at lines 2729-2733
- `agent/agent_session_queue.py:2787` — `_handle_dev_session_completion()` definition — confirmed at line 2787
- `agent/agent_session_queue.py:2882-2892` — steer call in `_handle_dev_session_completion` with no return-value check — confirmed
- `models/session_lifecycle.py:61` — `TERMINAL_STATUSES` frozenset definition — confirmed at line 61
- `models/session_lifecycle.py:530-609` — `_finalize_parent_sync()` — confirmed at lines 530-609

**Cited sibling issues/PRs re-checked:**
- #887 — CLOSED 2026-04-10 (session isolation bypass: PM in main checkout instead of worktree)
- #791 — CLOSED 2026-04-07 (PM skipping CRITIQUE and REVIEW stages)
- #846 — CLOSED 2026-04-09 (PM routing: classifier buckets, tool visibility)
- #786 — CLOSED 2026-04-11 (PM session fan-out for multi-issue prompts)

**Commits on main since issue was filed (touching referenced files):**
None — the issue was filed less than 12 hours ago.

**Active plans in `docs/plans/` overlapping this area:**
- `pm-dev-session-briefing.md` — touches `sdk_client.py` PM message enrichment but does not affect `_handle_dev_session_completion` or `steer_session`. No conflict.
- `pm-session-child-fanout.md` — established `wait-for-children` for multi-issue fan-out. This plan extends the same pattern to single-issue dev dispatch. No conflict; builds on top.

**Notes:** All line references match exactly. The codebase is stable for this area.

## Prior Art

- **Issue #791 / Plan `pm-skips-critique-and-review.md`**: Added hard rules to PM persona enforcing CRITIQUE and REVIEW gates. Addressed the symptom (skipping stages) but not the root cause (PM exiting before dev session completes, so stage results never reach the PM).
- **Issue #786 / Plan `pm-session-child-fanout.md`**: Introduced `wait-for-children` pattern for multi-issue fan-out. But `wait-for-children` is only invoked for fan-out (multiple child PM sessions), not for single dev session dispatches. The pattern exists but is not applied broadly enough.
- **Issue #743**: Externalized session steering — established `steer_session()` and `queued_steering_messages`. Working correctly, but the caller (`_handle_dev_session_completion`) does not check the return value.
- **PR #902**: Harness abstraction — introduced `_handle_dev_session_completion` and the parent PM steering call. The steer call was added without a fallback for terminal parent.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Issue #791 (PM persona hard rules) | Added CRITIQUE and REVIEW enforcement rules to PM persona | Addressed which stages to dispatch, not the timing problem. PM still exits before dev session completes, so the PM never sees the stage result and never dispatches the next stage. |
| Issue #786 (fan-out wait-for-children) | Added `wait-for-children` for multi-issue fan-out | Only applies to multi-issue PM→PM fan-out. Single-issue PM→dev dispatch does not use `wait-for-children`. The PM dispatches one dev session and immediately returns. |

**Root cause pattern:** All prior fixes operated at the PM persona level (instructions to the LLM) without addressing the infrastructure gap: there is no mechanism to keep a PM alive while its dev session runs, and no fallback when the PM is already gone.

## Data Flow

1. **PM session receives work request** → PM assesses state via `/sdlc`, decides which stage to dispatch
2. **PM spawns dev session** → `python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID" --message "..."` → child `AgentSession` created in Redis with `parent_agent_session_id` set
3. **PM session returns** → PM produces output ("I've dispatched BUILD"), the SDK client returns, worker finalizes PM session as `completed`
4. **Dev session executes** → Worker picks up the dev session, runs it via CLI harness
5. **Dev session completes** → Worker calls `_handle_dev_session_completion(session, agent_session, result)`
6. **Completion handler steers parent** → `steer_session(parent.session_id, steering_msg)` → returns `{"success": False}` because parent is terminal
7. **Pipeline stalls** → No active PM session exists to receive the stage result and dispatch the next stage

The break is at step 6→7: the steering is silently dropped and no continuation PM is created.

## Architectural Impact

- **New dependencies**: None — all changes use existing infrastructure (`steer_session`, `valor_session create`, `_finalize_parent_sync`)
- **Interface changes**: `_handle_dev_session_completion` gains a fallback path (continuation PM creation). `steer_session` return value is checked. No public API changes.
- **Coupling**: Slightly increases coupling between `_handle_dev_session_completion` and `valor_session create`, but this is the natural escalation path when steering fails.
- **Data ownership**: No change — `AgentSession` remains the single source of truth for session state.
- **Reversibility**: Fully reversible — the continuation PM path is additive, and the PM persona changes are text edits.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on continuation PM behavior)
- Review rounds: 1-2 (code review for the infrastructure change)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are within the existing agent infrastructure.

## Solution

### Key Elements

- **Fix 1 — PM persona: `wait-for-children` after every dev dispatch**: Extend the PM persona instructions so the PM calls `wait-for-children` after dispatching ANY dev session (not just multi-issue fan-out). This keeps the PM process alive until the steering message arrives.
- **Fix 2 — Continuation PM fallback in `_handle_dev_session_completion`**: When `steer_session()` returns `success: False` because the parent is terminal, create a continuation PM session with the stage result and issue context. This is the safety net for when Fix 1 fails (e.g., PM exits due to token limit, crash, or kill).
- **Fix 3 — PM persona: single-issue scoping**: When a PM session's message references a specific issue number, the PM must only assess and advance that issue — not any other issue it discovers via `gh issue list`.

### Flow

**Normal path (Fix 1):**
PM receives "issue 934" → PM dispatches dev session → PM calls `wait-for-children` → PM stays alive → Dev completes → `_handle_dev_session_completion` steers PM → PM receives steering → PM dispatches next stage

**Fallback path (Fix 2):**
PM exits early (token limit, crash) → Dev completes → `_handle_dev_session_completion` tries to steer PM → Steer fails (terminal) → Completion handler creates continuation PM → Continuation PM reads stage result and issue context → Pipeline resumes

### Technical Approach

**Fix 1 — PM persona update:**

Update PM persona instructions in both:
- `~/Desktop/Valor/personas/project-manager.md` (production, private)
- `config/personas/project-manager.md` (repo template)
- `agent/sdk_client.py` (message enrichment for PM sessions)

Add a rule: after dispatching any dev session via `valor_session create --role dev`, immediately call:
```bash
python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"
```

This transitions the PM to `waiting_for_children` status. The existing `_finalize_parent_sync()` hook in `models/session_lifecycle.py` will auto-transition the PM back to `completed` when the dev session finishes, but critically: the PM process stays alive (blocked on the `wait-for-children` call) so it can receive the steering message.

**Fix 2 — Continuation PM in `_handle_dev_session_completion`:**

In `agent/agent_session_queue.py`, after the `steer_session()` call (line 2889), check the return value:

```python
steer_result = steer_session(parent.session_id, steering_msg)
if steer_result.get("success"):
    logger.info(f"[harness] Steered parent PM session {parent.session_id}")
else:
    logger.warning(
        f"[harness] Steering rejected for parent {parent.session_id}: "
        f"{steer_result.get('error')} — creating continuation PM"
    )
    _create_continuation_pm(
        parent=parent,
        agent_session=agent_session,
        issue_number=issue_number,
        stage=current_stage,
        outcome=outcome,
        result_preview=result_preview,
    )
```

The `_create_continuation_pm` function creates a new PM session via `AgentSession.create()` with:
- `session_type="pm"`, same `chat_id` and `project_key` as the parent
- `message_text` containing: the issue number, which stage just completed, the outcome, and a directive to resume the SDLC pipeline
- `parent_agent_session_id` set to the original parent (for lineage tracking)
- Status set to `pending` so the worker picks it up

**Fix 3 — Issue scoping in PM persona:**

Add a hard rule to the PM persona: "If this message references a specific issue number (e.g., 'issue 934', 'issue #934', or 'issues/934'), you MUST only assess and advance that issue. Do not query `gh issue list` for other issues. Do not dispatch stages for any issue other than the one specified."

This is a persona-level instruction, not a code change. It prevents the cross-contamination observed in the incident where PM session for #928 dispatched BUILD for #927's issue.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_handle_dev_session_completion` lines 2882-2892: the `steer_session` call is wrapped in try/except but the return value `success: False` is not treated as a failure — test that the continuation PM path fires when steer returns failure
- [ ] `_create_continuation_pm` must have its own try/except so a failure to create the continuation PM does not crash the completion handler
- [ ] Test that `steer_session` returning `success: False` with a non-terminal error (e.g., "Session not found") also triggers the continuation path

### Empty/Invalid Input Handling
- [ ] Test `_create_continuation_pm` with `issue_number=None` — should still create a continuation PM with a fallback message
- [ ] Test `_create_continuation_pm` with empty `result_preview` — should handle gracefully

### Error State Rendering
- [ ] Verify continuation PM messages are human-readable and contain enough context for the PM to resume
- [ ] Verify log messages clearly distinguish "steer succeeded" from "steer failed, creating continuation"

## Test Impact

- [ ] `tests/integration/test_parent_child_round_trip.py::TestHandleDevSessionCompletion::test_success_result_steers_parent` — UPDATE: verify that steer_session return value is checked (currently mocked but the mock doesn't assert return-value handling)
- [ ] `tests/integration/test_parent_child_round_trip.py::TestHandleDevSessionCompletion::test_steer_message_contains_stage_and_outcome` — UPDATE: the mock `_capture_steer` returns `success: True` which is fine, but add a parallel test where it returns `success: False` to verify the continuation path
- [ ] `tests/unit/test_steering_mechanism.py::TestSteerSessionGuards` — no change needed (tests the guard itself, which is unchanged)

## Rabbit Holes

- **Implementing a blocking wait at the infrastructure level** — It's tempting to make the worker hold the PM process alive via an async wait loop, but this would require fundamental changes to the worker's session execution model. The `wait-for-children` CLI command already provides this behavior at the PM persona level. The continuation PM is the safety net.
- **Automatic retry of failed steers** — Adding retry logic to `steer_session` would mask timing bugs. The continuation PM is a cleaner solution because it creates a fresh PM with full context rather than retrying a stale one.
- **Changing `wait-for-children` to block the worker** — Currently `wait-for-children` transitions the session status and the CLI exits. The PM agent process is kept alive by the SDK client's turn loop, not by the CLI command. Making it actually block would tie up a worker slot.

## Risks

### Risk 1: Continuation PM creates a runaway chain
**Impact:** If continuation PMs fail and create more continuations, we get an infinite chain of PM sessions.
**Mitigation:** Cap continuation depth. Add a `continuation_depth` field to `AgentSession` (or track via the `parent_agent_session_id` chain). If depth exceeds 3, log an error and stop — do not create another continuation. The health check in `_agent_session_hierarchy_health_check` already monitors stuck parent-child chains.

### Risk 2: PM persona instruction not followed (LLM non-compliance)
**Impact:** The PM ignores the `wait-for-children` instruction and exits early, falling back to Fix 2 every time.
**Mitigation:** Fix 2 (continuation PM) is specifically designed as a safety net for this case. Additionally, reinforce the instruction in `sdk_client.py` message enrichment (injected programmatically, not just in persona docs). The `wait-for-children` instruction already exists for fan-out — extending it to single dev dispatches is a small delta.

### Risk 3: Continuation PM lacks sufficient context
**Impact:** The continuation PM doesn't have the full conversation history, so it might make incorrect routing decisions.
**Mitigation:** The continuation PM message includes the issue number, completed stage, outcome, and result preview. Combined with `sdlc-stage-comment` posts on the GitHub issue and `stage_states` from `PipelineStateMachine`, the continuation PM has enough signal to resume correctly. This is the same pattern used by the SDLC router skill.

## Race Conditions

### Race 1: Dev session completes while PM is transitioning to `waiting_for_children`
**Location:** `models/session_lifecycle.py:583-585` and `agent/agent_session_queue.py:2889`
**Trigger:** PM calls `wait-for-children` at the exact moment the dev session completes and tries to steer the PM. The PM's status might be mid-transition (e.g., `active` → `waiting_for_children`).
**Data prerequisite:** PM session must exist in Redis with a non-terminal status.
**State prerequisite:** PM status must be writable (not locked by another process).
**Mitigation:** `steer_session` checks status at call time. If PM is still `active` or `waiting_for_children` (both non-terminal), steering succeeds. `_finalize_parent_sync` handles the completion transition. The continuation PM path only fires if the parent is already terminal, so the race window is: PM finalized → dev completes → steer fails. This is the exact scenario Fix 2 addresses.

### Race 2: Two dev sessions complete simultaneously and both try to create continuation PMs
**Location:** `agent/agent_session_queue.py:2882-2895`
**Trigger:** PM spawned two dev sessions (e.g., via parallel dispatch) and both complete at the same moment, both see parent as terminal.
**Data prerequisite:** Parent PM must be terminal.
**State prerequisite:** No locking on continuation PM creation.
**Mitigation:** Each continuation PM is independently valid — it contains the stage and outcome from its specific dev session. The second continuation PM will see that the first has already advanced the pipeline (via `stage_states`) and either no-op or dispatch the next stage. This is idempotent because the SDLC router assesses state before dispatching.

## No-Gos (Out of Scope)

- Changing the fan-out pattern itself (multi-issue → child PM sessions)
- Changing how dev sessions are classified or dispatched by the worker
- Implementing a worker-level blocking wait (would tie up worker slots)
- Changing `_finalize_parent_sync` behavior
- Adding retry logic to `steer_session`

## Update System

No update system changes required — all changes are to agent infrastructure code (`agent/agent_session_queue.py`) and persona instructions. The update script does not need modification. Standard `git pull` propagates all changes.

## Agent Integration

No new agent integration required — this is a worker-internal change. The continuation PM is created via `AgentSession.create()` inside the worker process, not via an MCP tool. The PM persona changes are injected via `sdk_client.py` message enrichment (already the established pattern). No changes to `.mcp.json` or `mcp_servers/`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-dev-session-architecture.md` to document the `wait-for-children` requirement after dev dispatch and the continuation PM fallback
- [ ] Add entry to `docs/features/README.md` index table if a new doc is created

### Inline Documentation
- [ ] Docstring on `_create_continuation_pm` explaining when and why it fires
- [ ] Comment block in `_handle_dev_session_completion` explaining the steer-check → continuation fallback

## Success Criteria

- [ ] A PM session spawned for a single issue does not dispatch dev work for any other issue
- [ ] A PM session that spawns a dev session calls `wait-for-children` before exiting
- [ ] When `_handle_dev_session_completion` cannot steer the parent (terminal status), a continuation PM is created with the stage result and issue context
- [ ] Running SDLC on two issues simultaneously produces two clean, non-interfering pipelines
- [ ] No BUILD dev session is ever queued before CRITIQUE completes for the same issue
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (agent-infrastructure)**
  - Name: infra-builder
  - Role: Implement continuation PM fallback in `_handle_dev_session_completion`, update PM persona instructions
  - Agent Type: builder
  - Resume: true

- **Validator (agent-infrastructure)**
  - Name: infra-validator
  - Role: Verify continuation PM creation, steer-check behavior, and PM persona compliance
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement continuation PM fallback in `_handle_dev_session_completion`
- **Task ID**: build-continuation-pm
- **Depends On**: none
- **Validates**: tests/integration/test_parent_child_round_trip.py, tests/unit/test_continuation_pm.py (create)
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_create_continuation_pm()` function to `agent/agent_session_queue.py` that creates a new PM session with the stage result and issue context
- Modify `_handle_dev_session_completion` steer call (line 2889) to check `steer_session()` return value and call `_create_continuation_pm()` on failure
- Add continuation depth tracking: check parent chain depth, cap at 3
- Add logging to clearly distinguish "steer succeeded" from "steer failed → continuation created"

### 2. Update PM persona: wait-for-children after every dev dispatch
- **Task ID**: build-persona-wait
- **Depends On**: none
- **Validates**: manual review of persona text
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `config/personas/project-manager.md` with new hard rule: after dispatching any dev session, call `wait-for-children`
- Update `agent/sdk_client.py` message enrichment for PM sessions to include the `wait-for-children` instruction after dev dispatch (not just fan-out)
- Note: `~/Desktop/Valor/personas/project-manager.md` (production) must be updated separately by the human or deploy process

### 3. Update PM persona: single-issue scoping
- **Task ID**: build-persona-scope
- **Depends On**: none
- **Validates**: manual review of persona text
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Add hard rule to `config/personas/project-manager.md`: "If this message references a specific issue number, only assess and advance that issue"
- Add the same rule to `agent/sdk_client.py` PM message enrichment
- The rule must explicitly prohibit querying `gh issue list` for other issues when a specific issue is assigned

### 4. Write tests for continuation PM fallback
- **Task ID**: build-tests
- **Depends On**: build-continuation-pm
- **Validates**: tests/unit/test_continuation_pm.py (create), tests/integration/test_parent_child_round_trip.py (update)
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_continuation_pm.py` with tests for:
  - `_create_continuation_pm` creates a valid PM session with correct fields
  - `_create_continuation_pm` with `issue_number=None` still creates a session
  - Continuation depth cap prevents infinite chains
  - `_handle_dev_session_completion` calls `_create_continuation_pm` when steer returns failure
- Update `tests/integration/test_parent_child_round_trip.py`:
  - Add test where PM is terminal before dev completes → continuation PM is created
  - Add test where steer returns `success: False` → continuation PM fires

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-continuation-pm, build-persona-wait, build-persona-scope, build-tests
- **Assigned To**: infra-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_continuation_pm.py tests/unit/test_steering_mechanism.py tests/integration/test_parent_child_round_trip.py -v`
- Verify `_create_continuation_pm` is called when steer fails
- Verify PM persona text includes `wait-for-children` after dev dispatch
- Verify PM persona text includes single-issue scoping rule
- Run `python -m ruff check agent/agent_session_queue.py config/personas/project-manager.md`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: infra-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-dev-session-architecture.md` with continuation PM fallback and wait-for-children requirement
- Add entry to `docs/features/README.md` index table

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: infra-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_continuation_pm.py tests/unit/test_steering_mechanism.py tests/integration/test_parent_child_round_trip.py -v` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| Continuation PM function exists | `grep -n '_create_continuation_pm' agent/agent_session_queue.py` | output contains _create_continuation_pm |
| Steer return value checked | `grep -n 'steer_result.*success' agent/agent_session_queue.py` | output contains steer_result |
| PM persona has wait rule | `grep -c 'wait-for-children' config/personas/project-manager.md` | output > 1 |
| PM persona has scope rule | `grep -c 'only.*that issue\|single.issue' config/personas/project-manager.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Continuation PM depth cap**: The plan proposes capping at depth 3. Is that sufficient, or should the cap be lower (e.g., 2) to fail fast and alert the human sooner?
2. **Production persona sync**: `~/Desktop/Valor/personas/project-manager.md` (production) must be updated alongside `config/personas/project-manager.md` (repo template). Should the build process update the production persona directly, or should this be a manual step after merge?
