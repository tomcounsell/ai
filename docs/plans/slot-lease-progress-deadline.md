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
killers — but those killers run from the health loop, not the worker loop that owns
the slot: they can cancel `handle.task` if a registry handle happens to be populated
(`session_health.py:2019`) but **cannot release the ownerless slot** and **do not
fd-kill the session's PTY**. A session parked with no progress therefore has its DB
row flipped by an out-of-band killer while its slot stays leaked and its PTY process
survives.

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

**File:line references re-verified against current HEAD `9d47033e` (corrected inline in Technical Approach):**
- `agent/session_state.py:76` — `_global_session_semaphore: asyncio.Semaphore | None = None` — still the ownerless semaphore. Initialized at `worker/__main__.py:649` (`_ss._global_session_semaphore = asyncio.Semaphore(_max_sessions)`); re-exported at `agent_session_queue.py:126`; read into the local `semaphore` var at `agent_session_queue.py:1328`.
- `agent/agent_session_queue.py:1494` — the `await _execute_agent_session(session)` try block — **still holds** (execute call at `:1494`; acquire is at `:1330`, `_semaphore_acquired` flag at `:1331`).
- **CancelledError handler at `agent_session_queue.py:1496-1514`** — re-verified: the `except asyncio.CancelledError` branch logs *"session interrupted, will be re-queued by startup recovery"*, sets `session_completed = True`, and **re-raises** to exit the worker loop, deliberately NOT finalizing (so startup recovery re-queues). **This is the handler Blocker 1 must disambiguate** — a progress-deadline cancel must NOT be misclassified as worker-shutdown-interrupt. `finalized_by_execute` flag is at `:1487` (set True only on non-exceptional return of `_execute_agent_session`); the outer `finally` at `:1583-1659` runs `_complete_agent_session` + `semaphore.release()` only when `not session_completed and not finalized_by_execute`.
- `agent/agent_session_queue.py` release sites — **12 confirmed**: `:1349,1354,1360,1398,1403,1419,1424,1438,1443,1463,1473,1658`. **5 acquire sites confirmed**: `:1330,1392,1413,1430,1455`. **Every one uses the local `semaphore` variable** (assigned from `_global_session_semaphore` at `:1328`), NOT the module global directly — so a `_global_session_semaphore` grep does **not** catch them (Concern 1). The re-acquire sites (`:1392/1413/1430/1455`) live on the drain/standalone/bridge/fallback branches, each with its own release-on-None/exception guard.
- `agent/session_health.py:2560-2613` — leaked-slot fingerprint — **re-verified and drift corrected: it is NESTED inside `for entry in pending_sessions:` (loop at `:2553`) and runs ONLY when `worker_alive` is True (`:2560`).** A literal in-place edit would run the reap N-times-per-tick and skip it entirely on a drained queue (Blocker 2). The enclosing function `_agent_session_health_check` begins at `:2330`; `now = time.time()` is at `:2380`; the SIGKILL-escalation drain runs `:2385-2409` before the RUNNING scan (`:2418`). The reap must be hoisted to a single top-of-tick pass here.
- **Out-of-band `no_progress` decision path** — re-verified: the running-session scan classifies `reason_kind` at `:2526-2529` (`no_progress` when `worker_alive` but `not _has_progress`, `:2506-2517`; `worker_dead` when the worker future is dead, `:2482-2494`) and delegates to `_apply_recovery_transition` (`:2537`). The `DISABLE_PROGRESS_KILL` gate is at `:1994`; the Tier-2 reprieve (active-children / compaction) is at `:1962-1991` and is gated on `reason_kind == "no_progress"`. `_apply_recovery_transition` already cancels `handle.task` at `:2019-2024` when a registry handle is populated. **The `worker_dead` branch cannot be owned by Fix #3** (an in-scope watcher is dead if the worker loop is dead); **the worker-alive `no_progress` branch is exactly what Fix #3 supersedes** (see OQ3 resolution).
- Tool-timeout loop — `_agent_session_tool_timeout_loop` at `:3450` (interval `TOOL_TIMEOUT_LOOP_INTERVAL=30s`), check at `_agent_session_tool_timeout_check` (`:3223`), finalizes via `_apply_recovery_transition(reason_kind="tool_timeout")` (`:3308`). Covers per-tool-tier wedges at finer cadence than Fix #3.
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

**Fix #2 — lease acquire / reclaim** (lease recorded at `bind()` only — no
token/unbound sub-system; see "unbound-permit simplification" in Technical Approach):
1. **Acquire (anonymous):** worker loop needs a slot → `agent_session_queue.py:1330`.
   Today: `await semaphore.acquire()`. New: `await registry.acquire()` — this just
   awaits the wrapped `asyncio.Semaphore`, decrementing the permit count (so
   `permits_free` stays accurate) WITHOUT recording any lease. Acquire happens
   *before* `_pop_agent_session` to keep the running-count accurate, per the
   existing comment at `:1325`.
2. **Release-before-bind:** every branch where the pop returns `None` or raises
   before a lease exists (the StatusConflict/BaseException/None branches at
   `:1349,1354,1360` and each re-acquire branch at `:1398,1403,1419,1424,1438,1443,
   1463,1473`) calls `registry.release_unbound()` — a raw `semaphore.release()`
   that bumps the permit back with no lease bookkeeping. Because bind is synchronous
   after a non-None pop (step 3), an acquired-but-unbound permit **cannot outlive the
   pop** — during the `await _pop_agent_session` gap the permit is legitimately
   in-use and simply absent from the lease map, so the reaper (which iterates only
   bound leases) never observes it. This dissolves the acquire-before-bind leak
   entirely (old Risk 2 / Race 2 removed).
