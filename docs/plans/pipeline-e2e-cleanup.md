---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-03-22
tracking: https://github.com/tomcounsell/ai/issues/467
---

# Pipeline Cleanup + E2E Tests

## Problem

After the ChatSession/DevSession redesign (PRs #464, #466), the codebase carries dead code and a vestigial session type that contradict the new architecture. Writing e2e tests against the current code would encode wrong behavior.

**Current behavior:**
1. `bridge/agents.py` (387 lines) is mostly dead: lines 1-193 define retry/self-healing code nothing calls (`get_agent_response()` is a passthrough to `sdk_client.get_agent_response_sdk()`). Lines 195-387 define tracked-work-detection functions that feed `WorkflowState` — a file-based SDLC phase tracker (`agent/workflow_state.py`, `agent/workflow_types.py`) that duplicates what `AgentSession.sdlc_stages` already handles in Redis.
2. Three session types exist (`chat`, `dev`, `simple`) but `simple` serves no distinct purpose. The bridge routes non-SDLC messages to `simple`, then `sdk_client.py` tells simple sessions to "Invoke /sdlc immediately" — contradicting the direct-delivery intent.
3. No "Dev: X" chat group → DevSession routing exists. Currently all messages go through the classifier → ChatSession or simple path.
4. Phase 2 e2e tests (hook integration, nudge loop, session isolation) don't exist.

**Desired outcome:**
- Dead code and redundant state tracking deleted entirely (not relocated)
- Two session types only: `chat` (default) and `dev` ("Dev: X" groups)
- E2e tests that verify pipeline behavior at boundaries without coupling to internals

## Prior Art

- **PR #464**: ChatSession/DevSession split — introduced session_type discriminator, factory methods
- **PR #466**: Nudge loop + Observer deletion — replaced Observer with nudge loop, deleted `bridge/observer.py`
- **PR #431**: Organized test suite with feature markers and e2e structure
- **PR #327**: Removed dead SDLC stage-tracking code from hook files — precedent for dead code cleanup

## Data Flow

After this change, the message routing simplifies to:

1. **Entry**: Telegram message arrives at `bridge/telegram_bridge.py`
2. **Routing decision**: Check `chat_title` prefix
   - `"Dev: "` prefix → `session_type="dev"` (DevSession, full permissions, dev persona)
   - Everything else → `session_type="chat"` (ChatSession, PM persona, read-only)
3. **Enqueue**: `job_queue.enqueue_job()` creates AgentSession via factory method, queues for worker
4. **Execute**: Worker calls `sdk_client.get_agent_response_sdk()` with session_type
   - ChatSession: PM persona prompt, `permission_mode="plan"`, may spawn DevSession subagent
   - DevSession: Dev persona prompt, `permission_mode="bypassPermissions"`
5. **Output**: `send_to_chat()` nudge loop — deliver if end_turn + content, nudge if empty/rate-limited

## Architectural Impact

- **Interface changes**: `AgentSession.create_simple()` removed; `workflow_id` field removed from AgentSession; bridge routing simplified from classifier-based to chat_title-based for Dev groups
- **Coupling**: Decreases — removes the simple/chat/dev three-way branch in sdk_client.py, removes file-based WorkflowState in favor of existing Redis-backed `sdlc_stages`
- **New dependencies**: None
- **Data ownership**: No change — AgentSession model still owns session state in Redis. SDLC phase tracking consolidates on `AgentSession.sdlc_stages` (already exists)
- **Reversibility**: High — if we need a third session type later, add it back with a factory method

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on this plan)
- Review rounds: 1 (PR review)

## Prerequisites

No prerequisites — this work has no external dependencies. Requires only Redis (for tests) which is already running.

## Solution

### Key Elements

- **Dead code + redundant state deletion**: Delete `bridge/agents.py` entirely, delete `agent/workflow_state.py` and `agent/workflow_types.py`, remove `workflow_id` field from AgentSession and job queue pipeline. Nothing replaces `workflow_id` — its purposes are already served by existing AgentSession fields: identity (`session_id`/`job_id`), plan path (derived from `work_item_slug`), tracking URL (`session.get_links()["issue"]`), phase/stage (`session.sdlc_stages`/`session.current_stage`)
- **Simple session removal**: Delete `create_simple()`, `is_simple`, `SESSION_TYPE_SIMPLE` from model; update bridge routing and sdk_client branching
- **Dev group routing**: "Dev: X" chat title prefix → `session_type="dev"` in bridge, bypassing classifier
- **Inline SDK call**: Replace `bridge.agents.get_agent_response()` passthrough with direct `get_agent_response_sdk()` call in bridge
- **E2E tests**: Three new test files verifying behavior at system boundaries

