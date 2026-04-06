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

**Date**: 2026-04-06
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 5 total (2 blockers, 2 concerns, 1 nit)

### Blockers

#### 1. `send_to_chat` is a mid-execution callback, not a post-execution hook
- **Severity**: BLOCKER
- **Critics**: Skeptic, Adversary
- **Location**: Solution > Technical Approach, Task 3 (build-simplified-executor)
- **Finding**: The plan says "Change `send_to_chat()` to always deliver via `send_cb()`" and "Move the routing decision to `_worker_loop()` after `_execute_agent_session()` returns." However, `send_to_chat` is wired as the `_send_callback` on `BossMessenger` (agent/messenger.py:68) and is invoked by `BackgroundTask._run_work()` (agent/messenger.py:152) when the SDK returns output. The nudge-enqueue currently happens *inside* this callback, which sets `chat_state.defer_reaction = True` and `chat_state.completion_sent = True` — these flags control critical post-execution behavior at agent/agent_session_queue.py:2446-2554 (reaction setting, transcript completion, branch cleanup, session snapshot). If `send_to_chat` always delivers and routing moves to after `_execute_agent_session()` returns, the post-execution cleanup will have already run with the wrong flags (it will think the session completed normally rather than being nudged). The `_worker_loop` finally block (L1725-1758) also has a "nudge guard" that re-reads session status to avoid overwriting nudge state — this guard assumes the nudge happened *during* execution.
- **Suggestion**: The plan must address the temporal coupling between `send_to_chat` setting `chat_state` flags and the post-execution cleanup reading them. Either: (a) keep the routing decision inside `send_to_chat` but delegate to `output_router.route_session_output()` (extract logic, don't move the call site), or (b) restructure `_execute_agent_session()` so it returns *before* post-execution cleanup runs, and the caller handles both routing and cleanup based on the routing result. Option (a) is simpler and lower risk.

#### 2. `_worker_loop` nudge guard depends on mid-execution enqueue
- **Severity**: BLOCKER
- **Critics**: Adversary, Skeptic
- **Location**: Solution > Technical Approach, Data Flow > Target Flow
- **Finding**: The `_worker_loop` finally block (agent/agent_session_queue.py:1725-1758) re-reads the session from Redis after execution. If the session status is "pending" (set by `_enqueue_nudge` during execution), it skips `_complete_agent_session()` to avoid overwriting the nudge. If routing moves to *after* `_execute_agent_session()` returns, this guard will see the session as NOT pending (because `re_enqueue_session` hasn't been called yet), and will call `_complete_agent_session()`, marking the session completed *before* the router can re-enqueue it. This creates a race where the session is finalized then re-enqueued — exactly the zombie scenario Risk 2 warns about.
- **Suggestion**: The finally block's nudge guard must be updated to work with the new flow. If the router decision happens after execution but before completion, the finally block needs to know the routing result. This reinforces that option (a) from Blocker 1 is safer: keep the enqueue inside the execution scope where the guard already works correctly.

### Concerns

#### 3. Plan claims ~470 lines of nudge logic but actual count is ~345
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Problem statement
- **Finding**: The plan states "~470 lines of nudge loop logic" but `determine_delivery_action` is 46 lines (L66-111), `_enqueue_nudge` is 130 lines (L1867-1996), and `send_to_chat` routing is 169 lines (L2137-2305), totaling ~345 lines. The discrepancy suggests either inflated scope estimation or inclusion of adjacent code that isn't actually nudge-specific (e.g., the PM outbox drain at L2278-2297, which is delivery logic, not nudge logic).
- **Suggestion**: Audit the exact lines to be moved vs. lines that stay. The PM outbox drain and the enrichment code in `send_to_chat`'s deliver path are delivery concerns, not nudge concerns, and should remain in the executor.

#### 4. No rollback procedure or feature flag
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Risks, Architectural Impact
- **Finding**: The plan acknowledges "Reversibility: Medium" but provides no concrete rollback procedure. If the refactored nudge loop fails in production (sessions stalling, zombie sessions), the only recovery path is reverting the commit. For a 3000-line file that's the core execution engine, a revert may conflict with concurrent work. There's no feature flag to fall back to the old `send_to_chat` routing.
- **Suggestion**: Consider a simple feature flag (env var like `VALOR_USE_LEGACY_NUDGE=1`) that keeps the old `send_to_chat` routing as a fallback during the transition period. Remove it once validated.

### Nits

#### 5. Task 1 references `TEAMMATE_MAX_NUDGE_COUNT` import from `agent/teammate_handler.py`
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Task 1 (build-output-router)
- **Finding**: Task 1 says to "Import `TEAMMATE_MAX_NUDGE_COUNT` from `agent/teammate_handler.py` for teammate routing" into the new `output_router.py`. This creates a circular-looking dependency: the output router imports from teammate_handler, and the session queue already imports from teammate_handler. The constant (value: 10) could simply be defined in the output router since it's a routing concern, not a teammate persona concern.
- **Suggestion**: Either move the constant to the output router (canonical location for all nudge caps) or to `agent/constants.py` alongside other session constants. Minor, but cleaner.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Sequential 1-7, no gaps |
| Dependencies valid | PASS | All Depends On references resolve to valid Task IDs |
| File paths exist | PASS | 7 of 8 source files exist; `agent/output_router.py` correctly noted as new (to be created) |
| Test files exist | PASS | All 8 test files in Test Impact section exist |
| Prerequisites met | PASS | Plan states no prerequisites |
| Cross-references | PASS | Success criteria map to tasks; no-gos are not planned as work; rabbit holes are explicitly excluded |

### Verdict

**NEEDS REVISION** — 2 blockers must be resolved before build.

The core issue is that the plan proposes moving the nudge routing decision from *inside* `send_to_chat` (mid-execution callback) to *after* `_execute_agent_session()` returns (post-execution), but the post-execution cleanup logic (reactions, transcript completion, branch cleanup, nudge guard in finally block) depends on flags set by the mid-execution routing decision. The plan needs to either:

1. **Option A (recommended)**: Keep the routing call site inside `send_to_chat` but extract the logic to `agent/output_router.py`. The router module owns the decision logic; `send_to_chat` just calls it. This achieves the separation-of-concerns goal without restructuring the execution flow.
2. **Option B (higher risk)**: Fully restructure `_execute_agent_session()` to separate execution from cleanup, returning a result object that the caller uses for both routing and cleanup. This is more architecturally clean but touches more code and has higher regression risk.

The plan should be revised to explicitly address which option it takes and update the Data Flow and Technical Approach sections accordingly.

---

## Open Questions

1. **Rate-limit retry ownership**: Rate-limit backoff (`asyncio.sleep(5)` then re-enqueue) is arguably generic resilience, not PM-specific. Should it stay in the executor as a built-in retry, or move to the output router with all other nudge logic? Current plan moves it to the router for simplicity — all nudge paths in one place.

2. **Execution result shape**: The result object returned by `_execute_agent_session()` needs to carry stop_reason, last output text, session state, and auto_continue_count. Should this be a new dataclass (e.g., `ExecutionResult`), or can we reuse/extend `SendToChatResult`? Current plan: rename `SendToChatResult` → `ExecutionResult` and extend it.
