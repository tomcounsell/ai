# Slot-Lease Ownership + Progress-Deadline Cancel Scope

The worker's global concurrency slot is owned. Every held permit carries an
explicit `owner_session_id`, so any actor — the health-check reaper, an
out-of-band killer, or the owning worker loop — can release it idempotently. A
session parked with no progress past its deadline is cancelled from the scope
that owns its execution task: its slot is force-released and its runner
subprocess is torn down via task cancellation.

This is the continuation of [`worker-liveness-recovery.md`](worker-liveness-recovery.md)
(#1815), which landed the dead-man's-switch and deferred
these two fixes to #1820. Issue #1821 (fixes #5/#6) builds on the lease registry
described here.

## The leak class this removes

The global concurrency slot used to be an ownerless `asyncio.Semaphore`. A
permit was acquired by the worker loop and could only ever be released by that
same loop's `finally` block. When a session was killed **out of band** — by the
health-check progress-kill or the per-tool tier timeout, both of which run from
the health loop, not the worker loop — the DB row transitioned to terminal but
the permit stayed held by the parked worker loop. Nothing else could release it.

Permits leaked one at a time until `permits_free == 0` while `running_count <
max`, and the worker stopped picking up new work. Recovery required a human
`worker-restart`. The prior detector (the leaked-slot fingerprint) was
logging-only by design: it saw the leak and did nothing. This is the
#1537/#1808 incident class.

## `SlotLeaseRegistry`

`agent/slot_lease.py` wraps one `asyncio.Semaphore(max)` for backpressure and
records a `{owner_session_id: Lease}` map. The wrapped semaphore preserves the
exact counting-semaphore contract: the worker loop still blocks at `acquire()`
when the ceiling is full.

| Method | Behavior |
|--------|----------|
| `acquire()` | Awaits the wrapped semaphore. Records **no** lease. |
| `release_unbound()` | Raw `semaphore.release()` for a permit acquired but never bound. |
| `bind(owner)` | Records `Lease(owner, acquired_at)`. Called once, synchronously, right before execution. |
| `release(owner)` | Pops the lease and releases the permit. Idempotent no-op if unheld. |
| `reclaim(owner)` | Same as `release` plus a WARNING log and telemetry. Idempotent. |
| `leases()` | Snapshot list of held leases, safe to iterate. |
| `permits_free()` | Free-permit count (reads `Semaphore._value`). |

### Lease recorded at `bind()` only

A `Lease` is recorded **only** when `bind()` is called — never at `acquire()`.
There is no separate "unbound permit" tally or token system to leak or reap.
This is safe because of the worker loop's structure:

1. `acquire()` decrements the semaphore (so `permits_free()` stays accurate) and
   records nothing.
2. Every branch where the loop fails to resolve a session (the pop returns
   `None` or raises, before a lease exists) calls `release_unbound()`.
3. `bind()` is called synchronously immediately after a non-`None` pop, with no
   intervening `await` between the resolving pop and the bind.

During the `await _pop_agent_session(...)` gap the permit is legitimately in use
and simply absent from the lease map, so a reaper iterating only bound leases
never observes it. Immediately after the pop resolves, the permit is either
given back (`release_unbound()`) or bound. There is no window in which a permit
is "acquired but forgotten."

### No reclaim deadline

`Lease` deliberately carries no `deadline`/TTL field. `acquired_at` exists only
as the progress-timestamp fallback for the Fix #3 watcher. A fixed,
never-reset `acquired_at + TTL` cutoff would strip the permit from a
still-running, progressing owner while its execution task keeps running, causing
semaphore over-admission (concurrent sessions > max) and re-imposing exactly the
wall-clock duration cap #1172 deliberately removed. The reaper reclaims on
**terminal-owner status only**, never on elapsed time. Live-but-stuck sessions
are handled by the progress-deadline cancel scope (Fix #3), the per-tool timeout
loop, and the worker-dead scan.

### On-loop-only mutation

All registry state is mutated exclusively on the asyncio event loop — by the
worker loop (acquire/bind/release) and by the health-check reaper, which is a
different *task* on the *same* loop. No lock is used or needed:
`asyncio.Semaphore` is loop-affine, and every mutating method contains no
`await`, so each call runs to completion atomically with respect to the
cooperative scheduler.

## Fix #2 — reclaim path (leaked-slot self-heal)

### Hoisted top-of-tick reap

The health check (`_agent_session_health_check`, 300s) runs a single reap pass
at the top of the tick, hoisted above the pending-sessions loop and independent
of `worker_alive` — so it fires even on a drained queue (the parked-worker
starvation case). The pass has two phases:

- **Phase 1 — detection (always runs).** Computes and logs the leaked-slot
  fingerprint (WARNING when `permits_free == 0 AND running < max`; INFO on
  healthy backpressure) and emits the zero-reclaim heartbeat. This phase is
  never gated, so `SLOT_LEASE_REAP_DISABLED=1` preserves the old detect-and-log
  behavior exactly — the kill-switch disables reclaim, not visibility.
- **Phase 2 — reclaim (gated on `SLOT_LEASE_REAP_DISABLED` unset).** For each
  lease whose owner (re-read fresh) is in `TERMINAL_STATUSES`, calls
  `registry.reclaim(owner)` and increments the `slot_reclaims` counter. There is
  no wall-clock reclaim arm.

Between the two phases the tick drains bridge-pushed reclaim-requests and runs a
read-only `bridge_contract_stale` observability check. As of #1873 the drain
(`_drain_reclaim_requests`) returns `drained: int` and no longer calls the
stale-check; `_reap_slot_leases` builds an owner→record map once (only when
`drained == 0`) and passes it to `_maybe_emit_bridge_contract_stale`, decoupling
that read-only check from the drain. Phase-2 reclaim is unaffected — it still
re-reads each owner FRESH at reclaim time and never consults the map, which is
what prevents a `valor-session resume` during the bounded drain window from
having its live permit stripped. The healthy-tick reclaim-dedup clear now
enumerates its markers with a non-blocking `scan_iter` + batched delete instead
of a blocking `KEYS` scan. See
[`out-of-domain-recovery.md`](out-of-domain-recovery.md) for the full stale-check
and drain contract.

### Prompt reclaim in the killer

`_apply_recovery_transition` (the out-of-band killer) also calls
`registry.reclaim(session_id)` immediately after flipping the row, so the slot
frees on the health/tool-timeout cadence instead of waiting for the next 300s
reap tick.

## Fix #3 — progress-deadline cancel scope

The worker loop runs execution as an owned child task
(`exec_task = asyncio.create_task(_execute_agent_session(session))`) watched by
a small on-loop poller. When `now - last_progress > SESSION_PROGRESS_DEADLINE_S`
while `exec_task` is not done, the watcher consults the shared reprieve gate and,
if it says kill, finalizes then cancels.

### Progress signal

`_session_progress_ts(session, acquired_at)` is the max over every progress
signal that happens to be populated:

- `last_tool_use_at` — bumped by the PreToolUse/PostToolUse hooks.
- `last_turn_at` — bumped on the stream-json `result` event.
- `acquired_at` — the lease's bind timestamp — is the fallback baseline present
  for every session, so the deadline is always well-defined even for a
  never-started session with no other signal.

### Kill scope

Subprocess teardown rides `exec_task.cancel()`: the headless session runner's
harness kills its own `claude -p` child on cancellation (the runner spawns each
turn's subprocess in its own process group), and the worker-startup orphan
sweep reaps any survivor.

### The #1039 SessionHandle contract is honored

Fix #3 does **not** rewire the registry handle to `exec_task`.
`_execute_agent_session` keeps its own `_active_sessions[sid] =
SessionHandle(task=None)` and later sets `handle.task = task._task` (the inner
SDK task) once `BackgroundTask.run()` has created it. `BackgroundTask` absorbs
cancellation of `task._task` (its coroutine catches `CancelledError` and
completes normally), so an out-of-band killer cancelling `handle.task` tears down
the SDK subprocess without propagating `CancelledError` into the worker loop —
the worker survives. Wiring `handle.task = exec_task` would tear down the healthy
worker on such a cancel and would be silently reversed by the executor anyway.
Fix #3's own deadline cancel targets the local `exec_task` reference directly;
the runner subprocess is torn down by that cancellation (the harness kills its
own `claude -p` child).

### `CancelledError` disambiguation

On expiry the order is fixed: (a) `registry.reclaim(...)`;
(b) `_apply_recovery_transition(reason_kind="progress_deadline")`, capturing the
return; (b') if recovery declined (`MAX_RECOVERY_ATTEMPTS` / OOM-defer), re-read
the row fresh and force `finalize_session(..., "cancelled")` only if not already
terminal (a concurrent killer may have won the race — do not overwrite its
terminal state); (b'') set `finalized_by_execute=True` so the outer `finally` is
skipped; (c) `exec_task.cancel()` + `await`.

The `except asyncio.CancelledError` branch inspects `deadline_cancelled`:

- **`True`** — the session is already finalized + reclaimed; swallow the
  `CancelledError` (no "will be re-queued" log, no re-raise), so it never reaches
  the worker-shutdown classifier and the session is not re-queued into a loop.
- **`False`** — genuine worker shutdown. Because `asyncio.wait({exec_task})` does
  not cancel its watched task when the waiter is cancelled, the branch first
  cancels `exec_task` and bounded-awaits it (`asyncio.wait_for(exec_task,
  TASK_CANCEL_TIMEOUT)`) to tear down the orphaned runner subprocess, then logs
  "interrupted", sets `session_completed=True`, and re-raises so startup recovery
  re-queues — but only after teardown.

### Single authoritative killer per running session

Fix #3 is the sole no-progress killer for **worker-alive** RUNNING sessions. The
reprieve decision and its telemetry live in one shared predicate,
`_should_kill_no_progress(session, handle, emit_telemetry=...)`, called by both
Fix #3's watcher and `_apply_recovery_transition`'s never-started
`no_progress` path. The tier1/tier2 counters fire exactly once per session (on
the first kill-decision, latched by `np_telemetry_emitted`), never once per
`PROGRESS_POLL_S` tick for a long-reprieved session. A `waiting_for_children` PM
is reprieved, not killed.

The reprieve gate itself (`_tier2_reprieve_signal`) reprieves when a compaction
completed within `COMPACT_REPRIEVE_WINDOW_SEC`, or when the recorded handle pid
still has live child processes ("children") or a non-zombie status ("alive",
psutil). Sessions that have never produced any output are subject to the
reprieve-escalation cap (`MAX_NO_OUTPUT_REPRIEVES`) so an alive-but-silent
session is eventually recovered rather than reprieved forever.

The running-scan `no_progress` elif is **narrowed** on the in-scope handle (not
deleted), retaining the #944 shared-`worker_key` orphan net for worker-alive
orphans Fix #3 cannot reach. The residual killers own disjoint cases:

| Killer | Owns | Cadence |
|--------|------|---------|
| Fix #3 in-scope watcher | worker-**alive** running session, no progress past deadline, reprieve failed | worker-loop poll (`PROGRESS_POLL_S`) |
| `worker_dead` scan | running session whose worker loop is dead | 300s health tick |
| `tool_timeout` loop | a tool in flight past its per-tier budget | 30s |

`SESSION_PROGRESS_DEADLINE_S` is set ≥ the maximum tool-timeout tier, so
`tool_timeout` always fires first for a tool-in-flight wedge and Fix #3 catches
only the residual (no tool in flight / model-inference stall / wedged between
tool calls). All three converge on idempotent `reclaim` + terminal-guarded
`_apply_recovery_transition`, so any cross-boundary race is harmless.

## Environment constants

All defaults are provisional — tune after observing real rates in production.

| Constant | Default | Purpose |
|----------|---------|---------|
| `SESSION_PROGRESS_DEADLINE_S` | `1800` (30 min) | No-progress deadline; must stay ≥ the max tool-timeout tier. |
| `PROGRESS_POLL_S` | `30` | Watcher poll interval. |
| `SLOT_LEASE_REAP_DISABLED` | unset | `1` disables the reap **reclaim** action (detection still logs). |
| `DISABLE_PROGRESS_KILL` | unset | `1` disables the Fix #3 cancel (reused from the tool-timeout loop). |

## Observability

`localhost:8500/dashboard.json` surfaces `slot_reclaims` in the `worker` health
block (`worker_slot_reclaims`), summed from the per-project
`{project_key}:session-health:slot_reclaims` Redis counters. A rising count
signals a recurring leak worth root-causing — under a healthy system it should
stay flat, since a reclaim means a permit was leaked and self-healed.

## Design precedents

The owned-lease shape follows the Kubernetes `Lease` object + node-eviction
pattern (an explicit owner reclaims on terminal status) and Go's
`context.WithDeadline` (a progress-fed deadline, not a wall-clock TTL). These are
conceptual; the implementation is a stdlib `asyncio` in-memory registry with no
Popoto model field and no migration — leases are rebuilt fresh on worker restart.

## Status Quo

The on-loop reap pass documented above (`_reap_slot_leases()`) now has a
bridge-domain complement. Issue #1821 extended it with a lease-snapshot
publish and a reclaim-request drain, so a bridge-process watchdog can trigger
a restart-free reclaim from outside the worker loop — the sole reclaim lever
that still fires under `SLOT_LEASE_REAP_DISABLED=1`, since the drain sits in
the always-run region ahead of that flag's early return. `registry.reclaim()`
itself still runs on the worker loop; only the trigger crosses the process
boundary. See [Out-of-Domain Recovery + Per-Tool Budget Backstop](out-of-domain-recovery.md).

## See Also

- [Worker Liveness Recovery](worker-liveness-recovery.md) — the dead-man's-switch
  beacon this feature's lease registry sits alongside (#1815)
- [Out-of-Domain Recovery + Per-Tool Budget Backstop](out-of-domain-recovery.md) —
  the bridge-domain reclaim trigger built on top of this registry's reap pass,
  plus the synchronous per-tool budget (#1821)