3. **Bind (single site):** once a non-None `session` is resolved and about to run
   (just before the `try: await _execute_agent_session(session)` at `:1493`), call
   `registry.bind(session.agent_session_id, deadline)` — synchronous, no await
   between the resolving pop and this bind. The lease now records
   `(owner_session_id, acquired_at, deadline=acquired_at + SLOT_LEASE_TTL_S)`.
4. **Normal release:** the worker loop's `finally` at `:1658` calls
   `registry.release(session.agent_session_id)` (idempotent) instead of
   `semaphore.release()`.
5. **Reaper reclaim (the new recovery path):** on **each** health-check tick
   (`_agent_session_health_check`, 300s), a SINGLE top-of-tick pass (hoisted ABOVE
   the pending-sessions loop, independent of `worker_alive`, gated only by
   `SLOT_LEASE_REAP_DISABLED`) iterates a snapshot `list(registry.leases())`; for
   any lease whose `owner_session_id` (re-read fresh) is in `TERMINAL_STATUSES`
   **or** whose `deadline` has passed, call `registry.reclaim(owner)` — releases the
   permit, drops the lease, increments the `slot_reclaims` counter, logs at WARNING.
   The counter (and a zero-reclaim heartbeat) is emitted **unconditionally** every
   tick, even when the queue is drained (the parked-worker/empty-queue starvation
   case Acceptance #1 targets).
6. **Prompt reclaim:** `_apply_recovery_transition` (the out-of-band killer) also
   calls `registry.reclaim(session_id)` immediately after flipping the row, so the
   slot frees within the health/tool-timeout cadence instead of waiting for the
   300s reap tick.
7. **Output:** `permits_free` recovers; the worker loop unblocks at `acquire()`.

**Fix #3 — progress-deadline cancel scope** (the single authoritative no-progress
killer for worker-alive RUNNING sessions; see OQ3 resolution):
1. **Entry point:** `agent_session_queue.py:1494`. New: run execution as an owned
   child task — `exec_task = asyncio.create_task(_execute_agent_session(session))` —
   and a `deadline_cancelled = False` flag in loop scope.
2. **Deadline watch:** a small on-loop watcher computes
   `last_progress = max(last_tool_use_at, last_turn_at, acquired_at)` for the session
   and, if `now - last_progress > SESSION_PROGRESS_DEADLINE_S` while `exec_task` is
   not done, consults the shared no-progress kill gate (the extracted Tier-2 reprieve
   predicate — active-children/compaction — so a `waiting_for_children` PM is NOT
   falsely killed). The deadline is fed by **progress**, never wall-clock (a session
   making tool calls resets it).
3. **On expiry (gate says kill):** set `deadline_cancelled = True`; **finalize FIRST**
   — (a) fd-level PTY kill (scoped process-group teardown of the session's granite
   slot via the `container.py` `os.killpg` path from #1816); (b)
   `registry.reclaim(session.agent_session_id)`; (c) transition to terminal via the
   shared `_apply_recovery_transition` semantics (`reason_kind="progress_deadline"`,
   idempotent) — THEN `exec_task.cancel()` and `await exec_task`.
4. **CancelledError disambiguation (Blocker 1):** the `except asyncio.CancelledError`
   branch at `:1496` inspects `deadline_cancelled`. If `True` → the session is already
   finalized + reclaimed; **swallow** the `CancelledError` (no "will be re-queued"
   log, no re-raise) so it never reaches the worker-shutdown classifier and the
   session is NOT re-queued into an infinite loop. If `False` → the existing
   worker-shutdown path runs (log "interrupted, will be re-queued", set
   `session_completed=True`, re-raise). Reconcile with `finalized_by_execute` (#898):
   because Fix #3 finalizes via `_apply_recovery_transition` before the cancel, the
   outer `finally` sees a terminal row and its terminal-status idempotency prevents a
   double transition.
5. **Output:** the parked session is finalized, its slot freed, its PTY dead — all
   from the scope that owned the task, and the deadline-cancel is never confused with
   a worker shutdown.

## Architectural Impact

- **New dependencies:** none (stdlib `asyncio`, `time`, existing `os.killpg`).
- **Interface changes:** `agent/session_state.py` replaces the raw
  `_global_session_semaphore: asyncio.Semaphore` with a `SlotLeaseRegistry`
  instance (new class, likely `agent/slot_lease.py`) that *wraps* an
  `asyncio.Semaphore` for backpressure and adds `acquire()`, `release_unbound()`,
  `bind(owner, deadline)`, `release(owner)`, `reclaim(owner)`, `leases()`,
  `permits_free()`. The **5 `semaphore.acquire()` and 12 `semaphore.release()` sites**
  in `agent_session_queue.py` (all via the local `semaphore` var, `:1328`) and the
  `_sem._value` read in `session_health.py` migrate to registry methods. **No legacy
  shim** — the raw semaphore is fully removed (NO LEGACY CODE TOLERANCE). A lease is
  recorded **only at `bind()`** (owner-keyed); `acquire()` records nothing, so there
  is no token/unbound-permit sub-system to leak or reap.
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
  "off-loop reaper"; OQ3 single-killer decision already resolved in-plan)
- Review rounds: 2 (async correctness of the registry; cancel-scope + CancelledError
  disambiguation + fd-PTY-kill ordering)

**PR strategy (split — nit).** Fix #2 (lease registry + hoisted reap +
`_apply_recovery_transition` reclaim) alone satisfies **Acceptance #1** and is the
lower-risk half — it should land as its own PR first, against which the concurrency
tests and `test_slot_lease_reclaim.py` stabilize. Fix #3 (progress-deadline cancel
scope + Blocker-1 CancelledError disambiguation + OQ3 branch deletion) then lands as
a follow-up PR against the stable registry, satisfying **Acceptance #2**. This keeps
each PR reviewable and isolates the highest-blast-radius change (the worker-loop
cancel scope) behind an already-merged, tested registry. The Step-by-Step tasks are
already ordered Fix #2 → Fix #3 to support this split; if the builder chooses one PR,
it must still gate Fix #3 behind green Fix #2 tests in the same branch.

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
  map. A lease exists **only after `bind()`** — there is no separate unbound-permit
  tally. Methods: `acquire()` (awaits the wrapped semaphore, records nothing),
  `release_unbound()` (raw `semaphore.release()` for a permit released before bind),
  `bind(owner_session_id, deadline)`, `release(owner_session_id)` (idempotent),
  `reclaim(owner_session_id)` (idempotent), `leases()`, `permits_free()`.
