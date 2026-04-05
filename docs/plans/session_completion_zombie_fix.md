---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/700
last_comment_id:
---

# Session Completion Zombie Fix

## Problem

Completed sessions revert to `pending` status and are re-executed by the worker loop, causing duplicate Telegram responses every ~15 seconds.

**Current behavior:**
After the agent responds and a session is marked `completed`, it cycles back to `pending → running → completed → pending` indefinitely. Each cycle sends a duplicate response to Telegram. Observed on DM session `tg_dm_179144806_8664`.

**Desired outcome:**
- Completed sessions retain `status="completed"` permanently in Redis
- Completed sessions are never re-picked by `_pop_agent_session()` (which already filters by `status="pending"`)
- Completed sessions can be revived only by explicit reply-to messages
- No infinite re-execution loops or duplicate responses

## Prior Art

- **Commit `d43f5553`**: Original session retention — changed `_complete_agent_session()` from `async_delete()` to `save()` with `status="completed"`. Introduced the zombie loop because it didn't account for health check orphan-fixing.
- **Commit `ec18c2a6`**: Emergency revert to delete-on-complete. Stopped the zombie but lost session retention for reply-to revival.
- **Commit `8b77a5f9`**: Re-revert to restore retention. Zombie loop is live again.
- **Issue #392**: Strengthened Popoto model relationships — documented KeyField vs IndexedField behavior, directly relevant to understanding why delete-and-recreate is used.
- **Issue #495** (closed): Bridge resilience for dependency outages — unrelated.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `d43f5553` | Changed delete to save-with-completed-status | Did not account for `_agent_session_hierarchy_health_check()` orphan-fixing, which uses `_extract_agent_session_fields()` → `AgentSession.create()`. Since `status` is not in the extracted fields, recreated sessions default to `pending`. |
| `ec18c2a6` | Reverted to delete-on-complete | Correct emergency fix but lost the session retention feature needed for reply-to revival. |
| `8b77a5f9` | Re-reverted to restore retention | Knowingly re-introduced the zombie loop pending a proper fix (this plan). |

**Root cause pattern:** The fix was applied at the completion layer (`_complete_agent_session`) without auditing the health check layer that can recreate sessions. The `_extract_agent_session_fields()` helper silently drops `status` because the comment at line 126-128 says "status is an IndexedField, so it does not need delete-and-recreate — just mutate and save." But the health check at line 1296 ignores this guidance and uses delete-and-recreate anyway, causing `status` to default to `"pending"`.

## Data Flow

The zombie loop follows this cycle:

1. **Session completes**: `_complete_agent_session()` sets `session.status = "completed"` and calls `session.save()` (line 914-916). Session persists in Redis correctly.
2. **Health check runs** (every 5 minutes): `_agent_session_hierarchy_health_check()` scans all sessions for orphaned children (line 1282-1299).
3. **Orphan detected**: If a completed child's parent no longer exists, the health check runs `fields = _extract_agent_session_fields(child)` → `child.delete()` → `AgentSession.create(**fields)`.
4. **Status lost**: `_extract_agent_session_fields()` does not include `status` in the extracted fields (line 124-170). The recreated session gets the default `status="pending"`.
5. **Worker picks up**: `_pop_agent_session()` finds the now-pending session and executes it again, sending duplicate responses.
6. **Cycle repeats**: The session completes again (step 1), health check runs again (step 2), and the loop continues indefinitely.

Secondary issue (nudge overwrite):

1. **Nudge enqueued**: `_enqueue_nudge()` sets `session.status = "pending"` on a fresh Redis copy (line 1875).
2. **Worker finally block**: Runs `_complete_agent_session(session, failed=session_failed)` with the *original* (stale) session object (line 1690).
3. **Status overwritten**: The finally block overwrites the nudge's `pending` back to `completed`, causing the nudge session to never execute.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `_extract_agent_session_fields()` gains `status` in its field list — all callers that intentionally override status (retry at line 811, nudge fallback at line 1850) already do `fields["status"] = "pending"` explicitly, so they are unaffected.
- **Coupling**: No change — fix is internal to `agent/agent_session_queue.py`
- **Data ownership**: No change
- **Reversibility**: Trivial — single-file change, can revert any individual fix independently

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

