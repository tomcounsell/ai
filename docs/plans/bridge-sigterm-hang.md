---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/937
last_comment_id:
revision_applied: true
---

# Fix: Bridge process hangs after SIGTERM

## Problem

When the bridge receives SIGTERM, it enters `_graceful_shutdown()` but the process never exits. It hangs indefinitely because `asyncio.run()` waits for background tasks that have infinite `while True` loops and are never cancelled.

**Current behavior:**
SIGTERM → `_graceful_shutdown()` disconnects Telegram client → `main()` returns → `asyncio.run()` tries to clean up remaining tasks → six background tasks with `while True` loops block shutdown → process hangs for hours → launchd never restarts → dashboard shows `error`.

**Desired outcome:**
SIGTERM → `_graceful_shutdown()` cancels all background tasks → process exits within 10 seconds → launchd restarts bridge → dashboard returns to `ok`.

## Freshness Check

**Baseline commit:** `4f76314f`
**Issue filed at:** 2026-04-13T09:38:19Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/telegram_bridge.py:159` — `SHUTTING_DOWN = False` global flag — still holds
- `bridge/telegram_bridge.py:1733` — `_shutdown_handler` signal handler — still holds (was line 1734 in issue, now 1733)
- `bridge/telegram_bridge.py:1741` — `_graceful_shutdown` coroutine — still holds
- `bridge/telegram_bridge.py:2020` — `asyncio.create_task(_run_catchup())` — still holds
- `bridge/telegram_bridge.py:2029` — `asyncio.create_task(reconciler_loop(...))` — still holds
- `bridge/telegram_bridge.py:2046` — `asyncio.create_task(watchdog_loop(...))` — still holds
- `bridge/telegram_bridge.py:2084` — `asyncio.create_task(message_query_loop())` — still holds
- `bridge/telegram_bridge.py:2091` — `asyncio.create_task(relay_loop(client))` — still holds
- `bridge/telegram_bridge.py:2132` — `asyncio.create_task(heartbeat_loop())` — still holds
- `bridge/telegram_bridge.py:2135` — `await client.run_until_disconnected()` — still holds
- `bridge/telegram_bridge.py:2139` — `asyncio.run(main())` — still holds

**Cited sibling issues/PRs re-checked:**
- PR #742 — Worker graceful shutdown — merged 2026-04-06. Established the cancellation pattern the bridge should follow.
- PR #789 — Worker exit code 1 on SIGTERM — merged. launchd ThrottleInterval handling.

**Commits on main since issue was filed (touching referenced files):**
- `c718a6e8` "feat(#912): migrate all session types to CLI harness" — touched telegram_bridge.py but NOT the shutdown or background task code. Irrelevant to this bug.

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** Line number drift is trivial (±1 line). All code references verified accurate.

## Prior Art

- **Issue #45**: "Bridge fails to auto-restart after self-triggered SIGTERM" — Fixed early on, but addressed a different symptom (bridge calling SIGTERM on itself). Did not fix the background task cancellation problem.
- **PR #742**: "Worker persistent mode and graceful shutdown" — **Direct prior art.** Implemented the correct pattern for the worker: `asyncio.Event` for shutdown signaling, explicit `task.cancel()` for each background task, `await asyncio.gather(*pending, return_exceptions=True)` for cleanup, and `sys.exit(1)` for launchd ThrottleInterval. The bridge was not updated with this pattern.
- **Issue #741**: "Worker service: persistent event loop, graceful shutdown" — Tracking issue for PR #742. Successfully solved the identical problem for the worker process.
- **Issue #776**: "Worker launchd restart takes ~9 minutes" — Fixed ThrottleInterval behavior for worker. Bridge may need the same exit-code-1 pattern from PR #789.

## Data Flow

1. **Entry point**: OS sends SIGTERM to bridge PID (from launchd stop or manual kill)
2. **Signal handler** (`_shutdown_handler`, line 1733): Sets `SHUTTING_DOWN = True`, schedules `_graceful_shutdown(client)` on the event loop
3. **`_graceful_shutdown`** (line 1741): Stops knowledge watcher, writes final `last_connected`, sleeps 2s, calls `await tg_client.disconnect()`
4. **`run_until_disconnected`** (line 2135): Returns because client is disconnected, so `main()` returns
5. **`asyncio.run()`** (line 2139): Attempts to cancel remaining tasks — but six `while True` loop tasks never check `SHUTTING_DOWN` and are never explicitly cancelled
6. **Hang**: `asyncio.run()` waits indefinitely (or up to implementation-specific timeout) for tasks that will never finish

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

- **Task tracking**: Store references to all `asyncio.create_task()` results so they can be cancelled during shutdown
- **Explicit cancellation**: In `_graceful_shutdown()`, cancel all tracked tasks and await their completion
- **Safety net**: Add a hard `sys.exit()` after cleanup to guarantee the process terminates

### Flow

**SIGTERM received** → `_shutdown_handler` sets flag, schedules shutdown → `_graceful_shutdown` cancels all background tasks → `asyncio.gather(*tasks, return_exceptions=True)` → disconnect client → `main()` returns → `asyncio.run()` cleans up (nothing left) → process exits → launchd restarts

### Technical Approach

Follow the proven pattern from PR #742 (worker graceful shutdown):

1. **Create a module-level list** `_background_tasks: list[asyncio.Task] = []` to track all background tasks created in `main()`
2. **Append each `asyncio.create_task()` result** to `_background_tasks` (6 tasks: catchup, reconciler, watchdog, message_query, relay, heartbeat)
3. **In `_graceful_shutdown()`**, after the existing cleanup steps and before `tg_client.disconnect()`:
   - Cancel all tasks in `_background_tasks`
   - `await asyncio.gather(*_background_tasks, return_exceptions=True)`
4. **After `main()` returns** from `run_until_disconnected`, add `sys.exit(1)` as a safety net (exit code 1 for launchd ThrottleInterval, matching PR #789 pattern)

This is the same approach that already works in `worker/__main__.py` lines 346-378.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_graceful_shutdown` function has a `try/except` around knowledge watcher stop — existing, no change needed
- [ ] New cancellation code must use `return_exceptions=True` to avoid propagating `CancelledError`

