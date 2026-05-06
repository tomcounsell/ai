---
status: Critique-Resolved
revision_applied: true
type: bug
appetite: Small
owner: Valor Engels
created: 2026-05-06
tracking: https://github.com/valorengels/ai/issues/1313
last_comment_id:
---

# Stalled Session User-Visible Alert

## Problem

When `monitoring/session_watchdog.py` detects a stalled session (default >300s in `pending`), it emits a `LIFECYCLE_STALL` warning to the worker log and does nothing else. On 2026-05-06, session `tg_cuttlefish_-5295380350_9642` sat pending for over 5 hours; the watchdog logged 60+ stall warnings; the user (the CEO) saw silence on Telegram and assumed "agent is thinking." Silent failure compounds short outages into long ones because no human-visible signal triggers an investigation.

**Current behavior:**
- `monitoring/session_watchdog.py:367-376` -- `logger.warning("LIFECYCLE_STALL ...")` is the only output of the stall branch.
- The watchdog runs in the worker process. It does not import telethon and has no Telegram client.
- `AgentSession` already carries `chat_id` and the property `telegram_message_id` (extracted from the `initial_telegram_message` dict).

**Desired outcome:**
- When a session is stalled past its threshold AND has an originating Telegram message, the watchdog queues a warning reaction emoji on that message. Exactly once per stall period.
- The existing `LIFECYCLE_STALL` warning log is preserved -- this is an *additional* user-visible channel.
- Sessions without an originating Telegram message are skipped silently.
- Re-stalls (session leaves `pending` and returns) reset the dedup so a fresh reaction lands.

## Freshness Check

**Baseline commit:** `455bfa17`
**Issue filed at:** 2026-05-06T10:41:56Z (~6h before plan time 2026-05-06T16:29Z)
**Disposition:** Unchanged

**File:line references re-verified:**
- `monitoring/session_watchdog.py:367-376` -- `LIFECYCLE_STALL` warning emission point. Still holds at lines 367-376 verbatim. Correct insertion point.
- `monitoring/session_watchdog.py:402-498` -- `_inject_watchdog_steer`. Still present, exact pattern shape to follow (env-flag gate, atomic `SET NX EX` cooldown, `logger.warning` on success, fail-quiet on exceptions).
- `models/agent_session.py:973-990` -- `telegram_message_id` property over `initial_telegram_message` dict. Still holds. Returns `int | None`.
- `bridge/response.py:258` -- `set_reaction` exists. Note: irrelevant to the fix because we'll write to the outbox, not call set_reaction directly.

**Cited sibling issues/PRs re-checked:**
- #777 -- closed (timezone fix). No regression risk for this work.
- #402 -- closed.
- #1128 -- closed (added `_inject_watchdog_steer`). The pattern shape we mirror.
- #1250 -- open. Orthogonal: detects stalled SDLC PRs, not user-facing escalation. No conflict.

**Commits on main since issue was filed (touching referenced files):**
- `git log --since=2026-05-06T10:00:00Z` on `monitoring/session_watchdog.py models/agent_session.py bridge/telegram_relay.py agent/output_handler.py` returned no commits. Code is pristine relative to the issue.

**Active plans in `docs/plans/` overlapping this area:** None. `emoji-embedding-reactions.md` (similar name) is `status: Merged` and unrelated (vector-search reactions, not stall alerts).

**Notes:** During investigation I confirmed an even cheaper path than the issue's "bridge subscriber" sketch: `agent/output_handler.py:763-820` already exposes a `_build_reaction_payload` schema and `bridge/telegram_relay.py:84-143` already drains `type:"reaction"` payloads from `telegram:outbox:{session_id}`. The watchdog can write a reaction payload directly to that outbox -- no new pubsub channel, no new bridge-side subscriber.

## Prior Art

