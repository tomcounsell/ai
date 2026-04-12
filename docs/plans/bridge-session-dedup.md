---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-12
tracking: https://github.com/tomcounsell/ai/issues/918
last_comment_id:
---

# Bridge: Prevent Duplicate Session Execution on Health-Check Recovery

## Problem

Session `tg_valor_-5051653062_8866` received the same Telegram message "hot fix your telegram history tool and run /update" 6+ times, each time triggering a full agent session execution and delivering a duplicate response to Telegram.

**Current behavior:**
When a worker crashes or is cancelled (CancelledError) mid-execution, the session is intentionally left in `running` state for startup recovery. After `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s), the health check sees the worker as dead and resets the session to `pending`. The worker then re-runs the session from scratch with the original message — including delivering a duplicate response. If each re-run also fails to complete cleanly, this repeats indefinitely.

**Desired outcome:**
Each unique Telegram message is processed and responded to exactly once. If a session is recovered after already delivering its final response, the health check marks it `completed` rather than re-running it.

## Freshness Check

**Baseline commit:** `c4f166879e464d1650705c8e9369b0b78c05778b`
**Issue filed at:** `2026-04-12T08:41:31Z`
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/agent_session_queue.py:1369` — health check scans running sessions, resets to pending when worker dead — still holds
- `agent/agent_session_queue.py:68` — `AGENT_SESSION_HEALTH_MIN_RUNNING = 300` — still holds
- `bridge/dedup.py` — DedupRecord only deduplicates at bridge intake (catch_up replays), NOT at worker execution — still holds
- `bridge/telegram_relay.py:297` — `_record_sent_message` records sent Telegram message IDs on `AgentSession.pm_sent_message_ids` — still holds

**Commits on main since issue was filed (touching referenced files):**
- `8eb93a9e` chore: consolidate secrets to ~/Desktop/Valor/.env — irrelevant (secrets management only)

**Active plans in `docs/plans/` overlapping this area:** none — no active plans touching session health check or delivery dedup.

**Notes:** The code at the relevant locations matches what the issue describes. No prior PR has fixed this specific path.

## Prior Art

- **PR #194**: Fix duplicate delivery from catchup scanner race condition — Addressed a different duplicate delivery vector: catch_up scanner re-enqueuing live messages. That fix added DedupRecord at bridge intake. This issue is about the worker health-check recovery path re-executing a session that already delivered — a distinct vector the DedupRecord doesn't cover.

- **Issue #588**: Bridge misses messages during live connection — Addressed the opposite problem (messages not delivered). No overlap.

## Data Flow

The failure path that leads to duplicate execution:

1. **Telegram message arrives** → bridge creates `AgentSession` with status `pending`, records message in `DedupRecord`
2. **Worker pops session** → sets status `running`, spawns SDK subprocess (Claude Code)
3. **Session executes** → delivers response via `send_to_chat` → outbox → relay → Telegram; `pm_sent_message_ids` is populated by `_record_sent_message`
4. **Worker process dies / receives SIGTERM** → CancelledError in worker loop → session intentionally left in `running` state for startup recovery
5. **After 300s** → health check sees: worker dead + `running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING` → **resets session to `pending`**
6. **Worker re-runs session** → same original message → delivers duplicate response to Telegram
7. **If re-run also fails to complete**: steps 4-6 repeat indefinitely

The `DedupRecord` (step 1) only prevents bridge intake duplicates. Steps 5-6 bypass it entirely — the health check re-enqueues without checking whether output was already delivered.

## Architectural Impact

- **New field on AgentSession model**: `response_delivered_at` (DatetimeField, nullable) — additive, no breaking change
- **Minimal coupling**: fix is self-contained to two locations (`send_to_chat` callback + `_agent_session_health_check`)
- **No interface changes**: no public APIs modified
- **Reversibility**: easy — field is nullable; removing the health check guard restores prior behavior

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

- **`response_delivered_at` field**: New `DatetimeField(null=True)` on `AgentSession`. Set when the `deliver` action fires in `send_to_chat` (the final output delivery path, not nudges or interim messages).
- **Health check recovery guard**: In `_agent_session_health_check`, when evaluating whether to reset a `running` session to `pending`, check if `response_delivered_at` is set. If so, call `finalize_session(entry, "completed")` instead.
- **Relay delivery guard** (secondary): In `_record_sent_message` (called by the relay when it delivers an outbox message), also set `response_delivered_at` if not already set. This covers PM sessions whose final delivery goes through the relay rather than `send_to_chat`'s deliver action directly.

### Flow

Message arrives → Session runs → Response delivered → `response_delivered_at` stamped on session → Worker dies → Session stuck in `running` → Health check sees `response_delivered_at` set → Marks `completed` instead of resetting to `pending` → No duplicate execution

