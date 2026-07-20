---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-20
tracking: https://github.com/tomcounsell/ai/issues/1312
last_comment_id: 4786392177
revision_applied: true
revision_applied_at: 2026-07-20T05:40:07Z
---

# Bridge Warning Reaction When No Worker Is Alive

## Problem

A Telegram user sends a message. The bridge accepts it, classifies it, writes an
`AgentSession` to the Redis queue, and reacts with the normal "seen" emoji (👀).
Everything *looks* fine. But if the worker process on this machine is dead, that
session sits in `pending` forever — nothing drains the queue. From the user's
seat the message looks "received and being thought about." On 2026-05-06 this
silent-failure mode turned a 30-second worker outage into a 7-hour one
(`tg_cuttlefish_-5295380350_9642`).

**Current behavior:**
The bridge calls `dispatch_telegram_session(...)` (which enqueues the session)
without ever checking whether a worker is alive to pick it up. No signal reaches
the user that processing is paused rather than in progress.

**Desired outcome:**
When a message arrives and the machine's worker is not alive, the bridge applies
a visible warning reaction (⚠) to the originating message *before* enqueueing.
The message is still enqueued — no work is lost — but the reaction makes it
obvious that processing is paused. When the worker is alive, nothing changes:
the happy path is byte-identical.

## Freshness Check

