---
status: Planning
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
1. `bridge/agents.py` contains ~190 lines of dead retry/self-healing code (lines 1-193) that nothing calls — the bridge imports `get_agent_response()` which is a thin passthrough to `sdk_client.get_agent_response_sdk()`. The file also contains ~190 lines of live tracked-work-detection code (lines 195-387) that the bridge actively uses.
2. Three session types exist (`chat`, `dev`, `simple`) but `simple` serves no distinct purpose. The bridge routes non-SDLC messages to `simple`, then `sdk_client.py` tells simple sessions to "Invoke /sdlc immediately" — contradicting the direct-delivery intent.
3. No "Dev: X" chat group → DevSession routing exists. Currently all messages go through the classifier → ChatSession or simple path.
4. Phase 2 e2e tests (hook integration, nudge loop, session isolation) don't exist.

**Desired outcome:**
- Dead code deleted, live code relocated
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

- **Interface changes**: `AgentSession.create_simple()` removed; bridge routing simplified from classifier-based to chat_title-based for Dev groups
- **Coupling**: Decreases — removes the simple/chat/dev three-way branch in sdk_client.py, replaces with two-way
- **Data ownership**: No change — AgentSession model still owns session state in Redis
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

- **Dead code removal**: Delete retry/self-healing functions from `bridge/agents.py`, relocate live tracked-work functions to `bridge/tracked_work.py`
- **Simple session removal**: Delete `create_simple()`, `is_simple`, `SESSION_TYPE_SIMPLE` from model; update bridge routing and sdk_client branching
- **Dev group routing**: "Dev: X" chat title prefix → `session_type="dev"` in bridge, bypassing classifier
- **E2E tests**: Three new test files verifying behavior at system boundaries

### Flow

**Cleanup flow:**
`bridge/agents.py` dead code deleted → live code moved to `bridge/tracked_work.py` → imports updated in `telegram_bridge.py` → simple session references removed from model/bridge/sdk_client/job_queue → "Dev: X" routing added to bridge

**Test flow:**
Hook test: simulate PreToolUse input → verify DevSession exists in Redis with parent linkage → simulate SubagentStop → verify status is "completed"

Nudge test: create session → call send_to_chat with various (stop_reason, output) combos → verify deliver vs nudge outcome

Isolation test: enqueue two sessions on same chat → first session fails → second session still processes

### Technical Approach

- Relocate tracked-work code to its own module rather than leaving in a gutted `bridge/agents.py` — the file name "agents" no longer describes what's in it
- For "Dev: X" routing: detect at bridge level before classifier runs, set `session_type="dev"` directly. The classifier is irrelevant for dev groups — they always get a DevSession
- For e2e tests: mock only `get_agent_response_sdk` (Claude API boundary) and Telegram client. Use real Redis. Verify outcomes not internals.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/agents.py` deletion removes dead exception handlers — no new ones introduced
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

## Rabbit Holes

- **Re-implementing retry/self-healing** — The dead code in `bridge/agents.py` represents a previous design. Don't rebuild it; the session watchdog (`monitoring/session_watchdog.py`) already handles recovery with exponential backoff and is well-tested (800+ lines of tests)
- **Testing the nudge loop's internal branching** — Test deliver-vs-nudge *outcomes*, not the `if/elif` structure inside `send_to_chat()`. The test should survive a refactor of the branching logic
- **Steering queue in nudge tests** — Steering and nudging are separate systems. Don't conflate them in a single test file. Steering injection is already tested in `tests/integration/test_steering.py` (32 tests)

## Risks

### Risk 1: `bridge/agents.py` has live code mixed with dead code
**Impact:** Deleting the whole file breaks tracked-work detection used by the bridge
**Mitigation:** Relocate live functions (`detect_tracked_work`, `create_workflow_for_tracked_work`, `_match_plan_by_name`, `_detect_issue_number`, `_get_github_repo_url`) to `bridge/tracked_work.py` before deleting the original file. Update imports in `telegram_bridge.py`.

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

- [ ] `bridge/agents.py` does not exist
- [ ] `bridge/tracked_work.py` exists with relocated live functions
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
  - Role: Delete dead code, relocate tracked-work functions, remove simple sessions, add Dev group routing
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

### 1. Relocate tracked-work code and delete bridge/agents.py
- **Task ID**: build-cleanup-agents
- **Depends On**: none
- **Validates**: `python -c "from bridge.tracked_work import detect_tracked_work, create_workflow_for_tracked_work"` succeeds; `bridge/agents.py` does not exist
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/tracked_work.py` with functions: `detect_tracked_work`, `create_workflow_for_tracked_work`, `_match_plan_by_name`, `_detect_issue_number`, `_get_github_repo_url` (and their imports)
- Move `get_agent_response` passthrough to `bridge/telegram_bridge.py` inline or remove if only called once
- Update all imports in `bridge/telegram_bridge.py` to point to `bridge.tracked_work`
- Delete `bridge/agents.py`

### 2. Remove simple session type
- **Task ID**: build-remove-simple
- **Depends On**: none
- **Validates**: `grep -rn "simple" models/agent_session.py agent/ bridge/ | grep -v __pycache__` returns no session-type references
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true (with task 1)
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
- **Depends On**: build-cleanup-agents, build-remove-simple, build-dev-routing
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `bridge/agents.py` does not exist
- Verify `bridge/tracked_work.py` exists and is importable
- Verify no `simple` session type references in production code
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
| No bridge/agents.py | `test ! -f bridge/agents.py` | exit code 0 |
| No simple session refs | `grep -rn "SESSION_TYPE_SIMPLE\|create_simple\|is_simple" models/ agent/ bridge/ --include="*.py"` | exit code 1 |
| tracked_work importable | `python -c "from bridge.tracked_work import detect_tracked_work"` | exit code 0 |
| E2E tests pass | `pytest tests/e2e/ -x -q` | exit code 0 |

## Open Questions

1. **`get_agent_response` passthrough** — `bridge/agents.py` defines `get_agent_response()` which just calls `get_agent_response_sdk()`. The bridge imports it. Should we inline this call in `telegram_bridge.py` (removing the indirection), or keep the wrapper somewhere? My recommendation: inline it — one less hop.

2. **"Dev: X" routing vs classifier** — For Dev groups, should we skip the classifier entirely (just set `session_type="dev"` and enqueue), or still run it for metadata? My recommendation: skip it — Dev groups always get a DevSession, classification adds no value there.

3. **WorkflowState** — The tracked-work code depends on `agent/workflow_state.py`. Is WorkflowState still actively used, or is it also dead? If dead, we could delete the tracked-work code entirely instead of relocating it.
