---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-17
tracking: https://github.com/tomcounsell/ai/issues/1712
last_comment_id:
revision_applied: true
---

# Bridge Stale-Update-Stream Detector

## Problem

The Telegram bridge can be **process-alive and TCP-connected to Telegram while its
Telethon update loop has silently stopped delivering new-message events** — and
nothing detects it. This is the "alive ≠ working" failure class, one layer up from
the granite startup hang (#1710) and worker-zombie work (#1536).

**Current behavior:**
On 2026-06-16 the bridge reported `RUNNING` with current heartbeats
(`[heartbeat] Bridge alive (uptime=Nm)`) and a live TCP connection, but enqueued
**zero real messages for ~8 hours**. The only non-heartbeat log line was Telethon's
`_update_loop` logging `Account is now banned in <channel_id>` (a benign per-channel
condition), after which the update stream went quiet. Recovery required a manual
`./scripts/valor-service.sh restart`, which re-ran `catch_up=True` and backfilled
cleanly.

Every existing monitor misses this:
- `monitoring/bridge_watchdog.py` checks `process_running` (pgrep), `logs_fresh`
  (`logs/bridge.log` mtime < 300s), `no_crash_pattern`, and zombie age — all
  **process liveness, not update-flow liveness**. Worse, `are_logs_fresh()` reads
  the bridge log mtime, but the heartbeat loop writes that log every ~2 min
  *independent of update flow*, so logs stay "fresh" while the update loop is wedged.
- The bridge tracks `last_connected` (`bridge/telegram_bridge.py:188`) for catchup
  lookback, but has **no `last_update_received` / update-flow liveness signal**.
- The per-chat silent-stream watcher (`bridge/silent_stream.py`, #1408) is
  observability-only (logs a WARNING, never recovers), is gated on
  `respond_to_unaddressed` chats with prior activity, and would not fire for the
  account-wide silence in this incident.

**Desired outcome:**
A wedged-but-connected update loop is **detected via a positive liveness signal and
auto-recovered** (a restart re-runs catchup and backfills losslessly), instead of
silently dropping messages until a human notices — without resurrecting the
"silence = failure" trap that #1172 explicitly rejected.

## Freshness Check

**Baseline commit:** `4357347a`
**Issue filed at:** 2026-06-16T06:13:22Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/telegram_bridge.py:188` `_LAST_CONNECTED_FILE` / `_write_last_connected()` — still holds (writer at :2593 + every 5 min in `heartbeat_loop` ~:2999).
- `bridge/telegram_bridge.py:1086` `catch_up=True` — still holds; `client.catch_up()` also at :2902.
- `bridge/telegram_bridge.py:1093` single `@client.on(events.NewMessage)` handler — still holds.
- `monitoring/bridge_watchdog.py` `HealthStatus` + `are_logs_fresh()` (300s log-mtime check) — still holds; confirms the log-freshness blind spot.
- `grep -rn last_update_received bridge/ monitoring/` — empty; confirms no signal exists.

**Cited sibling issues/PRs re-checked:**
- #1408 — closed 2026-06-03 (PR #1559). Shipped `bridge:last_event:{chat_id}` and
  `bridge/silent_stream.py` (observability-only). Reshapes scope but does not solve
  the recovery layer or account-wide positive liveness.
- #1172 — referenced as the "silence = failure" no-go precedent; principle still applies.
- #1710 / #1536 — same "alive ≠ working" class; conceptual siblings, no code overlap.

**Commits on main since issue was filed (touching referenced files):**
- `fc3e3acc` (#1708) — added persona resolution to `bridge/catchup.py` and
  `bridge/reconciler.py`. Does NOT touch the update-loop-liveness root cause,
  watchdog, or silent-stream wiring. Reconciler cadence (`RECONCILE_INTERVAL_SECONDS=180`)
  unchanged.

**Active plans in `docs/plans/` overlapping this area:** none. (`granite_*` plans touch
the PTY container path, not the Telethon update loop or watchdog.)

**Notes:** Minor drift only — reconciler gained persona logic but its dialog-pass
structure (where any positive-probe could ride) is intact.

## Prior Art

- **#1408 / PR #1559** (`fix: close catchup dead zone + extend reconciler lookback`):
  Shipped `bridge:last_event:{chat_id}` (per-chat received-event timestamp,
  `bridge/dedup.py:133`) and `bridge/silent_stream.py` — a per-chat WARNING-only
  watcher riding the reconciler's `get_dialogs()` pass. **Relevance: high.** This is
  the closest prior work; it provides the observability primitive (`record_last_event`
  / `get_last_event_ts`) but explicitly stops at observability and per-chat scope.
  This plan extends the *same conceptual line* to an account-wide positive-liveness
  signal with automatic recovery.
- **#1614 / #1270** (session-liveness-check): "alive ≠ working" pattern in the
  session/worker domain (per-tool timeouts, sticky own-progress fields). **Relevance:
  conceptual** — same failure class, different substrate; informs the design instinct
  to use a *positive* progress signal rather than absence-of-traffic.
- **#1172** (rejected "silence = failure" for sessions): **Relevance: high as a
  no-go** — establishes that a fixed silence threshold is the wrong primitive. This
  plan's positive-probe design exists specifically to avoid repeating it.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #1559 (#1408) | Added `bridge:last_event` + per-chat silent-stream WARNING | Observability-only (never recovers); per-chat and gated on `respond_to_unaddressed` chats with prior activity — blind to account-wide silence where no such chat recently fired |
| Existing `bridge_watchdog.py` | Process-liveness + log-freshness monitoring | `logs_fresh` is satisfied by the heartbeat loop's own writes, which are independent of update flow — so a wedged loop never looks unhealthy |

**Root cause pattern:** Every existing check measures a proxy (process up, log
recently written, a specific chat received traffic) rather than a *positive proof
that the update-dispatch path round-trips end to end*. The fix must add that proof
and wire it into the existing recovery ladder.

## Research

No relevant external findings — Telethon update-loop wedging is a known but
undocumented edge; the design relies on codebase context (existing reconciler
round-trip, heartbeat cadence, watchdog ladder) and the #1408/#1172 precedents.
The positive-probe approach (`get_me`-style authenticated round-trip) is a standard
liveness pattern, not library-specific.

## Spike Results

### spike-1: Does the reconciler's existing `get_dialogs()` round-trip prove the *update handler* is live, or only the connection?
- **Assumption**: "A successful `get_dialogs()` every 180s already proves enough liveness, so no new probe is needed."
- **Method**: code-read (`bridge/reconciler.py`, `bridge/telegram_bridge.py`)
- **Finding**: `get_dialogs()` proves the **connection** round-trips, but the wedge
  in the incident was the `@client.on(events.NewMessage)` *handler* going silent
  while the connection stayed live. A connection probe alone would have stayed green
  during the 8-hour outage. The positive signal must be tied to **handler-firing**,
  not connection health.
- **Confidence**: high
- **Impact on plan**: The liveness signal is a **`last_update_received` heartbeat
  written EXCLUSIVELY from inside the NewMessage handler**, NOT from the reconciler
  ingest path and NOT a standalone connection probe. **Critical (B2):** the reconciler
  rides the `get_dialogs()` poll every ~180s and recovers missed messages on that path;
  if it also wrote `last_update_received`, it would re-green the signal every ~3 min
  while the handler is dead — defeating the entire detector. The handler is the *only*
  writer of `last_update_received`; the reconciler writes `last_probe_ok` only. The
  watchdog's PRIMARY rule is an absolute-staleness ceiling on `last_update_received`
  with no per-chat precondition (see B1 resolution / Open Question 1).

### spike-2: Can the external watchdog read the bridge's liveness signal?
- **Assumption**: "The watchdog (separate launchd process) can consume an internal bridge signal."
- **Method**: code-read (`monitoring/bridge_watchdog.py`)
- **Finding**: The watchdog runs in-repo with `sys.path` set, already imports
  `bridge.utc` and `monitoring.crash_tracker`, and can construct the same
  `decode_responses` Redis client `bridge/dedup.py` already uses. It can read the
  `bridge:last_update_received` / `bridge:last_probe_ok` Redis keys with zero new
  infrastructure. **N1 resolution:** the earlier draft proposed a redundant
  `data/last_update_received` file fallback — DROPPED. The whole system (bridge,
  reconciler, dedup keys, silent_stream) already depends on Redis being reachable, and
  the watchdog treats an unreadable signal as *inconclusive* (never wedged, see C3), so
  a second file-based source adds a write path, a coherence question (which wins on
  disagreement?), and no real availability. Single source of truth: Redis.
- **Confidence**: high
- **Impact on plan**: Signal is **produced inside the bridge** (where it's accurate)
  and **consumed by the external watchdog** (which owns recovery and survives a fully
  wedged bridge). This resolves the issue's "internal vs external" open question by
  splitting it: produce internal, decide-and-recover external. No `data/` file
  fallback — Redis-only (N1).

## Data Flow

1. **Entry point**: An incoming Telegram message dispatches Telethon's
   `_update_loop` → fires `@client.on(events.NewMessage)` (`telegram_bridge.py:1093`).
2. **Liveness write (NEW)**: The NewMessage handler — **and ONLY the NewMessage
   handler** — records `last_update_received = now` to a freeform Redis key
   `bridge:last_update_received` (mirroring the #1408 freeform-key convention).
   Best-effort, never raises. The reconciler's `get_dialogs()`/ingest path does NOT
   write this key (see B2 below): writing it from the reconciler would re-green the
   signal every ~180s while the handler is dead, making the wedge undetectable. The
   reconciler writes `last_probe_ok` only.
2b. **Connection-probe write (NEW)**: The reconciler's existing `get_dialogs()` pass
   (no new API call) records `bridge:last_probe_ok = now` whenever the connection
   round-trips successfully. This is the *only* signal the reconciler writes. It
   distinguishes "wedged update loop" (probe OK, no `NewMessage` updates) from "bridge
   disconnected" (probe failing — already handled by existing reconnect logic).
3. **Watchdog read (NEW)**: Every 60s, `bridge_watchdog.py` reads both signals and
   evaluates the wedged rule. The **PRIMARY, always-on trigger** is the
   absolute-staleness ceiling: process alive + recent `last_probe_ok` (connection
   works) + `last_update_received` older than `UPDATE_STALENESS_CEILING`. This path
   has **no per-chat `bridge:last_event` precondition** — it fires on account-wide
   silence, which is exactly the 2026-06-16 incident shape. A secondary, *earlier*
   trigger (the recently-active-chat-went-quiet corroboration) may fire sooner when a
   `respond_to_unaddressed` chat that was active inside the window goes quiet, but it
   is an accelerator, never a gate: the ceiling alone is sufficient. Cold start within
   the startup grace window is treated as healthy, not wedged.
4. **Output**: On a wedged verdict, the watchdog injects a new escalation step into
   its existing ladder: a `restart_bridge()` (which re-runs `catch_up=True` →
   lossless backfill), logged via `crash_tracker.log_crash("bridge_update_loop_wedged")`.

## Architectural Impact

- **New dependencies**: none. Reuses Redis (already used for `bridge:last_event:*`)
  and the existing watchdog/crash-tracker plumbing.
- **Interface changes**: `HealthStatus` dataclass gains an `update_flow_live: bool`
  field (and supporting issue string). `bridge/dedup.py` (or a new small
  `bridge/liveness.py`) gains `record_update_received()` / `get_last_update_received()`
  and `record_probe_ok()` / `get_last_probe_ok()` mirroring the existing
  `record_last_event` pattern.
- **Signal design decision (C1 — keep both, not one):** Two signals are retained
  deliberately. `last_update_received` (handler-only) detects the wedge;
  `last_probe_ok` (reconciler) is the **disconfirmation guard** that prevents a
  false-positive restart during a genuine network partition — without it, a
  disconnected-but-process-alive bridge (no updates *because* there's no connection)
  would be misclassified as wedged and restarted pointlessly while the existing
  reconnect logic is already handling it. Collapsing to a single signal would either
  reintroduce that false positive or require the watchdog to re-derive connection
  health some other way. The marginal cost of the second signal is one cheap `SET`
  per ~180s reconciler pass (not per message) and one watchdog read — negligible. Both
  signals stay.
- **Escalation model (C4):** `update_flow_live` is a boolean health field; the wedged
  reason maps to a fixed `recovery_level` of 1–2 (plain restart) and is explicitly
  capped so it can never reach level 4 (auto-revert). The integer `recovery_level`
  ladder is unchanged; the wedged reason simply slots a new max() contribution bounded
  at 2. No new escalation tier, no auto-revert coupling.
- **Coupling**: Slightly increases coupling between bridge and watchdog via shared
  freeform signal keys — but this mirrors the already-accepted `bridge:last_event`
  cross-process pattern. Redis-only, no `data/` file (N1).
- **Data ownership**: Bridge owns/writes the liveness signals (handler →
  `last_update_received`, reconciler → `last_probe_ok`); watchdog is read-only on both.
  No Popoto-managed keys touched (freeform keys per #1408 convention).
- **Reversibility**: High. Removing the field, the writers, and the watchdog branch
  fully reverts; no schema or migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm threshold/corroboration policy, recovery placement)
- Review rounds: 1 (async/timing correctness review of the wedged-vs-quiet logic)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "import redis,os; redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0')).ping()"` | Liveness signal storage (freeform keys) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/bridge_stale_update_stream_detector.md`

## Solution

### Key Elements

- **Update-flow liveness signal**: a `last_update_received` timestamp written
  **exclusively from inside the NewMessage handler** (NOT the reconciler) — the
  *positive* proof that the update-dispatch handler is firing, not an
  absence-of-traffic inference. (B2: reconciler writing this would re-green it every
  ~180s while the handler is dead.)
- **Connection corroboration signal**: `last_probe_ok`, set when the reconciler's
  existing `get_dialogs()` pass succeeds — distinguishes a wedged update loop
  (connection fine, no updates) from a disconnect (already handled elsewhere). This is
  the watchdog's guard against false-positive restarts during a genuine network
  partition (see C1 for why both signals are retained).
- **Wedged detector**: a watchdog branch whose **PRIMARY trigger is an absolute
  staleness ceiling** on `last_update_received` with **no per-chat precondition** —
  fires on account-wide silence (the 2026-06-16 incident shape). Gated only by
  `process_running` + recent `last_probe_ok` + past the startup grace window. The
  recently-active-chat-went-quiet check is a secondary *accelerator* that can fire
  sooner, never a required gate (B1 / Open Question 1).
- **Recovery**: a new dedicated reason at the low end of the watchdog's existing
  5-level ladder (level 1/2, a plain `restart_bridge()` re-running `catch_up=True` →
  lossless backfill). **Never escalates to level 4 auto-revert** (C4): a wedged update
  loop is a runtime condition, not a bad-commit signature.

### Flow

Incoming message → NewMessage handler fires → **writes `last_update_received`** (handler
is the ONLY writer) → (periodically) reconciler `get_dialogs()` succeeds → **writes
`last_probe_ok`** (reconciler's only liveness write) → watchdog 60s tick reads both →
**wedged verdict** when process alive + `last_probe_ok` recent + `last_update_received`
past the absolute ceiling (primary, no per-chat gate) → **restart bridge** → catchup
backfills → handler fires again → `last_update_received` resumes → green

### Technical Approach

- Add `record_update_received()` / `get_last_update_received()` to a small
  `bridge/liveness.py` (or extend `bridge/dedup.py`), mirroring the existing
  `record_last_event` freeform-key + best-effort-never-raises pattern. Call it in the
  NewMessage handler **only** (`telegram_bridge.py:1093`, after the early-return
  guards). **Do NOT call it from the reconciler** — the reconciler rides the
  `get_dialogs()` poll and would re-green the signal every ~180s while the handler is
  dead, defeating the detector (B2).
- Add `record_probe_ok()` in the reconciler's existing success path (no new API call —
  rides the `get_dialogs()` already happening every 180s). This is the reconciler's
  **only** liveness write.
- Extend `HealthStatus` with `update_flow_live` and add `assess_update_flow()` to
  `bridge_watchdog.py` implementing the **ceiling-primary** wedged rule:
  - **PRIMARY trigger (always-on, no per-chat precondition):** `process_running` AND
    a recent `last_probe_ok` (connection works) AND `last_update_received` older than
    `UPDATE_STALENESS_CEILING` (a long absolute ceiling at the incident's multi-hour
    scale) AND the bridge is past its startup grace window. This alone is sufficient
    and would have fired on the 2026-06-16 account-wide silence.
  - **SECONDARY accelerator (optional, fires sooner, never a gate):** if a
    `respond_to_unaddressed` chat had a `bridge:last_event` *inside* the window and has
    since gone quiet past a shorter `UPDATE_STALENESS_WARN` threshold (still with
    `process_running` + recent `last_probe_ok`), the verdict can trip earlier. Absence
    of any such chat must NOT suppress the primary ceiling trigger.
  - Threshold *values* are tunable constants in `bridge_watchdog.py`, no hard-coded
    magic in the policy narrative — see Open Question 1 (now resolved: ceiling is
    mandatory).
- Wire a recovery branch that calls the existing `restart_bridge()` and records
  `crash_tracker.log_crash("bridge_update_loop_wedged")`. Slot it as a dedicated
  reason at level 1/2 of the existing ladder (a plain restart suffices because catchup
  is lossless). **Hard cap: this reason never raises `recovery_level` above 2** — it
  must not interact with the level-4 auto-revert path, which keys off crash-pattern
  commit signatures, not runtime wedges (C4).
- Self-blindness signal (C3): if `assess_update_flow()` cannot read either signal
  (Redis unreadable, both keys missing past the grace window), it returns
  *inconclusive* (NOT wedged, no restart) **and** logs a distinct WARNING
  (`bridge_update_flow_signal_unreadable`) so a persistently blind detector is itself
  visible in `logs/watchdog.log` rather than silently failing open forever.
- All writes best-effort (`try/except` → WARNING), consistent with #1408.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_update_received` / `record_probe_ok` use `except Exception` → WARNING
  (matching `record_last_event`). Add a test asserting a Redis failure logs a WARNING
  and the handler still processes the message (signal write never blocks ingest).
- [ ] Watchdog `assess_update_flow` must treat "signal unreadable" as inconclusive
  (NOT wedged) — test that a Redis read failure does not trigger a false restart.

### Empty/Invalid Input Handling
- [ ] `get_last_update_received` returns `None` when no key exists (cold start) —
  test that a missing signal within the startup grace window is treated as healthy,
  not wedged.
- [ ] Test corrupt/non-numeric stored value is coerced to `None` and treated as
  inconclusive.

### Error State Rendering
- [ ] No user-visible UI; the "rendering" path is the `crash_tracker.log_crash` entry
  and the watchdog log line. Test that a wedged verdict produces both the crash record
  and a distinguishable log message (`bridge_update_loop_wedged`).
- [ ] Self-blindness (C3): test that when the signal is unreadable past the grace
  window, the watchdog emits the distinct `bridge_update_flow_signal_unreadable`
  WARNING (so failing-open is itself observable) and does NOT restart.

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py` (or equivalent watchdog unit test, if
  present) — UPDATE: `HealthStatus` gains a field; existing constructor/health-verdict
  assertions must account for `update_flow_live`. (Builder: confirm the file name via
  `ls tests/ | grep -i watchdog`; if none exists, this becomes a new test file.)
- [ ] `tests/unit/test_silent_stream.py` / `tests/unit/test_dedup*.py` (if present) —
  UPDATE only if `bridge/dedup.py` is extended in place; if a new `bridge/liveness.py`
  is added instead, no change.
- [ ] New: `tests/unit/test_bridge_liveness.py` — REPLACE/CREATE: cover signal
  read/write, cold-start grace, corruption, Redis-failure inconclusiveness.
- [ ] New: `tests/integration/test_update_loop_wedge_recovery.py` — CREATE: cover the
  matrix — (a) account-wide silence past the absolute ceiling with healthy
  `last_probe_ok` and NO recently-active chat → wedged verdict + restart (B1 incident
  shape); (b) quiet *below* the ceiling → no restart; (c) stale update +
  failing-probe disconnect → NOT wedged; (d) reconciler activity while handler dead →
  signal stays stale, still detected (B2 regression); (e) unreadable signal →
  inconclusive + `bridge_update_flow_signal_unreadable` WARNING (C3).

No existing behavior is removed — changes are additive to `HealthStatus` and the
watchdog ladder, so most existing watchdog tests pass unchanged once the new field
has a default.

## Rabbit Holes

- **Inspecting Telethon's internal `_update_loop` task state directly.** Tempting
  (the issue lists it as a candidate) but couples to Telethon internals that change
  across versions. The external positive-signal approach is version-stable — avoid
  reaching into private task objects.
- **Detecting the specific `Account is now banned` / `ChannelPrivateError` terminal
  conditions.** Pattern-matching log strings is brittle and the issue itself notes the
  banned message is benign per-channel. Don't build a log-string classifier; rely on
  the positive liveness signal which is agnostic to *why* the loop wedged.
- **Building the runtime singleton mutex** (the issue's "Related hardening" sibling).
  Out of scope — separate concern, see No-Gos.
- **Re-tuning the per-chat silent_stream watcher** to recover instead of warn.
  Different scope (per-chat vs account-wide); leave #1408's watcher as observability.

## Risks

### Risk 1: False-positive restart during a genuinely quiet period
**Impact:** Unnecessary bridge restart (cheap — catchup is lossless — but noisy and
could mask a real issue if it loops).
**Mitigation:** The PRIMARY ceiling is deliberately long (multi-hour, the incident
scale), so a genuinely quiet period shorter than the ceiling never trips it; a
successful `last_probe_ok` is required so a disconnect is not misread as a wedge; and a
startup grace window suppresses cold-start false positives. The ceiling being long is
what makes pure overnight quiet safe — not a per-chat gate (B1).

### Risk 1b (C2): Per-restart suppression swallows a genuine re-wedge
**Impact:** If the suppression window is too long, a bridge that wedges *again*
immediately after a restart-driven recovery would be ignored until the window expires,
re-introducing a silent outage the detector was built to catch.
**Mitigation:** Scope suppression tightly. The suppression window must be only
`startup_grace + worst_case_catchup` long (enough for the restarted bridge to reach a
fresh `last_update_received` or, in genuinely quiet periods, a fresh `last_probe_ok`),
NOT a fixed long cooldown. Basis decision (C2): the suppression clock keys off the
**observed bridge process start time** (the watchdog already resolves the bridge PID
via `is_bridge_running()`; process start is read from `ps etime`, the same source the
zombie check uses — proven, not novel). When the restarted process's start time is
newer than the last recovery action, suppression naturally clears — so a re-wedge of a
*freshly restarted* process is detected on the next ceiling crossing rather than being
masked by a stale cooldown timer. The detector also records each wedged restart via
`crash_tracker.log_crash`; the existing "≥5 crashes in 30 min → level 5 alert human"
rule (already in `check_bridge_health`) is the backstop against a true restart loop, so
a persistent re-wedge escalates to a human rather than looping silently.

### Risk 2: The liveness write itself adds latency to the hot message path
**Impact:** Every inbound message would pay a Redis write.
**Mitigation:** Write is a single `SET` with TTL (same cost as the already-present
`record_last_event` call in the same handler) and is best-effort/non-blocking. Net
adds one cheap SET alongside one already there.

### Risk 3: Watchdog and bridge disagree because the signal is unreadable
**Impact:** A Redis blip could make the watchdog think the loop is wedged.
**Mitigation:** "Signal unreadable" is treated as inconclusive (never wedged) and
emits a distinct `bridge_update_flow_signal_unreadable` WARNING so a persistently blind
detector is visible rather than silently failing open (C3). No `data/` file fallback
(N1) — the whole bridge already requires Redis, and inconclusive-on-unreadable already
removes the false-restart risk a second source would guard against.

## Race Conditions

### Race 1: Restart fires before catchup window is established
**Location:** `monitoring/bridge_watchdog.py` recovery branch ↔ `telegram_bridge.py`
startup (`_write_last_connected`, catchup at :2902).
**Trigger:** Watchdog restarts the bridge; on restart the new process must write a
fresh `last_update_received`/`last_probe_ok` before the watchdog's next tick, or a
second restart could fire.
**Data prerequisite:** The startup grace window (`last_update_received` missing within
N seconds of process start = healthy) must be longer than worst-case catchup time.
**State prerequisite:** Bridge must record `last_probe_ok` on first successful
`get_dialogs()` after restart. Note: in a genuinely quiet account, `last_update_received`
may legitimately stay stale after restart (no messages arrive) — this is why the grace
window keys off process start, not off seeing a fresh `last_update_received`, and why
the absolute ceiling (not a short timeout) is the trigger.
**Mitigation:** Startup grace window keyed off the **observed process start time**
(`is_bridge_running()` PID → `ps etime`, the same proven source the zombie check uses,
C2); per-restart suppression scoped to `grace + worst_case_catchup` and cleared when a
newer process-start time is observed, so the watchdog won't re-restart within the
window but also won't mask a re-wedge of a freshly restarted process.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1712] The **runtime singleton mutex** for the bridge
  (`bridge:instance_lock:{hostname}` `SET NX` + TTL, mirroring
  `agent/session_pickup.py:_acquire_pop_lock`). The issue explicitly files it as a
  smaller sibling ("could be its own issue") and rates it lower priority than the
  detector; it is defense-in-depth against a rare bypass-launchd scenario, orthogonal
  to update-loop wedging. Tracked in the same issue's "Related hardening" section for
  a follow-up split. *(Resolved Decision 3: stays out of scope; split to its own issue
  later.)*
- Reworking `bridge/silent_stream.py` from observability to recovery — different
  (per-chat) scope; this plan adds an account-wide signal instead.

## Update System

No update system changes required — this feature is purely internal to the bridge and
watchdog, both already deployed by the existing `/update` flow. No new dependency, no
new config file, no new launchd label (the watchdog `com.valor.bridge-watchdog` already
exists). The new Redis keys are freeform and self-expiring (TTL), needing no migration.

## Agent Integration

No agent integration required — this is a bridge/watchdog-internal change. The agent
(Telegram-facing) does not invoke the detector; it is a background self-heal mechanism.
No MCP server, no `.mcp.json` change, no new CLI entry point in `pyproject.toml`. The
bridge already imports the liveness writer internally (NewMessage handler + reconciler);
the watchdog already imports bridge/monitoring helpers.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` — add the update-flow liveness
  signal and the new "update loop wedged" escalation step to the documented ladder.
- [ ] Update `docs/features/bridge-worker-architecture.md` if it enumerates monitored
  health signals — add `last_update_received` / `last_probe_ok`.
- [ ] Confirm/add an entry in `docs/features/README.md` index if the self-healing doc
  isn't already listed.

### External Documentation Site
- [ ] No external docs site for this repo — N/A.

### Inline Documentation
- [ ] Docstring on the new liveness module/functions explaining the positive-signal
  rationale and the freeform-key convention (cross-reference #1408).
- [ ] Comment on the watchdog wedged-rule explaining the corroboration requirement and
  the #1172 silence=failure no-go it avoids.

## Success Criteria

- [ ] `bridge/liveness.py` (or extended `bridge/dedup.py`) writes
  `last_update_received` from the NewMessage handler ONLY — NOT the reconciler (B2).
- [ ] Reconciler records `last_probe_ok` on successful `get_dialogs()` (no new API
  call) and is its only liveness write.
- [ ] `bridge_watchdog.py` `HealthStatus.update_flow_live` + `assess_update_flow()`
  implement the **ceiling-primary** rule: the absolute-staleness ceiling fires with NO
  per-chat precondition (B1).
- [ ] Account-wide silence past the ceiling (no recently-active chat) DOES trigger a
  restart — the 2026-06-16 incident shape — verified by test.
- [ ] A wedged verdict triggers `restart_bridge()` and logs
  `crash_tracker.log_crash("bridge_update_loop_wedged")`, with `recovery_level` capped
  at ≤2 (never level-4 auto-revert) (C4).
- [ ] Quiet *below* the ceiling does NOT trigger a restart — verified by test.
- [ ] Signal-unreadable / cold-start are treated as inconclusive, not wedged, and an
  unreadable signal past the grace window emits `bridge_update_flow_signal_unreadable`
  (C3).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms the NewMessage handler references the liveness writer and the
  watchdog references the new signal reader.

## Team Orchestration

### Team Members

- **Builder (liveness-signal)**
  - Name: liveness-builder
  - Role: Add the `last_update_received` / `last_probe_ok` writers and wire them into
    the NewMessage handler and reconciler.
  - Agent Type: builder
  - Resume: true

- **Builder (watchdog-detector)**
  - Name: watchdog-builder
  - Role: Extend `HealthStatus`, implement `assess_update_flow()` corroborated rule,
    and wire the recovery branch.
  - Agent Type: async-specialist
  - Resume: true

- **Validator (detector)**
  - Name: detector-validator
  - Role: Verify wedged-vs-quiet logic, cold-start grace, and no-false-restart.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update self-healing + architecture docs.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(see template — Tier 1 builder/validator/documentarian + async-specialist for the
timing-sensitive watchdog rule.)

## Step by Step Tasks

### 1. Build liveness signal writers
- **Task ID**: build-liveness-signal
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_liveness.py (create)
- **Informed By**: spike-1 (signal must be tied to handler-firing, not connection)
- **Assigned To**: liveness-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `record_update_received()` / `get_last_update_received()` and
  `record_probe_ok()` / `get_last_probe_ok()` in `bridge/liveness.py`, mirroring the
  `record_last_event` freeform-key + best-effort pattern. **Redis-only, no `data/`
  file fallback** (N1).
- Call `record_update_received()` in the NewMessage handler ONLY (after early-return
  guards). **Do NOT call it from the reconciler** (B2 — reconciler re-greening defeats
  the detector). Call `record_probe_ok()` on the reconciler's successful
  `get_dialogs()` pass — that is the reconciler's only liveness write.
- Unit tests: read/write, cold-start `None`, corruption coercion, Redis-failure
  WARNING + non-blocking; assert the reconciler ingest path does NOT write
  `last_update_received` (regression guard for B2).

### 2. Build watchdog wedged detector + recovery
- **Task ID**: build-watchdog-detector
- **Depends On**: build-liveness-signal
- **Validates**: tests/integration/test_update_loop_wedge_recovery.py (create), watchdog unit tests
- **Informed By**: spike-2 (watchdog can read the signal), #1172 (no silence=failure)
- **Assigned To**: watchdog-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Add `HealthStatus.update_flow_live` (default preserving existing tests) and
  `assess_update_flow()` implementing the **ceiling-primary** rule: PRIMARY trigger is
  the absolute staleness ceiling with NO per-chat precondition (B1); the
  recently-active-chat-quiet check is a secondary accelerator only.
- Wire a `bridge_update_loop_wedged` recovery branch into the existing ladder using
  `restart_bridge()` + `crash_tracker.log_crash`; **cap its `recovery_level`
  contribution at 2 — never level 4 auto-revert** (C4). Add startup grace keyed off
  observed process start time and per-restart suppression scoped to
  `grace + worst_case_catchup`, cleared on a newer process-start observation (C2).
- Emit a distinct `bridge_update_flow_signal_unreadable` WARNING when neither signal
  is readable past the grace window, so a persistently blind detector is visible (C3).
- Integration tests: (a) account-wide silence past the ceiling with healthy
  `last_probe_ok` and NO recently-active chat → wedged verdict + restart (the B1
  incident shape); (b) overnight-quiet below the ceiling → no restart; (c) stale
  `last_update_received` but failing `last_probe_ok` (disconnect) → NOT wedged; (d)
  unreadable signal → inconclusive + WARNING; (e) reconciler-only activity does NOT
  keep the signal green while the handler is dead (B2 regression).

### 3. Validate detector
- **Task ID**: validate-detector
- **Depends On**: build-watchdog-detector
- **Assigned To**: detector-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify wedged-vs-quiet matrix, cold-start grace, no-false-restart, lossless-recovery
  rationale.
- Run the new unit + integration tests; report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-detector
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` and architecture doc; confirm index entry.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: detector-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_bridge_liveness.py tests/integration/test_update_loop_wedge_recovery.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Handler wires writer | `grep -n "record_update_received" bridge/telegram_bridge.py` | output contains record_update_received |
| Watchdog reads signal | `grep -n "get_last_update_received\|update_flow_live" monitoring/bridge_watchdog.py` | output contains update_flow_live |
| Recovery reason logged | `grep -n "bridge_update_loop_wedged" monitoring/bridge_watchdog.py` | output contains bridge_update_loop_wedged |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | B1 | Corroboration rule re-inherits #1408's per-chat blind spot; ceiling demoted to OR-clause | Data Flow step 3, Solution Key Elements, Technical Approach, Resolved Decision 1 | Absolute-staleness ceiling is now the PRIMARY always-on trigger with NO per-chat precondition; recently-active-chat check is a non-gating accelerator only |
| Blocker | B2 | Reconciler-ingest write of `last_update_received` re-greens signal every ~180s while handler dead | spike-1 impact, Data Flow step 2, Solution, Technical Approach, task 1, tests | `last_update_received` written ONLY from the NewMessage handler; reconciler writes `last_probe_ok` only; B2 regression test added |
| Concern | C1 | Two-signal design possibly over-engineered | Architectural Impact (Signal design decision) | Keep both: `last_probe_ok` is the disconfirmation guard against false-positive restart during a genuine disconnect; cost is one SET per ~180s |
| Concern | C2 | Per-restart suppression can swallow a re-wedge; process-start basis unproven | Risk 1b, Race 1, Technical Approach, task 2 | Suppression scoped to `grace+catchup`, keyed off observed `ps etime` process-start (same source as zombie check), cleared on newer start; ≥5-crashes/30min backstop |
| Concern | C3 | Detector fails open with no self-blindness alert | Technical Approach, Failure Path Test Strategy, Risk 3, task 2 | Unreadable signal past grace → inconclusive + distinct `bridge_update_flow_signal_unreadable` WARNING |
| Concern | C4 | boolean vs integer escalation unspecified; could creep to level-4 auto-revert | Architectural Impact (Escalation model), Solution, Technical Approach, Resolved Decision 2 | `update_flow_live` is boolean health; wedged reason contributes `max(level,2)`, hard-capped, never level-4 auto-revert |
| Nit | N1 | Redundant `data/` file fallback | spike-2 finding, Architectural Impact, Risk 3, task 1 | Dropped; Redis-only single source of truth |

---

## Resolved Decisions

These were the plan's three Open Questions; all are now resolved in-plan (critique
revision, addressing blockers B1/B2 and concerns C1–C4, N1).

1. **Corroboration policy — RESOLVED: the absolute-staleness ceiling is the PRIMARY,
   mandatory, always-on trigger with NO per-chat precondition** (B1). The earlier draft
   demoted the ceiling to an OR-clause and gated the primary path on a recently-active
   `respond_to_unaddressed` chat going quiet — which re-inherited the exact #1408 blind
   spot, since the 2026-06-16 incident was account-wide silence with no such chat. Now:
   the ceiling alone (process alive + recent `last_probe_ok` + `last_update_received`
   past the ceiling + past startup grace) is sufficient. The recently-active-chat-quiet
   check survives only as an optional *accelerator* that can trip the verdict sooner; it
   can never gate or suppress the primary ceiling. Threshold *values* remain tunable
   constants in `bridge_watchdog.py` (no hard-coded numbers in the policy narrative).

2. **Recovery placement — RESOLVED: dedicated reason at level 1–2 (plain restart),
   hard-capped so it can NEVER reach level 4 auto-revert** (C4). Catchup is lossless, so
   a plain `restart_bridge()` fully recovers; a wedged update loop is a runtime
   condition, not a bad-commit signature, so coupling it to the commit-revert path would
   be wrong. The wedged reason contributes `max(recovery_level, 2)` at most. The
   existing "≥5 crashes in 30 min → level 5 alert human" rule remains the loop backstop.

3. **Singleton mutex sibling — RESOLVED: out of scope, stays a tagged No-Go.** The
   runtime singleton lock is orthogonal defense-in-depth (it guards a bypass-launchd
   double-launch, not update-loop wedging) and the issue itself rates it lower priority.
   It remains a `[SEPARATE-SLUG #1712]` No-Go to be split into its own issue; pulling it
   in now would widen a Medium-appetite bug fix into two unrelated deliverables.
