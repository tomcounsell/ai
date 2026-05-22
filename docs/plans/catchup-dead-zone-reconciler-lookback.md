---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-05-22
tracking: https://github.com/tomcounsell/ai/issues/1408
last_comment_id:
---

# Catchup Dead Zone + Reconciler Lookback Fix

## Problem

The Telegram bridge can permanently lose messages sent to a monitored group when Telethon silently stops delivering `NewMessage` events for that group. No error is logged, no disconnect occurs, and the two safety-net mechanisms (startup catchup + periodic reconciler) both fail to catch the gap.

**Current behavior:**

Three compounding failures produce a permanent dead zone:

1. **Silent Telethon update gap** — Telethon can stop firing the event handler for a specific group with no error or disconnect (known unresolved upstream bugs: LonamiWebs/Telethon issues #4361, #4345, #3014; library archived 2026-02-21).
2. **Catchup dead zone** — `bridge/catchup.py` cuts off at `data/last_connected`, which advances on every 5-minute heartbeat. A message sent *inside* the connection window but silently dropped by Telethon falls *before* the cutoff on restart and is excluded from catchup.
3. **Reconciler lookback too short** — `bridge/reconciler.py` has a fixed 10-minute lookback. If the worker is down across multiple restarts, a message can age out of the lookback window before the reconciler scans it.

Observed 2026-05-22: a `respond_to_unaddressed: true` message in Cyndra Dev at 08:29 UTC was never delivered to the handler. Catchup cutoff after the 08:32 restart was 08:31:49, excluding the message. Reconciler scans ran but the message aged past the 10-minute window before the first effective scan. Result: permanent loss until manual `valor-session resume`.

**Desired outcome:**

A message sent to a monitored group during a period when the bridge is nominally connected must not be permanently lost. Either the reconciler catches it, or the catchup catches it, within 30 minutes of restart. Silent Telethon gaps become observable (logged as warnings) even when no message is missed.

## Freshness Check

**Baseline commit:** `69749977` (main)
**Issue filed at:** 2026-05-22T09:13:38Z (~4 hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/telegram_bridge.py:188` (`_LAST_CONNECTED_FILE`) — still holds; `last_connected` is written via `_write_last_connected()` on connect and every 5 minutes via heartbeat (line 2989).
- `bridge/routing.py:1069` (`respond_to_unaddressed` check) — still holds.
- `bridge/catchup.py` — `lookback_override` param and `CATCHUP_LOOKBACK_MINUTES = 60` still present; cutoff is computed against `datetime.now(UTC) - effective_lookback`.
- `bridge/reconciler.py` — `RECONCILE_LOOKBACK_MINUTES = 10`, `RECONCILE_INTERVAL_SECONDS = 180`, `RECONCILE_MESSAGE_LIMIT = 20` confirmed.

**Cited sibling issues/PRs re-checked:**
- #588 — closed 2026-03-30, resolution: introduced `bridge/reconciler.py` (the reconciler this plan extends).
- #532 — merged earlier, introduced `data/last_connected` and dynamic catchup lookback (the mechanism that creates the dead zone described here).
- #70 — closed; original 5-minute restart delay issue, predates current architecture.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since="2026-05-22T09:13:38Z" -- bridge/...` is empty.

**Active plans in `docs/plans/` overlapping this area:** None — `ls docs/plans/ | grep -iE "catchup|reconcil|telethon"` returned no matches.

**Notes:** No drift. The premise of the issue is intact and the code paths cited match exactly.

## Prior Art

- **Issue #588** (closed 2026-03-30): *Bridge misses messages during live connection — no runtime gap detection*. This issue motivated the creation of `bridge/reconciler.py`. The reconciler addresses *runtime* gaps but the 10-minute fixed lookback was set conservatively and does not cover the multi-restart scenario.
- **PR #532** (merged): *Bridge flood-backoff persistence and dynamic catchup lookback*. Introduced `data/last_connected` and replaced the fixed 60-minute catchup window with a dynamic lookback. This PR is the **direct cause** of the catchup dead zone described in this plan — it tied catchup's cutoff to the heartbeat-advancing `last_connected`, which assumes "connected and heartbeating" implies "receiving all events."
- **Issue #948** (closed 2026-04-14): *Centralize dedup recording in bridge dispatch + add flow diagrams*. Made dedup recording uniform across catchup, reconciler, and the live handler — relevant because both new code paths in this plan will use the same `bridge.dedup` API.

No prior fixes addressed the specific failure mode where the per-chat event stream goes silent while the bridge is otherwise healthy.

## Research

**Queries used:**
- `Telethon NewMessage event handler silently misses messages pts gap recovery 2026`
- `Telethon catch_up getChannelDifference manually fetch missed updates per-chat`

**Key findings:**
- **Telethon library was archived 2026-02-21** by the owner — no upstream fix is coming for these update gap bugs. ([LonamiWebs/Telethon issues #4361, #4345, #3014](https://github.com/LonamiWebs/Telethon/issues/4361))
- **The silent-miss pattern is well documented** — multiple users report `NewMessage` failing to fire for specific channels (especially large public channels, or after a long quiet period) with no error logged. We must treat the bridge's event stream as inherently unreliable per chat.
- **`client.catch_up()` is already invoked** at bridge startup (line 2887 in `bridge/telegram_bridge.py`) and only triggers `getChannelDifference` for channels Telegram flags as `updateChannelTooLong`. It does **not** detect the silent-fail case where Telegram believes the client is current.
- **`get_messages(entity, limit=N)`** (already used by both catchup and reconciler) is the only reliable per-chat recovery mechanism — it does not depend on Telethon's update state.

**Implication for the plan:** Per-chat polling is the right recovery primitive. We just need (a) a smarter cutoff than `last_connected`, and (b) a longer reconciler window for cases where the worker was down and multiple restarts compounded the gap.

## Spike Results

No spikes needed — the three options proposed in the issue (extended reconciler lookback, per-chat last-processed cursor, per-chat event-silence detector) are all straightforward additions to existing code with no architectural ambiguity. The data-flow and APIs are already in place (`bridge/dedup.py` provides the dedup primitive, `bridge/reconciler.py` already loops monitored groups, `bridge/catchup.py` already accepts a per-call `lookback_override`).

## Data Flow

**Entry point:** Message sent to a monitored Telegram group.

1. **Telethon event handler** (`bridge/telegram_bridge.py` `handle_new_message`): receives `NewMessage` event, runs dedup + routing + enqueue. **NEW:** also writes `bridge:last_event:{chat_id}` to Redis with the message timestamp.
2. **Live handler success path:** dedup check → routing → enqueue session → `record_message_processed(chat_id, msg.id)` → **NEW:** `record_last_processed(chat_id, msg.id, msg.date)` (per-chat cursor).
3. **Silent failure path:** Telethon never fires the handler. No record is updated for the chat.
4. **Periodic reconciler** (`bridge/reconciler.py` `reconcile_once`, every 180s): scans monitored groups, fetches last 20 messages per group, filters by dedup. **CHANGE:** lookback extended from 10 to 30 minutes; message limit raised to 30 to ensure 30-minute window is covered in busy chats.
5. **Startup catchup** (`bridge/catchup.py` `scan_for_missed_messages`): on restart, fetches recent messages per group. **CHANGE:** per-chat lookback computed from `max(last_processed_at_for_chat, last_connected)` — uses the per-chat cursor (when present) instead of just the global heartbeat timestamp.
6. **NEW: Silent-stream observability task** (added to `bridge/telegram_bridge.py` background tasks): periodically (every 300s) compares `bridge:last_event:{chat_id}` against `last_connected`. If a chat with `respond_to_unaddressed: true` has had no events for 15+ minutes while the bridge has been continuously connected and the chat had prior activity in the session, log a `WARNING`. Observability only — does not re-dispatch.

**Output:** Missed messages flow through the same `enqueue_agent_session` path as live messages, with `priority="low"`.

## Architectural Impact

- **New dependencies:** None. Uses existing `bridge.dedup` Redis-backed primitive and existing `popoto` ORM patterns.
- **Interface changes:**
  - `bridge.dedup` gains a per-chat last-processed cursor API: `record_last_processed(chat_id, message_id, message_date)` and `get_last_processed(chat_id) -> (msg_id, date) | None`. Backed by a small new Popoto model (`models/last_processed.py`) or extended `DedupRecord` — see Technical Approach.
  - `bridge/catchup.py::scan_for_missed_messages` gains an internal per-chat cutoff computation (no signature change — `lookback_override` is the *global* upper bound, per-chat cutoff is computed inside the loop).
  - `bridge/reconciler.py` constants change values but no API change.
- **Coupling:** Slight increase between the live handler and the reconciler/catchup — they now share a per-chat cursor. This is an *improvement* in coupling cohesion (all three already shared `DedupRecord`).
- **Data ownership:** The bridge owns the new `LastProcessedRecord` (or extended `DedupRecord`) — same ownership as existing dedup state. Redis-backed, no new persistence layer.
- **Reversibility:** Fully reversible. Per-chat cursors are write-only side data; if the model is dropped, code falls back to the existing `last_connected` cutoff. Constant changes (10→30, 20→30) are trivial reverts.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is well-defined; issue's solution sketch is the agreed approach)
- Review rounds: 1 (standard PR review)

This is a focused bug fix touching three files (`catchup.py`, `reconciler.py`, `telegram_bridge.py`) plus one new small Popoto model. No architectural changes, no new external surfaces.

## Prerequisites

No prerequisites — this work has no external dependencies beyond the existing bridge/Redis stack.

## Solution

### Key Elements

- **Per-chat last-processed cursor** — A Redis-backed record (Popoto model or extension of `DedupRecord`) tracking the latest message ID and timestamp the bridge successfully processed for each chat. Written by the live handler, the reconciler, and catchup on every successful dispatch.
- **Smarter catchup cutoff** — Catchup uses `max(last_processed_for_chat, last_connected) - safety_margin` per chat instead of the global `last_connected`. This closes the gap between "bridge heartbeating" and "bridge actually receiving messages from this group."
- **Extended reconciler lookback** — `RECONCILE_LOOKBACK_MINUTES` raised from 10 to 30. Covers the worst-case multi-restart scenario described in the issue. `RECONCILE_MESSAGE_LIMIT` raised from 20 to 30 to maintain coverage in active chats over the longer window.
- **Silent-stream warning task** — A new background coroutine (`silent_stream_watcher`) compares `bridge:last_event:{chat_id}` against `last_connected`. Emits a single `WARNING` per chat when a `respond_to_unaddressed: true` group has been silent 15+ minutes during a continuous connection. Self-suppresses to one warning per silent-period to avoid log spam.

### Flow

Bridge starts → reads `last_connected` AND per-chat `last_processed_for_chat[X]` for each monitored chat X → catchup uses `max(last_processed_for_chat[X], last_connected)` as cutoff for X → catchup completes → live handler runs → every successful dispatch updates BOTH `DedupRecord` (existing) AND per-chat `LastProcessedRecord` (new) AND `bridge:last_event:{chat_id}` (new) → reconciler runs every 180s with 30-minute lookback → silent-stream watcher runs every 300s and warns on chats silent for 15+ minutes.

### Technical Approach

1. **New Popoto model `LastProcessedRecord`** (`models/last_processed.py`):
   - Fields: `chat_id` (PK, str), `last_message_id` (int), `last_message_ts` (int, unix), `updated_at` (int, unix).
   - `Meta.ttl = 30 days` — long enough to survive any reasonable downtime; auto-expires for inactive chats.
   - Use `popoto.Field(default=...)`; follow existing `DedupRecord` conventions exactly (see `models/dedup.py`).
   - Idempotent `get_or_create(chat_id)` constructor.
   - **Decision rationale**: separate model instead of extending `DedupRecord` because `DedupRecord` tracks the *set* of recent IDs (for dedup checks) and conflating it with a *cursor* would muddy the abstraction. `bridge.utc.to_unix_ts` handles datetime → unix conversion (per memory `feedback_timestamp_timezone`).

2. **New `bridge/dedup.py` helpers** (alongside the existing `is_duplicate_message` / `record_message_processed`):
   - `async def record_last_processed(chat_id, message_id, message_ts) -> None` — wraps `LastProcessedRecord.get_or_create + .save`; logs `WARNING` on failure but never raises (same safety contract as `record_message_processed`).
   - `async def get_last_processed(chat_id) -> tuple[int, datetime] | None` — returns `(message_id, datetime_utc)` or `None` if no record exists. Catches and logs all exceptions; returns `None` on failure (callers fall back to `last_connected`).

3. **Live handler update** (`bridge/telegram_bridge.py` `handle_new_message`):
   - At the same site `record_message_processed` is currently called on successful dispatch, also call `record_last_processed(chat_id, msg.id, msg.date)`.
   - On *every* incoming message (regardless of routing decision or outgoing/empty filtering), set `bridge:last_event:{chat_id}` in Redis (using a small helper in `bridge/dedup.py` or directly via Redis client) with the current timestamp. This is purely observability — separate from the cursor, which only advances on dispatch.
   - Both writes are best-effort: failures log WARNING and continue (already the bridge's pattern).

4. **Catchup update** (`bridge/catchup.py::scan_for_missed_messages`):
   - For each matched `chat_title` → `chat_id`, after `project = find_project_fn(chat_title)` and before the `get_messages` call, compute the per-chat cutoff:
     ```python
     per_chat_cutoff = cutoff  # fallback: existing global cutoff
     last_proc = await get_last_processed(chat_id)
     if last_proc is not None:
         _last_msg_id, last_proc_dt = last_proc
         # Use the EARLIER of (last_processed - 60s safety) and the global cutoff,
         # so we never look further back than the 24h cap allows, but we DO look
         # further back than last_connected if last_processed is older.
         candidate = last_proc_dt - timedelta(seconds=60)
         per_chat_cutoff = min(cutoff, candidate)
     ```
   - Use `per_chat_cutoff` instead of the global `cutoff` for that chat's message filter. Log at INFO when the per-chat cutoff differs materially from the global cutoff.
   - On successful dispatch from catchup (where `record_message_processed` is already called), also call `record_last_processed`.

5. **Reconciler update** (`bridge/reconciler.py`):
   - Change `RECONCILE_LOOKBACK_MINUTES` from `10` to `30`.
   - Change `RECONCILE_MESSAGE_LIMIT` from `20` to `30`.
   - On successful dispatch (where `record_message_processed` is already called), also call `record_last_processed`.

6. **Silent-stream watcher** (new function in `bridge/telegram_bridge.py` or new file `bridge/silent_stream.py`):
   - `async def silent_stream_loop(client, monitored_groups, projects_config, find_project_fn)`:
     - Sleeps 300s between iterations.
     - For each monitored group with `respond_to_unaddressed: true`:
       - Read `bridge:last_event:{chat_id}` from Redis.
       - If no record exists, skip (no prior activity baseline → no signal).
       - Read `last_connected` (in-memory `_catchup_last_connected` or via `_read_last_connected()`).
       - If `now - last_event_ts >= 15 * 60` AND `now - bridge_start_ts >= 15 * 60`, log a WARNING:
         `[silent-stream] No events for chat <title> (id=<id>) in 15+ min — possible Telethon update gap; reconciler will scan within 3 min`.
       - Use a small in-memory set `_warned_chats: dict[chat_id, last_warn_ts]` to suppress repeated warnings within a 30-minute window per chat.
   - Spawn this task in the same place catchup/reconciler are spawned (around line 2920 in `telegram_bridge.py`).

7. **Per-memory rules:**
   - Use `bridge.utc.to_unix_ts` for any datetime→unix conversion (memory `feedback_timestamp_timezone`).
   - All Redis access on the new `LastProcessedRecord` MUST go through the Popoto ORM — no raw `r.hset`/`r.hget`/`r.delete` (memory `feedback_never_raw_delete_popoto`). The `bridge:last_event:{chat_id}` key is *not* Popoto-managed (it's a freeform observability key), so raw `redis.set`/`redis.get` is acceptable for that key only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/dedup.py` — `record_last_processed` and `get_last_processed` both swallow exceptions and log WARNING. Tests must assert the warning log on simulated Redis failure AND that callers continue to function (catchup/reconciler fall back to the global cutoff).
- [ ] `bridge/telegram_bridge.py::silent_stream_loop` — the loop has a try/except around each iteration that logs `error()` and continues. Test: inject a Redis read failure and assert the loop survives and continues the next cycle.
- [ ] The catchup loop already has `except Exception as e: logger.error(...); continue` per-group — the new `per_chat_cutoff` computation must not break this contract. Test: inject a `get_last_processed` failure and assert the per-group scan falls back to the global cutoff.

### Empty/Invalid Input Handling
- [ ] `get_last_processed` returns `None` for unknown `chat_id` — verified by test.
- [ ] `record_last_processed` accepts `message_ts=None` defensively (no `NewMessage` should arrive without a date, but Telethon edge cases happen) → coerces to `datetime.now(UTC)`. Test the coercion.
- [ ] Silent-stream watcher with no `bridge:last_event:{chat_id}` record (chat never had activity) → skips, does not warn. Test the skip path.

### Error State Rendering
- [ ] Silent-stream WARNING log includes chat title and chat_id so operators can locate the affected group. Test the log message format.
- [ ] On Redis outage, no per-chat cursor logic runs and the bridge falls back to today's behavior (global `last_connected` cutoff) — no user-visible error, but the WARNING logs are observable in `tail -f logs/bridge.log`.

## Test Impact

- [ ] `tests/unit/test_reconciler.py` — UPDATE: existing tests assert the 10-minute lookback; update assertions to 30 minutes. Tests that mock `RECONCILE_LOOKBACK_MINUTES` may already use the constant — verify and update if any hard-code `10`.
- [ ] `tests/integration/test_reconciler.py` — UPDATE: same — verify lookback assertions match the new 30-minute window. Add at least one new test case where a message is 20 minutes old at reconciler scan time and asserts recovery (this would have failed under the old 10-minute window).
- [ ] `tests/integration/test_catchup_revival.py` — UPDATE: at least one test exercises the `lookback_override` path. Add a new test where `last_processed_for_chat` predates `last_connected` by 5 minutes and assert catchup picks up a message in that 5-minute window.
- [ ] **NEW:** `tests/unit/test_last_processed.py` — CREATE: unit tests for the new `LastProcessedRecord` Popoto model (`get_or_create` idempotency, TTL behavior, Redis failure fallback in `record_last_processed`/`get_last_processed`).
- [ ] **NEW:** `tests/unit/test_silent_stream.py` — CREATE: unit tests for the silent-stream watcher (skip-when-no-prior-activity, warn-after-15-min, suppress-within-30-min-window, survive-Redis-failure).
- [ ] **NEW:** `tests/integration/test_per_chat_catchup_cutoff.py` — CREATE: integration test that fakes a chat where `last_processed_for_chat` is 5 minutes older than `last_connected` and asserts catchup queues a message in the 5-minute gap.

## Rabbit Holes

- **Reimplementing Telethon's update reconciliation** — tempting because the root cause is upstream. Don't. The library is archived; treat the event stream as best-effort and rely on per-chat polling. The reconciler model is correct; we're just tuning it.
- **Per-chat tunable lookbacks via `projects.json`** — overkill for this fix. The 30-minute default is fine for all current chats; per-chat tuning is a future optimization if a high-volume chat needs a shorter window for cost reasons.
- **Telegram API rate-limit deep dive** — the reconciler already runs every 180s for all monitored groups; bumping `limit` from 20 to 30 is a 50% increase per chat but `get_messages` is a single API call regardless of limit (within the 100-message API cap). Don't over-engineer rate-limit budgeting for this small bump.
- **Generalizing the silent-stream watcher into a full health probe** — separate concern. The watcher in this plan is purely observability for one specific failure mode. Resist scope creep into "is the bridge healthy overall" — that belongs in `monitoring/`.
- **Switching to MTProto raw updates** — vastly out of scope. Telethon's high-level API is fine for our scale.

## Risks

### Risk 1: Per-chat cursor writes add latency to the hot path
**Impact:** Every successful dispatch now does an extra Redis write (`record_last_processed`). At current message volume (~dozens per hour across all monitored chats), latency is negligible — but in a burst this could compound.
**Mitigation:** The write is fire-and-forget (`logger.warning` on failure, no raise). If profiling shows it's hot, batch into a single Popoto `.save()` with both dedup and cursor fields. **Detection:** Add a single INFO log line during reconciler scans showing the average `last_processed` age per scanned chat — gives a passive signal of cursor health.

### Risk 2: 30-minute reconciler lookback increases Telegram API calls
**Impact:** Each reconciler scan still makes one `get_messages(limit=30)` call per chat — same number of API calls, just with a larger limit (30 vs 20). At 180s interval × N monitored chats, this is well under any Telethon FloodWait threshold.
**Mitigation:** Constraint already noted in the issue. The 30-message limit fits comfortably in Telegram's per-call ceiling. If a chat exceeds 30 messages in 30 minutes, the scan caps at 30 — the oldest ones may age out, but that's the existing behavior and not a regression.

### Risk 3: Silent-stream watcher emits false positives during expected quiet periods
**Impact:** A `respond_to_unaddressed: true` chat that's just naturally quiet for 15+ minutes would trigger a misleading WARNING.
**Mitigation:** The watcher only fires when `bridge:last_event:{chat_id}` exists AND is old. If a chat has never had activity in the current bridge session, no warning fires. The 30-minute suppression window per chat further dampens false-positive noise. Verbal framing in the log message: "possible Telethon update gap" — invites operator judgment rather than asserting failure.

### Risk 4: Per-chat cursor and global cutoff disagree in edge cases
**Impact:** If `last_processed_for_chat` is *newer* than `last_connected` (e.g., bridge processed a message right before crash), using `max()` could cause catchup to miss a message that arrived between the last cursor update and the crash.
**Mitigation:** Use `min(global_cutoff, candidate)` rather than `max()`. The intent is: "use the earlier of the two — never look back less than the global cutoff allows." The 60-second safety margin (`candidate = last_proc_dt - timedelta(seconds=60)`) provides additional safety. The 24-hour global cap (already enforced in `scan_for_missed_messages`) bounds total lookback even when the per-chat cursor is ancient.

## Race Conditions

### Race 1: Concurrent cursor writes from live handler and reconciler
**Location:** `bridge/dedup.py::record_last_processed` writes from `handle_new_message` (live handler), `reconcile_once` (every 180s), and `scan_for_missed_messages` (startup).
**Trigger:** A reconciler scan recovers a message while a newer message arrives via the live handler concurrently. Both call `record_last_processed` near-simultaneously.
**Data prerequisite:** The cursor reflects the latest *successfully dispatched* message for the chat.
**State prerequisite:** Cursor monotonically advances (later updates do not regress to earlier message_ids).
**Mitigation:** Use Popoto's atomic save semantics. On every `record_last_processed`, read-then-compare-then-write inside a single Popoto transaction: only update if the incoming `message_id` is `>` the stored `last_message_id`. This is a small extension to the helper. If atomicity is impractical, accept eventual consistency — the cursor only needs to be approximately correct (it informs catchup's lookback, not dedup, which is already authoritatively handled by `DedupRecord`).

### Race 2: Catchup reads cursor while live handler updates it
**Location:** Startup catchup reads `get_last_processed` while the live handler is already running and may have written `record_last_processed`.
**Trigger:** Catchup is launched as a background task (`asyncio.create_task(_run_catchup())`) at the same time the event loop is processing incoming events.
**Data prerequisite:** Catchup's per-chat cutoff calculation reads a consistent snapshot of the cursor.
**State prerequisite:** Catchup is allowed to overlap the live handler (this is current behavior; restart catchup is non-blocking).
**Mitigation:** No special handling needed — Redis reads are atomic for a single key. If the cursor advances between catchup's read and the message scan, catchup may re-fetch a message that the live handler has already dispatched, but `is_duplicate_message` (existing `DedupRecord` check) will filter the duplicate. This is the same idempotency contract the system already relies on.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1408] Telethon library upgrade or replacement — the bridge's reliance on Telethon is broader than this fix; tracked implicitly by this issue's premise that we work *around* the silent-miss bug.
- Nothing further deferred — every item in the issue's "Solution Sketch" (extended reconciler lookback, per-chat cursor catchup, silent-stream observability) is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the bridge. The new `LastProcessedRecord` model uses the existing Popoto schema management (no migration needed; new models are auto-registered). No new dependencies, no new config files, no env vars. The `RECONCILE_LOOKBACK_MINUTES`/`RECONCILE_MESSAGE_LIMIT` constants stay in-code (per memory `feedback_no_specific_numbers_in_prompts` — these are config defaults, not prompt strings, and are fine to keep concrete in code).

The bridge restart after merge is the standard `./scripts/valor-service.sh restart` — already enforced by Development Principle #10. No new operator-side actions.

## Agent Integration

No agent integration required — this is a bridge-internal change. The agent does not directly interact with the catchup or reconciler logic; both run as bridge background tasks and route recovered messages through the existing `enqueue_agent_session` path that the agent already consumes via the worker. No new MCP tools, no new CLI entry points, no changes to `pyproject.toml [project.scripts]`.

The fix is observable in `logs/bridge.log` via the new `[silent-stream]` WARNING lines and via existing `[reconciler] Recovered` / `[catchup] Found missed message` logs.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` — add a new section "Silent Telethon Update Gap Handling" describing the per-chat cursor, extended reconciler lookback, and silent-stream watcher. Cross-link to issue #1408.
- [ ] Verify `docs/features/README.md` index still points to `bridge-self-healing.md` (no new entry needed).

### Inline Documentation
- [ ] Add a module-level docstring to `bridge/dedup.py` documenting the dual responsibility (dedup set + per-chat cursor).
- [ ] Add explanatory comments at the new `per_chat_cutoff` computation in `bridge/catchup.py` explaining why we use `min()` and the 60-second safety margin.
- [ ] Add a module-level docstring to the silent-stream watcher describing the false-positive suppression rules.

## Success Criteria

- [ ] A message sent to a `respond_to_unaddressed: true` group 25 minutes before a bridge restart is recovered within 30 minutes of the restart (covered by extended reconciler lookback).
- [ ] The bridge logs a `[silent-stream] WARNING` when a monitored group has been silent for 15+ minutes while the bridge is connected and the group had prior activity in the current session.
- [ ] Unit test (`tests/unit/test_last_processed.py` + `tests/integration/test_per_chat_catchup_cutoff.py`): catchup scan with a per-chat `last_processed_for_chat` that predates `last_connected` by 5 minutes correctly catches messages in that 5-minute gap.
- [ ] Integration test (`tests/integration/test_reconciler.py`): reconciler recovers a message injected 20 minutes into the past (simulated via message timestamp mock) — would have failed under the old 10-minute window.
- [ ] No regression: existing catchup dedup logic still prevents duplicate processing (existing `test_catchup_revival.py` tests pass unchanged).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (bridge-recovery)**
  - Name: `bridge-recovery-builder`
  - Role: Implement the per-chat cursor model, dedup helpers, catchup/reconciler updates, and silent-stream watcher.
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-recovery)**
  - Name: `bridge-recovery-validator`
  - Role: Verify all success criteria, run unit + integration tests, confirm bridge restart works.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `bridge-recovery-docs`
  - Role: Update `docs/features/bridge-self-healing.md` and inline docstrings.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build LastProcessedRecord model + dedup helpers
- **Task ID**: build-cursor-model
- **Depends On**: none
- **Validates**: `tests/unit/test_last_processed.py` (create)
- **Assigned To**: bridge-recovery-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `models/last_processed.py` with `LastProcessedRecord` (chat_id PK, last_message_id, last_message_ts, updated_at, Meta.ttl = 30 days).
- Add `record_last_processed` and `get_last_processed` helpers to `bridge/dedup.py`.
- Implement monotonic-only update inside `record_last_processed` (read-compare-write).
- Add unit tests in `tests/unit/test_last_processed.py` (get_or_create, monotonic update, TTL, Redis failure fallback).

### 2. Build catchup per-chat cutoff
- **Task ID**: build-catchup-cutoff
- **Depends On**: build-cursor-model
- **Validates**: `tests/integration/test_per_chat_catchup_cutoff.py` (create), `tests/integration/test_catchup_revival.py` (still passes).
- **Assigned To**: bridge-recovery-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/catchup.py::scan_for_missed_messages`, compute `per_chat_cutoff` using `min(global_cutoff, last_proc_dt - 60s)` when `get_last_processed(chat_id)` returns a record.
- On successful dispatch, call `record_last_processed`.
- Add integration test creating a per-chat record 5 minutes older than `last_connected` and asserting recovery.

### 3. Build reconciler extended lookback + cursor update
- **Task ID**: build-reconciler-extension
- **Depends On**: build-cursor-model
- **Validates**: `tests/unit/test_reconciler.py`, `tests/integration/test_reconciler.py` (both updated).
- **Assigned To**: bridge-recovery-builder
- **Agent Type**: builder
- **Parallel**: true (with task 2)
- Update `RECONCILE_LOOKBACK_MINUTES` to 30, `RECONCILE_MESSAGE_LIMIT` to 30 in `bridge/reconciler.py`.
- On successful dispatch, call `record_last_processed`.
- Update existing test assertions where the 10-minute window is hard-coded; add an integration test injecting a 20-minute-old message.

### 4. Build live handler cursor + event-timestamp updates
- **Task ID**: build-live-handler-hooks
- **Depends On**: build-cursor-model
- **Validates**: Existing `tests/integration/test_telegram_bridge_*.py` (run unchanged).
- **Assigned To**: bridge-recovery-builder
- **Agent Type**: builder
- **Parallel**: true (with task 2 and 3)
- In `bridge/telegram_bridge.py::handle_new_message`, after `record_message_processed` (on successful dispatch), also call `record_last_processed(chat_id, msg.id, msg.date)`.
- On *every* incoming event (before dedup check, regardless of dispatch outcome), set `bridge:last_event:{chat_id}` in Redis to current unix timestamp using `bridge.utc.to_unix_ts(datetime.now(UTC))`. Wrap in try/except → WARNING.

### 5. Build silent-stream watcher
- **Task ID**: build-silent-stream
- **Depends On**: build-live-handler-hooks
- **Validates**: `tests/unit/test_silent_stream.py` (create).
- **Assigned To**: bridge-recovery-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/silent_stream.py` with `silent_stream_loop(client, monitored_groups, projects_config, find_project_fn)`.
- Implement 300s sleep loop, 15-minute silence threshold, 30-minute per-chat warning suppression.
- Spawn the task in `bridge/telegram_bridge.py` alongside catchup/reconciler.
- Add unit tests covering: skip-when-no-prior-activity, warn-after-15-min, suppress-within-30-min-window, survive-Redis-failure, only-respond_to_unaddressed-chats.

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: build-cursor-model, build-catchup-cutoff, build-reconciler-extension, build-live-handler-hooks, build-silent-stream
- **Assigned To**: bridge-recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`.
- Run `pytest tests/integration/test_reconciler.py tests/integration/test_catchup_revival.py tests/integration/test_per_chat_catchup_cutoff.py -x -q`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Run `./scripts/valor-service.sh restart` and verify `tail -5 logs/bridge.log` shows "Connected to Telegram" and "Message reconciler started" (silent-stream task should also log startup).
- Verify all Success Criteria checkboxes individually.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: bridge-recovery-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with the new "Silent Telethon Update Gap Handling" section.
- Add module-level docstrings to `bridge/silent_stream.py` and the updated `bridge/dedup.py`.
- Verify the docs index still resolves.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Reconciler integration tests pass | `pytest tests/integration/test_reconciler.py -x -q` | exit code 0 |
| Catchup integration tests pass | `pytest tests/integration/test_catchup_revival.py tests/integration/test_per_chat_catchup_cutoff.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Reconciler lookback updated | `grep -n 'RECONCILE_LOOKBACK_MINUTES = 30' bridge/reconciler.py` | exit code 0 |
| Per-chat cursor helpers exist | `grep -n 'def record_last_processed\\|def get_last_processed' bridge/dedup.py` | output contains both |
| Silent-stream module exists | `test -f bridge/silent_stream.py` | exit code 0 |
| Bridge restarts cleanly | `./scripts/valor-service.sh restart && sleep 5 && grep 'Connected to Telegram' logs/bridge.log` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Per-chat cursor — separate Popoto model vs extend `DedupRecord`?** Plan currently proposes a separate `LastProcessedRecord` model for clean separation of concerns (set membership vs cursor). Acceptable, or prefer extending `DedupRecord` to keep all chat-level dedup state in one record?
2. **Silent-stream watcher scope — only `respond_to_unaddressed: true` chats, or all monitored groups?** Plan currently scopes to `respond_to_unaddressed: true` because those are the chats where a missed message has guaranteed downstream consequences (mention-gated chats can tolerate silence). Confirm scope is correct.
3. **Reconciler interval — keep at 180s or shorten?** Issue's acceptance criterion is "recovery within 30 minutes" which the new 30-minute lookback alone covers. Shortening to e.g. 90s would improve worst-case latency but doubles API call rate. Plan leaves interval unchanged. Confirm.