- **PR #344**: Fix session stuck in pending after BUILD COMPLETED -- addressed a different stall root cause (state transition bug). Does not overlap with user-facing escalation.
- **Issue #1128**: Reliability: watchdog hardening -- added `_inject_watchdog_steer` for `repetition`, `error_cascade`, `token_alert` triggers. The shape and idempotency guarantees we mirror.
- **Plan `emoji-embedding-reactions.md`**: shipped (Merged). Different feature (semantic embedding for reactions); shares no code with this work.

No prior fixes specifically for user-visible stall alerts. Greenfield branch in the watchdog's escalation surface.

## Research

No external research required -- this is purely internal (Telethon API patterns, Redis outbox, in-process watchdog logic, all already-coded in the repo).

## Data Flow

1. **Entry point**: Watchdog tick (every ~5 minutes) calls `check_stalled_sessions()` in `monitoring/session_watchdog.py`.
2. **Detection**: For each `pending`/`running`/`active` session, compute `duration` against per-status `threshold`. When `duration > threshold`, log the existing `LIFECYCLE_STALL` warning.
3. **NEW -- escalation branch**: After the warning, call `_apply_stall_reaction(session)`:
   - Atomic `SET NX EX` on `watchdog:stall_reaction_applied:{session_id}` (TTL = 1 day) -- short-circuits if already applied this stall period.
   - Skip if `session.chat_id` and `session.telegram_message_id` aren't both populated.
   - Write a reaction payload (`type:"reaction"`, `emoji:"⏳"`, `chat_id`, `reply_to=telegram_message_id`, `session_id=session.session_id`) to `telegram:outbox:{session.session_id}` via `RPUSH` + `EXPIRE` (TTL matches existing OUTBOX_TTL pattern in `agent/output_handler.py`).
4. **Handoff to bridge**: The bridge process's `bridge/telegram_relay.py::_send_queued_reaction` (already running) drains the outbox key on its normal poll, calls `set_reaction` over Telethon, and the user sees ⏳ on their original message.
5. **Stall-recovery path** (new): When a session transitions out of `pending`/`running` into a healthy or terminal state, `DELETE` the `watchdog:stall_reaction_applied:{session_id}` key so re-stalls trigger a fresh reaction.

## Architectural Impact

- **New dependencies**: None. Reuses Redis (already imported), the outbox key pattern (already used by `_rtr_queue_reaction`), and the `_build_reaction_payload` schema (already canonical in `agent/output_handler.py`).
- **Interface changes**: None. Adds one private function `_apply_stall_reaction(session)` to `monitoring/session_watchdog.py`.
- **Coupling**: No new coupling. Watchdog already writes to Redis (lifecycle history, cooldowns). Reaction outbox is a writer-multiple, reader-one pattern; the bridge does not need to know the watchdog is a writer.
- **Data ownership**: Unchanged. Bridge owns Telethon I/O; watchdog stays Telethon-free.
- **Reversibility**: Trivially reversible -- gate behind `WATCHDOG_STALL_REACTION_ENABLED` env flag (default on, mirror of `WATCHDOG_AUTO_STEER_ENABLED`). Set to `0` to disable.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is locked by the issue)
- Review rounds: 1 (PR review)

The diff lands in one file (`monitoring/session_watchdog.py`) with one helper function, one env flag, and one Redis key. Tests are unit-level plus one integration test.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge has reaction relay running | `grep -n "_send_queued_reaction" bridge/telegram_relay.py` | Confirms the outbox consumer exists |
| Reaction payload schema | `grep -n "_build_reaction_payload" agent/output_handler.py` | Confirms schema source of truth |
| `telegram_message_id` property | `grep -n "def telegram_message_id" models/agent_session.py` | Confirms session exposes the field |

## Solution

### Key Elements

