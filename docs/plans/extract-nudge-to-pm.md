---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/743
last_comment_id:
---

# Externalized Session Steering via AgentSession Model

## Problem

The session queue (`agent/agent_session_queue.py`) is a 3000-line generic executor that contains ~345 lines of hardcoded "nudge loop" logic — output routing that decides whether to deliver agent output or silently re-enqueue the session with "keep working." This creates two problems:

1. **PM-specific orchestration decisions are embedded in the generic executor.** The nudge loop knows about SDLC sessions, teammate caps, and PM pipeline stages. The executor should be persona-agnostic.

2. **No external process can steer a running session.** The nudge loop is the only steering mechanism, and it's hardcoded inside the executor. Claude Code sessions, scripts, and humans have no way to inject "stop after this stage" or "skip to review" into a running session. This was directly observed when we couldn't stop background subagents after their critique stage completed — there's no message inbox.

**Current behavior:**
- `determine_delivery_action()` (L66-111) makes persona-specific deliver-vs-nudge decisions inside the executor
- `send_to_chat()` (L2137-2305) is a mid-execution callback that routes output through 7 action paths
- `_enqueue_nudge()` (L1867-1996) re-enqueues sessions with hardcoded nudge messages
- No external process can write steering messages to a running session

**Desired outcome:**
- `AgentSession` has a `pending_messages` field that any process can write to
- The worker checks `pending_messages` between turns and injects them as the next input
- The PM writes steering messages ("keep working", "stop after critique") externally
- A clean CLI tool (`valor-session`) lets any agent spawn, steer, monitor, and stop sessions
- The executor becomes persona-agnostic — no nudge logic, no PM-specific routing

## Prior Art

- **PR #466**: Introduced the current nudge model, replacing the Observer agent. Established "bridge just nudges" but implemented it inside the executor.
- **PR #696**: Renamed `classify_nudge_action` → `determine_delivery_action`. Confirms it's a pure function, easy to relocate.
- **Issue #731**: Extracted standalone worker from bridge. Both still share nudge code.
- **Issue #741**: Added persistent worker mode with headless nudge loop.
- **`tools/valor_telegram.py`**: Example of a clean CLI tool that obscures Telethon/Redis complexity behind a simple interface. Model for `valor-session`.

## Data Flow

### Current Flow (nudge hardcoded in executor)

1. **Entry**: Telegram message → `bridge/telegram_bridge.py` → `enqueue_agent_session()`
2. **Queue**: `_worker_loop()` pops session → `_execute_agent_session()`
3. **Execute**: Claude SDK runs agent → agent produces output → SDK calls `send_to_chat()` callback
4. **Nudge decision** (embedded): `send_to_chat()` calls `determine_delivery_action()` → 7 action paths
5. **Nudge path**: `_enqueue_nudge()` → updates Redis session → `_ensure_worker()` → back to step 2
6. **Deliver path**: `send_cb()` → OutputHandler → Telegram
7. **No external steering possible** — only the executor can decide what happens next

### Target Flow (externalized steering)

1. **Entry**: Any process → `valor-session create` or `enqueue_agent_session()` → AgentSession in Redis
2. **Queue**: `_worker_loop()` pops session → `_execute_agent_session()`
3. **Execute**: Claude SDK runs agent → output delivered via OutputHandler
4. **Steering check**: Worker reads `AgentSession.pending_messages` from Redis
   - If messages exist: pop first message, set as next input, re-enqueue → back to step 2
   - If empty: check output router for automatic steering (PM auto-continue, rate-limit retry)
   - If no steering needed: session complete
5. **External steering**: Any process writes to `pending_messages` at any time:
   - PM writes "keep working" after analyzing output (replaces hardcoded nudge)
   - Human writes "stop after this stage" via `valor-session steer`
   - Another agent writes redirect instructions
6. **Output**: Always delivered via OutputHandler; steering is orthogonal to delivery

## Architectural Impact