### Empty/Invalid Input Handling
- [ ] Handle case where `_background_tasks` is empty (bridge crashed before creating tasks) — `asyncio.gather()` with empty list is a no-op, safe by default

### Error State Rendering
- [ ] Not applicable — no user-visible output from shutdown path

## Test Impact

No existing tests affected — the bridge shutdown path has no test coverage today. This fix adds a new unit test for the shutdown behavior.

## Rabbit Holes

- **Adding `SHUTTING_DOWN` checks to every loop body**: Tempting but unnecessary. Explicit `task.cancel()` is cleaner and guaranteed to work. The worker PR #742 uses cancellation, not flag-checking. Don't mix approaches.
- **Rewriting the signal handler to use `asyncio.Event`**: The worker uses this pattern, but the bridge's signal handler already works fine for scheduling the shutdown coroutine. Refactoring would increase scope for no benefit.
- **Adding structured shutdown ordering for tasks**: Overkill. All six tasks are independent and can be cancelled simultaneously.

## Risks

### Risk 1: CancelledError propagation during disconnect
**Impact:** If a task's cancellation handler raises, it could prevent clean Telegram disconnect.
**Mitigation:** Use `return_exceptions=True` in `asyncio.gather()` to swallow all exceptions from cancelled tasks.

### Risk 2: Heartbeat writing `last_connected` after shutdown begins
**Impact:** Stale `last_connected` timestamp could confuse dashboard briefly.
**Mitigation:** The cancellation happens before `tg_client.disconnect()`, and the existing `_write_last_connected()` call in `_graceful_shutdown()` writes the final authoritative timestamp.

## Race Conditions

No race conditions identified — signal handlers are single-threaded, `_graceful_shutdown` runs on the event loop, and task cancellation is an asyncio primitive with well-defined semantics.

## No-Gos (Out of Scope)

- Refactoring background task creation into a task manager class
- Adding health check endpoints to the bridge process
- Changing the bridge's launchd ThrottleInterval configuration
- Adding `SHUTTING_DOWN` flag checks inside background task loop bodies (cancellation is sufficient)