### Technical Approach

1. **`models/agent_session.py`**: Add `response_delivered_at = DatetimeField(null=True)` after `completed_at`.

2. **`agent/agent_session_queue.py`** — `send_to_chat` inner function:
   After the `action == "deliver"` branch sets `chat_state.completion_sent = True`, stamp the session:
   ```python
   try:
       session.response_delivered_at = datetime.now(UTC)
       session.save()
   except Exception:
       pass  # Non-fatal — best-effort guard
   ```

3. **`agent/agent_session_queue.py`** — `_agent_session_health_check`:
   Before the `should_recover` block executes `transition_status(entry, "pending", ...)`, add:
   ```python
   if getattr(entry, "response_delivered_at", None) is not None:
       finalize_session(
           entry,
           "completed",
           reason="health check: response already delivered, completing instead of re-running",
       )
       recovered += 1
       continue
   ```

4. **`bridge/telegram_relay.py`** — `_record_sent_message`:
   After `sessions[0].record_pm_message(msg_id)`, also stamp `response_delivered_at` if not already set:
   ```python
   if getattr(sessions[0], "response_delivered_at", None) is None:
       sessions[0].response_delivered_at = datetime.now(UTC)
       sessions[0].save()
   ```
   Note: `record_pm_message` already calls `save()`, so this requires a combined save or a second save. Prefer setting the field before the existing `save()` in `record_pm_message` (requires a small refactor of that method).

## Failure Path Test Strategy

### Exception Handling Coverage
- The `response_delivered_at` stamp in `send_to_chat` is wrapped in `try/except Exception: pass` — non-fatal by design. Test asserts that a save failure does NOT prevent session completion.
- The health check guard logs and continues — test asserts that if `response_delivered_at` is set, the session is completed (not re-queued) even if other fields are stale.

### Empty/Invalid Input Handling
- `getattr(entry, "response_delivered_at", None)` safely handles sessions missing the field (pre-migration records) — no code change needed there.
- Test: session without `response_delivered_at` field is correctly recovered (not prematurely completed).

### Error State Rendering
- No user-visible output changes — this fix operates entirely in the background.

## Test Impact

- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add test class `TestHealthCheckDeliveryGuard` covering (a) session with `response_delivered_at` is completed not re-queued, (b) session without `response_delivered_at` is recovered to pending as before
- [ ] `tests/unit/test_dedup.py` — UPDATE: add note/comment that `DedupRecord` handles intake dedup; this new field handles delivery dedup (no test change needed, but document distinction)

## Rabbit Holes

- **Using `pm_sent_message_ids` instead of new field**: `pm_sent_message_ids` is set for every PM relay message (status updates, questions, interim results) — not just final delivery. Using it as the guard would prematurely complete sessions that are mid-pipeline. Avoid.
- **Extending `DedupRecord` to cover delivery**: `DedupRecord` is per-chat and TTL-bound (2 hours). Extending it adds cross-concern coupling. The session model is the right place for this data.
- **Fixing session completion race directly**: The real fix to "why does the session stay in running?" requires deeper investigation into crash patterns. The delivery guard is the reliable safety net regardless of WHY completion fails.
- **Tracking all delivery actions (nudge, deliver_fallback, etc.)**: Only the `deliver` action represents final output. Other actions re-enqueue the session intentionally. Don't stamp `response_delivered_at` on nudge paths.

## Risks

### Risk 1: False positive completion for interrupted sessions
**Impact:** A session delivers an interim response, worker dies, health check sees `response_delivered_at` set, marks as `completed` — but the session had more work to do (e.g., mid-pipeline PM session).
**Mitigation:** Only stamp `response_delivered_at` on the `action == "deliver"` path, NOT on `nudge_continue`, `nudge_empty`, or other paths. The `deliver` action is the explicit terminal delivery decision. PM sessions mid-pipeline always use `nudge_continue`, never `deliver`, until the full pipeline is done.

### Risk 2: `save()` fails and field is never set
**Impact:** Session is recovered and re-run despite already delivering output.
**Mitigation:** This is best-effort. A failed save leaves the system in its current (broken) behavior, not in a worse state. Log the failure. The health check minimum runtime guard (5 minutes) still throttles excessive re-runs.

## Race Conditions

### Race 1: Concurrent save between `response_delivered_at` stamp and health check read
**Location:** `agent/agent_session_queue.py` — `send_to_chat` and `_agent_session_health_check`
**Trigger:** Session delivers response, stamps `response_delivered_at`, then health check reads the session from Redis before the save completes
**Data prerequisite:** `response_delivered_at` must be persisted in Redis before health check reads the session
**Mitigation:** The health check runs every 300 seconds. The `save()` is synchronous within the async context. Redis writes are atomic. The 5-minute guard means there's a significant window for the save to complete before the health check fires. Worst case: one extra re-run on the very first health check cycle after a crash that happened within 1s of the save.