### Flow

**Cleanup flow:**
Delete `bridge/agents.py`, `agent/workflow_state.py`, `agent/workflow_types.py` → inline `get_agent_response_sdk()` call in bridge → remove `workflow_id` from AgentSession/job_queue/sdk_client → remove simple session type → add "Dev: X" routing

**Test flow:**
Hook test: simulate PreToolUse input → verify DevSession exists in Redis with parent linkage → simulate SubagentStop → verify status is "completed"

Nudge test: create session → call send_to_chat with various (stop_reason, output) combos → verify deliver vs nudge outcome

Isolation test: enqueue two sessions on same chat → first session fails → second session still processes

### Technical Approach

- Delete tracked-work detection entirely — it exists solely to feed WorkflowState, which is redundant with `AgentSession.sdlc_stages`. No relocation needed.
- Inline `get_agent_response_sdk()` call directly in `telegram_bridge.py` — remove the one-hop passthrough
- For "Dev: X" routing: detect at bridge level before classifier runs, set `session_type="dev"` directly. The classifier is irrelevant for dev groups — they always get a DevSession. Skip classifier for Dev groups.
- For e2e tests: mock only `get_agent_response_sdk` (Claude API boundary) and Telegram client. Use real Redis. Verify outcomes not internals.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Deletion of `bridge/agents.py`, `agent/workflow_state.py`, `agent/workflow_types.py` removes dead exception handlers — no new ones introduced
- [ ] SubagentStop hook has `try/except` that swallows errors — existing unit test covers this

### Empty/Invalid Input Handling
- [ ] Nudge loop e2e tests cover empty output → nudge (not delivery)
- [ ] Hook tests cover missing `VALOR_SESSION_ID` env var → graceful skip

### Error State Rendering
- [ ] Session isolation test verifies failed session gets error status, other sessions unaffected

## Test Impact

- [ ] `tests/unit/test_chat_session_factory.py` — UPDATE: remove 5 simple-session tests, add "Dev: X" → DevSession routing tests
- [ ] `tests/e2e/test_session_lifecycle.py` — UPDATE: remove 1 simple-session creation test, add ChatSession-for-Q&A test
- [ ] `tests/e2e/test_context_propagation.py` — UPDATE: remove 4 simple-session tests, update type discrimination to chat/dev only
- [ ] `tests/unit/test_nudge_loop.py` — UPDATE: remove any simple-session references
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: if it references `bridge.agents` imports
- [ ] `tests/unit/test_workflow_sdk_integration.py` — DELETE: tests WorkflowState integration which is being removed
- [ ] `tests/integration/test_job_queue_race.py` — UPDATE: remove `workflow_id="wf123456"` from enqueue calls and field assertions
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: remove `mock_job.workflow_id = None` and WorkflowState comment
- [ ] `bridge/coach.py` — UPDATE: remove WorkflowState.phase comment reference (line 39)
- [ ] `bridge/catchup.py` — UPDATE: remove `workflow_id=None` from enqueue call (line 186)

## Rabbit Holes

- **Re-implementing retry/self-healing** — The dead code in `bridge/agents.py` represents a previous design. Don't rebuild it; the session watchdog (`monitoring/session_watchdog.py`) already handles recovery with exponential backoff and is well-tested (800+ lines of tests)
- **Testing the nudge loop's internal branching** — Test deliver-vs-nudge *outcomes*, not the `if/elif` structure inside `send_to_chat()`. The test should survive a refactor of the branching logic
- **Steering queue in nudge tests** — Steering and nudging are separate systems. Don't conflate them in a single test file. Steering injection is already tested in `tests/integration/test_steering.py` (32 tests)

## Risks

### Risk 1: Deleting tracked-work detection removes plan-file matching
**Impact:** The bridge currently auto-detects when a message references a plan file + issue number, and threads a `workflow_id` through the pipeline. Deleting this means the bridge no longer auto-links messages to plans.
**Mitigation:** This functionality is redundant. The ChatSession PM persona already has access to `docs/plans/` via file tools, and SDLC stage tracking uses `AgentSession.sdlc_stages` in Redis. The auto-detection was a convenience, not load-bearing.

### Risk 2: Removing simple sessions may break the bridge for non-SDLC messages
**Impact:** Q&A messages that previously got simple sessions could fail or route incorrectly
**Mitigation:** All messages become ChatSessions. The PM persona is intelligent enough to answer Q&A directly without spawning a DevSession. This is the intended architecture — PM decides what to do.

