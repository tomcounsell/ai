---
status: Completed
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1821
last_comment_id:
revision_applied: true
---

# Out-of-Domain Recovery + Per-Tool Budget Backstop (wedge fixes #5/#6)

## Problem

Two detection/backstop items were deferred from the liveness-wedge landing
(#1815, `docs/plans/completed/liveness-wedge-recovery.md`). Both address the
same structural flaw from a different angle: **the actors that recover a wedged
worker either run on the very loop they police, or fire only from a background
monitor that a frozen loop also stops running.**

**Fix #5 — recovery runs inside the failure domain it polices.** The slot-leak
reaper introduced by #1820 (`_agent_session_health_check`, the hoisted
top-of-tick reap pass) reclaims a leaked concurrency permit by calling
`registry.reclaim()` — but it runs **on the worker event loop** (the reap pass
`_reap_slot_leases()` at `agent/session_health.py:2485`, driven by
`_agent_session_health_loop` at `:3405`). When the loop is
synchronously frozen, the reaper task never runs, so the very recovery meant to
liberate a leaked slot is itself wedged. The acceptance criterion from #1815
demands that recovery be **verified to run from a process OTHER than the worker
loop it polices**. Today only two out-of-domain actors exist —
`monitoring/worker_watchdog.py` (a separate launchd process that kills+respawns a
dead/stale-heartbeat worker) and the off-loop dead-man's-switch thread inside the
worker (`worker/__main__.py:357` `_heartbeat_thread_main`, `_self_kill()` SIGKILL
self-recycle on a stale beacon — the former SIGABRT was replaced by SIGKILL per
#1808/#1816 to suppress the macOS crash-report dialog). Both
recover only by **process restart**, which is lossy: it re-queues all in-flight
work. Neither can perform the **lossless, targeted slot reclamation** that the
#1820 registry makes possible, and neither surfaces the slot-lease state to an
operator. There is no bridge-domain actor that reads worker liveness + lease
state and drives a targeted, restart-free recovery.

**Fix #6 — a per-tool budget that fires even when the health loop is frozen.**
The only per-tool-call budget today is `_agent_session_tool_timeout_loop`
(`agent/session_health.py`, `TOOL_TIMEOUT_LOOP_INTERVAL=30` at `:336`, function
`_agent_session_tool_timeout_loop` at `:3835`) — a
**background monitor** on the worker loop. When the loop freezes, it stops
ticking, so a runaway session that keeps issuing tool calls (or racking up cost)
against a partially-wedged harness has no ceiling. There is no **synchronous,
in-execution-path** budget: a check that runs at the point each tool call is
dispatched, denying the call inline (omnigent's `enforcement.py` model —
ALLOW/DENY, explicitly NOT a background monitor) so it fires independent of any
loop's health.

**Current behavior:**
1. A leaked concurrency slot can only be reclaimed by the on-loop reaper (which a
   frozen loop stops running) or by a lossy process restart; no out-of-domain
   actor performs targeted, restart-free reclamation, and the slot-lease state is
   not visible to any operator surface.
2. A session's per-tool spend is bounded only by a background monitor that a
   frozen loop halts; there is no inline per-call ceiling.

**Desired outcome:**
1. Fix #5 is PRIMARILY a **detection + operator-surface** capability: a bridge-process
   actor reads the worker's Redis-published loop beacon + lease snapshot, records
   `loop_wedged` when the loop is stale, surfaces the slot-lease/liveness state on the
   dashboard, and DEFERS all process recovery to the single existing killer (it never
   runs a second kill ladder). The SECONDARY, narrow lever is a Redis-mediated
   reclaim-request: for a terminal-owner lease held under a *live* loop, the bridge
   pushes a reclaim-request drained by the worker's on-loop reaper — a restart-free,
   targeted reclamation. This request path is deliberately narrow (see the resolved
   OQ2 below): when the loop is alive #1820's autonomous reaper already reclaims
   terminal owners, so the request's *unique* value is (a) it is the reclaim path that
   still fires under `SLOT_LEASE_REAP_DISABLED=1` (where the autonomous action is gated
   off), and (b) it is what makes Acceptance #1 — "recovery runs from a non-worker
   process" — provable. Being transparent: `registry.reclaim()` itself always runs on
   the worker loop (loop-affinity physics); only the *trigger* crosses the process
   boundary.
2. A per-tool-call budget (`MAX_TOOL_CALLS_PER_SESSION`, session cost cap) is
   enforced **synchronously in the PreToolUse dispatch path**, so it denies the
   call inline even when every background loop is frozen.

## Freshness Check

**Baseline commit (revision 5):** `2d1cf419` (true HEAD as of 2026-07-03; the prior
revision-4 baseline `bdb77c10` was superseded when **PR #1870** (`705136e7`,
"atomic per-message + pending→running claims") merged, adding +149 lines to
`agent/session_health.py` and shifting its anchors non-uniformly).
**Issue filed at:** 2026-06-29 (deferred from #1815)
**Disposition:** Minor drift — line numbers moved under the #1820 merge and then
again under PR #1870; all claims still hold; **every** `agent/session_health.py` /
`.claude/hooks/pre_tool_use.py` / `monitoring/session_watchdog.py` / `ui/app.py`
anchor was re-grepped against HEAD `2d1cf419` and corrected below.
**Revision 5 re-check (post-#1870-merge, 2026-07-03).** PR #1870 shifted the lower
half of `agent/session_health.py` by +149 lines and moved the reap pass by +26;
corrected anchors: `_reap_slot_leases()` `:2459`→`:2485` (called from
`_agent_session_health_check` at `:2679`), Phase-1 detection ends `:2574`, the
`if reap_disabled: return` early-gate is `:2577-2578`, the `SLOT_LEASE_REAP_DISABLED`
read `:2533`, the None-as-terminal reclaim branch `:2585`, `_write_worker_heartbeat`
`:3237`→`:3386`, `register_worker_pid` `:3217`→`:3243`, `_agent_session_health_loop`
`:3256`→`:3405`, `_agent_session_tool_timeout_loop` `:3686`→`:3835`,
`TOOL_TIMEOUT_LOOP_INTERVAL` `:310`→`:336`. `ui/app.py::_get_worker_health` stayed at
`:370` (the `/dashboard.json` route moved to `:590`); `session_watchdog.py` +
`pre_tool_use.py` + `post_tool_use.py` anchors are unchanged (verified below).
**Revision 4 re-check (post-#1820-merge, 2026-07-03).** #1820 (the hard dependency)
merged 2026-07-02 (PR #1867, `72ba5d50`). All Fix #5 dependency surfaces re-verified
against the merged code at HEAD `2d1cf419` (see the ✅ HARD DEPENDENCY SATISFIED block
above): `SlotLeaseRegistry.{leases,permits_free,reclaim}` present in
`agent/slot_lease.py`, the reap pass is the named `_reap_slot_leases()`
(`agent/session_health.py:2485`), and `SLOT_LEASE_REAP_DISABLED=1` gates only the
autonomous Phase-2 reclaim — confirming the bridge drain is the sole reclaim lever
under that flag. `scripts/check_prerequisites.py` reports all 4 prerequisites PASS.
**Fix #6 anchors re-verified against HEAD `2d1cf419` (line numbers corrected):**
- `monitoring/session_watchdog.py` — `watchdog_loop` at `:173`; `_apply_stall_reaction`
  at `:531` (the deny-surfacing precedent Fix #6 mirrors — atomic `SET NX EX` dedup +
  reaction-queue write). Confirmed, no drift.
- `agent/hooks/pre_tool_use.py` — `pre_tool_use_hook` at `:371`. Confirmed, no drift.
- `.claude/hooks/pre_tool_use.py` — `main()` at `:194`, wrapped by a module-level
  `except Exception` at `:223-229` (`if __name__ == "__main__"` → `log_hook_error`
  → exit 0). Confirmed this catches `Exception` (not `BaseException`), so a
  `SystemExit(2)` deny propagates while a check-internal bug fails open (concern #6
  grounding).
- `.claude/hooks/post_tool_use.py` — sidecar resolve at `:474`
  (`_load_agent_session_sidecar`, defined at `:69`), `AgentSession.get_by_id` at
  `:486`, `tool_call_count` bump at `:503` (the exact no-session-vs-infra path Fix #6
  reuses, concern #5). Confirmed.

**✅ HARD DEPENDENCY SATISFIED — #1820 lease registry MERGED (PR #1867,
2026-07-02, merge commit `72ba5d50`; plan archived at
`docs/plans/completed/slot-lease-progress-deadline.md`).** Fix #5 consumes the
`SlotLeaseRegistry` #1820 introduced in `agent/slot_lease.py` (replacing the
ownerless `_global_session_semaphore`; the registry singleton is
`agent/session_state.py:88` `_slot_registry`). **API re-verified against the
merged code (HEAD `2d1cf419`):** `SlotLeaseRegistry.leases() -> list[Lease]`
(`agent/slot_lease.py:186`), `permits_free() -> int` (`:190`, reads
`_semaphore._value`), and `reclaim(owner_session_id)` (`:166`, idempotent,
WARNING-logged) all exist exactly as this plan assumed. `Lease` (`:74`) carries
`owner_session_id` + `acquired_at` (a **wall-clock** `time.time()` value, `:147`)
— so the lease-snapshot JSON's `acquired_at_wall_ts` maps straight onto
`Lease.acquired_at` with no conversion. The on-loop reap pass is the named
function `_reap_slot_leases()` (`agent/session_health.py:2485`, called from
`_agent_session_health_check` at `:2679`); its autonomous Phase-2 terminal-owner
reclaim is gated on `os.environ.get("SLOT_LEASE_REAP_DISABLED") != "1"` (`:2533`,
`:2577`) while Phase-1 detection always runs — **confirming the plan's central
claim** that the bridge reclaim-request is the *only* reclaim lever under
`SLOT_LEASE_REAP_DISABLED=1`. **Fix #5 BUILD is therefore UNBLOCKED.** Fix #6 has
**no** dependency on #1820 and may build independently. See **## Prerequisites**
(all four now PASS).

**File:line references re-verified against HEAD `2d1cf419` (revision 4, post-#1820-merge):**
- `monitoring/session_watchdog.py` — `watchdog_loop` at `:173`; launched **in the
  bridge process** at `bridge/telegram_bridge.py:3053-3055` (`from
  monitoring.session_watchdog import watchdog_loop` → `asyncio.create_task`). Owns only
  session-level health today (silence/loop/error-cascade/token-alert steers,
  `_apply_stall_reaction`). No worker-loop-liveness or slot logic. Confirmed.
- `agent/session_state.py:96` — `last_loop_tick` is **`time.monotonic()`**, an
  in-worker-process module global; `get_loop_tick()` `:105`, `bump_loop_tick()`
  `:99`. **A monotonic clock is per-process — its raw value is meaningless in the
  bridge process. This is why Fix #5 cannot "read the beacon" directly and must
  publish a Redis wall-clock beacon** (Data Flow, Risk 1). Confirmed.
- `worker/__main__.py:256` — `_green_heartbeat_write` → `agent.session_health.
  _write_worker_heartbeat`; `_heartbeat_cycle` `:271` computes
  `beacon_age = now_monotonic - get_loop_tick()`; `_heartbeat_thread_main`
  `:357` runs off-loop every `WORKER_HEARTBEAT_INTERVAL=30s` (`:51`). Confirmed.
- `agent/session_health.py:3386` — `_write_worker_heartbeat()` writes
  `data/last_worker_connected` + calls `register_worker_pid()` (`:3243`, writes a
  Redis PID key) on every off-loop tick. **This is the publish seam for Fix #5's
  Redis wall-clock beacon.** The on-loop reap pass (from #1820, the named function
  `_reap_slot_leases()` at `agent/session_health.py:2485`, called from
  `_agent_session_health_check` at `:2679`) is the publish seam for the lease-table
  snapshot.
  Confirmed.
- `monitoring/worker_watchdog.py` — **existing** out-of-domain recovery (separate
  launchd, StartInterval 120s): `check()` `:158` reads `HEARTBEAT_FILE` (`:72`),
  `HEARTBEAT_THRESHOLD=180` (`:77`); `recover()` `:219` = SIGTERM→SIGKILL→bootout
  ladder; `_handle_missing_worker()` `:561` = kickstart ladder. **Already owns
  dead/stale-heartbeat process recovery** — Fix #5 must NOT duplicate this kill
  ladder. Confirmed.
- `agent/hooks/pre_tool_use.py:371` — SDK `pre_tool_use_hook`, blocks
  synchronously via `return {"decision":"block","reason":...}` (`:413,447,462`);
  registered at `agent/hooks/__init__.py:32`. Fix #6's SDK-path seam. Confirmed.
- `.claude/hooks/pre_tool_use.py` — CLI PreToolUse hook (the interactive `claude`
  TUI / granite-PTY path), currently logging-only. Session resolves via the
  sidecar in `.claude/hooks/post_tool_use.py::_update_agent_session`
  (`:444-513`, `AgentSession.get_by_id` at `:486`); `tool_call_count` bumped at `:503`.
  Fix #6's load-bearing seam (blocks via exit code 2). Confirmed.
- `agent/session_health.py` — `_agent_session_tool_timeout_loop` at `:3835`
  (`TOOL_TIMEOUT_LOOP_INTERVAL=30` at `:336`) — the **background** monitor
  Fix #6 must NOT be. Confirmed.
- `models/agent_session.py` — `tool_call_count` `:175`, `total_input_tokens`
  `:458`, `total_cost_usd` `:461`. Budget inputs present; Fix #6 only READS them.
  Confirmed.
- `ui/app.py:370` — `_get_worker_health()` (dashboard route now `/dashboard.json` at `:590`) reads the disk
  heartbeat. Extend for the Fix #5 operator surface. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1815 — CLOSED (PR #1823 merged); `last_loop_tick` beacon + `_self_kill()`
  landed. Deferred fixes #5/#6 here.
- #1820 — CLOSED (PR #1867 merged 2026-07-02, `72ba5d50`); the lease registry
  Fix #5 consumes. **Prerequisite now satisfied — Fix #5 BUILD unblocked.**
- #1818 — OPEN, tracking umbrella.
- #1816 — CLOSED (PR #1832); `supervise()`, scoped `os.killpg` teardown.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/completed/slot-lease-progress-deadline.md` (#1820, shipped) — the
  direct prerequisite (now merged/archived); Fix #5's No-Gos in that plan explicitly
  name #1821 as the next landing.
- `docs/plans/completed/liveness-wedge-recovery.md` (#1815, shipped) — parent.
- `docs/plans/completed/worker-fault-containment.md` (#1816, shipped) — adjacent.

## Prior Art

- **#1815 / PR #1823 (merged)** — dead-man's-switch: an on-loop task bumps
  `last_loop_tick`; an off-loop thread self-kills (`_self_kill()`, SIGKILL — the
  former SIGABRT, changed per #1808/#1816) on a stale beacon so launchd respawns.
  Fix #5 reuses the **off-loop publish cadence** (`_write_worker_heartbeat`) to also
  emit a Redis wall-clock beacon, and treats this `_self_kill()` self-recycle as the
  single killer it defers to. Recovery = restart (lossy).
- **#1820 (merged, PR #1867)** — `SlotLeaseRegistry` + on-loop reap pass
  (`_reap_slot_leases()`) + `registry.reclaim()`. Fix #5 reads its lease snapshot and drives a Redis
  reclaim-request drained by the same reap pass (targeted, lossless). **Hard
  prerequisite.**
- **`monitoring/worker_watchdog.py` (shipped, #1767/#1311)** — the existing
  out-of-domain process-restart recovery. Fix #5 is DISJOINT: it does
  restart-free, targeted slot reclamation + observability, and defers all
  process-kill to this actor + the dead-man's-switch.
- **`monitoring/session_watchdog.py` (shipped, #1128/#1313)** — the bridge-process
  session-health watchdog Fix #5 extends. Establishes the pattern of an actuating
  (not logging-only) watchdog with atomic Redis cooldowns and fail-quiet loops.
- **omnigent `enforcement.py`** — synchronous ALLOW/DENY at the tool-call
  dispatch point, "explicitly NOT a background monitor." The exact model for
  Fix #6.
- **Erlang/OTP supervisors** — recovery lives in a separate process from the
  supervised worker. The conceptual basis for putting Fix #5 in the bridge domain.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Is Incomplete for #1821 |
|-----------|-------------|-------------------------------|
| #1820 on-loop reap pass | Reclaims a terminal-owner leased slot via `registry.reclaim()` every health tick | **Runs on the loop it polices** — a frozen loop never runs it. Cannot satisfy "recovery from a process OTHER than the worker loop." |
| #1815 dead-man's-switch | Off-loop thread `_self_kill()`s (SIGKILL) on a stale beacon → launchd respawn | **Lossy** — a restart re-queues all in-flight work; cannot do targeted, restart-free reclamation of a single leaked slot. |
| `worker_watchdog.py` | Kills+respawns a dead/stale-heartbeat worker from launchd | **Lossy** and **coarse** (fresh process every 120s, disk-heartbeat only) — no per-slot lease visibility, no restart-free path. |
| `_agent_session_tool_timeout_loop` | Kills a tool-wedged session after a per-tier timeout | **Background monitor on the worker loop** — halts when the loop freezes; provides no inline per-call ceiling. |

**Root cause pattern:** every existing recovery either runs inside the failure
domain it polices, or recovers only by lossy restart. #1821 adds (a) a
bridge-domain actor that drives **restart-free** reclamation via a Redis-mediated
signal and surfaces the state, and (b) an **inline** budget that is structurally
independent of any loop.

## Research

No new external findings needed. The precedents (Erlang/OTP separate-process
supervisors; omnigent `enforcement.py` synchronous ALLOW/DENY) are captured in the
issue and inform the two designs directly. The one genuinely novel constraint —
that a loop-affine `asyncio.Semaphore` (and a `monotonic()` beacon) cannot be
touched or read from another process — is a physics fact, not a literature gap;
it drives the Redis-mediated contract below.

## Data Flow

### Fix #5 — scope (OQ2 resolved)

Fix #5 is framed **primarily as detection + operator-surface**, with the
reclaim-request as a **narrow secondary lever**. The rationale (previously OQ2):
when the worker loop is alive, #1820's autonomous on-loop reaper already reclaims
every terminal-owner lease each tick, so a bridge reclaim-request is *redundant* in
the common case; and when the loop is truly wedged, the request cannot be drained at
all. The request therefore has exactly **two** justified roles, and no others:

1. **The only reclaim lever under `SLOT_LEASE_REAP_DISABLED=1`.** #1820 gates its
   *autonomous* terminal-owner reclaim behind that flag (detection/log/heartbeat still
   run; the reclaim *action* is suppressed — confirmed in the #1820 plan). The bridge
   drain is a DISTINCT code path, so it remains a working reclaim lever exactly when the
   autonomous one is disabled.
2. **The mechanism that makes Acceptance #1 provable** — recovery driven from a
   non-worker process (the acceptance test pushes the request from the test process,
   not the loop).

Everything else in Fix #5 is detection + surfacing: the bridge records `loop_wedged`,
increments counters, writes the action log, and shows lease/liveness state on the
dashboard — and **defers every kill** to the existing killers. The reclaim TRIGGER
crosses the process boundary; `registry.reclaim()` still runs on the worker loop
(loop-affinity physics). This is the resolved scope; the reclaim-request is retained
precisely because of roles (1) and (2), not because it beats the on-loop reaper in the
live-loop common case.

### Fix #5 — the Redis-mediated cross-process contract (THE central design)

The bridge process **cannot** touch the worker's in-memory `SlotLeaseRegistry`
(the wrapped `asyncio.Semaphore` is loop-affine) and **cannot** read
`last_loop_tick` (a `monotonic()` value meaningless outside the worker process).
Therefore every cross-process signal goes through Redis, published by the worker
and read by the bridge. Three Redis keys form the contract (all per-host, all
TTL'd so a dead worker's records expire):

**Worker → Redis (publish):**
1. **Loop beacon** — `worker:loop_beacon:{host}` (TTL `= 3 × WORKER_HEARTBEAT_INTERVAL`).
   Written by `_write_worker_heartbeat()` (off-loop thread, existing cadence) as
   JSON `{wall_ts: time.time(), loop_beacon_age_s: <now_monotonic − get_loop_tick()>,
   armed: bool}`. The off-loop thread already computes `beacon_age` in
   `_heartbeat_cycle`; we translate the per-process monotonic age into a
   cross-process **wall-clock** timestamp + age. `armed=False`/age `None` means the
   loop has not ticked yet (never treated as wedged).
2. **Lease snapshot** — `worker:slot:leases:{host}` (TTL `= 3 × health-tick`).
   Written by the #1820 on-loop reap pass (top-of-tick, it already snapshots
   `registry.leases()`) as JSON `{permits_free, held, max, ts: time.time(),
   owners: [{owner_session_id, acquired_at_wall_ts}]}`.

**Bridge `session_watchdog` → reads both each tick, then:**
3. **Beacon fresh + terminal-owner lease held (leak under a live loop):** for each
   `owner` in the lease snapshot whose `AgentSession` status is terminal
   (`models/session_lifecycle.py` `TERMINAL_STATUSES`), and only when
   `now − loop_beacon.wall_ts ≤ BRIDGE_WORKER_BEACON_STALE_S` (loop alive) AND
   `BRIDGE_SLOT_RECLAIM_ENABLED` is set, push `owner` onto the Redis list
   **`worker:slot:reclaim_requests:{host}`** (dedup via a short-TTL `SET NX` marker
   per owner) — then **`LTRIM` the list to `RECLAIM_REQUESTS_MAX` entries**
   (default 256, mirroring the `worker:watchdog:actions` bound) so a multi-owner leak
   burst under a slow-but-not-wedged tick cannot grow the list unboundedly (Race 4).
   Also append an entry to the **`worker:watchdog:actions:{host}`** operator log
   (capped `LPUSH`+`LTRIM`). This is the out-of-domain reclaim TRIGGER; the actual
   `registry.reclaim()` runs on the worker loop (physics requirement).
4. **Beacon stale (loop wedged/worker down):** append a `loop_wedged` detection to
   `worker:watchdog:actions:{host}` and increment
   `{host}:worker-watchdog:loop_wedged_detected`. **Take NO kill action** — defer
   to the single existing killer (dead-man's-switch / `worker_watchdog.py`).
5. **Beacon fresh + no terminal-owner leak:** healthy tick — clear the per-owner
   dedup markers so a future re-leak re-triggers.

**Worker on-loop reaper (Fix #5 worker-side extension of #1820's reap pass):**
6. Inside `_reap_slot_leases()`, **drain** `worker:slot:reclaim_requests:{host}`
   (atomic `LPOP` loop) **in the always-run region — AFTER Phase 1 detection ends
   (`agent/session_health.py:2574`) and BEFORE the Phase-2 `if reap_disabled: return`
   (`:2576-2578`, concern #5)**, never inside the flag-gated Phase-2 reclaim loop.
   For each drained `owner`, re-read its status fresh; reclaim ONLY when the fresh
   status is an EXPLICIT terminal value — `registry.reclaim(owner)` (idempotent) and
   increment `{project_key}:session-health:bridge_reclaims`. **`get_by_id → None` (or
   any lookup exception) is "unknown → SKIP, do not reclaim" (concern #2, #1868)** — a
   DELIBERATE divergence from the autonomous reaper's None-as-terminal handling
   (`:2585`), so a transient Redis lookup blip cannot make the drain strip a LIVE
   session's permit (semaphore over-admission). **This drain is a DISTINCT code
   path from #1820's autonomous terminal-owner reclaim**, so it fires even when
   `SLOT_LEASE_REAP_DISABLED=1` gates the autonomous path — giving the bridge a
   genuine lever the on-loop reaper alone does not provide. Idempotent
   `registry.reclaim()` means the two paths converge harmlessly when both are on.
   **Mixed-version detectability (new-worker/old-bridge), right-sized (concern #5):**
   the `bridge_contract_stale` signal is minimum-viable and **reuses the existing
   `BRIDGE_WORKER_BEACON_STALE_S` threshold** — NO new staleness var (the mixed-version
   window self-heals in seconds, so the beacon-stale window is a fine proxy; a dedicated
   `BRIDGE_CONTRACT_STALE_S` was dropped per concern #5). The worker keeps ONE Redis
   timestamp `worker:slot:last_reclaim_request_drain:{host}` (set whenever the drain
   pops ≥1 request). On a tick where a terminal-owner leak is observed AND
   `now − last_drain_ts > BRIDGE_WORKER_BEACON_STALE_S`, emit
   `bridge_contract_stale` once (dedup `SET NX EX`, action-log + counter). No
   per-owner bookkeeping, no separate detector loop — just the one timestamp compared
   to the one threshold. The autonomous #1820 reaper still reclaims the leak (unless
   `SLOT_LEASE_REAP_DISABLED=1`), so no slot is lost; the signal exists only to make
   the contract gap operator-visible rather than a silent drop.

**Output:** `permits_free` recovers without a restart; the reclaim decision +
trigger provably ran in the bridge process (Acceptance #1); the lease/liveness
state is visible on the dashboard.

### Fix #6 — synchronous in-path budget

1. **Entry point:** the PreToolUse dispatch, on BOTH hook surfaces —
   `agent/hooks/pre_tool_use.py::pre_tool_use_hook` (SDK path) and
   `.claude/hooks/pre_tool_use.py::main` (interactive `claude` TUI / granite-PTY
   path, the load-bearing production path).
2. **Shared evaluator:** a new `agent/tool_budget.py::evaluate_tool_budget(session)
   -> BudgetVerdict` reads `session.tool_call_count` and `session.total_cost_usd`
   and returns `deny` (with a reason) when `tool_call_count >=
   MAX_TOOL_CALLS_PER_SESSION` or `total_cost_usd >= SESSION_COST_CAP_USD`, else
   `allow`. Pure, synchronous, no await, no I/O beyond the session read the hook
   already performs. **The `total_cost_usd` branch is SDK/headless-path-only
   (concern #1):** cost is populated only by `agent/sdk_client.py` (the SDK
   `ResultMessage.total_cost_usd` path at `:426` and the headless `claude -p
   stream-json` `result`-event path at `:2868`). The **load-bearing granite-PTY
   interactive path never writes `total_cost_usd`** (nothing under
   `agent/granite_container/` populates it — the interactive TUI transcript has no
   cost line), so on granite `total_cost_usd` stays `0.0` and the cost branch is a
   **permanent no-op there**. The operative granite backstop is therefore the
   `tool_call_count` cap alone. The cost check is retained (it is live and correct on
   the SDK/headless path) but is explicitly documented as SDK-path-only so the plan
   does NOT imply a working cost ceiling on granite; the constant carries an inline
   "SDK/headless-path-only — no-op on granite" comment.
3. **Surface adaptation — the inline DENY fires by default.** A `deny` verdict
   actuates a DENY on both surfaces (SDK `{"decision":"block","reason":<verdict.reason>}`
   / CLI stderr + `exit 2`) whenever `TOOL_BUDGET_ENABLED` is on (the **default**) —
   the backstop actually backstops (Acceptance #2), even under a frozen health loop.
   On every deny, once per session (dedup `SET NX`): increment
   `{project_key}:tool-budget:tripped`, log a WARNING, and set the race-free hook-owned
   `budget_tripped` flag (step 4(a) — a field write, NOT a `status` change).
   `TOOL_BUDGET_ENABLED=false` is the instant kill-switch if the cap misfires.
   **Granite shared-counter caveat:** on the granite path `tool_call_count` sums PM +
   Dev sub-agent tool calls (each sub-agent burns the same session counter, so the
   effective per-role ceiling is ~half `MAX_TOOL_CALLS_PER_SESSION`), so a trip can deny
   **both** PM and Dev mid-build — this is bounded by the conservative default (1000)
   and the kill-switch and is a MAX-**tuning** consideration (granite may want a higher
   `MAX`), NOT a reason to gate the deny off (which would leave Acceptance #2's backstop
   inert by default). Only the DISRUPTIVE extras — the `status → paused_budget`
   transition **and** the Telegram ping (step 4) — are gated behind the separate
   `TOOL_BUDGET_AUTO_PAUSE` switch (DISTINCT from `TOOL_BUDGET_ENABLED`, default **off**).
4. **Auto-pause + human surfacing — gated behind `TOOL_BUDGET_AUTO_PAUSE` (default
   off).** A budget deny is a NEW stopping point; under this codebase's auto-continue
   design a silent deny would strand the session. On EVERY deny (default included; see
   step 3) the caller sets the race-free hook-owned flag:
   (a) `budget_tripped = True` + `budget_tripped_reason = "per-session tool budget
       reached: <dimension> <value>"`, written with a narrow
       `save(update_fields=["budget_tripped", "budget_tripped_reason", "updated_at"])`.
       **The hook NEVER writes `status`** (concern #2): on the load-bearing granite-PTY
       path the `bridge_adapter` writes `AgentSession.status` through its own
       partitioned `update_fields` saves (`agent/granite_container/bridge_adapter.py:385-391,
       :804-845, :957`), and a hook-driven `status` write on another process/thread
       would RACE and clobber it (last-writer-wins on the `status` field).
       `budget_tripped` / `budget_tripped_reason` are fields NO other writer touches,
       so they are always race-free and are the authoritative human-legible signal; the
       dashboard, `valor-session status`, and the adapter/worker READ them.
   Then, **only when `TOOL_BUDGET_AUTO_PAUSE` is set**, on the FIRST deny per session
   (same `SET NX` dedup) the caller ALSO:
   (b) transitions the session to **`paused_budget`** — a NEW **non-drip-eligible**
       status added to `models/session_lifecycle.py` (`NON_TERMINAL_STATUSES` +
       `RECOVERY_OWNERSHIP["paused_budget"] = "human"`). This is the BLOCKER fix:
       `reflections/agents/session_recovery_drip.py` re-queues ONLY `status="paused"` /
       `"paused_circuit"` sessions back to `pending` (verified: it filters exactly those
       two and calls `transition_status(..., "pending")` one per tick), so setting bare
       `paused` here would create a `pending→denied→paused→pending` runaway — the exact
       loop the budget exists to stop, made worse because `tool_call_count`/
       `total_cost_usd` are CUMULATIVE and never reset. `paused_budget` is never dripped
       (its `RECOVERY_OWNERSHIP` is human-only), so no loop can form. To honor the
       concern #2 no-hook-status-write rule, the transition is performed by the **status
       owner** — the granite `bridge_adapter`/worker reads `budget_tripped` at its next
       turn boundary and calls `transition_status(session, "paused_budget")`; on the
       SDK/headless path (no competing status writer) the hook may transition directly.
       Because `paused_budget` is non-drip, no status-write interleaving can produce a
       flap; AND
   (c) queues a user-visible Telegram signal on the originating message — mirroring
       `monitoring/session_watchdog.py::_apply_stall_reaction` (write a reaction/steer
       payload to the bridge's reaction queue with the same atomic `SET NX EX` dedup
       pattern), so the human sees "this session hit its budget" without reading
       `logs/worker.log`.
   All of (a)-(c) are fail-quiet: a surfacing error must NEVER turn a legitimate allow
   into a deny, nor a deny into a crash — the deny itself (block/exit 2) always
   proceeds; only the *notification* is best-effort. The deny is NOT merely a log line
   + dashboard counter.
5. **Output:** the tool call is denied inline, at dispatch, regardless of whether
   any background loop is running — so a runaway session is capped even under a
   frozen health loop, AND the human is notified that the cap was hit.

## Architectural Impact

- **New dependencies:** none (stdlib `asyncio`, `time`, `json`; existing Redis via
  `POPOTO_REDIS_DB`).
- **Config location (not a defect):** the new env vars read via raw
  `os.environ.get()` at module scope, NOT through `config/settings.py`. This matches
  the sibling precedent — `WORKER_HEARTBEAT_INTERVAL` and the #1815/#1820 threshold
  constants use the same raw-`os.environ` pattern — so no `config/settings.py` entry
  is required or expected.
- **New module:** `agent/tool_budget.py` (Fix #6 shared evaluator).
- **Interface changes (Fix #5):** `_write_worker_heartbeat()` gains a Redis
  beacon-publish side effect; the #1820 reap pass gains a lease-snapshot publish +
  a reclaim-request drain; `monitoring/session_watchdog.py` gains an out-of-domain
  worker-liveness/slot check (a new function called from `watchdog_loop`).
- **Interface changes (Fix #6):** both PreToolUse hooks call `evaluate_tool_budget`;
  the inline deny fires by default under `TOOL_BUDGET_ENABLED`, and the
  `TOOL_BUDGET_AUTO_PAUSE` gate wraps only the status→paused_budget + Telegram extras.
- **Model change (Fix #6, concern #2):** `models/agent_session.py` gains two
  hook-owned fields — `budget_tripped` (bool, default `False`) and
  `budget_tripped_reason` (str) — for deny-surfacing WITHOUT a `status` write (which
  would race the granite `bridge_adapter` partitioned `update_fields` saves).
  Additive, falsy-default, schema-on-read → no data migration (see Update System).
- **Coupling:** the bridge watchdog depends on the worker's Redis-published
  contract only — never on any in-worker object. The reclaim-request drain adds a
  minimal, one-directional Redis coupling between bridge and worker loop.
- **Data ownership:** all new Redis keys are per-host, TTL'd, and rebuilt each tick
  — no Popoto model for the contract; the only model touch is the two additive
  `AgentSession` fields above (no migration).
- **Reversibility:** high. Kill-switches revert each fix to a no-op:
  `BRIDGE_SLOT_RECLAIM_ENABLED` (Fix #5 reclaim-trigger), `TOOL_BUDGET_ENABLED`
  (Fix #6 evaluation + inline deny entirely). `TOOL_BUDGET_AUTO_PAUSE` defaults OFF,
  so by default a tripped session is denied-inline + flagged + counted but is NOT
  auto-paused and NOT pinged on Telegram — the disruptive extras stay opt-in until the
  `tool-budget:tripped` histograms confirm a safe threshold. The beacon/lease publish is
  observability-only and harmless if unread.

## Appetite

**Size:** Medium

**Team:** Solo dev. Fix #5 needs careful cross-process reasoning (loop affinity,
Redis contract, no-parallel-killer boundary) and is gated on #1820; Fix #6 is a
small, self-contained inline gate. 1 PM check-in, 1-2 review rounds.

**Interactions:**
- PM check-ins: 1 (confirm the Redis-mediated contract + the no-second-kill
  ownership boundary; confirm which PreToolUse surface is load-bearing for
  production granite-PTY sessions).
- Review rounds: 1-2 (cross-process correctness of Fix #5; the block-on-both-hook-
  surfaces correctness of Fix #6).

**PR strategy (split into two independent sub-pipelines).** Fix #6 and Fix #5 are
**two dependency-disjoint pipelines**, each with its own build → validate → docs
arc — NOT one linear chain:

- **Sub-pipeline A — Fix #6 (inline budget):** dependency-free, builds **first**,
  and lands as its own small PR satisfying Acceptance #2. It does NOT wait on Fix #5
  or #1820. Its validation asserts only Acceptance #2 + the Fix #6 failure paths.
- **Sub-pipeline B — Fix #5 (out-of-domain recovery):** BUILD gate on **#1820
  merged** is now SATISFIED (PR #1867, 2026-07-02) — the `SlotLeaseRegistry` it
  extends exists. Lands as a second PR satisfying Acceptance #1. Its validation
  asserts Acceptance #1 + the Fix #5 failure paths + the four race scenarios.

The two sub-pipelines share only the documentarian and the final validation sweep
(which runs once both PRs have landed). This unblocks the independent,
higher-confidence half (A) immediately and isolates the cross-process change (B)
behind the merged registry. The **Step by Step** task graph below reflects exactly
this: `validate-tool-budget` depends ONLY on `build-tool-budget`;
`validate-recovery` depends ONLY on `build-out-of-domain-recovery` (+ #1820); the
final `validate-all` sweep depends on both.

## Prerequisites

| Requirement | Check Command | Purpose | Gates |
|-------------|---------------|---------|-------|
| **#1820 lease registry merged** | `grep -c "class SlotLeaseRegistry" agent/slot_lease.py` | Fix #5 reads `registry.leases()` / `reclaim()` + extends the on-loop reap pass | **Fix #5 BUILD** |
| #1815 beacon present | `grep -c "def get_loop_tick" agent/session_state.py` | Confirms `last_loop_tick` foundation (translated to the Redis wall-clock beacon) | Fix #5 |
| #1820 reap pass present | `grep -c "reclaim" agent/session_health.py` | Confirms the on-loop reap the reclaim-request drain extends | Fix #5 |
| Python ≥ 3.11 | `python -c "import sys; assert sys.version_info >= (3, 11)"` | repo runs 3.14.3 | both |

**Fix #6 has NO prerequisites** and may build immediately. **Fix #5 BUILD gate is
now SATISFIED** — #1820 merged (PR #1867, 2026-07-02), so the `SlotLeaseRegistry`,
the on-loop reap pass (`_reap_slot_leases()`), and `registry.reclaim()` all exist.
All four prerequisite checks above PASS as of HEAD `2d1cf419`
(`scripts/check_prerequisites.py` confirms). Both sub-pipelines may now build.

## Solution

### Key Elements

- **Worker-published Redis contract (Fix #5):** `worker:loop_beacon:{host}`
  (wall-clock ts + loop-beacon age, written by `_write_worker_heartbeat`) and
  `worker:slot:leases:{host}` (lease snapshot, written by the #1820 reap pass).
  Both TTL'd so a dead worker's records expire and the bridge sees "no beacon."
- **Bridge out-of-domain check (Fix #5):** a new `check_worker_liveness_and_slots()`
  in `monitoring/session_watchdog.py`, called from `watchdog_loop`. Reads the two
  keys; for a terminal-owner lease under a **fresh** beacon it pushes a
  reclaim-request (`worker:slot:reclaim_requests:{host}`) + logs an action; for a
  **stale** beacon it records a `loop_wedged` detection and DEFERS the kill. No
  kill ladder here.
- **Reclaim-request drain (Fix #5, worker-side):** the #1820 reap pass drains the
  request list at top-of-tick and `registry.reclaim()`s each terminal owner — a
  path distinct from the autonomous reclaim so it works under
  `SLOT_LEASE_REAP_DISABLED=1`.
- **Inline budget evaluator (Fix #6):** `agent/tool_budget.py::evaluate_tool_budget`
  — pure, synchronous ALLOW/DENY on `tool_call_count` / `total_cost_usd`, called
  from BOTH PreToolUse hooks; blocks inline.
- **Operator surface:** `worker:watchdog:actions:{host}` action log +
  `bridge_reclaims` / `loop_wedged_detected` / `bridge_contract_stale` /
  `tool-budget:tripped` counters, surfaced in the `worker` block of
  `localhost:8500/dashboard.json` (`_get_worker_health`, `ui/app.py:370`).
- **Env kill-switches (all NAMED, env-overridable, conservative-provisional):**
  `BRIDGE_SLOT_RECLAIM_ENABLED`, `BRIDGE_WORKER_BEACON_STALE_S`,
  `RECLAIM_REQUESTS_MAX` (list-cap for `worker:slot:reclaim_requests`, Race 4),
  (`bridge_contract_stale` reuses the existing `BRIDGE_WORKER_BEACON_STALE_S`
  threshold — no dedicated staleness var, concern #5),
  `TOOL_BUDGET_ENABLED` (evaluate + inline deny; DEFAULT ON), `TOOL_BUDGET_AUTO_PAUSE`
  (status→paused_budget + Telegram extras — DEFAULT OFF), `MAX_TOOL_CALLS_PER_SESSION`,
  `SESSION_COST_CAP_USD` (SDK/headless-path-only — no-op on granite, concern #1).

### Flow

**Fix #5:** worker off-loop tick → publish `worker:loop_beacon` → worker on-loop
reap tick → publish `worker:slot:leases` + drain `worker:slot:reclaim_requests`
(→ `registry.reclaim`) → bridge `watchdog_loop` tick → read beacon + leases →
terminal-owner under fresh beacon → push reclaim-request + log action / stale
beacon → log `loop_wedged` + defer to existing killer.

**Fix #6:** SDK or CLI PreToolUse fires → `evaluate_tool_budget(session)` →
`deny` if over-budget → `{"decision":"block"}` (SDK) / exit 2 (CLI) + increment
`tool-budget:tripped` / else `allow`.

### Technical Approach

**Fix #5 — out-of-domain recovery (BUILD after #1820 merges):**

- **Publish the loop beacon.** In `agent/session_health.py::_write_worker_heartbeat`
  (`:3386`, off-loop cadence), after the disk write, also
  `POPOTO_REDIS_DB.set("worker:loop_beacon:{host}", json.dumps({...}), ex=3*WORKER_HEARTBEAT_INTERVAL)`.
  The beacon age is computed the same way `_heartbeat_cycle` already does
  (`now_monotonic − get_loop_tick()`), but the **wall-clock** `time.time()` is what
  the bridge keys on — never a monotonic value (Risk 1). Fail-quiet (Redis error
  must never break the heartbeat).
- **Publish the lease snapshot + drain reclaim-requests.** In the #1820 on-loop
  reap pass (`_reap_slot_leases()` at `agent/session_health.py:2485`; access the
  registry via `_session_state._slot_registry`, guard on `is None`):
  (a) publish `worker:slot:leases:{host}` from the same `list(registry.leases())`
  snapshot; (b) **drain** `worker:slot:reclaim_requests:{host}` via an atomic `LPOP`
  loop, re-read each owner's status fresh, and `registry.reclaim(owner)` **only when
  the fresh status is an EXPLICIT terminal value** in `_TERMINAL_STATUSES` (increment
  `{project_key}:session-health:bridge_reclaims`).
  **None-on-transient-error trap (concern #2, #1868) — DELIBERATE divergence:** the
  autonomous Phase-2 reclaim at `agent/session_health.py:2585` treats
  `AgentSession.get_by_id(owner) → None` as terminal (`if fresh is None or ... in
  _TERMINAL_STATUSES`). The bridge-driven drain MUST NOT: a transient Redis lookup
  failure returning `None` (or any lookup exception) is "unknown", and reclaiming on it
  would strip a LIVE session's permit (semaphore over-admission). So the drain reclaims
  ONLY on an explicit terminal `status`; `None` or an exception → **SKIP, do not
  reclaim** (log at DEBUG, leave the request for a future tick to re-evaluate). This is
  a conscious departure from the reaper's None-as-terminal handling — the drain is
  request-driven and must not over-reclaim on a lookup blip.
  **EXACT INSERTION POINT (concern #5) — the drain (b) MUST land in the ~2-line gap
  AFTER Phase 1's detection `try/except` ends (currently
  `agent/session_health.py:2574`, the `logger.exception("...detection phase failed")`
  line) and BEFORE the Phase-2 early return `if reap_disabled: return` (currently
  `:2576-2578`, HEAD `2d1cf419`).** Placed there, the drain runs on EVERY tick,
  including under `SLOT_LEASE_REAP_DISABLED=1` — which is the whole point (the bridge
  reclaim-request is the *only* reclaim lever when the flag gates the autonomous
  Phase-2 reclaim off). Do NOT place the drain inside or after the Phase-2 `for lease
  in leases_snapshot:` block (currently `:2580+`): that block is SKIPPED by the
  `return` under the flag, so a drain there would never fire when reaping is disabled
  — silently defeating the feature's headline capability. The lease-snapshot publish
  (a) may sit anywhere in the always-run region (e.g. right after the Phase 1
  fingerprint); only the drain has the hard before-the-return constraint. Both publish
  and drain are fail-quiet.
- **Bridge out-of-domain check.** Add `check_worker_liveness_and_slots()` to
  `monitoring/session_watchdog.py`; call it from `watchdog_loop` (`:185` loop
  body, wrapped in its own try/except like the existing `check_stalled_sessions`).
  It: reads `worker:loop_beacon` + `worker:slot:leases`; if the beacon is missing
  or `now − wall_ts > BRIDGE_WORKER_BEACON_STALE_S` → log a `loop_wedged` action +
  increment `loop_wedged_detected`, **return without any kill**; else, for each
  lease owner **whose DB status is an explicit terminal value** (a `None`/error read
  is "unknown" → skip, mirroring the drain's #1868 posture), push to
  `worker:slot:reclaim_requests` (dedup `SET NX` per owner, short TTL) + append to
  `worker:watchdog:actions` (capped `LPUSH`+`LTRIM`), gated on
  `BRIDGE_SLOT_RECLAIM_ENABLED`. **Non-blocking Redis (concern #4):** these per-owner
  pushes run inside the async `watchdog_loop`, so they MUST use the **async Redis
  client** OR be batched into a **single pipeline** — never N sequential sync
  `POPOTO_REDIS_DB` calls (`socket_timeout=5`), which on a multi-owner leak burst could
  block the single bridge event loop up to `N×5s`, starving Telegram delivery +
  `check_stalled_sessions`. Fail-quiet.
- **No second killer (no-parallel-systems).** `check_worker_liveness_and_slots`
  NEVER sends a signal to the worker process, NEVER runs `launchctl`, NEVER writes
  `worker:watchdog:critical`. Process recovery stays with the dead-man's-switch +
  `worker_watchdog.py`. The bridge owns detection + restart-free reclaim-trigger
  only. (Verification greps assert no `os.kill`/`launchctl`/`SIGKILL`/`SIGABRT` in the new
  function.)
- **Operator surface.** Extend `_get_worker_health()` (`ui/app.py:370`) to read
  `worker:slot:leases` (`permits_free`/`held`), the `bridge_reclaims` /
  `loop_wedged_detected` / `bridge_contract_stale` / `tool_budget_tripped` /
  `tool_budget_resolution_errors` counters, and the last few `worker:watchdog:actions`
  entries — additive fields on the existing `worker` block only.

**Fix #6 — synchronous per-tool budget (BUILD independently, first):**

- **Shared evaluator.** Create `agent/tool_budget.py`:
  ```
  @dataclass
  class BudgetVerdict:
      allow: bool
      reason: str | None = None

  # Provisional, env-overridable — tune after observing real per-session
  # tool-call / cost distributions on the live bridge machine.
  MAX_TOOL_CALLS_PER_SESSION = int(os.environ.get("MAX_TOOL_CALLS_PER_SESSION", "1000"))
  # SDK/headless-path-only — NO-OP on granite (concern #1). total_cost_usd is
  # written solely by agent/sdk_client.py (SDK ResultMessage + headless
  # `claude -p stream-json`); the granite-PTY interactive path never populates
  # it, so on granite this cap can never fire. Kept for the SDK/headless path.
  SESSION_COST_CAP_USD = float(os.environ.get("SESSION_COST_CAP_USD", "50.0"))
  # Master switch: enables the budget AND the inline DENY. DEFAULT ON — a deny
  # verdict actuates the inline block/exit-2 by default, so the backstop actually
  # backstops (Acceptance #2). TOOL_BUDGET_ENABLED=false is the instant kill-switch
  # if the cap ever misfires in production.
  TOOL_BUDGET_ENABLED = os.environ.get("TOOL_BUDGET_ENABLED", "true").strip().lower() \
      not in ("", "0", "false", "no")
  # Auto-pause switch (BLOCKER + concern #3/#6): gates ONLY the status-mutation +
  # Telegram-surfacing a deny additionally performs. DEFAULT OFF. With it off, a
  # deny still blocks the call inline + counts + logs + sets the budget_tripped
  # flag, but the session `status` is LEFT UNTOUCHED — so nothing moves the session
  # into a drip-eligible state and no runaway pending→denied→paused→pending loop can
  # form. When opted in (=1), a deny ALSO transitions status → paused_budget (a
  # NON-drip-eligible status; see models/session_lifecycle.py) and queues Telegram.
  TOOL_BUDGET_AUTO_PAUSE = \
      os.environ.get("TOOL_BUDGET_AUTO_PAUSE", "false").strip().lower() \
      in ("1", "true", "yes")

  def evaluate_tool_budget(session) -> BudgetVerdict:
      # Pure verdict only — decides deny/allow. The CALLER (hook) actuates the inline
      # block on a deny (gated by TOOL_BUDGET_ENABLED) and, only when
      # TOOL_BUDGET_AUTO_PAUSE is set, the status→paused_budget transition + Telegram.
      if not TOOL_BUDGET_ENABLED or session is None:
          return BudgetVerdict(allow=True)
      calls = int(getattr(session, "tool_call_count", 0) or 0)
      cost = float(getattr(session, "total_cost_usd", 0.0) or 0.0)
      if calls >= MAX_TOOL_CALLS_PER_SESSION:
          return BudgetVerdict(False, f"per-session tool-call budget reached "
                                      f"({calls}/{MAX_TOOL_CALLS_PER_SESSION})")
      # Cost branch is dead on granite (cost stays 0.0); live only on SDK/headless.
      if cost >= SESSION_COST_CAP_USD:
          return BudgetVerdict(False, f"per-session cost cap reached "
                                      f"(${cost:.2f}/${SESSION_COST_CAP_USD:.2f})")
      return BudgetVerdict(allow=True)
  ```
  Pure and synchronous — no await, no background timer. This is the omnigent
  ALLOW/DENY model. **The evaluator returns a verdict; the hook (caller) actuates
  it.** On a `deny` verdict the caller ALWAYS (a) blocks the call inline (SDK
  `{"decision":"block"}` / CLI `exit 2`), (b) increments `tool-budget:tripped`
  (dedup `SET NX`), (c) logs a WARNING, and (d) sets the hook-owned `budget_tripped`
  flag — this is the backstop and it fires by default under `TOOL_BUDGET_ENABLED`.
  Only the additional **status-mutation + Telegram-surfacing** (Data Flow step 4) is
  gated behind `TOOL_BUDGET_AUTO_PAUSE` (default off), because a `status` transition
  and a user-facing ping are the disruptive parts. The **granite shared-counter
  caveat** (PM + Dev sub-agents burn the same `tool_call_count`, so the effective
  per-role ceiling is ~half `MAX_TOOL_CALLS_PER_SESSION`) is handled by the
  conservative default (1000) and the `TOOL_BUDGET_ENABLED=false` kill-switch — a
  tuning consideration (granite may want a higher `MAX`), NOT a reason to disable the
  deny by default (which would leave Acceptance #2's backstop inert). Enforcement
  policy lives in one place on each surface.
- **Fail-open MUST distinguish "no session" from "infra error"** (both hooks). The
  budget's fail-open posture is a backstop that must never brick the agent — but an
  unconditional fail-open conflates two very different cases and would let the
  backstop go **silently blind during exactly the partially-wedged Redis conditions
  it exists to guard**. Split them:
  - **Genuine no-session** (`AGENT_SESSION_ID` unset / sidecar has no
    `agent_session_id` / `get_by_id` returns `None` with no exception) → legitimately
    ALLOW, silently. Local CLI / non-agent sessions have no budget to enforce; this
    is the normal path and needs no log noise.
  - **Infra / resolution error** (Redis raised, `get_by_id` threw, JSON decode of the
    sidecar failed) → the backstop is going blind. ALLOW (still fail-open — a
    resolution failure must not brick tool calls) but **log LOUDLY at WARNING** with
    the error and an explicit "tool-budget backstop is BLIND this call" message, and
    increment a `{project_key}:tool-budget:resolution_errors` counter surfaced on the
    dashboard. A rising counter means the budget cannot see sessions — an operator
    signal, not a silent no-op.
  Implement the split by catching the resolution exception separately from the
  no-session branch (two distinct code paths), not one blanket `except: allow`.
- **SDK hook.** At the TOP of `agent/hooks/pre_tool_use.py::pre_tool_use_hook`
  (before the write-capable filter so it covers ALL tools), resolve the session via
  `AGENT_SESSION_ID` (as `_handle_skill_tool_start` already does at `:360`), applying
  the no-session-vs-infra-error split above; call `evaluate_tool_budget`. On a deny
  verdict (under `TOOL_BUDGET_ENABLED`, default on) ALWAYS return
  `{"decision":"block","reason":...}`, increment `{project_key}:tool-budget:tripped`
  (dedup `SET NX` per session) + WARNING, and set the `budget_tripped` flag — the
  inline block fires by default. **Only when `TOOL_BUDGET_AUTO_PAUSE` is set** does
  the deny ALSO transition status → `paused_budget` + queue the Telegram signal (Data
  Flow step 4).
- **CLI hook.** At the top of `.claude/hooks/pre_tool_use.py::main`, resolve the
  session via the sidecar (the exact `_load_agent_session_sidecar` →
  `AgentSession.get_by_id` path used in `.claude/hooks/post_tool_use.py:474-486`),
  applying the same no-session-vs-infra-error split; call `evaluate_tool_budget`. On
  a deny verdict (under `TOOL_BUDGET_ENABLED`, default on) ALWAYS print the reason to
  stderr + `sys.exit(2)` (Claude Code's block convention), increment
  `tool-budget:tripped` + WARNING, and set the `budget_tripped` flag — the inline
  block fires by default on the load-bearing granite-PTY surface. **Only when
  `TOOL_BUDGET_AUTO_PAUSE` is set** does the deny ALSO transition status →
  `paused_budget` + queue Telegram (Data Flow step 4). The granite shared-counter
  caveat is a MAX-tuning consideration, not a reason to gate the deny off. Fail-open
  on a resolution error (log loudly per the split above); a genuine no-session allows
  silently — the budget is a backstop, not a gate that can itself brick the agent.
- **CLI-hook fail-open granularity (exit-2 must propagate; a check bug must fail
  open).** Ground in `.claude/hooks/pre_tool_use.py`: `main()` is wrapped by a
  module-level `try/except Exception` at the bottom (`if __name__ == "__main__":`
  → `except Exception as e: log_hook_error(...)`), which exits 0 on a swallowed
  error. Two facts make this the correct wrapper granularity, and the plan relies on
  them explicitly:
  1. A genuine deny is raised as `sys.exit(2)` → `SystemExit`, which is NOT a
     subclass of `Exception`, so the module-level `except Exception` does **not**
     catch it — the exit-2 propagates and the tool is denied. (Verified against HEAD:
     the wrapper catches `Exception`, not `BaseException`.)
  2. A bug *inside* the budget check raises a normal `Exception` → caught by the
     module-level wrapper → logged via `log_hook_error` → process exits 0 → tool
     ALLOWED (fails open). This is the desired direction.
  Therefore the budget check is placed **inside `main()`**, and the deny path uses
  `sys.exit(2)` (never a caught-and-swallowed `return`). Do NOT wrap the budget
  check in its own `try/except` that would catch `SystemExit` (e.g. a bare
  `except:` or `except BaseException:`), which would invert the semantics and swallow
  a real deny. The no-session-vs-infra split (above) is a plain `if`/`try` on the
  *resolution* only, returning normally (allow) — it never intercepts the exit-2 that
  the deny branch raises AFTER a successful resolution.
- **Which surface is load-bearing.** The interactive granite-PTY path uses the CLI
  hook; the headless SDK path uses the SDK hook. Fix #6 wires BOTH so the ceiling
  holds regardless of harness — the shared evaluator guarantees identical
  thresholds. (PM check-in confirms the primary production surface.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The beacon/lease publish in `_write_worker_heartbeat` / the reap pass must
  never raise into the worker loop — a Redis error logs and the tick continues.
  Test asserts a publish exception is swallowed, heartbeat still written.
- [ ] `check_worker_liveness_and_slots` must never raise into `watchdog_loop` — a
  malformed beacon JSON or Redis error logs and the loop continues. Test feeds
  corrupt JSON, asserts no propagation.
- [ ] The reclaim-request drain must re-read owner status fresh and reclaim ONLY on
  an explicit terminal status — a non-terminal owner is a no-op (never strips a live
  owner's permit). Test asserts a still-`running` requested owner is NOT reclaimed.
- [ ] The drain treats `get_by_id → None`/lookup-exception as **unknown → SKIP**
  (concern #2, #1868), NOT terminal — a transient Redis blip must NOT reclaim a live
  permit. Test: a requested owner whose `get_by_id` returns `None` (and one that
  raises) is NOT reclaimed; `permits_free` unchanged.
- [ ] Bridge push stays non-blocking (concern #4): `check_worker_liveness_and_slots`
  pushes reclaim-requests via the async client / a single pipeline. **Bounded-wall-time
  test:** a burst of many terminal-owner leases completes the push phase well under a
  wall-clock bound (asserts NO `N×socket_timeout` serial-blocking of the async loop).
- [ ] Fix #6 evaluator + hooks must **fail open** — any session-resolution or Redis
  error results in `allow` (the budget never bricks a session). Test injects a
  resolution error, asserts the tool call proceeds.
- [ ] Fix #6 fail-open must **distinguish no-session from infra error**: a genuine
  no-session (unset `AGENT_SESSION_ID` / no sidecar) allows **silently**; a
  resolution/Redis **exception** allows but logs at WARNING ("backstop BLIND") and
  increments `tool-budget:resolution_errors`. Test both paths separately: assert the
  no-session path is silent, and the infra-error path logs loudly + increments.
- [ ] **Inline deny by default + auto-pause gate:** with `TOOL_BUDGET_ENABLED` on
  (default) an over-budget session is **denied** (SDK block / CLI exit 2), increments
  `tool-budget:tripped`, WARNING-logs, and sets the `budget_tripped` flag — the
  `status` is UNTOUCHED (no `paused_budget`, no Telegram) while `TOOL_BUDGET_AUTO_PAUSE`
  is unset. With `TOOL_BUDGET_AUTO_PAUSE=1`, the deny ALSO transitions status →
  `paused_budget` + queues Telegram. `TOOL_BUDGET_ENABLED=false` → always allow. Test
  all three switch states on both hook surfaces.
- [ ] **Drip-exclusion (BLOCKER):** `reflections/agents/session_recovery_drip.run()`
  MUST NOT resume a budget-tripped session — assert it does NOT drip a `paused_budget`
  session to `pending` (it filters only `paused`/`paused_circuit`), and does NOT touch
  a flag-only `budget_tripped` session whose status is still `running`. This proves no
  `pending→denied→paused→pending` runaway can form.
- [ ] CLI-hook exit-2 must **propagate through the module-level `except Exception`**
  (SystemExit is not an Exception) while a check-internal bug is **swallowed →
  exit 0 → allow**. Test (budget enabled — the default): a forced deny yields exit code 2;
  a forced check bug yields exit code 0 (fail-open).
- [ ] Budget deny **surfaces to the human**: on EVERY deny (default) the hook writes
  the race-free `budget_tripped` + `budget_tripped_reason` fields (NEVER a `status`
  write from the hook — that would race the granite adapter's partitioned
  `update_fields` saves, concern #2). **Only under `TOOL_BUDGET_AUTO_PAUSE`** does the
  deny additionally transition status → `paused_budget` (via the status owner) + queue
  a Telegram reaction/steer. All surfacing is fail-quiet — a surfacing error never
  flips the deny to allow or crashes. Test: flag set on default deny; under AUTO_PAUSE
  the `paused_budget` transition + Telegram fire once (dedup); a surfacing exception
  does not block the deny.
- [ ] No new `except Exception: pass` — every swallow emits a `logger.warning` with
  the owner/session id. Test captures the record.

### Empty/Invalid Input Handling
- [ ] Missing `worker:loop_beacon` (worker never started / TTL expired) is treated
  as "no beacon" → `loop_wedged` detection, NO reclaim, NO kill. Test both
  missing-key and expired-TTL.
- [ ] `armed=False` / age `None` beacon (loop not yet ticked) is NEVER treated as
  wedged. Test.
- [ ] Empty lease snapshot / empty reclaim-request list → the drain and the bridge
  check are no-ops. Test.
- [ ] `evaluate_tool_budget(None)` and a session with `tool_call_count=None` /
  `total_cost_usd=None` → `allow` (never a false deny on missing data). Test.
- [ ] Double reclaim-request for the same owner (bridge re-pushes before the worker
  drains) → the worker `registry.reclaim` is idempotent; `permits_free` unchanged
  after the first. Test.
- [ ] A burst of > `RECLAIM_REQUESTS_MAX` **distinct** owners pushed before a drain
  keeps `worker:slot:reclaim_requests` bounded (LTRIM cap, Race 4); a
  dropped-then-re-terminal owner is re-requested next tick and still reclaimed. Test.
- [ ] New-worker / old-bridge direction: the worker drains an always-empty request
  list, detects the contract gap, and emits `bridge_contract_stale` (action-log +
  counter) rather than silently dropping. Test the marker fires after the stale
  window with no bridge pushes.

### Error State Rendering
- [ ] A bridge-triggered reclaim emits a WARNING naming the owner + `bridge_reclaims`
  increment so `logs/worker.log` explains the recovery. Test captures it.
- [ ] A `loop_wedged` detection emits a WARNING + `loop_wedged_detected` increment
  and an action-log entry, and explicitly logs that it is deferring the kill. Test.
- [ ] A budget deny logs at WARNING with the session id, the tripped dimension
  (calls vs cost), and the value. Test captures it on both hook surfaces.

## Test Impact

- [ ] `tests/unit/test_session_watchdog.py` — UPDATE: the watchdog gains
  `check_worker_liveness_and_slots`; add coverage without disturbing the existing
  session-health assertions. No existing case changes behavior.
- [ ] `tests/integration/test_worker_concurrency.py` — UPDATE (Fix #5 only): after
  #1820 lands this file already targets `SlotLeaseRegistry`; add an assertion that a
  bridge-pushed reclaim-request frees a permit via the drain. No existing case is
  invalidated.
- [ ] `tests/unit/test_worker_deadman.py` — no change: Fix #5 reuses the beacon but
  does not alter the dead-man's-switch semantics. Verify the beacon-publish addition
  to `_write_worker_heartbeat` does not break the deadman cycle tests (they stub
  `_write_worker_heartbeat` / the Redis write). If they assert the exact call
  surface, UPDATE to tolerate the added Redis publish.
- [ ] `tests/unit/test_pre_tool_use_liveness_writes.py` — UPDATE: the SDK PreToolUse
  hook gains a budget check at the top; assert the liveness write still fires and
  the budget check is `allow` for an under-budget session (no behavior change for
  the common path).
- [ ] `.claude/hooks/pre_tool_use.py` has no dedicated test today — REPLACE/ADD: new
  greenfield tests below cover the CLI-hook budget block.
- [ ] `models/session_lifecycle.py` — UPDATE: add `paused_budget` to
  `NON_TERMINAL_STATUSES` + a human-only `RECOVERY_OWNERSHIP` entry. Any test that
  enumerates `ALL_STATUSES` / `NON_TERMINAL_STATUSES` (e.g.
  `tests/unit/test_session_lifecycle.py`) must tolerate the new status; UPDATE those
  membership assertions rather than pinning an exact frozenset.
- [ ] `tests/unit/test_session_recovery_drip.py` — UPDATE/ADD (**BLOCKER**): assert
  `session_recovery_drip.run()` does NOT re-queue a `paused_budget` session (nor a
  flag-only `budget_tripped` running session) to `pending` — the drip-exclusion that
  closes the flapping loop.

New tests (greenfield):
- `tests/unit/test_tool_budget.py` — `evaluate_tool_budget` ALLOW/DENY matrix:
  under budget → allow; `tool_call_count >= MAX` → deny; `total_cost_usd >= cap` →
  deny; `None` session / `None` fields → allow; `TOOL_BUDGET_ENABLED=false` →
  allow. **Acceptance #2 unit core.**
- `tests/integration/test_tool_budget_enforcement.py` — the SDK hook returns
  `{"decision":"block"}` and the CLI hook exits `2` for an over-budget session, and
  both proceed for an under-budget session; the deny path fires **with no
  background loop running** (the loop-independence property — construct the verdict
  and invoke the hook directly, asserting the block without any health/timeout loop
  task alive). ALSO: the fail-open split (no-session → silent allow; injected infra
  error → allow + loud WARNING + `resolution_errors` increment); the CLI exit-2
  propagation vs. check-bug fail-open (exit 2 vs exit 0); the **inline deny fires by
  default** (`TOOL_BUDGET_ENABLED` on, `TOOL_BUDGET_AUTO_PAUSE` unset → block + flag,
  status UNTOUCHED); and, under `TOOL_BUDGET_AUTO_PAUSE=1`, the deny-surfacing
  (status → `paused_budget` + Telegram queued once, surfacing error fail-quiet).
  **Acceptance #2.**
- `tests/integration/test_out_of_domain_reclaim.py` (Fix #5, after #1820) — orphan a
  slot (bind a lease to a session, transition it terminal without releasing);
  publish a fresh `worker:loop_beacon` + a `worker:slot:leases` snapshot; run
  `check_worker_liveness_and_slots()` **from the test process (NOT the worker
  loop)** and assert it pushes a reclaim-request; then run the worker-side drain and
  assert `permits_free` recovers. **This proves recovery is driven from a non-worker
  process — Acceptance #1.** ALSO: a **stale** beacon → `loop_wedged` action logged,
  NO reclaim-request, NO kill signal (assert no `worker:watchdog:critical` written).
  ALSO: `BRIDGE_SLOT_RECLAIM_ENABLED=0` → detection/logging still runs, no
  reclaim-request pushed. ALSO: a burst of > `RECLAIM_REQUESTS_MAX` distinct owners
  keeps the list bounded (Race 4 LTRIM) and completes the push under a wall-clock bound
  (concern #4 non-blocking async push); a drained owner whose `get_by_id → None`/raises
  is SKIPPED not reclaimed (concern #2/#1868); a new-worker/old-bridge scenario (worker
  drains an always-empty list) emits `bridge_contract_stale` (keyed on the reused
  `BRIDGE_WORKER_BEACON_STALE_S` threshold) rather than silently dropping.
- `tests/unit/test_worker_liveness_beacon_publish.py` (Fix #5) — `_write_worker_heartbeat`
  publishes a wall-clock `worker:loop_beacon` (assert `wall_ts` is `time.time()`-
  shaped, NOT a monotonic value) with the correct TTL; a Redis error is swallowed
  and the disk write still happens.

## Rabbit Holes

- **Do NOT try to release the worker's `asyncio.Semaphore` from the bridge.** It is
  loop-affine; cross-process release is undefined. The bridge only *requests* a
  reclaim via Redis; the release runs on the worker loop (Data Flow step 6).
- **Do NOT read `last_loop_tick` cross-process.** It is `monotonic()` — meaningless
  outside the worker. Publish a wall-clock beacon instead (Risk 1).
- **Do NOT build a second kill ladder in the bridge.** Process recovery belongs to
  the dead-man's-switch + `worker_watchdog.py`. The bridge detects + reclaims +
  defers; it never `os.kill`s (SIGKILL/SIGABRT), `launchctl`s, or writes `worker:watchdog:critical`.
- **Do NOT make Fix #6 a background monitor.** The whole point is a synchronous
  in-path deny that fires when loops are frozen. The existing
  `_agent_session_tool_timeout_loop` is the background monitor; Fix #6 is the inline
  complement, not a replacement for it.
- **Do NOT persist the beacon/lease/request keys without a TTL.** A dead worker's
  stale lease snapshot must expire so the bridge sees "no beacon," not a phantom
  live registry.
- **Do NOT let the budget hook brick a session.** Fail open on any error — the
  budget is a backstop, and a hook that raises would wedge every tool call.
- **Do NOT duplicate #1820's autonomous reclaim.** The bridge drain is a distinct,
  request-driven path; both converge on idempotent `registry.reclaim()`.

## Risks

### Risk 1: Monotonic-vs-wall-clock beacon confusion (the #1 design risk)
**Impact:** If the beacon publishes a raw `monotonic()` value, the bridge's
`now − beacon` math is nonsense (two unrelated clocks) → false "loop wedged" or
false "fresh" verdicts.
**Mitigation:** The beacon key stores `time.time()` (wall-clock) as `wall_ts`; the
bridge keys freshness ONLY on `wall_ts`. The monotonic beacon age is carried as an
advisory `loop_beacon_age_s` field but never used for cross-process time math. A
unit test asserts `wall_ts` is wall-clock-shaped (close to `time.time()`), not a
small monotonic uptime value.

### Risk 2: Redis-mediated reclaim races the worker's autonomous reclaim
**Impact:** The bridge pushes a reclaim-request for owner X the same tick the
on-loop reaper autonomously reclaims X → double reclaim.
**Mitigation:** `registry.reclaim()` is idempotent on `owner_session_id` (#1820
guarantee) — the second call finds no lease and no-ops. `permits_free` is never
over-released. Test concurrent fire.

### Risk 3: Bridge reclaims a slot whose owner is not actually terminal (incl. transient-None)
**Impact:** A stale lease snapshot lists owner X as held; X's DB row is terminal in
the snapshot but X was re-created/reused → wrong reclaim. **Sharper variant
(concern #2, #1868):** a transient Redis lookup failure makes `get_by_id(X) → None`;
if `None` were treated as terminal (as the autonomous reaper does at `:2585`), the
drain would reclaim a LIVE session's permit → semaphore over-admission.
**Mitigation:** The worker-side drain RE-READS owner status fresh and reclaims ONLY on
an EXPLICIT terminal status; `None` or a lookup exception is treated as "unknown →
SKIP, do not reclaim" (a deliberate divergence from the reaper's None-handling). The
bridge only *requests*; the worker *decides*. Test both a non-terminal requested owner
AND a `get_by_id → None`/exception requested owner are skipped (permit NOT stripped).

### Risk 4: Budget false-positive kills a legitimate long session
**Impact:** A legitimately large session (big refactor, many tool calls) hits the
cap and is denied further tools mid-task.
**Mitigation:** `MAX_TOOL_CALLS_PER_SESSION` (1000) and `SESSION_COST_CAP_USD`
($50) ship **conservative-provisional** — well above any observed healthy session —
and are env-tunable. `TOOL_BUDGET_ENABLED=false` is the instant kill-switch. The
deny reason is explicit so the human can raise the cap and resume. Tune after
observing real per-session distributions.

## Race Conditions

### Race 1: Bridge reads the lease snapshot mid-publish
**Location:** bridge `check_worker_liveness_and_slots` read vs worker reap-pass
publish of `worker:slot:leases`.
**Trigger:** the bridge `GET`s the key while the worker `SET`s a new snapshot.
**Data prerequisite:** the snapshot is written with a single atomic `SET` of a
complete JSON blob (never field-by-field).
**Mitigation:** a single-`SET` publish means the bridge always reads a
self-consistent snapshot (old or new, never partial). A slightly-stale snapshot is
harmless — the worker drain re-reads owner status fresh.

### Race 2: Reclaim-request drained by the worker after the owner's slot already freed
**Location:** worker drain of `worker:slot:reclaim_requests` vs the owner's normal
`registry.release`.
**Trigger:** the session completed and released its slot between the bridge push
and the worker drain.
**Data prerequisite:** the lease map is the single source of truth.
**Mitigation:** `registry.reclaim()` on an owner with no lease is a no-op
(idempotent). No over-release. Test.

### Race 3: Beacon TTL expiry races a slow worker tick
**Location:** bridge beacon-freshness check vs worker beacon-publish cadence.
**Trigger:** a legitimately slow (but not wedged) tick lets `wall_ts` age past the
TTL/threshold → false `loop_wedged`.
**Data prerequisite:** `BRIDGE_WORKER_BEACON_STALE_S` ≥ several multiples of
`WORKER_HEARTBEAT_INTERVAL` + the bridge tick.
**Mitigation:** the threshold is conservative (like #1815's
`WORKER_DEADMAN_STALENESS_THRESHOLD=90`), and a `loop_wedged` detection takes NO
destructive action — it only logs + defers to the existing killer, so a false
positive is observability noise, not a bad kill. Tune after observing real tick
cadence.

### Race 4: Unbounded `reclaim_requests` growth under a multi-owner leak burst
**Location:** bridge push to `worker:slot:reclaim_requests:{host}` vs the worker
drain cadence.
**Trigger:** many distinct owners leak at once while the worker tick is
slow-but-not-wedged (the loop still ticks, so the beacon stays fresh and the bridge
keeps pushing). The per-owner `SET NX` dedup prevents *duplicate* entries for the
same owner but does NOT bound the list across *distinct* owners — so, absent a cap,
the list could grow to (leaked owners × unexpired dedup windows).
**Data prerequisite:** unlike `worker:watchdog:actions` (explicitly `LTRIM`'d), the
round-1 reclaim-request list had NO list-level cap.
**Mitigation:** after each `LPUSH`, `LTRIM worker:slot:reclaim_requests:{host} 0
RECLAIM_REQUESTS_MAX-1` (default 256, mirroring the `worker:watchdog:actions`
bound), and set a TTL so a dead worker's backlog expires. Oldest requests drop
first; because the worker drain re-reads owner status fresh and `registry.reclaim()`
is idempotent, a dropped-then-re-requested owner is harmless (a still-terminal owner
is simply re-requested next tick). Test: a burst of > `RECLAIM_REQUESTS_MAX` distinct
owners keeps the list length bounded.

## No-Gos (Out of Scope)

- Fix #5 BUILD before the `SlotLeaseRegistry` / reap-pass / `registry.reclaim()`
  surfaces exist — this gate is now SATISFIED (#1820 merged, PR #1867), so the
  ordering constraint is discharged; retained here as the historical record.
- A second process-kill ladder in the bridge — process recovery stays with the
  dead-man's-switch + `worker_watchdog.py`. The bridge detects + reclaims + defers.
- Persisting the lease registry or beacon across worker restarts — records are
  TTL'd and rebuilt fresh; startup recovery re-queues running sessions.
- Cross-host / multi-worker slot coordination — single-machine ownership holds
  (one worker per host); the beacon/lease keys are per-host.
- Replacing `_agent_session_tool_timeout_loop` — Fix #6 is an inline complement,
  not a replacement for the background per-tier timeout.
- Final tuning of `MAX_TOOL_CALLS_PER_SESSION` / `SESSION_COST_CAP_USD` /
  `BRIDGE_WORKER_BEACON_STALE_S` to production-observed values — defaults ship
  conservative; tightening waits on live histograms (same posture as #1815).

## Update System

No update-script or data-migration changes required. All new cross-process state is
TTL'd Redis keys — no Popoto model for the Redis contract. The new env vars
(`BRIDGE_SLOT_RECLAIM_ENABLED`, `BRIDGE_WORKER_BEACON_STALE_S`,
`RECLAIM_REQUESTS_MAX`, `TOOL_BUDGET_ENABLED`,
`TOOL_BUDGET_AUTO_PAUSE`, `MAX_TOOL_CALLS_PER_SESSION`,
`SESSION_COST_CAP_USD`) are
all optional with safe defaults; add each to `.env.example` with a comment line
above (completeness-check requirement) for operator discoverability only — no
`.env` propagation needed. **Two new `AgentSession` fields (concern #2):**
`budget_tripped` (bool, default `False`) and `budget_tripped_reason` (str, default
`""`/`None`) are added to `models/agent_session.py` as the hook-owned deny-surfacing
fields (the hook-owned deny-surfacing signal). Because Popoto fields are schema-on-read
and both default falsy, existing records read the default when the field is absent —
**no data-backfill migration is required** and no `scripts/update/migrations.py` entry
is needed; the fields simply appear on new saves. **A new lifecycle status
`paused_budget` is ALSO added** to `models/session_lifecycle.py`
(`NON_TERMINAL_STATUSES` + a human-only `RECOVERY_OWNERSHIP` entry) so the auto-pause
path never lands a session in a drip-eligible state. This is a code-enum addition (a
frozenset + dict literal), not a persisted-schema change — existing rows are unaffected
and it carries no migration surface. **These ARE model/enum changes** (correcting any
"zero model change" reading): two additive `AgentSession` fields plus one status-enum
entry, all backward-compatible with no data migration. Both the **bridge** and the **worker** are restarted by
the standard `./scripts/valor-service.sh restart` after merge (Fix #5 touches both
processes: worker publishes/drains, bridge reads) — no new deploy step in
`scripts/update/run.py`. **Operational note for the builder — both mixed-version
directions:** because Fix #5 spans the bridge and worker, both must run the new code
for the contract to function. Each direction degrades safely, and BOTH must be
detectable — a silent drop is not acceptable:

- **Old worker / new bridge:** the old worker publishes no beacon and no lease
  snapshot → the new bridge sees "no beacon" → records `loop_wedged` and defers,
  taking no destructive action. Detectable: the `loop_wedged_detected` counter rises
  on the dashboard.
- **New worker / old bridge:** the new worker publishes the beacon + lease snapshot
  and drains `worker:slot:reclaim_requests`, but the old bridge never *pushes*
  requests — so the drain is a harmless no-op (empty list). This direction is
  otherwise the silent one, so the **worker emits an operator-visible signal when it
  detects a leak it would expect a bridge request for but the request channel has
  stayed empty past a concrete threshold** (concern #5): when a terminal-owner leak
  is observed AND `now − worker:slot:last_reclaim_request_drain:{host} >
  BRIDGE_WORKER_BEACON_STALE_S` (the existing beacon-freshness threshold — reused, no
  new var), the worker
  publishes a `bridge_contract_stale` marker (once, dedup `SET NX EX`) into
  `worker:watchdog:actions` + a `bridge_contract_stale` counter surfaced on the
  dashboard. It is one timestamp compared to one threshold — no per-owner bookkeeping
  and no separate detector loop. This makes a
  never-draining request channel (old bridge, or a bridge that stopped pushing)
  detectable rather than a silent drop. The autonomous #1820 reaper still reclaims
  the leak in this direction (unless `SLOT_LEASE_REAP_DISABLED=1`), so no slot is
  lost — the signal exists so the *contract gap* is visible.

(The contract is intentionally NOT hard version-gated — an explicit version field
would add a migration surface for no safety gain, since both directions already
degrade safely; the requirement satisfied here is *detectability*, per the concern.)

## Agent Integration

No new CLI entry point in `pyproject.toml [project.scripts]` and no MCP surface.
Fix #6 is invoked **by the agent implicitly** — every tool call the agent makes
traverses the PreToolUse hooks, which is precisely why the budget must live there
(the only place guaranteed to run inline on the agent's own tool dispatch). Fix #5
is bridge/worker-internal; the bridge already imports and launches
`monitoring/session_watchdog.py` (`bridge/telegram_bridge.py:3053-3055`), so the
new `check_worker_liveness_and_slots` call needs no new wiring beyond that loop.

**Operator surface (both fixes).** Surface the recovery/budget state on the
existing `localhost:8500/dashboard.json` `worker` block (additive fields only,
`_get_worker_health` at `ui/app.py:370`): `permits_free`/`held` (from
`worker:slot:leases`), `bridge_reclaims`, `loop_wedged_detected`,
`bridge_contract_stale`, `tool_budget_tripped`, `tool_budget_resolution_errors`
counters, plus the last few `worker:watchdog:actions` entries. A rising
`bridge_reclaims` signals a recurring leak the on-loop reaper isn't catching; a
rising `tool_budget_tripped` signals sessions hitting the cap; a rising
`bridge_contract_stale` signals a mixed-version deploy (new worker / old bridge); a
rising `tool_budget_resolution_errors` signals the budget backstop is going blind on
session resolution.

**Integration test wiring.** `test_tool_budget_enforcement.py` verifies the agent's
own PreToolUse dispatch is actually gated (both hook surfaces block over-budget);
`test_out_of_domain_reclaim.py` verifies the bridge-domain recovery path.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/out-of-domain-recovery.md` describing: the Redis-mediated
  cross-process contract (`worker:loop_beacon`, `worker:slot:leases`,
  `worker:slot:reclaim_requests` — all per-host, TTL'd, wall-clock), why the bridge
  cannot touch the in-memory registry or read `last_loop_tick`, the four-actor
  recovery ownership boundary (on-loop reaper / dead-man's-switch / `worker_watchdog.py`
  / bridge `session_watchdog`) with the no-second-kill rule, the synchronous
  per-tool budget (omnigent ALLOW/DENY model, both hook surfaces, the
  no-session-vs-infra-error fail-open split, the two-switch model —
  `TOOL_BUDGET_ENABLED` fires the inline deny by default, `TOOL_BUDGET_AUTO_PAUSE`
  (default off) gates only the `status → paused_budget` transition + Telegram — the
  non-drip `paused_budget` status and why bare `paused` would flap via
  `session_recovery_drip`, the human deny-surfacing, the CLI exit-2 propagation), the
  drain's None-as-unknown skip (#1868), the mixed-version-deploy detectability in both
  directions (`bridge_contract_stale`, keyed on the reused `BRIDGE_WORKER_BEACON_STALE_S`
  threshold), the `reclaim_requests` LTRIM cap (Race 4), the env kill-switches with
  provisional defaults, and the dashboard operator surface.
  State it is the continuation of `worker-liveness-recovery.md` (#1815) and
  `slot-lease-ownership.md` (#1820).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Forward-link from `docs/features/slot-lease-ownership.md` (#1820) and
  `docs/features/worker-liveness-recovery.md` (#1815) to this doc — describe the
  new status quo (bridge-domain reclaim-trigger + inline budget), per the
  no-historical-artifacts rule.

### Inline Documentation
- [ ] Comment the wall-clock-vs-monotonic beacon distinction on the publish site
  (why `wall_ts` is `time.time()` and the monotonic age is advisory only).
- [ ] Comment the no-second-kill boundary on `check_worker_liveness_and_slots` (why
  it defers process recovery to the existing killers).
- [ ] Comment each new budget/threshold constant with the grain-of-salt
  "provisional, tune after observing real rates" note.

## Success Criteria

- [ ] **Acceptance #1:** Recovery is verified to run from a process OTHER than the
  worker loop — `tests/integration/test_out_of_domain_reclaim.py` runs
  `check_worker_liveness_and_slots()` from the test process (not the worker loop),
  asserts it pushes a reclaim-request for a terminal-owner lease, and the worker-side
  drain then frees the slot without a restart.
- [ ] **Acceptance #2:** A per-tool-call budget fires from inside the execution path
  independent of the health loop — `tests/integration/test_tool_budget_enforcement.py`
  asserts the SDK hook blocks (`decision:block`) and the CLI hook exits 2 for an
  over-budget session, with no background loop running.
- [ ] Fix #5 is Redis-mediated: `grep -c "worker:loop_beacon" agent/session_health.py`
  `> 0`, `grep -c "worker:slot:leases" agent/session_health.py > 0`,
  `grep -c "worker:slot:reclaim_requests" agent/session_health.py > 0`.
- [ ] The beacon is wall-clock, never monotonic cross-process:
  `test_worker_liveness_beacon_publish.py` asserts `wall_ts ≈ time.time()`.
- [ ] The bridge check runs no kill: `grep -Ec "os\.kill|launchctl|SIGKILL|SIGABRT|watchdog:critical"`
  over the new `check_worker_liveness_and_slots` function `== 0`.
- [ ] The reclaim-request drain works under `SLOT_LEASE_REAP_DISABLED=1` (distinct
  path from autonomous reclaim) — asserted in `test_out_of_domain_reclaim.py`.
- [ ] Fix #6 is synchronous / not a background monitor: `agent/tool_budget.py`
  exists (`grep -c "def evaluate_tool_budget" agent/tool_budget.py > 0`), has no
  `asyncio`/`Thread`/`sleep` (`grep -Ec "asyncio|Thread|time\.sleep" agent/tool_budget.py == 0`).
- [ ] Both PreToolUse surfaces call the evaluator:
  `grep -c "evaluate_tool_budget" agent/hooks/pre_tool_use.py > 0` and
  `grep -c "evaluate_tool_budget" .claude/hooks/pre_tool_use.py > 0`.
- [ ] Fix #6 fails open (a hook error never bricks a session) — asserted in
  `test_tool_budget_enforcement.py`.
- [ ] Kill-switches work: `BRIDGE_SLOT_RECLAIM_ENABLED=0` → detect/log only, no
  reclaim-request; `TOOL_BUDGET_ENABLED=false` → always allow.
- [ ] **Inline deny fires by default:** with `TOOL_BUDGET_ENABLED` on (default) and
  `TOOL_BUDGET_AUTO_PAUSE` unset, an over-budget session is denied (block/exit 2) +
  `budget_tripped` flag set, but `status` is UNTOUCHED — asserted in
  `test_tool_budget_enforcement.py`.
- [ ] **Drip-exclusion (BLOCKER):** `session_recovery_drip.run()` does NOT re-queue a
  `paused_budget` (nor a flag-only `budget_tripped` running) session to `pending` —
  `grep -c "paused_budget" models/session_lifecycle.py > 0` and asserted in
  `test_session_recovery_drip.py`. No `pending→denied→paused→pending` loop.
- [ ] Drain None-safety (concern #2/#1868): a `get_by_id → None`/error requested owner
  is SKIPPED, not reclaimed — asserted in `test_out_of_domain_reclaim.py`.
- [ ] Bridge push is non-blocking (concern #4): the multi-owner burst push completes
  under a wall-clock bound — asserted in `test_out_of_domain_reclaim.py`.
- [ ] Fix #6 fail-open distinguishes no-session (silent allow) from infra error
  (loud WARNING + `tool_budget_resolution_errors` increment) — asserted in
  `test_tool_budget_enforcement.py`.
- [ ] Budget deny surfaces to the human (session annotated + Telegram signal queued,
  fail-quiet) — asserted in `test_tool_budget_enforcement.py`.
- [ ] `reclaim_requests` list is capped (Race 4 LTRIM) and new-worker/old-bridge
  emits `bridge_contract_stale` — asserted in `test_out_of_domain_reclaim.py`.
- [ ] Operator surface: `grep -Ec "bridge_reclaims|loop_wedged_detected|bridge_contract_stale|tool_budget_tripped|tool_budget_resolution_errors|permits_free" ui/app.py > 0`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`): `docs/features/out-of-domain-recovery.md`
  exists.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The
lead NEVER builds directly.

### Team Members

- **Builder (tool-budget)**
  - Name: budget-builder
  - Role: Fix #6 — `agent/tool_budget.py` evaluator + wire both PreToolUse hooks
    (SDK block, CLI exit-2, fail-open) + `tool_budget_tripped` counter. **No #1820
    dependency — builds first.**
  - Agent Type: builder
  - Resume: true

- **Builder (out-of-domain-recovery)**
  - Name: recovery-builder
  - Role: Fix #5 — Redis beacon/lease publish + reclaim-request drain (worker) +
    `check_worker_liveness_and_slots` (bridge) + dashboard surface. **BUILD gate
    satisfied — #1820 merged (PR #1867); may build now.**
  - Agent Type: builder
  - Domain: cross-process / async concurrency (loop-affine objects, Redis contract,
    no-second-kill boundary)
  - Resume: true

- **Validator (resilience)**
  - Name: resilience-validator
  - Role: Runs the two independent sub-pipeline validations then the final sweep —
    `validate-tool-budget` (Acceptance #2, fail-open split, deny-surfacing, exit-2
    propagation) gated only on the Fix #6 build; `validate-recovery` (Acceptance #1,
    Fix #5 failure paths, four race scenarios) gated only on the Fix #5 build; then
    `validate-all` once both PRs land.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: recovery-doc
  - Role: `docs/features/out-of-domain-recovery.md` + index + forward-links.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build the synchronous per-tool budget (Fix #6 — independent, first)
- **Task ID**: build-tool-budget
- **Depends On**: none (NO #1820 dependency)
- **Validates**: tests/unit/test_tool_budget.py (create), tests/integration/test_tool_budget_enforcement.py (create)
- **Assigned To**: budget-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/tool_budget.py` with `BudgetVerdict` + `evaluate_tool_budget`
  (pure, synchronous, fail-safe on `None`); constants
  `MAX_TOOL_CALLS_PER_SESSION` / `SESSION_COST_CAP_USD` (**SDK/headless-path-only —
  no-op on granite**) / `TOOL_BUDGET_ENABLED` (**DEFAULT ON — gates the inline deny**) /
  `TOOL_BUDGET_AUTO_PAUSE` (**DEFAULT OFF — gates only the status→paused_budget +
  Telegram extras**) (all provisional, commented). The evaluator returns a pure
  verdict; the caller actuates the inline deny (by default) and the auto-pause extras
  (only when opted in).
- Add the two hook-owned `AgentSession` fields `budget_tripped` (bool, default
  `False`) + `budget_tripped_reason` (str) to `models/agent_session.py` (concern #2 —
  additive, falsy-default, no data migration).
- Add the `paused_budget` lifecycle status to `models/session_lifecycle.py`
  (**BLOCKER**): include it in `NON_TERMINAL_STATUSES` and add
  `RECOVERY_OWNERSHIP["paused_budget"] = "human"` (human-only — NO auto-drip owner).
  Because `reflections/agents/session_recovery_drip.run()` re-queues only
  `status="paused"`/`"paused_circuit"`, `paused_budget` is never auto-resumed — closing
  the `pending→denied→paused→pending` runaway. Add
  `tests/unit/test_session_recovery_drip.py` drip-exclusion coverage.
- Wire the SDK hook (`agent/hooks/pre_tool_use.py::pre_tool_use_hook`, top, before
  the write-capable filter): resolve via `AGENT_SESSION_ID` with the
  **no-session-vs-infra-error split** (no-session → silent allow; infra exception →
  allow + loud WARNING + `tool-budget:resolution_errors` increment); on a deny verdict
  on a deny verdict (under `TOOL_BUDGET_ENABLED`, default on) ALWAYS return
  `{"decision":"block","reason":...}`, increment `{project_key}:tool-budget:tripped`
  (dedup) + WARNING, and set the hook-owned `budget_tripped`/`budget_tripped_reason`
  via `save(update_fields=[...])` — **NEVER a `status` write from the hook (concern
  #2)**. **Only when `TOOL_BUDGET_AUTO_PAUSE` is set** does the deny ALSO drive the
  status→`paused_budget` transition (via the status owner — adapter/worker on granite,
  hook on SDK) + queue Telegram, fail-quiet — Data Flow step 4.
- Wire the CLI hook (`.claude/hooks/pre_tool_use.py::main`, top): resolve via the
  sidecar path (`_load_agent_session_sidecar` → `AgentSession.get_by_id`) with the
  same no-session-vs-infra split; on a deny verdict (under `TOOL_BUDGET_ENABLED`,
  default on) ALWAYS print the reason to stderr + `sys.exit(2)`, increment
  `tool-budget:tripped` + WARNING, and set the `budget_tripped` flag. **Only when
  `TOOL_BUDGET_AUTO_PAUSE` is set** does the deny ALSO drive status→`paused_budget` +
  Telegram (Data Flow step 4). The budget check lives **inside `main()`** so a genuine deny
  (`SystemExit(2)`) propagates through the module-level `except Exception` wrapper
  while a check-internal bug is swallowed → exit 0 → **fails open**. Do NOT wrap the
  deny in an `except BaseException`/bare `except`.
- Verify: `grep -c "evaluate_tool_budget"` in both hooks `> 0`; `grep -c
  "TOOL_BUDGET_AUTO_PAUSE"` in both hooks `> 0`; `grep -c "paused_budget"
  models/session_lifecycle.py > 0`; no `asyncio/Thread/sleep` in `agent/tool_budget.py`;
  the inline-deny-by-default, auto-pause gate, drip-exclusion, fail-open split, and
  exit-2 propagation are covered by `test_tool_budget_enforcement.py` +
  `test_session_recovery_drip.py`.

### 2. Build the out-of-domain recovery (Fix #5 — #1820 merged, gate satisfied)
- **Task ID**: build-out-of-domain-recovery
- **Depends On**: build-tool-budget (**#1820 merged — PR #1867, gate satisfied**)
- **Validates**: tests/integration/test_out_of_domain_reclaim.py (create), tests/unit/test_worker_liveness_beacon_publish.py (create)
- **Assigned To**: recovery-builder
- **Agent Type**: builder
- **Parallel**: false
- **Gate (now passing):** re-confirm the #1820 surfaces before starting —
  `grep -c "class SlotLeaseRegistry" agent/slot_lease.py > 0` (currently 1),
  `_reap_slot_leases()` + `registry.reclaim()` present in `agent/session_health.py`.
  These pass as of HEAD `2d1cf419`; the guard remains only as a defensive re-check.
- Worker publish: add the wall-clock `worker:loop_beacon:{host}` write to
  `agent/session_health.py::_write_worker_heartbeat` (`:3386`); add the
  `worker:slot:leases:{host}` snapshot publish + the `worker:slot:reclaim_requests:{host}`
  drain (→ fresh-status re-read → reclaim ONLY on explicit-terminal, treat `None`/error
  as unknown→SKIP per concern #2/#1868 → `bridge_reclaims` counter) to the #1820 on-loop
  reap pass `_reap_slot_leases()`. **The drain MUST be inserted in
  the always-run region: AFTER Phase 1 detection ends (`:2574`) and BEFORE the
  Phase-2 `if reap_disabled: return` (`:2576-2578`), NOT in/after the Phase-2 `for
  lease in leases_snapshot:` block at `:2580+` (concern #5)** — otherwise the drain is
  skipped exactly when `SLOT_LEASE_REAP_DISABLED=1`, defeating the feature's headline
  capability. Emit `bridge_contract_stale` (action-log + counter, dedup `SET NX EX`)
  when a terminal-owner leak is observed AND `now − last_reclaim_request_drain_ts >
  BRIDGE_WORKER_BEACON_STALE_S` (the existing beacon-freshness threshold, reused — no
  new staleness var, concern #5). All fail-quiet, all TTL'd.
- Bridge: add `check_worker_liveness_and_slots()` to
  `monitoring/session_watchdog.py`; call it from `watchdog_loop` (own try/except).
  Fresh beacon + explicit-terminal lease owner (None/error → skip) → push reclaim-request
  via the **async Redis client or a single pipeline** (concern #4 — never N sequential
  sync `socket_timeout=5` calls in the async loop) (`LPUSH` +
  `LTRIM ... 0 RECLAIM_REQUESTS_MAX-1`, Race 4) + action-log entry
  (gated `BRIDGE_SLOT_RECLAIM_ENABLED`); stale/missing beacon → `loop_wedged`
  action + counter, **NO kill**. No `os.kill`/`launchctl`/`critical` key.
- Dashboard: extend `_get_worker_health()` (`ui/app.py:370`) with `permits_free`/
  `held`/`bridge_reclaims`/`loop_wedged_detected`/`bridge_contract_stale`/
  `tool_budget_tripped`/`tool_budget_resolution_errors` + recent actions (additive
  only).

### 3. Validate Fix #6 (sub-pipeline A)
- **Task ID**: validate-tool-budget
- **Depends On**: build-tool-budget
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Fix #6 tests only; verify **Acceptance #2** (both hook surfaces block
  over-budget with no loop running), the fail-open no-session-vs-infra split, the
  deny-surfacing to the human, and the CLI-hook exit-2 propagation. Confirm no
  regression in `test_pre_tool_use_liveness_writes.py`. **Independent of Fix #5 /
  #1820 — runs as soon as `build-tool-budget` lands.**

### 4. Validate Fix #5 (sub-pipeline B)
- **Task ID**: validate-recovery
- **Depends On**: build-out-of-domain-recovery
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Fix #5 tests only; verify **Acceptance #1** (recovery driven from a
  non-worker process), all Fix #5 failure-path items, and the **four** race
  scenarios (including the Race 4 `reclaim_requests` LTRIM cap). Confirm no
  regression in the existing watchdog / concurrency / deadman / liveness-writer
  tests. **Gated on #1820 merged (inherited from `build-out-of-domain-recovery`).**

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tool-budget, validate-recovery
- **Assigned To**: recovery-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/out-of-domain-recovery.md`; add the README index entry;
  forward-link the #1815 and #1820 docs.

### 6. Final Validation (both sub-pipelines landed)
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full verification table across BOTH fixes; confirm the doc deliverable
  exists; generate the final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tool-budget module exists (Fix #6) | `grep -c "def evaluate_tool_budget" agent/tool_budget.py` | output > 0 |
| Budget is synchronous, not a monitor | `grep -Ec "asyncio\|Thread\|time\.sleep" agent/tool_budget.py` | == 0 |
| SDK hook enforces budget | `grep -c "evaluate_tool_budget" agent/hooks/pre_tool_use.py` | output > 0 |
| CLI hook enforces budget | `grep -c "evaluate_tool_budget" .claude/hooks/pre_tool_use.py` | output > 0 |
| Budget unit test passes | `pytest tests/unit/test_tool_budget.py -q` | exit code 0 |
| Budget enforcement acceptance test passes | `pytest tests/integration/test_tool_budget_enforcement.py -q` | exit code 0 |
| Loop beacon published wall-clock (Fix #5) | `grep -c "worker:loop_beacon" agent/session_health.py` | output > 0 |
| Lease snapshot published | `grep -c "worker:slot:leases" agent/session_health.py` | output > 0 |
| Reclaim-request drain present | `grep -c "worker:slot:reclaim_requests" agent/session_health.py` | output > 0 |
| Bridge out-of-domain check exists | `grep -c "def check_worker_liveness_and_slots" monitoring/session_watchdog.py` | output > 0 |
| Bridge runs NO kill ladder (no-parallel-systems) | `sed -n '/def check_worker_liveness_and_slots/,/^def /p' monitoring/session_watchdog.py \| grep -Ec "os\.kill\|launchctl\|SIGKILL\|SIGABRT\|watchdog:critical"` | == 0 |
| Beacon publish is wall-clock | `pytest tests/unit/test_worker_liveness_beacon_publish.py -q` | exit code 0 |
| Out-of-domain reclaim acceptance test passes | `pytest tests/integration/test_out_of_domain_reclaim.py -q` | exit code 0 |
| Operator surface additive fields present | `grep -Ec "bridge_reclaims\|loop_wedged_detected\|bridge_contract_stale\|tool_budget_tripped\|tool_budget_resolution_errors\|permits_free" ui/app.py` | output > 0 |
| Lint clean | `python -m ruff check agent/ monitoring/ ui/ .claude/hooks/pre_tool_use.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ monitoring/ ui/` | exit code 0 |
| Feature doc exists | `test -f docs/features/out-of-domain-recovery.md && echo ok` | ok |

## Open Questions

1. **Load-bearing PreToolUse surface.** Production sessions run via the granite PTY
   interactive `claude` TUI (CLI hook) — but does that process reliably have the
   sidecar → `AgentSession` resolution available at PreToolUse time, and does an
   `exit 2` from the CLI PreToolUse hook cleanly deny the tool in the interactive
   TUI (vs. the headless SDK path)? The plan wires BOTH surfaces to be safe;
   confirm at build time which is authoritative and that the CLI block path works
   end-to-end in the granite PTY. (PM/builder verify.)
2. **RESOLVED — Fix #5 scope: detection + operator-surface primary, reclaim-request
   as a narrow secondary lever.** (Was: is the reclaim-request worth the coupling given
   the on-loop reaper already covers the live-loop case?) Resolved in favor of framing
   Fix #5 primarily as detection + operator-surface (beacon/lease read → `loop_wedged`
   record → dashboard → DEFER kill), with the reclaim-request retained ONLY for its two
   unique roles: (a) it is the sole reclaim lever under `SLOT_LEASE_REAP_DISABLED=1`
   (the autonomous reclaim action is gated off there — confirmed against the #1820
   plan), and (b) it makes Acceptance #1 ("recovery from a non-worker process")
   provable. The request is deliberately NOT positioned as beating the on-loop reaper
   in the live-loop common case. See the "Fix #5 — scope (OQ2 resolved)" block in
   **## Data Flow** for the full resolution.
