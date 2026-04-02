---
status: Draft
type: bug
appetite: Small
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/626
last_comment_id:
---

# Fix Silent Session Death

> Sessions can die silently -- no error logged, no snapshot saved, tool counts diverge. Four targeted fixes across three files to ensure every session termination is visible and diagnosed.

## Problem

**Current behavior:**

Sessions can terminate without leaving any diagnostic trace. Three contributing failures combine to make this invisible:

1. **Tool count divergence** (`agent/hooks/session_registry.py:157-182`): The heartbeat watchdog in `messenger.py` calls `get_activity(bridge_session_id)` which does a reverse lookup through `_registry` to find the Claude UUID. If the pending-to-UUID promotion never happened (e.g., crash before first hook fires), the reverse lookup returns an empty dict and the heartbeat reports `tools=0` while `health_check.py:_tool_counts` has the real count. The two counters are independent and never reconciled.

2. **Polling gap swallows exceptions** (`agent/agent_session_queue.py:2196-2204`): After `task.run()` starts the asyncio task, the worker polls `task.is_running` every 2 seconds. If the task's exception handler itself crashes (exception-in-exception), the polling loop sees `is_running=False` on the next poll and exits normally -- the exception is lost because nobody awaits the actual `task._task` future.

3. **Crash-path snapshot gap** (`agent/agent_session_queue.py:2229-2244`, `2303-2316`): `save_session_snapshot(event="complete")` only runs on the success path (line 2304). `save_session_snapshot(event="error")` only runs when `task.error` is set (line 2231). If the task crashes in a way that bypasses `BackgroundTask._run_work`'s exception handler, `task.error` is never set, so neither snapshot fires. The `finally` block at line 1637 calls `_complete_agent_session()` which deletes the Redis record -- destroying evidence without saving it.

4. **Missing lifecycle transitions** (`agent/agent_session_queue.py:1606-1638`): The finally block transitions to completed/failed via `_complete_agent_session()` but never calls `log_lifecycle_transition()` first. The `complete_transcript()` path does call it (line 296 of `session_transcript.py`), but the exception/cancellation paths in the finally block skip `complete_transcript()` entirely.

**Observed impact:** Session `tg_valor_-1003449100931_326` ran 100+ tool calls, heartbeat showed `tools=59` stuck, then workers dropped to 0. No error logged, no snapshot saved, Redis record deleted.

## Prior Art

- **PR #603**: Created the session registry UUID-to-bridge-session mapping. The reverse lookup in `get_activity()` is where divergence originates.
- **Issue #374**: Fixed stale counts via `reset_session_count()` in health_check.py -- different path, same symptom family.
- **PR #616**: Massive rename that may have introduced the transition gap by restructuring the finally block.

## Appetite

**Size:** Small (4 surgical changes, no interface changes, no new modules)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

None. All changes are internal to existing files with no external dependencies.

## Solution

### Fix 1: Robust tool count reporting in `get_activity()`

**File:** `agent/hooks/session_registry.py`, function `get_activity()` (lines 157-182)

**Change:** When the reverse UUID lookup fails (returns empty dict), fall back to reading `_tool_counts` from `health_check.py` keyed by bridge_session_id. Also log a warning when the reverse lookup fails so the divergence is visible in logs.

```
def get_activity(bridge_session_id: str | None) -> dict:
    if not bridge_session_id:
        return {}

    # Primary path: reverse lookup UUID from registry
    for uuid, sid in _registry.items():
        if sid == bridge_session_id and uuid != _PENDING_KEY:
            activity = _activity.get(uuid)
            if activity:
                return {
                    "tool_count": activity["tool_count"],
                    "last_tools": list(activity["last_tools"]),
                }

    # Fallback: read from health_check._tool_counts (authoritative counter)
    try:
        from agent.health_check import _tool_counts
        count = _tool_counts.get(bridge_session_id, 0)
        if count > 0:
            logger.warning(
                "[session_registry] get_activity reverse lookup failed for %s, "
                "falling back to health_check count=%d",
                bridge_session_id, count,
            )
            return {"tool_count": count, "last_tools": []}
    except Exception:
        pass

    return {}
```

