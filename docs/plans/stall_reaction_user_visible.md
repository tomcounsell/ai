---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-05-06
tracking: https://github.com/valorengels/ai/issues/1313
last_comment_id:
---

# Stalled Session — User-Visible Reaction

## Problem

When a session stalls (e.g., worker crashes, queue blocked, dev session wedged), the only signal today is `LIFECYCLE_STALL` warning lines in `logs/worker.log`. The user gets nothing — the Telegram message they sent looks accepted (a 👀 reaction was applied at receipt) and silence reads as "agent is thinking." On 2026-05-06, session `tg_cuttlefish_-5295380350_9642` (a "configure customer" request for Graham Derry) sat pending for 5+ hours; the watchdog logged 60+ stall warnings; no human knew anything was wrong.

**Current behavior:**
- `monitoring/session_watchdog.py:367-376` emits `logger.warning("LIFECYCLE_STALL ...")` whenever a non-terminal session crosses its threshold. That's the entire user-facing signal.
- The watchdog runs in the worker process and has no Telethon client — it cannot apply reactions directly.
- AgentSession carries `chat_id` (KeyField at line 161) and `telegram_message_id` (property at lines 973-990, backed by `initial_telegram_message`).
- The bridge already exposes `set_reaction()` (`bridge/response.py:258-321`) and the relay already accepts cross-process `type: "reaction"` payloads on `telegram:outbox:{session_id}` (`bridge/telegram_relay.py:84-144`). This is the established cross-process bus for reactions.

**Desired outcome:**
- When a session crosses its stall threshold and has an originating Telegram message, the watchdog enqueues a single warning-emoji reaction (e.g., ⏳) on `telegram:outbox:{session_id}`. The bridge's existing relay applies the reaction.
- Idempotent: at most one stall-reaction per stall period per session. Re-stalls (transition out of the stalled status and back in) reset the dedup so a fresh reaction can be applied.
- Sessions without an originating Telegram message are skipped silently.
- The existing `LIFECYCLE_STALL` log warning is preserved unchanged — the reaction is *additional*.

## Freshness Check

**Baseline commit:** `455bfa17bd3020986176f04f23d38e17688332e5`
**Issue filed at:** 2026-05-06T10:41:56Z (today, ~5h ago)
**Disposition:** Unchanged

**File:line references re-verified:**
- `monitoring/session_watchdog.py:262-387` — `check_stalled_sessions` body — confirmed; the warning is emitted at lines 367-376 exactly as the issue describes.
- `monitoring/session_watchdog.py:402-498` — `_inject_watchdog_steer` — confirmed; the right shape to follow for an additional escalation branch.
- `models/agent_session.py:973-990` — `telegram_message_id` property — confirmed.
- `bridge/response.py:258-321` — `set_reaction` — confirmed.

**Cited sibling issues/PRs re-checked:**
- #777 (timezone fix) — closed, irrelevant to this change.
- #402 (stall recovery / kill stuck worker) — closed, orthogonal to user-facing escalation.
- #1128 (watchdog hardening / `_inject_watchdog_steer`) — closed, the pattern reference.
- #1250 (stalled SDLC PR detection) — open, orthogonal.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since="2026-05-06T10:41:56Z"` returned no commits to `monitoring/session_watchdog.py`, `bridge/telegram_relay.py`, `bridge/response.py`, or `models/agent_session.py`.

**Active plans in `docs/plans/` overlapping this area:** None — no active plan touches `session_watchdog.py` or stall-handling.

**Notes:** Recon Summary in the issue body matches current code exactly. Proceed.

## Prior Art

`gh issue list --state closed --search "stall reaction watchdog telegram"` returned `[]`. No prior issue or PR has shipped a user-visible stall signal.

- **#1128 — Reliability: watchdog hardening** — closed; introduced `_inject_watchdog_steer` for repetition / error_cascade / token_alert categories. **Pattern reference**, not a prior fix for this same problem. Same shape applies here: per-session/per-reason cooldown via Redis `SET NX EX`, fail-quiet on errors, behind a feature gate.
- **#777 — `LIFECYCLE_STALL` duration inflated by UTC offset** — closed; fixed timezone math in the warning's duration field. Confirms the warning is well-formed today.
- **#402 — Watchdog stall recovery for pending sessions never kills stuck worker** — closed; addressed worker-side recovery, not user-facing signal.

## Research

