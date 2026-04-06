---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/743
last_comment_id:
---

# Extract Nudge Loop from Session Queue into PM-Scoped Output Router

## Problem

The session queue (`agent/agent_session_queue.py`) is a 3000-line generic executor that processes agent sessions regardless of persona type (PM, Teammate, Dev). However, it contains ~470 lines of "nudge loop" logic — output routing that decides whether to deliver agent output to Telegram or silently re-enqueue the session with a "keep working" message.

This nudge logic makes PM-specific orchestration decisions (e.g., "Is this a PM running SDLC work? Keep it going through pipeline stages") inside a generic executor, violating the architecture principle that the bridge/queue is a dumb pipe and the PM owns orchestration intelligence.

**Current behavior:**
- `determine_delivery_action()` (L66-111) decides deliver vs. nudge, including PM-specific `nudge_continue` for SDLC sessions
- `send_to_chat()` (L2137-2305) is a 170-line nested closure routing output through 7 action paths
- `_enqueue_nudge()` (L1867-1996) re-enqueues sessions with nudge messages, managing Redis state
- The standalone worker shares the same nudge loop, coupling it to PM orchestration logic

**Desired outcome:**
- Session queue is a generic executor: run agent, return output, deliver via OutputHandler
- Nudge intelligence is owned by persona-specific code (PM router, teammate handler)
- Each persona type can define its own output routing without touching the shared executor

## Prior Art

- **PR #466**: [SDLC Redesign Phase 2: Nudge loop, per-chat queue, Observer deletion](https://github.com/tomcounsell/ai/pull/466) — Introduced the current nudge model, replacing the Observer agent. Established the "bridge just nudges" principle, but implemented it inside the executor rather than the PM.
- **PR #696**: [Rename classify_nudge_action to determine_delivery_action](https://github.com/tomcounsell/ai/pull/696) — Naming cleanup. Confirms the function is a pure decision function, easy to relocate.
- **Issue #731**: [Extract standalone worker from bridge monolith](https://github.com/tomcounsell/ai/issues/731) — Separated worker from bridge. Both still share nudge code in `agent_session_queue.py`.
- **Issue #741**: [Worker persistent event loop with headless nudge loop](https://github.com/tomcounsell/ai/issues/741) — Added persistent mode to worker. Worker shares same nudge logic.

## Data Flow

### Current Flow (nudge embedded in executor)

1. **Entry**: Telegram message → `bridge/telegram_bridge.py` → `enqueue_agent_session()`
2. **Queue**: `_worker_loop()` pops session → `_execute_agent_session()`
3. **Execute**: Claude SDK runs agent → agent produces output → SDK calls `send_to_chat()` callback
4. **Nudge decision** (embedded): `send_to_chat()` calls `determine_delivery_action()` → 7 action paths
5. **Nudge path**: `_enqueue_nudge()` → updates Redis session → `_ensure_worker()` → back to step 2
6. **Deliver path**: `send_cb()` → OutputHandler → Telegram

### Target Flow (nudge as post-execution router)

1. **Entry**: Telegram message → `bridge/telegram_bridge.py` → `enqueue_agent_session()`
2. **Queue**: `_worker_loop()` pops session → `_execute_agent_session()`
3. **Execute**: Claude SDK runs agent → agent produces output → always delivered via OutputHandler
4. **Post-execution**: `_execute_agent_session()` returns execution result (output, stop_reason, session state)
5. **Output router**: Persona-specific router inspects result:
   - PM router: decides deliver-to-user vs re-enqueue based on SDLC state, empty output, rate limits
   - Teammate router: uses reduced cap, simpler logic
   - Dev sessions: no routing needed (returns to parent PM via `subagent_stop.py`)
6. **Re-enqueue path**: Router calls public `re_enqueue_session()` API → back to step 2
7. **Deliver path**: Output already delivered in step 3; router just decides whether to also re-enqueue

## Architectural Impact