**Why this is safe:** `_tool_counts` in `health_check.py` is keyed by the same bridge session ID (resolved via `resolve()` at line 421), so the fallback key matches. The warning log makes the divergence visible for debugging.

### Fix 2: Replace polling with direct task await

**File:** `agent/agent_session_queue.py`, lines 2196-2204

**Change:** Replace the `while task.is_running: await asyncio.sleep(2)` polling loop with `await task._task`, wrapped in try/except to catch exceptions that escaped `BackgroundTask._run_work`. Keep the calendar heartbeat as a concurrent task.

```
# Start periodic calendar heartbeats as a background task
async def _heartbeat_loop():
    while not task._task.done():
        await asyncio.sleep(CALENDAR_HEARTBEAT_INTERVAL)
        asyncio.create_task(
            _calendar_heartbeat(session.project_key, project=session.project_key)
        )

heartbeat = asyncio.create_task(_heartbeat_loop())
try:
    # Await the actual task future -- propagates exceptions immediately
    await task._task
except Exception as e:
    # Exception escaped BackgroundTask._run_work's handler
    if not task.error:
        task._error = e
        logger.error(
            "[%s] Task crashed outside _run_work: %s",
            session.session_id, e,
        )
finally:
    heartbeat.cancel()
```

**Why this is safe:** `BackgroundTask.run()` stores the asyncio task as `self._task` (line 138 of `messenger.py`). Awaiting it directly means any unhandled exception propagates immediately. The `BackgroundTask` interface is unchanged -- we only access `_task` which is already set by `run()`.

### Fix 3: Save diagnostic snapshot on every termination

**File:** `agent/agent_session_queue.py`, in the finally block around line 1637

**Change:** Before calling `_complete_agent_session()`, always save a diagnostic snapshot. This captures state even when `task.error` is not set and when the success path was not reached.

```
finally:
    if not session_completed:
        # Always save a diagnostic snapshot before deleting the Redis record
        try:
            _event = "crash" if session_failed else "complete"
            from agent.hooks.session_registry import get_activity
            activity = get_activity(session.session_id)
            save_session_snapshot(
                session_id=session.session_id,
                event=_event,
                project_key=session.project_key,
                branch_name=_session_branch_name(session.session_id),
                task_summary=(
                    f"Session {session.agent_session_id} "
                    f"{'failed' if session_failed else 'terminated'}"
                ),
                extra_context={
                    "agent_session_id": session.agent_session_id,
                    "tool_count": activity.get("tool_count", 0),
                    "trigger": "finally_block",
                },
                working_dir=str(
                    Path(session.working_dir) if hasattr(session, 'working_dir')
                    else Path(__file__).parent.parent
                ),
            )
        except Exception as snap_err:
            logger.warning(
                "Failed to save crash snapshot for %s: %s",
                session.agent_session_id, snap_err,
            )
        await _complete_agent_session(session, failed=session_failed)
```

**Why this is safe:** `save_session_snapshot` writes to disk only (no Redis dependency). It is already wrapped in its own try/except (see `session_logs.py:117`), and we add an outer guard here. The snapshot happens before `_complete_agent_session()` deletes the Redis record, preserving evidence.

### Fix 4: Log lifecycle transitions in the finally block

**File:** `agent/agent_session_queue.py`, in the same finally block and in the CancelledError handler

**Change:** Call `log_lifecycle_transition()` before `_complete_agent_session()` on every path: normal completion, failure, and cancellation. The method is already idempotent (it reads current status and logs the transition).

In the `except asyncio.CancelledError` block (around line 1610):
```
except asyncio.CancelledError:
    logger.warning(...)
    try:
        session.log_lifecycle_transition("failed", "worker cancelled")
    except Exception:
        pass
    await _complete_agent_session(session, failed=True)
```