- **`_apply_stall_reaction(session)`**: New private helper in `monitoring/session_watchdog.py`. Mirrors the shape of `_inject_watchdog_steer`: env-flag gate, atomic `SET NX EX` dedup, fail-quiet on exceptions.
- **Outbox write**: Reuses `telegram:outbox:{session_id}` and the canonical reaction payload schema. Inlines the small payload literal (avoids importing async output_handler into the watchdog).
- **Dedup key**: `watchdog:stall_reaction_applied:{session_id}` with 1-day TTL via `SET NX EX`. Idempotent under concurrent ticks.
- **Re-stall reset**: When a session leaves `pending`/`running`/`active` and lands in a terminal state (or transitions back to a healthy state), delete the dedup key so the next stall triggers a new reaction.
- **Emoji choice**: ⏳ (hourglass). Visually distinct from existing reactions in the bridge (👀 received/looking, 🔥 drafting, 👍 work done) and reads as "stalled / waiting too long." Verified against `bridge/update.py:98,171` and `bridge/routing.py:1087`.

### Flow

Watchdog tick → stall detected (>300s pending) → `LIFECYCLE_STALL` log emitted (unchanged) → `_apply_stall_reaction(session)` → dedup key claimed → reaction payload pushed to `telegram:outbox:{session.session_id}` → bridge relay drains → ⏳ visible on user's original message.

### Technical Approach

- **Insertion point**: `monitoring/session_watchdog.py` line 376 (immediately after the existing `logger.warning("LIFECYCLE_STALL ...")` call). One new function call: `_apply_stall_reaction(session)`.
- **Helper function** (new, ~40 lines):
  ```python
  def _apply_stall_reaction(session: AgentSession) -> bool:
      if not _env_flag_enabled("WATCHDOG_STALL_REACTION_ENABLED"):
          return False
      try:
          chat_id = getattr(session, "chat_id", None)
          msg_id = getattr(session, "telegram_message_id", None)
          session_id = session.session_id or session.agent_session_id
          if not (chat_id and msg_id and session_id):
              return False  # silent skip -- local sessions, no original message
          dedup_key = f"watchdog:stall_reaction_applied:{session_id}"
          slot_open = POPOTO_REDIS_DB.set(dedup_key, "1", nx=True, ex=86400)
          if not slot_open:
              return False
          payload = {
              "type": "reaction",
              "chat_id": str(chat_id),
              "reply_to": int(msg_id),
              "emoji": STALL_REACTION_EMOJI,  # "⏳"
              "session_id": session_id,
              "timestamp": time.time(),
          }
          queue_key = f"telegram:outbox:{session_id}"
          POPOTO_REDIS_DB.rpush(queue_key, json.dumps(payload))
          POPOTO_REDIS_DB.expire(queue_key, OUTBOX_TTL)
          logger.warning("[watchdog] Stall reaction queued for %s (chat=%s msg=%s)",
                         session_id, chat_id, msg_id)
          return True
      except Exception as e:
          logger.warning("[watchdog] Failed to queue stall reaction for %s: %s",
                         session.session_id, e)
          return False
  ```
