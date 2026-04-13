---
title: "Bug: health-check-recovered sessions not finalized — causes duplicate Telegram delivery"
slug: health-check-recovery-finalization
type: bug
status: Complete
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/917
last_comment_id: null
---

# Health-Check Recovery Finalization

## Freshness Check

**Disposition: Unchanged**

Verified at baseline commit `57b16132` (2026-04-12). Zero commits to any affected file since issue was filed:
- `agent/agent_session_queue.py` — no changes since `57b16132`
- `models/agent_session.py` — no changes
- `models/session_lifecycle.py` — no changes
- `bridge/session_transcript.py` — no changes

All file:line references from the issue confirmed accurate in current code:
- `finalized_by_execute` flag: L2174/L2177 ✅
- `agent_session = None` lookup block: L2930–2938 ✅
- `if agent_session:` finalization guard: L3364 ✅
- `complete_transcript()` call: L3374 ✅
- `_agent_session_health_check()`: L1341 ✅

No related sibling issues or PRs cited that have changed state. Proceed with plan.

## Problem

When the health check recovers a session (`running → pending → running`), the re-executed session may complete successfully but remain in `running` state in Redis. The health check then finds it "stuck" 10–15 minutes later and re-runs it, sending duplicate Telegram messages.

**Root cause:** Inside `_execute_agent_session()`, `complete_transcript()` is only called when the inner `agent_session` lookup returns non-None (L3364). If that lookup fails (race condition on the `status="running"` filter during recovery), finalization is silently skipped. Because `_execute_agent_session()` returns normally, `finalized_by_execute = True`, and the outer `finally` block is suppressed — leaving the session permanently in `running` state.

**Incident:** Session `c0642630` (thread 8866) repeatedly sent responses every ~10–15 minutes until manually stopped.

## Appetite

**Small** — isolated fix in `_execute_agent_session()`. No schema changes, no new dependencies, no new services.

## Solution

**Option B (structural):** Make finalization unconditional on the success path. When `_execute_agent_session()` is about to return normally (no exception), ensure `finalize_session()` is called using the outer `session` parameter (always available) even if the inner `agent_session` lookup returned `None`.

The `agent_session` object is used for several purposes:
- Branch name / task_list_id lookups (needed for routing/context)
- Appending to history (best-effort)
- Calling `complete_transcript()` (the finalization path)

Only the third use needs to be decoupled from the `agent_session` guard. Finalization must happen regardless.

**Nudge path preservation:** The existing `chat_state.defer_reaction` check (L3373) already guards against premature completion when a nudge is in flight. This logic stays — finalization is only forced when `not chat_state.defer_reaction`.

**`finalized_by_execute` preservation:** The outer `finally` guard from #898 is NOT changed. `finalized_by_execute = True` still suppresses the outer block on normal return. The fix operates inside `_execute_agent_session()`, not outside it.

## Technical Approach

### Current code structure (L3362–3382):

```python
# Update session status in Redis via AgentSession
# When auto-continue deferred, session is still active (not completed)
if agent_session:
    try:
        from bridge.session_transcript import complete_transcript

        final_status = (
            "active"
            if chat_state.defer_reaction
            else ("completed" if not task.error else "failed")
        )
        if not chat_state.defer_reaction:
            complete_transcript(session.session_id, status=final_status)
        # else: nudge path — _enqueue_nudge already wrote authoritative state
    except Exception as e:
        logger.warning(...)
```

### Fix — add fallback finalization when `agent_session is None`:

```python
if agent_session:
    try:
        from bridge.session_transcript import complete_transcript

        final_status = (
            "active"
            if chat_state.defer_reaction
            else ("completed" if not task.error else "failed")
        )
        if not chat_state.defer_reaction:
            complete_transcript(session.session_id, status=final_status)
        # else: nudge path — _enqueue_nudge already wrote authoritative state
    except Exception as e:
        logger.warning(...)
else:
    # agent_session lookup returned None (race on status="running" filter, e.g.
    # after health-check recovery). Finalize using outer `session` param directly
    # to prevent session from staying in `running` state permanently.
    # Uses complete_transcript() (not finalize_session() directly) to ensure
    # the SESSION_END transcript marker is written — complete_transcript queries
    # by session_id alone (no status filter), so it works here.
    if not chat_state.defer_reaction:
        try:
            from bridge.session_transcript import complete_transcript

            final_status = "completed" if not task.error else "failed"
            complete_transcript(session.session_id, status=final_status)
            logger.info(
                "Fallback finalization: session %s → %s (agent_session was None)",
                session.agent_session_id,
                final_status,
            )
        except StatusConflictError:
            # CAS conflict = another process already finalized. This is success.
            logger.info(
                "Fallback finalization skipped: session %s already transitioned (CAS conflict — expected)",
                session.agent_session_id,
            )
        except Exception as e:
            logger.warning(
                "Fallback finalization failed for session %s: %s",
                session.agent_session_id,
                e,
            )
```

**Why this is safe:**
- `session` (outer param) is always the `AgentSession` object passed to `_execute_agent_session()` — it is always non-None
- `complete_transcript()` queries by `session_id` alone (L285: `AgentSession.query.filter(session_id=session_id)`), not by `status="running"` — so it succeeds even when the inner `agent_session` lookup failed
- `complete_transcript()` calls `finalize_session()` internally, which is idempotent and CAS-protected
- `StatusConflictError` is caught separately (CAS conflict = another process already finalized = success case, logged at `info` level)
- Unexpected exceptions caught separately at `warning` level (preserves signal-to-noise)
- Nudge path is preserved: the `if not chat_state.defer_reaction` guard prevents premature finalization when `_enqueue_nudge` is in control

### Files to modify

