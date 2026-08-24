---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2148
last_comment_id:
---

# Ownership-Based Startup-Recovery Guard (No More Stranded Recent Sessions)

## Problem

`_recover_interrupted_agent_sessions_startup` (`agent/session_health.py:660`)
skips any `running` session started <300s ago
(`AGENT_SESSION_HEALTH_MIN_RUNNING`). After a worker restart, a session
started <300s before the crash is skipped-but-owned-by-no-one: status stays
`running`, its harness keeps executing detached, and the new worker's queue
loop pops the NEXT pending session for the same worker_key — two concurrent
sessions on one key, violating per-project serialization (observed live
2026-07-17 14:11-14:13, sessions `0_1784286827622` + `0_1784286833111`).

The guard's real purpose is "don't recover sessions owned by a live
(concurrent) worker" — age is a proxy that fails exactly when restarts are
frequent. Sessions do not currently record their owning worker PID, so
ownership cannot be tested directly.

## Freshness Check

**Baseline commit:** b97569fc. Re-verified 2026-07-18:
- Age guard at `agent/session_health.py:700-720`; identical guard in
  `_sweep_dead_worker_sessions:936-945` — both hold.
- Pickup transition sites (`agent/session_pickup.py:473, 632`) set
  `started_at` then `transition_status(...)`, which does a FULL `save()`
  preserving companion fields — a `worker_pid` stamp set alongside
  `started_at` persists with no extra save.
- No `worker_pid`-like field exists on `AgentSession` (grep confirms).
- #2141 (just merged) terminates harness children on graceful shutdown —
  but a SIGKILL'd worker still leaves a live detached harness, so recovery
  must handle the live-harness case itself.

## Prior Art

- #1767 dead-worker sweep already keys on PID liveness (`claude_pid`) — the
  recovery guard predates that signal; this plan aligns recovery with it.
- #2141: drain + shutdown child-kill make restarts rarer and cleaner; this
  plan fixes the recovery half that runs when a kill happens anyway.

## Solution

### Key Elements

1. **`AgentSession.worker_pid`** (`IntField(null=True)`, mirrors `pm_pid`):
   the OS PID of the worker process that owns this session's execution.
   Stamped `os.getpid()` at BOTH pending→running pickup sites
   (`session_pickup.py`) alongside `started_at` (persisted by
   `transition_status`'s full save).

2. **Ownership-based recovery guard** (`_recover_interrupted_agent_sessions_startup`):
   replace the age filter with:
   - `worker_pid` present AND alive (`os.kill(pid, 0)`, int-coerced,
     any exception → not alive) → SKIP (owned by a live concurrent worker —
     the guard's original purpose, now exact).
   - `worker_pid` present AND dead → interrupted → recover **regardless of
     age** (a session started 10s before the crash is still interrupted).
   - `worker_pid` absent/garbage (legacy rows written before this change) →
     fall back to the existing 300s age guard (conservative transitional
     behavior; disappears as rows cycle).
   - When recovering a session whose `claude_pid` is STILL ALIVE (detached
     harness from a SIGKILL'd worker), terminate that harness first (SIGTERM,
     loud log) so the re-picked session cannot double-execute against it.

3. **Serialization follows for free:** recovery runs at boot BEFORE queue
   loops start popping (worker startup ordering), so the previously-stranded
   session is back in `pending` when the per-key loop starts — one session
   per key, across restarts. No `_pop_agent_session` change needed.

4. `_sweep_dead_worker_sessions`'s age guard is left unchanged: it only
   sweeps sessions with a provably dead `claude_pid`, and its recent-skip
   protects the pickup→spawn window where `claude_pid` is stale from a
   prior turn; recovery (running after it) now handles those correctly.

## No-Gos

- No pop-side blocking in `agent_session_queue.py` (recovery-side fix makes
  it redundant; two mechanisms racing each other is worse than one).
- No removal of the age fallback for legacy rows this release.
- No change to the sweep's semantics.

## Update System

No update system changes required — worker-internal code deployed by the
normal git pull + (now drained, #2141) worker restart. The new field is
nullable with no backfill (same convention as every liveness field).

## Agent Integration

No agent integration required — worker-internal recovery logic. No CLI
entry point, no bridge import changes.

## Failure Path Test Strategy

- `worker_pid` garbage (string, Mock, negative) → int coercion fails →
  treated as absent → age fallback.
- `os.kill` raising PermissionError (PID recycled to another user's
  process) → treated as alive (EPERM means a process EXISTS) — skip, do not
  strand: EPERM implies a live process, and a live foreign process is not
  this worker either… treated as ALIVE to be conservative (skip recovery,
  health loop resolves later).
- Harness-terminate failure during recovery → logged, recovery proceeds
  (the wedge/health loops own the stragglers).

## Test Impact

- [ ] `tests/unit/test_recovery_respawn_safety.py` — UPDATE:
  `_mock_agent_session` gains `worker_pid: None` default (MagicMock
  auto-vivification would otherwise poison the ownership check, same
  precedent as the `is_ledger: False` default); existing stale-session
  tests keep passing via the legacy age-fallback path.
- [ ] Same file — ADD: recent session + dead `worker_pid` → recovered;
  recent session + live `worker_pid` (own PID) → skipped; recent session +
  no `worker_pid` → skipped (legacy fallback); recovery with live
  `claude_pid` terminates the detached harness.
- [ ] `tests/unit/test_session_health_orphan_reap.py` and sweep tests —
  no changes (sweep untouched).

## Rabbit Holes

- Don't invent a boot-id/epoch scheme — PID liveness is sufficient and
  matches the sweep's existing test.
- Don't try to adopt (reparent) the detached harness — terminating it and
  resuming the transcript is the supported continuation path.
- Don't backfill `worker_pid` on existing rows.

## Documentation

- [ ] `docs/features/session-lifecycle.md` — document the ownership-based
  recovery rule (worker_pid stamp, liveness test, legacy age fallback,
  detached-harness termination).
- [ ] `docs/features/bridge-worker-architecture.md` — one-line update in
  the "Worker Restart Recovery" section pointing at the new rule.

## Success Criteria

- [ ] After a worker restart, no session remains `running` without a live
  owning worker (acceptance 1).
- [ ] A project-keyed queue loop never runs two sessions concurrently for
  the same key across restarts (acceptance 2 — via boot-ordering argument +
  recovered-not-stranded sessions).
- [ ] Test: session started <300s before a worker death is recovered, not
  stranded (acceptance 3).
- [ ] Ownership rule documented (acceptance 4).

## Verification

1. `pytest tests/unit/test_recovery_respawn_safety.py -n0` — all pass
   including new ownership cases.
2. Grep: no remaining age-only skip in `_recover_interrupted_agent_sessions_startup`.
3. CI green on the PR.
