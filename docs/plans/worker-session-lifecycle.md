---
status: docs_complete
type: feature
appetite: Medium
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/741
last_comment_id:
---

# Worker Service: Persistent Event Loop, Graceful Shutdown, Headless Nudge Loop

## Problem

The standalone worker (`python -m worker`) exits when its queue is empty, breaking multi-turn SDLC pipelines that rely on nudge re-enqueues arriving within milliseconds.

**Current behavior:**

1. `_worker_loop()` uses a 1.5s `DRAIN_TIMEOUT` on `asyncio.Event.wait()`. When no new work arrives after the timeout and the sync fallback finds nothing, the worker exits. Launchd restarts it after a 10s `ThrottleInterval` gap.
2. Nudge re-enqueues (`_enqueue_nudge()`) call `_ensure_worker()`, but if the worker process has already exited, the event fires into nothing. The session sits as "pending" in Redis for 10+ seconds.
3. SIGTERM (`launchctl kickstart -k`) sets a `shutdown_event` in `worker/__main__.py`, but does not await active `_worker_loop` tasks — the current session's SDK subprocess is killed mid-execution, leaving the session in "running" status.
4. PM sessions running SDLC pipelines produce output, the nudge loop determines "nudge_continue", but the worker exits before the re-enqueued session is processed.

**Desired outcome:**

The standalone worker stays alive indefinitely, processes nudge re-enqueues within milliseconds (no launchd restart gap), handles SIGTERM by finishing the current session before exiting, and supports full SDLC pipeline execution (Plan → Critique → Build → Test → Review → Docs → Merge) end-to-end without human intervention.

## Prior Art

No prior issues found related to making the worker persistent. The worker was extracted from the bridge in PR #737 (issue #731) as a first step, with the explicit intent to add persistent behavior later. Related session lifecycle fixes:

- **Issue #730 / PR**: Fixed terminal-status guard in session intake path (re-enqueue loop bug)
- **Issue #727**: Fixed startup recovery resetting recently-started sessions
- **Issue #700**: Fixed completed sessions reverting to pending (zombie loop)

These are all downstream consequences of the same root problem: the worker exits prematurely, creating timing windows where sessions get lost or corrupted.

## Data Flow

The nudge cycle data flow is the core path this plan modifies:

1. **Entry point**: `_execute_agent_session()` completes, `send_to_chat()` is called with agent output
2. **`determine_delivery_action()`**: For PM/SDLC sessions, returns `"nudge_continue"` — session should keep running
3. **`_enqueue_nudge()`**: Sets session status back to "pending" with incremented `auto_continue_count`, calls `_ensure_worker(chat_id)`
4. **`_ensure_worker()`**: Checks `_active_workers[chat_id]` — if the task is still running (not `.done()`), returns immediately. If the task exited (the bug), creates a new asyncio task running `_worker_loop(chat_id, event)`
5. **`_worker_loop()`**: Pops the re-enqueued session, executes it, cycle repeats
6. **Output**: `FileOutputHandler.send()` writes intermediate output to `logs/worker/{session_id}.log`

**The bug**: Between steps 4 and 5, if the worker process has exited (because the previous `_worker_loop` broke out of its `while True` loop), `_ensure_worker()` cannot create a new asyncio task — there is no running event loop. The session is stranded as "pending" in Redis until launchd restarts the process.

**The fix**: In standalone mode, `_worker_loop` never exits its `while True` loop on queue empty — it waits indefinitely for the event to fire. The worker process stays alive, the event loop stays running, and `_ensure_worker()` always has a working event loop to create tasks in.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation)
- Review rounds: 1

The changes are concentrated in two files (`worker/__main__.py` and `agent/agent_session_queue.py`) with well-defined behavior boundaries. The complexity comes from preserving backward compatibility with the bridge's embedded worker.

## Prerequisites

No prerequisites — this work uses only existing infrastructure (Redis, asyncio, launchd).

## Solution

### Key Elements

- **Persistent worker mode**: `_worker_loop` waits indefinitely on the asyncio.Event when queue is empty (no timeout exit) when `VALOR_WORKER_MODE=standalone` is set
- **Graceful SIGTERM handler**: On SIGTERM, set a shutdown flag that `_worker_loop` checks after completing the current session — finish work, then exit cleanly
- **Backward-compatible bridge mode**: The bridge's embedded worker retains existing drain-timeout-exit behavior unchanged

### Flow

**Standalone worker startup** → Register FileOutputHandler → Recover interrupted sessions → Start worker loops for pending sessions → **Wait indefinitely** → Process nudge re-enqueues instantly → **SIGTERM** → Finish current session → Drain remaining sessions to "pending" → Exit

### Technical Approach

1. **Mode detection**: `_worker_loop` reads `os.environ.get("VALOR_WORKER_MODE")`. When `"standalone"`, the drain path uses `await event.wait()` with no timeout instead of `asyncio.wait_for(event.wait(), timeout=DRAIN_TIMEOUT)`. When the event fires, pop and process. When no event fires, just keep waiting — no exit.