No external research needed — this is purely internal: existing Redis bus, existing Telethon helper, existing watchdog. Skipping WebSearch.

## Spike Results

No spikes needed. Small appetite + the exact bus shape is already in production (`telegram_relay.py:84-144` accepts `type: "reaction"` payloads with `chat_id`, `reply_to`, `emoji`).

## Data Flow

1. **Entry point**: `check_stalled_sessions` tick (every ~5 min) iterates `pending`/`running`/`active` sessions.
2. **Stall detection**: For each session, `duration > threshold` triggers the existing warning branch at line 346-376.
3. **NEW: Escalation branch**: After `logger.warning(...)`, call `_apply_stall_reaction(session)`. That function:
   - Returns early if `session.stall_reaction_applied` is truthy.
   - Returns early if `session.chat_id` is missing or `session.telegram_message_id` is None (local sessions, sessions started without a Telegram trigger).
   - Returns early if the per-session cooldown key is closed (Redis `SET NX EX` on `watchdog:stall_reaction:{session_id}`, TTL = 1 hour).
   - Builds the reaction payload `{type: "reaction", chat_id, reply_to: telegram_message_id, emoji: "⏳", session_id, timestamp}` and `RPUSH`es it to `telegram:outbox:{session_id}` with `EXPIRE 3600`.
   - Sets `session.stall_reaction_applied = True` via `session.save(update_fields=["stall_reaction_applied"])`.
4. **Bridge relay**: The existing async loop in `bridge/telegram_relay.py` polls `telegram:outbox:*`, dispatches by `type`, and `_send_queued_reaction()` calls `set_reaction(client, chat_id, msg_id, "⏳")`.
5. **User outcome**: The user sees a ⏳ reaction land on their original Telegram message. They now know to check.
6. **Re-stall reset**: When a session leaves the stalled status (transitions to `running`/`completed`/etc., or back to `pending` on a fresh enqueue), a lifecycle hook clears `stall_reaction_applied` so the next stall period gets a fresh reaction.

## Why Previous Fixes Failed

No prior fixes targeted user-visible stall signal. Skipping.

## Architectural Impact

- **New dependencies**: None. Uses existing `telegram:outbox:*` bus and existing `set_reaction` helper.
- **Interface changes**: One new nullable field on `AgentSession` (`stall_reaction_applied: Field(null=True)` — Popoto bool-as-string convention, see `feedback_popoto_bool_storage`). One new private function `_apply_stall_reaction(session)` in `monitoring/session_watchdog.py`.
- **Coupling**: No increase. The watchdog still does NOT import telethon — it speaks Redis.
- **Data ownership**: AgentSession gains one tiny boolean. The bridge owns Telethon I/O.
- **Reversibility**: Trivial. Delete `_apply_stall_reaction`, remove its call site, remove the field. The field's null-safety means existing records keep working.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue Recon Summary)
- Review rounds: 1 (the standard SDLC review on the PR)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Outbox bus + cooldown key |
| Bridge process running for manual test | `./scripts/valor-service.sh status` | Relay must be alive to drain reaction payloads |

## Solution

### Key Elements

- **Watchdog escalation hook** (`_apply_stall_reaction(session)` in `monitoring/session_watchdog.py`): Single new private function called from inside the existing warning branch in `check_stalled_sessions`. Cross-process boundary respected — no telethon import in the worker process.
- **Cross-process bus**: Existing `telegram:outbox:{session_id}` Redis list, existing `type: "reaction"` payload schema. Zero new pubsub channels.
- **Per-session dedup**: New nullable field `AgentSession.stall_reaction_applied`. Set when reaction is enqueued. Cleared when the session leaves stalled status (lifecycle hook).
- **Per-session cooldown**: Redis `SET NX EX` on `watchdog:stall_reaction:{session_id}` with 1-hour TTL. Defense-in-depth against concurrent watchdog ticks racing the field write.
- **Feature gate**: `WATCHDOG_STALL_REACTION_ENABLED` env var (default on). Mirrors the `_inject_watchdog_steer` pattern from #1128.
- **Reaction emoji choice**: ⏳ (hourglass). Distinct from the bridge's existing reactions: 👀 receipt, 🔥 update.py reactor, 👍 routing acknowledge, 👌 success, 👏 complete, 😢 error. ⏳ reads as "this is taking longer than it should" without panic.

### Flow

