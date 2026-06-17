---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-17
tracking: https://github.com/tomcounsell/ai/issues/1712
last_comment_id:
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
  written from inside the NewMessage handler** (and the reconciler's own
  message-ingest path), NOT a standalone connection probe. The watchdog compares
  this against a *staleness-with-corroboration* rule, not a bare timeout.

### spike-2: Can the external watchdog read the bridge's liveness signal?
- **Assumption**: "The watchdog (separate launchd process) can consume an internal bridge signal."
- **Method**: code-read (`monitoring/bridge_watchdog.py`)
- **Finding**: The watchdog runs in-repo with `sys.path` set, already imports
  `bridge.utc` and `monitoring.crash_tracker`, and reads files under `DATA_DIR`. It
  can read a `data/last_update_received` file or a `bridge:last_update_received`
  Redis key with zero new infrastructure.
- **Confidence**: high
- **Impact on plan**: Signal is **produced inside the bridge** (where it's accurate)
  and **consumed by the external watchdog** (which owns recovery and survives a fully
  wedged bridge). This resolves the issue's "internal vs external" open question by
  splitting it: produce internal, decide-and-recover external.

## Data Flow

1. **Entry point**: An incoming Telegram message dispatches Telethon's
   `_update_loop` → fires `@client.on(events.NewMessage)` (`telegram_bridge.py:1093`).
2. **Liveness write (NEW)**: The handler (and the reconciler's ingest path) records
   `last_update_received = now` to a freeform Redis key `bridge:last_update_received`
   (mirroring the #1408 freeform-key convention) and, as a watchdog-readable fallback,
   a `data/last_update_received` file. Best-effort, never raises.
2b. **Corroboration write (NEW)**: A lightweight periodic self-probe (riding the
   existing reconciler `get_dialogs()` pass, no new API call) records
   `bridge:last_probe_ok = now` whenever the connection round-trips successfully.
   This distinguishes "wedged update loop" (probe OK, no updates) from "bridge
   disconnected" (probe failing — already handled by existing reconnect logic).
3. **Watchdog read (NEW)**: Every 60s, `bridge_watchdog.py` reads both signals plus
   the **expected-traffic floor**: how many monitored `respond_to_unaddressed` chats
   exist and whether any `bridge:last_event:{chat_id}` shows activity within the
   window. The "wedged" verdict requires **all** of: process alive, probe OK
   (connection works), `last_update_received` stale beyond threshold, AND at least
   one corroborating signal that traffic *should* have arrived (a recently-active
   chat went quiet, OR the staleness exceeds a long absolute ceiling). Pure overnight
   silence with no recently-active chat does NOT trip recovery.
4. **Output**: On a wedged verdict, the watchdog injects a new escalation step into
   its existing ladder: a `restart_bridge()` (which re-runs `catch_up=True` →
   lossless backfill), logged via `crash_tracker.log_crash("bridge_update_loop_wedged")`.

## Architectural Impact

- **New dependencies**: none. Reuses Redis (already used for `bridge:last_event:*`)
  and the existing watchdog/crash-tracker plumbing.
- **Interface changes**: `HealthStatus` dataclass gains an `update_flow_live: bool`
  field (and supporting issue string). `bridge/dedup.py` (or a new small
  `bridge/liveness.py`) gains `record_update_received()` / `get_last_update_received()`
  mirroring the existing `record_last_event` pattern.
- **Coupling**: Slightly increases coupling between bridge and watchdog via a shared
  freeform signal key — but this mirrors the already-accepted `bridge:last_event`
  cross-process pattern.
- **Data ownership**: Bridge owns/writes the liveness signal; watchdog is read-only
  on it. No Popoto-managed keys touched (freeform keys per #1408 convention).
- **Reversibility**: High. Removing the field, the writer, and the watchdog branch
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

- **Update-flow liveness signal**: a `last_update_received` timestamp written from
  inside the NewMessage handler and the reconciler ingest path — the *positive*
  proof that update dispatch is firing, not an absence-of-traffic inference.
- **Connection corroboration signal**: `last_probe_ok`, set when the reconciler's
  existing `get_dialogs()` pass succeeds — distinguishes a wedged update loop
  (connection fine, no updates) from a disconnect (already handled elsewhere).
- **Wedged detector**: a watchdog branch that declares the update loop wedged ONLY
  when all corroborating conditions hold (process alive + connection probing OK +
  update-flow stale + evidence traffic should have arrived). Avoids #1172's
  silence=failure trap.
- **Recovery**: a new escalation step in the watchdog's existing 5-level ladder that
  restarts the bridge (re-running `catch_up=True` → lossless backfill).

### Flow

Incoming message → NewMessage handler fires → **writes `last_update_received`** →
(periodically) reconciler `get_dialogs()` succeeds → **writes `last_probe_ok`** →
watchdog 60s tick reads both + expected-traffic floor → **wedged verdict** (all
conditions) → **restart bridge** → catchup backfills → liveness signal resumes → green

### Technical Approach

- Add `record_update_received()` / `get_last_update_received()` to a small
  `bridge/liveness.py` (or extend `bridge/dedup.py`), mirroring the existing
  `record_last_event` freeform-key + best-effort-never-raises pattern. Call it in the
  NewMessage handler (`telegram_bridge.py:1093`, after the early-return guards) and in
  the reconciler's message-ingest path.
- Add `record_probe_ok()` in the reconciler's existing success path (no new API call —
  rides the `get_dialogs()` already happening every 180s).
- Extend `HealthStatus` with `update_flow_live` and add `assess_update_flow()` to
  `bridge_watchdog.py` implementing the **corroborated** wedged rule:
  - Require `process_running` AND a recent `last_probe_ok` (connection works).
  - Require `last_update_received` older than `UPDATE_STALENESS_THRESHOLD`.
  - Require corroboration: at least one `respond_to_unaddressed` chat with a
    `bridge:last_event` *inside* the window that has since gone quiet, OR staleness
    beyond a long absolute ceiling (e.g. the incident's multi-hour scale). Tunable
    constants, no hard-coded magic in the policy narrative — see Open Questions.
- Wire a recovery branch that calls the existing `restart_bridge()` and records
  `crash_tracker.log_crash("bridge_update_loop_wedged")`. Slot it as a dedicated
  reason within the existing ladder (likely level 1/2 — a plain restart suffices
  because catchup is lossless), NOT auto-revert.
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
- [ ] New: `tests/integration/test_update_loop_wedge_recovery.py` — CREATE: simulate
  stale `last_update_received` + healthy `last_probe_ok` + a recently-active-then-quiet
  chat, assert the watchdog reaches a wedged verdict and would restart; and the inverse
  (overnight silence, no recently-active chat → no restart).

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
**Mitigation:** The corroborated wedged rule requires positive evidence traffic
*should* have arrived (a recently-active chat gone quiet) OR a long absolute ceiling,
plus a successful connection probe. A startup grace window suppresses cold-start
false positives. Per-restart suppression prevents restart loops.

### Risk 2: The liveness write itself adds latency to the hot message path
**Impact:** Every inbound message would pay a Redis write.
**Mitigation:** Write is a single `SET` with TTL (same cost as the already-present
`record_last_event` call in the same handler) and is best-effort/non-blocking. Net
adds one cheap SET alongside one already there.

### Risk 3: Watchdog and bridge disagree because the signal is unreadable
**Impact:** A Redis blip could make the watchdog think the loop is wedged.
**Mitigation:** "Signal unreadable" is treated as inconclusive (never wedged). The
`data/last_update_received` file fallback gives a second source the watchdog can read
without Redis.

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
`get_dialogs()` after restart.
**Mitigation:** Startup grace window keyed off process start (watchdog already knows
bridge PID/start); per-restart suppression so the watchdog won't re-restart within the
grace+catchup window.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1712] The **runtime singleton mutex** for the bridge
  (`bridge:instance_lock:{hostname}` `SET NX` + TTL, mirroring
  `agent/session_pickup.py:_acquire_pop_lock`). The issue explicitly files it as a
  smaller sibling ("could be its own issue") and rates it lower priority than the
  detector; it is defense-in-depth against a rare bypass-launchd scenario, orthogonal
  to update-loop wedging. Tracked in the same issue's "Related hardening" section for
  a follow-up split. *(If the reviewer prefers, split into a dedicated issue before
  merge — see Open Question 3.)*
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
  `last_update_received` from the NewMessage handler and reconciler ingest path.
- [ ] Reconciler records `last_probe_ok` on successful `get_dialogs()` (no new API call).
- [ ] `bridge_watchdog.py` `HealthStatus.update_flow_live` + `assess_update_flow()`
  implement the corroborated wedged rule.
- [ ] A wedged verdict triggers `restart_bridge()` and logs
  `crash_tracker.log_crash("bridge_update_loop_wedged")`.
- [ ] Overnight-quiet (no recently-active chat, no corroboration) does NOT trigger a
  restart — verified by test.
- [ ] Signal-unreadable / cold-start are treated as inconclusive, not wedged.
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
  `record_last_event` freeform-key + best-effort pattern; include a `data/` file
  fallback for `last_update_received`.
- Call `record_update_received()` in the NewMessage handler (after early-return guards)
  and in the reconciler ingest path; call `record_probe_ok()` on successful
  `get_dialogs()`.
- Unit tests: read/write, cold-start `None`, corruption coercion, Redis-failure
  WARNING + non-blocking.

### 2. Build watchdog wedged detector + recovery
- **Task ID**: build-watchdog-detector
- **Depends On**: build-liveness-signal
- **Validates**: tests/integration/test_update_loop_wedge_recovery.py (create), watchdog unit tests
- **Informed By**: spike-2 (watchdog can read the signal), #1172 (no silence=failure)
- **Assigned To**: watchdog-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Add `HealthStatus.update_flow_live` (default preserving existing tests) and
  `assess_update_flow()` implementing the corroborated rule.
- Wire a `bridge_update_loop_wedged` recovery branch into the existing ladder using
  `restart_bridge()` + `crash_tracker.log_crash`; add startup grace + per-restart
  suppression.
- Integration tests: wedged verdict triggers restart; overnight-quiet does not;
  unreadable signal is inconclusive.

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Corroboration policy & thresholds.** The plan recommends a *corroborated* wedged
   rule (stale `last_update_received` + healthy `last_probe_ok` + evidence traffic
   should have arrived, e.g. a recently-active chat that went quiet, OR a long absolute
   staleness ceiling). Is the recently-active-chat corroboration sufficient, or do you
   want the absolute-ceiling-only path as a backstop for accounts with no
   `respond_to_unaddressed` chats? (Threshold *values* are deliberately left to
   implementation/tuning per the no-hardcoded-numbers-in-prompts convention.)
2. **Recovery placement.** Plan slots the restart as a dedicated reason at the low end
   of the existing ladder (plain restart, since catchup is lossless), NOT auto-revert.
   Confirm this is the right rung.
3. **Singleton mutex sibling.** The issue files the runtime singleton lock as a smaller
   sibling. Keep it as a tagged No-Go in this plan (split to its own issue later), or
   pull it into scope now as a second small deliverable?
