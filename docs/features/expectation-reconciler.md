# Expectation Reconciler

`reflections/expectation_reconciler.py` — recovers orphaned lanes from the
Job's open **outbound** expectations (#2708). The founding incident (#2494):
a backgrounded dev subagent SIGKILLed mid-turn exited 0 as `completed`, its
worktree was deleted, and nothing could have detected the loss. The
expectation recorded on the immortal Job at spawn time is what outlives the
lane; this reflection (registered in `reflections.yaml`, 30-minute cadence)
is what looks.

## What it does, per open outbound expectation

1. **Age gate** — entries younger than `EXPECTATION_MIN_AGE_HOURS`
   (default 1h) are left alone so a just-spawned lane is never raced.
2. **Owner liveness** — the recorded `owner` (lane session id or slug) is
   resolved against `AgentSession` rows. A row claiming a non-terminal
   status is **respawn-blocking only**: a session row is a claim, not proof
   of life (#2705), and the stale-`running` tie-breaker belongs to the
   #2716 liveness work. *Gone* means no row, or only terminal rows.
3. **Shipped-work guard** (the sole cross-actor collision guard) — a fresh
   git/GitHub read for `session/<slug>`: an open/merged PR
   (`gh pr list --head`) or a pushed branch (`git ls-remote`) means the
   work is visible outside the session model. **Shipped work is never
   respawned** — the evidence is steered to a PM for deliberate discharge
   (`tools/job_tool expectation-remove`). Mechanical discharge on PR merge
   was considered and rejected (same evidence that killed auto-discharge
   for promises).
4. **The ladder**, keyed `(job_id, expectation_id)` in raw-Redis
   bookkeeping keys (`expectation:reconcile:*`):
   - escalation key set → stop acting entirely;
   - attempts ≥ `EXPECTATION_RECONCILE_MAX_ATTEMPTS` (default 3) →
     escalate once, stop;
   - action cooldown live (default 1h) → skip this tick;
   - **steer** a live PM (the recorded holder first, else the most recent
     live non-ledger eng session) with the evidence and the exact
     discharge/respawn commands;
   - no live PM and work unshipped → **respawn** the lane via
     `create_session` with the recorded `what` (which re-records the
     expectation through the spawn chokepoint, `--job-id` bound);
   - action failed → escalate once (Telegram operator alert), stop.

Immediately before acting it re-fetches the Job by KeyFields and re-checks
the expectation is still open — a PM discharge racing the tick always wins.

## Invariants

- **Attempts TTL is floored at the escalation TTL** —
  `max(configured, escalation_ttl)`, exactly as
  `reflections/sdlc_progress.py::_attempts_ttl_seconds()`. An attempts key
  that expires while the escalation key still suppresses paging turns
  "escalate once and stop" into "act forever, silently".
- **No writes outside its own bookkeeping keys**: it never discharges an
  expectation, takes no locks, and adds no lock semantics — expectations
  are readable ownership records; a second PM reads and decides (#2704).
- **Never raises**: every boundary (ORM scan, per-expectation pass, git/gh
  subprocess, steer/create) logs a warning and continues.
- **A corrupt goal is a finding, never an empty loop** (#2862):
  `Job.with_open_expectations()` retains a flagged Job whose `goal` no
  longer decodes, and the tick records `corrupt-goal: <job_id>` in its
  findings and moves on. It cannot act on obligations it cannot read; the
  row waits for a human. See rule 8 in
  [`durability-model.md`](durability-model.md).
- **Single-machine ownership**: acts only for projects this machine owns
  (`machine_owns_project`).
- Composes with `reflections/sdlc_progress.py` (PR-based stall recovery)
  rather than rebuilding it: `sdlc_progress` only sees work that reached a
  pushed branch/PR; the reconciler covers the nothing-committed case. Both
  are steer-first, and the shipped-work guard is what prevents a double
  respawn (a double steer is benign).

## Configuration (env, all provisional)

| Var | Default |
|-----|---------|
| `EXPECTATION_RECONCILER_ENABLED` | `true` |
| `EXPECTATION_MIN_AGE_HOURS` | `1` |
| `EXPECTATION_RECONCILE_MAX_ATTEMPTS` | `3` |
| `EXPECTATION_RECONCILE_COOLDOWN_HOURS` | `1` |
| `EXPECTATION_ATTEMPTS_TTL_DAYS` | `30` (floored at escalation TTL) |
| `EXPECTATION_ESCALATION_TTL_DAYS` | `30` |

## See also

- [`durability-model.md`](durability-model.md) — the expectation primitive
  itself (schema, chokepoint-derived status, rest derivation, discharge).
- Tests: `tests/unit/reflections/test_expectation_reconciler.py`.