User sends Telegram message → bridge applies 👀 (received) → enqueues AgentSession → worker stalls (e.g., crashed/wedged) → watchdog tick detects `duration > 300s` for `pending` → enqueues `type: "reaction" emoji: "⏳"` on outbox → bridge relay applies ⏳ on the user's original message → user sees the hourglass and knows to investigate.

### Technical Approach

1. Add `stall_reaction_applied = Field(null=True)` to `AgentSession`. Use the `_truthy()` helper convention when reading (Popoto stores bools as strings — see `feedback_popoto_bool_storage`).
2. Add a lifecycle hook in `models/session_lifecycle.py` (or wherever transitions clear watchdog state — confirm during build) that clears `stall_reaction_applied` when a session transitions out of a stalled status. If no central transition hook exists, clear it inside `check_stalled_sessions` whenever a session is observed in a non-stalled status with the flag set (passive heal).
3. Add `_apply_stall_reaction(session, stalled_info)` to `monitoring/session_watchdog.py`. Insert call after `logger.warning("LIFECYCLE_STALL ...")` at line 376.
4. The function:
   - Reads `WATCHDOG_STALL_REACTION_ENABLED` via the existing `_env_flag_enabled()` helper. Returns False if disabled.
   - Reads `session.stall_reaction_applied` via `_truthy()`. Returns False if already True.
   - Reads `session.chat_id` and `session.telegram_message_id`. Returns False if either is missing — local/internal sessions skip silently.
   - Validates `chat_id` is convertible to int (matches `_send_queued_reaction` which drops non-Telegram chat_ids at line 113-116).
   - Acquires per-session cooldown via Redis `SET NX EX watchdog:stall_reaction:{session_id} "1" 3600`. Returns False if slot was closed.
   - Builds payload, `RPUSH`es to `telegram:outbox:{session_id}`, sets `EXPIRE 3600`.
   - Sets `session.stall_reaction_applied = True` and saves with `update_fields=["stall_reaction_applied"]`.
   - Logs `logger.info("[watchdog] Stall reaction queued for %s", session_id)`.
   - All exceptions caught — fail-quiet, never crash the watchdog tick.
