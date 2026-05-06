---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-05-06
tracking: https://github.com/tomcounsell/ai/issues/1313
last_comment_id:
---

# Stalled Session User-Visible Alert

## Problem

On 2026-05-06, session `tg_cuttlefish_-5295380350_9642` (a "configure customer" request for Graham Derry in the Cuttlefish project) sat in `pending` for over 5 hours. The watchdog logged 60+ `LIFECYCLE_STALL` warnings to `logs/worker.log`. The user saw nothing on Telegram — the `👀` "received" reaction the bridge applied at ingest stayed there, silence read as "agent is thinking", and the outage compounded.

**Current behavior:**
- `monitoring/session_watchdog.py:262-387` (`check_stalled_sessions`) detects sessions whose duration in their current state exceeds `STALL_THRESHOLDS[status]` (300s for `pending`, 2700s for `running`, 600s for `active`).
- Detection emits `logger.warning("LIFECYCLE_STALL ...")` at lines 367-376. That is the entire user-visible signal: a log line.
- The watchdog runs in the **worker process**. It has no Telegram client and must not import telethon (the bridge owns Telegram I/O).
- The bridge already runs a relay loop (`bridge/telegram_relay.py`) that consumes the `telegram:outbox:{session_id}` Redis queue and supports `type: "reaction"` payloads — see `_send_queued_reaction` at lines 84-144. `tools/react_with_emoji.py` is the existing producer pattern.

**Desired outcome:**
- When a session is detected stalled, the watchdog enqueues a single warning reaction (⏳) on the originating Telegram message via the existing outbox queue. The bridge's relay applies it.
- Re-detection on the same stall does not re-enqueue — at most one reaction per stall period.
- If the session ever transitions out of the stalled status and stalls again, the dedup flag resets so a new reaction can fire.
- Sessions without an originating Telegram message (local sessions, no `initial_telegram_message`) are silently skipped — no exceptions, no log spam.
- The existing `LIFECYCLE_STALL` log warning is preserved unchanged — this is **additional**, not a replacement.

## Freshness Check

**Baseline commit:** `fcbb93c4`
**Issue filed at:** 2026-05-06T10:41:56Z (today)
**Disposition:** Unchanged

**File:line references re-verified:**
- `monitoring/session_watchdog.py:262-387` — `check_stalled_sessions` body — still holds; warning emitted at 367-376 as cited.
- `monitoring/session_watchdog.py:402-498` — `_inject_watchdog_steer` — still the right shape reference for an additional escalation branch (cooldown via `SET NX EX`, fail-quiet, behind a feature gate).
- `models/agent_session.py:973-990` — `telegram_message_id` property over `initial_telegram_message` dict — still holds.
- `bridge/response.py:258-321` — `set_reaction(client, chat_id, msg_id, emoji)` — still holds.
- `bridge/telegram_relay.py:84-144` — `_send_queued_reaction` already handles `type: "reaction"` payloads with `chat_id`, `reply_to`, `emoji` keys, drops non-Telegram chat_ids silently.