In the finally block (around line 1637):
```
finally:
    if not session_completed:
        # Log the lifecycle transition before completing
        try:
            target = "failed" if session_failed else "completed"
            session.log_lifecycle_transition(target, "worker finally block")
        except Exception:
            pass
        # ... snapshot saving (Fix 3) ...
        await _complete_agent_session(session, failed=session_failed)
```

**Why this is safe:** `log_lifecycle_transition()` is already guarded with try/except at its call sites (see line 555-557). The method appends to session history and logs -- both are idempotent in the sense that duplicate calls produce duplicate log lines but no state corruption. If Redis is unreachable, the exception is caught and swallowed.

## No-Gos

- Do NOT change the `BackgroundTask` public interface (constructor, `run()`, `is_running`, `error` properties)
- Do NOT add new Redis keys or models -- all diagnostic data goes to disk snapshots
- Do NOT change the `_tool_counts` dict in `health_check.py` -- it is the authoritative counter and should remain independent
- Do NOT add retry logic for failed sessions -- that is a separate concern (session revival already handles it)

## Update System

No update system changes required. All changes are internal to the bridge process and take effect on restart. The `scripts/remote-update.sh` and update skill need no modifications -- a standard `git pull && restart` propagates these fixes.

## Agent Integration

No agent integration required. These fixes are bridge-internal: session registry, worker loop, and snapshot saving are all in the bridge/agent process, not exposed through MCP or tool interfaces. The agent (Claude Code subprocess) is unaware of these mechanisms.

## Failure Path Test Strategy

Each fix has a distinct failure mode that must be tested:

1. **Fix 1 (tool count fallback):** Test that `get_activity()` returns health_check counts when registry reverse lookup fails. Mock `_registry` to be empty while `_tool_counts` has data.

2. **Fix 2 (task await):** Test that exceptions from the task future propagate to the caller. Create a `BackgroundTask` with a coroutine that raises, verify the exception is captured in `task.error`.

3. **Fix 3 (crash snapshot):** Test that the finally block saves a snapshot file to disk when `session_failed=True`. Mock `save_session_snapshot` and verify it is called with `event="crash"`.

4. **Fix 4 (lifecycle transitions):** Test that `log_lifecycle_transition()` is called before `_complete_agent_session()` in both the normal and exception paths. Use mock patching on the session object.

## Test Impact

- [ ] `tests/integration/test_silent_failures.py` -- UPDATE: Add test cases for the new fallback path in `get_activity()` and verify crash snapshots are saved
- [ ] `tests/integration/test_lifecycle_transition.py` -- UPDATE: Add cases verifying `log_lifecycle_transition()` is called in the finally block of the worker loop
- [ ] `tests/unit/test_stall_detection.py` -- UPDATE: Verify tool count fallback does not break existing stall detection logic

New tests to create:
- [ ] `tests/unit/test_session_registry_fallback.py` -- Test `get_activity()` fallback to `health_check._tool_counts`
- [ ] `tests/unit/test_crash_snapshot.py` -- Test that finally block always produces a snapshot file on disk

## Rabbit Holes

- **Unifying `_tool_counts` and `_activity`**: Tempting to merge the two counters into one, but they serve different purposes (health_check runs in subprocess hooks, session_registry runs in parent process). Keep them separate and use fallback instead.
- **Making `BackgroundTask._task` public**: Could rename to `task` or add a `wait()` method, but that changes the interface which other callers depend on. Access `_task` directly -- it is a stable internal set by `run()`.
- **Adding Redis-based snapshots**: The snapshot must work when Redis is unreachable (that is often the cause of the crash). Disk-only is the right choice.

## Documentation

- [ ] Update `docs/features/session-lifecycle-diagnostics.md` to document the crash snapshot behavior and tool count fallback
- [ ] Add a troubleshooting entry for "heartbeat shows stale tool count" pointing to the fallback mechanism