## Race Conditions

No race conditions identified — the routing change is synchronous (chat_title prefix check before enqueue), and session type is immutable once set at creation time.

## No-Gos (Out of Scope)

- Re-implementing retry/backoff at the bridge layer — watchdog handles this
- Testing full SDLC pipeline runs end-to-end (wish list item, not this PR)
- Modifying the classifier — it still runs for ChatSessions, just no longer gates session_type
- Adding new session types — strictly reducing to two for now

## Update System

No update system changes required — this is an internal refactoring. The bridge restart after deploy will pick up the new routing automatically.

## Agent Integration

No agent integration required — this changes bridge-internal routing and model structure. No new MCP servers or tool registrations needed. The agent (Claude) is unaffected; it receives the same prompts via sdk_client.py, just without the stale "Invoke /sdlc" prompt for non-SDLC messages.

## Documentation

- [ ] Update `docs/features/chat-dev-session-architecture.md` — remove simple session references, add "Dev: X" → DevSession routing
- [ ] Update `CLAUDE.md` — remove any simple session references in architecture section
- [ ] Update `tests/README.md` — if it references simple sessions or bridge/agents.py

## Success Criteria

- [ ] `bridge/agents.py`, `agent/workflow_state.py`, `agent/workflow_types.py` do not exist
- [ ] No references to `workflow_id` in production code (`models/`, `agent/`, `bridge/`) except as comments
- [ ] No references to `simple` session type in production code (`models/`, `agent/`, `bridge/`)
- [ ] "Dev: X" chat groups route to `session_type="dev"`
- [ ] All other messages route to `session_type="chat"`
- [ ] Hook integration e2e tests pass (DevSession creation + completion via real Redis)
- [ ] Nudge loop e2e tests pass (deliver-vs-nudge outcomes for 4+ scenarios)
- [ ] Session isolation e2e test passes (failure doesn't block queue)
- [ ] All existing tests pass (`pytest tests/ -x -q`)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete dead code (bridge/agents.py, WorkflowState, workflow_id), remove simple sessions, add Dev group routing
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write e2e tests for hook integration, nudge loop, and session isolation
  - Agent Type: test-engineer
  - Resume: true

- **Validator (all)**
  - Name: final-validator
  - Role: Verify all success criteria, run full test suite, check no simple session references remain
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update architecture docs, CLAUDE.md, tests README
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Delete bridge/agents.py and WorkflowState infrastructure
- **Task ID**: build-cleanup-dead-code
- **Depends On**: none
- **Validates**: `test ! -f bridge/agents.py && test ! -f agent/workflow_state.py && test ! -f agent/workflow_types.py`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `bridge/agents.py` entirely — `get_agent_response()` is a passthrough (inline the `get_agent_response_sdk` call); `get_agent_response_with_retry()`, `attempt_self_healing()`, `create_failure_plan()` are dead code never called by any active code path (session watchdog in `monitoring/session_watchdog.py` handles retry/recovery); tracked-work functions exist solely to feed WorkflowState which is also being deleted
- Delete `agent/workflow_state.py` and `agent/workflow_types.py`
- In `bridge/telegram_bridge.py`: replace `from bridge.agents import get_agent_response` with direct `from agent import get_agent_response_sdk` call; remove all other `bridge.agents` imports; remove `create_workflow_for_tracked_work()` call and `workflow_id` variable
- In `agent/job_queue.py`: remove `workflow_id` parameter from `enqueue_job()`, `_push_job()`, Job property; remove from `_JOB_FIELDS`. No replacement needed — `session_id` handles identity, `work_item_slug` handles plan context
- In `agent/sdk_client.py`: remove `workflow_id` parameter from `get_agent_response_sdk()` and `ValorAgent.__init__()`; remove `WorkflowState` import, `_build_workflow_context()`, `update_workflow_state()`, `get_workflow_data()`; remove workflow context injection from system prompt. Plan/phase context already available via `AgentSession.sdlc_stages` and `current_stage`
- In `models/agent_session.py`: remove `workflow_id` field. Identity covered by `session_id`/`job_id`; plan path derived from `work_item_slug`; tracking URL via `get_links()`
- In `bridge/coach.py`: update comment referencing `WorkflowState.phase` — coaching reads phase from `AgentSession.current_stage` instead
- In `bridge/catchup.py`: remove `workflow_id=None` from enqueue call
- Delete `tests/unit/test_workflow_sdk_integration.py`
- Remove any remaining imports of deleted modules

### 2. Remove simple session type
- **Task ID**: build-remove-simple
- **Depends On**: build-cleanup-dead-code (both touch agent/job_queue.py and agent/sdk_client.py)
- **Validates**: `grep -rn "simple" models/agent_session.py agent/ bridge/ | grep -v __pycache__` returns no session-type references
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `create_simple()`, `is_simple`, `SESSION_TYPE_SIMPLE` from `models/agent_session.py`
- Update `bridge/telegram_bridge.py`: replace `_session_type = "simple"` with `_session_type = "chat"`
- Update `agent/sdk_client.py`: remove the simple-session "Invoke /sdlc" enrichment branch
- Update `agent/job_queue.py`: remove `is_simple_session` branching in nudge loop
- Update affected tests (3 files, ~10 test methods)

### 3. Add "Dev: X" → DevSession routing
- **Task ID**: build-dev-routing
- **Depends On**: build-remove-simple
- **Validates**: Tests confirm "Dev: X" chat_title produces session_type="dev"
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/telegram_bridge.py`, before classifier: if `chat_title` starts with "Dev:" → set `_session_type = "dev"`, skip classifier
- Add unit tests for the routing decision

### 4. Write hook integration e2e tests
- **Task ID**: build-test-hooks
- **Depends On**: build-remove-simple
- **Validates**: `pytest tests/e2e/test_session_spawning.py -x -q` passes
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true (with task 3)
- Create `tests/e2e/test_session_spawning.py`
- Test: PreToolUse hook input → DevSession exists in Redis with correct parent_chat_session_id
- Test: SubagentStop hook → DevSession status changes to "completed"
- Test: Status guard — "failed" DevSession not overwritten to "completed"
- Test: Multiple DevSessions under one parent, all tracked
- Use real Redis, mock only Claude API

### 5. Write nudge loop e2e tests
- **Task ID**: build-test-nudge
- **Depends On**: build-remove-simple
- **Validates**: `pytest tests/e2e/test_nudge_loop.py -x -q` passes
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true (with task 3)
- Create `tests/e2e/test_nudge_loop.py`
- Test: empty output → nudge (not delivery)
- Test: rate-limited → nudge after backoff
- Test: end_turn + substantive output → deliver
- Test: MAX_NUDGE_COUNT (50) reached → deliver regardless
- Verify outcomes (was message delivered or re-enqueued), not internal branching

### 6. Write session isolation e2e test
- **Task ID**: build-test-isolation
- **Depends On**: build-remove-simple
- **Validates**: `pytest tests/e2e/test_error_boundaries.py -x -q` passes
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true (with tasks 4, 5)
- Create `tests/e2e/test_error_boundaries.py`
- Test: Session failure doesn't block other sessions in the same chat queue
- Test: Failed session gets error status, other sessions complete normally
- Test: Error emoji set on failed session

### 7. Validate cleanup
- **Task ID**: validate-cleanup
- **Depends On**: build-cleanup-dead-code, build-remove-simple, build-dev-routing
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `bridge/agents.py`, `agent/workflow_state.py`, `agent/workflow_types.py` do not exist
- Verify no `workflow_id`, `simple` session type, or `WorkflowState` references in production code
- Verify "Dev: X" routing works
- Run `pytest tests/ -x -q`

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-cleanup
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/chat-dev-session-architecture.md`
- Update `CLAUDE.md`
- Update `tests/README.md` if needed

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-test-hooks, build-test-nudge, build-test-isolation, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No dead files | `test ! -f bridge/agents.py && test ! -f agent/workflow_state.py && test ! -f agent/workflow_types.py` | exit code 0 |
| No simple session refs | `grep -rn "SESSION_TYPE_SIMPLE\|create_simple\|is_simple" models/ agent/ bridge/ --include="*.py"` | exit code 1 |
| No workflow_id refs | `grep -rn "workflow_id" models/ agent/ bridge/ --include="*.py" \| grep -v "^#" \| grep -v "# "` | exit code 1 |
| E2E tests pass | `pytest tests/e2e/ -x -q` | exit code 0 |

## Resolved Questions

1. **`get_agent_response` passthrough** — Remove entirely. Inline the `get_agent_response_sdk()` call directly in the bridge. The system should be as simple as possible.

2. **"Dev: X" routing vs classifier** — Skip classifier for Dev groups. They always go straight to DevSession. Steering and SDLC adherence is possible but not guaranteed via Dev chats.

3. **WorkflowState** — Delete entirely along with all tracked-work detection code. File-based state tracking is redundant with Redis-backed `AgentSession.sdlc_stages`. Only Agent SDK session logs (raw conversation transcripts) should be file-based.