- **Coupling**: Decreases. Session queue no longer needs to know about PM/SDLC/Teammate personas.
- **Interface changes**: New public `re_enqueue_session()` API on the queue. `_execute_agent_session()` returns a result object instead of handling delivery internally.
- **Data ownership**: Nudge decision ownership moves from queue (shared) to persona handlers (scoped).
- **New dependencies**: `agent/output_router.py` (new module) depends on queue's public API + session model.
- **Reversibility**: Medium. The public API is additive; the removal of nudge logic from the queue is the breaking change. Tests are the main migration cost.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on router placement)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal refactoring.

## Solution

### Key Elements

- **Output Router Module** (`agent/output_router.py`): Contains `determine_delivery_action()` (moved, unchanged) and persona-specific router functions that decide deliver vs. re-enqueue after session execution.
- **Public Re-enqueue API**: `re_enqueue_session()` on the session queue — encapsulates Redis state management, terminal guards, and worker wake-up so routers don't reach into internals.
- **Simplified Executor**: `_execute_agent_session()` returns an execution result; `send_to_chat()` becomes a simple always-deliver passthrough.
- **Post-execution routing hook**: After `_execute_agent_session()` completes, the worker loop calls the output router to decide whether to re-enqueue.

### Flow

**Message arrives** → Queue pops session → Executor runs agent → Output delivered to OutputHandler → Worker loop calls output router → Router decides: done (exit) or re-enqueue (loop) → If re-enqueue: `re_enqueue_session()` → back to queue

### Technical Approach