- **Bug 1 fix (primary)**: Change the health check orphan-fixing code to mutate `parent_agent_session_id` directly instead of using delete-and-recreate. Since `parent_agent_session_id` is listed in the comment as a KeyField, the fix will use `_extract_agent_session_fields()` but explicitly preserve `status` by adding it to `_AGENT_SESSION_FIELDS`.
- **Bug 2 fix (secondary)**: Guard the worker finally block so it skips `_complete_agent_session()` when `chat_state.defer_reaction` is True, meaning a nudge was already enqueued.
- **Defense in depth**: Add `status` to `_AGENT_SESSION_FIELDS` so any future delete-and-recreate path preserves it.

### Flow

**Session completes** → `status="completed"` saved → Health check finds orphaned child → Preserves status during orphan fix → Session stays `completed` → Worker ignores it

**Nudge enqueued** → `defer_reaction=True` → Worker finally block skips completion → Nudge session executes normally

### Technical Approach

1. Add `"status"` to `_AGENT_SESSION_FIELDS` list (after the comment at line 126). This is the defense-in-depth fix.
2. In `_agent_session_hierarchy_health_check()` at line 1294-1299: the health check currently does delete-and-recreate because `parent_agent_session_id` is a KeyField. With `status` now in the fields list, the recreated session will preserve its original status. No logic change needed in the health check itself — the field list fix handles it.
3. In the worker finally block at line 1650-1690: add a check for `chat_state.defer_reaction`. If True, skip the `_complete_agent_session()` call since a nudge was already enqueued and the session should remain pending. The `chat_state` variable needs to be accessible in the finally block — it's defined in `_execute_agent_session()` which is called inside the worker loop's try block. The finally block at line 1650 is inside the same scope, so `chat_state` may not be available there. Need to check scope.

**Scope clarification for Bug 2**: The worker finally block at lines 1650-1690 is in `_worker_loop()`, NOT in `_execute_agent_session()`. The `chat_state` object is local to `_execute_agent_session()`. The finally block in `_worker_loop()` runs AFTER `_execute_agent_session()` returns. The nudge is enqueued inside `_execute_agent_session()`, and if a nudge was enqueued, the session's Redis status was already set to `pending` by `_enqueue_nudge()`. The worker finally block then calls `_complete_agent_session(session)` with the stale in-memory object, overwriting the nudge's `pending` back to `completed`.

**Fix for Bug 2**: Before calling `_complete_agent_session()` in the worker finally block, re-read the session from Redis. If its current status is `pending` (meaning a nudge was enqueued), skip completion.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The health check orphan-fixing at lines 1296-1299 is wrapped in a try/except that logs errors — no change needed
- [ ] The worker finally block at lines 1650-1690 already handles exceptions around snapshot saving — the new guard adds no new exception paths

### Empty/Invalid Input Handling
- [ ] `_extract_agent_session_fields()` with a session that has `status=None` — should preserve None (defaults on create are separate)
- [ ] Health check with no orphaned children — no-op, no status change

### Error State Rendering
- No user-visible output changes — this is a backend lifecycle fix

## Test Impact

No existing tests affected — the health check orphan-fixing path and the worker finally block completion path have no dedicated unit tests today. This plan creates new tests for both paths.

## Rabbit Holes

- **Replacing all delete-and-recreate with direct mutation**: Tempting but requires auditing every KeyField in the model. The `status` field is an IndexedField and CAN be mutated directly, but `parent_agent_session_id` is a KeyField and genuinely needs delete-and-recreate. Keep the current pattern and just fix the field list.
- **Adding TTL-based cleanup for completed sessions**: Out of scope — the model already has a 90-day TTL via Popoto Meta.ttl.
- **Redesigning the health check to avoid delete-and-recreate entirely**: Would require changing Popoto's KeyField behavior — out of scope.

## Risks

### Risk 1: Adding `status` to `_AGENT_SESSION_FIELDS` breaks retry logic
**Impact:** Retry (line 810) already does `fields["status"] = "pending"` after extracting, so the extracted `status` (e.g., `"failed"`) would be overwritten. No breakage.
**Mitigation:** Audit confirms all three callers that use `_extract_agent_session_fields()` with intentional status override (retry at 810, nudge fallback at 1850) already set `fields["status"]` explicitly after extraction.

### Risk 2: Re-reading session from Redis in worker finally block adds latency
**Impact:** Negligible — single Redis GET on a hot key, sub-millisecond.
**Mitigation:** This is a guard check, not a hot path. Runs once per session completion.