### Race 2: Health check fires between session delivery and `_complete_agent_session`
**Location:** `agent/agent_session_queue.py` — `_execute_agent_session` completion path
**Trigger:** Session calls `send_to_chat` (sets `response_delivered_at`), then before `_complete_agent_session` runs, health check fires and sees the session in `running`
**Mitigation:** Health check will see `response_delivered_at` set → calls `finalize_session("completed")`. The completion path may then also call `_complete_agent_session`. The `_complete_agent_session` function already handles the case where the session is no longer in `running` state (it falls back to the fresh re-read).

## No-Gos (Out of Scope)

- Fixing why sessions fail to complete after delivering (separate investigation)
- Reducing `AGENT_SESSION_HEALTH_MIN_RUNNING` timeout (separate tuning decision)
- Deduplicating at the Telegram message send level (different layer, different problem)
- Tracking partial/interim deliveries with separate timestamps

## Update System

No update system changes required — this feature is purely internal. The new `response_delivered_at` field is nullable and handled by Popoto's graceful defaults; no migration needed on existing installations.

## Agent Integration

No agent integration required — this is a worker/health-check internal change. No MCP servers, bridge intake, or `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` to mention the `response_delivered_at` delivery guard as part of the health check recovery logic
- [ ] Add inline docstring to `_agent_session_health_check` explaining the delivery guard

## Success Criteria

- [ ] `response_delivered_at` field exists on `AgentSession` model
- [ ] `send_to_chat`'s `deliver` action stamps `response_delivered_at` on the session
- [ ] `_record_sent_message` in relay stamps `response_delivered_at` if not already set
- [ ] `_agent_session_health_check` completes sessions with `response_delivered_at` set instead of recovering to pending
- [ ] Unit tests cover both branches: session with and without `response_delivered_at`
- [ ] Simulated recovery scenario: session delivered + crashed → health check marks completed (not re-queued)
- [ ] Tests pass (`pytest tests/unit/test_agent_session_queue.py tests/unit/test_dedup.py -x -q`)

## Team Orchestration

### Team Members

- **Builder (session-dedup)**
  - Name: session-dedup-builder
  - Role: Implement the `response_delivered_at` field, set it in send_to_chat and relay, add guard in health check
  - Agent Type: builder
  - Resume: true

- **Validator (session-dedup)**
  - Name: session-dedup-validator
  - Role: Verify implementation meets all success criteria and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `response_delivered_at` field and stamp it
- **Task ID**: build-field
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py`, `tests/unit/test_dedup.py`
- **Assigned To**: session-dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `response_delivered_at = DatetimeField(null=True)` to `models/agent_session.py` after `completed_at`
- In `agent/agent_session_queue.py` `send_to_chat` inner function, after `action == "deliver"` sets `chat_state.completion_sent = True`, stamp `session.response_delivered_at = datetime.now(UTC)` and save (non-fatal try/except)
- In `bridge/telegram_relay.py` `_record_sent_message`, after calling `record_pm_message`, also set `response_delivered_at` on the session if not already set
- In `agent/agent_session_queue.py` `_agent_session_health_check`, add guard before `transition_status(entry, "pending", ...)`: if `response_delivered_at` is set, call `finalize_session(entry, "completed", ...)` and continue
- Write unit tests in `tests/unit/test_agent_session_queue.py`: `TestHealthCheckDeliveryGuard` with cases for (a) session with `response_delivered_at` → completed, (b) session without → recovered to pending

### 2. Validate implementation
- **Task ID**: validate-field
- **Depends On**: build-field
- **Assigned To**: session-dedup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `response_delivered_at` field exists in `models/agent_session.py`
- Verify `send_to_chat` stamps the field on `deliver` action
- Verify `_record_sent_message` stamps the field
- Verify `_agent_session_health_check` guard is present and correct
- Run `pytest tests/unit/test_agent_session_queue.py -x -q` — must pass
- Confirm no existing tests broken

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-field
- **Assigned To**: session-dedup-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` to mention delivery guard
- Add inline docstring update to `_agent_session_health_check`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_session_queue.py tests/unit/test_dedup.py -x -q` | exit code 0 |
| Field exists | `grep -n "response_delivered_at" models/agent_session.py` | output contains response_delivered_at |
| Health guard present | `grep -n "response_delivered_at" agent/agent_session_queue.py` | output > 0 |
| Relay guard present | `grep -n "response_delivered_at" bridge/telegram_relay.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
