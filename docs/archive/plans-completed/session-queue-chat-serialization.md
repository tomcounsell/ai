---
status: docs_complete
type: bug
appetite: Small
owner: Dev
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/785
last_comment_id: none
---

# Session Queue Chat Serialization

## Problem

Two `AgentSession` records with the same `chat_id` ran concurrently, violating the FIFO sequential guarantee. Sessions `eae2c17570454c85985524ea8f02e1cd` ("Finish the rest of SDLC") and `e5e74ac08f0d480bbf2f203fff1ddd12` ("Run SDLC on issues 777, 775, 776") were both `status=running` simultaneously on `chat_id=-1003449100931`.

**Current behavior:** After worker restart with N recovered sessions on the same `chat_id`, each session triggers `_ensure_worker()` independently. The in-process dedup check in `_ensure_worker()` (`_active_workers.get(chat_id)`) is correct for steady-state — but at startup, the pending sessions are iterated in `worker/__main__.py` (line 197-201) and `_ensure_worker()` is called once per unique `chat_id` using a local `started_chats` set. The health check loop (lines 1219, 1271 in `agent_session_queue.py`) also calls `_ensure_worker()` per-session for both running and pending sessions — and if two sessions share a `chat_id`, both paths can call `_ensure_worker()` before the first task registers itself in `_active_workers`.

**Desired outcome:** Exactly one worker loop runs per `chat_id` at any time. Sessions on the same chat are always processed strictly sequentially.

**Impact:** Concurrent PM sessions on the same chat can both modify the same GitHub branch, write conflicting plan documents, and produce garbled Telegram output.

## Prior Art

- **Issue #738**: `fix: stale session cleanup kills live sessions and corrupts state on forced termination` — addressed a different race in session lifecycle transitions. Established the pattern of timing guards (`AGENT_SESSION_HEALTH_MIN_RUNNING`) to protect recently-started sessions.
- **Issue #705**: `Bug: rapid-fire messages bypass coalescing — race condition + semantic routing disabled` — tackled a different concurrency issue (coalescing rapid messages). Not directly related.

No prior attempts to fix the `_ensure_worker()` duplicate-spawn bug found.

## Data Flow

1. **Worker restart** → `_recover_interrupted_agent_sessions_startup()` resets N running sessions to `pending`
2. **Step 5 in `worker/__main__.py`** → iterates all pending sessions, calls `_ensure_worker(chat_id)` once per unique `chat_id` (guarded by `started_chats` set) — this path is already correct
3. **`_agent_session_health_check()`** → runs shortly after startup in background; iterates running sessions (none now, all reset to pending) and then pending sessions — for each pending session without a live worker, calls `_ensure_worker(worker_key)` independently — this is the vulnerable path if two sessions share a `chat_id` and neither has a live worker yet
4. **`_ensure_worker(chat_id)`** → checks `_active_workers.get(chat_id)` for existing non-done task — if two calls arrive before either task is registered, both pass the guard and create duplicate tasks
5. **Two concurrent `_worker_loop` tasks** → each pops from the same `chat_id` pending queue independently, running sessions in parallel

## Architectural Impact

- **No schema changes**: `AgentSession` model, `_active_workers` dict, and `_worker_loop` are unchanged
- **Interface changes**: `_ensure_worker()` gains idempotency guarantee; callers remain unchanged
- **Coupling**: None — fix is local to `agent_session_queue.py` and `worker/__main__.py`
- **Reversibility**: Easy — the `_starting_workers` guard set can be removed if needed

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this fix requires no external dependencies or environment changes.

## Solution

### Key Elements

- **`_starting_workers: set[str]`**: A module-level set (alongside `_active_workers`) that tracks `chat_id`s for which a worker create is in-flight. Added to and removed from within `_ensure_worker()`.
- **`_ensure_worker()` idempotency**: Before spawning, check both `_active_workers[chat_id]` (task exists and not done) AND `_active_workers[chat_id] in _starting_workers` (spawn in-flight). Only create if neither holds.
- **Health check deduplication**: The health check's pending-session loop already iterates per-session. The fix prevents the duplicate spawn without changing the health check logic itself.