- **Re-stall reset hook**: At the existing point where session status transitions from `pending`/`running`/`active` to a healthy or terminal state, delete the dedup key. The cleanest insertion is in `monitoring/session_watchdog.py`'s `assess_session_health` (or its caller in the same module). Final placement to be selected at build time after one quick read of the recovery branch.
- **Env flag**: `WATCHDOG_STALL_REACTION_ENABLED` (default on; falsy values: `0`, `false`, `no`). Identical semantics to `WATCHDOG_AUTO_STEER_ENABLED`.
- **Constants**: `STALL_REACTION_EMOJI = "⏳"`, `STALL_REACTION_DEDUP_TTL = 86400` (1 day). Place at module top with other watchdog constants.
- **Schema reuse strategy**: The payload dict matches `_build_reaction_payload` byte-for-byte. We do NOT call `_build_reaction_payload` directly because `agent/output_handler.py` is async-handler code; importing into the watchdog risks a cycle. Instead, keep the payload literal in `_apply_stall_reaction` and add a unit test asserting parity with `_build_reaction_payload(...)` so any schema drift fails CI.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_apply_stall_reaction` wraps the entire body in `try/except Exception`. Test asserts that on Redis failure (mocked to raise), the function returns `False`, logs a warning, and the surrounding `check_stalled_sessions` loop continues to the next session.

### Empty/Invalid Input Handling
- [ ] Test: session with no `initial_telegram_message` dict → returns `False`, no Redis writes.
- [ ] Test: session with `chat_id` set but `telegram_message_id` is None → returns `False`, no Redis writes.
- [ ] Test: session with valid `chat_id` and `telegram_message_id=0` → returns `False` (treat 0 as falsy).
- [ ] Test: `session_id` is empty string → returns `False`.

### Error State Rendering
- [ ] N/A -- the watchdog renders no UI. The user-visible artifact is the reaction emoji, validated by the manual test in Success Criteria.

## Test Impact

- [ ] `tests/unit/test_stall_detection.py` -- UPDATE: add a test class `TestStallReaction` covering: (a) reaction queued on first stall detection, (b) reaction NOT re-queued on second tick within dedup TTL, (c) skip when no `telegram_message_id`, (d) skip when env flag is `0`, (e) Redis exception is fail-quiet, (f) payload schema matches `_build_reaction_payload`.
- [ ] `tests/integration/test_watchdog_to_bridge.py` (NEW) -- integration: drive a real watchdog tick against a stale-pending fixture session, assert the reaction shows up in `telegram:outbox:{session_id}` with the right schema, and assert the bridge relay's drain function recognizes it as a `type:"reaction"` payload.
- [ ] No existing tests are deleted. The change is additive: the warning log is preserved, the existing stall return value (list of dicts) is unchanged.

## Rabbit Holes

- **Don't redesign the watchdog.** This is a single-function add. Resist the temptation to refactor `check_stalled_sessions` for "clarity" -- keep the diff surgical.
- **Don't introduce a new pubsub channel.** The outbox-based reaction queue already works cross-process. Inventing a `notifications:stall:*` Redis pubsub channel is wasted work.
- **Don't import telethon into the watchdog.** The bridge owns Telegram I/O. The watchdog writes to the outbox; the relay calls `set_reaction`. Crossing this boundary breaks the worker/bridge separation.
- **Don't implement the 30-min stretch goal in v1.** The issue lists it as "stretch, only if surgically simple." Posting a status reply requires deciding which chat (`dev_chat_id`?), suppressing repeats, and threading project config through the watchdog -- all non-trivial. Defer to a follow-up if user requests it.
- **Don't add per-project tunable thresholds.** Use the existing `STALL_THRESHOLDS` constants. Tunability can be a separate issue.
- **Don't replace the warning log.** It stays. The reaction is *additional* signal.

## Risks

### Risk 1: Reaction outbox key TTL races with the bridge's drain
**Impact:** If the bridge relay is down longer than the outbox TTL, a queued stall reaction expires before delivery. User never sees ⏳.
**Mitigation:** Use the same TTL the existing `_rtr_queue_reaction` uses (`OUTBOX_TTL` from `agent/output_handler.py`). The bridge being down for >TTL is a bigger-than-watchdog incident; the watchdog log warning still fires, and the next tick will re-queue once the bridge returns and the dedup key TTL expires.

### Risk 2: Dedup key leaks across actual session lifetimes
**Impact:** A `session_id` is reused (rare in production, possible in test fixtures); old dedup key suppresses a fresh reaction.
**Mitigation:** 1-day TTL bounds the leak window. The re-stall reset hook deletes the key on healthy transitions. `session_id` is timestamp-derived in production, so realistic collisions are test-only -- and tests must clean their own Redis keys.

### Risk 3: Schema drift between watchdog payload literal and `_build_reaction_payload`
**Impact:** If `_build_reaction_payload`'s schema changes (e.g., a new field), the watchdog-emitted payload diverges and the bridge relay rejects it.
**Mitigation:** Unit test asserts byte-for-byte parity between the watchdog's literal and `_build_reaction_payload(...)`. Test fails on drift; CI catches it.

### Risk 4: ⏳ emoji not in Telegram's allowed reactions for the chat
**Impact:** Reaction send fails silently; user sees nothing.
**Mitigation:** ⏳ is in Telegram's standard free reaction set. The relay's `set_reaction` already handles unknown-emoji failure (logs and moves on). If a real chat rejects ⏳, swap `STALL_REACTION_EMOJI` to ⚠️ or ❗ -- a one-line change.

## Race Conditions

### Race 1: Two watchdog ticks overlap on the same stalled session
**Location:** `monitoring/session_watchdog.py::_apply_stall_reaction`
**Trigger:** Watchdog tick latency > tick interval (rare, but possible under Redis slowness). Two ticks both observe the same session as stalled and both attempt to queue.
**Data prerequisite:** None -- the dedup key is the synchronization primitive itself.
**State prerequisite:** `watchdog:stall_reaction_applied:{session_id}` does not exist before the first tick.
**Mitigation:** `SET NX EX` is atomic. The losing tick observes `slot_open=False` and short-circuits. Same pattern used by `_inject_watchdog_steer:466-473`.

### Race 2: Session recovers (leaves pending) between watchdog detection and outbox write
**Location:** `monitoring/session_watchdog.py::check_stalled_sessions` → `_apply_stall_reaction`
**Trigger:** A session transitions out of `pending` immediately after the watchdog reads it.
**Data prerequisite:** Session is no longer pending by the time the reaction is delivered.
**State prerequisite:** None -- this is benign.
**Mitigation:** No mitigation needed. A spurious "we noticed it stalled" reaction is acceptable -- the user will see the recovery in the form of a real bot message that follows. The dedup key will be cleared by the re-stall reset hook, so the next genuine stall will alert.

## No-Gos (Out of Scope)

- 30-minute "post one status reply in dev chat" stretch goal -- deferred to a follow-up issue.
- Configurable thresholds via env / `projects.json` -- use existing `STALL_THRESHOLDS`.
- Replacing the `LIFECYCLE_STALL` log warning with the reaction -- the log stays.
- DM-style alerts to operators -- out of scope for v1.
- Per-project emoji choice -- single emoji `⏳` for all stalls.
- Building a new bridge subscriber/pubsub channel -- reuse `telegram:outbox:*`.
- Touching `_inject_watchdog_steer` itself -- additive only.

## Update System

No update system changes required -- this feature is purely internal (single-file diff in `monitoring/`, no new deps, no new config files, no migration steps for existing installations). The new env flag `WATCHDOG_STALL_REACTION_ENABLED` defaults on; existing machines need no `.env` updates.

## Agent Integration

No agent integration required -- this is a watchdog-internal change that produces user-visible Telegram reactions via the existing bridge relay. No new CLI entry point, no new bridge import, no new MCP tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` -- add a short subsection "User-visible stall alerts" describing the ⏳ reaction, the env flag, and the dedup-key Redis schema.
- [ ] Update `docs/features/session-lifecycle.md` if it documents lifecycle transitions -- note the new dedup-key reset on healthy/terminal transitions.