**Cited sibling issues/PRs re-checked:**
- #777 — closed 2026-04-07 (timezone fix; `LIFECYCLE_STALL` duration now correct). No effect on this plan.
- #402 — closed 2026-03-14 (kill-stuck-worker recovery). Orthogonal.
- #1128 — closed 2026-04-23 (`_inject_watchdog_steer` introduced). Reference shape for this work.
- #1250 — open; orthogonal (detects stalled SDLC PRs, different state).

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/emoji-embedding-reactions.md` — orthogonal: covers AI-driven emoji selection for agent reactions, not watchdog stalls. The reaction payload shape this plan uses is the same one that plan documented (`type: "reaction"` outbox).

**Notes:** All claims hold. No drift.

## Prior Art

- **#1128**: "Reliability: watchdog hardening" — added `_inject_watchdog_steer` to inject steering messages on `repetition`, `error_cascade`, `token_alert`. Same insertion-point pattern (extra branch inside the watchdog tick), same fail-quiet / cooldown discipline. We mirror this shape for the stall-reaction branch.
- **#777**: "Bug: LIFECYCLE_STALL duration inflated by UTC offset" — closed (timezone fix). Confirms the warning string and its reliability today.
- **#344**: "Fix session stuck in pending after BUILD COMPLETED" — merged. Orthogonal: that fix unstuck a specific status-transition bug; it didn't add user-facing escalation.
- **`docs/features/emoji-embedding-reactions.md`** / `tools/react_with_emoji.py`: established the `telegram:outbox:{session_id}` reaction-payload contract. We reuse it verbatim — no new pubsub channel.

## Research

No relevant external findings — proceeding with codebase context. The mechanism is fully internal: existing Redis queue, existing relay consumer, existing `set_reaction`. Nothing to look up externally.

## Architectural Impact

- **New dependencies:** none. The watchdog already has Redis access (via `popoto.redis_db.POPOTO_REDIS_DB` used by `_inject_watchdog_steer`).
- **Interface changes:** one new field on `AgentSession`: `stall_reaction_applied: bool = Field(default=False)`. Additive, backcompat-safe — `_heal_descriptor_pollution` walks all fields generically (per memory `feedback_field_backcompat_heal`), no migration needed.
- **Coupling:** unchanged. The watchdog stays in the worker process; the bridge stays the sole telethon owner. Communication is via the **existing** Redis outbox queue — same channel `tools/react_with_emoji.py` uses.
- **Data ownership:** the dedup flag lives on the AgentSession record, owned by the watchdog (writer) and reset when the session transitions out of the stalled status (writer: lifecycle code or a small reset hook).
- **Reversibility:** trivially revertable. Removing the new branch from `check_stalled_sessions` and the field from `AgentSession` removes the feature.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Single-file changes in two files plus tests. No design ambiguity.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable from worker | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Watchdog must enqueue to outbox |
| Bridge relay running | `./scripts/valor-service.sh status` | Relay drains the queue and applies the reaction |

Run all checks: `python scripts/check_prerequisites.py docs/plans/stalled-session-user-visible-alert.md`

## Solution

### Key Elements

- **`AgentSession.stall_reaction_applied`**: bool flag, default False. Set True by the watchdog after enqueueing the warning reaction; reset to False when the session transitions out of the currently-stalled status.
- **`_apply_stall_reaction(session)`** in `monitoring/session_watchdog.py`: reads `chat_id` and `telegram_message_id` from the session, builds the `type: "reaction"` payload, RPUSHes onto `telegram:outbox:{session_id}` with a 1h TTL, sets the dedup flag. Fails silently on missing fields or Redis errors.
- **Reset hook**: when the session's status changes between watchdog ticks (it left `pending` and came back, or moved to `running` and stalled there), the watchdog clears `stall_reaction_applied` so the next stall in a new state can re-fire.
- **Feature gate**: `WATCHDOG_STALL_REACTION_ENABLED` env var (default on), matching the `_env_flag_enabled` discipline used by `_inject_watchdog_steer`.
- **Reaction emoji**: `⏳` (hourglass) — distinct from `👀` (received), `🤔` (processing), `🫡` (abort), and any happy-path emoji.

### Flow

Worker watchdog tick → `check_stalled_sessions` detects stall → existing `logger.warning("LIFECYCLE_STALL ...")` → **new**: `_apply_stall_reaction(session)` → if `chat_id` and `telegram_message_id` present and flag not yet set → RPUSH `{type: "reaction", chat_id, reply_to: telegram_message_id, emoji: "⏳", session_id, timestamp}` onto `telegram:outbox:{session_id}` → set `stall_reaction_applied = True` → save session.

Bridge relay (already running) → polls `telegram:outbox:*` → `_send_queued_reaction` → `set_reaction(client, chat_id, msg_id, "⏳")` → user sees ⏳ on their original message.

Subsequent ticks while still stalled → flag is True → `_apply_stall_reaction` returns early. No duplicate reaction.

Session transitions out of stalled status (worker progresses, session moves `pending` → `running` or completes) → flag is reset (see Reset hook below) → if it stalls again later, a fresh ⏳ can fire.

### Technical Approach

- **Insertion point**: `monitoring/session_watchdog.py`, immediately after the existing `logger.warning("LIFECYCLE_STALL ...")` block (line 376), call `_apply_stall_reaction(session)`. Do NOT touch the warning itself.
- **Skip silently** when `session.chat_id` is unset, when `session.telegram_message_id` returns `None`, or when `chat_id` is non-integer (matches the `_send_queued_reaction` drop-rule for local sessions). Log at `debug` level only.
- **Dedup**: read `getattr(session, "stall_reaction_applied", False)` at the top of the helper. If True, return early. After successful enqueue, set it True and `session.save()`. If save fails, swallow — the duplicate-reaction risk on the next tick is bounded (one extra ⏳ at worst) and is preferable to a watchdog crash.
- **Reset**: tracked the simplest way — `_apply_stall_reaction` *also* records the status it stalled in (e.g. a sibling field `stall_reaction_status: str = Field(default="")`). On each watchdog tick, before the dedup check, compare the current status against `stall_reaction_status`; if different (including empty), reset the flag. This avoids needing a separate lifecycle hook elsewhere — all the state machinery lives in the watchdog.
- **Cooldown via flag, not Redis TTL**: unlike `_inject_watchdog_steer` which uses `SET NX EX` because steers can race across watchdog instances, the worker watchdog is a single in-process loop. The session-field flag is sufficient and the natural place for state. (Validated by reading `worker/__main__.py` — only one watchdog runs.)
- **Feature gate**: wrap the new branch in `_env_flag_enabled("WATCHDOG_STALL_REACTION_ENABLED")`. Default on, falsy values disable.
- **No telethon import in the worker.** Watchdog only RPUSHes JSON. Verified: `tools/react_with_emoji.py` does the same and works today.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Wrap the entire `_apply_stall_reaction` body in `try/except Exception` and `logger.warning(...)` with a stable prefix (`[watchdog] stall-reaction enqueue failed for %s: %s`). Add a unit test that injects a Redis failure and asserts (a) no exception escapes, (b) the warning is logged, (c) `check_stalled_sessions` still returns the stalled list.
- [ ] No `except Exception: pass` blocks introduced. The single existing one (`session.save()` failure) logs at `debug` and is intentional.

### Empty/Invalid Input Handling
- [ ] Test: session with `initial_telegram_message = None` → helper returns False, no enqueue, no exception, no log spam.
- [ ] Test: session with `initial_telegram_message = {}` (no `telegram_message_id` key) → same.
- [ ] Test: session with non-integer `chat_id` (e.g. `"local-abc"`) → same.
- [ ] Test: session with both `chat_id` and `telegram_message_id` valid but `stall_reaction_applied = True` already → no enqueue.

### Error State Rendering
- [ ] If the user sees no ⏳ because the relay isn't running, the watchdog log line `[watchdog] stall-reaction queued for {session_id}` still emits, so an operator can correlate. Test asserts the log line is present on successful enqueue.

## Test Impact

- [ ] `tests/unit/test_stall_detection.py` — UPDATE: existing tests for `check_stalled_sessions` must continue to pass unchanged (the warning + return list behavior is preserved). Add new test cases for the reaction-enqueue branch to the same file.
- [ ] `tests/unit/test_session_watchdog.py` — UPDATE if it touches `check_stalled_sessions` directly; otherwise no change. Verify by running it before/after.
- [ ] No existing tests of the relay change — `bridge/telegram_relay.py` already supports `type: "reaction"`; we just feed it the same shape `tools/react_with_emoji.py` does. `tests/unit/test_bridge_relay.py` already covers reaction-dispatch and stays as-is.
- [ ] `tests/unit/test_send_telegram.py` — no changes; we don't touch `send_telegram.py`.

New tests added (no replacement):
- [ ] `tests/unit/test_stall_detection.py::TestStallReaction` — the four empty/invalid-input cases above plus the happy path (queued payload shape matches `_send_queued_reaction`'s expectations).
- [ ] `tests/unit/test_stall_detection.py::test_stall_reaction_dedup_resets_on_status_change` — set flag with status `pending`, change session status to `running`, tick again, assert flag reset and a new reaction enqueued if still stalled.

## Rabbit Holes

- **DM the user when stalled.** The issue lists this as out of scope for v1. Skip.
- **Configurable thresholds via env or projects.json.** Use existing `STALL_THRESHOLDS`. Tunability is a separate issue if it ever matters.
- **Replacing the LIFECYCLE_STALL log with the reaction.** Logs stay. Reaction is additive.
- **Pubsub / new Redis channel for stall events.** The existing outbox queue is the right channel — `tools/react_with_emoji.py` proves it works for cross-process reactions.
- **Per-stall-category emoji (different emoji for `pending` vs `running` vs `active`).** One emoji (⏳) is enough. Differentiation belongs in the log, not the reaction.
- **Stretch: 30-min status reply in dev chat.** The issue marks this stretch and "only if surgically simple." It is not surgically simple — it requires `dev_chat_id` resolution per project, message dedup separate from the reaction dedup, and a second outbox payload type. **Defer to a follow-up issue.**
- **Watchdog steer (push a steering message into the session).** That is `_inject_watchdog_steer`'s job for `repetition`, `error_cascade`, `token_alert`. A stalled-pending session has no SDK process to steer — the right user-visible signal is the reaction, full stop.

## Risks

### Risk 1: Spurious ⏳ during legitimate slow operations (large model calls, file uploads)
**Impact:** User sees ⏳ even though the agent is working. Reads as a false alarm.
**Mitigation:** The existing `STALL_THRESHOLDS` (300s for `pending`, 2700s for `running`, 600s for `active`) and the existing transcript-liveness check at lines 334-342 already filter "actively-working" sessions from the stall set. The reaction only fires after the same checks the warning already passes — so if `LIFECYCLE_STALL` is sound today (which #777 closed), the reaction will be too. No new false-positive surface.

### Risk 2: Watchdog crashes on AgentSession field-add backcompat
**Impact:** Existing AgentSession records lack `stall_reaction_applied`. If reads aren't backcompat-safe, the watchdog tick raises and stops detecting stalls entirely.
**Mitigation:** Read with `getattr(session, "stall_reaction_applied", False)` and `getattr(session, "stall_reaction_status", "")` so missing fields default cleanly. Saves go through Popoto's `_heal_descriptor_pollution` which walks all fields generically — adding a nullable bool field needs no extra migration code (per memory `feedback_field_backcompat_heal`).

### Risk 3: Relay not running → ⏳ never appears, dedup flag still set
**Impact:** Worst case: a stall never gets a user-visible reaction even after the relay comes back up, because the dedup flag is set on the first attempted enqueue.
**Mitigation:** RPUSH always succeeds whether or not the relay is up — the payload waits in Redis with the existing 3600s TTL. When the relay restarts it drains the queue. So the only failure is total Redis loss, which is a separate operational problem the watchdog can't solve. Acceptable.

### Risk 4: Dedup-flag reset race between status change and tick ordering
**Impact:** Session moves `pending` → `running` → back to `pending` between two watchdog ticks (5 min apart). The reset compares against the *current* status, not the prior, so the flag may stay set if the watchdog never sees the intermediate state.
**Mitigation:** Acceptable. A bouncing session that re-stalls within one tick is rare; user already saw the ⏳ for the first stall. If the second stall persists, the next tick (5 min later) will show a different status (or the same status with cleared `stall_reaction_status`) and the flag resets.

## Race Conditions

### Race 1: Watchdog and bridge race on session.save()
**Location:** `monitoring/session_watchdog.py` (new helper) and any bridge-side code that mutates the same AgentSession.
**Trigger:** Watchdog sets `stall_reaction_applied = True` and saves; concurrently the bridge updates `chat_id` or `pm_sent_message_ids` on the same record.
**Data prerequisite:** The Popoto save merges field-level — this is verified daily across the codebase (e.g. relay's `record_pm_message` runs concurrently with executor saves).
**State prerequisite:** AgentSession's Popoto layer handles concurrent save merging; we don't read-modify-write any list fields.
**Mitigation:** Save only the bool flag and the status string — both scalar fields. No list mutation, no read-modify-write. If two writers update different scalar fields concurrently, last-write-wins per field is acceptable for a dedup flag.

## No-Gos (Out of Scope)

- DM-style alerts, dev-chat status replies, paging.
- Configurable thresholds.
- Replacing the existing log warning.
- Adding a new Redis pubsub channel.
- Per-status-category emoji selection (one ⏳ for all stall types).
- Stretch goal: 30-min "still stalled" follow-up reply (deferred to a follow-up issue).

## Update System

No update system changes required. The change is purely internal to the worker and bridge processes; both already restart via `./scripts/valor-service.sh restart`. No new env vars required for default behavior (the feature gate defaults on).

## Agent Integration

No agent integration required. This is a watchdog-internal change that surfaces a user-visible signal via the existing bridge-owned reaction path. The agent never invokes this code.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` — add a "Stall reaction" subsection under the watchdog escalation ladder describing the new branch, the ⏳ emoji, the `WATCHDOG_STALL_REACTION_ENABLED` flag, and the dedup-via-AgentSession-flag mechanism.
- [ ] Update `docs/features/README.md` index table if `bridge-self-healing.md` does not yet list "stall reaction" in its summary column.