- `agent/agent_session_queue.py` — add `else` branch after the `if agent_session:` finalization block (~L3364–3382)

### Files to add

- `tests/unit/test_health_check_recovery_finalization.py` — new test for recovered session finalization

## Step-by-Step Tasks

- [x] Read `_execute_agent_session()` from L3360–3390 in `agent/agent_session_queue.py` to confirm exact location of the `if agent_session:` finalization block
- [x] Add `else` branch after the `if agent_session:` block calling `complete_transcript(session.session_id, status=final_status)` guarded by `if not chat_state.defer_reaction` — this writes the SESSION_END transcript marker AND calls `finalize_session()` internally
- [x] Add `from models.session_lifecycle import StatusConflictError` import and catch `StatusConflictError` separately at `info` level (CAS conflict = success case, another process already finalized)
- [x] Catch remaining exceptions at `warning` level (preserves signal-to-noise ratio)
- [x] Add `logger.info` on successful fallback finalization (for observability)
- [x] Write `tests/unit/test_health_check_recovery_finalization.py`:
  - Test 1: `agent_session = None` + `task.error = None` + `defer_reaction = False` → `complete_transcript` called with `"completed"`
  - Test 2: `agent_session = None` + `task.error = "some error"` + `defer_reaction = False` → `complete_transcript` called with `"failed"`
  - Test 3: `agent_session = None` + `defer_reaction = True` → `complete_transcript` NOT called (nudge path preserved)
  - Test 4: `agent_session` is non-None → existing `complete_transcript` path used (regression guard)
  - Test 5: Fallback `complete_transcript` raises `StatusConflictError` → info logged, no exception propagated
  - Test 6: Fallback `complete_transcript` raises unexpected exception → warning logged, no exception propagated
- [x] Run `pytest tests/unit/test_health_check_recovery_finalization.py -v` — all pass
- [x] Run `pytest tests/unit/test_recovery_respawn_safety.py -v` — all pass (regression check for #898)
- [x] Run `python -m ruff check agent/agent_session_queue.py` — clean
- [x] Update `docs/features/session-recovery-mechanisms.md` to document the finalization gap and fallback fix

## Success Criteria

- [x] A session recovered by the health check (`running → pending → running`) is marked `completed` in Redis after successful execution, even when the inner `agent_session` lookup returns `None`
- [x] No session remains in `running` state after `_execute_agent_session()` returns normally (non-exception path)
- [x] The nudge path is unaffected: sessions where `chat_state.defer_reaction = True` are not prematurely finalized
- [x] `finalized_by_execute` guard preserved — `#898` regression does not reappear
- [x] All new unit tests pass
- [x] `test_recovery_respawn_safety.py` continues to pass

## Prior Art

- [#700](https://github.com/tomcounsell/ai/issues/700) / [PR #703](https://github.com/tomcounsell/ai/pull/703): Fixed zombie loop in `_agent_session_hierarchy_health_check()` — different code path, same symptom (session re-runs after completion)
- [#898](https://github.com/tomcounsell/ai/issues/898): Introduced `finalized_by_execute` flag to prevent double-finalization on the nudge path. This fix must not regress that work.
- [#723](https://github.com/tomcounsell/ai/issues/723): Broad audit of all 7 recovery mechanisms — confirmed health check was safe at the time. This bug only manifests on the recovered session's re-execution path.

## Failure Path Test Strategy

| Failure | Behavior |
|---------|----------|
| `complete_transcript()` raises `StatusConflictError` (CAS conflict) | Caught separately → `info` logged → session already in terminal state (success case) |
| `complete_transcript()` raises other exception | Caught → `warning` logged → session remains in `running` → health check will recover again (same as today) |
| `session` param is `None` | `complete_transcript()` queries by `session_id` — if session has no `session_id`, caught → warning → no crash |
| `chat_state` not available | Not possible — `chat_state` is always initialized before this code path |

## Test Impact

No existing tests affected — the health-check recovery finalization path (`agent_session = None` on normal return) currently has no unit test coverage. New test file added: `tests/unit/test_health_check_recovery_finalization.py`.

## Rabbit Holes

- **Do not** attempt to fix the root cause of why `agent_session` lookup returns `None` — the race on `status="running"` during recovery is transient and acceptable. The fallback finalization is the right fix.
- **Do not** add a Redis round-trip check after `_execute_agent_session()` returns (Option A) — Option B is cleaner and avoids an extra network call.
- **Do not** change `finalized_by_execute` behavior — it is correct and prevents the #898 regression.
- **Do not** change the health check timeout values or recovery logic — this is not a health check bug, it is a finalization bug.

## No-Gos

- No changes to `_agent_session_health_check()` logic
- No changes to `finalized_by_execute` flag behavior
- No schema changes to `AgentSession` model
- No new Redis keys or data structures

## Update System

No update system changes required — this is a worker-internal fix with no new config, dependencies, or migration steps.

## Agent Integration

No agent integration changes required — this is internal session lifecycle logic in the worker. No MCP server changes, no `.mcp.json` changes, no bridge changes.

## Documentation

- [x] Update `docs/features/session-recovery-mechanisms.md` — Section 2 "Health Check (`_agent_session_health_check`)" currently describes only the recovery trigger (running → pending). Add a subsection documenting the finalization gap: when `agent_session` inner lookup returns `None` on re-execution, finalization was silently skipped. Document the fallback path added by this fix: `finalize_session(session, final_status, reason="agent_session lookup miss — fallback finalization")` using the outer `session` param.
- [x] In the same doc, update the "Known Race Windows" section (or add an entry) describing the `agent_session = None` race window on health-check-recovered sessions and how the fallback finalization closes it.

## Open Questions

None. The recon is thorough, root cause confirmed, solution approach validated against code.