**Baseline commit:** 6891ceb5ee30db2795743371587f2a940934efb6
**Issue filed at:** 2026-05-06T10:41:51Z (last updated 2026-06-24T06:10:29Z)
**Disposition:** Minor drift + Overlap (non-blocking)

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1005` (handler entry) — still the live-handler region; enqueue call sites **drifted** from the issue's `1665, 2193, 2337` to **`1954, 2475, 2648`**. Function names unchanged.
- `dispatch_telegram_session` — **moved**: no longer defined inline in `telegram_bridge.py`; it now lives in **`bridge/dispatch.py:84`** and is imported at `telegram_bridge.py:104`. It does **not** receive the Telethon `client`, so the reaction cannot be set from inside it — the wrap must be at the call sites where `client`/`event` are in scope. This is the key correction to the issue's "wrap inside dispatch" sketch.
- `bridge/response.py:258-321` (`set_reaction`) — **drifted** to `bridge/response.py:315`; still present and used 10+ places.
- `agent/session_health.py:1647-1664` (`register_worker_pid`) — **drifted** to `agent/session_health.py:4092`; still writes `worker:registered_pid:{hostname}:{pid}` with a **24h** TTL (`WORKER_REGISTERED_PID_TTL_SECONDS = 86400`).

**Cited sibling issues/PRs re-checked:**
- #871, #495 — still closed; background context only, no landscape shift.

**Commits on main since issue was filed (touching referenced files):** many (the
bridge and `session_health.py` are actively developed). The relevant *new*
infrastructure since filing is the wall-clock loop beacon (`worker:loop_beacon:{host}`,
issue #1712/#1821) — see Research/Spike below. The problem this issue describes is
**still present**: `grep` confirms no worker-liveness gate exists on any
`dispatch_telegram_session` call site.

**Active plans in `docs/plans/` overlapping this area:**
`resilience-simplification-three-tier.md` (status: **draft**, tracking: none) names
#1312 under item **T3.3** ("worker leases"): "#1312 becomes trivially detectable
once worker leases exist." That is a larger future lease architecture; the draft
explicitly states "each tier item ships as its own issue/plan/PR through the normal
SDLC pipeline." **Non-blocking:** this plan ships a narrow beacon-based fix *now*
using infrastructure that already exists, independent of the lease work. When
leases land, the liveness helper can be repointed at them without touching the
bridge call sites.

**Notes:** The corrected call-site line numbers (1954/2475/2648) and the
`dispatch.py` relocation are carried into Technical Approach below. The bug is
reproducible by reading the code path: no liveness check exists between message
ingestion and enqueue.

## Prior Art

- **#871** (closed): session-recovery coverage split between worker and
  bridge-watchdog was undocumented/misleading. Related but broader — this issue
  is only about *ingestion-time* liveness signalling, not recovery.
- **#495** (closed): bridge resilience / graceful degradation for dependency
  outages. Established the "degrade visibly, don't fail silently" posture this
  fix extends to the worker-liveness case.
- **#1712 / #1821** (shipped): the update-loop wedged detector and the wall-clock
  loop beacon (`worker:loop_beacon:{host}`). This is the infrastructure the fix
  reuses — the bridge already reads this beacon in
  `monitoring/session_watchdog.py::check_worker_liveness_and_slots()`. No prior
  attempt tried to gate *ingestion* on it.

No prior attempt fixed the ingestion-time silent-rot; nothing to learn from a
failed fix (this section's "Why Previous Fixes Failed" is therefore omitted).

## Research

**Queries used:** none external — this is a purely internal bridge/worker change
using existing Redis infrastructure. No new libraries, APIs, or ecosystem patterns.

**Key findings (from codebase recon):**
- The worker publishes a **wall-clock** liveness beacon at
  `worker:loop_beacon:{host}` via `agent/session_health.py::_publish_loop_beacon()`
  on every heartbeat tick (`WORKER_HEARTBEAT_INTERVAL = 30s`). The payload is
  `{"wall_ts": time.time(), "loop_beacon_age_s": <monotonic advisory>, "armed": bool}`.
  Key TTL is `WORKER_LOOP_BEACON_TTL_SECONDS = 3 * WORKER_HEARTBEAT_INTERVAL = 90s`.
- The bridge process **already consumes** this beacon:
  `monitoring/session_watchdog.py::check_worker_liveness_and_slots()` reads the
  same key and keys freshness **only** on `wall_ts` (never the advisory monotonic
  age — "Risk 1"), with staleness threshold
  `BRIDGE_WORKER_BEACON_STALE_S = 90s` (env-overridable).
- This is the correct signal per the issue's investigation-task-1: the 24h
  `worker:registered_pid:*` TTL is too coarse; the beacon is the purpose-built,
  cross-process, sub-90s liveness signal. **No new heartbeat scheme is needed.**

No relevant external findings — proceeding with codebase context.

### Signal-Choice Reconciliation (issue directive vs. beacon)

The tracking issue's re-scope comment (2026-06-24, comment `4786392177`) explicitly
directs: *"add `_worker_alive_for()` reusing `WORKER_DOWN_THRESHOLD_S` + the
heartbeat file (do NOT invent a new threshold)."* That directive points at the
**file-based** signal `data/last_worker_connected` (`agent/constants.py::WORKER_DOWN_THRESHOLD_S = 600`,
resolved via `tools/valor_session.py::_resolve_heartbeat_path`). This plan
deliberately selects a **different existing signal** — the Redis loop beacon
`worker:loop_beacon:{host}` — and this subsection reconciles that divergence so a
consistency reviewer does not read it as an unexplained override.

| Dimension | Heartbeat file (issue directive) | Loop beacon (this plan) |
|-----------|----------------------------------|-------------------------|
| Transport | File on disk (`data/last_worker_connected`) | Redis key `worker:loop_beacon:{host}` |
| Write cadence | 300s | 30s (`WORKER_HEARTBEAT_INTERVAL`) |
| Down threshold | 600s (`WORKER_DOWN_THRESHOLD_S`) | 90s (`BRIDGE_WORKER_BEACON_STALE_S`) |
| Worktree hazard | **Yes** — only under the MAIN checkout; needs `_resolve_heartbeat_path` | **No** — Redis is process-global |
| Already read cross-process by the bridge? | No (CLI-side only) | **Yes** — `session_watchdog.check_worker_liveness_and_slots` |
| Cost on the ingestion hot path | File stat + read | Single Redis GET (≤5ms AC) |

**Why the beacon wins for *this* ingestion-path check** (all four are load-bearing):
1. **No worktree-path hazard.** The heartbeat file exists only under the MAIN
   checkout; a bridge running from a worktree would resolve the wrong path unless
   it re-implements `_resolve_heartbeat_path`. The beacon is a process-global Redis
   key with no such trap.
2. **Fresher detection** — 90s vs 600s. For a check whose whole purpose is *fast*
   feedback in the outage window, a 6.7× tighter window is the point.
3. **Already cross-process consumed by the bridge process** — reusing the beacon
   means one liveness definition the bridge already trusts, not a second signal.
4. **It does NOT "invent a new threshold."** `BRIDGE_WORKER_BEACON_STALE_S`
   (default 90, env-overridable) already ships in `monitoring/session_watchdog.py`
   and is used in production today. The issue's prohibition was against *minting a
   new heartbeat scheme*; reusing an already-deployed constant honors that intent
   while picking the better-suited of the two existing signals.

**Bottom line:** the issue directive predates the loop beacon becoming the bridge's
primary liveness read; the beacon satisfies the directive's *intent* (reuse
existing infra, no new threshold) better than the literal file it named. This
plan does not fabricate a signal — it selects the fresher of two that already
exist. (`last_comment_id` frontmatter now records `4786392177` to close the
Phase 2.7 comment-sync gap.)

## Spike Results

### spike-1: What is the correct worker-liveness signal, and at what granularity?
- **Assumption**: "Liveness must be checked per `project_key`, via a helper like `_worker_alive_for(project_key)`" (the issue's framing).
- **Method**: code-read.
- **Finding**: Liveness is **per-worker-process (per-host)**, NOT per-project. The
  worker is one process per machine that internally spawns one queue-worker loop
  *per project* (`worker/__main__.py:874` "one per project's known chat_ids"). If
  the worker process is dead, **every** project loop is dead simultaneously. There
  is exactly one loop beacon per host (`worker:loop_beacon:{host}`), not one per
  project. Combined with strict single-machine ownership (the bridge only receives
  messages for projects *this* machine owns), the correct question is: "is *this
  machine's* worker process alive?" — a single host-scoped Redis GET.
- **Confidence**: high.
- **Impact on plan**: The helper is `worker_loop_beacon_fresh(host=None) -> bool`
  (host-scoped), **not** `_worker_alive_for(project_key)`. This corrects the issue's
  sketch and makes the check a single Redis GET (satisfies the ≤5ms AC trivially).

### spike-2: Is there already a beacon-freshness reader to reuse?
- **Assumption**: "The freshness read must be written from scratch."
- **Method**: code-read.
- **Finding**: `session_watchdog.check_worker_liveness_and_slots()` already contains
  the exact read-and-freshness logic (read `worker:loop_beacon:{host}`, parse JSON,
  compare `now - wall_ts` against a stale threshold, fail-quiet on malformed/missing).
  It is inlined inside a larger slot-reclaim function.
- **Confidence**: high.
- **Impact on plan**: Extract a small pure helper `worker_loop_beacon_fresh()` into
  `agent/session_health.py` (co-located with the beacon *publisher* and its
  constants), then have both the watchdog and the new bridge helper call it. DRY;
  one definition of "worker alive."

## Data Flow

1. **Entry point**: Telegram message hits the live handler in
   `bridge/telegram_bridge.py` (handler region from ~line 1005).
2. **Classification / routing**: existing logic decides the dispatch branch and
   sets the initial 👀 reaction (`REACTION_RECEIVED`).
3. **New gate (this fix)**: immediately before each `dispatch_telegram_session(...)`
   call (sites `1954`, `2475`, `2648`), the handler calls
   `await react_if_worker_down(client, chat_id, message_id)`.
   - That helper calls `worker_loop_beacon_fresh()` → one Redis GET of
     `worker:loop_beacon:{host}` → fresh/stale decision.
   - If **not** fresh (missing key, stale `wall_ts`, or malformed): `set_reaction(...,
     REACTION_WORKER_DOWN)` overwrites the message reaction with ⚠. If fresh: no-op.
4. **Enqueue (unchanged)**: `dispatch_telegram_session(...)` runs regardless — the
   message is always enqueued. The reaction is purely additive signalling.
5. **Output**: user sees ⚠ on their message within the reaction round-trip
   (single Redis GET + one `SendReactionRequest`).

## Architectural Impact

- **New dependencies**: none. Reuses `set_reaction`, the existing Redis client,
  and the existing loop-beacon.
- **Interface changes**: one new public helper in `agent/session_health.py`
  (`worker_loop_beacon_fresh`); one new bridge helper in `bridge/response.py`
  (`react_if_worker_down`); one new constant (`REACTION_WORKER_DOWN`). No changes
  to `dispatch_telegram_session`'s signature.
- **Coupling**: slightly *reduces* duplication — `session_watchdog` stops carrying
  its own inline beacon-freshness read (and its own `BRIDGE_WORKER_BEACON_STALE_S`
  read) and calls the shared helper. This adds one new import edge
  (`monitoring/session_watchdog.py → agent.session_health`); verified acyclic at
  plan time (`session_health.py` imports nothing from `monitoring/`).
- **Data ownership**: unchanged. The worker still solely owns beacon publication;
  the bridge is a read-only consumer.
- **Reversibility**: trivial. Delete the helper call at the three sites and the two
  helpers; no schema, no migration, no persisted state.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0-1 (confirm the detection-window tradeoff in Open Questions)
- Review rounds: 1

The whole change is one shared liveness helper + one bridge helper + one constant
+ three one-line call-site wraps. If the diff approaches 100 lines the design has
drifted — push back.

## Prerequisites

No prerequisites — this work has no external dependencies. It reads an existing
Redis key and uses the existing Telethon client.

## Solution

### Key Elements

- **`worker_loop_beacon_fresh(host=None) -> bool`** (in `agent/session_health.py`):
  a pure, fail-quiet reader that returns `True` iff the host's loop beacon exists
  and its `wall_ts` is within `BRIDGE_WORKER_BEACON_STALE_S`. Returns `False` on a
  missing/expired key, stale `wall_ts`, malformed JSON, or any Redis error
  (fail-closed toward "warn the user" — a Redis outage is itself a degraded state
  worth signalling).
- **`REACTION_WORKER_DOWN = "⚠"`** (in `bridge/response.py`, alongside the existing
  `REACTION_RECEIVED` / `REACTION_PROCESSING` / `REACTION_ABORT` constants):
  visually distinct from every reaction the bridge already uses (👀 ✍ 🫡 🤔 👀-suppress).
- **`react_if_worker_down(client, chat_id, message_id)`** (in `bridge/response.py`):
  calls `worker_loop_beacon_fresh()`; if not fresh, sets `REACTION_WORKER_DOWN`;
  fully fail-quiet (never raises into the handler).
- **Three call-site wraps** in `bridge/telegram_bridge.py` (sites 1954, 2475, 2648):
  `await react_if_worker_down(...)` immediately before `dispatch_telegram_session(...)`.

### Flow

Message arrives → handler sets 👀 → **worker beacon fresh?** →
 - **yes** → (no change) → enqueue → user waits normally
 - **no** → overwrite reaction with ⚠ → enqueue anyway → user sees "paused, not lost"

### Technical Approach

- **Extract, don't duplicate.** Pull the beacon read + `wall_ts` freshness compare
  out of `session_watchdog.check_worker_liveness_and_slots()` into
  `worker_loop_beacon_fresh(host=None)`. Repoint the watchdog at the new helper so
  there is exactly one freshness definition. Keep the watchdog's *recovery* logic
  (loop-wedged recording, slot reclaim) where it is — only the boolean read moves.
- **Move the staleness constant with the read.** `BRIDGE_WORKER_BEACON_STALE_S`
  currently lives ONLY in `monitoring/session_watchdog.py:143` (a module-level
  `os.environ.get("BRIDGE_WORKER_BEACON_STALE_S", "90")`), NOT in
  `config/settings.py`. When `worker_loop_beacon_fresh` moves to
  `agent/session_health.py`, the constant must move there too (co-located with the
  beacon publisher `_publish_loop_beacon` and `WORKER_LOOP_BEACON_KEY_PREFIX`,
  which `session_health.py` already defines at line 216). `session_watchdog.py`
  then imports the single definition — do NOT leave two `os.environ.get` reads of
  the same env var in two modules (that is exactly the duplication this extraction
  removes). The env-var name and default (90) are unchanged, so no deploy/config
  change is required.
- **Import direction is safe (no circular import).** The extraction adds a new
  `monitoring/session_watchdog.py → agent.session_health` import. Verified safe:
  `agent/session_health.py` does not import from `monitoring/` (grep-confirmed at
  plan time), so no import cycle is introduced. The build must re-confirm this
  after the move (add it to the Verification table).
- **Behavior-preserving extraction is a hard requirement.** `session_watchdog` is a
  recovery-critical path; the extracted `worker_loop_beacon_fresh` must produce
  byte-identical fresh/stale/missing/malformed decisions to today's inlined read.
  `tests/unit/test_session_watchdog.py` (UPDATE) must assert the watchdog's
  observable behavior is unchanged across the refactor, not merely that the new
  helper exists.
- **Freshness rule is `wall_ts`-only** (Risk 1): never use the advisory monotonic
  `loop_beacon_age_s` for cross-process math. A missing key ⇒ not fresh.
- **`armed` handling:** treat an unarmed-but-fresh beacon (worker up, loop not yet
  ticked) as **alive** — the worker exists and will drain shortly; warning would be
  a false positive during the startup window. Only missing/stale ⇒ warn.
- **Call-site placement** is the correctness surface: the wrap must run *before*
  `dispatch_telegram_session`, and enqueue must proceed unconditionally afterward.
  Verify `client` + chat_id + message_id are in scope at each of 1954/2475/2648
  (site 2475 uses `telegram_chat_id`; confirm the client handle name at that site
  during build).
- **Fail-closed on Redis errors:** if the beacon read raises, return `False`
  (warn). Rationale: a bridge that can't read Redis is itself degraded; a spurious
  ⚠ is strictly safer than a false "all good."

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `worker_loop_beacon_fresh` wraps its Redis read in try/except and returns
  `False` on error — add a unit test asserting `False` when the Redis client raises,
  and when the beacon JSON is malformed.
- [ ] `react_if_worker_down` swallows `set_reaction` failures (non-fatal, matches the
  existing `set_reaction failed (non-fatal)` pattern) — assert a raising `set_reaction`
  does not propagate and the handler still reaches enqueue.

### Empty/Invalid Input Handling
- [ ] Beacon key missing (None) → `worker_loop_beacon_fresh` returns `False`.
- [ ] Beacon present but `wall_ts` absent / non-numeric → returns `False`.
- [ ] Beacon present, `armed=False`, fresh `wall_ts` → returns `True` (startup grace).
- [ ] This feature produces a reaction, not agent output — no empty-output loop risk.

### Error State Rendering
- [ ] The user-visible error signal *is* the ⚠ reaction; test asserts it is applied
  on the not-alive path and **not** applied on the alive path (no reaction churn on
  the happy path).
- [ ] Assert the message is enqueued on both paths (worker-down must never drop work).

## Test Impact

- [ ] `tests/unit/test_session_watchdog.py` — UPDATE: `check_worker_liveness_and_slots`
  now delegates its freshness read to `worker_loop_beacon_fresh`. Existing beacon
  fresh/stale/missing/malformed cases must still pass through the refactor; update any
  test that patched the inlined read to patch/observe the extracted helper instead.
- [ ] `tests/unit/test_worker_liveness_beacon_publish.py` — UPDATE (if it asserts read
  behavior): confirm the extracted helper reads the same key/field; likely additive only.
- [ ] New `tests/unit/test_bridge_worker_liveness_reaction.py` — CREATE: unit tests for
  `worker_loop_beacon_fresh` (fresh / stale / missing / malformed / redis-error / unarmed)
  and `react_if_worker_down` (reacts on down, no-op on alive, fail-quiet on set_reaction error).
- [ ] New integration coverage — CREATE: assert a message enqueues on both worker-alive
  and worker-down paths (no dropped work), and that ⚠ is applied only on the down path.

No other existing tests exercise a worker-liveness gate at ingestion (grep confirms
none exists today), so no deletions or rewrites of unrelated suites are required.

## Rabbit Holes

- **Clearing the ⚠ reaction when the worker recovers.** The issue lists this as a
  stretch goal. It requires the worker (or watchdog) to track which messages got a
  warning and reach back to un-react. Out of scope for v1 — a follow-up message from
  the user gets a normal reaction once the worker is back. Do NOT build reaction
  reconciliation here.
- **Per-project liveness.** Tempting to mirror the issue's `project_key` framing,
  but spike-1 proved liveness is per-process. Do not introduce a per-project loop
  registry — it does not exist and would be a fabricated signal.
- **Tightening the detection window below 90s.** Would mean a new, faster heartbeat
  scheme — explicitly forbidden by the issue and by the "no new heartbeat" AC. The
  90s window is an accepted tradeoff (see Risk 1 / Open Questions).
- **Posting a text reply on no-worker.** The issue dropped this deliberately — a
  reaction is unobtrusive; text would spam every queued message during a brief restart.

## Risks

### Risk 1: Detection window — up to 90s of "looks fine" after the worker dies
**Impact:** The beacon is refreshed every 30s and considered fresh for
`BRIDGE_WORKER_BEACON_STALE_S = 90s`. In the ~90s immediately after the worker
stops (before the beacon key expires / `wall_ts` ages out), a message would still
get the normal 👀, not ⚠. The acceptance-criteria "observe within ≤2 seconds" refers
to *reaction latency once the beacon reads not-alive* (a single Redis GET +
SendReaction), not to worker-death detection latency.
**Mitigation:** For the multi-hour outage this issue targets, 90s is negligible —
the warning fires reliably. Document the window explicitly. The manual test must
either wait >90s after stopping the worker, or delete the `worker:loop_beacon:{host}`
key to simulate an already-dead worker instantly (see Success Criteria). Open
Questions asks whether a graceful-shutdown beacon-clear (instant detection on clean
stops) is worth a tiny addition.

### Risk 2: Fail-closed spurious warnings during a Redis blip
**Impact:** If the beacon read raises (transient Redis error), the helper returns
`False` and the user sees ⚠ even though the worker may be fine.
**Mitigation:** Intentional. A bridge that cannot read Redis is itself degraded;
over-warning is strictly safer than a silent-rot false negative. The condition is
transient and self-corrects on the next message. Logged at debug for observability.

## Race Conditions

### Race 1: Beacon read races the worker's 30s publish tick
**Location:** `bridge/telegram_bridge.py` call sites ↔ `_publish_loop_beacon()`.
**Trigger:** A message arrives in the instant between two beacon publishes.
**Data prerequisite:** The beacon key holds the last-published `wall_ts`.
**State prerequisite:** None — the read is a point-in-time snapshot.
**Mitigation:** No true race. The 90s freshness window is deliberately ≥3× the 30s
publish interval, so a single missed/late tick never flips a live worker to "down."
The read is idempotent and side-effect-free; concurrent reads from the watchdog and
the handler cannot interfere.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2178] Reaction *reconciliation* — clearing ⚠ when the worker
  recovers — is filed as issue #2178. It needs per-message warning tracking plus a
  worker→bridge reach-back path, which is beyond this Small appetite.
- [FOLLOW-UP] Graceful-shutdown beacon-clear — the worker deleting
  `worker:loop_beacon:{host}` on a clean stop so clean restarts signal ⚠ instantly
  (instead of waiting out the 90s staleness window). It touches the worker shutdown
  path, not the bridge ingestion path this plan owns; the 90s window is acceptable
  for v1 (the outage case this issue targets is a crash, already covered by
  staleness). File as its own issue if desired.

Every other relevant item is in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the
bridge/worker processes. No new dependencies, config files, or migration steps.
The `BRIDGE_WORKER_BEACON_STALE_S` env var already ships (read today in
`monitoring/session_watchdog.py`); this plan *relocates its single definition*
into `agent/session_health.py` beside the beacon publisher but keeps the env-var
name and default (90) byte-identical, so no `.env.example`, `config/settings.py`,
or deploy change is needed. The `REACTION_WORKER_DOWN` constant and helpers ship
with the normal code deploy; `./scripts/valor-service.sh restart` picks them up
like any bridge change.

## Agent Integration

No agent integration required — this is a bridge-internal change. It adds no MCP
tool and no CLI entry point; it operates entirely inside the message-ingestion path
the bridge already owns. The only "surface" is the ⚠ reaction the user sees in
Telegram. `bridge/telegram_bridge.py` calls the new `bridge/response.py` helper
directly (an internal import, matching the existing `set_reaction` usage) — no
`.mcp.json` change.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-resilience.md` with a "Worker-liveness ingestion
  signal" section: the ⚠ reaction, the `worker_loop_beacon_fresh` helper, the 90s
  detection window, and the fail-closed rationale.
