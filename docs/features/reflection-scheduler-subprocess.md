# Reflection Scheduler Subprocess

The reflection scheduler runs in its own supervised launchd subprocess
(`python -m reflections`, `com.valor.reflection-worker`), separate from the worker.
This is the continuation of [Worker Fault Containment](worker-fault-containment.md)
(#1816): "Fix #5", the highest-leverage decoupling, deferred until Stages A–C shipped
and were observed in production.

## Why

The scheduler's 31 reflection jobs (repo audits, memory dedup, crash recovery, PM
briefings, sentry triage, docs auditing …) used to run as a long-lived asyncio task on
the worker's event loop. That coupled every reflection to the loop that also owns
session execution: a reflection memory leak, CPU spin, or synchronous freeze could
degrade the worker that runs customer-facing sessions.

#1816 Fixes #1–#4 removed the acute critical-path starvation risk (bounded reflection
thread pool + `supervise()` respawn). This change closes the residual structural gap:
true crash-domain and freeze isolation. The scheduler no longer shares the worker's
event loop, memory space, or crash domain. The worker only executes the `AgentSession`
records the scheduler enqueues.

## How it works

```
launchd (com.valor.reflection-worker, KeepAlive=true, ThrottleInterval)
  → python -m reflections   (reflections/__main__.py, VALOR_LAUNCHD=1)
      → ON BOOT: atomically write data/reflection_worker_starts {count, last_start_ts}
      → ReflectionScheduler().start()   (the same class, reused verbatim)
          → each tick: write data/last_reflection_tick (heartbeat mtime)
          → reads config/reflections.yaml (local copy; VALOR_LAUNCHD skips the vault path)
          → Reflection / ReflectionRun records in Redis   (unchanged contract)
          → agent reflections enqueue AgentSession → executed by the WORKER process
```

The scheduler↔worker seam is the existing Redis `Reflection` / `AgentSession` records.
Moving the scheduler to a sibling process changes *who ticks*, not *how work is enqueued
or executed*. `ReflectionScheduler` is unchanged; the process-specific heartbeat I/O is a
thin wrap in `reflections/__main__.py`, never threaded into the shared class.

launchd `KeepAlive` is the supervisor (it replaces the in-worker `supervise()` respawn).
There is deliberately no bespoke watchdog process.

## Operator visibility

Because the scheduler is out-of-process, a crash-looping or silently-dead scheduler must
not become invisible. Two `data/` file signals surface on `/dashboard.json`, `/health`,
and a health badge:

- **`data/last_reflection_tick`** (mtime) — written every tick. Drives
  `reflection_scheduler_status` (`ok` when fresh, `error` when stale/absent) and
  `reflection_scheduler_tick_age_s`. Status is derived **purely** from tick freshness,
  mirroring `_get_worker_health`. A grace window (a stale threshold of ~2× the 60s tick)
  keeps a just-deployed scheduler's first-tick lag from false-positiving as dead.

- **`data/reflection_worker_starts`** (`{count, last_start_ts}`) — written once per boot,
  atomically (temp-file + `os.replace`, PID-suffixed temp name) so a SIGKILL mid-write
  during a crash storm never truncates the file. Surfaces
  `reflection_scheduler_restart_count` (informational-only) and
  `reflection_scheduler_last_start_age_s`.

**Crash-loop indicator:** `reflection_scheduler_last_start_age_s` staying persistently
near-zero. A crash loop keeps the tick file fresh (each short-lived process writes one
before dying) but keeps resetting `last_start_ts` to ~now as launchd respawns it. A
healthy long-lived scheduler's start-age climbs unboundedly (booted once, then just
ticks). This is computable from a single snapshot and immune to benign deploy restarts (a
deploy bumps `count` once, then the start-age climbs again). `restart_count` is
informational-only — every `/update` bootout→bootstrap inflates it, so it cannot
distinguish a deploy from a crash and is never an alarm source. There is no windowed
restart-rate classifier: it is not computable from the cumulative snapshot, and
`launchctl print` already holds per-restart history for the rare forensic case.

## Install gate: worker-role, not bridge-role

The subprocess installs exactly where the worker installs. The worker install is ungated
by role (`scripts/install_worker.sh` has no machine gate; `run.py` guards it only on plist
existence), so `scripts/install_reflection_worker.sh` gates on **`has_worker_role()`** —
`has_bridge_role()` minus the Telegram-block check. It qualifies as soon as any project's
`machine` matches this host, regardless of Telegram config, with the same fail-open
contract and self-skip + stale-plist removal.

Gating on bridge-role would strand reflections on worker-only (non-Telegram) machines —
the [#1379](https://github.com/tomcounsell/ai/issues/1379) over-narrow-gating failure
class (which gated calendar work on `session.slug` and dropped all non-slug work).
`reflection_machine_filter` still scopes project-specific audits so a machine runs only
the reflections it owns.

## Cutover ordering

`/update` restarts the worker first (new code, no in-process scheduler), then bootstraps
the reflection plist. `restart_worker` uses `launchctl kickstart -k` — a hard
kill-and-relaunch — so the old in-process scheduler dies atomically before the new plist
loads. The zero-scheduler window is a handful of seconds (worker-restart to
plist-bootstrap); reflections are periodic (tick ≥ 60s, job intervals hours/days), so no
reflection misses its due window. Because the old scheduler is hard-killed rather than
drained in parallel, two schedulers never tick at once by construction; `is_reflection_running`
+ `reap_stale_running()` are idempotency defense-in-depth.

## Config ownership

The `config/reflections.yaml` copy + `reflection_machine_filter` step moved from
`install_worker.sh` into `install_reflection_worker.sh` (single owner). The `projects.json`
copy stays in `install_worker.sh` (the worker needs it; the reflection installer's
machine-filter reads that copy).

## Commands

- `python -m reflections --dry-run` — load the registry, print status, exit 0.
- `./scripts/install_reflection_worker.sh` — install/reload the subprocess (self-gating).
- `tail -f logs/reflection_worker.log` — stream subprocess logs.
- `curl -s localhost:8500/dashboard.json | python3 -c "import json,sys; h=json.load(sys.stdin)['health']; print(h['reflection_scheduler_status'], h['reflection_scheduler_last_start_age_s'])"`

## Manual verification (isolation payoff)

The isolation is a structural property, verified by inspection rather than an automated
assertion: after `/update`, `pgrep -f "python -m reflections"` shows the scheduler in a
**separate PID** from `pgrep -f "python -m worker"`. The two processes share only Redis
records. The behavioral tests cover the visibility surface (the `reflection_scheduler_*`
dashboard fields); the crash-domain separation is confirmed by the distinct PIDs.