## Update System

No update system changes required — this is a bridge-internal bug fix with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change to the shutdown path. No new tools, MCP servers, or bridge imports needed.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` — add a subsection on graceful shutdown task cancellation
- [ ] Add inline code comments in `_graceful_shutdown()` explaining the cancellation pattern and its origin (PR #742)

## Success Criteria

- [ ] After SIGTERM, bridge process exits within 10 seconds
- [ ] launchd restarts the bridge automatically (no manual intervention needed)
- [ ] Dashboard Telegram badge returns to `ok`/`running` within ~60 seconds of SIGTERM
- [ ] No background tasks keep running after shutdown begins
- [ ] Unit test validates that `_graceful_shutdown` cancels all tracked tasks
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-shutdown)**
  - Name: shutdown-builder
  - Role: Implement task cancellation in `_graceful_shutdown()` and add safety-net exit
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-shutdown)**
  - Name: shutdown-validator
  - Role: Verify shutdown behavior and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement task cancellation in bridge shutdown
- **Task ID**: build-shutdown-fix
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_shutdown.py (create)
- **Assigned To**: shutdown-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_background_tasks: list[asyncio.Task] = []` module-level list in `bridge/telegram_bridge.py`
- Wrap each `asyncio.create_task()` call (lines 2020, 2029, 2046, 2084, 2091, 2132) to append the returned task to `_background_tasks`
- In `_graceful_shutdown()`, before `tg_client.disconnect()`: cancel all tasks in `_background_tasks`, then `await asyncio.gather(*_background_tasks, return_exceptions=True)`
- After `await client.run_until_disconnected()` returns (line 2135), add `sys.exit(1)` safety net for launchd ThrottleInterval
- Add unit test `tests/unit/test_bridge_shutdown.py` that creates mock tasks, calls `_graceful_shutdown`, and asserts all tasks are cancelled

### 2. Validate shutdown fix
- **Task ID**: validate-shutdown-fix
- **Depends On**: build-shutdown-fix
- **Assigned To**: shutdown-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_background_tasks` list exists and all 6 `create_task` calls append to it
- Verify `_graceful_shutdown` cancels all tasks before disconnecting
- Verify `sys.exit(1)` safety net is present after `run_until_disconnected`
- Run `pytest tests/unit/test_bridge_shutdown.py -v` and confirm pass
- Run `python -m ruff check bridge/telegram_bridge.py` and confirm clean

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-shutdown-fix
- **Assigned To**: shutdown-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with graceful shutdown task cancellation subsection
- Add inline code comments in `_graceful_shutdown()`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: shutdown-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify lint clean: `python -m ruff check .`
- Verify format clean: `python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Shutdown test passes | `pytest tests/unit/test_bridge_shutdown.py -v` | exit code 0 |
| Task tracking exists | `grep -c '_background_tasks' bridge/telegram_bridge.py` | output > 0 |
| Cancellation in shutdown | `grep 'task.cancel' bridge/telegram_bridge.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-13. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic, Operator | Plan lists 5 background tasks but source has 6 — `reconciler_loop` at line 2029 is missing | Task 1 | Add `reconciler_loop` (line 2029) to `_background_tasks` list. There are 6 `asyncio.create_task()` calls in `main()` startup: lines 2020, 2029, 2046, 2084, 2091, 2132. |
| NIT | Simplifier | Plan says `sys.exit(1)` for launchd ThrottleInterval but issue sketch says `sys.exit(0)` — pick one and justify | Task 1 | Use `sys.exit(1)` per PR #789 pattern: exit code 1 triggers launchd ThrottleInterval (10s default), preventing tight restart loops. Exit code 0 would restart immediately. |
| NIT | Archaeologist | Prior Art section references PR #742 pattern but the worker uses `asyncio.Event` + `shutdown_event.set()` while the plan uses direct `task.cancel()` — the plan correctly notes this divergence in Rabbit Holes but could be clearer that the bridge pattern is intentionally simpler | N/A | No action needed — the plan already addresses this in Rabbit Holes section. |

---

## Open Questions

No open questions — the root cause is fully diagnosed, the fix pattern is proven (PR #742), and the scope is narrow.
