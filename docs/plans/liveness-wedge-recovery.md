---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-06-29
tracking: https://github.com/tomcounsell/ai/issues/1815
last_comment_id:
---

# Liveness-vs-Progress Wedge Recovery

## Problem

A worker event loop can wedge — process alive, loop frozen — and nothing recovers it.
launchd only restarts on process **exit**, and a wedged loop never exits. The system
is instrumented for this class (#1808/#1537/#1767) but not actually fixed: it detects
and logs, then does nothing.

**Current behavior:**
A session parks somewhere inside `_execute_agent_session` (a deadlocked PTY acquire, a
synchronous freeze, an `await semaphore.acquire()` on a leaked permit). The off-loop
heartbeat thread keeps writing "process alive" every 30s regardless of loop health, so
every external monitor sees green. The global concurrency slot stays held, the granite
PTY pool's permits exhaust, and the on-loop health monitor — which would notice — can't
run because it lives on the very loop that's frozen. The one detector that does fire
(`session_health.py:2561-2613`) only prints a fingerprint. Work stops accumulating
indefinitely with zero recovery.

**Desired outcome:**
A wedge becomes a recoverable event. A frozen loop self-kills (or goes stale) and is
respawned by launchd within a bounded window; a leaked slot is reclaimed without a
restart; a parked PTY acquirer times out and recycles; a no-progress session is
cancelled and its slot released; and the recovery logic provably runs from a process
*other* than the loop it polices.

## Freshness Check

**Baseline commit:** `b7fd781b`
**Issue filed at:** 2026-06-29T09:21:23Z
**Disposition:** Minor drift (line numbers shifted a few lines under prior edits; every claim holds)

**File:line references re-verified:**
- `worker/__main__.py:74-94` — off-loop `_heartbeat_thread_main`, unconditional writes — **holds** (thread started at `:530`).
- `agent/agent_session_queue.py:1330` — global semaphore acquire — **holds**; `:1494` bare `await _execute_agent_session(session)` — **holds**; `:1657` slot release — **holds**.
- `agent/granite_container/pty_pool.py:285,358,362` — unbounded `await` on `_sem.acquire()` / `_slot_available.wait()` / `slot.event.wait()` — **holds**; POOL-1 hazard documented `:38-52`, divergent-primitive wake at `:540-568` — **holds**.
- `agent/session_health.py:2561-2613` — logging-only leaked-slot fingerprint reading `_sem._value` — **holds**; `_agent_session_health_loop` on-loop at `:3020` — **holds**.
- `agent/session_state.py:75` — `_global_session_semaphore: asyncio.Semaphore | None` — **holds**.

**Cited sibling issues/PRs re-checked:**
- #1808 — closed investigation that produced this issue's root-cause analysis. Resolution: instrument-only (the gap this issue closes).
- #1767 — merged; introduced the off-loop heartbeat thread (FACT A) and the `worker_watchdog.py` stale-heartbeat ladder. Directly relevant: fix #1 *arms* that ladder.
- #1537 / #1172 — the leaked-slot ("running < slots_held") class and the wall-clock-timeout removal, respectively. Still describe current behavior.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none (`granite-send-cb-delivery-timeout-reflection-contention` is adjacent granite work but does not touch the wedge class).

**Notes:** Two pre-existing recovery surfaces discovered during recon reshape the work from
"build from scratch" to "arm and extend": `monitoring/worker_watchdog.py` (separate launchd
service, reads `data/last_worker_connected`, W1-W5 kill ladder) and `monitoring/session_watchdog.py`
(runs in the bridge process). Progress fields `last_tool_use_at` / `last_turn_at` already exist
on `AgentSession`.

## Prior Art

- **Issue #1808**: *Investigation: wedged-but-alive worker leaves sessions pending indefinitely despite 300s health backstop* — the investigation that mapped this class. Concluded the system is instrumented but not fixed. This plan is its remediation.
- **Issue #1767**: introduced the off-loop heartbeat thread (`_heartbeat_thread_main`) and `worker_watchdog.py`'s stale-heartbeat W1-W5 ladder. The off-loop thread is FACT A (the lie); the ladder is the recovery path fix #1 arms.
- **Issue #1537**: the "running_count < slots_held" leaked-permit class — produced the `_sem._value`-based fingerprint that fix #2 converts from log-only into a reclaim.
- **Issue #1172**: retired the wall-clock execution timeout, deliberately, because wall-clock is the wrong signal for progress cadence. Fix #3 must use a *progress* deadline, not re-add a wall-clock one.
- **Issue #1712**: the bridge's "update-loop wedged" detector (Telethon handler stopped firing) — precedent for loop-liveness detection from outside the frozen loop; conceptually mirrors fix #5 for the worker.

## Research

External patterns were mined in the issue itself (systemd, k8s, Erlang/OTP, Go, omnigent, jcode). One confirmatory search grounds fix #1's design.

**Queries used:**
- `systemd WatchdogSec sd_notify WATCHDOG=1 service self-monitoring restart semantics`

**Key findings:**
- systemd's watchdog is exactly the dead-man's-switch shape fix #1 needs: the service must emit a keep-alive (`sd_notify("WATCHDOG=1")`) from *live* code within `WatchdogSec`; a deadlocked/stuck loop fails to emit and the supervisor restarts it. Recommended emit interval is **half** the timeout. ([sd_notify](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html), [systemd watchdog health checks](https://oneuptime.com/blog/post/2026-03-02-how-to-configure-systemd-watchdog-for-service-health-checks-on-ubuntu/view)) — informs: the on-loop tick must come from live loop code (not a thread/timer), and the watchdog threshold should be ≥2× the tick interval. A `~5s` tick with a `~15s` self-kill threshold mirrors the "half-interval" guidance with margin.
- launchd has **no native `WatchdogSec`** equivalent. The systemd pattern must be emulated: the off-loop thread plays the role of the supervisor's timer (self-`SIGABRT` when the on-loop tick is stale), with `KeepAlive=true` + `ThrottleInterval=10` on `com.valor.worker.plist` providing the respawn — confirmed present. The existing `worker_watchdog.py` stale-heartbeat ladder is the slower out-of-process backstop.

## Data Flow

End-to-end path of a wedge and the recovery hooks this plan adds:

1. **Entry point**: `_worker_loop` (`agent_session_queue.py`) acquires the global semaphore (`:1330`), pops a session, transitions it `running`, and calls `await _execute_agent_session(session)` (`:1494`).
2. **Execution**: inside, the granite path calls `PTYPool.acquire_pair` (`pty_pool.py:285+`), which awaits `_sem.acquire()` then idle/respawn events. A respawn-task death leaves a slot `respawning` forever (POOL-1); the acquirer parks on `slot.event.wait()` (`:362`) which is never set.
3. **The wedge**: the park holds the granite permit AND the outer global semaphore slot (`:1330`, released only at `:1657`). No deadline anywhere cancels it.
4. **Today's monitors**: off-loop heartbeat thread (`worker/__main__.py:530`) keeps `data/last_worker_connected` fresh → `worker_watchdog.py` sees green. On-loop `_agent_session_health_loop` (`session_health.py:3020`) can't run if the loop is frozen; when it does run, `:2561-2613` logs a fingerprint and returns.
5. **Output (after this plan)**: an on-loop tick (`session_state.last_loop_tick`) goes stale → off-loop watchdog thread self-`SIGABRT`s → launchd respawns (fix #1). Independently, an off-loop lease reaper reclaims the leaked permit (fix #2); a bounded `wait_for` recycles the parked PTY slot (fix #4); a progress-deadline cancel scope cancels the no-progress session and force-releases its slot (fix #3); and `session_watchdog.py` in the bridge process owns cross-domain liveness + reclaim (fix #5).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1767 | Moved the heartbeat to an off-loop daemon thread so PTY/thread saturation couldn't starve it | Decoupled "process alive" from "loop making progress" — the heartbeat became a structural lie that hides the wedge from every downstream monitor (FACT A) |
| #1808 | Added the `_sem._value` leaked-slot fingerprint + asyncio set_debug | Detection-only by explicit design ("Logging-ONLY — the recovery decision is unchanged"). Runs on the frozen loop. No recovery (FACT D) |
| #1172 | Removed the wall-clock execution timeout | Correct to remove (wall-clock is wrong for progress cadence) but left **no** replacement deadline — execution now has no cancel scope at all (FACT B) |

**Root cause pattern:** every prior fix improved *detection* or *correctness of a signal* without ever converting a wedge into an **exit or a reclaim**. launchd recovers on exit; nothing in the wedged path exits or releases. The fixes here are deliberately recovery actions, not new detectors.

## Architectural Impact

- **New dependencies**: none external. `signal` (stdlib) for the `SIGABRT` self-kill; everything else is internal.
- **Interface changes**: `session_state` gains a `last_loop_tick` beacon + accessors; the ownerless `asyncio.Semaphore` is replaced by a lease-recording wrapper exposing the same `acquire`/`release` surface plus owner/deadline metadata. PTY `_sem` similarly. `pty_pool.acquire_pair` gains bounded internal waits (no signature change).
- **Coupling**: *decreases* failure-domain coupling — recovery moves off the policed loop (fix #5) and into launchd (fix #1). The lease registry is a new shared data structure read by an off-loop reaper.
- **Data ownership**: slot permits gain explicit owners (`session_id`, `acquired_at`, `deadline`); the lease registry becomes the source of truth for "who holds a slot."
- **Reversibility**: each fix is independently revertible and independently shippable in the landing order; env flags gate the aggressive behaviors (self-kill threshold, progress deadline, reclaim TTL) for conservative rollout.

## Appetite

**Size:** Large

**Team:** Solo dev, PM check-ins, code reviewer (async-specialist for the lease/cancel-scope work)

**Interactions:**
- PM check-ins: 2-3 (this is six fixes; landing order and "how far in this batch" are alignment calls)
- Review rounds: 2+ (concurrency-sensitive; lease semantics and the SIGABRT path warrant careful review)

This is a phased plan. Each phase is independently shippable in the recommended landing order. The appetite is Large because the *class* removal (fix #2, lease semaphore) touches the global concurrency primitive and the PTY pool, and because the recovery paths must be tested with real injected freezes/orphans.

## Prerequisites

No external prerequisites — all changes are internal to `worker/`, `agent/`, and `monitoring/`. launchd respawn capability is already present (`com.valor.worker.plist`: `KeepAlive=true`, `ThrottleInterval=10`).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worker plist has KeepAlive | `python -c "import pathlib,sys; t=pathlib.Path('com.valor.worker.plist').read_text(); sys.exit(0 if 'KeepAlive' in t else 1)"` | Confirms launchd respawns on SIGABRT exit (fix #1) |
| Progress fields exist | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'last_tool_use_at') and hasattr(AgentSession, 'last_turn_at')"` | Confirms fix #3 needs no new fields |

## Solution

### Key Elements

- **Loop-driven dead-man's-switch (fix #1)**: an on-loop task bumps `session_state.last_loop_tick` every ~5s; the off-loop watchdog thread writes the heartbeat green *only if the tick is fresh*, else self-`SIGABRT`s so launchd respawns. Converts wedge → crash → respawn. Arms the existing `worker_watchdog.py` ladder as a slower backstop.
- **Bounded PTY waits (fix #4)**: wrap every unbounded `await` in `pty_pool.py` (`:285,358,362`) with `asyncio.wait_for`; on timeout, force-recycle the slot stuck in `respawning` (resolves POOL-1). Smallest blast radius — lands second.
- **Progress-deadline cancel scope (fix #3)**: wrap `await _execute_agent_session` (`:1494`) in a cancel scope whose deadline is driven by progress (`last_tool_use_at`/`last_turn_at`), not wall-clock; on expiry, cancel, force-release the slot (`:1657`), and fd-level kill the PTY.
- **Lease-based slot ownership (fix #2 — removes the class)**: replace the ownerless `asyncio.Semaphore` (`session_state.py:75`) and PTY `_sem` with a lease-recording wrapper. Each permit records `(owner_session_id, acquired_at, deadline)`; an off-loop reaper reclaims leases whose owner is terminal or expired. The `session_health.py:2561-2613` fingerprint becomes a *reclaim* call.
- **Out-of-domain recovery (fix #5)**: extend `monitoring/session_watchdog.py` (bridge process) to read the `last_loop_tick` beacon and the lease registry, owning worker-loop liveness + slot reclamation from a separate failure domain.
- **Per-tool-call budget (fix #6 — stretch backstop)**: synchronous `max_tool_calls_per_session` / cost cap enforced *in the execution path* so it fires even when the health loop is frozen.

### Flow

Wedge occurs → on-loop tick stops → off-loop watchdog sees stale tick → **SIGABRT** → launchd respawns worker → recovery (re-queue pending sessions).

Leaked permit → owner session terminal/expired → off-loop reaper reclaims lease → slot freed **without restart**.

Parked PTY acquirer → `wait_for` timeout → force-recycle `respawning` slot → acquirer proceeds.

No-progress session → progress deadline expires → cancel scope cancels → slot force-released + PTY killed.

### Technical Approach

- **Phase 1 (fix #1)**: add `last_loop_tick` to `agent/session_state.py` with a monotonic-clock writer scheduled on the loop (an `asyncio.create_task` heartbeat-tick coroutine in the worker startup, alongside `_agent_session_health_loop`). Modify `worker/__main__.py:_heartbeat_thread_main` to read the tick and gate the green write; add a self-kill branch (`os.kill(os.getpid(), signal.SIGABRT)`) when the tick is older than `WORKER_LOOP_TICK_DEADLINE` (env-tunable, default ~15s = 3× the ~5s tick). Provisional magic numbers become named env-overridable constants with grain-of-salt comments.
- **Phase 2 (fix #4)**: in `pty_pool.py`, replace the three unbounded awaits with `asyncio.wait_for(..., timeout=PTY_ACQUIRE_TIMEOUT)`; on `TimeoutError`, transition any `respawning` slot whose respawn task is done/dead to a force-recycle path that re-spawns it. Keep the divergent-primitive bug in mind (`:540-568`) — the recycle must notify both the event and the condition var.
- **Phase 3 (fix #3)**: introduce a progress-deadline cancel scope around `:1494`. A small monitor (could be the same on-loop tick task) compares `now - max(last_tool_use_at, last_turn_at)` against `SESSION_PROGRESS_DEADLINE`; on breach it cancels the execution task, which unwinds through the existing `except asyncio.CancelledError` at `:1496` and the `:1657` release. Add an fd-level PTY kill on the cancel path.
- **Phase 4 (fix #2)**: introduce a `LeaseSemaphore` (or lease registry beside the semaphore) recording owner/acquired_at/deadline per permit. Replace `_global_session_semaphore` and PTY `_sem`. An off-loop reaper (daemon thread or the bridge-side watchdog) reclaims leases whose `owner_session_id` is in a terminal status or past `deadline`. Convert `session_health.py:2561-2613` from log-only to a reclaim call. **Popoto note**: if the lease registry is persisted on `AgentSession` or a new Popoto model, add an idempotent migration to `scripts/update/migrations.py` and register it in `MIGRATIONS`. Prefer in-memory lease state keyed by session_id to avoid a migration unless cross-process reads require persistence (fix #5 does — see below).
- **Phase 5 (fix #5)**: extend `monitoring/session_watchdog.py` (already in the bridge process, `telegram_bridge.py:3050`) to read the `last_loop_tick` beacon (written to `data/` or Redis so it's cross-process) and the lease registry. This is where lease state likely **must** be cross-process (Redis via Popoto) — drives the persistence decision in Phase 4.
- **Phase 6 (fix #6, stretch)**: a synchronous counter/cost guard in the execution path (`enforcement`-style ALLOW/DENY) that raises when `max_tool_calls_per_session` is exceeded, independent of any background loop.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The leaked-slot fingerprint block (`session_health.py:2561-2613`) currently ends in `except Exception: pass` (fail-quiet). When it becomes a reclaim call, the reclaim must log on success (observable) and the fail-quiet must remain only for the *read* of `_sem._value`, not the reclaim action. Add a test asserting a reclaim emits a log/metric.
- [ ] The SIGABRT self-kill branch must be reachable and testable without actually aborting the test process — inject the kill via a seam (a `_self_kill()` function patched in tests) and assert it is called when the tick is stale.

### Empty/Invalid Input Handling
- [ ] `last_loop_tick == None` (pre-initialization / legacy) must be treated as "not yet started," NOT as "stale → kill." Test the None path explicitly.
- [ ] A lease with `owner_session_id` referencing a deleted session must reclaim, not crash. Test the missing-owner path.

### Error State Rendering
- [ ] Worker self-kill + respawn must surface in `dashboard.json` health (the respawn is visible). Verify the dashboard reflects a recovery, not a silent gap.
- [ ] A reclaimed slot must update the running/permits view so the dashboard shows the freed slot.

## Test Impact

- [ ] `tests/unit/test_session_heartbeat_progress.py` — UPDATE: heartbeat write is no longer unconditional; assert the green write is gated on a fresh `last_loop_tick` and skipped/self-kills when stale.
- [ ] `tests/integration/test_worker_wedge_pending.py` — UPDATE: this #1808 wedge test currently asserts the wedge is *detected*; extend it to assert *recovery* (self-kill seam invoked / slot reclaimed).
- [ ] `tests/unit/test_watchdog_recovery.py` / `tests/integration/test_watchdog_to_bridge.py` — UPDATE: `worker_watchdog.py` stale-heartbeat ladder now actually fires for wedged loops (heartbeat goes stale); add/adjust cases that exercise the armed path.
- [ ] `tests/unit/test_worker_concurrency.py` — UPDATE: the global semaphore is replaced by the lease wrapper; assert lease metadata is recorded on acquire and cleared on release while preserving the existing concurrency-ceiling behavior.
- [ ] `tests/unit/test_agent_session_liveness_fields.py` / `tests/unit/test_pre_tool_use_liveness_writes.py` — UPDATE if the progress-deadline cancel scope reads these fields differently; likely additive (new assertions), no behavior change to the fields themselves.
- [ ] PTY pool tests (search `tests/ -k pty_pool`) — UPDATE: bounded `wait_for` changes timeout behavior; add a force-respawn-failure case asserting bounded wait + recycle (acceptance criterion 3).
- [ ] New test files (REPLACE/create): `test_worker_dead_mans_switch.py` (fix #1), `test_pty_pool_bounded_acquire.py` (fix #4), `test_progress_deadline_cancel.py` (fix #3), `test_lease_slot_reclaim.py` (fix #2), `test_session_watchdog_loop_liveness.py` (fix #5).

## Rabbit Holes

- **Cross-process PTY fd close on macOS** — already proven infeasible (#1767 spike: `os.close` only owns own fds, `/proc` is Linux-only, `psutil.open_files` doesn't surface PTY devices). Do NOT try to close the wedged worker's PTY fds from the watchdog; respawn the process instead.
- **Re-adding a wall-clock execution timeout** — explicitly rejected by #1172 and the issue's research. The deadline must be progress-driven. Resist the temptation to bound execution by wall-clock "to be safe."
- **Rewriting the PTY pool state machine** — fix #4 is a narrow `wait_for` + force-recycle, not a redesign of the respawn contract. The divergent-primitive bug (`:540-568`) is real but should be patched surgically (notify both primitives), not by re-architecting.
- **Tuning the asyncio set_debug / `slow_callback_duration`** — structurally blind to coroutines parked at `await semaphore.acquire()`. Keep it, but it is not part of any fix here.
- **Persisting lease state prematurely** — only Phase 5 forces cross-process lease reads. Don't add a Popoto model + migration in Phase 4 if in-memory state suffices until Phase 5 lands.

## Risks

### Risk 1: Dead-man's-switch self-kills a healthy-but-slow loop (false positive)
**Impact:** A long synchronous-but-legitimate operation that doesn't tick for >threshold triggers SIGABRT, re-queuing in-flight work (lossy).
**Mitigation:** Set the tick deadline conservatively (≥3× the tick interval), env-tunable for rollout. The tick task is cheap and on-loop, so any loop that's *making progress at all* ticks. Ship behind a high default threshold first, tighten after observation. Provisional numbers carry grain-of-salt comments.

### Risk 2: Lease reclaim races with legitimate slow execution
**Impact:** Reclaiming a permit whose owner is still legitimately working frees a slot that's actually in use → over-admission past the concurrency ceiling.
**Mitigation:** Reclaim only when the owner session is in a **terminal** status OR past an explicit deadline that is itself progress-driven (not wall-clock). The reclaim is idempotent. Reconcile against `AgentSession` status as the source of truth, never against running-count alone (the #1537 lesson).

### Risk 3: Bounded PTY `wait_for` recycles a slot mid-legitimate-respawn
**Impact:** Force-recycling a slot whose respawn is simply slow (not dead) doubles a spawn or corrupts slot state.
**Mitigation:** Only force-recycle a `respawning` slot whose respawn **task is done/dead** (`task.done()` and raised/exited), not one whose task is still running. Hold the per-slot lock during the recycle decision.

### Risk 4: SIGABRT respawn storm under a persistent wedge cause
**Impact:** If the wedge cause is deterministic (e.g., a poisoned session), the worker self-kills, respawns, re-picks the same work, and wedges again — a crash loop.
**Mitigation:** `ThrottleInterval=10` rate-limits launchd respawns. Recovery on restart should quarantine the session that was running at wedge time (mark it for review rather than immediate re-pick), reusing the existing crash-signature / auto-resume-policy machinery.

## Race Conditions

### Race 1: on-loop tick vs off-loop watchdog read
**Location:** `agent/session_state.py` (tick write) ↔ `worker/__main__.py:_heartbeat_thread_main` (tick read)
**Trigger:** The watchdog thread reads `last_loop_tick` while the loop writes it.
**Data prerequisite:** `last_loop_tick` must be initialized (to "started" sentinel) before the watchdog's first read, else None is misread as stale.
**State prerequisite:** the tick must be a single atomic value (a float timestamp), read/written without a compound update.
**Mitigation:** Use a module-level float updated by a single assignment (atomic in CPython); initialize it to the start time *before* starting the watchdog thread. Treat None as "not started," never as stale.

### Race 2: lease reclaim vs slot release
**Location:** lease reaper ↔ `agent_session_queue.py:1657` (release) / `pty_pool.py` release
**Trigger:** The reaper reclaims a lease at the same moment the owner legitimately releases it → double-release / negative permit count.
**Data prerequisite:** the lease record and the underlying permit count must move together.
**State prerequisite:** reclaim and release must be mutually exclusive per permit.
**Mitigation:** Guard each permit's lease mutation with a lock (or compare-and-set on the owner id); a release clears the lease atomically and a reclaim is a no-op if the lease is already cleared/owned by a different session. Idempotent on both sides.

### Race 3: progress-deadline cancel vs natural completion
**Location:** progress-deadline monitor ↔ `agent_session_queue.py:1494-1496`
**Trigger:** The monitor cancels the execution task at the same instant it completes naturally.
**Data prerequisite:** `finalized_by_execute` and the slot-release path must run exactly once.
**State prerequisite:** cancel and natural-completion must not both finalize.
**Mitigation:** The existing `except asyncio.CancelledError` + single `finally`-style release already guards single finalization; ensure the cancel path checks `task.done()` before cancelling and that the deadline monitor stops once execution returns.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1815] Fix #6 (synchronous per-tool-call budget) is the lowest-priority backstop and is **stretch** within this plan — if the first five phases consume the appetite, #6 ships as a follow-up tranche under this same issue's landing order. It is in scope to *attempt*; deferring it does not block the acceptance criteria (which #6 is not part of).
- [EXTERNAL] Tuning the production `WORKER_LOOP_TICK_DEADLINE` / `SESSION_PROGRESS_DEADLINE` to their final aggressive values requires observing real wedge incidents on the live bridge machine — initial ship uses conservative defaults; final tightening is an operator tuning step after rollout.
- Nothing else deferred — fixes #1-#5 and docs are all in scope for this plan.

## Update System

- **launchd**: no plist change required for fix #1 — `com.valor.worker.plist` already has `KeepAlive=true` + `ThrottleInterval=10`. If a dedicated tick-deadline env var needs a non-default value on rollout, set it via the existing env-propagation path (no new file).
- **Migrations**: only if Phase 4/5 persist lease state on a Popoto model. If so, add an idempotent migration to `scripts/update/migrations.py` and register it in `MIGRATIONS` (recorded once in `data/migrations_completed.json`). Prefer in-memory lease state until Phase 5 forces cross-process reads; decide at Phase 4.
- **New env constants** (`WORKER_LOOP_TICK_INTERVAL`, `WORKER_LOOP_TICK_DEADLINE`, `PTY_ACQUIRE_TIMEOUT`, `SESSION_PROGRESS_DEADLINE`, lease TTL) read from the environment with safe defaults — add to `.env.example` with a comment line above each (completeness-check requirement) if any must be operator-overridable on a machine. No `config/settings.py` field needed unless surfaced in settings.
- Otherwise no `scripts/update/run.py` changes — this is internal worker/agent/monitoring code propagated by the normal git pull.

## Agent Integration

No agent integration required — this is entirely a worker/agent-runtime and monitoring change. There is no new MCP tool, no `.mcp.json` change, and no bridge-imported entry point for an agent to call. The recovery paths are invoked by the worker loop, the off-loop watchdog thread, launchd, and the bridge-side `session_watchdog.py` — none are agent-facing surfaces. The dashboard (`dashboard.json`) reflects recoveries but is a read-only observability surface, not an agent tool.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/worker-liveness-recovery.md` describing the heartbeat-inversion (dead-man's-switch) + lease model, the landing order, and how each fix maps to an acceptance criterion. (Acceptance criterion: docs.)
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/worker-wedge-investigation.md` (the #1808 instrument-only doc) to point forward to the remediation, so the two read as a coherent before/after rather than a parallel-run. Per the no-historical-artifacts rule, describe the new status quo.

### Inline Documentation
- [ ] Docstrings on `LeaseSemaphore`/lease registry, the tick task, and the self-kill branch explaining the systemd-watchdog analogy and the failure mode each closes.
- [ ] Update the `pty_pool.py:38-52` POOL-1 docstring once the unbounded waits are bounded — it currently says "recovers via the operator's intervention," which fix #4 replaces with automatic recycle.

## Success Criteria

- [ ] A wedged event loop self-kills and is respawned by launchd within a bounded window (test: inject a synchronous freeze; observe the self-kill seam fires + recovery re-queues work).
- [ ] A leaked semaphore permit is automatically reclaimed without a process restart (test: orphan a slot whose owner is terminal; observe reclaim).
- [ ] A parked PTY-pool acquirer cannot block forever (test: force a respawn-task failure; observe bounded `wait_for` + slot recycle).
- [ ] A session parked with no progress past its deadline is cancelled and its slot released (test: stall progress fields; observe cancel + release at `:1657`).
- [ ] Recovery logic verified to run from a process *other* than the worker loop it polices (test/assert: `session_watchdog.py` reads the beacon + lease registry from the bridge process).
- [ ] `session_health.py:2561-2613` is a reclaim call, not log-only (grep confirms the recovery action replaced the comment's "decision is unchanged").
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`) — `docs/features/worker-liveness-recovery.md` exists.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (dead-mans-switch)**
  - Name: dms-builder
  - Role: Fix #1 — on-loop tick + off-loop watchdog self-kill (`session_state.py`, `worker/__main__.py`)
  - Agent Type: async-specialist
  - Resume: true

- **Builder (pty-bounded-waits)**
  - Name: pty-builder
  - Role: Fix #4 — bounded `wait_for` + force-recycle in `pty_pool.py`
  - Agent Type: async-specialist
  - Resume: true

- **Builder (progress-deadline)**
  - Name: deadline-builder
  - Role: Fix #3 — progress-deadline cancel scope around `:1494`
  - Agent Type: async-specialist
  - Resume: true

- **Builder (lease-semaphore)**
  - Name: lease-builder
  - Role: Fix #2 — LeaseSemaphore + off-loop reaper; convert `:2561-2613` to reclaim
  - Agent Type: async-specialist
  - Resume: true

- **Builder (out-of-domain-recovery)**
  - Name: watchdog-builder
  - Role: Fix #5 — extend `session_watchdog.py` to own loop-liveness + reclaim
  - Agent Type: builder
  - Resume: true

- **Validator (resilience)**
  - Name: resilience-validator
  - Role: Verify each acceptance criterion via injected-fault tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: liveness-doc
  - Role: `docs/features/worker-liveness-recovery.md` + index + forward-link the #1808 doc
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Dead-man's-switch (fix #1)
- **Task ID**: build-dms
- **Depends On**: none
- **Validates**: tests/unit/test_worker_dead_mans_switch.py (create), tests/unit/test_session_heartbeat_progress.py (update)
- **Informed By**: research (systemd half-interval emit; launchd KeepAlive confirmed)
- **Assigned To**: dms-builder
- **Agent Type**: async-specialist
- **Parallel**: false (lands first — safety net under the rest)
- Add `last_loop_tick` beacon + accessors to `agent/session_state.py`; initialize before the watchdog thread starts.
- Add an on-loop tick coroutine in the worker startup (alongside `_agent_session_health_loop`).
- Gate `_heartbeat_thread_main`'s green write on tick freshness; add a `_self_kill()` seam that SIGABRTs when the tick exceeds `WORKER_LOOP_TICK_DEADLINE`.
- Name all thresholds as env-overridable constants with grain-of-salt comments.

### 2. Bound PTY pool waits (fix #4)
- **Task ID**: build-pty
- **Depends On**: none (independent; smallest blast radius)
- **Validates**: tests/unit/test_pty_pool_bounded_acquire.py (create), existing pty_pool tests (update)
- **Assigned To**: pty-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Wrap `pty_pool.py:285,358,362` awaits with `asyncio.wait_for(timeout=PTY_ACQUIRE_TIMEOUT)`.
- On timeout, force-recycle a `respawning` slot whose respawn task is done/dead; notify both the event and the condition var (`:540-568` divergent-primitive bug).

### 3. Progress-deadline cancel scope (fix #3)
- **Task ID**: build-deadline
- **Depends On**: build-dms (reuses the on-loop tick infrastructure for the deadline monitor)
- **Validates**: tests/integration/test_progress_deadline_cancel.py (create)
- **Assigned To**: deadline-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Wrap `await _execute_agent_session` (`:1494`) in a cancel scope driven by `max(last_tool_use_at, last_turn_at)` vs `SESSION_PROGRESS_DEADLINE`.
- On breach: cancel, force-release slot (`:1657`), fd-level PTY kill. Guard single-finalization against natural completion.

### 4. Lease-based slot ownership (fix #2 — removes the class)
- **Task ID**: build-lease
- **Depends On**: build-pty (PTY `_sem` is one of the two semaphores replaced)
- **Validates**: tests/unit/test_lease_slot_reclaim.py (create), tests/unit/test_worker_concurrency.py (update)
- **Assigned To**: lease-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Replace `_global_session_semaphore` and PTY `_sem` with a lease-recording wrapper (`(owner_session_id, acquired_at, deadline)`).
- Add an off-loop reaper reclaiming leases whose owner is terminal/expired; convert `session_health.py:2561-2613` to a reclaim call.
- Decide in-memory vs Popoto-persisted lease state (persist only if Phase 5 needs cross-process reads → add migration to `scripts/update/migrations.py`).

### 5. Out-of-domain recovery (fix #5)
- **Task ID**: build-watchdog
- **Depends On**: build-dms, build-lease (reads the beacon + lease registry)
- **Validates**: tests/integration/test_session_watchdog_loop_liveness.py (create)
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `monitoring/session_watchdog.py` (bridge process) to read `last_loop_tick` + the lease registry and own worker-loop liveness + reclaim from a separate failure domain.

### 6. Validation
- **Task ID**: validate-resilience
- **Depends On**: build-dms, build-pty, build-deadline, build-lease, build-watchdog
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run each acceptance-criterion fault-injection test; verify recovery (not just detection) for every fix.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-resilience
- **Assigned To**: liveness-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-liveness-recovery.md`; add to `docs/features/README.md`; forward-link `worker-wedge-investigation.md`.

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Dead-man's-switch wired | `grep -rn "last_loop_tick" agent/session_state.py worker/__main__.py` | output contains last_loop_tick |
| Self-kill path exists | `grep -rn "SIGABRT" worker/__main__.py` | output contains SIGABRT |
| PTY waits bounded | `grep -rn "wait_for" agent/granite_container/pty_pool.py` | output contains wait_for |
| Leaked-slot fingerprint is now a reclaim, not log-only | `grep -n "recovery decision is unchanged" agent/session_health.py` | exit code 1 |
| Feature doc exists | `test -f docs/features/worker-liveness-recovery.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Batch scope**: should this plan's `/do-build` land all five recovery fixes (#1,#4,#3,#2,#5) in one PR, or land the safety net (#1) + smallest-blast-radius (#4) first as a separate PR and the class-removal (#2,#3,#5) as a second? The landing order is fixed; the PR boundary is the open call.
2. **Lease persistence**: confirm whether lease state should be Popoto-persisted from the start (cleaner for fix #5's cross-process reads, costs a migration) or stay in-memory until Phase 5 forces it. Plan currently recommends in-memory-until-#5.
3. **Self-kill default threshold**: is a ~15s tick deadline (3× a ~5s tick) acceptably conservative for first rollout, or should the initial deploy use a looser value (e.g. 60s) and tighten after observing real incidents?
4. **Fix #6 (per-tool budget)**: in scope for this plan as stretch, or split to a clean follow-up tranche so the five acceptance-criteria fixes ship without it?