5. Reaction emoji is a constant at the top of the file: `STALL_REACTION_EMOJI = "⏳"`. Easy to retune later without touching call sites.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_apply_stall_reaction` wraps the whole body in `try/except Exception` and logs at `warning` level on failure (matches `_inject_watchdog_steer` shape). Test asserts the watchdog tick continues even when Redis is unavailable.
- [ ] If `session.save()` fails (e.g., transient Redis blip), the cooldown key is already set — so the next tick will skip via cooldown rather than spamming. Test simulates save failure and asserts no second enqueue on the next tick within the cooldown window.

### Empty/Invalid Input Handling
- [ ] Session with `initial_telegram_message = None` → `telegram_message_id` returns None → function returns False, no error.
- [ ] Session with `chat_id = None` → function returns False, no error.
- [ ] Session with `chat_id = "local"` (non-int) → function returns False (mirrors `_send_queued_reaction` line 113-116).
- [ ] Session with `stall_reaction_applied = "False"` (Popoto string) → `_truthy("False")` is False → enqueue proceeds.

### Error State Rendering
- [ ] If the bridge is offline, the payload sits on `telegram:outbox:{session_id}` until TTL expires (1h). The user sees no reaction, but the watchdog log still emits `LIFECYCLE_STALL` exactly as today. Test asserts the existing log warning is unchanged.

## Test Impact

- [ ] `tests/unit/test_session_watchdog.py` — UPDATE: add new test cases for `_apply_stall_reaction` covering: enqueues on first stall, skips on `stall_reaction_applied=True`, skips on missing `chat_id`, skips on missing `telegram_message_id`, skips on non-int `chat_id`, skips when feature gate disabled, fail-quiet on Redis exception, cooldown blocks second enqueue within 1h window.
- [ ] `tests/unit/test_session_watchdog.py::test_check_stalled_sessions_*` (existing) — UPDATE: assert that the existing `LIFECYCLE_STALL` log warning is still emitted unchanged after the new branch is inserted.
- [ ] `tests/unit/test_agent_session_model.py` — UPDATE: add a small assertion that `AgentSession(stall_reaction_applied=True).save()` round-trips correctly (Popoto bool-as-string sanity).
- [ ] `tests/integration/test_telegram_relay.py` (if it exists; otherwise add one) — REPLACE/CREATE: end-to-end test that pushing a `type: "reaction"` payload from a watchdog-style call site results in a `set_reaction` invocation on the relay side. Use existing test patterns from `tests/unit/test_send_telegram.py` (which already tests the reaction-payload shape).

## Rabbit Holes

- **Don't redesign the watchdog.** Add one branch and one function. Reference shape: `_inject_watchdog_steer` is ~96 lines and that includes its docstring; the new function should be smaller.
- **Don't add a stretch "30-min status reply in dev chat" in v1.** The issue lists it as stretch only-if-surgically-simple. It introduces a second escalation tier, a second dedup field, and chat-config lookup — too much for Small appetite. Defer to a follow-up issue if needed.
- **Don't tunable thresholds.** Use existing `STALL_THRESHOLDS`. Don't add env vars per-status.
- **Don't replace the log warning.** Logs stay.
- **Don't invent a new pubsub channel.** The `telegram:outbox:*` bus already does exactly this job.
- **Don't import telethon in the watchdog/worker process.** The whole point is the cross-process bus.

## Risks

### Risk 1: Reaction spam if dedup field write fails before cooldown is set
**Impact:** A persistent `session.save()` failure could in principle cause repeated enqueues each tick.
**Mitigation:** Set the Redis cooldown key BEFORE writing the field. Cooldown is the primary guard (1h TTL); the field is the secondary guard (survives Redis flushes). Order: enqueue → set cooldown → set field → save. If save fails, cooldown still suppresses the next tick.

### Risk 2: The user's Telegram message has been deleted
**Impact:** `set_reaction` returns False (logged at debug in `bridge/response.py:319-321`). The bridge already handles this; nothing additional needed.
**Mitigation:** None required — existing `set_reaction` failure path is fail-quiet. The watchdog already logs the warning regardless.

### Risk 3: New `stall_reaction_applied` field collides with existing field name on old records
**Impact:** Old AgentSession Redis hashes don't have this key — Popoto `Field(null=True)` reads return None, `_truthy(None)` is False, behavior is correct.
**Mitigation:** Field-backcompat heal is automatic (see `feedback_field_backcompat_heal`). No migration needed.

### Risk 4: Watchdog tick races itself across processes (e.g., dev + prod worker on the same Redis)
**Impact:** Two workers enqueue two reactions before either sets the cooldown.
**Mitigation:** Redis `SET NX EX` is atomic — only one watchdog wins the cooldown slot per cycle. The other returns False and skips. This is the same atomic primitive `_inject_watchdog_steer` already uses in production.

## Race Conditions

### Race 1: Concurrent watchdog ticks on the same session
**Location:** `monitoring/session_watchdog.py::_apply_stall_reaction`
**Trigger:** Two watchdog processes (or two ticks of one process if the loop ever overlaps) detect the same stalled session simultaneously.
**Data prerequisite:** None — both processes read `stall_reaction_applied=False`.
**State prerequisite:** Cooldown key must not exist.
**Mitigation:** Atomic `SET NX EX` on `watchdog:stall_reaction:{session_id}` is the sole gate before enqueue. Field write is secondary. This is the exact pattern `_inject_watchdog_steer` (line 467-473) uses and it's been in production since #1128.

### Race 2: Session transitions out of stalled while watchdog is enqueueing
**Location:** Between cooldown acquisition and `RPUSH`.
**Trigger:** Worker recovers and pushes the session to `running` while the watchdog enqueues a stall reaction.
**Data prerequisite:** Session is observably stalled at watchdog read time.
**State prerequisite:** None.
**Mitigation:** Acceptable — the user sees a transient ⏳ that immediately resolves when the bridge later applies a 👏 (complete) or 👌 (success). Both reactions can coexist on the same message; Telegram replaces with the most recent. If this turns out to be ugly in practice, follow-up work can clear the ⏳ on terminal transitions; out of scope for v1.

## No-Gos (Out of Scope)

- 30-minute "status reply in dev chat" stretch — defer to a separate issue.
- Configurable thresholds via env or projects.json — use existing `STALL_THRESHOLDS`.
- Replacing the log warning — logs stay.
- DM the user / page anyone — reaction emoji is the entire user-facing surface for v1.
- Custom emoji (`ReactionCustomEmoji`) — use a standard emoji. The custom-emoji code path exists in `set_reaction` but adds zero value here.
- Clearing the ⏳ on terminal transitions — Telegram displays the most recent reaction; bridge already applies 👏/👌/😢 on terminal states. Acceptable visual.
- Migration / backfill — new field is null-safe; old records work unchanged.

## Update System

No update system changes required. The change is internal — no new dependencies, no new config files, no new launchd services, no new env vars beyond the optional feature gate. The feature gate defaults to enabled, so deployment via the existing `/update` skill rolls it out automatically on next restart.

## Agent Integration

No agent integration required. This is a bridge/worker-internal change. The watchdog runs in the worker process; the relay runs in the bridge process; the agent never invokes either. No new MCP tool, no new CLI entry point.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-watchdog-reliability.md` to add a "User-Visible Stall Reaction" subsection describing the new behavior, the `WATCHDOG_STALL_REACTION_ENABLED` gate, and the chosen emoji.
- [ ] No new entry in `docs/features/README.md` — this is an enhancement to an existing feature.