2. **Shutdown coordination**: Replace the simple `shutdown_event.wait()` in `worker/__main__.py` with a coordinated shutdown sequence:
   - SIGTERM sets a module-level `_shutdown_requested` flag in `agent_session_queue.py`
   - `_worker_loop` checks this flag after completing each session
   - If shutdown requested: break out of the loop (don't start new sessions)
   - `_run_worker()` gathers all active `_worker_loop` tasks and awaits them with a timeout
   - Sessions that were pending but not started get left as "pending" in Redis for the next startup

3. **Event loop integration**: The standalone worker's `asyncio.run()` already provides the event loop. `_ensure_worker()` creates tasks in this loop. No changes needed to `_ensure_worker()` itself — it already works correctly when the process is alive.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `_worker_loop` already has `except asyncio.CancelledError` and `except Exception` blocks — verify both paths work correctly with the new persistent mode
- [x] The SIGTERM handler in `worker/__main__.py` must not raise — verify it gracefully handles edge cases (shutdown during startup, double SIGTERM)

### Empty/Invalid Input Handling
- [x] `_worker_loop` in persistent mode receiving an event.set() but finding no pending session after pop — must not crash, just re-wait
- [x] `_enqueue_nudge()` called after shutdown flag is set — must still work (the current session needs to complete its nudge)

### Error State Rendering
- [x] Worker log output on shutdown clearly shows "finishing current session" vs "no active sessions"
- [x] `FileOutputHandler.send()` continues to work during graceful shutdown

## Test Impact

- [x] `tests/integration/test_worker_drain.py` — existing tests pass unchanged (bridge mode behavior preserved)
- [x] `tests/unit/test_agent_session_queue_async.py` — no changes needed, shutdown flag is independent
- [x] `tests/integration/test_agent_session_lifecycle.py` — existing tests pass unchanged

## Rabbit Holes

- **Distributed worker coordination**: Multiple worker processes sharing the same queue with leader election. Not needed — single worker per machine is sufficient.
- **Persistent connection pooling**: Keeping Redis connections warm across idle periods. Redis already handles this; asyncio event waiting is zero-cost.
- **Worker auto-scaling**: Dynamically spawning more worker loops based on queue depth. The per-chat worker model already provides parallelism where needed.
- **Rewriting the drain strategy entirely**: The event-based drain works well — we're only changing the exit condition, not the drain mechanism.

## Risks

### Risk 1: Worker stays alive but stops processing
**Impact:** Sessions accumulate as "pending" in Redis with no progress, harder to detect than a crashed process (launchd would restart a crash).
**Mitigation:** The existing `_agent_session_health_loop` already monitors for stuck sessions. Add a heartbeat counter that the health loop checks — if the worker is alive but hasn't processed a session in N minutes and pending sessions exist, log a critical warning.

### Risk 2: SIGTERM timeout leaves orphaned SDK subprocess
**Impact:** Claude Code subprocess keeps running after worker exits, consuming resources and potentially corrupting session state.
**Mitigation:** Use a two-phase shutdown: (1) await active worker loops with a generous timeout (60s), (2) if timeout expires, cancel the tasks (which triggers the existing CancelledError handler in `_worker_loop` that properly cleans up the session).

## Race Conditions

### Race 1: Shutdown flag set between session pop and session execute
**Location:** `agent/agent_session_queue.py` `_worker_loop` around line 1537-1572
**Trigger:** SIGTERM arrives after `_pop_agent_session()` returns a session but before `_execute_agent_session()` starts
**Data prerequisite:** Session must be in "running" status (set by pop)
**State prerequisite:** Shutdown flag has been set
**Mitigation:** Check the shutdown flag before `_execute_agent_session()`. If set, complete the already-popped session (don't abandon it mid-pop) — the session was already transitioned to "running", so it must be executed or explicitly reverted to "pending".

### Race 2: _enqueue_nudge fires during shutdown
**Location:** `agent/agent_session_queue.py` `_enqueue_nudge` around line 1780-1908
**Trigger:** A session completes and the nudge loop re-enqueues it, but shutdown was requested during execution
**Data prerequisite:** Session must exist in Redis with "running" status
**State prerequisite:** Shutdown flag is set
**Mitigation:** Allow the nudge to proceed — the re-enqueued session will be picked up on next worker startup. The `_worker_loop` will see the shutdown flag after completing the current session and exit without processing the newly enqueued one, which is correct behavior (it stays as "pending" for next startup).

## No-Gos (Out of Scope)

- Telegram output from the standalone worker — output goes to `FileOutputHandler` only
- Multi-process worker clustering — single process per machine
- Changes to `determine_delivery_action()` logic — nudge decisions are unchanged
- Changes to the bridge's embedded worker behavior — must remain backward compatible
- Session priority rebalancing during shutdown — pending sessions keep their current priority

## Update System

The update script (`scripts/remote-update.sh`) already handles worker restarts via `valor-service.sh worker-restart`. The SIGTERM graceful shutdown means `worker-restart` will cleanly finish the current session before restarting, which is a strict improvement. No update script changes needed.

The launchd plist for the worker (`com.valor.worker.plist`) should be updated to remove `KeepAlive` since the worker now stays alive on its own. However, keeping `KeepAlive` as a safety net is harmless — if the worker crashes, launchd restarts it, and the persistent mode kicks in. No mandatory change.

No update system changes required — the worker binary, dependencies, and config format are unchanged.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent session queue API (`enqueue_agent_session`, `_ensure_worker`, `_enqueue_nudge`) is unchanged. The `OutputHandler` protocol is unchanged. The only behavioral change is that the worker process stays alive between sessions.

## Documentation

- [x] Update `docs/features/worker-service.md` to document persistent mode behavior, graceful shutdown, and the `VALOR_WORKER_MODE` environment variable
- [x] Update `CLAUDE.md` worker-related entries if any operational commands change — no changes needed, existing commands unchanged

## Success Criteria

- [x] Worker process stays alive indefinitely (no exit on empty queue) when `VALOR_WORKER_MODE=standalone`
- [x] `_enqueue_nudge()` re-enqueue is processed within 2 seconds (no 10s launchd gap)
- [x] SIGTERM finishes current session before exit (no orphaned SDK subprocesses)
- [x] `auto_continue_count` increments correctly across nudge cycles in headless mode
- [x] Worker survives 10+ consecutive nudge cycles without exiting
- [x] Existing bridge behavior is unchanged (backward compatible) — drain-timeout-exit still works when `VALOR_WORKER_MODE` is not `standalone`
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (worker-lifecycle)**
  - Name: worker-builder
  - Role: Implement persistent event loop, graceful shutdown, and mode detection
  - Agent Type: async-specialist
  - Resume: true

- **Validator (worker-lifecycle)**
  - Name: worker-validator
  - Role: Verify backward compatibility, nudge timing, and shutdown behavior
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement persistent worker mode in `_worker_loop`
- **Task ID**: build-persistent-loop
- **Depends On**: none
- **Validates**: tests/integration/test_worker_drain.py, tests/unit/test_agent_session_queue_async.py
- **Assigned To**: worker-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add mode detection: read `VALOR_WORKER_MODE` env var in `_worker_loop`
- When `standalone`: replace `asyncio.wait_for(event.wait(), timeout=DRAIN_TIMEOUT)` with `await event.wait()` (no timeout) in the drain path
- When `standalone`: remove the exit-time safety check and `break` — the loop never exits on empty queue
- Preserve existing bridge behavior when `VALOR_WORKER_MODE` is not `standalone`

### 2. Implement graceful SIGTERM shutdown
- **Task ID**: build-graceful-shutdown
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue_async.py (create new test cases)
- **Assigned To**: worker-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add module-level `_shutdown_requested: bool = False` flag in `agent_session_queue.py`
- Add `request_shutdown()` function that sets the flag and sets all active events (to wake up waiting workers)
- In `_worker_loop`: check `_shutdown_requested` after each session completes — if set, break
- In `worker/__main__.py`: SIGTERM handler calls `request_shutdown()` instead of just setting `shutdown_event`
- In `_run_worker()`: after `shutdown_event.wait()`, gather all `_active_workers` tasks and await them with a 60s timeout
- If timeout: cancel remaining tasks (triggers CancelledError cleanup)

### 3. Validate backward compatibility
- **Task ID**: validate-compat
- **Depends On**: build-persistent-loop, build-graceful-shutdown
- **Assigned To**: worker-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify bridge mode (no `VALOR_WORKER_MODE` env var) still exits `_worker_loop` on empty queue
- Verify `_ensure_worker()` behavior unchanged
- Verify `_enqueue_nudge()` behavior unchanged
- Run existing test suite to confirm no regressions

### 4. Write integration tests for persistent mode
- **Task ID**: build-tests
- **Depends On**: build-persistent-loop, build-graceful-shutdown
- **Validates**: tests/integration/test_worker_drain.py (update), tests/integration/test_worker_persistent.py (create)
- **Assigned To**: worker-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test: worker stays alive after queue empty in standalone mode
- Test: nudge re-enqueue processed without worker exit/restart
- Test: SIGTERM finishes current session before exit
- Test: SIGTERM with no active session exits immediately
- Test: 10+ consecutive nudge cycles without worker exit

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: worker-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/worker-service.md` with persistent mode section
- Document graceful shutdown behavior
- Document `VALOR_WORKER_MODE` environment variable

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: worker-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Worker starts in persistent mode | `VALOR_WORKER_MODE=standalone timeout 5 python -m worker --dry-run` | exit code 0 |
| Bridge mode unchanged | `python -c "from agent.agent_session_queue import DRAIN_TIMEOUT; assert DRAIN_TIMEOUT == 1.5"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the issue is well-specified with clear acceptance criteria, the recon confirmed the technical approach, and the solution is narrowly scoped to three changes in two files with well-defined backward compatibility boundaries.