- Move `determine_delivery_action()` as-is to `agent/output_router.py` (it's already a pure function)
- Create `route_session_output()` in `agent/output_router.py` that wraps the decision logic with persona-specific behavior (PM SDLC continuation, teammate cap, rate-limit retry)
- Extract `_enqueue_nudge()` internals into public `re_enqueue_session(session_id, message, auto_continue_count)` on the queue
- Change `send_to_chat()` to always call `send_cb()` — no routing, no nudge
- Move the routing decision to `_worker_loop()` after `_execute_agent_session()` returns, calling `route_session_output()` to decide whether to loop

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `re_enqueue_session()` must preserve terminal status guards from `_enqueue_nudge()` — test that calling re-enqueue on a completed/killed/failed session is a no-op
- [ ] Router must handle missing/None stop_reason gracefully (already handled by `determine_delivery_action`)

### Empty/Invalid Input Handling
- [ ] Empty output + PM session → router re-enqueues (not delivers empty string)
- [ ] Empty output + safety cap reached → router delivers fallback message
- [ ] None/whitespace output treated same as empty

### Error State Rendering
- [ ] Fallback message ("The task completed but produced no output") still delivered when cap reached
- [ ] Watchdog unhealthy path still forces delivery

## Test Impact

- [ ] `tests/unit/test_nudge_loop.py` — UPDATE: change imports from `agent.agent_session_queue` to `agent.output_router`
- [ ] `tests/unit/test_qa_nudge_cap.py` — UPDATE: change imports for `MAX_NUDGE_COUNT`, `determine_delivery_action`
- [ ] `tests/unit/test_recovery_respawn_safety.py` — UPDATE: change imports for `determine_delivery_action`, `_enqueue_nudge` → `re_enqueue_session`
- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: change `_enqueue_nudge` import to `re_enqueue_session`
- [ ] `tests/unit/test_duplicate_delivery.py` — UPDATE: adjust for new delivery model (always-deliver + post-routing)
- [ ] `tests/e2e/test_nudge_loop.py` — UPDATE: change imports for `MAX_NUDGE_COUNT`, `SendToChatResult`
- [ ] `tests/integration/test_stage_aware_auto_continue.py` — UPDATE: change `MAX_NUDGE_COUNT` import
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: change `_enqueue_nudge` reference

## Rabbit Holes

- **Splitting the summarizer's `nudge_feedback`**: The summarizer has its own `nudge_feedback` field for LLM-generated completion rejection feedback. This is a completely separate concept from the auto-continue nudge and must NOT be moved or modified.
- **Making the router a Claude Code hook**: Hooks in `.claude/hooks/` run as shell subprocesses — they can't call async Python APIs like `re_enqueue_session()`. The router must be a Python module called directly by the worker loop, not a hook.
- **Refactoring `_execute_agent_session()`**: The 500+ line function has many concerns beyond nudging. Only extract the nudge-related parts; don't try to refactor the entire function.
- **Per-persona hook system**: Building a generic hook system where personas register output handlers. Overkill — just use if/elif on session_type in the router.

## Risks

### Risk 1: Worker regression
**Impact:** Standalone worker stops auto-continuing sessions, causing sessions to stall after first turn.
**Mitigation:** Worker uses the same `_worker_loop()` → output router path as the bridge. Integration test with `test_stage_aware_auto_continue.py` validates end-to-end.

### Risk 2: Race in re-enqueue
**Impact:** Session finalized by one process while router tries to re-enqueue, creating zombie sessions.
**Mitigation:** `re_enqueue_session()` inherits the same terminal status guards (entry + re-read) from `_enqueue_nudge()`. No new race surfaces.

### Risk 3: `send_to_chat` closure coupling
**Impact:** Extracting `send_to_chat()` from its nested closure breaks access to scoped variables (`session`, `branch_name`, `task_list_id`, `agent_session`, `chat_state`).
**Mitigation:** Change `_execute_agent_session()` to return a result object containing stop_reason, output, and session state. The router receives this object instead of being embedded in the closure.

## Race Conditions

### Race 1: Terminal status change during routing
**Location:** Output router, between reading session status and calling `re_enqueue_session()`
**Trigger:** Another process (watchdog, human kill) finalizes the session while the router is deciding
**Data prerequisite:** Session must be non-terminal when router starts
**State prerequisite:** Redis session record must reflect current status
**Mitigation:** `re_enqueue_session()` re-reads session from Redis before modifying — same double-check pattern as existing `_enqueue_nudge()`. Terminal status at re-read time → no-op.

## No-Gos (Out of Scope)

- Modifying `bridge/summarizer.py` `nudge_feedback` — separate concept, separate system
- Refactoring `_execute_agent_session()` beyond extracting nudge concerns
- Building a generic per-persona plugin/hook system
- Changing DevSession output routing (already uses `subagent_stop.py` hook)
- Modifying the OutputHandler protocol

## Update System

No update system changes required — this is a pure internal refactoring. No new dependencies, config files, or migration steps. The worker process uses the same code path and will pick up the changes on next restart.

## Agent Integration

No agent integration required — this is an internal refactoring of the session execution layer. No new tools, MCP servers, or bridge imports needed.

## Documentation

- [ ] Update `docs/features/nudge-loop.md` (if exists) or create it to document the new output router architecture
- [ ] Update architecture section in `CLAUDE.md` to reflect that nudge intelligence lives in `agent/output_router.py`, not the session queue
- [ ] Add entry to `docs/features/README.md` index table for the output router

## Success Criteria

- [ ] `agent/agent_session_queue.py` has no `determine_delivery_action()`, no nudge-specific `send_to_chat()` routing, no `_enqueue_nudge()`
- [ ] `agent/output_router.py` exists with `determine_delivery_action()`, `route_session_output()`, and constants
- [ ] Public `re_enqueue_session()` API exists on session queue
- [ ] PM SDLC auto-continue works end-to-end (session progresses through pipeline stages without human intervention)
- [ ] Teammate sessions use reduced nudge cap (10) via teammate-scoped code
- [ ] Rate-limit retry and empty-output retry still function
- [ ] All tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (output-router)**
  - Name: router-builder
  - Role: Create `agent/output_router.py`, extract nudge logic, add public re-enqueue API, simplify executor
  - Agent Type: builder
  - Resume: true

- **Builder (test-migration)**
  - Name: test-migrator
  - Role: Update all test imports and assertions to match new module locations
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end nudge behavior, PM auto-continue, teammate cap, rate-limit retry
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update CLAUDE.md architecture section, create/update feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create output router module
- **Task ID**: build-output-router
- **Depends On**: none
- **Validates**: tests/unit/test_nudge_loop.py (update imports)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Move `determine_delivery_action()` to `agent/output_router.py` (pure function, no changes needed)
- Move `MAX_NUDGE_COUNT`, `NUDGE_MESSAGE`, `SendToChatResult` constants to `agent/output_router.py`
- Create `route_session_output(session, output, stop_reason, auto_continue_count, ...)` that contains the persona-specific routing logic currently in `send_to_chat()`
- Import `TEAMMATE_MAX_NUDGE_COUNT` from `agent/teammate_handler.py` for teammate routing

### 2. Add public re-enqueue API
- **Task ID**: build-re-enqueue-api
- **Depends On**: none
- **Validates**: tests/unit/test_recovery_respawn_safety.py (update)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true (with task 1)
- Extract `_enqueue_nudge()` internals into public `async re_enqueue_session(session_id, message, auto_continue_count, task_list_id, branch_name)` in `agent/agent_session_queue.py`
- Preserve all terminal status guards (entry check + re-read)
- Preserve fallback recreate path for missing sessions
- Keep `_ensure_worker()` call at the end

### 3. Simplify executor and wire router
- **Task ID**: build-simplified-executor
- **Depends On**: build-output-router, build-re-enqueue-api
- **Validates**: tests/unit/test_duplicate_delivery.py, tests/e2e/test_nudge_loop.py (update)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `send_to_chat()` in `_execute_agent_session()` to always deliver via `send_cb()` — remove all 7-path routing
- Have `_execute_agent_session()` return an execution result object (stop_reason, last_output, session state)
- In `_worker_loop()`, after `_execute_agent_session()` returns, call `route_session_output()` from the output router
- If router returns "re-enqueue", call `re_enqueue_session()` and loop; otherwise exit

### 4. Migrate tests
- **Task ID**: build-test-migration
- **Depends On**: build-simplified-executor
- **Validates**: all test files listed in Test Impact section
- **Assigned To**: test-migrator
- **Agent Type**: builder
- **Parallel**: false
- Update imports in all 8 test files from `agent.agent_session_queue` to `agent.output_router` for moved symbols
- Update `_enqueue_nudge` references to `re_enqueue_session`
- Adjust test assertions that depend on `send_to_chat` behavior (now always-deliver)
- Run full test suite to verify

### 5. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-test-migration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify PM SDLC auto-continue: session with `session_type=pm, classification_type=sdlc` gets re-enqueued
- Verify teammate cap: teammate sessions cap at 10 nudges
- Verify rate-limit retry: `stop_reason=rate_limited` triggers backoff + re-enqueue
- Verify empty output retry: empty/whitespace output triggers re-enqueue
- Verify terminal guard: completed/killed sessions cannot be re-enqueued
- Verify watchdog override: unhealthy sessions force delivery
- Run: `pytest tests/unit/test_nudge_loop.py tests/unit/test_qa_nudge_cap.py tests/unit/test_recovery_respawn_safety.py tests/integration/test_stage_aware_auto_continue.py -v`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create/update `docs/features/output-router.md` documenting the PM output router architecture
- Update CLAUDE.md architecture diagram to show output router as separate from session queue
- Add entry to `docs/features/README.md`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format check: `python -m ruff format --check .`
- Verify no remaining imports of nudge symbols from `agent.agent_session_queue`
- Verify `agent/output_router.py` exists and exports expected symbols

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No nudge imports from queue | `grep -rn 'from agent.agent_session_queue import.*determine_delivery_action\|from agent.agent_session_queue import.*NUDGE_MESSAGE\|from agent.agent_session_queue import.*_enqueue_nudge' tests/ agent/` | exit code 1 |
| Output router exists | `python -c "from agent.output_router import determine_delivery_action, route_session_output, MAX_NUDGE_COUNT"` | exit code 0 |
| Re-enqueue API exists | `python -c "from agent.agent_session_queue import re_enqueue_session"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Rate-limit retry ownership**: Rate-limit backoff (`asyncio.sleep(5)` then re-enqueue) is arguably generic resilience, not PM-specific. Should it stay in the executor as a built-in retry, or move to the output router with all other nudge logic? Current plan moves it to the router for simplicity — all nudge paths in one place.

2. **Execution result shape**: The result object returned by `_execute_agent_session()` needs to carry stop_reason, last output text, session state, and auto_continue_count. Should this be a new dataclass (e.g., `ExecutionResult`), or can we reuse/extend `SendToChatResult`? Current plan: rename `SendToChatResult` → `ExecutionResult` and extend it.