- [ ] Cross-reference from `docs/features/worker-liveness-recovery.md` (this is the
  *ingestion-time signalling* companion to the recovery machinery documented there).
- [ ] Confirm `docs/features/README.md` index still resolves (no new file created;
  updating existing docs).

### Inline Documentation
- [ ] Docstring on `worker_loop_beacon_fresh` stating the `wall_ts`-only rule,
  fail-closed semantics, and the unarmed-is-alive grace.
- [ ] Comment at each of the three call sites noting the wrap must precede enqueue
  and enqueue is unconditional.

## Success Criteria

- [ ] When the worker is not alive for this machine, the bridge applies ⚠ to the
  originating Telegram message before enqueueing the session.
- [ ] When the worker IS alive, no extra reaction is added (happy path byte-identical).
- [ ] The liveness check is a single Redis GET (`worker:loop_beacon:{host}`), adding
  ≤5ms to the ingestion path.
- [ ] The liveness signal is sourced from the existing loop beacon — no new heartbeat
  scheme is introduced (`grep` confirms no new `*heartbeat*`/`*beacon*` publisher).
- [ ] `worker_loop_beacon_fresh` is the single freshness definition; `session_watchdog`
  delegates to it (grep confirms the inlined read is gone).
- [ ] The message is enqueued on both worker-alive and worker-down paths (no dropped work).
- [ ] Manual test (down): stop the worker AND delete `worker:loop_beacon:{host}` (or wait
  >90s), send a message in a project-tagged chat, observe ⚠ within ≤2s of the beacon
  reading not-alive.
