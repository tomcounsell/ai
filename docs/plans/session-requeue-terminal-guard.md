---
status: Done
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/730
last_comment_id:
---

# Session Re-Enqueue Loop: Intake Path Terminal-Status Guard

## Problem

When a PM `AgentSession` completes, follow-up messages for the same chat (sent without using Telegram's reply feature) cause the completed session to cycle through states repeatedly before the work is actually delivered.

**Current behavior:**
A completed session transitions `completed → superseded → pending → running → completed` up to 4 times before the nudge fires. Each cycle is a full agent execution with no useful work — just overhead. The intake path in `bridge/telegram_bridge.py` calls `enqueue_agent_session()` without first checking whether the existing session for that `session_id` is already terminal.

**Desired outcome:**
Once a session reaches a terminal status (`completed`, `failed`, `killed`, `abandoned`, `cancelled`), it is never re-enqueued by the message intake path. Follow-up messages either route to a fresh session (new `session_id`) or are absorbed by the intake classifier. No terminal session is ever transitioned to `superseded`.

## Prior Art

- **PR #724** (Session recovery audit: terminal status respawn safety) — Audited and hardened 7 recovery mechanisms. Added `reject_from_terminal` to `transition_status()`. Guarded `determine_delivery_action()`, `_enqueue_nudge()` (three-layer defense), and `check_revival()`. Explicitly carved out `_mark_superseded()` with `reject_from_terminal=False` as an intentional exception. **Did NOT guard the intake path in `telegram_bridge.py`** — this is the missing 8th vector.
- **Issue #723** (Audit all session recovery mechanisms for completed-session respawn safety) — The parent audit issue. Identified and catalogued 7 mechanisms; the intake path was not in scope or was missed.
- **PR #721** (Consolidate session lifecycle mutations into single module) — Foundation work enabling the `transition_status()` guard.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #724 | Audited 7 session recovery mechanisms and added terminal guards to each | Missed the intake path in `telegram_bridge.py:1603` as an 8th re-enqueue vector. The audit doc lists 7 mechanisms; the intake path was undocumented and unguarded. Also, `_mark_superseded()` was kept with `reject_from_terminal=False` — this means a completed session is still transitioned to `superseded` (non-terminal), allowing the worker to re-pick it. |

**Root cause pattern:** The audit scope was framed as "recovery mechanisms" — paths that explicitly try to revive or recover sessions. The intake path was not conceptualized as a recovery mechanism; it creates new sessions. But when a follow-up message arrives for an existing `session_id`, the intake path effectively re-enqueues the existing session record by calling `_mark_superseded()` on it and creating a new `pending` record under the same `session_id`. This falls through all existing guards because those guards protect nudge/revival paths, not fresh-message intake.

## Data Flow

The bug is a **timing race** triggered by two cooperating defects:

1. **User sends follow-up message** (not a Telegram reply — no `reply_to_msg_id`) to a chat where a session is in progress or has recently completed.
2. **Routing** (`telegram_bridge.py:948–1025`): No reply-to → semantic routing runs (`session_router.py:58`). Semantic routing **only considers `active`/`dormant` sessions** — it cannot return a terminal `session_id`. It matches a dormant session and assigns its `session_id` at line 1011.
3. **Async gap** (between line 1011 and the enqueue call at line 1603): The matched dormant session finishes — status transitions to `completed` (terminal) while the bridge is still processing the intake path.
4. **Intake classifier** (`telegram_bridge.py:1341–1489`): Queries for `running/active/dormant` sessions only — the now-completed session is invisible here. Falls through to enqueue.
5. **Enqueue call** (`telegram_bridge.py:1603`): `enqueue_agent_session()` called with the stale (now-terminal) `session_id`.
6. **`_push_agent_session()`** (`agent_session_queue.py:247`): Calls `_mark_superseded()` which queries all sessions for that `session_id` with status=`completed` and transitions each to `superseded` (non-terminal via `reject_from_terminal=False`). Creates new `pending` record under same `session_id`.
7. **Worker picks up the re-activated session**: Executes again, doing nothing useful.
8. **Repeat** until nudge threshold fires.

**The two cooperating defects:**

- **Defect 1 (bridge, primary):** Intake path does not check if the session for `session_id` went terminal in the async gap before calling `enqueue_agent_session()`. The guard needs to fire after line 1025 where `session_id` is first assigned.
- **Defect 2 (queue, defense-in-depth):** `_mark_superseded()` passes `reject_from_terminal=False` to `transition_status()`, which explicitly bypasses the terminal guard and converts `completed` → `superseded` (non-terminal), re-activating the session for the worker.

## Architectural Impact

- **Interface changes**: None — no public API signatures change.
- **Coupling**: Adds one `AgentSession` status lookup to the intake path before the enqueue call. Mild coupling increase between bridge and session model, but this lookup already happens elsewhere in the same function for steering checks.
- **Data ownership**: No change — the bridge continues to own routing decisions.
- **Reversibility**: Trivially reversible — removing the guard restores old behavior.
- **New dependencies**: None.

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

- **Intake path terminal guard** (`bridge/telegram_bridge.py`): Before calling `enqueue_agent_session()`, check if the most recent session for `session_id` is in `TERMINAL_STATUSES`. If terminal, force a fresh `session_id` (use the current `message.id` to generate one) rather than re-using the terminal session's ID.
- **`_mark_superseded()` terminal skip** (`agent_session_queue.py`): When iterating over sessions to supersede, skip any that are already terminal. Do not call `transition_status(reject_from_terminal=False)` on a terminal session. Only supersede sessions whose status is `completed` (the current filter) AND whose status is not already in `TERMINAL_STATUSES` — which is redundant since `completed` is terminal, so the real fix is: do not supersede at all when the prior session is terminal. The guard should be: if the prior session is terminal, leave it alone and proceed to create the new `pending` record without touching the old one.
- **Documentation update** (`docs/features/session-recovery-mechanisms.md`): Add the intake path as the 8th (previously undocumented) re-enqueue vector, with its new terminal-safety status.

### Flow

Message arrives → Routing assigns `session_id` → Intake path queries existing session status → **If terminal: generate fresh `session_id`** → Enqueue with fresh ID (no supersede of terminal session) → Worker picks up and runs fresh session once.

### Technical Approach

**Fix 1 — Intake path guard** (preferred approach: force fresh session):

After routing assigns `session_id` and before the enqueue call at line 1603, add a lookup:

```python
# Terminal-status guard on intake path (#730)
# If the existing session for this session_id is already terminal,
# generate a fresh session_id instead of re-using it.
# This prevents _mark_superseded() from converting terminal->superseded.
if not (is_reply_to_valor and message.reply_to_msg_id):
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES
        existing = list(AgentSession.query.filter(session_id=session_id))
        if existing and existing[0].status in TERMINAL_STATUSES:
            old_session_id = session_id
            session_id = f"tg_{project_key}_{event.chat_id}_{message.id}"
            logger.info(
                f"[routing] Intake terminal guard: session {old_session_id} "
                f"is terminal ({existing[0].status}), forcing fresh session {session_id}"
            )
    except Exception as e:
        logger.debug(f"Intake terminal guard check failed (non-fatal): {e}")
```

This approach is preferable to guarding inside `_mark_superseded()` because it stops the problem at the earliest point and avoids a new `pending` record being created under the same `session_id` as a completed session.

**Fix 2 — `_mark_superseded()` defense in depth**:

Even with Fix 1, `_mark_superseded()` should be tightened as defense-in-depth. The root issue is not the filter (changing it to `not in TERMINAL_STATUSES` would incorrectly supersede `pending`/`running`/`dormant` sessions, breaking PR #721 reply-to resumption). The root issue is the explicit `reject_from_terminal=False` override that bypasses the existing terminal guard.

The fix: **remove `reject_from_terminal=False`** from the `transition_status()` call, so `transition_status()` uses its default behavior (terminal → reject). A `completed` session will be rejected rather than moved to `superseded`. The session record stays `completed`; no re-activation occurs.

```python
def _mark_superseded():
    from models.session_lifecycle import transition_status

    old_completed = [
        s
        for s in AgentSession.query.filter(session_id=session_id)
        if s.status == "completed"
    ]
    for old in old_completed:
        transition_status(
            old,
            "superseded",
            reason=f"superseded by new session for {session_id}",
            # reject_from_terminal=False removed — terminal sessions must not be re-activated
        )
        logger.info(
            f"Marked old completed session {old.id} as superseded "
            f"for session_id={session_id}"
        )
```

The existing filter (`s.status == "completed"`) and loop structure remain unchanged. Only the `reject_from_terminal=False` override is removed. This means `_mark_superseded()` will log a warning when it tries to supersede a `completed` session and fails (the existing `except Exception` wrapper handles this gracefully).

## Failure Path Test Strategy

### Exception Handling Coverage

- The intake guard uses `except Exception` with a `logger.debug` fallback — failure is non-fatal, bridge continues to enqueue. This matches the existing pattern for all other checks in this function. The test should assert the guard runs and the fallback is exercised.

### Empty/Invalid Input Handling

- If `AgentSession.query.filter(session_id=session_id)` returns empty list (no existing session for this ID): guard correctly does nothing, enqueue proceeds normally.
- If `session_id` is None or malformed: `TERMINAL_STATUSES` check is never reached (existing code sets `session_id` before this point).

### Error State Rendering

- No user-visible output change — the guard operates silently. Logging at `INFO` level for the terminal detection case.

## Test Impact

- [x] `tests/unit/test_recovery_respawn_safety.py` — UPDATE: add test class `TestIntakePathTerminalGuard` covering the new guard. No existing tests break since we're adding to the module, not modifying existing guards.
- [x] `tests/unit/test_agent_session_queue_async.py` — UPDATE: add test for revised `_mark_superseded()` behavior (skips terminal sessions, only supersedes non-terminal ones). Check existing `test_mark_superseded` tests if present.

## Rabbit Holes

- **Changing `superseded` to be terminal**: Tempting but high blast radius — `superseded` may be used in other contexts. Out of scope.
- **Rearchitecting how `session_id` is assigned for follow-up messages**: The deeper question of "should follow-up messages always get fresh `session_id`s?" is out of scope. The guard handles the specific bug without redesigning routing.
- **Auditing all callers of `_mark_superseded()`**: There's only one call site. Don't audit the broader supersede pattern — fix the specific defect.
- **Telemetry for re-enqueue loops**: Interesting but not required for this fix.

## Risks

### Risk 1: Guard fires incorrectly on reply-to resumption
**Impact:** User replies to a completed session to continue work; the guard forces a fresh session instead of resuming. Loss of context.
**Mitigation:** The guard is explicitly skipped for `is_reply_to_valor and message.reply_to_msg_id` — reply-to resumption is a different code path that already has the correct behavior. The guard only fires for non-reply messages.

### Risk 2: Semantic routing assigns a terminal `session_id`
**Impact:** If semantic routing (`find_matching_session`) returns a `session_id` for a terminal session, the guard correctly intercepts and generates a fresh ID. This is the exact bug scenario — the guard handles it correctly.
**Mitigation:** The guard fires after routing assigns `session_id` and before enqueue. Any terminal `session_id` (whether from semantic routing or the coalescing guard) is caught.

### Risk 3: Removing `reject_from_terminal=False` breaks existing tests
**Impact:** `test_completed_to_superseded_with_reject_false` may fail if it exercises `_mark_superseded()` rather than `transition_status()` mechanics directly.
**Mitigation:** Verify the test calls `transition_status()` directly, not via `_mark_superseded()`. If it tests `_mark_superseded()`, the test itself documents the now-unwanted behavior and should be updated to expect rejection. The failing test is a signal, not a blocker.

### Risk 4: Removing `reject_from_terminal=False` accumulates orphaned `completed` records
**Impact:** Old `completed` records are no longer promoted to `superseded`; queries for a `session_id` may return multiple `completed` records.
**Mitigation:** Fix 1 (intake guard) prevents the scenario that creates duplicate records under the same `session_id` in the first place. Accumulation only occurs if Fix 1 is bypassed. The existing `except Exception` wrapper in `_push_agent_session()` means `_mark_superseded()` failure is logged but non-fatal — the new `pending` record is still created correctly.

## Race Conditions

### Race 1: Session completes between intake guard check and `_push_agent_session()`
**Location:** `bridge/telegram_bridge.py` (guard) → `agent_session_queue.py:_push_agent_session()`
**Trigger:** Session status is non-terminal when the guard reads it, then completes before `_mark_superseded()` runs.
**Data prerequisite:** Session status must be accurate at the time `_mark_superseded()` runs.
**State prerequisite:** Session must not transition to terminal between guard check and `_mark_superseded()`.
**Mitigation:** This is the existing race condition that PR #724's `_enqueue_nudge` re-read guard addresses. For the intake path, the window is very short (microseconds between guard and enqueue call). Defense-in-depth: Fix 2 (`_mark_superseded()` skips terminal sessions) catches this race — by the time `_mark_superseded()` runs, if the session has gone terminal, it will be skipped.

### Race 2: Concurrent messages arrive simultaneously for same `session_id`
**Location:** `bridge/telegram_bridge.py` (coalescing guard) → intake guard
**Trigger:** Two rapid-fire messages both pass the coalescing guard and both run the intake guard concurrently.
**Mitigation:** The coalescing guard (`_recent_session_by_chat`) already handles rapid-fire messages. By the time the second message reaches the intake guard, the first has already set the in-memory guard. This race is pre-existing and not worsened by this fix.

## No-Gos (Out of Scope)

- Changing `superseded` status to be terminal
- Redesigning how `session_id` is assigned for follow-up messages (semantic routing rearchitecture)
- Fixing any other undocumented re-enqueue vectors (none known)
- Adding telemetry or metrics to the re-enqueue loop

## Update System

No update system changes required — this is a bridge-internal bug fix with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change to the intake path. The agent (ChatSession/DevSession) is not involved in session routing. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [x] Update `docs/features/session-recovery-mechanisms.md`: change "7 mechanisms" to "8 mechanisms" in the overview, add a new "8. Message Intake Path" section under Active Mechanisms (location, trigger, what it does, guard description), and add a row to the Test Coverage table.
- [x] Add entry for mechanism 8 to the `## Test Coverage` table in `docs/features/session-recovery-mechanisms.md` linking to the new `TestIntakePathTerminalGuard` tests.

## Success Criteria

- [x] A `completed` `AgentSession` is never transitioned to `superseded` by a follow-up message (Fix 2)
- [x] The intake path in `telegram_bridge.py` has an explicit terminal-status guard before `enqueue_agent_session()` (Fix 1)
- [x] `_mark_superseded()` skips sessions that are already in `TERMINAL_STATUSES` (Fix 2)
- [x] The `completed → superseded → pending → running → completed` cycling does not occur
- [x] `docs/features/session-recovery-mechanisms.md` documents the intake path as Mechanism 8 with its guard status
- [x] New tests cover the intake path guard (Fix 1) and revised `_mark_superseded()` behavior (Fix 2)
- [x] All 15 existing `test_recovery_respawn_safety.py` tests continue to pass
- [x] Ruff lint and format pass

## Team Orchestration

### Team Members

- **Builder (intake-guard)**
  - Name: intake-guard-builder
  - Role: Implement Fix 1 (intake path terminal guard in telegram_bridge.py) and Fix 2 (_mark_superseded() filter fix in agent_session_queue.py)
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-writer
  - Role: Write new unit tests for intake path terminal guard and _mark_superseded() behavior
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Update session-recovery-mechanisms.md with the 8th intake path vector
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Run full test suite and verify all success criteria
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Implement Fix 1 — Intake Path Terminal Guard
- **Task ID**: build-intake-guard
- **Depends On**: none
- **Validates**: `tests/unit/test_recovery_respawn_safety.py`, `tests/unit/test_agent_session_queue_async.py`
- **Assigned To**: intake-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add terminal-status guard in `bridge/telegram_bridge.py` after line 1025 where `session_id` is first assigned (and before any downstream use — the enqueue call at ~1603 is the latest acceptable insertion point)
- Guard: if existing session for `session_id` is in `TERMINAL_STATUSES`, force fresh `session_id = f"tg_{project_key}_{event.chat_id}_{message.id}"`
- Skip the guard for `is_reply_to_valor and message.reply_to_msg_id` (reply-to resumption is a different path)
- Log at INFO level when the guard fires (terminal detected, fresh session_id assigned)
- Wrap in `except Exception` with `logger.debug` fallback (non-fatal)
- Ensure `_recent_session_by_chat` write at line ~1034 uses the post-guard `session_id` (it will naturally since the guard fires before line 1034 if placed after 1025)

### 2. Implement Fix 2 — `_mark_superseded()` Filter Fix
- **Task ID**: build-mark-superseded-fix
- **Depends On**: none
- **Validates**: `tests/unit/test_recovery_respawn_safety.py`, `tests/unit/test_agent_session_queue_async.py`
- **Assigned To**: intake-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent_session_queue.py:_push_agent_session()`, remove `reject_from_terminal=False` from the `transition_status()` call inside `_mark_superseded()` (line ~260)
- Keep the filter `if s.status == "completed"` and loop structure unchanged — only the override kwarg is removed
- This allows the existing terminal guard in `transition_status()` to reject the `completed → superseded` transition, preventing re-activation
- Note: `test_completed_to_superseded_with_reject_false` in `test_recovery_respawn_safety.py` tests the `transition_status()` mechanics directly — verify it does NOT test `_mark_superseded()` internals before changing

### 3. Write Tests for Both Fixes
- **Task ID**: write-tests
- **Depends On**: build-intake-guard, build-mark-superseded-fix
- **Assigned To**: test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestIntakePathTerminalGuard` class to `tests/unit/test_recovery_respawn_safety.py`
  - Test: intake guard fires when session status is each of the 5 terminal statuses → fresh `session_id` generated
  - Test: intake guard does NOT fire when session is non-terminal (pending, running, dormant)
  - Test: intake guard does NOT fire for reply-to messages (skipped)
  - Test: intake guard falls back gracefully when AgentSession query raises exception
- Add test for revised `_mark_superseded()` to `tests/unit/test_agent_session_queue_async.py` (or `test_recovery_respawn_safety.py`)
  - Test: `_mark_superseded()` does NOT transition `completed` sessions (transition is rejected by terminal guard)
  - Test: `_mark_superseded()` behavior is unchanged for non-completed sessions (if any filter path exists)

### 4. Update Documentation
- **Task ID**: document-fix
- **Depends On**: write-tests
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-recovery-mechanisms.md`
  - Change "7 mechanisms" to "8 mechanisms" in the overview
  - Add new section "8. Message Intake Path" under "Active Mechanisms"
  - Document: location (`bridge/telegram_bridge.py`), trigger (new Telegram message), what it does, terminal safety status (Guarded — intake terminal guard), guard description
  - Update the "Test Coverage" table at the bottom

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-fix
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_recovery_respawn_safety.py -v` — all existing tests pass, new tests pass
- Run `pytest tests/unit/test_agent_session_queue_async.py -v` — all tests pass
- Run `python -m ruff check bridge/telegram_bridge.py agent/agent_session_queue.py` — clean
- Run `python -m ruff format --check bridge/telegram_bridge.py agent/agent_session_queue.py` — clean
- Verify `docs/features/session-recovery-mechanisms.md` mentions "8 mechanisms" and has the intake path entry

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_recovery_respawn_safety.py tests/unit/test_agent_session_queue_async.py -q` | exit code 0 |
| Recovery safety tests | `pytest tests/unit/test_recovery_respawn_safety.py -v` | exit code 0 |
| Lint clean | `python -m ruff check bridge/telegram_bridge.py agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/telegram_bridge.py agent/agent_session_queue.py` | exit code 0 |
| Doc updated | `grep -c "Mechanism 8\|intake path\|8 mechanisms\|Message Intake" docs/features/session-recovery-mechanisms.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the root cause and fix approach are both fully specified by the issue and confirmed by code reading.