### Flow

Worker restart → pending sessions recovered → Step 5 loops unique `chat_id`s with `started_chats` set (already correct) → health check fires → iterates pending sessions by `chat_id` → `_ensure_worker()` called → `_starting_workers` guard prevents duplicate task creation → exactly one `_worker_loop` per `chat_id`

### Technical Approach

1. Add `_starting_workers: set[str] = set()` near `_active_workers` in `agent_session_queue.py`
2. In `_ensure_worker()`, add guard: if `chat_id in _starting_workers`, return early
3. Add `_starting_workers.add(chat_id)` before `asyncio.create_task()`
4. Use a `done_callback` on the new task to remove from `_starting_workers` once the task starts (the loop is now "active" and `_active_workers` takes over)
5. Add a `logger.warning` when a duplicate spawn attempt is detected, for observability

Since asyncio is cooperative (single-threaded event loop), there is no true concurrent access to these sets — the guard works because Python dict/set mutation is not interrupted between `get` and `set` within a single coroutine turn.

## Failure Path Test Strategy

### Exception Handling Coverage
- The `_ensure_worker()` function has no try/except — it is simple synchronous dict access and `asyncio.create_task()`. No new exception handlers introduced.
- The done_callback must not raise: wrap removal in a try/except to ensure a crashing session task doesn't leave `_starting_workers` polluted.

### Empty/Invalid Input Handling
- `chat_id` is always a non-empty string at call sites (guarded by `or session.project_key` upstream). No None/empty guard needed in `_ensure_worker()`.

### Error State Rendering
- No user-visible output for this fix. Observability via the warning log when duplicate spawn is blocked.

## Test Impact

- [ ] `tests/integration/test_agent_session_queue_race.py` — UPDATE: existing race condition tests continue to pass unchanged; add new test case (see Success Criteria below)
- No other existing tests reference `_ensure_worker` directly.

## Rabbit Holes

- **Asyncio Lock**: Adding `asyncio.Lock` to `_ensure_worker()` is unnecessary because asyncio is single-threaded. The cooperative scheduler means no two coroutines execute the same code concurrently without an `await`. A plain set guard is sufficient and simpler.
- **Per-chat_id worker lifecycle management**: Tempting to refactor the entire worker lifecycle (birth, heartbeat, death) into a class. Out of scope — the flat module-level approach works and this is a targeted bug fix.
- **Cross-process serialization**: If multiple worker processes run simultaneously, chat-level serialization would require Redis-level locks. This is a separate, larger problem. This fix only addresses the single-worker-process case.

## Risks

### Risk 1: Done callback timing
**Impact:** If the task's done_callback fires before `_active_workers[chat_id]` is set (race in task scheduling), the set membership check could pass a second time briefly.
**Mitigation:** Set `_active_workers[chat_id] = task` immediately after `asyncio.create_task()` (before any `await`), as is already done. The `_starting_workers` removal via done_callback only happens after the task starts running, by which point `_active_workers` already holds the task.

### Risk 2: `_starting_workers` leak on task creation failure
**Impact:** If `asyncio.create_task()` raises (rare), `chat_id` stays in `_starting_workers` permanently, blocking all future workers for that chat.
**Mitigation:** Wrap the `create_task` call in try/finally to ensure `_starting_workers.discard(chat_id)` on failure path.

## Race Conditions