### Inline Documentation
- [ ] Module-level docstring on `_apply_stall_reaction` matching the prose style of `_inject_watchdog_steer` (cooldown contract, fail-quiet contract, feature-gate, telethon-isolation note).
- [ ] AgentSession field docstrings on `stall_reaction_applied` and `stall_reaction_status` explaining who writes and who resets.

## Success Criteria

- [ ] When a session in `pending` exceeds 300s and has a valid `chat_id` + `telegram_message_id`, exactly one ⏳ reaction is enqueued onto `telegram:outbox:{session_id}` per stall period.
- [ ] Sessions with no `initial_telegram_message`, no `telegram_message_id`, or non-integer `chat_id` are skipped silently — no exceptions, no warning-level log lines.
- [ ] Re-detection on subsequent watchdog ticks while still stalled does NOT enqueue a duplicate reaction.
- [ ] Status change between ticks (e.g. `pending` → `running` → `pending`) resets the dedup flag so a new stall in the new status can re-fire.
- [ ] The existing `LIFECYCLE_STALL` log warning is unchanged.
- [ ] `WATCHDOG_STALL_REACTION_ENABLED=0` disables the new branch entirely.
- [ ] Manual test: enqueue a session with the worker stopped, wait >300s, observe ⏳ on the originating Telegram message after the next tick.
- [ ] Manual test: with worker running normally, no false-positive ⏳.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog-reaction)**
  - Name: watchdog-reaction-builder
  - Role: Implement the AgentSession field, the `_apply_stall_reaction` helper, and the call site in `check_stalled_sessions`.
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-reaction)**
  - Name: watchdog-reaction-validator
  - Role: Verify the implementation against the success criteria, run unit tests, confirm no regressions in existing watchdog tests.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: watchdog-reaction-docs
  - Role: Update `docs/features/bridge-self-healing.md` and the index.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add AgentSession fields