- **Coupling**: Decreases significantly. Executor no longer knows about PM/SDLC/Teammate personas.
- **Interface changes**: New `pending_messages` field on AgentSession. New `valor-session` CLI. New public `steer_session()` / `re_enqueue_session()` APIs.
- **Data ownership**: Steering decisions move from executor (shared) to external callers (PM, tools, humans).
- **New module**: `tools/valor_session.py` — CLI tool for session management.
- **Reversibility**: Medium. The `pending_messages` field and CLI are additive. Nudge logic removal is the breaking change.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on CLI interface)
- Review rounds: 1

## Prerequisites

No prerequisites — all changes are internal.

## Solution

### Key Elements

- **`AgentSession.pending_messages`**: New ListField on the session model. Any process writes steering messages here. Worker pops and injects them between turns.
- **`valor-session` CLI** (`tools/valor_session.py`): Clean interface for session management — create, steer, status, list, kill. Modeled after `valor-telegram`.
- **Output Router** (`agent/output_router.py`): Contains `determine_delivery_action()` (moved from queue) and PM-specific auto-steering logic. Called by `send_to_chat()` internally — **call site stays inside the mid-execution callback** (per critique blocker resolution), but decision logic is extracted.
- **Public Steering API**: `steer_session(session_id, message)` writes to `pending_messages`. `re_enqueue_session()` encapsulates Redis state management and worker wake-up.

### Flow

```
valor-session create --role pm --message "Plan #735"
  → AgentSession created in Redis (status=pending)
  → Worker picks up, runs agent

valor-session steer --id abc123 --message "Stop after critique"
  → Writes to AgentSession.pending_messages in Redis
  → Worker reads pending_messages at next turn boundary
  → Injects message as next input

valor-session status --id abc123
  → Reads AgentSession from Redis, shows status/stage/output

valor-session kill --id abc123
  → Transitions session to killed status
```

### Technical Approach

**Critique blocker resolution (Option A):** Keep the routing call site inside `send_to_chat()` but delegate decision logic to `agent/output_router.py`. The `send_to_chat()` callback calls `output_router.route_session_output()` which returns an action. `send_to_chat()` executes the action (deliver, re-enqueue, drop) and sets `chat_state` flags correctly. This preserves the temporal coupling between flag-setting and post-execution cleanup.

**Pending messages mechanism:** After each agent turn, the worker checks `AgentSession.pending_messages`. If non-empty, it pops the first message, sets it as the session's `message_text`, and transitions back to pending. This replaces `_enqueue_nudge()` — the PM writes "keep working" to `pending_messages` instead of the executor hardcoding it.