## Race Conditions

### Race 1: Nudge enqueued between completion check and `_complete_agent_session()`
**Location:** `agent/agent_session_queue.py` lines 1650-1690 (worker finally block)
**Trigger:** `_enqueue_nudge()` sets status to `pending` on the Redis record, then the worker finally block re-reads the record and sees `pending`, skipping completion. But if the nudge and the finally block read at nearly the same instant, the finally block might read the old `completed` status.
**State prerequisite:** `_enqueue_nudge()` must have called `async_save()` before the finally block reads.
**Mitigation:** `_enqueue_nudge()` is called inside `_execute_agent_session()` which runs inside the try block. The finally block runs AFTER `_execute_agent_session()` returns. Since `_enqueue_nudge()` awaits `async_save()` before returning, and `_execute_agent_session()` awaits the nudge call, the Redis write is guaranteed to complete before the finally block reads. No race.

## No-Gos (Out of Scope)

- Redesigning the entire session lifecycle state machine
- Changing Popoto KeyField behavior or migrating fields between KeyField/IndexedField
- Adding session deduplication or idempotency keys
- Fixing the recon validator regex bug (separate issue)

## Update System

No update system changes required — this is a bridge-internal bug fix in `agent/agent_session_queue.py`. After merge, the standard `/update` skill pulls and restarts the bridge, which picks up the fix automatically.

## Agent Integration

No agent integration required — this is a bridge-internal change to the session lifecycle in `agent/agent_session_queue.py`. No MCP servers, tools, or bridge imports are affected.

## Documentation

- [ ] Update `docs/features/session-lifecycle.md` (if it exists) to document that `_extract_agent_session_fields()` now preserves `status`
- [ ] Add inline code comments explaining the Bug 1 and Bug 2 fixes

## Success Criteria

- [ ] Completed sessions retain `status="completed"` permanently in Redis (verified by test)
- [ ] `_agent_session_hierarchy_health_check()` orphan-fixing preserves original session status (verified by test)
- [ ] `_extract_agent_session_fields()` includes `status` field (verified by assertion)
- [ ] Worker finally block skips `_complete_agent_session()` when nudge was enqueued (verified by test)
- [ ] Test: completed session with orphaned parent survives health check without status change
- [ ] Test: nudged session retains `status="pending"` after worker finally block runs
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-lifecycle)**
  - Name: lifecycle-builder
  - Role: Implement the three fixes in agent_session_queue.py
  - Agent Type: builder
  - Resume: true

- **Validator (session-lifecycle)**
  - Name: lifecycle-validator
  - Role: Verify fixes don't break existing session flows
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `status` to `_AGENT_SESSION_FIELDS`
- **Task ID**: build-field-list
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue.py (create test for field extraction)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"status"` to the `_AGENT_SESSION_FIELDS` list at line 127 in `agent/agent_session_queue.py`
- Update the comment at line 126-128 to reflect that status is now included for defense-in-depth
- Write unit test: extract fields from a session with `status="completed"`, verify `status` is in the result

### 2. Guard worker finally block against nudge overwrite
- **Task ID**: build-finally-guard
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue.py (create test for finally block guard)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- In the worker finally block (line 1650-1690), before calling `_complete_agent_session()`, re-read the session from Redis
- If the re-read session's `status` is `"pending"`, skip the `_complete_agent_session()` call and log that a nudge was detected
- Write unit test: mock a session that was nudged (Redis status = "pending"), verify `_complete_agent_session()` is not called

### 3. Validate all callers of `_extract_agent_session_fields`
- **Task ID**: validate-callers
- **Depends On**: build-field-list
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify retry (line 810) still overrides status to `"pending"` after extraction
- Verify nudge fallback (line 1850) still overrides status to `"pending"` after extraction
- Verify health check (line 1296) now correctly preserves completed status via the field list

### 4. Integration validation
- **Task ID**: validate-all
- **Depends On**: build-field-list, build-finally-guard, validate-callers
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify lint and format pass
- Confirm no regressions in session lifecycle

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Status in field list | `python -c "from agent.agent_session_queue import _AGENT_SESSION_FIELDS; assert 'status' in _AGENT_SESSION_FIELDS"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions — the issue's recon summary confirmed the root causes and the fixes are straightforward.