### External Documentation Site
- N/A.

### Inline Documentation
- [ ] Docstring on `_apply_stall_reaction` mirroring the depth of `_inject_watchdog_steer` (purpose, cooldown contract, fail-quiet contract, feature gate).
- [ ] Inline comment near the new field in `models/agent_session.py` referencing issue #1313.

## Success Criteria

- [ ] When a `pending` session crosses 300s with `chat_id` and `telegram_message_id` populated, ⏳ appears on the user's original Telegram message exactly once per stall period.
- [ ] Sessions without `initial_telegram_message` are skipped silently — no exceptions, no log spam beyond the existing `LIFECYCLE_STALL` warning.
- [ ] When a session transitions out of a stalled status and re-stalls, a fresh ⏳ is applied (dedup flag was reset).
- [ ] Existing `LIFECYCLE_STALL` log warning is preserved unchanged (string match in test).
- [ ] Setting `WATCHDOG_STALL_REACTION_ENABLED=0` disables the new branch entirely; `LIFECYCLE_STALL` warning still fires.
- [ ] Manual test: stop worker, send a Telegram message, wait >300s, observe ⏳ on the message.
- [ ] Manual test: with worker healthy, no ⏳ appears within a 10-minute window across normal traffic.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff check .` and `python -m ruff format --check .` clean.

## Team Orchestration

### Team Members

- **Builder (watchdog escalation)**
  - Name: `watchdog-stall-reaction-builder`
  - Role: Add the field, the helper function, the call site, and the lifecycle reset.
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog escalation)**
  - Name: `watchdog-stall-reaction-validator`
  - Role: Verify all success criteria; run targeted unit + integration tests; verify the existing `LIFECYCLE_STALL` warning is intact.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `stall-reaction-documentarian`
  - Role: Update `docs/features/session-watchdog-reliability.md` with the new subsection.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add `stall_reaction_applied` field to AgentSession
- **Task ID**: build-field
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_model.py (round-trip assertion)
- **Informed By**: feedback_popoto_bool_storage memory (use `_truthy()` when reading), feedback_field_backcompat_heal (no migration needed)
- **Assigned To**: watchdog-stall-reaction-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `stall_reaction_applied = Field(null=True)` near the existing watchdog fields (line 213-214).
- Add an inline comment referencing issue #1313.

### 2. Add `_apply_stall_reaction` helper + call site
- **Task ID**: build-helper
- **Depends On**: build-field
- **Validates**: tests/unit/test_session_watchdog.py (new cases)
- **Informed By**: `_inject_watchdog_steer` shape (lines 402-498), `_send_queued_reaction` payload schema (lines 84-144 in `bridge/telegram_relay.py`).
- **Assigned To**: watchdog-stall-reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `STALL_REACTION_EMOJI = "⏳"` constant.
- Add `WATCHDOG_STALL_REACTION_ENABLED` gate via existing `_env_flag_enabled()`.
- Add `_apply_stall_reaction(session)` function. Body: feature-gate check → `_truthy(session.stall_reaction_applied)` early return → chat_id/telegram_message_id presence + int-castability checks → Redis `SET NX EX watchdog:stall_reaction:{session_id} "1" 3600` cooldown → build reaction payload → `RPUSH telegram:outbox:{session_id}` + `EXPIRE 3600` → `session.stall_reaction_applied = True; session.save(update_fields=["stall_reaction_applied"])` → log info. All wrapped in `try/except Exception` with warning log on failure.
- Insert call `_apply_stall_reaction(session)` after `logger.warning("LIFECYCLE_STALL ...")` at line 376.

### 3. Lifecycle reset of `stall_reaction_applied`
- **Task ID**: build-reset
- **Depends On**: build-field
- **Validates**: tests/unit/test_session_watchdog.py (re-stall test case)
- **Informed By**: `models/session_lifecycle.py` (existing transition handlers — confirm during build)
- **Assigned To**: watchdog-stall-reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Inspect `models/session_lifecycle.py` for the central status-transition hook. If one exists, clear `stall_reaction_applied` on transitions out of stalled statuses.
- If no central hook exists, add a passive heal at the top of `check_stalled_sessions`'s session loop: if a session is observed in a status whose threshold it has NOT crossed (i.e. healthy) and `_truthy(session.stall_reaction_applied)` is True, clear the flag. This handles both restarts and recoveries.

### 4. Unit tests for the new helper
- **Task ID**: build-tests
- **Depends On**: build-helper, build-reset
- **Validates**: tests/unit/test_session_watchdog.py
- **Informed By**: existing test patterns in `tests/unit/test_session_watchdog.py` and `tests/unit/test_send_telegram.py`
- **Assigned To**: watchdog-stall-reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Add cases for: enqueues on first stall; skips on already-applied; skips on missing chat_id; skips on missing telegram_message_id; skips on non-int chat_id; feature-gate-disabled returns early; cooldown blocks second enqueue; Redis exception is fail-quiet; existing `LIFECYCLE_STALL` warning still emitted alongside the new branch.

### 5. Integration test — outbox payload shape
- **Task ID**: build-integration-test
- **Depends On**: build-helper
- **Validates**: tests/integration/test_telegram_relay.py (create or extend)
- **Informed By**: `bridge/telegram_relay.py::_send_queued_reaction` (line 84-144)
- **Assigned To**: watchdog-stall-reaction-builder
- **Agent Type**: builder
- **Parallel**: true
- Push a watchdog-shaped reaction payload onto `telegram:outbox:{session_id}` and verify the relay's dispatcher routes it to the reaction handler with the right chat_id, msg_id, and emoji.

### 6. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-tests, build-integration-test, build-reset
- **Assigned To**: watchdog-stall-reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_watchdog.py tests/unit/test_agent_session_model.py tests/integration/test_telegram_relay.py -x -q`.
- Run `python -m ruff check monitoring/session_watchdog.py models/agent_session.py` and `python -m ruff format --check monitoring/session_watchdog.py models/agent_session.py`.
- Grep-confirm the existing `LIFECYCLE_STALL` warning string is still present unchanged in `session_watchdog.py`.
- Verify `_apply_stall_reaction` is called inside `check_stalled_sessions` after the warning log.
- Report pass/fail.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: stall-reaction-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Add "User-Visible Stall Reaction" subsection to `docs/features/session-watchdog-reliability.md` describing the new behavior, gate, emoji choice, and dedup logic.
- Cross-link from `docs/features/session-watchdog.md` if it covers stall detection at a high level.