### Race 1: Health check calls `_ensure_worker()` for two sessions sharing a `chat_id`
**Location:** `agent/agent_session_queue.py` lines 1219, 1271 (health check) and 1695 (`_ensure_worker`)
**Trigger:** Health check iterates N pending sessions with same `chat_id`; both pass the `_active_workers.get()` guard before either task is registered
**Data prerequisite:** `_active_workers[chat_id]` must be set to a non-done task before any subsequent `_ensure_worker()` call for that `chat_id`
**State prerequisite:** `_starting_workers` must be updated atomically within the same event loop turn as the task creation
**Mitigation:** Add `_starting_workers` set; check and update within `_ensure_worker()` without any `await`, so the check-and-set is atomic within the cooperative event loop

## No-Gos (Out of Scope)

- Redis-level distributed locking for multi-process worker serialization
- Refactoring `_active_workers` dict into a class or dataclass
- Changing the `_worker_loop` architecture or standalone-mode behavior
- Any changes to `AgentSession` model, schema, or indexes

## Update System

No update system changes required — this is a purely internal fix to `agent/agent_session_queue.py` and `worker/__main__.py`. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required — this is an internal worker scheduling fix. No MCP servers, `.mcp.json`, or bridge changes needed.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` to document the `_starting_workers` guard and the chat-serialization invariant
- [ ] Add inline docstring to `_ensure_worker()` explaining the two-guard idempotency mechanism

## Success Criteria

- [ ] After worker restart with N recovered sessions on the same `chat_id`, exactly one worker loop runs for that `chat_id` (verified by log: only one `[chat:{chat_id}] Started session queue worker` per `chat_id`)
- [ ] Sessions on the same `chat_id` are always processed sequentially (only one `pending→running` transition at a time per `chat_id`)
- [ ] Existing race condition tests in `tests/integration/test_agent_session_queue_race.py` continue to pass
- [ ] New test: simulate 2 sessions on same `chat_id` recovering simultaneously, assert only one `_worker_loop` task is created
- [ ] `docs/features/bridge-worker-architecture.md` updated with the serialization guarantee

## Team Orchestration

### Team Members

- **Builder (queue-serialization)**
  - Name: queue-builder
  - Role: Implement `_starting_workers` guard in `_ensure_worker()` and add new test
  - Agent Type: async-specialist
  - Resume: true

- **Validator (queue-serialization)**
  - Name: queue-validator
  - Role: Verify the fix, run existing and new tests, confirm no duplicate workers
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `docs/features/bridge-worker-architecture.md`
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Implement `_starting_workers` guard
- **Task ID**: build-queue-guard
- **Depends On**: none
- **Validates**: `tests/integration/test_agent_session_queue_race.py`
- **Assigned To**: queue-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_starting_workers: set[str] = set()` near `_active_workers` in `agent/agent_session_queue.py`
- Update `_ensure_worker()`: check `chat_id in _starting_workers`, add to set before `create_task`, remove via done_callback and try/finally
- Add `logger.warning` when duplicate spawn is blocked
- Update docstring for `_ensure_worker()`

### 2. Add new recovery race test
- **Task ID**: build-test
- **Depends On**: build-queue-guard
- **Validates**: `tests/integration/test_agent_session_queue_race.py` (new test case)
- **Assigned To**: queue-builder
- **Agent Type**: test-writer
- **Parallel**: false
- Add test: create 2 pending sessions with same `chat_id`, call `_ensure_worker()` twice in rapid succession, assert `len([t for t in _active_workers.values() if not t.done()]) == 1`

### 3. Validate fix
- **Task ID**: validate-queue-guard
- **Depends On**: build-test
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_agent_session_queue_race.py -v`
- Confirm no duplicate worker logs in test output
- Verify all existing race tests pass

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-queue-guard
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` with chat-serialization invariant and `_starting_workers` guard explanation

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: queue-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_agent_session_queue_race.py -v` | exit code 0 |
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| No duplicate worker logs | `pytest tests/integration/test_agent_session_queue_race.py -v -s 2>&1 \| grep "Started session queue worker"` | output contains single entry per chat_id |
| Format clean | `python -m black agent/agent_session_queue.py --check` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the root cause is confirmed, the solution is straightforward, and all implementation details are resolved.