- **Task ID**: build-agentsession-fields
- **Depends On**: none
- **Validates**: tests/unit/test_stall_detection.py (existing tests still pass), new field-default tests
- **Informed By**: memory `feedback_field_backcompat_heal` (Popoto handles new nullable fields without extra backcompat code)
- **Assigned To**: watchdog-reaction-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `stall_reaction_applied = Field(default=False)` to `models/agent_session.py`.
- Add `stall_reaction_status = Field(default="")` to `models/agent_session.py`.
- Add docstrings explaining writer (watchdog) and reset semantics.

### 2. Add `_apply_stall_reaction` helper
- **Task ID**: build-helper
- **Depends On**: build-agentsession-fields
- **Validates**: tests/unit/test_stall_detection.py::TestStallReaction (new)
- **Assigned To**: watchdog-reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `_apply_stall_reaction(session)` in `monitoring/session_watchdog.py`, modeled on `_inject_watchdog_steer`'s structure (feature-gate check, fail-quiet, debug-on-skip).
- Skip silently when `chat_id` or `telegram_message_id` missing or non-integer.
- Read dedup flag with `getattr(...)` for backcompat.
- Reset flag when current status differs from `stall_reaction_status`.
- RPUSH `{type: "reaction", chat_id, reply_to, emoji: "⏳", session_id, timestamp}` onto `telegram:outbox:{session_id}` with `expire(key, 3600)`.
- Set both fields and `session.save()`. Swallow save errors at debug level.
- Wrap entire body in `try/except Exception` with a single warning log on failure.