- [ ] Manual test (up): with the worker running, send a message, observe no ⚠.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (liveness-helper)**
  - Name: liveness-builder
  - Role: Extract `worker_loop_beacon_fresh` into `agent/session_health.py`, repoint
    `session_watchdog`, add `REACTION_WORKER_DOWN` + `react_if_worker_down`, wire the
    three call sites.
  - Agent Type: builder
  - Domain: async/concurrency (cross-process Redis read on the ingestion hot path)
  - Resume: true

- **Validator (liveness)**
  - Name: liveness-validator
  - Role: Verify the extraction is behavior-preserving, the three call sites wrap
    before enqueue, enqueue is unconditional, and all Success Criteria hold.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extract shared liveness helper
- **Task ID**: build-liveness-helper
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_worker_liveness_reaction.py (create), tests/unit/test_session_watchdog.py
- **Informed By**: spike-1 (per-host, not per-project), spike-2 (reuse the watchdog's read)
- **Assigned To**: liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `worker_loop_beacon_fresh(host=None) -> bool` to `agent/session_health.py`
  (co-located with `_publish_loop_beacon` and the beacon constants). `wall_ts`-only
  freshness against `BRIDGE_WORKER_BEACON_STALE_S`; fail-closed (`False`) on
  missing/malformed/redis-error; unarmed-but-fresh ⇒ `True`.
- Repoint `monitoring/session_watchdog.py::check_worker_liveness_and_slots` to call
  the new helper for its freshness read; keep recovery logic intact.

### 2. Add bridge reaction helper + constant
- **Task ID**: build-bridge-helper
- **Depends On**: build-liveness-helper
- **Validates**: tests/unit/test_bridge_worker_liveness_reaction.py
- **Assigned To**: liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `REACTION_WORKER_DOWN = "⚠"` beside the other `REACTION_*` constants in
  `bridge/response.py`.
- Add `async def react_if_worker_down(client, chat_id, message_id)` in
  `bridge/response.py`: call `worker_loop_beacon_fresh()`; on not-alive, `set_reaction(...
  REACTION_WORKER_DOWN)`; fully fail-quiet.

### 3. Wire the three ingestion call sites
- **Task ID**: build-callsites
- **Depends On**: build-bridge-helper
- **Validates**: integration test (create), grep-based Verification rows
- **Assigned To**: liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert `await react_if_worker_down(...)` immediately before `dispatch_telegram_session(...)`
  at `bridge/telegram_bridge.py` sites ~1954, ~2475, ~2648. Confirm client/chat_id/msg_id
  scope at each (site 2475 uses `telegram_chat_id`). Enqueue proceeds unconditionally.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-callsites
- **Validates**: the new unit + integration files
- **Assigned To**: liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_bridge_worker_liveness_reaction.py` (helper matrix + reaction
  behavior). Add integration coverage asserting enqueue-on-both-paths and ⚠-only-on-down.
- Update `tests/unit/test_session_watchdog.py` for the extracted read.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-callsites
- **Assigned To**: liveness-validator (documentarian pass)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-resilience.md` and cross-reference
  `docs/features/worker-liveness-recovery.md`.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: liveness-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm every Success Criterion; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_bridge_worker_liveness_reaction.py tests/unit/test_session_watchdog.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Helper exists | `grep -c "def worker_loop_beacon_fresh" agent/session_health.py` | output > 0 |
| Constant added | `grep -c "REACTION_WORKER_DOWN" bridge/response.py` | output > 0 |
| Call sites wired | `grep -c "react_if_worker_down" bridge/telegram_bridge.py` | output > 0 |
| Watchdog delegates (no duplicate read) | `grep -c "worker_loop_beacon_fresh" monitoring/session_watchdog.py` | output > 0 |
| Constant single-sourced | `grep -c "BRIDGE_WORKER_BEACON_STALE_S = " agent/session_health.py` | output == 1 |
| No duplicate constant left behind | `grep -c "os.environ.get(\"BRIDGE_WORKER_BEACON_STALE_S\"" monitoring/session_watchdog.py` | output == 0 |
| No circular import introduced | `python -c "import monitoring.session_watchdog, agent.session_health"` | exit code 0 |
| No new heartbeat scheme | `grep -rn "def _publish\|def _write_worker_heartbeat" bridge/` | match count == 0 |

## Critique Results

Revision pass (2026-07-20) — CRITIQUE returned NEEDS REVISION. The war-room result
files were not persisted (run dir cleaned on the `complete:true` path, table left
empty), so this revision re-derives the material findings from the issue + comments
+ codebase and addresses each. Findings and resolutions:

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| HIGH | History & Consistency | Plan silently overrides the tracking issue's explicit re-scope directive (comment `4786392177`: reuse `WORKER_DOWN_THRESHOLD_S` + `data/last_worker_connected`, "do NOT invent a new threshold") by selecting the loop beacon, with no reconciliation. `last_comment_id` was empty — Phase 2.7 comment-sync was skipped. | New **Signal-Choice Reconciliation** subsection in Research; `last_comment_id: 4786392177` set in frontmatter. | Beacon justified over the file signal on 4 load-bearing axes (no worktree-path hazard, 90s vs 600s, already cross-process consumed, does not mint a new threshold). |
| MEDIUM | Scope & Value | The DRY extraction touches recovery-critical `session_watchdog` and moves a beacon read out of a proven path, but the plan under-specified where `BRIDGE_WORKER_BEACON_STALE_S` lives after the move and did not require behavior-preservation. | Technical Approach: "Move the staleness constant with the read" + "Behavior-preserving extraction is a hard requirement" bullets; new Verification rows for single-sourced constant. | Constant currently lives only in `session_watchdog.py:143`; must relocate to `session_health.py` and be imported, not duplicated. |
| MEDIUM | Risk & Robustness | New import edge `monitoring/session_watchdog.py → agent.session_health` not analyzed for cycles. | Technical Approach import-direction bullet + Architectural Impact coupling note + `No circular import` Verification row. | Verified acyclic at plan time; build re-confirms via import smoke test. |
| LOW | Scope & Value | Two Open Questions blocked build with choices that are safely defaultable for a Small v1. | Resolved below (emoji = ⚠; 90s window accepted for v1; graceful-shutdown clear filed as follow-up). | Neither question requires human input to ship v1. |

---

## Open Questions

_Resolved in the 2026-07-20 revision pass — no blockers remain for v1 build:_

1. **Detection window on graceful shutdown — RESOLVED (accept 90s for v1).** The
   beacon-only approach means up to ~90s of "looks fine" after the worker stops.
   For the multi-hour outage this issue targets, that is negligible, and the real
   outage case (crash) is covered by beacon staleness. A graceful-shutdown
   beacon-clear (worker deletes `worker:loop_beacon:{host}` on clean stop → instant
   ⚠ on the next message) is a worthwhile *follow-up* but is out of scope for this
   Small appetite — it touches the worker shutdown path, not the bridge ingestion
   path this plan owns. Noted in No-Gos.
2. **Emoji choice — RESOLVED (⚠, U+26A0).** Distinct from every reaction the bridge
   uses today (👀 ✍ 🫡 🤔). The build step must confirm Telegram accepts ⚠ as a
   reaction in the target chats during the manual test; if a specific chat rejects
   it, fall back to 🚧 (the issue's floated alternative) — a one-constant change
   with no structural impact.