- **Fingerprint → reclaim, hoisted** (`session_health.py`): the logging-only block
  currently nested in the pending loop is REMOVED from that loop and replaced by a
  single top-of-tick reap pass over a snapshot of `registry.leases()`, reclaiming
  leases whose owner is terminal or whose deadline expired — runs unconditionally
  every tick, independent of `worker_alive` and of any pending session.
- **Prompt reclaim in the killer** (`_apply_recovery_transition`): reclaim the
  slot immediately when an out-of-band kill flips the row.
- **Progress-deadline cancel scope** (`agent_session_queue.py:1494`): own the
  execution task; on no-progress-past-deadline (reprieve gate consulted) finalize
  (fd-PTY-kill + reclaim + terminal transition) then cancel, with a
  `deadline_cancelled` flag so the `CancelledError` handler swallows instead of
  re-queuing. This is the single authoritative killer for worker-alive running
  sessions (the out-of-band worker-alive `no_progress` branch is deleted; OQ3).
- **Env kill-switches** (all NAMED, env-overridable, conservative-provisional):
  `SESSION_PROGRESS_DEADLINE_S`, `SLOT_LEASE_TTL_S` (absolute lease TTL backstop,
  distinct from the progress deadline), `SLOT_LEASE_REAP_DISABLED`, reuse
  `DISABLE_PROGRESS_KILL` for the Fix #3 cancel.

### Flow

Worker loop → `await registry.acquire()` → pop session (None/exception →
`registry.release_unbound()`) → `registry.bind(session_id, deadline)` → run
`exec_task` under the progress-deadline watcher →
**normal completion** → `registry.release(session_id)` /
**no progress past deadline** → fd-PTY-kill → `registry.reclaim(session_id)` →
finalize (`_apply_recovery_transition`) → set `deadline_cancelled` → cancel
`exec_task` → `CancelledError` handler swallows (no requeue).

Independently, health tick → single top-of-tick pass iterates `registry.leases()`
→ any owner terminal or
deadline expired → `registry.reclaim(owner)` (this is the leaked-permit safety net
that needs no restart).

### Technical Approach

**Fix #2 — lease-based slot ownership:**

*Unbound-permit simplification (Concern 2 — adopted).* A lease is recorded **only at
`bind()`**, keyed by `owner_session_id`. The pop path already releases the permit on
every exception/None branch (`:1349-1360` and each re-acquire branch), and `bind` is
synchronous immediately after a non-None pop — so an acquired-but-unbound permit
**cannot leak**: during the `await _pop_agent_session` gap the permit is legitimately
in use and simply absent from the lease map, and after pop it is either given back
(`release_unbound()`) or bound. Therefore `acquire()` just awaits the wrapped
semaphore (permit count decremented, `permits_free` accurate) and a reaper over
`_held.items()` never observes an unbound permit. **This dissolves the old Risk 2,
Race 2, `SLOT_LEASE_BIND_GRACE_S`, and the bind-grace reclaim** (all removed below),
and simplifies the re-acquire handling of Concern 1 (each None/exception branch just
calls `release_unbound()`).