### 3. Wire helper into check_stalled_sessions
- **Task ID**: build-call-site
- **Depends On**: build-helper
- **Validates**: tests/unit/test_stall_detection.py
- **Assigned To**: watchdog-reaction-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert `_apply_stall_reaction(session)` immediately after the `logger.warning("LIFECYCLE_STALL ...")` block at `monitoring/session_watchdog.py:376`.
- Do not modify the warning itself.

### 4. Tests for helper and integration
- **Task ID**: build-tests
- **Depends On**: build-call-site
- **Validates**: tests/unit/test_stall_detection.py
- **Assigned To**: watchdog-reaction-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestStallReaction` class to `tests/unit/test_stall_detection.py`:
  - happy path: stalled pending session with valid Telegram fields → payload RPUSHed, flag set
  - missing `initial_telegram_message` → no enqueue
  - missing `telegram_message_id` key → no enqueue
  - non-integer `chat_id` (e.g. `"local-abc"`) → no enqueue
  - already-flagged session → no duplicate enqueue
  - flag reset on status change → re-enqueue allowed
  - feature gate `WATCHDOG_STALL_REACTION_ENABLED=0` → no enqueue
  - Redis RPUSH failure → no exception, warning logged
- Mock `POPOTO_REDIS_DB.rpush` and verify call args directly. Do NOT touch the live bridge in unit tests.

### 5. Validate
- **Task ID**: validate-watchdog-reaction
- **Depends On**: build-tests
- **Assigned To**: watchdog-reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_stall_detection.py tests/unit/test_session_watchdog.py -v`.
- Run `python -m ruff check monitoring/session_watchdog.py models/agent_session.py tests/unit/test_stall_detection.py`.
- Confirm all success criteria are met.
- Verify the existing `LIFECYCLE_STALL` warning string is unchanged (grep for it).

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-watchdog-reaction
- **Assigned To**: watchdog-reaction-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with a "Stall reaction" subsection.
- Update `docs/features/README.md` if needed.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: watchdog-reaction-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit tests touching watchdog: `pytest tests/unit/ -k "watchdog or stall" -v`.
- Run lint and format checks.
- Verify success criteria checklist is complete.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Stall-detection tests pass | `pytest tests/unit/test_stall_detection.py -x -q` | exit code 0 |
| Watchdog tests pass | `pytest tests/unit/test_session_watchdog.py tests/unit/test_watchdog_loop_break_steer.py tests/unit/test_watchdog_token_alert.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/ models/ tests/unit/test_stall_detection.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/ models/ tests/unit/test_stall_detection.py` | exit code 0 |
| Existing warning preserved | `grep -n 'LIFECYCLE_STALL' monitoring/session_watchdog.py` | output contains LIFECYCLE_STALL |
| New helper present | `grep -n '_apply_stall_reaction' monitoring/session_watchdog.py` | output contains _apply_stall_reaction |
| AgentSession field present | `grep -n 'stall_reaction_applied' models/agent_session.py` | output contains stall_reaction_applied |
| Reaction emoji distinct from existing | `grep -n 'REACTION_RECEIVED\|REACTION_PROCESSING\|REACTION_ABORT' bridge/response.py` | does not include ⏳ |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

1. **Reaction emoji choice**: ⏳ (hourglass) vs ⚠️ (warning) vs 🐌 (slow). The issue suggests ⏳ or ⚠️. ⏳ reads as "still working / slow", ⚠️ reads as "broken — operator action needed". For a 5-min stall on `pending` (which may be a transient queue backup), ⏳ is more honest. For a 5-hour stall (which is what motivated the issue), ⚠️ would be more accurate but the watchdog has no native concept of "elevated severity." Acceptable answer: start with ⏳ — escalation to ⚠️ at e.g. 2× threshold could be a follow-up if the data shows people miss ⏳.
2. **Should the dedup field live on AgentSession or in a Redis cooldown key (matching `_inject_watchdog_steer`)?** Plan picks AgentSession because (a) it's the natural place for per-session state, (b) the watchdog is single-threaded so no cross-instance race exists, (c) it survives Redis-key TTL expiry. If we ever shard the watchdog across processes, we'd revisit.
3. **Should non-`pending` stalls get the reaction too (`running` and `active` states)?** The issue's acceptance criteria only mentions `pending`. The plan applies the reaction to *any* stall (since the helper is called from the existing warning branch, which fires on all three statuses). Confirm this is desired — if not, gate by `status_val == "pending"` inside the helper.