### 8. Final validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: watchdog-stall-reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run full validator commands.
- Verify all Success Criteria are met.
- Confirm doc edits exist and read coherently.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_session_watchdog.py tests/unit/test_agent_session_model.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_telegram_relay.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/session_watchdog.py models/agent_session.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/session_watchdog.py models/agent_session.py` | exit code 0 |
| New helper exists | `grep -n '_apply_stall_reaction' monitoring/session_watchdog.py` | output > 0 |
| New helper is wired in | `grep -n '_apply_stall_reaction(session)' monitoring/session_watchdog.py` | output > 0 |
| Existing warning preserved | `grep -n 'LIFECYCLE_STALL session=' monitoring/session_watchdog.py` | output > 0 |
| Field added | `grep -n 'stall_reaction_applied' models/agent_session.py` | output > 0 |
| Doc subsection exists | `grep -n 'Stall Reaction' docs/features/session-watchdog-reliability.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. Does `models/session_lifecycle.py` have a central transition hook we should use to clear `stall_reaction_applied`, or is the passive heal inside `check_stalled_sessions` the right place? (The builder will inspect during task 3 and pick whichever is smaller.)
2. Reaction emoji: ⏳ vs ⚠️. The issue suggests both. ⏳ reads as "still working / slow"; ⚠️ reads as "needs attention." Default: ⏳, since the worker may still be alive and recover. Confirm or override.