**CLI design** (modeled after `valor-telegram`):
```bash
valor-session create --role pm --chat-id 123 --message "Plan issue #735"
valor-session create --role dev --message "Fix the bug" --parent abc123
valor-session steer --id abc123 --message "Stop after critique stage"
valor-session status --id abc123          # Show session state
valor-session list                        # All sessions
valor-session list --status running       # Filter by status
valor-session list --role pm              # Filter by role
valor-session kill --id abc123            # Kill a session
valor-session kill --all                  # Kill all running
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `re_enqueue_session()` preserves terminal status guards from `_enqueue_nudge()` — re-enqueue on completed/killed session is a no-op
- [ ] `steer_session()` on a terminal session returns error, does not write to `pending_messages`
- [ ] Router handles missing/None stop_reason gracefully

### Empty/Invalid Input Handling
- [ ] Empty `pending_messages` list → no steering, normal flow
- [ ] Empty output + PM session → PM auto-steering writes "keep working" to `pending_messages`
- [ ] Empty output + safety cap reached → delivers fallback message
- [ ] `valor-session steer` with empty message → rejected

### Error State Rendering
- [ ] `valor-session status` on non-existent session → clear error message
- [ ] `valor-session create` with invalid role → clear error message
- [ ] Fallback message still delivered when nudge cap reached

## Test Impact

- [ ] `tests/unit/test_nudge_loop.py` — UPDATE: change imports from `agent.agent_session_queue` to `agent.output_router`
- [ ] `tests/unit/test_qa_nudge_cap.py` — UPDATE: change imports for `MAX_NUDGE_COUNT`, `determine_delivery_action`
- [ ] `tests/unit/test_recovery_respawn_safety.py` — UPDATE: change imports for `determine_delivery_action`, `_enqueue_nudge` → `re_enqueue_session`
- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: change `_enqueue_nudge` import to `re_enqueue_session`
- [ ] `tests/unit/test_duplicate_delivery.py` — UPDATE: adjust for new delivery model
- [ ] `tests/e2e/test_nudge_loop.py` — UPDATE: change imports for `MAX_NUDGE_COUNT`, `SendToChatResult`
- [ ] `tests/integration/test_stage_aware_auto_continue.py` — UPDATE: change `MAX_NUDGE_COUNT` import
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: change `_enqueue_nudge` reference

## Rabbit Holes

- **Splitting the summarizer's `nudge_feedback`**: Separate concept (LLM-generated completion rejection feedback). Do NOT move or modify.
- **Making the router a Claude Code hook**: Hooks run as shell subprocesses — can't call async Python APIs. Router must be a Python module called within `send_to_chat()`.
- **Refactoring `_execute_agent_session()` beyond nudge extraction**: The 500+ line function has many concerns. Only extract nudge-related parts.
- **Building a generic per-persona plugin system**: Overkill. Simple if/elif on session_type in the router.
- **Post-execution routing (critique blocker)**: Moving the routing call site to after `_execute_agent_session()` returns breaks `chat_state` flag coupling and the `_worker_loop` nudge guard. Keep call site inside `send_to_chat()`.

## Risks

### Risk 1: Worker regression
**Impact:** Standalone worker stops auto-continuing sessions.
**Mitigation:** Worker uses same `send_to_chat()` → output router path. Integration test validates end-to-end. Consider env var `VALOR_USE_LEGACY_NUDGE=1` as fallback during transition.

### Risk 2: Race in pending_messages
**Impact:** Multiple processes write to `pending_messages` simultaneously, messages lost or duplicated.
**Mitigation:** Redis list operations (LPUSH/RPOP) are atomic. Use Redis list primitives, not read-modify-write on a JSON field.

### Risk 3: `send_to_chat` closure coupling
**Impact:** Extracting decision logic from `send_to_chat()` while keeping the call site requires passing context.
**Mitigation:** `route_session_output()` receives all needed context as parameters (output, stop_reason, session_type, classification_type, auto_continue_count, watchdog status). Pure function, no closure access needed.

## Race Conditions

### Race 1: Terminal status change during steering
**Location:** `steer_session()` / output router, between reading session status and writing `pending_messages`
**Trigger:** Another process finalizes the session while steering is in progress
**Data prerequisite:** Session must be non-terminal when steering starts
**State prerequisite:** Redis session record must reflect current status
**Mitigation:** `steer_session()` reads status first; if terminal, returns error. `re_enqueue_session()` re-reads before modifying. Atomic Redis operations prevent partial writes.

### Race 2: Pending message consumed while new one being written
**Location:** Worker popping from `pending_messages` while external process pushes
**Trigger:** Concurrent RPOP (worker) and LPUSH (external caller)
**Mitigation:** Redis list operations are atomic. RPOP and LPUSH are safe concurrent operations. No lock needed.

## No-Gos (Out of Scope)

- Modifying `bridge/summarizer.py` `nudge_feedback` — separate concept
- Restructuring `_execute_agent_session()` beyond extracting nudge decision logic
- Moving the `send_to_chat()` call site to post-execution (critique blocker)
- Changing DevSession output routing (uses `subagent_stop.py` hook)
- Modifying the OutputHandler protocol
- Building a web UI for session management (CLI only for now)

## Update System

The `valor-session` CLI tool needs to be available on all machines. Add to the update skill:
- Install/symlink `valor-session` alongside `valor-telegram`
- No new dependencies beyond what's already installed (Redis, popoto)

## Agent Integration

- `valor-session` CLI must be registered as an MCP tool or made available to agents so they can spawn and steer sessions
- Consider adding to `.mcp.json` if agents need to call it programmatically
- Bridge does NOT need changes — it continues using `enqueue_agent_session()` as today
- The CLI is the primary interface for external callers (Claude Code, scripts, humans)

## Documentation

- [ ] Create `docs/features/session-steering.md` describing the externalized steering architecture
- [ ] Update `CLAUDE.md` architecture section to show `pending_messages` flow and `valor-session` CLI
- [ ] Update `CLAUDE.md` Quick Commands table with `valor-session` commands
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add `valor-session` to `docs/tools-reference.md`

## Success Criteria

- [ ] `AgentSession` model has `pending_messages` field (Redis list)
- [ ] Worker checks `pending_messages` between turns and injects messages
- [ ] `determine_delivery_action()` and routing constants moved to `agent/output_router.py`
- [ ] `send_to_chat()` delegates to `output_router.route_session_output()` (call site preserved, logic extracted)
- [ ] PM auto-continue works via `pending_messages` (PM writes "keep working" externally)
- [ ] `valor-session create/steer/status/list/kill` CLI works
- [ ] Public `steer_session()` and `re_enqueue_session()` APIs exist
- [ ] Teammate sessions use reduced nudge cap via persona-scoped code
- [ ] Rate-limit retry and empty-output retry still function
- [ ] All tests pass
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (steering-model)**
  - Name: model-builder
  - Role: Add `pending_messages` to AgentSession, implement steering check in worker, extract output router
  - Agent Type: builder
  - Resume: true

- **Builder (cli-tool)**
  - Name: cli-builder
  - Role: Create `valor-session` CLI tool with create/steer/status/list/kill commands
  - Agent Type: builder
  - Resume: true

- **Builder (test-migration)**
  - Name: test-migrator
  - Role: Update all test imports and assertions to match new module locations
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end steering, PM auto-continue, CLI commands, teammate cap
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create session-steering docs, update CLAUDE.md, tools reference
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add pending_messages to AgentSession model
- **Task ID**: build-pending-messages
- **Depends On**: none
- **Validates**: tests/unit/test_pending_messages.py (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `pending_messages` as a ListField on `AgentSession` in `models/agent_session.py`
- Implement `steer_session(session_id, message)` in `agent/agent_session_queue.py` — pushes to `pending_messages`, validates non-terminal status
- Implement pending message consumption in the worker: after each agent turn, check `pending_messages`; if non-empty, pop first message, set as `message_text`, transition to pending
- Write unit tests for steer_session (happy path, terminal guard, concurrent access)

### 2. Extract output router module
- **Task ID**: build-output-router
- **Depends On**: none
- **Validates**: tests/unit/test_nudge_loop.py (update imports)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true (with task 1)
- Move `determine_delivery_action()` to `agent/output_router.py` (pure function, no changes)
- Move `MAX_NUDGE_COUNT`, `NUDGE_MESSAGE`, `SendToChatResult` constants to `agent/output_router.py`
- Create `route_session_output()` wrapping the persona-specific routing logic
- Define all nudge cap constants in the router (including teammate cap of 10)
- Keep `send_to_chat()` call site inside `_execute_agent_session()` — it calls `output_router.route_session_output()` and executes the returned action

### 3. Wire steering into executor
- **Task ID**: build-wire-steering
- **Depends On**: build-pending-messages, build-output-router
- **Validates**: tests/unit/test_duplicate_delivery.py (update), tests/e2e/test_nudge_loop.py (update)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `_enqueue_nudge()` calls in `send_to_chat()` with writes to `pending_messages` + `re_enqueue_session()`
- Extract `_enqueue_nudge()` internals into public `re_enqueue_session()` with terminal status guards preserved
- Update `send_to_chat()` to delegate routing to `output_router.route_session_output()`
- Ensure `chat_state` flags are set correctly by the routing action (preserving post-execution cleanup coupling)

### 4. Create valor-session CLI tool
- **Task ID**: build-cli-tool
- **Depends On**: build-pending-messages
- **Validates**: tests/unit/test_valor_session_cli.py (create)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true (with task 3)
- Create `tools/valor_session.py` modeled after `tools/valor_telegram.py`
- Implement subcommands: create, steer, status, list, kill
- `create`: builds AgentSession in Redis, calls `_ensure_worker()` or enqueues
- `steer`: calls `steer_session()` to write to `pending_messages`
- `status`: reads and formats AgentSession fields (status, stage_states, auto_continue_count)
- `list`: queries AgentSession by status/role with table output
- `kill`: transitions session to killed status
- Add `valor-session` entry point (symlink or script)

### 5. Migrate tests
- **Task ID**: build-test-migration
- **Depends On**: build-wire-steering
- **Validates**: all test files listed in Test Impact section
- **Assigned To**: test-migrator
- **Agent Type**: builder
- **Parallel**: false
- Update imports in all 8 test files from `agent.agent_session_queue` to `agent.output_router` for moved symbols
- Update `_enqueue_nudge` references to `re_enqueue_session`
- Add tests for `pending_messages` consumption in worker
- Add tests for `valor-session` CLI commands
- Run full test suite

### 6. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-test-migration, build-cli-tool
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify PM SDLC auto-continue via `pending_messages`
- Verify external steering: `valor-session steer` injects message that worker consumes
- Verify `valor-session create --role pm` spawns working session
- Verify teammate cap, rate-limit retry, empty-output retry
- Verify terminal guard: steering a completed session returns error
- Verify `valor-session kill` stops a running session
- Run: `pytest tests/ -x -q`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/session-steering.md`
- Update CLAUDE.md architecture section and Quick Commands table
- Add `valor-session` to `docs/tools-reference.md`
- Add entry to `docs/features/README.md`

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format: `python -m ruff format --check .`
- Verify no remaining imports of nudge symbols from `agent.agent_session_queue`
- Verify `valor-session --help` works
- Verify `agent/output_router.py` exports expected symbols

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No nudge imports from queue | `grep -rn 'from agent.agent_session_queue import.*determine_delivery_action\|from agent.agent_session_queue import.*NUDGE_MESSAGE\|from agent.agent_session_queue import.*_enqueue_nudge' tests/ agent/` | exit code 1 |
| Output router exists | `python -c "from agent.output_router import determine_delivery_action, route_session_output, MAX_NUDGE_COUNT"` | exit code 0 |
| Steering API exists | `python -c "from agent.agent_session_queue import steer_session, re_enqueue_session"` | exit code 0 |
| CLI works | `python -m tools.valor_session --help` | exit code 0 |
| pending_messages field | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'pending_messages')"` | exit code 0 |

## Critique Results

**Date**: 2026-04-06
**Verdict**: NEEDS REVISION → **REVISED** (blockers addressed, scope expanded)

### Blocker Resolutions

1. **`send_to_chat` mid-execution callback** → Resolved: keep call site inside `send_to_chat()`, extract decision logic to `agent/output_router.py`. `chat_state` flag coupling preserved.
2. **`_worker_loop` nudge guard** → Resolved: routing still happens during execution (inside `send_to_chat`), so the finally block guard sees the correct status.

### Incorporated Feedback

3. **Line count** → Corrected to ~345 lines. PM outbox drain stays in executor.
4. **Rollback** → Consider `VALOR_USE_LEGACY_NUDGE=1` env var during transition.
5. **Teammate cap** → All nudge cap constants defined in output router module.

---

## Open Questions

1. **`pending_messages` storage**: Should this be a Popoto ListField on AgentSession, or a separate Redis list (`session:steering:{session_id}`)? A separate list gives atomic LPUSH/RPOP without Popoto's read-modify-write. A model field keeps everything on the session record. Leaning toward separate Redis list with a helper method on the model.