### Inline Documentation
- [ ] Module-level docstring in `monitoring/session_watchdog.py` -- add bullet to the watchdog actuators list (currently lists steer triggers from #1128).
- [ ] Function docstring on `_apply_stall_reaction` -- mirror the depth and shape of `_inject_watchdog_steer`'s docstring.

## Success Criteria

- [ ] Stalled `pending` session with originating Telegram message receives exactly one ⏳ reaction per stall period.
- [ ] Stalled session without `initial_telegram_message` populated → no reaction queued, no exception, no log spam.
- [ ] After the session recovers and stalls again, a new ⏳ reaction is queued (dedup key reset).
- [ ] Existing `LIFECYCLE_STALL` log warning still emitted unchanged.
- [ ] `WATCHDOG_STALL_REACTION_ENABLED=0` disables the new behavior with no other side effects.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Manual test: enqueue a session with the worker stopped, wait >300s, observe ⏳ on the Telegram message.
- [ ] Manual test: with worker running normally for 5+ minutes of healthy work, no false-positive ⏳ reactions appear.

## Team Orchestration

### Team Members

- **Builder (watchdog-escalation)**
  - Name: `watchdog-builder`
  - Role: Add `_apply_stall_reaction`, env flag, dedup key, and the recovery hook to `monitoring/session_watchdog.py`. Update inline docstrings.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `watchdog-tester`
  - Role: Add unit tests in `tests/unit/test_stall_detection.py` and integration test for outbox handoff.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `watchdog-doc`
  - Role: Update `docs/features/bridge-self-healing.md` and `docs/features/session-lifecycle.md` with the new escalation surface.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `watchdog-validator`
  - Role: Verify the diff is single-file (plus tests/docs), the env flag works, and the schema parity test passes.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement `_apply_stall_reaction` and wire it into `check_stalled_sessions`
- **Task ID**: build-stall-reaction
- **Depends On**: none
- **Validates**: `tests/unit/test_stall_detection.py` (TestStallReaction class -- to be added)
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Add module constants `STALL_REACTION_EMOJI = "⏳"` and `STALL_REACTION_DEDUP_TTL = 86400`.
- Add `_apply_stall_reaction(session)` mirroring the shape of `_inject_watchdog_steer`.
- Insert call site immediately after the `logger.warning("LIFECYCLE_STALL ...")` block (around line 376).
- Add the env flag `WATCHDOG_STALL_REACTION_ENABLED` (default on) using `_env_flag_enabled`.
- Identify the recovery branch and add `POPOTO_REDIS_DB.delete(f"watchdog:stall_reaction_applied:{session_id}")` when a session transitions out of stall.
- Update module docstring to include the new escalation surface.

### 2. Add unit tests for stall reaction behavior
- **Task ID**: test-stall-reaction-unit
- **Depends On**: build-stall-reaction
- **Assigned To**: watchdog-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestStallReaction` class to `tests/unit/test_stall_detection.py`.
- Cover all 6 cases listed in Test Impact (queued first time, deduped second time, skip-no-msg-id, skip-flag-disabled, fail-quiet on Redis exception, schema parity).
- Use `fakeredis` or the existing test Redis fixture; never touch production keys.
- Verify tests pass with `pytest tests/unit/test_stall_detection.py -v`.

### 3. Add integration test for outbox handoff
- **Task ID**: test-stall-reaction-integration
- **Depends On**: build-stall-reaction
- **Assigned To**: watchdog-tester
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_watchdog_to_bridge.py`.
- Drive a real watchdog tick against a stale-pending fixture session.
- Assert the reaction payload lands in `telegram:outbox:{session_id}` with `type:"reaction"`, correct `chat_id`, `reply_to`, `emoji:"⏳"`.
- Assert `bridge.telegram_relay._send_queued_reaction` (called as a unit, not via real Telethon) accepts the payload shape without warning.

### 4. Update feature docs
- **Task ID**: document-stall-reaction
- **Depends On**: build-stall-reaction
- **Assigned To**: watchdog-doc
- **Agent Type**: documentarian
- **Parallel**: true
- Add "User-visible stall alerts" subsection to `docs/features/bridge-self-healing.md`.
- Update `docs/features/session-lifecycle.md` with the dedup-key reset note.
- Verify both docs render and link correctly.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-stall-reaction, test-stall-reaction-unit, test-stall-reaction-integration, document-stall-reaction
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm diff is one source file (`monitoring/session_watchdog.py`) plus tests and docs.
- Confirm `WATCHDOG_STALL_REACTION_ENABLED=0` disables behavior.
- Confirm schema parity test passes.
- Confirm `pytest tests/unit/test_stall_detection.py tests/integration/test_watchdog_to_bridge.py` is green.
- Confirm `python -m ruff check . && python -m ruff format --check .` is green.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_stall_detection.py tests/integration/test_watchdog_to_bridge.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/session_watchdog.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/session_watchdog.py` | exit code 0 |
| Helper exists | `grep -n "_apply_stall_reaction" monitoring/session_watchdog.py` | output contains `def _apply_stall_reaction` |
| Env flag wired | `grep -n "WATCHDOG_STALL_REACTION_ENABLED" monitoring/session_watchdog.py` | output > 0 |
| Schema parity | `pytest tests/unit/test_stall_detection.py::TestStallReaction::test_payload_matches_build_reaction_payload -x -q` | exit code 0 |
| Existing warning preserved | `grep -n "LIFECYCLE_STALL" monitoring/session_watchdog.py` | output > 0 |

## Critique Results

**Cycle 1 verdict:** READY TO BUILD (with concerns) — recorded 2026-05-06T16:42Z by `/do-plan-critique`.

The war-room verdict cleared the structural review (sections present, no internal contradictions, dependencies resolve, no plan-overlap, no schema-drift between the watchdog payload literal and `_build_reaction_payload`). No revision-blocking concerns were raised; the verdict's "with concerns" qualifier flagged the plan for builder attention on the items already enumerated in the plan itself rather than for re-planning.

### Implementation Notes (builder, read before starting)

These embed the war-room follow-ups directly into the plan so the builder does not need to re-derive them:

1. **Re-stall reset placement (Open Question 1) — decision**: implement on the **watchdog side** (delete the `watchdog:stall_reaction_applied:{session_id}` dedup key when the next tick observes the session in a healthy / non-stall state). This keeps the diff inside `monitoring/session_watchdog.py` only. The ≤5-minute window where ⏳ briefly persists after recovery is acceptable — the user will see the bot's recovery message land before the next tick clears the reaction. Do **not** thread this through `models/agent_session.py` lifecycle hooks.

2. **Emoji robustness (Open Question 2) — decision**: ship with `STALL_REACTION_EMOJI = "⏳"`. If a real-chat send fails, the relay's existing `set_reaction` error handling logs and moves on (no crash). Swap to `"⚠️"` is a one-line change behind the same constant if needed post-merge.

3. **Stretch goal (Open Question 3) — decision**: explicitly out of scope. Do **not** post a 30-min status reply in the dev chat in this PR. If the user wants it, file a follow-up issue after merge.

4. **Schema parity test is non-negotiable**: the `test_payload_matches_build_reaction_payload` unit test (Test Impact item f) MUST be implemented and green before PR. This is the only mechanical defense against schema drift between the watchdog's inlined payload literal and `agent/output_handler.py::_build_reaction_payload`. If `_build_reaction_payload` cannot be imported into the unit test cleanly (cycles), reconstruct the canonical payload via a thin helper in a test fixture rather than skipping the assertion.

5. **Recovery branch insertion**: the recovery hook in Step 1 of the task list says "Identify the recovery branch and add `POPOTO_REDIS_DB.delete(...)`". The cleanest insertion point is inside `check_stalled_sessions` itself: when iterating sessions, if a session is observed in a healthy state and a dedup key exists for it, delete the key in the same loop. This avoids creating a new entry point and keeps the change watchdog-internal.

6. **Manual test fidelity**: the manual test "enqueue a session with the worker stopped, wait >300s, observe ⏳" requires the bridge relay to remain running while the worker is stopped. Verify the bridge is up (`./scripts/valor-service.sh status` shows `bridge running`) before declaring the manual test passed. Otherwise the outbox accumulates and the reaction never delivers — that's not a code bug, but it WILL produce a false negative.

No structural revisions were applied to the plan body — every concern above is a build-time directive, not a re-design.

---

## Open Questions

1. **Re-stall reset placement**: should the dedup key be cleared in the watchdog's recovery branch (next tick observes session as healthy → delete) or at the lifecycle-history append point (status transition → delete)? The watchdog-side option is simpler (one file changed) but introduces a ≤5-minute window where the user sees ⏳ briefly after recovery before the next tick. The lifecycle-side option is cleaner but touches more files. Default for build: watchdog-side.
2. **Emoji robustness**: confirm ⏳ is in Telegram's free reaction set for the chats Valor's bridge serves. Fallback: ⚠️. Acceptable to ship with ⏳ and swap if a real-world test fails.
3. **Stretch (30-min status reply)**: explicitly out of scope per No-Gos; confirm no objection before a follow-up issue is filed.
