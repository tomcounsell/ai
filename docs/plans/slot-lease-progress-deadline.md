---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1820
last_comment_id:
---

# Lease-Based Slot Ownership + Progress-Deadline Cancel Scope (wedge fixes #2/#3)

## Problem

The global concurrency slot is an **ownerless** `asyncio.Semaphore`
(`agent/session_state.py:76`). A permit is acquired by the worker loop
(`agent/agent_session_queue.py:1330`) and can only ever be released by that same
loop's `finally` blocks. When a session is killed **out of band** — by the
health-check progress-kill (`session_health.py:1994`, `_apply_recovery_transition`
at `:1848`) or the per-tool tier timeout (`_agent_session_tool_timeout_loop`) —
the DB row transitions to terminal but the permit stays held by the parked worker
loop. Nothing else can release it. Permits leak one at a time until
`permits_free == 0` while `running_count < max`, and the whole worker stops
picking up new work. This is the #1537/#1808 leak class.

The existing detector — the leaked-slot fingerprint at `session_health.py:2560-2613`
— is **logging-only** by design ("recovery decision is unchanged"). It sees the
leak and does nothing about it. Recovery requires a human `worker-restart`.

Separately, the session-execution point (`agent_session_queue.py:1494`,
`await _execute_agent_session(session)`) has **no in-scope deadline**. The prior
landing (#1172) correctly removed the wall-clock timeout, and #1815/#1816 added
progress signals (`last_tool_use_at`/`last_turn_at`) plus out-of-band progress
killers — but those killers cannot cleanly cancel the running coroutine, cannot
release the ownerless slot, and do not fd-kill the session's PTY. A session parked
with no progress therefore hangs until an out-of-band killer flips its DB row,
leaving the slot leaked and the PTY process alive.

**Current behavior:** An out-of-band-killed or no-progress session leaks its
global concurrency permit; the leaked-slot fingerprint only logs; recovery needs a
process restart. A no-progress session's PTY process survives its logical death.

**Desired outcome:**
1. A leaked permit is automatically reclaimed **without a process restart** — the
   fingerprint becomes a reclaim call, and out-of-band kills release the slot
   promptly via lease ownership.
2. A session parked with no progress past its deadline is **cancelled**, its slot
   **force-released**, and its PTY **fd-killed** — from a scope that owns the
   execution task.

## Freshness Check

**Baseline commit:** `9d47033eee937b11fa60ededaa466b017d5cbebb`
**Issue filed at:** 2026-06-29T11:13:36Z
**Disposition:** Minor drift

Three sibling PRs landed on the referenced files **after** this issue was filed,
so every file:line pointer was re-verified against `9d47033e` and drift corrected:

**Commits on main since issue was filed (touching referenced files):**
- `657ac2be` fix(worker): liveness-wedge recovery — dead-man's-switch + bounded PTY waits (#1815) (#1823) — **the prior landing this issue builds on.** Added the `last_loop_tick` beacon, `_self_kill()` SIGABRT seam, and bounded PTY `wait_for`. Foundation, not a conflict.
- `bab446d8` feat: worker fault containment (#1816) (#1832) — added `supervise()` (background-**task** supervisor), scoped process-group teardown (`container.py` `os.killpg`), reflection bulkhead, and the per-tool timeout loop wiring. **Adjacent, not overlapping:** `supervise()` respawns auxiliary loops; it does NOT own the session-execution slot, so it neither subsumes Fix #2 nor Fix #3. Its scoped-teardown `os.killpg` is the API Fix #3's fd-level PTY kill should reuse.
- `ee6d598f` feat(redis): durability hardening (#1814) (#1824) — Redis/Popoto client hardening. Irrelevant to the in-memory slot registry.

**File:line references re-verified (corrected inline in Technical Approach):**
- `agent/session_state.py:75 → :76` — `_global_session_semaphore: asyncio.Semaphore | None = None` — still the ownerless semaphore. Corrected line.
- `agent/agent_session_queue.py:1494` — the `await _execute_agent_session(session)` try block — **still holds** (execute call at ~`:1494`; acquire is at `:1330`, `_semaphore_acquired` flag at `:1331`).
- `agent/agent_session_queue.py:1657 → :1658` — `semaphore.release()` on session done — still holds; there are 12 release sites (`:1349,1354,1360,1398,1403,1419,1424,1438,1443,1463,1473,1658`), all in the worker loop's `finally`/guard paths.
- `agent/session_health.py:2561-2613 → :2560-2613` — leaked-slot fingerprint inside `_agent_session_health_check` (cadence `AGENT_SESSION_HEALTH_CHECK_INTERVAL=300s`) — **still holds; still logging-only.**
- Progress signals — `models/agent_session.py:505` (`last_tool_use_at`), `:523` (`last_turn_at`), bumped in `agent/hooks/liveness_writers.py:76/158` (5s cooldown) — confirmed present and already consumed by the health loop + tool-timeout loop.
- `_apply_recovery_transition` (`session_health.py:1848`) — the common out-of-band kill path — confirmed it transitions the DB row and **never touches the semaphore** (the leak).
- Terminal states — `models/session_lifecycle.py:61` `TERMINAL_STATUSES = {completed, failed, killed, abandoned, cancelled}`.
- PTY `_sem` — `agent/granite_container/pty_pool.py:166`, already bounded by `wait_for` at `:307` (#1815). Out of scope here except as the model for lease bounding.

**Cited sibling issues/PRs re-checked:**
- #1815 — CLOSED 2026-06-30 (PR #1823 merged) — the prior landing; primitives available.
- #1818 — OPEN — tracking umbrella for the resilience cluster.
- #1821 — OPEN — sibling (fixes #5/#6); Fix #5 depends on the lease registry this plan builds.
- #1537, #1808 — the leak-class incidents this plan removes.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/completed/liveness-wedge-recovery.md` (#1815, shipped) — direct predecessor; this plan is its explicit continuation (fixes #2/#3 were deferred to #1820 in that plan's No-Gos).
- `docs/plans/completed/worker-fault-containment.md` (#1816, shipped) — adjacent; provides `supervise()` and scoped teardown. No open plan conflicts.

**Notes:** No major drift — the core defect (ownerless semaphore → out-of-band killer can't reclaim; no in-scope no-progress cancel; no fd-PTY-kill) still holds verbatim. The only revision is scoping Fix #3 to *reuse* the now-extensive progress machinery rather than build a new detector (see Revised bucket in the issue's Recon Summary).

## Prior Art

- **#1815 / PR #1823 (merged)** — Dead-man's-switch + bounded PTY waits. Explicitly deferred fixes #2/#3 to this issue. Landed the beacon + `_self_kill()` + bounded-wait primitives this plan composes with. Its No-Gos name #1820 as the home for the lease registry.
- **#1816 / PR #1832 (merged)** — `supervise()` task supervisor + scoped process-group teardown + per-tool timeout loop. Provides the respawn-supervised-task pattern the reaper can reuse and the `os.killpg` teardown Fix #3's PTY kill reuses.
- **#1172 (merged)** — Removed the wall-clock execution timeout around `agent_session_queue.py:1494`. Confirms a **progress-based** deadline (this plan's Fix #3), not a wall-clock one, is the correct replacement.
- **#1270 (merged)** — Per-tool tier timeout loop (`_agent_session_tool_timeout_loop`). Already kills tool-wedged sessions via `_apply_recovery_transition`; Fix #3 reuses this path rather than duplicating it.
- **#1537, #1808 (investigations)** — Documented the exact leaked-slot mechanism (semaphore exhaustion while running_count < max). This plan is the fix.
- **`docs/features/worker-wedge-investigation.md`** — the instrument-only write-up; forward-links here for the fix.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `session_health.py:2560-2613` (leaked-slot fingerprint) | Reads `_sem._value`, logs a WARNING when `permits_free==0 AND running<max` | **Logging-only by design** — detection without recovery. The classic watchdog trap. This plan converts it to a reclaim call. |
| `_apply_recovery_transition` out-of-band kills (#1270/#1815) | Transitions a wedged session's DB row to terminal | **Cannot release the ownerless semaphore** — the permit is owned by the parked worker loop, not the killer. The DB says "done" while the slot stays leaked. Fix #2 gives the killer a reclaim path. |
| #1172 (removed wall-clock timeout) | Deleted the blunt execution timeout | Correct removal, but left **no in-scope backstop** for parked execution. Progress signals were added (#1815/#1816) but only consumed out of band, which (see row above) can't clean up the slot or PTY. Fix #3 adds the in-scope cancel. |

**Root cause pattern:** Every prior change improved *detection* or moved the kill
*out of band* without giving any actor the *ownership* needed to release the slot
and kill the PTY. The fix is to make the slot **owned** (a lease keyed by
`owner_session_id`) so any actor — the reaper, the out-of-band killer, or an
in-scope cancel — can reclaim it idempotently.

## Research

No relevant external findings needed — this is an internal concurrency refactor
building on documented primitives (#1815/#1816) and stdlib `asyncio`. The external
precedents named in the issue (k8s Lease + node eviction; Go `context.WithDeadline`;
omnigent `HOST_LIVENESS_TTL`) are conceptual and already captured in the issue body;
they inform the lease-with-deadline shape and the reclaim-on-terminal-owner rule.

## Data Flow

**Fix #2 — lease acquire / reclaim:**
1. **Entry point:** worker loop needs a slot → `agent_session_queue.py:1330`.
   Today: `await semaphore.acquire()`. New: `token = await registry.acquire()`
   (anonymous — owner not yet known; acquire happens *before* `_pop_agent_session`
   to keep the running-count accurate, per the existing comment at `:1325`).
2. **Bind:** after `_pop_agent_session` returns the session, bind the lease:
   `registry.bind(token, session.agent_session_id, deadline)`. The lease now
   records `(owner_session_id, acquired_at, deadline)`.
3. **Normal release:** the worker loop's existing `finally`/guard sites call
   `registry.release(session.agent_session_id)` (idempotent) instead of
   `semaphore.release()`.
4. **Reaper reclaim (the new recovery path):** on the health-check tick
   (`_agent_session_health_check`, 300s) the former fingerprint block iterates
   `registry.leases()`; for any lease whose `owner_session_id` is in
   `TERMINAL_STATUSES` **or** whose `deadline` has passed, call
   `registry.reclaim(owner)` — releases the permit, drops the lease, increments a
   telemetry counter, logs at WARNING.
5. **Prompt reclaim:** `_apply_recovery_transition` (the out-of-band killer) also
   calls `registry.reclaim(session_id)` immediately after flipping the row, so the
   slot frees within the health/tool-timeout cadence instead of waiting for the
   300s fingerprint tick.
6. **Output:** `permits_free` recovers; the worker loop unblocks at `acquire()`.

**Fix #3 — progress-deadline cancel scope:**
1. **Entry point:** `agent_session_queue.py:1494`. New: run execution as an owned
   child task — `exec_task = asyncio.create_task(_execute_agent_session(session))`.
2. **Deadline watch:** a small on-loop watcher computes
   `last_progress = max(last_tool_use_at, last_turn_at)` for the session and, if
   `now - last_progress > SESSION_PROGRESS_DEADLINE_S` while `exec_task` is not
   done, cancels `exec_task`. The deadline is fed by **progress**, never
   wall-clock (a session making tool calls resets it).
3. **On cancel/expiry:** fd-level PTY kill (scoped process-group teardown of the
   session's granite slot via the `container.py` `os.killpg` path from #1816) →
   `registry.reclaim(session.agent_session_id)` → transition to terminal via the
   shared `_apply_recovery_transition` semantics (idempotent).
4. **Output:** the parked session is finalized, its slot freed, its PTY dead —
   all from the scope that owned the task.

## Architectural Impact

- **New dependencies:** none (stdlib `asyncio`, `time`, existing `os.killpg`).
- **Interface changes:** `agent/session_state.py` replaces the raw
  `_global_session_semaphore: asyncio.Semaphore` with a `SlotLeaseRegistry`
  instance (new class, likely `agent/slot_lease.py`) that *wraps* an
  `asyncio.Semaphore` for backpressure and adds `acquire()/bind()/release()/
  reclaim()/leases()/permits_free()`. The 12 `semaphore.release()` sites in
  `agent_session_queue.py` and the `_sem._value` read in `session_health.py`
  migrate to registry methods. **No legacy shim** — the raw semaphore is fully
  removed (NO LEGACY CODE TOLERANCE).
- **Coupling:** the reaper (health check) and the out-of-band killer now *depend
  on* the registry's reclaim API — a deliberate, minimal coupling that replaces an
  impossible cross-actor release. The lease is keyed by `owner_session_id`, the
  natural ownership key already threaded everywhere.
- **Data ownership:** the slot gains an explicit owner. Leases are **in-memory**
  (module-global registry), rebuilt fresh on worker restart — no Popoto model
  field, no migration.
- **Reversibility:** high. The registry preserves the exact counting-semaphore
  backpressure contract; env kill-switches (`SLOT_LEASE_REAP_DISABLED`,
  `DISABLE_PROGRESS_KILL` reused for Fix #3) revert to detect-only / no-cancel.

## Appetite

**Size:** Large

**Team:** Solo dev with async/concurrency framing (the global concurrency
primitive every session flows through — high blast radius, careful race analysis),
PM check-in, 2 review rounds.

**Interactions:**
- PM check-ins: 1-2 (confirm the "on-loop reaper task" reframing of the issue's
  "off-loop reaper"; confirm Fix #3 reuses vs. supplants the existing progress-kill)
- Review rounds: 2 (async correctness of the registry + acquire-before-bind window;
  cancel-scope + fd-PTY-kill ordering)

## Prerequisites

Builds on #1815/#1816 primitives, already merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Python ≥ 3.11 | `python -c "import sys; assert sys.version_info >= (3, 11)"` | Leak-safe `wait_for(Semaphore.acquire())` for bounded lease acquire |
| #1815 beacon present | `grep -c "def get_loop_tick" agent/session_state.py` | Confirms the liveness foundation landed |
| #1816 scoped teardown present | `grep -c "killpg" agent/granite_container/container.py` | Confirms the fd-level PTY kill API exists for Fix #3 |

Run via `python scripts/check_prerequisites.py docs/plans/slot-lease-progress-deadline.md`.

## Solution

### Key Elements

- **`SlotLeaseRegistry`** (`agent/slot_lease.py`, referenced from
  `agent/session_state.py`): wraps an `asyncio.Semaphore(max)` for backpressure
  and records a `{owner_session_id: Lease(owner_session_id, acquired_at, deadline)}`
  map plus an unbound-permit tally. Methods: `acquire() -> token`,
  `bind(token, owner_session_id, deadline)`, `release(owner_session_id)` (idempotent),
  `reclaim(owner_session_id)` (idempotent), `leases()`, `permits_free()`.
- **Fingerprint → reclaim** (`session_health.py:2560-2613`): the logging-only block
  becomes a reap pass over `registry.leases()`, reclaiming leases whose owner is
  terminal or whose deadline expired.
- **Prompt reclaim in the killer** (`_apply_recovery_transition`): reclaim the
  slot immediately when an out-of-band kill flips the row.
- **Progress-deadline cancel scope** (`agent_session_queue.py:1494`): own the
  execution task; cancel on no-progress-past-deadline; fd-PTY-kill + reclaim +
  finalize.
- **Env kill-switches** (all NAMED, env-overridable, conservative-provisional):
  `SESSION_PROGRESS_DEADLINE_S`, `SLOT_LEASE_TTL_S` (default deadline for a lease),
  `SLOT_LEASE_REAP_DISABLED`, reuse `DISABLE_PROGRESS_KILL` for the Fix #3 cancel.

### Flow

Worker loop → `token = await registry.acquire()` → pop session →
`registry.bind(token, session_id, deadline)` → run `exec_task` under the
progress-deadline watcher →
**normal completion** → `registry.release(session_id)` /
**no progress past deadline** → cancel `exec_task` → fd-PTY-kill → `registry.reclaim(session_id)` → finalize.

Independently, health tick → iterate `registry.leases()` → any owner terminal or
deadline expired → `registry.reclaim(owner)` (this is the leaked-permit safety net
that needs no restart).

### Technical Approach

**Fix #2 — lease-based slot ownership:**

- Add `agent/slot_lease.py`: `Lease` dataclass `(owner_session_id, acquired_at,
  deadline)` + `SlotLeaseRegistry`. The registry holds one `asyncio.Semaphore` so
  the worker loop still blocks at `acquire()` when full — the counting-semaphore
  backpressure contract is preserved exactly. All mutation is on-loop (no lock
  needed beyond the loop's cooperative scheduling; document this).
- `acquire()` awaits the semaphore and returns an opaque `token` recorded as an
  **unbound** permit `(token, acquired_at)`. Bounded by `SLOT_LEASE_TTL_S` via
  `wait_for` only if we choose to bound it — default unbounded (backpressure is
  legitimate); see Open Question 2.
- `bind(token, owner_session_id, deadline)` promotes the unbound permit to a bound
  `Lease`. Called right after `_pop_agent_session`.
- `release(owner_session_id)` releases the permit and drops the lease; a
  double-release or unknown-owner release is a **no-op** (idempotency guard so the
  permit count can never be over-released — critical, since both the loop and the
  reaper may try).
- `reclaim(owner_session_id)` = `release` + telemetry + WARNING log; also idempotent.
- `session_state.py:76`: replace `_global_session_semaphore: asyncio.Semaphore | None`
  with `_slot_registry: SlotLeaseRegistry | None`, initialized in `_run_worker()`
  exactly where the semaphore is today. Update the re-export at
  `agent_session_queue.py:126`.
- Migrate the 12 release sites (`agent_session_queue.py`) to
  `registry.release(session_id)`; migrate the acquire (`:1330`) + `_semaphore_acquired`
  flag to the `token` model.
- `session_health.py:2560-2613`: replace the logging-only fingerprint with a reap:
  for each `lease in registry.leases()`, if `lease.owner_session_id` (re-read fresh,
  terminal-status-guarded like the existing tool-timeout path) is in
  `TERMINAL_STATUSES` or `now > lease.deadline`, `registry.reclaim(owner)` +
  increment `{project_key}:session-health:slot_reclaims` counter. Gated by
  `SLOT_LEASE_REAP_DISABLED`. Keep an INFO line for the healthy-backpressure case
  (`permits_free==0 AND running>=max`).
- `_apply_recovery_transition`: after the transition, call
  `registry.reclaim(session_id)` (idempotent) so out-of-band kills free the slot
  promptly. This is the wiring that makes acceptance criterion #1 fire on the
  tool-timeout/health cadence, not the 300s fingerprint tick.

**Fix #3 — progress-deadline cancel scope:**

- Near `agent_session_queue.py:1494`, replace `await _execute_agent_session(session)`
  with an owned-task pattern:
  ```
  exec_task = asyncio.create_task(_execute_agent_session(session))
  while not exec_task.done():
      done, _ = await asyncio.wait({exec_task}, timeout=PROGRESS_POLL_S)
      if exec_task in done:
          break
      last = _session_progress_ts(session)  # max(last_tool_use_at, last_turn_at, acquired_at)
      if last is not None and (now - last) > SESSION_PROGRESS_DEADLINE_S:
          exec_task.cancel(); ... # fd-PTY-kill + reclaim + finalize
  await exec_task  # propagate result/CancelledError
  ```
  This keeps the loop ticking (bumping `last_loop_tick`) while watching progress —
  it is NOT a wall-clock cap and resets on any tool/turn activity.
- On expiry: (a) fd-level PTY kill via the granite container's scoped
  process-group teardown (`container.py` `os.killpg` path, #1816) for the session's
  slot; (b) `registry.reclaim(session.agent_session_id)`; (c) finalize via the
  shared `_apply_recovery_transition` semantics (reason_kind `progress_deadline`),
  reusing the terminal-status idempotency so an out-of-band killer racing the same
  session is harmless.
- Reuse `DISABLE_PROGRESS_KILL=1` as the kill-switch (parity with the existing
  out-of-band progress-kill and tool-timeout loops).
- **Coordination with existing killers:** the out-of-band progress-kill
  (`session_health.py:1994`) and tool-timeout loop still run; both now reclaim via
  Fix #2. The in-scope cancel is the *fast, clean* path (owns the task, kills the
  PTY); the out-of-band killers are the backstop for sessions the in-scope watcher
  can't reach (e.g. the loop is between sessions). All three converge on
  idempotent `reclaim` + terminal-guarded `_apply_recovery_transition`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `registry.reclaim()` / `release()` must never raise into the health loop or
  worker loop — wrap the reap pass so a single bad lease logs and the loop
  continues. Test asserts a reclaim exception is logged, not propagated.
- [ ] The fd-PTY-kill in Fix #3 must not raise into the cancel handler — a failed
  `killpg` (already-dead pgid) logs and proceeds to reclaim+finalize. Test asserts
  finalize still runs when the PTY kill errors.
- [ ] No new `except Exception: pass` — every swallow in the new code emits a
  `logger.warning` with the owner_session_id. Test captures the record.

### Empty/Invalid Input Handling
- [ ] `release`/`reclaim` on an unknown or already-released `owner_session_id` is a
  no-op (never over-releases the permit). Test double-reclaim → permit count
  unchanged after the first.
- [ ] `_session_progress_ts` with all-None progress fields (legacy/never-started
  session) falls back to `acquired_at` so the deadline is still well-defined; a
  never-started session past the deadline is still cancelled. Test both.
- [ ] An unbound permit (acquire succeeded, `bind` not yet called because pop is
  mid-`await`) is NEVER reclaimed by the reaper. Test: reap pass with an unbound
  token in flight leaves the permit held.

### Error State Rendering
- [ ] A reclaimed slot emits a WARNING naming the owner + reason (terminal vs
  deadline) so `logs/worker.log` explains the recovery. Test captures it.
- [ ] A progress-deadline cancel emits a CRITICAL/WARNING with the stall age before
  killing. Test captures it.

## Test Impact

- [ ] `tests/integration/test_worker_concurrency.py::TestGlobalSemaphore::test_semaphore_limits_concurrent_sessions` — UPDATE: the raw semaphore is replaced by `SlotLeaseRegistry`; the concurrency-limit assertion must go through the registry (limit still enforced via the wrapped semaphore). Rename/retarget to the registry API.
- [ ] `tests/integration/test_worker_concurrency.py::TestGlobalSemaphore::test_semaphore_none_allows_unlimited_sessions` — UPDATE: `None` registry = no ceiling; assert the registry-None path preserves unlimited behavior.
- [ ] `tests/integration/test_worker_wedge_pending.py::TestWorkerLoopParksOnZeroSemaphore` — UPDATE: still valid (the loop parks when the registry is exhausted), but retarget to the registry's `permits_free`.
- [ ] `tests/integration/test_worker_wedge_pending.py` (health-check-cannot-escalate case) — REPLACE: this asserts the health check can only *nudge*, not recover, a leaked slot (the old logging-only behavior). Rewrite as the **acceptance-criterion regression guard**: an orphaned/terminal-owner lease is *reclaimed* by the reap pass and the worker unblocks — no restart. This is the inversion of the documented-bug test.
- [ ] Any test reading `_global_session_semaphore` / `_sem._value` directly (grep before build) — UPDATE to the registry accessor.

New tests (greenfield):
- `tests/unit/test_slot_lease_registry.py` — acquire/bind/release/reclaim happy path; double-reclaim idempotency (no over-release); unbound-permit-not-reclaimed; terminal-owner reclaim; deadline-expired reclaim; `permits_free` accounting.
- `tests/integration/test_slot_lease_reclaim.py` — end-to-end: orphan a slot (bind a lease to a session, transition it terminal without releasing), run the reap pass, assert `permits_free` recovers and a parked worker proceeds — **acceptance criterion #1**.
- `tests/integration/test_progress_deadline_cancel.py` — a session with no progress past `SESSION_PROGRESS_DEADLINE_S` is cancelled, its slot reclaimed, its PTY killed (mock/assert the `killpg` seam); a session making steady progress is NOT cancelled — **acceptance criterion #2**.

## Rabbit Holes

- **Do NOT build a new progress detector.** `last_tool_use_at`/`last_turn_at`, the
  `DISABLE_PROGRESS_KILL` loop, and the tool-timeout tiers already exist. Fix #3
  *consumes* them and reuses `_apply_recovery_transition` — it does not re-derive
  "no progress."
- **Do NOT make the reaper an off-loop thread that calls `semaphore.release()`.**
  `asyncio.Semaphore` is loop-affine; releasing from the watchdog thread is
  undefined behavior. The reaper runs **on-loop** in the health check (a different
  task from the parked worker loop, which is what "off the parked loop" really
  requires). If a truly cross-thread signal is ever needed, use
  `loop.call_soon_threadsafe`. See Open Question 1.
- **Do NOT store leases on the AgentSession Popoto model.** They are in-memory,
  rebuilt on restart. A model field would drag in a migration for zero benefit
  (startup recovery already re-queues running sessions).
- **Do NOT machine-wide `pkill` for the fd-PTY-kill.** Use the scoped
  process-group teardown from #1816 for the session's own slot only — machine-wide
  pkill was the #1816 bug that matched the operator's personal `claude`.
- **Do NOT leave a legacy semaphore shim.** Fully remove
  `_global_session_semaphore`; migrate every reference. No parallel-run.
- **Do NOT chase mid-flight cancellation of synchronous work.** `exec_task.cancel()`
  interrupts at `await` points; a truly CPU-frozen loop is #1815's SIGABRT job, not
  this cancel scope.

## Risks

### Risk 1: Over-release corrupts the permit count
**Impact:** If both the worker loop `release` and the reaper `reclaim` fire for the
same owner, the semaphore could gain a phantom permit → over-admission → too many
concurrent sessions.
**Mitigation:** `release`/`reclaim` are idempotent on `owner_session_id` — the
first drops the lease and releases exactly one permit; subsequent calls find no
lease and no-op. A single source of truth (the lease map) gates the underlying
`semaphore.release()`. Unit test asserts double-reclaim leaves `permits_free`
unchanged after the first.

### Risk 2: Acquire-before-bind window
**Impact:** The permit is acquired at `:1330` but the owner isn't known until after
`await _pop_agent_session`. During that await the reaper could run; a leaked
*unbound* permit (pop raised, release skipped) could hide from the reaper.
**Mitigation:** Unbound permits are tracked with `acquired_at` and are **never**
reclaimed while unbound-and-young; a `bind` grace ceiling
(`SLOT_LEASE_BIND_GRACE_S`, conservative) lets the reaper reclaim an unbound permit
only if it stayed unbound implausibly long (a real pop-path leak). The pop path
already releases on every exception branch (`:1349-1360` etc.), so the normal case
never leaks. Test the bind-grace reclaim.

### Risk 3: In-scope cancel races the out-of-band killer
**Impact:** The Fix #3 watcher cancels a session at the same instant the
tool-timeout loop transitions it → double finalize / double reclaim.
**Mitigation:** Both converge on terminal-status-guarded
`_apply_recovery_transition` (already idempotent — `_TERMINAL_STATUSES` guard) and
idempotent `reclaim`. Whichever wins, the other no-ops. Test concurrent fire.

### Risk 4: Progress-deadline false-positive cancels a legitimately-long tool call
**Impact:** A long-but-healthy tool call (e.g. a big build) with no intervening
tool/turn events past `SESSION_PROGRESS_DEADLINE_S` gets cancelled.
**Mitigation:** `SESSION_PROGRESS_DEADLINE_S` is conservative-provisional and
aligned with the existing tool-timeout tiers (default 300s) so Fix #3 never fires
*before* the per-tool tier would. `last_tool_use_at` is bumped on PreToolUse (tool
*start*), so a tool that has started but not finished keeps the deadline fresh.
`DISABLE_PROGRESS_KILL=1` is the instant kill-switch. Tune after observing real
stall-vs-legit histograms.

## Race Conditions

### Race 1: Reaper reads a lease while the worker loop releases it
**Location:** `session_health.py` reap pass vs `agent_session_queue.py` release sites.
**Trigger:** Health tick iterates `registry.leases()` as the worker loop finishes a
session and calls `release`.
**Data prerequisite:** The lease map is the single source of truth for permit ownership.
**State prerequisite:** All registry mutation is on-loop; no `await` inside a single
`reclaim`/`release` so each is atomic w.r.t. the cooperative scheduler.
**Mitigation:** Idempotent `release`/`reclaim` keyed by owner; a snapshot copy of
`leases()` is iterated so mutation during iteration can't corrupt the loop. Re-read
owner status fresh before reclaim (existing tool-timeout race pattern).

### Race 2: Acquire/bind interleaving with the reaper
**Location:** `agent_session_queue.py:1330-1494` (acquire→pop→bind) vs reap pass.
**Trigger:** Reaper runs during the `await _pop_agent_session` gap.
**Data prerequisite:** Unbound permits carry `acquired_at`.
**State prerequisite:** Reaper skips unbound permits younger than `SLOT_LEASE_BIND_GRACE_S`.
**Mitigation:** Never reclaim young unbound permits (Risk 2). Bind is synchronous
(no await between pop-return and bind).

### Race 3: In-scope cancel vs session completing normally
**Location:** `agent_session_queue.py:1494` watcher vs `exec_task` finishing.
**Trigger:** `exec_task` completes in the same tick the deadline is judged expired.
**Data prerequisite:** `exec_task.done()` checked before cancel.
**State prerequisite:** `asyncio.wait({exec_task}, timeout=...)` returns `done`
before the deadline branch runs.
**Mitigation:** Check `exec_task in done` first and `break`; only evaluate the
deadline when the task is still pending. `exec_task.cancel()` on an already-done
task is a no-op.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1821] Fix #5 (out-of-domain recovery — read the beacon/leases
  from the bridge process) and Fix #6 (synchronous per-tool-call budget backstop).
  Fix #5 *depends on* the lease registry this plan builds; it is explicitly the
  next landing.
- [SEPARATE-SLUG #1821] Persisting the lease registry across worker restarts. Not
  needed — startup recovery re-queues running sessions and the registry rebuilds
  fresh; cross-process lease visibility is #1821's concern.
- [ORDERED] Final tuning of `SESSION_PROGRESS_DEADLINE_S` / `SLOT_LEASE_TTL_S` to
  production-observed values — the defaults ship conservative; tightening waits on
  observed stall/leak histograms on the live bridge machine (same posture as
  #1815's threshold tuning).

## Update System

No update-script or migration changes required. The `SlotLeaseRegistry` replaces an
in-memory module global (`_global_session_semaphore`) with another in-memory module
global — it is **not** a Popoto model, so no `scripts/update/migrations.py` entry.
The new env vars (`SESSION_PROGRESS_DEADLINE_S`, `SLOT_LEASE_TTL_S`,
`SLOT_LEASE_BIND_GRACE_S`, `SLOT_LEASE_REAP_DISABLED`) are all optional with safe
defaults; add them to `.env.example` with a comment line above each (completeness-
check requirement) only for operator discoverability — no `.env` propagation is
required. The worker is restarted by the standard
`./scripts/valor-service.sh worker-restart` after merge — no new deploy step in
`scripts/update/run.py`.

## Agent Integration

No agent integration required — this is a worker-internal concurrency change. No new
CLI entry point in `pyproject.toml [project.scripts]`, no MCP surface, and the
bridge does not import the new code. The dashboard's running-count is derived from
`AgentSession` status queries and the slot accounting; the registry preserves the
same `permits_free`/`held_count` semantics the fingerprint already reads, so the
dashboard contract is unchanged. (The bridge-side consumer of the lease registry is
the deferred Fix #5 in #1821 — out of scope here.)

## Documentation

### Feature Documentation
- [ ] Create `docs/features/slot-lease-ownership.md` describing: the ownerless-
  semaphore leak class, the `SlotLeaseRegistry` (lease = owner+acquired_at+deadline),
  the on-loop reap pass (fingerprint→reclaim), the prompt reclaim wired into
  `_apply_recovery_transition`, the progress-deadline cancel scope + fd-PTY-kill,
  the env constants with provisional defaults, and the k8s-Lease / Go-context
  precedents. State it is the continuation of `worker-liveness-recovery.md` (#1815)
  and that #1821 (fixes #5/#6) builds on the registry. (Acceptance criterion of #1815.)
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Forward-link from `docs/features/worker-wedge-investigation.md` (the
  logging-only write-up) and `docs/features/worker-liveness-recovery.md` to this
  doc — describe the new status quo (the fingerprint now reclaims), per the
  no-historical-artifacts rule.

### Inline Documentation
- [ ] Comment the on-loop-only mutation assumption on `SlotLeaseRegistry` (no lock;
  loop-affine `asyncio.Semaphore`; why the reaper is on-loop not off-loop).
- [ ] Comment the acquire-before-bind window + unbound-permit bind-grace rule.
- [ ] Comment each new timeout/deadline constant with the grain-of-salt
  "provisional, tune after observing real rates" note.

## Success Criteria

- [ ] **Acceptance #1:** A leaked semaphore permit is automatically reclaimed
  without a process restart — `tests/integration/test_slot_lease_reclaim.py` orphans
  a slot (terminal owner, unreleased) and asserts the reap pass recovers
  `permits_free` and a parked worker proceeds.
- [ ] **Acceptance #2:** A session parked with no progress past its deadline is
  cancelled and its slot released — `tests/integration/test_progress_deadline_cancel.py`
  asserts cancel + reclaim + PTY-kill on a no-progress session, and no cancel on a
  progressing session.
- [ ] The raw `_global_session_semaphore` is fully removed (no legacy shim); all
  release/acquire sites go through `SlotLeaseRegistry`.
- [ ] `_apply_recovery_transition` reclaims the slot on out-of-band kill (prompt
  recovery, not 300s-tick-only).
- [ ] `release`/`reclaim` are idempotent (double-fire never over-releases) —
  `tests/unit/test_slot_lease_registry.py`.
- [ ] The dashboard running-count/`permits_free` contract is unchanged.
- [ ] Kill-switches work: `SLOT_LEASE_REAP_DISABLED=1` disables reclaim (detect-only);
  `DISABLE_PROGRESS_KILL=1` disables the Fix #3 cancel.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`): `docs/features/slot-lease-ownership.md` exists.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The
lead NEVER builds directly.

### Team Members

- **Builder (lease-registry)**
  - Name: lease-builder
  - Role: Fix #2 — `agent/slot_lease.py` registry + `session_state.py` swap +
    migrate the 12 release sites + fingerprint→reclaim + `_apply_recovery_transition` reclaim
  - Agent Type: builder
  - Domain: async/concurrency (see DOMAIN_FRAMING.md — loop-affine asyncio objects,
    idempotent release, acquire-before-bind window)
  - Resume: true

- **Builder (progress-deadline)**
  - Name: deadline-builder
  - Role: Fix #3 — owned-task cancel scope at `agent_session_queue.py:1494` +
    fd-PTY-kill (scoped `killpg`) + reclaim + finalize
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Validator (resilience)**
  - Name: resilience-validator
  - Role: Verify both acceptance criteria + failure-path + race tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: lease-doc
  - Role: `docs/features/slot-lease-ownership.md` + index + forward-links
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build the lease registry (Fix #2 foundation)
- **Task ID**: build-lease-registry
- **Depends On**: none
- **Validates**: tests/unit/test_slot_lease_registry.py (create), tests/integration/test_worker_concurrency.py, tests/integration/test_slot_lease_reclaim.py (create)
- **Assigned To**: lease-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/slot_lease.py` (`Lease` dataclass + `SlotLeaseRegistry` with
  acquire/bind/release/reclaim/leases/permits_free; idempotent release/reclaim;
  unbound-permit tracking + bind-grace).
- Swap `session_state.py:76` to `_slot_registry`; init in `_run_worker()`; update
  the `agent_session_queue.py:126` re-export.
- Migrate the acquire (`:1330`) to the token model and all 12 release sites to
  `registry.release(session_id)`; add `registry.bind(...)` right after `_pop_agent_session`.
- Convert the `session_health.py:2560-2613` fingerprint to a reap pass (reclaim
  terminal/expired leases, `SLOT_LEASE_REAP_DISABLED` gate, telemetry counter).
- Wire `registry.reclaim(session_id)` into `_apply_recovery_transition`.

### 2. Build the progress-deadline cancel scope (Fix #3)
- **Task ID**: build-progress-deadline
- **Depends On**: build-lease-registry
- **Validates**: tests/integration/test_progress_deadline_cancel.py (create)
- **Assigned To**: deadline-builder
- **Agent Type**: builder
- **Parallel**: false
- At `agent_session_queue.py:1494`, run `_execute_agent_session` as an owned task
  under a progress-deadline watcher (`_session_progress_ts` = max of
  `last_tool_use_at`/`last_turn_at`/`acquired_at`).
- On expiry: fd-PTY-kill via the scoped `container.py` `killpg` path → reclaim →
  finalize via `_apply_recovery_transition` (reason `progress_deadline`).
- Reuse `DISABLE_PROGRESS_KILL` kill-switch; add `SESSION_PROGRESS_DEADLINE_S` /
  `PROGRESS_POLL_S` constants (provisional, commented).

### 3. Validate both fixes
- **Task ID**: validate-resilience
- **Depends On**: build-lease-registry, build-progress-deadline
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run new + updated tests; verify both acceptance criteria, all failure-path items,
  and the three race scenarios. Confirm no regression in `TestGlobalSemaphore` and
  the dashboard running-count contract.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-resilience
- **Assigned To**: lease-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/slot-lease-ownership.md`; add README index entry;
  forward-link the investigation + liveness-recovery docs.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full verification table; confirm the doc deliverable exists; generate the
  final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lease registry tests pass | `pytest tests/unit/test_slot_lease_registry.py -q` | exit code 0 |
| Reclaim acceptance test passes | `pytest tests/integration/test_slot_lease_reclaim.py -q` | exit code 0 |
| Progress-deadline acceptance test passes | `pytest tests/integration/test_progress_deadline_cancel.py -q` | exit code 0 |
| Concurrency tests pass | `pytest tests/integration/test_worker_concurrency.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/ worker/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ worker/` | exit code 0 |
| Registry exists | `grep -c "class SlotLeaseRegistry" agent/slot_lease.py` | output > 0 |
| Raw semaphore fully removed (no legacy shim) | `grep -rc "_global_session_semaphore" agent/ worker/` | match count == 0 |
| Fingerprint became a reclaim | `grep -c "reclaim" agent/session_health.py` | output > 0 |
| Killer reclaims the slot | `grep -c "reclaim" agent/session_health.py` | output > 0 |
| Progress-deadline cancel present | `grep -c "SESSION_PROGRESS_DEADLINE_S" agent/agent_session_queue.py` | output > 0 |
| fd-PTY-kill uses scoped teardown (not machine pkill) | `grep -c "pkill" agent/agent_session_queue.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Reaper location — "off-loop" reframing.** The issue says "off-loop reaper,"
   but `asyncio.Semaphore` is loop-affine — releasing from the watchdog thread is
   unsafe. The plan runs the reaper **on-loop** in the health check (a task
   distinct from the parked worker loop, which is the real requirement). Confirm
   this satisfies the intent, or do we want a `loop.call_soon_threadsafe` bridge
   from the #1815 watchdog thread as well?
2. **Bound the lease acquire?** Should `registry.acquire()` be bounded by
   `wait_for(SLOT_LEASE_TTL_S)` (a worker loop can't wait forever for a slot), or
   is unbounded backpressure correct (the reaper guarantees slots free up)? Plan
   assumes unbounded acquire + reaper-guaranteed liberation; a bound would add a
   loud "pool wedged" signal at the cost of a new failure mode.
3. **Fix #3 primacy vs the existing out-of-band progress-kill.** The plan makes the
   in-scope cancel the fast path and keeps the out-of-band killers as backstops
   (all converging on idempotent reclaim). Is that the right division, or should
   Fix #3 *replace* the out-of-band progress-kill entirely (simpler, but loses the
   backstop for sessions the in-scope watcher can't reach)?