- Add `agent/slot_lease.py`: `Lease` dataclass `(owner_session_id, acquired_at,
  deadline)` + `SlotLeaseRegistry`. The registry holds one `asyncio.Semaphore` so
  the worker loop still blocks at `acquire()` when full — the counting-semaphore
  backpressure contract is preserved exactly. All mutation is on-loop (no lock
  needed beyond the loop's cooperative scheduling; document this).
- `acquire()` `await`s the wrapped semaphore and records nothing. Unbounded
  (backpressure is legitimate; the reaper guarantees liberation — see OQ2).
- `release_unbound()` = raw `semaphore.release()` for a permit acquired but never
  bound (pop returned None / raised). No lease bookkeeping.
- `bind(owner_session_id, deadline)` records `_held[owner] = Lease(owner,
  acquired_at, deadline=acquired_at + SLOT_LEASE_TTL_S)`. Called once, synchronously,
  right before the `try: await _execute_agent_session(session)` block (`:1493`),
  where a non-None `session` is resolved with no intervening await.
- `release(owner_session_id)`: if `owner in _held`, pop it and `semaphore.release()`;
  else **no-op**. The lease map is the single source of truth gating the underlying
  release, so a double-release or unknown-owner release can never over-release the
  permit — critical since both the loop `finally` and the reaper may fire for the
  same owner.
- `reclaim(owner_session_id)` = `release` + telemetry + WARNING log; also idempotent.
- `session_state.py:76`: replace `_global_session_semaphore: asyncio.Semaphore | None`
  with `_slot_registry: SlotLeaseRegistry | None`, initialized at
  `worker/__main__.py:649` exactly where the semaphore is today. Update the re-export
  at `agent_session_queue.py:126`.
- **Migrate ALL slot sites (Concern 1), not just `:1330`.** The re-acquire sites use
  the local `semaphore` variable, so a `_global_session_semaphore` grep misses them —
  each must be migrated by hand:
  - `:1330,1392,1413,1430,1455` (5 acquire sites) → `await registry.acquire()`.
  - `:1349,1354,1360,1398,1403,1419,1424,1438,1443,1463,1473` (11 release sites, all
    on None/exception branches that fire **before** bind) → `registry.release_unbound()`.
  - `:1658` (the `finally`, after bind) → `registry.release(session.agent_session_id)`.
  - Keep a local `_slot_acquired` bool (replacing `_semaphore_acquired`) to know
    whether to `release_unbound()` on the None/exception branches; add the single
    `registry.bind(...)` call at `:1493`.
  - Verification greps: `grep -c "semaphore\.acquire(" agent/agent_session_queue.py == 0`
    and `grep -c "semaphore\.release(" agent/agent_session_queue.py == 0` (all sites
    migrated to registry methods).
- **`session_health.py` reap — hoisted to a single top-of-tick pass (Blocker 2).**
  DELETE the logging-only fingerprint block from inside `for entry in pending_sessions:`
  (`:2560-2613`). Add, near the top of `_agent_session_health_check` (after `now =
  time.time()` at `:2380`, alongside the SIGKILL-escalation drain, ABOVE the RUNNING
  scan at `:2418`), a single reap pass: for each `lease in list(registry.leases())`
  (snapshot), if `lease.owner_session_id` (re-read fresh, terminal-status-guarded like
  the existing tool-timeout path) is in `TERMINAL_STATUSES` or `now > lease.deadline`,
  `registry.reclaim(owner)` + increment `{project_key}:session-health:slot_reclaims`.
  The pass is **independent of `worker_alive`** (so it fires on a drained queue —
  the exact parked-worker starvation case Acceptance #1 targets), gated **only** by
  `SLOT_LEASE_REAP_DISABLED`, and emits the `slot_reclaims` counter (plus a
  zero-reclaim heartbeat) **unconditionally** every tick. The healthy-backpressure
  INFO line (`permits_free==0 AND running>=max`) is computed once in this top-of-tick
  pass too — decoupled from pending iteration.
- `_apply_recovery_transition`: after the transition, call
  `registry.reclaim(session_id)` (idempotent) so out-of-band kills free the slot
  promptly. This is the wiring that makes acceptance criterion #1 fire on the
  tool-timeout/health cadence, not the 300s reap tick.

**Fix #3 — progress-deadline cancel scope:**

- Near `agent_session_queue.py:1494`, replace `await _execute_agent_session(session)`
  with an owned-task pattern that carries a `deadline_cancelled` flag and **finalizes
  before it cancels** (Blocker 1):
  ```
  deadline_cancelled = False
  exec_task = asyncio.create_task(_execute_agent_session(session))
  try:
      while not exec_task.done():
          done, _ = await asyncio.wait({exec_task}, timeout=PROGRESS_POLL_S)
          if exec_task in done:
              break
          last = _session_progress_ts(session)  # max(last_tool_use_at, last_turn_at, acquired_at)
          if last is not None and (time.time() - last) > SESSION_PROGRESS_DEADLINE_S:
              if os.environ.get("DISABLE_PROGRESS_KILL") == "1":
                  break  # kill-switch: let it run
              if not _should_kill_no_progress(session):  # Tier-2 reprieve gate
                  continue  # active children / compaction — reprieve, keep watching
              deadline_cancelled = True
              # FINALIZE FIRST — at the watcher scope, before cancel reaches :1496:
              _fd_pty_kill(session)                      # scoped os.killpg (#1816)
              registry.reclaim(session.agent_session_id) # free the slot
              await _apply_recovery_transition(          # terminal-guarded, idempotent
                  session, reason="progress deadline exceeded",
                  reason_kind="progress_deadline", handle=..., worker_key=worker_key)
              exec_task.cancel()
              break
      await exec_task  # propagate result / CancelledError
      finalized_by_execute = True  # only on non-exceptional return
  except asyncio.CancelledError:
      if deadline_cancelled:
          # Already finalized+reclaimed above — SWALLOW. Do NOT log "will be
          # re-queued", do NOT re-raise. Prevents the requeue-loop (Blocker 1).
          pass
      else:
          ... existing worker-shutdown path (log "interrupted", session_completed=True, raise)
  ```
  This keeps the loop ticking (bumping `last_loop_tick`) while watching progress —
  it is NOT a wall-clock cap and resets on any tool/turn activity.
- **Blocker 1 — CancelledError disambiguation.** `deadline_cancelled` is set `True`
  immediately before `exec_task.cancel()`. The existing `except asyncio.CancelledError`
  at `:1496` (which today unconditionally logs "session interrupted, will be
  re-queued by startup recovery", sets `session_completed=True`, and re-raises)
  is split on the flag: `deadline_cancelled` True → the session is already terminal +
  reclaimed, so swallow (no requeue log, no re-raise, no re-raise into the
  worker-loop handler at `:1496-1514`); False → the existing worker-shutdown path is
  unchanged. Because Fix #3 finalizes **before** `await exec_task`, the terminal
  transition is committed before any `CancelledError` propagates, so a requeue can
  never re-land the session before finalize. Reconcile with `finalized_by_execute`
  (#898): the deadline path leaves `finalized_by_execute=False` but the row is already
  terminal, so the outer `finally` (`:1583`) — which runs only when `not
  session_completed and not finalized_by_execute` — hits `_apply_recovery_transition`'s
  terminal-status idempotency and does NOT double-finalize or double-reclaim.
- On expiry the order is fixed: (a) fd-level PTY kill via the granite container's
  scoped process-group teardown (`container.py` `os.killpg` path, #1816) for the
  session's slot; (b) `registry.reclaim(session.agent_session_id)`; (c) finalize via
  `_apply_recovery_transition` (`reason_kind="progress_deadline"`); (d) cancel +
  await. Steps a-c reuse terminal-status idempotency so an out-of-band killer racing
  the same session is harmless.
- Reuse `DISABLE_PROGRESS_KILL=1` as the kill-switch (parity with the tool-timeout
  loop).
- **OQ3 resolution — single authoritative killer per running session.** Fix #3
  becomes the sole no-progress killer for **worker-alive** RUNNING sessions. In the
  same change, DELETE the out-of-band worker-alive `no_progress` branch
  (`session_health.py:2506-2517`, the `elif ... not _has_progress(entry)` arm and its
  `_reason_kind = "no_progress"` classification at `:2526-2527`). To preserve the
  Tier-2 reprieve semantics that branch provided, EXTRACT the reprieve decision
  (`_has_progress` + `_tier2_reprieve_signal`, currently `_apply_recovery_transition:
  1962-1991`) into a shared predicate `_should_kill_no_progress(session)` that Fix #3
  consults before cancelling — so a `waiting_for_children` PM with no own tool/turn
  activity is reprieved, not killed. The reprieve gate is evaluated **once** — by the
  watcher via `_should_kill_no_progress` before cancelling; `_apply_recovery_transition`
  invoked with `reason_kind="progress_deadline"` SKIPS its own internal reprieve block
  (which was gated on `reason_kind == "no_progress"`, a kind no scan now produces) so
  the reprieve telemetry is not double-counted. **Residual ownership (no overlap):**

  | Killer | Owns | Cadence | Why it can't be Fix #3 |
  |--------|------|---------|------------------------|
  | Fix #3 in-scope watcher | worker-**alive** running session, no progress past deadline, reprieve gates failed | worker-loop poll (`PROGRESS_POLL_S`) | — (this IS Fix #3) |
  | out-of-band `worker_dead` (`session_health.py:2482-2494`) | running session whose **worker loop is dead** | 300s health tick | an in-scope watcher is dead when the worker loop is dead |
  | `tool_timeout` loop (`:3450`) | a **tool in flight** past its per-tier budget | 30s | finer per-tool granularity; distinct trigger (`current_tool_name` non-null) |

  `SESSION_PROGRESS_DEADLINE_S` is set **≥ the maximum tool-timeout tier** so
  `tool_timeout` always fires first for a tool-in-flight wedge and Fix #3 only catches
  the residual (no tool in flight / model-inference stall / wedged between tool calls).
  All three converge on idempotent `reclaim` + terminal-guarded
  `_apply_recovery_transition`, so any cross-boundary race is harmless. This satisfies
  NO-LEGACY / no-parallel-systems: exactly one authoritative killer per running
  session, with the two survivors owning provably disjoint cases Fix #3 cannot reach.

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
- [ ] A permit acquired but released before bind (pop returned None / raised) is
  given back via `release_unbound()` and is NEVER a lease — the reaper (which iterates
  only bound leases) cannot observe or reclaim it. Test: `acquire()` then
  `release_unbound()` leaves `permits_free` restored and `leases()` empty.
- [ ] A **progress-deadline cancel is NOT re-queued** (Blocker 1). Test: with
  `deadline_cancelled=True`, the `CancelledError` handler swallows (no "will be
  re-queued" log, session stays terminal), and the finalize/reclaim already ran — the
  session is not resurrected by startup recovery. Contrast: a worker-shutdown cancel
  (`deadline_cancelled=False`) still logs + re-raises + leaves the row `running` for
  requeue.

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
- [ ] Any test reading `_global_session_semaphore` / `_sem._value` directly — UPDATE to the registry accessor. Confirmed affected: `tests/integration/test_worker_concurrency.py` (10 refs at `:131,135,173,182,187,189,197,244,246,261,304,306,326,364,366,377,418,420,436`) and `tests/integration/test_worker_wedge_pending.py` (`:197,204,292,350,356,400,424,457`) all set/read `_global_session_semaphore` directly — retarget to `SlotLeaseRegistry` init + `permits_free()`.
- [ ] Any test asserting the out-of-band worker-alive `no_progress` kill (reason_kind `no_progress` from the running-session scan) — UPDATE/REPLACE: that branch is deleted (OQ3); the equivalent kill is now Fix #3's in-scope watcher (`test_progress_deadline_cancel.py`). The `worker_dead` and `tool_timeout` recovery tests are unaffected.

New tests (greenfield):
- `tests/unit/test_slot_lease_registry.py` — acquire/bind/release/reclaim happy path; double-reclaim idempotency (no over-release); `acquire()`+`release_unbound()` leaves no lease and restores `permits_free`; terminal-owner reclaim; deadline-expired reclaim; `permits_free` accounting.
- `tests/integration/test_slot_lease_reclaim.py` — end-to-end: orphan a slot (bind a lease to a session, transition it terminal without releasing), run the reap pass **on a drained queue with no live worker** (hoisted top-of-tick pass), assert `permits_free` recovers and a parked worker proceeds — **acceptance criterion #1**.
- `tests/integration/test_progress_deadline_cancel.py` — a session with no progress past `SESSION_PROGRESS_DEADLINE_S` is cancelled, its slot reclaimed, its PTY killed (mock/assert the `killpg` seam), and **NOT re-queued** (`deadline_cancelled` swallow path — Blocker 1); a session making steady progress is NOT cancelled; a `waiting_for_children` session with an active-children reprieve is NOT cancelled (OQ3 reprieve preservation) — **acceptance criterion #2**.

## Rabbit Holes

- **Do NOT build a new progress detector or a new reprieve rule.**
  `last_tool_use_at`/`last_turn_at`, the tool-timeout tiers, and the Tier-2 reprieve
  already exist. Fix #3 *consumes* them: it reuses `_apply_recovery_transition` and
  the extracted `_should_kill_no_progress` reprieve gate — it does not re-derive "no
  progress" or invent a second reprieve policy (that would recreate the
  parallel-system OQ3 explicitly removes).
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

### Risk 2: In-scope cancel races the out-of-band killer
**Impact:** The Fix #3 watcher cancels a session at the same instant the
tool-timeout loop (or `worker_dead` scan) transitions it → double finalize / double
reclaim.
**Mitigation:** Both converge on terminal-status-guarded
`_apply_recovery_transition` (already idempotent — `_TERMINAL_STATUSES` guard) and
idempotent `reclaim`. Whichever wins, the other no-ops. Test concurrent fire.

> **Note — old "acquire-before-bind window" risk removed.** With the Concern-2
> simplification (lease recorded only at `bind()`; reaper iterates bound leases
> only; `release_unbound()` on every pre-bind None/exception branch), there is no
> unbound permit for the reaper to observe or mis-reclaim, so the old Risk 2 / Race 2
> / `SLOT_LEASE_BIND_GRACE_S` apparatus is deleted rather than mitigated.

### Risk 3: Progress-deadline false-positive cancels a legitimately-long tool call
or a `waiting_for_children` session
**Impact:** A long-but-healthy tool call (e.g. a big build) with no intervening
tool/turn events past `SESSION_PROGRESS_DEADLINE_S`, or a `waiting_for_children` PM
session with no own tool/turn activity, gets falsely cancelled.
**Mitigation:** `SESSION_PROGRESS_DEADLINE_S` is conservative-provisional and set
**≥ the maximum tool-timeout tier** so `tool_timeout` fires first for tool-in-flight
wedges and Fix #3 never pre-empts it. `last_tool_use_at` is bumped on PreToolUse
(tool *start*), so a tool that has started but not finished keeps the deadline fresh.
The `waiting_for_children` / compaction case is covered by the **shared Tier-2
reprieve gate** (`_should_kill_no_progress`, extracted per OQ3) that Fix #3 consults
before cancelling — the same gate the deleted out-of-band `no_progress` branch used,
so no reprieve behavior is lost. `DISABLE_PROGRESS_KILL=1` is the instant kill-switch.
Tune after observing real stall-vs-legit histograms.

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

### Race 2: In-scope cancel vs session completing normally
**Location:** `agent_session_queue.py:1494` watcher vs `exec_task` finishing.
**Trigger:** `exec_task` completes in the same tick the deadline is judged expired.
**Data prerequisite:** `exec_task.done()` checked before cancel.
**State prerequisite:** `asyncio.wait({exec_task}, timeout=...)` returns `done`
before the deadline branch runs.
**Mitigation:** Check `exec_task in done` first and `break`; only evaluate the
deadline when the task is still pending. `exec_task.cancel()` on an already-done
task is a no-op, and `deadline_cancelled` is only set on the deadline branch, so a
natural completion never enters the swallow path.

### Race 3: Deadline-cancel vs worker-shutdown cancel (CancelledError source)
**Location:** the `except asyncio.CancelledError` handler at `:1496-1514`.
**Trigger:** A `CancelledError` reaches the handler — either from the Fix #3
deadline branch (`exec_task.cancel()`) or from the worker loop itself being
cancelled (shutdown/restart), which propagates into the same handler.
**Data prerequisite:** the loop-scope `deadline_cancelled` flag.
**State prerequisite:** `deadline_cancelled` is set `True` immediately before
`exec_task.cancel()`, and Fix #3 finalizes (reclaim + terminal transition) BEFORE
`await exec_task`, so the row is terminal before any `CancelledError` propagates.
**Mitigation:** the handler branches on `deadline_cancelled`: `True` → swallow (row
already terminal + reclaimed; no requeue log, no re-raise) so the deadline-kill never
reaches the worker-shutdown classifier and can never loop forever via requeue; `False`
→ the unchanged worker-shutdown path (log, `session_completed=True`, re-raise). This
is the mitigation Blocker 1 requires. Test both sources hit the correct branch.

> **Note — old "acquire/bind interleaving" race removed.** Dissolved by the
> Concern-2 simplification (see Risks note); the reaper never observes an unbound
> permit, so there is no acquire→pop→bind race to guard.

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
`PROGRESS_POLL_S`, `SLOT_LEASE_REAP_DISABLED`) are all optional with safe
defaults; add them to `.env.example` with a comment line above each (completeness-
check requirement) only for operator discoverability — no `.env` propagation is
required. (`SLOT_LEASE_BIND_GRACE_S` was dropped — the unbound-permit apparatus it
guarded no longer exists.) The worker is restarted by the standard
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
  semaphore leak class, the `SlotLeaseRegistry` (lease recorded at `bind()` only;
  owner+acquired_at+deadline), the hoisted top-of-tick reap pass (fingerprint→reclaim),
  the prompt reclaim wired into `_apply_recovery_transition`, the progress-deadline
  cancel scope + `deadline_cancelled` disambiguation + fd-PTY-kill, the single-
  authoritative-killer division (Fix #3 for worker-alive; `worker_dead` and
  `tool_timeout` for the disjoint residuals) with the shared reprieve gate, the env
  constants with provisional defaults, and the k8s-Lease / Go-context precedents. State it is the continuation of `worker-liveness-recovery.md` (#1815)
  and that #1821 (fixes #5/#6) builds on the registry. (Acceptance criterion of #1815.)
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Forward-link from `docs/features/worker-wedge-investigation.md` (the
  logging-only write-up) and `docs/features/worker-liveness-recovery.md` to this
  doc — describe the new status quo (the fingerprint now reclaims), per the
  no-historical-artifacts rule.

### Inline Documentation
- [ ] Comment the on-loop-only mutation assumption on `SlotLeaseRegistry` (no lock;
  loop-affine `asyncio.Semaphore`; why the reaper is on-loop not off-loop).
- [ ] Comment why a lease is recorded only at `bind()` (no token/unbound tally) and
  why `release_unbound()` on the pre-bind None/exception branches cannot leak.
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
  **5 acquire + 12 release** sites go through `SlotLeaseRegistry` — `grep -c
  "semaphore\.acquire(" agent/agent_session_queue.py == 0` and same for `.release(`.
- [ ] A progress-deadline cancel is finalized in-scope and **NOT re-queued**
  (`deadline_cancelled` swallow path) — the `CancelledError` handler no longer
  misclassifies it as a worker-shutdown interrupt (Blocker 1).
- [ ] Exactly one authoritative no-progress killer per running session (OQ3): the
  out-of-band worker-alive `no_progress` branch is deleted; `worker_dead` and
  `tool_timeout` remain for the disjoint cases Fix #3 cannot reach; Tier-2 reprieve
  is preserved via the shared `_should_kill_no_progress` gate.
- [ ] `_apply_recovery_transition` reclaims the slot on out-of-band kill (prompt
  recovery, not 300s-tick-only).
- [ ] The reap pass runs once per health tick, independent of `worker_alive` and of
  any pending session, and reclaims on a drained queue (Blocker 2).
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
  - Role: Fix #2 — `agent/slot_lease.py` registry (lease-at-bind-only) +
    `session_state.py` swap + migrate ALL 5 acquire / 12 release sites (Concern 1) +
    hoisted top-of-tick reap (Blocker 2) + `_apply_recovery_transition` reclaim
  - Agent Type: builder
  - Domain: async/concurrency (see DOMAIN_FRAMING.md — loop-affine asyncio objects,
    idempotent release, lease-at-bind-only / `release_unbound` on pre-bind branches)
  - Resume: true

- **Builder (progress-deadline)**
  - Name: deadline-builder
  - Role: Fix #3 — owned-task cancel scope at `agent_session_queue.py:1494` +
    `deadline_cancelled` CancelledError disambiguation (Blocker 1) + fd-PTY-kill
    (scoped `killpg`) + reclaim + finalize + OQ3 branch deletion & reprieve extraction
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
  `acquire`/`release_unbound`/`bind`/`release`/`reclaim`/`leases`/`permits_free`;
  idempotent release/reclaim; lease recorded ONLY at `bind()` — no token/unbound
  sub-system, no bind-grace).
- Swap `session_state.py:76` to `_slot_registry`; init at `worker/__main__.py:649`;
  update the `agent_session_queue.py:126` re-export.
- Migrate ALL slot sites (Concern 1): 5 acquire sites (`:1330,1392,1413,1430,1455`)
  → `await registry.acquire()`; 11 pre-bind release sites (`:1349,1354,1360,1398,1403,
  1419,1424,1438,1443,1463,1473`) → `registry.release_unbound()`; `:1658` finally →
  `registry.release(session_id)`; add the single `registry.bind(...)` at `:1493`;
  rename `_semaphore_acquired` → `_slot_acquired`. Confirm
  `grep -c "semaphore\.(acquire|release)(" == 0`.
- Hoist the `session_health.py:2560-2613` fingerprint OUT of the pending loop into a
  single top-of-tick reap pass in `_agent_session_health_check` (after `:2380`,
  independent of `worker_alive`, `SLOT_LEASE_REAP_DISABLED` gate, snapshot
  `list(registry.leases())`, reclaim terminal/expired leases, emit `slot_reclaims` +
  zero-reclaim heartbeat unconditionally). Move the healthy-backpressure INFO line
  into this pass too.
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
  `last_tool_use_at`/`last_turn_at`/`acquired_at`) with a `deadline_cancelled` flag.
- On expiry (reprieve gate `_should_kill_no_progress` says kill): finalize FIRST —
  fd-PTY-kill via the scoped `container.py` `killpg` path → reclaim → finalize via
  `_apply_recovery_transition` (reason `progress_deadline`) — THEN `exec_task.cancel()`.
- Blocker 1: split the `except asyncio.CancelledError` handler at `:1496` on
  `deadline_cancelled` — True → swallow (no requeue log/re-raise); False → unchanged
  worker-shutdown path. Reconcile with `finalized_by_execute` (terminal idempotency).
- OQ3: extract the Tier-2 reprieve into shared `_should_kill_no_progress`; DELETE the
  out-of-band worker-alive `no_progress` branch (`session_health.py:2506-2517` +
  `:2526-2527` classification). Keep `worker_dead` and `tool_timeout`.
- Reuse `DISABLE_PROGRESS_KILL` kill-switch; add `SESSION_PROGRESS_DEADLINE_S`
  (≥ max tool tier) / `PROGRESS_POLL_S` constants (provisional, commented).

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
| Local semaphore acquire sites migrated (Concern 1) | `grep -c "semaphore\.acquire(" agent/agent_session_queue.py` | == 0 |
| Local semaphore release sites migrated (Concern 1) | `grep -c "semaphore\.release(" agent/agent_session_queue.py` | == 0 |
| Fingerprint became a reclaim | `grep -c "reclaim" agent/session_health.py` | output > 0 |
| Killer reclaims the slot in `_apply_recovery_transition` | `grep -A40 "_apply_recovery_transition" agent/session_health.py \| grep -c reclaim` | output > 0 |
| Reap emits the reclaim counter | `grep -c "slot_reclaims" agent/session_health.py` | output > 0 |
| Deadline-cancel disambiguation present (Blocker 1) | `grep -c "deadline_cancelled" agent/agent_session_queue.py` | output > 0 |
| Progress-deadline cancel present | `grep -c "SESSION_PROGRESS_DEADLINE_S" agent/agent_session_queue.py` | output > 0 |
| Single-killer: worker-alive no_progress branch deleted (OQ3) | `grep -c "no_progress" agent/session_health.py` | fewer than baseline (branch + reason_kind removed) |
| Shared reprieve gate extracted (OQ3) | `grep -c "_should_kill_no_progress" agent/session_health.py` | output > 0 |
| fd-PTY-kill uses scoped teardown (not machine pkill) | `grep -c "pkill" agent/agent_session_queue.py` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | CancelledError source not disambiguated — deadline-cancel misclassified as worker-shutdown → requeue loop | Fix #3 Technical Approach + Data Flow + Race 3 | `deadline_cancelled` flag set before `exec_task.cancel()`; finalize before `await exec_task`; handler at `:1496` swallows on the flag; reconciled with `finalized_by_execute` (#898) |
| BLOCKER | Risk & Robustness | Reap inherits pending-loop nesting + `worker_alive` gate → never reclaims on drained queue | Fix #2 Technical Approach + Data Flow step 5 | Single top-of-tick pass in `_agent_session_health_check` above the pending loop, independent of `worker_alive`, `slot_reclaims` emitted unconditionally |
| CONCERN | Risk & Robustness (Skeptic) | Re-acquire sites beyond `:1330` use local `semaphore` var — grep won't catch | Fix #2 Technical Approach (Concern 1) + Verification | All 5 acquire / 12 release sites enumerated & migrated; `release_unbound()` on pre-bind branches; `grep -c "semaphore\.(acquire\|release)(" == 0` added |
| CONCERN | Scope & Value (Simplifier) | Unbound-permit sub-system unnecessary | Adopted — Fix #2 "unbound-permit simplification" | Lease recorded only at `bind()`; dissolves Risk 2, Race 2, `SLOT_LEASE_BIND_GRACE_S`, bind-grace reclaim |
| CONCERN | Scope & Value (User) | OQ3 keep-both vs replace out-of-band progress-kill — design fork unresolved | Resolved into Fix #3 Technical Approach (residual-ownership table) + OQ3 moved to resolved | Fix #3 single authoritative killer for worker-alive sessions; delete worker-alive `no_progress` branch; keep `worker_dead`+`tool_timeout`; reprieve preserved via shared gate |
| NIT | Scope & Value (Simplifier) | Fix #2/#3 bundled into one Large PR | Appetite → PR strategy | Split: Fix #2 first (Acceptance #1), Fix #3 follow-up against stable registry |
| NIT | History & Consistency | Duplicate verification grep (fingerprint vs killer rows) | Verification table | Killer row retargeted to `grep -A40 "_apply_recovery_transition" ... \| grep -c reclaim` |

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
   loud "pool wedged" signal at the cost of a new failure mode. (Note: `SLOT_LEASE_TTL_S`
   is still used as the bound lease's absolute-deadline backstop for the reaper —
   this question is only about whether to *also* bound the `acquire()` wait.)

> **Resolved (was OQ3) — Fix #3 primacy vs the out-of-band progress-kill.**
> **Decision: Fix #3 is the single authoritative no-progress killer for worker-alive
> RUNNING sessions; the out-of-band worker-alive `no_progress` branch
> (`session_health.py:2506-2517`) is DELETED in the same change.** Tier-2 reprieve is
> preserved by extracting the reprieve decision into a shared
> `_should_kill_no_progress` gate that Fix #3 consults before cancelling. The two
> survivors own provably disjoint cases Fix #3 cannot reach: `worker_dead` (the
> in-scope watcher is dead when the worker loop is dead) and `tool_timeout` (finer
> per-tool-tier cadence; `SESSION_PROGRESS_DEADLINE_S ≥ max tool tier` so it always
> fires first for a tool-in-flight wedge). This honors NO-LEGACY / no-parallel-systems
> — exactly one authoritative killer per running session — see the Fix #3 Technical
> Approach residual-ownership table for the full rationale.
