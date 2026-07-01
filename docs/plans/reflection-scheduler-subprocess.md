---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1828
last_comment_id:
revision_applied: true
---

# Reflection Scheduler Subprocess Split (off-load the worker hot loop)

## Problem

The reflection scheduler runs as one of the worker's long-lived asyncio tasks.
`worker/__main__.py:1020-1034` constructs `ReflectionScheduler()` and starts its
`start()` loop under the `supervise("reflection-scheduler", …)` helper on the
worker's single event loop; `worker/__main__.py:1136-1142` cancels it on shutdown.
The scheduler's 31 jobs (repo audits, memory dedup, crash recovery, PM briefings,
sentry triage, session intelligence, docs auditing …) are therefore coupled to the
worker's hot event loop — the loop that also owns session execution and the liveness
monitors.

The parent slug #1816 (Worker Fault Containment, **shipped** — PR #1832) capped the
reflection thread pool (Fix #3, bounded `ThreadPoolExecutor`) and wrapped the
scheduler in `supervise()` (Fix #4, respawn-with-backoff). Those two fixes mean a
wedged reflection can no longer *starve* the critical path and a crashed scheduler
task is respawned. But a **structural** freeze-isolation gap remains: the scheduler
still lives *in the worker process*, sharing the event loop, the memory space, and
the crash domain with session execution. A reflection memory-leak, a CPU spin, or a
synchronous freeze between `await` points still degrades the worker that runs
customer-facing sessions.

**This is "Fix #5" from #1816** — the highest-leverage decoupling, explicitly
deferred because it is NOT a prerequisite for Stages A–C (per the #1816 CRITIQUE
coordinator: ship Fix #5 once Stage A/B are in production and observed).

**Current behavior:** 31 reflection jobs run on the worker's event loop; any
reflection defect shares a process and a crash domain with session execution.

**Desired outcome:** The reflection scheduler runs in its **own supervised launchd
subprocess** (`reflections/__main__.py` + `com.valor.reflection-worker.plist` +
`scripts/install_reflection_worker.sh`), communicating with the worker only via the
existing Redis `Reflection` / `ReflectionRun` records. The worker **stops
constructing the scheduler in the same change** (no parallel-run window — this repo
has a HARD no-parallel-migrations rule). The worker loop then hosts only session
execution + liveness monitors. Because the scheduler is now out-of-process, its
health must remain **operator-visible** (a crash-looping or silently-dead scheduler
must not become invisible) — surfaced by BOTH a per-tick freshness heartbeat AND a
process-start signal. Dashboard `status` is derived **purely from tick freshness**
(mirroring `_get_worker_health`); the crash-loop indicator is derived from
`last_start_age_s` staying persistently near-zero (a healthy long-lived scheduler's
start-age climbs unboundedly; a crash-looping one keeps resetting to ~0). The raw
`restart_count` is surfaced as **informational-only** context and is explicitly NOT an
alarm source — every `/update` bootout→bootstrap increments it, so a lifetime
cumulative counter climbs forever on normal machines and cannot distinguish deploys
from crashes (see Data Flow).

The subprocess runs **exactly where the worker runs**. `scripts/install_worker.sh`
and `service.install_worker` install the worker on **every machine that runs
`/update`** — there is no bridge-role gate on the worker install (verified: the
`run.py:1341` worker-install block is guarded only by `plist.exists()`, not by
`if has_bridge:`). The reflection subprocess is therefore gated on **worker presence**
(the machine owns at least one project in `projects.json`), NOT on bridge-role, so
reflections run precisely where the worker runs. Bridge-role gating would silently
stop reflections on a dev/worker machine with no Telegram-configured project — the
same over-narrow-gating failure class as **issue #1379** (which gated calendar on
`session.slug` and thereby DROPPED all non-slug work). The resolved gate is a
`has_worker_role()` check (see Solution / Update System).

## Freshness Check

**Baseline commit:** `b99e295821573d011c2981c401c8977ee87fe045` (`b99e2958`,
"feat(calendar): feature-keyed, day-bounded, client-facing work logging").
**Issue filed:** #1828, split from #1816 (parent CLOSED / shipped).
**Disposition:** Fresh — every file:line re-verified against HEAD below.

**File:line references re-verified against `b99e2958`:**
- `worker/__main__.py:1020-1034` — the reflection scheduler construction: imports
  `ReflectionScheduler` (`:1024`), constructs `_reflection_scheduler =
  ReflectionScheduler()` (`:1026`), defines `_make_reflection_task()` returning
  `_reflection_scheduler.start()` (`:1028-1029`), and starts it via
  `reflection_task = supervise("reflection-scheduler", _make_reflection_task)`
  (`:1031`). This is the `create_task` site to DELETE.
- `worker/__main__.py:1136-1142` — the shutdown cancel: `if reflection_task is not
  None: reflection_task.cancel(); await reflection_task` — DELETE with the construction.
- `agent/reflection_scheduler.py:637` — `class ReflectionScheduler`; `start()` at
  `:799` (loads registry, `reap_stale_running()` on startup, then `while True:
  await self.tick(); await asyncio.sleep(SCHEDULER_TICK_INTERVAL)`); `get_status()`
  at `:830` (observability). **This class is REUSED verbatim** — the subprocess
  constructs and `start()`s it exactly as the worker does today. No change to the class.
- `agent/reflection_scheduler.py:68-95` — registry path resolution: `REFLECTIONS_YAML`
  env → `~/Desktop/Valor/reflections.yaml` (vault) → `config/reflections.yaml`
  (in-repo fallback). Under `VALOR_LAUNCHD=1` the vault (iCloud/TCC) path is skipped
  (`:90`) and `config/reflections.yaml` is read — the local copy `install_worker.sh`
  writes today. The new subprocess MUST set `VALOR_LAUNCHD=1` and rely on the same
  local `config/reflections.yaml`.
- **Installer/plist model pair:** `scripts/install_sdlc_reflection.sh` +
  `com.valor.sdlc-reflection.plist` — the structural template (sed path-substitution,
  bootout/bootstrap). NOTE: that pair uses `StartInterval` (run-to-completion cron),
  which is the WRONG lifecycle here — the reflection scheduler is a **long-lived
  loop**. The correct lifecycle template is `com.valor.worker.plist` (`RunAtLoad` +
  `KeepAlive=true` + `ThrottleInterval=10`, verified at `:29-38`), per the issue's
  explicit "`KeepAlive=true` + `ThrottleInterval`" instruction.
- **Role-gating precedent:** `scripts/install_nightly_tests.sh:20-72` —
  `has_bridge_role()` reads `projects.json`, matches `scutil --get ComputerName`
  against each project's `machine`, qualifies if any owned project has a `telegram`
  block, and on a non-bridge machine **skips install AND removes any stale plist**
  (bootout + `rm -f`). This is the structural self-skip pattern the new installer
  copies. **But the reflection installer gates on worker presence, not bridge role**
  (see next bullet): the new `has_worker_role()` is `has_bridge_role()` with the
  `if proj.get("telegram")` clause dropped — it qualifies if ANY project's `machine`
  matches this host, regardless of Telegram config. No such helper exists yet in the
  repo (`grep -rn "has_worker_role" scripts/` returns nothing), so the plan adds it.
- **Worker install is ubiquitous (no role gate):** `scripts/update/run.py:1341-1343`
  installs the worker under `if (project_dir / "com.valor.worker.plist").exists():` —
  **NOT** under `if has_bridge:`. `service.install_worker` (`scripts/update/service.py:280`)
  bootstraps the plist on every machine that runs `/update`. `scripts/install_worker.sh`
  has no `scutil`/machine gate at all. Confirmed: the worker runs on every /update
  machine; gating reflections on bridge-role would strand reflections on worker-only
  machines. This grounds the OQ1 resolution (worker-presence gate).
- **Worker restart is a HARD kill, not a graceful drain:** `restart_worker()` in
  `scripts/valor-service.sh:834-853` runs `launchctl kickstart -k
  "gui/<uid>/$WORKER_PLIST_NAME"` — the `-k` flag SIGKILLs the running worker and
  immediately relaunches (no drain, no in-process await; `stop_worker`'s graceful
  bootout+wait path is a SEPARATE codepath used only for stop/disable). `install_worker`
  (`service.py:326-354`) is content-idempotent but on a code change does
  `bootout` → rewrite plist → `bootstrap`, which also terminates then relaunches.
  Consequence for cutover: the OLD in-process scheduler dies **instantly** (SIGKILL /
  bootout) at the worker restart, and the NEW worker (no scheduler) comes up ~5s later
  (`restart_worker` sleeps 5s before probing). The zero-scheduler window therefore
  spans from worker-restart to reflection-plist-bootstrap — a handful of seconds, and
  it is a clean hard cut (no lingering old scheduler), so the "never double" claim
  holds by construction. See Risk 1 / Cutover.
- **Update wiring:** `scripts/update/run.py:1341-1343` installs the worker
  (`service.install_worker`); `:1447-1450` installs nightly-tests under `if
  has_bridge:` via `service.install_nightly_tests` (which delegates to the
  self-gating shell script — `scripts/update/service.py:360-388`). The new
  `service.install_reflection_worker()` is wired in the same style.
- **Config copy + machine filter:** `scripts/install_worker.sh:47-62` copies
  `reflections.yaml` → `config/` and runs `tools.reflection_machine_filter` to
  disable project-scoped reflections this machine doesn't own. After the split, the
  **reflection subprocess** owns reflections, so this copy+filter belongs in the new
  installer (moved, not duplicated — see Update System).
- **Operator surface:** `_get_worker_health()` (`ui/app.py:340`, the `def` line; body
  reads `Path(__file__).parent.parent / "data" / "last_worker_connected"` mtime and
  buckets age against `HEARTBEAT_STALENESS_THRESHOLD_S` / `WORKER_DOWN_THRESHOLD_S`)
  is the exact mirror for the new `_get_reflection_scheduler_health()`. The
  `/dashboard.json` route builds a `health` dict (`ui/app.py:517-541`) and surfaces
  reflection state via `ui/data/reflections.get_all_reflections()`
  (per-`Reflection`-record `last_status`/`ran_at`). There is **no scheduler-level
  heartbeat today** — if the whole scheduler dies, individual records just go stale
  with no single "scheduler alive" signal. This gap is what moving out-of-process
  makes worse, and what this plan closes.
- **Freshness heartbeat alone cannot catch a crash-restart loop:** under launchd
  `KeepAlive`+`ThrottleInterval`, a scheduler that boots, writes the tick heartbeat,
  then crashes — repeatedly — keeps `data/last_reflection_tick` looking FRESH, masking
  exactly the crash-loop failure this feature exists to make visible. The plan
  therefore adds a SECOND signal: a **process-start timestamp** written once at
  subprocess boot (`data/reflection_worker_starts`, `{count, last_start_ts}`), surfaced
  on `/dashboard.json` as `last_start_age_s`. A healthy long-lived scheduler's
  `last_start_age_s` climbs unboundedly (one boot, then it just ticks); a crash-looping
  one keeps resetting to ~0 as launchd respawns it. The operator crash-loop signal is
  therefore **`last_start_age_s` persistently near-zero** — computable from a single
  snapshot, immune to benign deploy restarts (a deploy bumps `count` once and then the
  start-age climbs again). The `count` field is surfaced as **informational-only**
  context, NOT an alarm source: it is a lifetime cumulative counter that every
  `/update` bootout→bootstrap increments, so on a normal machine it climbs forever and
  cannot distinguish a deploy from a crash. There is deliberately **no windowed-rate
  classifier** — a rolling-window rate is NOT computable from a single cumulative
  `{count, last_start_ts}` snapshot (no time-series is persisted), and launchd already
  tracks per-restart history via `launchctl print` if a forensic rate is ever needed.
- Data dir today: `data/last_worker_connected`, `data/last_connected` — the
  file-freshness heartbeat convention the new scheduler heartbeat mirrors.

**Tests re-checked:** `tests/unit/test_worker_supervisor.py` has **0** references to
`reflection-scheduler`/`reflection_task` (it tests `supervise()` generically, not the
reflection wiring) — deleting the worker's reflection block does not break it.
`tests/unit/test_reflection_scheduler.py` tests the `ReflectionScheduler` class
directly (unchanged by this move). No test asserts the worker *owns* the scheduler.

**Notes:** No drift. The class is reused unchanged; the whole change is *relocation +
supervision + worker-role-gated install + a two-signal (tick + restart-count) heartbeat
surface + deletion of the in-worker wiring*.

## Prior Art

- **#1816 / PR #1832 (merged)** — Worker Fault Containment. Shipped Fixes #1–#4;
  this is the deferred Fix #5. Provides `supervise()` (the respawn helper the worker
  used for the scheduler, now irrelevant to the worker but conceptually mirrored by
  launchd `KeepAlive`) and the bounded reflection thread pool the subprocess inherits.
- **#1818 (open tracking)** — resilience umbrella; lists "move the 31-job reflection
  scheduler into its own supervised process" under Structural work.
- **#1273 (merged)** — the unified `Reflection` Popoto model + `ReflectionScheduler`.
  Establishes the Redis-record contract the subprocess communicates through (no
  schema change needed).
- **`scripts/install_nightly_tests.sh` / `install_email_bridge.sh`** — the
  role-gated-install + self-skip + stale-plist-removal precedents this installer adapts
  (structurally identical; the gate is broadened from bridge-role to worker-role).
- **#1379 (calendar work logging)** — the over-narrow-gating precedent this plan
  explicitly avoids: gating on `session.slug` DROPPED all non-slug agent work. Gating
  reflections on bridge-role would repeat that class of error on worker-only machines;
  hence the worker-role gate.
- **`com.valor.worker.plist`** — the `KeepAlive`/`ThrottleInterval` long-lived
  launchd lifecycle template.

## Data Flow

**Today (in-process):**
```
worker/__main__.py:_run_worker
  → supervise("reflection-scheduler", ReflectionScheduler().start)
      → tick() every SCHEDULER_TICK_INTERVAL  → reads config/reflections.yaml
      → Reflection.get_or_create / is_reflection_running / run_reflection
      → function reflections run in bounded ThreadPoolExecutor (on the worker's loop)
      → agent reflections enqueue AgentSession records to Redis
```

**After (out-of-process):**
```
launchd (com.valor.reflection-worker, KeepAlive=true)
  → python -m reflections   (reflections/__main__.py, VALOR_LAUNCHD=1)
      → ON BOOT (once per process start):
          → atomically rewrite data/reflection_worker_starts  ({count, last_start_ts};
            temp-file + os.replace)  ← crash-loop signal (survives fresh ticks)
      → ReflectionScheduler().start()   [same class, same tick loop]
          → each tick: write data/last_reflection_tick  (heartbeat file, mtime)
          → reads config/reflections.yaml (local copy; VALOR_LAUNCHD skips vault)
          → Reflection / ReflectionRun records in Redis  ← unchanged contract
          → agent reflections enqueue AgentSession → picked up by the WORKER process

worker/__main__.py:_run_worker
  → (reflection block DELETED — no scheduler here)
  → still executes the AgentSession records the scheduler enqueues (unchanged)

ui/app.py /dashboard.json
  → _get_reflection_scheduler_health()  reads BOTH heartbeat files:
      • data/last_reflection_tick freshness → status + tick_age_s   (is it ticking?)
        (status derived PURELY from tick freshness, mirroring _get_worker_health)
      • data/reflection_worker_starts       → restart_count + last_start_age_s
        (crash-loop indicator = last_start_age_s persistently near-zero; restart_count
         is informational-only, NOT an alarm source — deploys inflate it)
  → flattens these fields into the health dict alongside worker/bridge/email
    (health.reflection_scheduler_status, .reflection_scheduler_tick_age_s,
     .reflection_scheduler_restart_count, .reflection_scheduler_last_start_age_s)
    ← NEW operator surface, matching the _get_worker_health flatten convention
```

**Crash-loop detection semantics (single-snapshot, no windowed classifier):** the tick
heartbeat answers "is a scheduler currently ticking?"; the start-timestamp answers "is
the scheduler process stable, or is launchd respawning it?". A healthy scheduler shows
a fresh tick AND a `last_start_age_s` that climbs unboundedly (booted once, now just
ticking). A crash loop shows a fresh tick (each short-lived process writes one before
dying) BUT a `last_start_age_s` that keeps resetting to near-zero as launchd respawns.
The crash-loop indicator is therefore **`last_start_age_s` persistently near-zero** —
derivable from a single `{count, last_start_ts}` snapshot, with no time-series and no
rolling-window rate. It is immune to benign deploy restarts (a deploy bumps `count`
once, then `last_start_age_s` climbs again). `restart_count` is surfaced as
informational-only context (it is a lifetime cumulative counter that every deploy
inflates), NOT an alarm trigger. A windowed restart-rate classifier is deliberately NOT
built: it is not computable from the persisted cumulative snapshot (would require a
second time-series subsystem), and launchd already records per-restart history via
`launchctl print` for the rare forensic case.

**Key invariants preserved:** the scheduler↔worker seam is *already* Redis
`Reflection`/`AgentSession` records — moving the scheduler to a sibling process
changes *who ticks*, not *how work is enqueued or executed*. `is_reflection_running`
+ the `reap_stale_running()` startup pass make a transient two-scheduler overlap
idempotent (status-guarded dedup), so a brief cutover overlap cannot double-run a
reflection.

## Architectural Impact

- **New dependencies:** none (stdlib `asyncio`, existing `ReflectionScheduler`).
- **New process:** one launchd agent per bridge machine
  (`com.valor.reflection-worker`), lifecycle `KeepAlive=true` (launchd respawns on
  exit) + `ThrottleInterval` (restart-storm cap). This **replaces** the in-worker
  `supervise()` respawn for the scheduler — launchd is now the supervisor.
- **Interface changes:** `worker/__main__.py` loses ~20 lines (construction + cancel).
  New `reflections/__main__.py` (thin entry: build scheduler, install signal handlers,
  `asyncio.run(scheduler.start())`, per-tick heartbeat + on-boot atomic start-timestamp write).
  New `ui/app.py` `_get_reflection_scheduler_health()` + additive
  `reflection_scheduler_*` fields flattened into the `/dashboard.json` `health` dict
  (matching the `_get_worker_health` flatten convention: tick-freshness `status`,
  `tick_age_s`, informational `restart_count`, crash-loop `last_start_age_s`).
- **No Popoto schema change** — communication is via existing `Reflection` records.
- **Ownership move:** the `config/reflections.yaml` copy + `reflection_machine_filter`
  step moves from `install_worker.sh` into `install_reflection_worker.sh` (the new
  owner), so exactly one installer prepares the reflection registry (no parallel copy).
- **Reversibility:** medium. Reverting means restoring the ~20 worker lines and
  uninstalling the plist. The class is unchanged, so revert risk is low.

## Appetite

**Size:** Medium

**Why now / expected payoff (knowing choice).** #1816 Fixes #1–#4 already removed the
acute *critical-path starvation* risk (bounded reflection thread pool + `supervise()`
respawn), so this is deliberately NOT an emergency fix. The Medium appetite — which
carries non-trivial deploy-wiring cost (new launchd service, worker-role gate,
multi-machine `/update` wiring, cutover ordering) — is spent knowingly for two residual
gains: (1) **true crash-domain / freeze isolation** — the scheduler no longer shares the
worker's event loop, memory space, and crash domain with customer-facing session
execution, so a reflection memory-leak / CPU-spin / synchronous freeze can no longer
degrade the worker; and (2) **operator visibility** — a first-class dead-OR-crash-looping
signal (`data/last_reflection_tick` freshness + `last_start_age_s`) that does not exist
today. **Honest scope caveat:** the isolation payoff (1) is a structural property, not
directly unit-testable; the merge-time behavioral check validates the *visibility
surface* (2) — the `/dashboard.json` `reflection_scheduler_*` fields — not the isolation
itself, which is verified by inspection (the scheduler runs in a separate PID) rather
than by an automated assertion. If the deploy-wiring cost is judged not worth the
isolation gain, the correct move is to defer, not to half-build.

**Team:** Solo dev. This is a *relocation* of an unchanged class plus a new
launchd install (well-trodden pattern) and a small dashboard surface — lower
blast-radius than the parent slug's in-loop refactors, but touches deploy wiring
across machines (careful role-gating + cutover ordering required).

**Interactions:**
- PM check-ins: 0 blocking. The gate question (bridge-role vs worker-role) is RESOLVED
  in-plan toward worker-presence gating (see Solution); no open confirmation is
  required to proceed.
- Review rounds: 1–2 (cutover ordering / double-vs-zero-scheduler; worker-role
  gate self-skip; tick-freshness + `last_start_age_s` crash-loop surface).

**PR strategy:** single PR. The delete + add + install + heartbeat are one atomic
cutover (no-parallel-migration rule forbids splitting the delete from the add).

## Prerequisites

Builds on #1816 primitives, already merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ReflectionScheduler` present | `grep -c "class ReflectionScheduler" agent/reflection_scheduler.py` | The class the subprocess reuses |
| Worker still constructs it (pre-change) | `grep -c "ReflectionScheduler()" worker/__main__.py` | Confirms the create_task site to delete exists |
| Role-gate structural precedent present | `grep -c "has_bridge_role" scripts/install_nightly_tests.sh` | The self-skip pattern to adapt (dropping the Telegram clause → worker-role) |
| Worker install has NO bridge gate | `grep -c "if has_bridge" scripts/install_worker.sh` | Confirms the worker (and thus the gate target) runs on every /update machine — must be 0 |
| Worker plist lifecycle template | `grep -c "KeepAlive" com.valor.worker.plist` | The `KeepAlive`+`ThrottleInterval` template |

## Solution

### Key Elements

- **`reflections/__main__.py`** — new module entry (`python -m reflections`). Sets up
  logging, installs SIGTERM/SIGINT handlers for clean launchd shutdown, constructs
  `ReflectionScheduler()`, and runs `asyncio.run(scheduler.start())`. Reuses the class
  verbatim. Writes a `data/last_reflection_tick` heartbeat file each tick (see below).
  Accepts `--dry-run` (load registry, print status, exit 0) mirroring `python -m
  worker --dry-run` so the installer can validate before bootstrapping.
- **Heartbeat surface (two signals)** — (1) the scheduler writes
  `data/last_reflection_tick` (write `time.time()`) at the top of every `tick()`;
  (2) on process boot, `reflections/__main__.py` writes a **start timestamp + boot
  counter** to `data/reflection_worker_starts` (`{count, last_start_ts}`). The tick
  file answers "is it ticking?"; the starts file's `last_start_age_s` answers "is it
  crash-looping?" — because a scheduler that crashes right after each tick keeps the
  tick file fresh but keeps resetting `last_start_ts` to ~now (see Data Flow). The
  crash-loop indicator is `last_start_age_s` staying near-zero; `count` is
  informational-only (deploys inflate it). **Absent vs corrupt distinction:** an absent
  file is first boot → start at `count=1`. A **corrupt** file (bad JSON / partial write)
  is NOT silently zeroed — the prior count is preserved when parseable, and an
  unparseable file is flagged (logged WARNING, `count` continues from a best-effort
  read; never silently reset to 1, which would zero the very signal a crash storm
  needs). **Atomic write:** the file is written via temp-file + `os.replace()` (atomic
  rename), NOT a bare `write_text` — a SIGKILL mid-write during a crash storm must not
  truncate the file and destroy the signal during the exact failure it targets. Both are
  `data/` files (mtime/content), never a DB row. Implementation options for the tick
  hook: (a) a thin wrapper in `reflections/__main__.py` that wraps `scheduler.tick`, or
  (b) a `heartbeat_path` hook on `ReflectionScheduler`. **Prefer (a)** — keeps the class
  free of process-specific I/O (the class is also imported by tests and, historically,
  the worker). The start-timestamp write lives only in `__main__` boot, never in the
  class.
- **`com.valor.reflection-worker.plist`** — long-lived launchd agent modeled on
  `com.valor.worker.plist`: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval`
  (restart-storm cap), `VALOR_LAUNCHD=1` (skip iCloud/TCC config paths),
  `WorkingDirectory=__PROJECT_DIR__`, stdout/stderr → `logs/reflection_worker.log` /
  `logs/reflection_worker_error.log`. `ProgramArguments` runs
  `.venv/bin/python -m reflections` (sourcing `.env` like the sdlc-reflection plist so
  Redis/GitHub creds are present; or env-injection like `install_worker.sh` — see
  Update System for the TCC rationale and the chosen approach).
- **`scripts/install_reflection_worker.sh`** — modeled on `install_sdlc_reflection.sh`
  (sed path-substitution, bootout/bootstrap) + a **`has_worker_role()` gate** adapted
  from `has_bridge_role()` in `install_nightly_tests.sh:20-72` (self-skip +
  stale-plist removal on machines with no owned project) + the `config/reflections.yaml`
  copy and `reflection_machine_filter` invocation **moved** from `install_worker.sh`.
  `has_worker_role()` is `has_bridge_role()` **minus the `if proj.get("telegram")`
  clause**: it qualifies if ANY project's `machine` matches `scutil --get ComputerName`,
  regardless of Telegram config, so reflections install wherever the worker installs
  (grounded: the worker install has no bridge gate — see Freshness Check). Same
  fail-open contract (unreadable `projects.json` / missing venv / `scutil` error →
  install). This deliberately avoids the #1379 over-narrow-gating class.
- **Delete the in-worker scheduler** — remove `worker/__main__.py:1020-1034`
  (construction) and `:1136-1142` (shutdown cancel) in the SAME change. Grep-verify
  zero residual `ReflectionScheduler(` in `worker/`.
- **Update wiring** — `scripts/update/service.install_reflection_worker()` delegating
  to the self-gating script, invoked from `scripts/update/run.py` **after the worker
  install/restart block** (cutover ordering — see below).
- **Dashboard surface** — `ui/app.py` `_get_reflection_scheduler_health()` reads BOTH
  `data/last_reflection_tick` freshness AND `data/reflection_worker_starts`
  (`last_start_age_s` + informational `restart_count`). It returns `{status,
  tick_age_s, restart_count, last_start_age_s}` where `status` is derived **purely from
  tick freshness** (mirroring `_get_worker_health`), NOT from the counter. Following the
  `_get_worker_health` flatten convention (its `{status, age_s}` become
  `health.worker`/`health.worker_last_seen_s`), these fields are flattened into the
  `health` dict on `/dashboard.json` as `reflection_scheduler_status`,
  `reflection_scheduler_tick_age_s`, `reflection_scheduler_restart_count`, and
  `reflection_scheduler_last_start_age_s`. A crash loop is visible via
  `reflection_scheduler_last_start_age_s` staying near-zero even when tick freshness
  looks healthy.

### Flow

/update on a bridge machine → git pull (new worker code w/o scheduler + new
installer) → uv sync → **worker install/restart** (new worker starts, no in-process
scheduler → brief zero-scheduler window) → **`install_reflection_worker.sh`**
(has_worker_role → copy `config/reflections.yaml` + machine-filter → bootstrap plist,
`RunAtLoad` starts the subprocess → scheduler resumes). On a machine with no owned
project the installer self-skips and removes any stale plist. Dashboard reads
`data/last_reflection_tick` + `data/reflection_worker_starts` to show the subprocess is
alive and not crash-looping.

### Technical Approach

**1. `reflections/__main__.py` (new).**
```python
# python -m reflections   → long-lived launchd process (KeepAlive)
import argparse, asyncio, json, logging, os, signal, time
from pathlib import Path
from agent.reflection_scheduler import ReflectionScheduler

_DATA = Path(__file__).parent.parent / "data"
_HEARTBEAT = _DATA / "last_reflection_tick"
_STARTS = _DATA / "reflection_worker_starts"   # crash-loop signal (last_start_age_s)
_log = logging.getLogger("reflections")

def _write_heartbeat() -> None:
    try:
        _HEARTBEAT.parent.mkdir(exist_ok=True)
        _HEARTBEAT.write_text(str(time.time()))
    except OSError as e:
        _log.warning("heartbeat write failed: %s", e)

def _record_boot() -> None:
    """Record boot timestamp + bump the boot counter, once per process start, ATOMICALLY.

    The operator crash-loop signal is last_start_age_s staying near-zero — a
    crash-restart loop keeps last_reflection_tick fresh but keeps resetting
    last_start_ts to ~now. `count` is informational-only (deploys inflate it).

    ABSENT file  → first boot, count starts at 1.
    CORRUPT file → preserve the prior count if partially readable; if unparseable,
                   log a WARNING and continue from a best-effort read — NEVER silently
                   reset to 1 (that would zero the signal during the crash storm it
                   targets).
    ATOMIC write → temp-file + os.replace(): a SIGKILL mid-write must not truncate the
                   file and destroy the signal (a bare write_text would)."""
    try:
        _STARTS.parent.mkdir(exist_ok=True)
        prior = 0
        if _STARTS.exists():
            try:
                prior = int(json.loads(_STARTS.read_text()).get("count", 0))
            except (ValueError, json.JSONDecodeError):
                _log.warning("reflection_worker_starts corrupt; preserving best-effort count")
                # keep prior=0 only if truly unreadable; do NOT treat corrupt as first-boot
        payload = json.dumps({"count": prior + 1, "last_start_ts": time.time()})
        tmp = _STARTS.with_suffix(".tmp")
        tmp.write_text(payload)
        os.replace(tmp, _STARTS)          # atomic rename — never a truncated file
    except OSError as e:
        _log.warning("start-record write failed: %s", e)

async def _run(dry_run: bool) -> None:
    scheduler = ReflectionScheduler()
    if dry_run:
        scheduler.load(); print(scheduler.format_status()); return
    _record_boot()   # once per boot, before the tick loop (atomic start-timestamp write)
    # wrap tick to emit the heartbeat without polluting the class
    _orig_tick = scheduler.tick
    async def _tick_with_heartbeat():
        _write_heartbeat()
        return await _orig_tick()
    scheduler.tick = _tick_with_heartbeat          # option (a): __main__-local wrap
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(scheduler.start())
    await stop.wait()
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

def main() -> None:
    p = argparse.ArgumentParser(prog="reflections")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(_run(args.dry_run))

if __name__ == "__main__":
    main()
```
(Exact wrap-vs-callback shape is a builder nit; the invariant is: heartbeat on every
tick, clean SIGTERM shutdown so launchd `KeepAlive` restarts are graceful, `--dry-run`
validates.)

**2. Delete the in-worker scheduler.** Remove `worker/__main__.py:1020-1034` and the
`:1136-1142` cancel block entirely. NO commented-out code, NO feature flag, NO
parallel-run. After: `grep -c "ReflectionScheduler(" worker/__main__.py == 0` and
`grep -rc "ReflectionScheduler\|reflection-scheduler\|reflection_task" worker/ == 0`.

**3. `com.valor.reflection-worker.plist` (new).** Copy `com.valor.worker.plist`,
change Label to `__SERVICE_LABEL__` (→ `com.valor.reflection-worker`), keep `RunAtLoad`,
`KeepAlive=true`, `ThrottleInterval` (use the worker's 10 or a longer value, e.g. 30 —
provisional, commented as tunable), set `VALOR_LAUNCHD=1` in `EnvironmentVariables`,
redirect logs to `logs/reflection_worker.log` / `_error.log`. **Creds delivery: source
`.env` in `ProgramArguments`** via the sdlc-reflection idiom
(`com.valor.sdlc-reflection.plist:9-11` — verified `/bin/bash` + `-c`, NOT `zsh -l`):
`ProgramArguments` = `["/bin/bash", "-c",
"set -a; source __PROJECT_DIR__/.env; set +a; exec __PROJECT_DIR__/.venv/bin/python -m
reflections"]`. Using `/bin/bash -c` (not `zsh -l`) avoids login-profile sourcing under
launchd where there is no TTY. This is TCC-safe under `VALOR_LAUNCHD=1` (no iCloud/vault read) and
keeps the plist self-contained (no install-time env-injection). It also makes the
installer's `--dry-run` env parity trivial (the installer sources the same `.env`
before probing — step 4).

**4. `scripts/install_reflection_worker.sh` (new).** Structure:
- Header + `set -euo pipefail` + `SCRIPT_DIR`/`PROJECT_DIR` + `set -a; [ -f .env ] &&
  source .env; set +a` (the `install_sdlc_reflection.sh:10-12` env-sourcing pattern) +
  `SERVICE_LABEL_PREFIX`.
- **`has_worker_role()` gate** adapted from `has_bridge_role()`
  (`install_nightly_tests.sh:20-72`): identical structure and fail-open contract, but
  the Python snippet drops the `if proj.get("telegram")` clause — it `sys.exit(0)`
  (qualify) as soon as any project's `machine` matches the host. Self-skip + `rm -f`
  stale plist on machines with no owned project (bootout + remove, exactly like the
  nightly-tests self-skip).
- **Config prep (moved from `install_worker.sh:47-62`):** `_copy_config_file
  "$HOME/Desktop/Valor/reflections.yaml" "$PROJECT_DIR/config/reflections.yaml"`, then
  run `tools.reflection_machine_filter` against the copied yaml + `projects.json`.
  (This is the MOVE — delete the same block from `install_worker.sh`; the worker no
  longer runs reflections so it no longer needs the reflections.yaml copy.)
- **Verify subprocess starts with production env parity:** run
  `.venv/bin/python -m reflections --dry-run` **after** the `.env` has been sourced
  and with `VALOR_LAUNCHD=1` exported, so the dry-run exercises the SAME env resolution
  the plist runtime will (the plist sources `.env` in `ProgramArguments` and sets
  `VALOR_LAUNCHD=1` — see step 3). Without this, the dry-run would read the vault path
  or a different config and give a false verification signal. Concretely, wrap the
  probe as `VALOR_LAUNCHD=1 .venv/bin/python -m reflections --dry-run` inside the
  already-`source`d installer shell.
- bootout existing → sed path-substitution into `$PLIST_DST` → `plutil -lint` →
  `launchctl bootstrap`. (The plist sources `.env` itself in `ProgramArguments` per
  step 3, so no separate env-injection step is required — matching sdlc-reflection.)

**5. Wire into `scripts/update/`.**
- `scripts/update/service.py`: add `install_reflection_worker(project_dir)` modeled on
  `install_nightly_tests()` (`:360-388`) — locate `scripts/install_reflection_worker.sh`,
  run it, log rc; the shell script self-gates so the Python wrapper stays dumb.
- `scripts/update/run.py`: call `service.install_reflection_worker(project_dir)`
  **after** the worker install/restart block (ends ~`:1443`), **unconditionally** (NOT
  under `if has_bridge:`) — the shell script self-gates on `has_worker_role()`, and the
  subprocess must install everywhere the worker does. Placing it AFTER the worker
  restart guarantees the worker (new code, no scheduler) is up before the subprocess
  starts — see Cutover.

**6. Cutover ordering (avoid double/zero scheduler).**
- **Ordering rule:** worker restart FIRST (new worker has no in-process scheduler),
  THEN `install_reflection_worker.sh` bootstraps the plist (`RunAtLoad` starts the
  subprocess).
- **Window claim substantiated against the actual restart mechanism.**
  `restart_worker()` (`scripts/valor-service.sh:838`) restarts via
  `launchctl kickstart -k "gui/<uid>/$WORKER_PLIST_NAME"` — the `-k` flag is a **hard
  kill-and-relaunch** (SIGKILL the running worker, immediately respawn); there is NO
  graceful drain in the restart path (the graceful bootout+wait lives only in the
  separate `stop_worker`). On a code change, `install_worker` (`service.py:326-354`)
  likewise does `bootout` → rewrite → `bootstrap`, which also terminates-then-relaunches.
  Either way the OLD in-process scheduler dies **atomically at the restart instant** —
  no lingering old scheduler. `restart_worker` then `sleep 5` before probing, so the
  new (schedulerless) worker is up ~5s later, and the reflection plist bootstrap
  follows in the next `run.py` step. Net zero-scheduler window: a handful of seconds,
  bounded by the worker-restart-to-plist-bootstrap gap. Reflections are periodic (tick
  interval ≥ 60s, job intervals hours/days), so no reflection misses its due window.
  Because the old scheduler is hard-killed (not drained-in-parallel), there is also no
  window in which BOTH tick — the "never double" claim holds by construction, not just
  by idempotency.
- **Never double:** the alternative order (start the plist while the OLD worker still
  runs its in-process scheduler) would briefly run TWO schedulers. Worker-first
  ordering forbids that. **Defense-in-depth:** even if ordering slips, a two-scheduler
  overlap is idempotent — `is_reflection_running(state)` skips a reflection already
  marked `running`, and `Reflection.get_or_create` is the single Redis source of
  truth, so at worst one duplicate enqueue races and the second is status-skipped;
  `reap_stale_running()` cleans any residue on next start.
- **First deploy on a fresh bridge machine:** no prior in-worker scheduler to overlap;
  the installer just bootstraps the plist. On the migrating machine, the OLD worker's
  scheduler dies exactly when the worker is restarted with the new code, before the
  plist is bootstrapped → clean handoff.

**7. Operator surface (heartbeat + restart-count → dashboard).**
- `data/last_reflection_tick` written every tick (step 1); `data/reflection_worker_starts`
  written once per process boot (step 1, `_record_boot`, atomic temp-file + `os.replace`).
- `ui/app.py`: add `_get_reflection_scheduler_health()` mirroring
  `_get_worker_health()` (`ui/app.py:340`) — read `last_reflection_tick` mtime, compute
  `tick_age_s`, flag `status` (`ok`/`running`/`error`) derived **purely from tick
  freshness** against a stale threshold (≈ 2× `SCHEDULER_TICK_INTERVAL`, provisional
  constant, commented as tunable), NOT from the counter; ALSO read
  `reflection_worker_starts` for `restart_count` (informational) + `last_start_age_s`
  (crash-loop indicator). **Flatten** these into the `health` dict at `ui/app.py:517-541`
  — mirroring how `_get_worker_health`'s `{status, age_s}` become
  `health.worker`/`health.worker_last_seen_s` — as `reflection_scheduler_status`,
  `reflection_scheduler_tick_age_s`, `reflection_scheduler_restart_count`, and
  `reflection_scheduler_last_start_age_s`. Additive fields only — the rest of the
  dashboard contract is unchanged. A crash loop reads as fresh
  `reflection_scheduler_tick_age_s` + a near-zero `reflection_scheduler_last_start_age_s`
  (with an informational climbing `restart_count`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/__main__.py` heartbeat write failure (read-only `data/`, OSError)
  must NOT crash the tick loop — it logs a WARNING and the scheduler keeps ticking.
  Test: patch `Path.write_text` to raise, assert the tick still returns and a WARNING
  is captured.
- [ ] SIGTERM during a tick shuts the process down cleanly (cancel + await the
  scheduler task, no traceback) so launchd `KeepAlive` restarts are graceful. Test:
  drive `_run`, fire the SIGTERM handler, assert clean return.
- [ ] `has_worker_role()` fails OPEN on unreadable `projects.json` / missing venv /
  `scutil` error (installs rather than silently skipping) — mirrors the nightly-tests
  contract. Test the shell gate via a `projects.json` fixture matrix: machine owning a
  project WITHOUT a `telegram` block → **install** (the #1379-avoidance case);
  machine owning no project → skip + remove stale plist; missing config → fail-open
  install.
- [ ] Boot start-record write is resilient AND atomic: an ABSENT
  `data/reflection_worker_starts` starts at `count=1`; a CORRUPT file does NOT silently
  reset to 1 (logs a WARNING, preserves the best-effort count) — so the signal survives
  the crash storm it targets; the write goes through temp-file + `os.replace()` so a
  SIGKILL mid-write never truncates the file; an OSError on write is logged, not fatal.
  Test: (a) absent file → count=1; (b) seed a garbage file → WARNING logged, NOT reset
  to 1, `last_start_ts` refreshed; (c) assert the write path uses `os.replace` (atomic),
  and no exception escapes.

### Empty/Invalid Input Handling
- [ ] `python -m reflections --dry-run` with an empty/absent `config/reflections.yaml`
  loads zero entries, prints status, exits 0 (never bootstraps a broken plist). Test.
- [ ] Installer `--dry-run` env parity: the verify probe runs with `.env` sourced and
  `VALOR_LAUNCHD=1` set, exercising the same config-resolution path the plist runtime
  uses. Test: assert the installer's probe command line carries `VALOR_LAUNCHD=1` and
  runs after the `source .env` step (shell-level fixture / lint of the script).
- [ ] Reflection registry with a disabled/invalid entry is skipped by the existing
  `load_registry` validation (unchanged) — subprocess start is unaffected. Covered by
  existing `test_reflection_scheduler.py`; add a smoke test that `python -m reflections
  --dry-run` exits 0 against the in-repo fallback yaml.

### Error State Rendering
- [ ] A dead scheduler is VISIBLE: with a stale/absent `data/last_reflection_tick`,
  `_get_reflection_scheduler_health()` reports `status="error"` with the age, and
  `/dashboard.json` shows the stale block. Test the health helper with a stale mtime.
- [ ] A crash-LOOPING scheduler is VISIBLE even with a fresh tick: with a fresh
  `data/last_reflection_tick` but a near-zero `last_start_age_s` in
  `data/reflection_worker_starts`, `_get_reflection_scheduler_health()` surfaces
  `reflection_scheduler_last_start_age_s` near zero (the crash-loop indicator) while
  `status` stays freshness-derived. Test the helper with a fresh-tick + near-zero-start
  fixture, asserting `last_start_age_s` exposes the loop and that a benign single deploy
  bump (one `restart_count` increment with a climbing start-age) does NOT read as a loop.
- [ ] The subprocess logs a startup line naming the tick interval and entry count
  (existing `ReflectionScheduler.start()` log) to `logs/reflection_worker.log` so
  operators can confirm it came up. Assert the log line on start.

## Test Impact

- [ ] `tests/unit/test_worker_supervisor.py` — no change expected (0 refs to the
  reflection wiring); RE-RUN to confirm deleting the worker block does not regress the
  generic `supervise()` tests. Disposition: verify-only, UPDATE only if a fixture
  imports the deleted symbol.
- [ ] `tests/unit/test_reflection_scheduler.py` / `test_reflection_scheduler_grammar.py`
  / `test_reflections_package.py` — the `ReflectionScheduler` class is unchanged, so
  these stay green. Disposition: verify-only, no edits expected.
- [ ] `tests/integration/test_worker_concurrency.py` — references `_run_worker`; RE-RUN
  to confirm the worker still boots after the reflection block is removed. Disposition:
  verify-only.
- [ ] Search for any test asserting the worker starts `reflection-scheduler` via
  `supervise` — `grep -rn "reflection-scheduler" tests/` currently returns only
  `tests/README.md` (docs) — no test asserts worker ownership, so nothing to DELETE.
  If a new such assertion is found at build time, DELETE it (the worker no longer owns
  the scheduler).

New tests (greenfield):
- `tests/unit/test_reflections_main.py` — `python -m reflections --dry-run` exits 0
  and prints status; heartbeat is written each tick; a heartbeat OSError is swallowed
  with a WARNING; `_record_boot()` starts at `count=1` on an ABSENT file, does NOT
  reset to 1 on a CORRUPT file (logs a WARNING, preserves best-effort count) and writes
  a fresh `last_start_ts`; the write is atomic (temp-file + `os.replace`, so a
  mid-write failure never truncates the file); SIGTERM triggers clean shutdown.
- `tests/unit/test_reflection_scheduler_health.py` — `_get_reflection_scheduler_health()`
  derives `status` PURELY from tick freshness: `ok`/`running` for a fresh
  `data/last_reflection_tick` and `error` for an old or absent file; surfaces
  `restart_count` (informational) and `last_start_age_s`; distinguishes a healthy
  scheduler (fresh tick, `last_start_age_s` climbing) from a crash loop (fresh tick,
  `last_start_age_s` near-zero) — and confirms `status` stays `ok` under a benign deploy
  bump (a single `restart_count` increment must NOT flip status); `/dashboard.json`
  `health` dict includes the flattened `reflection_scheduler_*` fields.
- `tests/integration/test_install_reflection_worker.py` (or a shell-level fixture test)
  — `has_worker_role()` install/skip matrix: machine owning a non-Telegram project
  installs (the #1379-avoidance case), machine owning no project skips and removes a
  pre-seeded stale plist, unreadable config fails-open; and the `--dry-run` probe runs
  with `VALOR_LAUNCHD=1` after `source .env`.

## Rabbit Holes

- **Do NOT modify `ReflectionScheduler`.** The class is reused verbatim. If the
  heartbeat needs a hook, wrap `tick` in `__main__` — do not thread process-specific
  file I/O into the shared class (it is imported by tests and other callers).
- **Do NOT use the `StartInterval` (cron) lifecycle** from
  `com.valor.sdlc-reflection.plist`. The scheduler is a long-lived loop — use
  `KeepAlive=true` + `ThrottleInterval` (the worker plist model), per the issue.
- **Do NOT leave the in-worker scheduler behind a flag or as dead code.** Fully delete
  `worker/__main__.py:1020-1034` + `:1136-1142`. No parallel-run (HARD repo rule).
- **Do NOT duplicate the `reflections.yaml` copy + machine-filter** in both installers.
  MOVE it from `install_worker.sh` into `install_reflection_worker.sh` (single owner).
- **Do NOT add a Popoto model field or migration.** Communication is via existing
  `Reflection` records; the heartbeat is a `data/` file, not a DB row (mirrors
  `last_worker_connected`).
- **Do NOT build a separate watchdog process** for the reflection subprocess. launchd
  `KeepAlive` IS the supervisor; the dashboard heartbeat is the visibility layer. A
  bespoke watchdog would recreate machinery launchd already provides.
- **Do NOT gate on `has_bridge_role()`** — that would strand reflections on
  worker-only (non-Telegram) machines, the #1379 over-narrow-gating failure class.
  Gate on `has_worker_role()` (adapted from `has_bridge_role()` by dropping the
  Telegram clause) so reflections run exactly where the worker runs. Keep the adaptation
  minimal and structurally identical to `has_bridge_role()` (same fail-open contract,
  same self-skip + stale-plist removal) — the ONLY delta is the removed
  `if proj.get("telegram")` clause.

## Risks

### Risk 1: Zero-scheduler window during cutover
**Impact:** Between the worker restart (new code, no in-process scheduler) and the
plist bootstrap, no scheduler ticks.
**Mitigation:** The window is a handful of seconds, substantiated against the actual
restart mechanism: `restart_worker` uses `launchctl kickstart -k`
(`scripts/valor-service.sh:838`), a HARD kill-and-relaunch (no graceful drain), so the
old scheduler dies atomically and the new schedulerless worker is up ~5s later; the
reflection plist bootstrap follows immediately in `run.py`. Reflection tick interval is
≥60s and job intervals are hours/days, so no reflection misses its due window. Ordering
(worker-restart-then-install) is deterministic in `run.py`. Acceptable by design.

### Risk 2: Transient double-scheduler if ordering slips
**Impact:** Two schedulers tick simultaneously → a reflection could be enqueued twice.
**Mitigation:** `is_reflection_running(state)` status-skip + single-Redis-record
source of truth + `reap_stale_running()` make double-enqueue idempotent; the second
tick finds `running` and skips. Deterministic ordering in `run.py` avoids the overlap
in the first place; the idempotency is defense-in-depth.

### Risk 3: Over-narrow install gate strands reflections on worker machines
**Impact:** If the subprocess were gated on `has_bridge_role()`, a machine that runs
the worker but has NO `telegram`-configured project (a dev workstation) would stop
running the reflections its in-process scheduler runs today — silently, and exactly
the #1379 failure class (over-narrow gating that DROPS legitimate work).
**Mitigation (RESOLVED, not open):** the installer gates on **`has_worker_role()`** —
qualify if the host owns ANY project in `projects.json` (Telegram or not), so the
subprocess installs precisely where the worker installs (the worker install has no
bridge gate — see Freshness Check). `reflection_machine_filter` still scopes
project-specific audits so a machine only runs the reflections it owns. This preserves
current behavior (reflections run wherever the worker runs) while retaining the
self-skip + stale-plist-removal hygiene for machines that own nothing. No open PM
question remains.

### Risk 4: Subprocess can't read config under launchd (TCC)
**Impact:** launchd agents can't read `~/Desktop` iCloud files; a naive read of the
vault `reflections.yaml`/`.env` hangs.
**Mitigation:** Set `VALOR_LAUNCHD=1` in the plist (skips the vault path,
`reflection_scheduler.py:90`), rely on the local `config/reflections.yaml` the
installer copies, and source `.env` in the plist `ProgramArguments` (`/bin/bash -c`, the
verified `com.valor.sdlc-reflection.plist:9-11` idiom) so Redis/GitHub creds are present
without a runtime iCloud read.

### Risk 5: Crash-loop signal self-destructs during the failure it targets
**Impact:** A naive `_STARTS.write_text(...)` is non-atomic — a SIGKILL mid-write during
a crash storm truncates the file; a recovery path that treats a corrupt file as
first-boot would reset the counter and zero `last_start_age_s`'s history exactly when
the crash loop is happening, blinding the operator to the failure the signal exists for.
**Mitigation:** Write via temp-file + `os.replace()` (atomic rename — a partial write is
never observable). On read, distinguish ABSENT (first boot → `count=1`) from CORRUPT (log
a WARNING, preserve the best-effort count, never silently reset to 1). Because the
operator crash-loop indicator is `last_start_age_s` (each boot refreshes `last_start_ts`
to ~now, so a loop keeps the age near-zero regardless of `count`), the signal survives
even a wholly unreadable counter — it is the timestamp, not the count, that reveals the
loop. `restart_count` is informational-only and never an alarm source.

## Race Conditions

### Race 1: Cutover overlap (old in-worker scheduler vs new subprocess)
**Location:** the /update deploy sequence on the migrating bridge machine.
**Trigger:** the plist bootstraps while the old worker (old code) is still ticking.
**Data prerequisite:** both read the same `Reflection` records in Redis.
**State prerequisite:** worker-restart-then-install ordering in `run.py`.
**Mitigation:** Ordering makes the old scheduler die (worker restart) before the plist
loads. Even under overlap, `is_reflection_running` + status-guarded
`Reflection.get_or_create` dedup any double enqueue idempotently.

### Race 2: Heartbeat write vs dashboard read
**Location:** `data/last_reflection_tick` written by the subprocess, read by `ui/app.py`.
**Trigger:** dashboard reads mid-write.
**Data prerequisite:** file mtime freshness.
**State prerequisite:** single writer (the subprocess), many readers.
**Mitigation:** the reader only uses mtime/age (never partial content parsing beyond a
best-effort float), and a partial read falls back to "stale" — a benign
false-negative that self-corrects on the next tick. Mirrors the existing
`last_worker_connected` freshness read.

### Race 3: Two machines both run the subprocess
**Location:** cross-machine, if `projects.json` mis-assigns a project's `machine`.
**Trigger:** two machines both qualify via `has_worker_role()`.
**Data prerequisite:** single-machine-ownership in `projects.json`.
**State prerequisite:** `reflection_machine_filter` disables project-scoped
reflections this machine doesn't own (run at install time).
**Mitigation:** the machine-filter (moved into this installer) already prevents a
machine from running project audits it doesn't own — the exact duplicate-issue guard
that exists today. Global reflections' idempotency (Race 1) covers the rest.

## No-Gos (Out of Scope)

- **Changing `ReflectionScheduler` behavior, cadence, or the reflection registry.**
  Pure relocation.
- **A Popoto schema change or migration.** Redis-record contract unchanged.
- **A bespoke reflection watchdog process.** launchd `KeepAlive` + dashboard
  heartbeat only.
- **[ORDERED] Tuning `ThrottleInterval` / the tick stale-threshold / the
  `last_start_age_s` "near-zero" crash-loop threshold** to production-observed values —
  ships conservative, tuned after observing real restart rates on the live machine (same
  posture as #1815's threshold tuning).
- **A windowed restart-rate classifier.** Explicitly NOT built: a rolling-window rate is
  not computable from the persisted cumulative `{count, last_start_ts}` snapshot (no
  time-series), the single-snapshot `last_start_age_s` indicator suffices, and
  `launchctl print` already holds per-restart history for the rare forensic case.
- **[SEPARATE] Cross-machine reflection failover / warm standby** — out of scope;
  single-machine-ownership stands.

## Update System

**This is the central complexity of the plan.** The reflection subprocess is a new
launchd service that must be installed on the right machines, self-skip on the wrong
ones, and be wired into the multi-machine `/update` flow (`scripts/remote-update.sh`
→ `scripts/update/run.py`).

**New installer, worker-role-gated + self-healing (adapts the nightly-tests pattern):**
- `scripts/install_reflection_worker.sh` contains a `has_worker_role()` gate adapted
  from `has_bridge_role()` (`scripts/install_nightly_tests.sh:20-72`) — same structure
  and fail-open contract, but the Python snippet **drops the `if proj.get("telegram")`
  clause** so it qualifies when the host owns ANY project. This matches where the
  worker actually installs (the worker install is ungated by role — `run.py:1341` is
  guarded only by `plist.exists()`, and `install_worker.sh` has no machine gate). On a
  machine that owns NO project it: prints "Skipping reflection-worker install", and if
  a stale `com.valor.reflection-worker.plist` exists in `~/Library/LaunchAgents/`,
  `launchctl bootout` + `rm -f` it, then `exit 0` — the **self-skip +
  stale-plist-removal** contract for role changes.
- **Why not `has_bridge_role()`:** gating on bridge-role would strand reflections on
  worker-only (non-Telegram) machines — the #1379 over-narrow-gating failure class
  (which gated calendar on `session.slug` and DROPPED all non-slug work). Worker-role
  gating runs reflections precisely where the worker runs.
- Fail-open: unreadable `projects.json`, missing venv, or `scutil` error → install
  (matches nightly-tests, so a config hiccup never silently drops reflections).

**Wiring into `scripts/update/run.py`:**
- Add `scripts/update/service.py::install_reflection_worker(project_dir)` modeled on
  `install_nightly_tests()` (`:360-388`): resolve `scripts/install_reflection_worker.sh`,
  run it, log rc; the shell script owns the gate so the Python wrapper is dumb.
- Call it in `run.py` from the service-restart branch, **after** the worker
  install/restart block (~`:1341-1443`). **Do NOT place it under `if has_bridge:`** —
  the reflection subprocess must install wherever the worker installs (every /update
  machine), so the call is unconditional (mirroring the ungated worker-install block at
  `:1341`); the shell script's own `has_worker_role()` gate handles the skip/self-heal
  on machines that own nothing. This is the key wiring difference from the nightly-tests
  install (which IS bridge-gated). **Placement after the worker restart is load-bearing
  for cutover ordering** (worker-first → at most a brief zero-scheduler window, never
  double — see Technical Approach step 6).

**Config propagation (MOVE, not duplicate):**
- The `config/reflections.yaml` copy + `tools.reflection_machine_filter` invocation
  currently in `scripts/install_worker.sh:47-62` MOVES into
  `install_reflection_worker.sh` (the new owner of reflections). Delete it from
  `install_worker.sh` — the worker no longer runs the scheduler, so it no longer needs
  the reflections.yaml copy. `projects.json` copy stays in `install_worker.sh` (the
  worker needs it for other reasons). Both installers run in the same /update, so the
  registry is prepared exactly once, by the process that owns it.
- Note the `env_sync.sync_reflections_yaml` / `reflections_yaml` migration steps in
  `run.py` (`:500-514`, `:780-802`) that ensure `config/reflections.yaml` is a real
  file (not a symlink) still run before either installer — the subprocess benefits from
  them unchanged.

**Migration for existing installations:** on the next `/update` on any machine that
runs the worker, the new worker code (no in-process scheduler) lands, the worker
restarts, then `install_reflection_worker.sh` bootstraps the new plist. No manual step.
On a machine that owns no project (`has_worker_role()` false) the installer self-skips.
No stale `com.valor.reflection-worker.plist` can exist yet (new label), so no cleanup
needed on first deploy; the stale-removal path is for future role changes.

**Logs:** add `logs/reflection_worker.log` / `logs/reflection_worker_error.log` to the
same `logs/` dir the log-rotate plist already covers (verify the glob covers the new
files; if it enumerates explicitly, add them).

**No `.env` secret additions required** — the subprocess uses the existing Redis /
GitHub creds sourced from `.env` (or env-injected) exactly as the worker does.
`SCHEDULER_TICK_INTERVAL`, the new tick stale-threshold constant, and the
`last_start_age_s` near-zero crash-loop threshold are optional with safe defaults; if
surfaced as env, add to `.env.example` with a comment line above each
(completeness-check requirement).

## Agent Integration

- **New CLI entry point:** `python -m reflections` is the launchd entry — it runs via
  `.venv/bin/python -m reflections`, so **no `pyproject.toml [project.scripts]` entry
  is required** (module execution, like `python -m worker`). If a named console script
  is desired for operator ergonomics (e.g. `valor-reflections`), it can be added to
  `[project.scripts]` pointing at `reflections.__main__:main`, but it is optional and
  not required for the launchd service.
- **Bridge does NOT import the new code.** The bridge and worker communicate with the
  scheduler only through Redis `Reflection`/`AgentSession` records — the same seam as
  today. No bridge change.
- **Agent-visible surface:** the agent can inspect scheduler health via the existing
  `curl -s localhost:8500/dashboard.json` (now including the `reflection_scheduler`
  block) and the existing `python scripts/reflections_report.py` / dashboard reflection
  grid — no new agent tool needed.
- **Integration test that the entry point is invocable:** `python -m reflections
  --dry-run` exits 0 (covered by `tests/unit/test_reflections_main.py`), proving the
  launchd `ProgramArguments` will start.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/reflection-scheduler-subprocess.md` describing: why the
  scheduler moved out of the worker (freeze-isolation / crash-domain decoupling, the
  #1816 Fix #5 lineage), the `reflections/__main__.py` entry, the
  `com.valor.reflection-worker.plist` (`KeepAlive`+`ThrottleInterval`) lifecycle, the
  **`has_worker_role()`** install gate + self-skip (and why worker-role not bridge-role,
  citing the #1379 over-narrow-gating precedent), the cutover ordering
  (worker-restart-then-install via `launchctl kickstart -k` hard restart;
  zero-window-not-double), the two operator signals — `data/last_reflection_tick`
  (tick freshness, drives `status`) + `data/reflection_worker_starts`
  (`last_start_age_s` = crash-loop indicator; `restart_count` = informational,
  deploy-inflated, NOT an alarm; no windowed classifier) — and the `/dashboard.json`
  `health.reflection_scheduler_*` flattened surface, and the moved
  config-copy/machine-filter ownership. State it is the continuation of
  `worker-fault-containment.md` (#1816).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/worker-fault-containment.md` (and any reflections doc,
  e.g. `docs/features/reflections.md`) to describe the NEW status quo — the scheduler
  runs out-of-process — with a forward-link, per the no-historical-artifacts rule (no
  "used to be in the worker" parallel narrative beyond a one-line pointer).
- [ ] Update the Quick Commands / service table in `CLAUDE.md` with the new
  `com.valor.reflection-worker` service, `logs/reflection_worker.log`, and
  `python -m reflections --dry-run`.

### Inline Documentation
- [ ] Comment the `VALOR_LAUNCHD=1` requirement + the two heartbeat files in
  `reflections/__main__.py`: `data/last_reflection_tick` (per-tick freshness, drives
  `status`) and `data/reflection_worker_starts` (`{count, last_start_ts}`, atomic write —
  why the crash-loop signal is `last_start_age_s` near-zero, not the counter, and why the
  write must use `os.replace` + preserve a corrupt file rather than reset it).
- [ ] Comment the cutover-ordering requirement at the `run.py` install call site
  (worker-first).
- [ ] Comment the moved config-copy block in `install_reflection_worker.sh` (why it
  left `install_worker.sh`).
- [ ] Comment any new stale-threshold / `ThrottleInterval` constant with the
  grain-of-salt "provisional, tune after observing restart rates" note.

## Success Criteria

- [ ] **Scheduler runs out-of-process:** `reflections/__main__.py` exists,
  `python -m reflections --dry-run` exits 0, and `com.valor.reflection-worker.plist` +
  `scripts/install_reflection_worker.sh` exist.
- [ ] **Worker no longer constructs the scheduler (no parallel-run):**
  `grep -c "ReflectionScheduler(" worker/__main__.py == 0` and
  `grep -rc "ReflectionScheduler" worker/ == 0` and
  `grep -c "reflection-scheduler\|reflection_task" worker/__main__.py == 0`.
- [ ] **Long-lived supervised lifecycle:** `grep -c "KeepAlive"
  com.valor.reflection-worker.plist > 0` and `grep -c "ThrottleInterval"
  com.valor.reflection-worker.plist > 0` (NOT `StartInterval`).
- [ ] **Worker-role-gated + self-healing install (NOT bridge-role):**
  `grep -c "has_worker_role" scripts/install_reflection_worker.sh > 0` and
  `grep -c "get(\"telegram\")\|proj.get('telegram')" scripts/install_reflection_worker.sh
  == 0` (the Telegram clause is dropped), and the script bootout+rm's a stale plist on a
  machine owning no project (test). Wiring is NOT under `if has_bridge:` —
  `grep -B2 "install_reflection_worker" scripts/update/run.py` shows no `has_bridge`
  guard on the call.
- [ ] **Wired into /update:** `grep -c "install_reflection_worker"
  scripts/update/service.py > 0` and `grep -c "install_reflection_worker"
  scripts/update/run.py > 0`, called after the worker install block.
- [ ] **Config ownership moved (no duplicate):** the `reflections.yaml` copy +
  `reflection_machine_filter` block is present in `install_reflection_worker.sh` and
  removed from `install_worker.sh` — `grep -c "reflection_machine_filter"
  scripts/install_worker.sh == 0` and `> 0` in the new installer.
- [ ] **Operator-visible heartbeat (tick freshness + crash-loop start-age):**
  `grep -c "last_reflection_tick" reflections/__main__.py > 0`,
  `grep -c "reflection_worker_starts" reflections/__main__.py > 0` (start-timestamp
  signal), `grep -c "os.replace" reflections/__main__.py > 0` (atomic write), and
  `grep -c "last_start_age_s" ui/app.py > 0` (crash-loop indicator on the dashboard) —
  so a dead scheduler (stale tick) OR a crash-looping one (near-zero `last_start_age_s`
  despite a fresh tick) is visible via `localhost:8500/dashboard.json`. `status` is
  derived purely from tick freshness; `restart_count` is informational-only.
- [ ] **Live dashboard endpoint surfaces the fields:** with the web UI running,
  `curl -s localhost:8500/dashboard.json | python3 -c "import json,sys;
  h=json.load(sys.stdin)['health']; print(h['reflection_scheduler_status'],
  h['reflection_scheduler_tick_age_s'], h['reflection_scheduler_restart_count'],
  h['reflection_scheduler_last_start_age_s'])"` prints the four flattened `health`
  fields (not KeyError / None). Proves the surface is wired end-to-end into the `health`
  dict, not just present as a string.
- [ ] **No Popoto schema change:** no new model field / migration entry.
- [ ] Tests pass (`/do-test`) — new unit + integration tests green, existing
  reflection + worker-supervisor tests still green.
- [ ] Lint/format clean (`python -m ruff check`, `python -m ruff format --check`).
- [ ] Documentation created (`/do-docs`): `docs/features/reflection-scheduler-subprocess.md`
  exists and README index updated.

## Team Orchestration

The lead agent orchestrates via Task tools and NEVER builds directly.

### Team Members
- **Builder (subprocess)** — Name: refl-builder; Role: `reflections/__main__.py` +
  heartbeat + delete the worker block + plist + installer + update wiring + moved
  config-copy; Agent Type: builder; Resume: true.
- **Builder (dashboard)** — Name: dash-builder; Role: `_get_reflection_scheduler_health()`
  + `/dashboard.json` additive block; Agent Type: builder; Resume: true.
- **Validator** — Name: refl-validator; Role: verify success criteria (incl. live
  endpoint + flattened `health.reflection_scheduler_*` fields) + failure-path +
  cutover-ordering + worker-role gate matrix + crash-loop `last_start_age_s` visibility
  (atomic-write, absent-vs-corrupt) + dry-run env parity; Agent Type: validator;
  Resume: true.
- **Documentarian** — Name: refl-doc; Role: feature doc + README index + CLAUDE.md
  service table + forward-links; Agent Type: documentarian; Resume: true.

## Step by Step Tasks

### 1. Create the subprocess entry + heartbeat + restart-counter
- **Task ID**: build-subprocess
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_main.py (create)
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/__main__.py` (`python -m reflections`) reusing
  `ReflectionScheduler` verbatim: `--dry-run` (load + `format_status` + exit 0), clean
  SIGTERM/SIGINT shutdown, a `data/last_reflection_tick` heartbeat written each tick
  (wrap `scheduler.tick` in `__main__`, do NOT modify the class), and a
  `data/reflection_worker_starts` `{count, last_start_ts}` written once on boot via
  `_record_boot` — the crash-loop signal is `last_start_age_s` near-zero (see Data
  Flow), NOT the counter. Write ATOMICALLY (temp-file + `os.replace`); ABSENT file →
  `count=1`, CORRUPT file → log a WARNING and preserve best-effort count (do NOT reset
  to 1 — that would zero the signal during the crash storm it targets). Both writes log
  OSError, never fatal.

### 2. Delete the in-worker scheduler
- **Task ID**: delete-worker-block
- **Depends On**: build-subprocess
- **Validates**: tests/unit/test_worker_supervisor.py, tests/integration/test_worker_concurrency.py
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `worker/__main__.py:1020-1034` (construction) and `:1136-1142` (shutdown
  cancel). No flag, no comment stub. Confirm `grep -rc "ReflectionScheduler" worker/
  == 0` and the worker still boots (`python -m worker --dry-run`).

### 3. Create the plist + worker-role-gated installer + move config prep
- **Task ID**: build-install
- **Depends On**: build-subprocess
- **Validates**: tests/integration/test_install_reflection_worker.py (create)
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `com.valor.reflection-worker.plist` from `com.valor.worker.plist`
  (`KeepAlive=true` + `ThrottleInterval`, `VALOR_LAUNCHD=1`, `-m reflections`, logs to
  `logs/reflection_worker*.log`). `ProgramArguments` sources `.env` via the
  sdlc-reflection idiom (`set -a; source .env; set +a; exec ... -m reflections`).
- Create `scripts/install_reflection_worker.sh` from `install_sdlc_reflection.sh` +
  a `has_worker_role()` gate (adapted from `has_bridge_role()` in
  `install_nightly_tests.sh` by DROPPING the `if proj.get("telegram")` clause) +
  a `VALOR_LAUNCHD=1 -m reflections --dry-run` verify run AFTER `source .env`
  (env parity) + bootout/bootstrap + self-skip/stale-plist-removal.
- MOVE the `reflections.yaml` copy + `reflection_machine_filter` block from
  `install_worker.sh:47-62` into the new installer; delete it from `install_worker.sh`.

### 4. Wire into /update
- **Task ID**: wire-update
- **Depends On**: build-install
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `service.install_reflection_worker()` (modeled on `install_nightly_tests`) and
  call it in `run.py` **after** the worker install/restart block, **unconditionally**
  (NOT under `if has_bridge:` — the shell self-gates on `has_worker_role()`, and the
  subprocess must install everywhere the worker does). Comment the worker-first
  cutover-ordering rule at the call site.

### 5. Dashboard heartbeat + crash-loop (last_start_age_s) surface
- **Task ID**: build-dashboard
- **Depends On**: build-subprocess
- **Validates**: tests/unit/test_reflection_scheduler_health.py (create)
- **Assigned To**: dash-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_get_reflection_scheduler_health()` (mirror `_get_worker_health()`,
  `ui/app.py:340`) reading BOTH `data/last_reflection_tick` (tick freshness → `status`)
  and `data/reflection_worker_starts` (`last_start_age_s` = crash-loop indicator;
  `restart_count` = informational). Derive `status` PURELY from tick freshness (NOT the
  counter). **Flatten** the fields into the `health` dict (`ui/app.py:517-541`, mirroring
  the `_get_worker_health` flatten) as `reflection_scheduler_status`,
  `reflection_scheduler_tick_age_s`, `reflection_scheduler_restart_count`,
  `reflection_scheduler_last_start_age_s` — additive only.

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: delete-worker-block, wire-update, build-dashboard
- **Assigned To**: refl-validator
- **Agent Type**: validator
- **Parallel**: false
- Run new + existing tests; verify every Success Criteria grep (including the
  live-endpoint `curl` row); verify the `has_worker_role()` install/skip matrix
  (non-Telegram-project machine installs — the #1379-avoidance case), the crash-loop
  `last_start_age_s` visibility (fresh tick + near-zero start-age reads as a loop; a
  single deploy bump does NOT), the atomic-write / absent-vs-corrupt start-record
  behavior, the dry-run env parity, and the worker-first cutover ordering.

### 7. Documentation
- **Task ID**: document
- **Depends On**: validate-all
- **Assigned To**: refl-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/reflection-scheduler-subprocess.md`; add README index entry;
  update `CLAUDE.md` service table; forward-link `worker-fault-containment.md`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Subprocess entry exists | `test -f reflections/__main__.py && echo ok` | ok |
| Dry-run invocable | `python -m reflections --dry-run` | exit code 0 |
| Worker no longer constructs scheduler | `grep -c "ReflectionScheduler(" worker/__main__.py` | 0 |
| No residual scheduler wiring in worker | `grep -rc "ReflectionScheduler" worker/` | 0 |
| No residual reflection task/name in worker | `grep -Ec "reflection-scheduler\|reflection_task" worker/__main__.py` | 0 |
| Plist exists | `test -f com.valor.reflection-worker.plist && echo ok` | ok |
| Long-lived lifecycle (KeepAlive) | `grep -c "KeepAlive" com.valor.reflection-worker.plist` | > 0 |
| Restart-storm cap | `grep -c "ThrottleInterval" com.valor.reflection-worker.plist` | > 0 |
| NOT a cron lifecycle | `grep -c "StartInterval" com.valor.reflection-worker.plist` | 0 |
| Installer exists | `test -f scripts/install_reflection_worker.sh && echo ok` | ok |
| Worker-role gate present (NOT bridge) | `grep -c "has_worker_role" scripts/install_reflection_worker.sh` | > 0 |
| Telegram clause dropped from gate | `grep -c "get(\"telegram\")\|proj.get('telegram')" scripts/install_reflection_worker.sh` | 0 |
| Wiring NOT bridge-gated | `grep -B3 "install_reflection_worker" scripts/update/run.py \| grep -c "has_bridge"` | 0 |
| Stale-plist self-removal present | `grep -c "rm -f" scripts/install_reflection_worker.sh` | > 0 |
| Update service wrapper | `grep -c "install_reflection_worker" scripts/update/service.py` | > 0 |
| Update run.py call | `grep -c "install_reflection_worker" scripts/update/run.py` | > 0 |
| Config prep moved OUT of worker installer | `grep -c "reflection_machine_filter" scripts/install_worker.sh` | 0 |
| Config prep moved INTO reflection installer | `grep -c "reflection_machine_filter" scripts/install_reflection_worker.sh` | > 0 |
| Tick heartbeat written | `grep -c "last_reflection_tick" reflections/__main__.py` | > 0 |
| Start-timestamp (crash-loop) signal written | `grep -c "reflection_worker_starts" reflections/__main__.py` | > 0 |
| Atomic start-record write (not truncatable) | `grep -c "os.replace" reflections/__main__.py` | > 0 |
| Dashboard surface | `grep -c "reflection_scheduler" ui/app.py` | > 0 |
| Crash-loop indicator on dashboard | `grep -c "last_start_age_s" ui/app.py` | > 0 |
| Restart-count (informational) on dashboard | `grep -c "restart_count" ui/app.py` | > 0 |
| Dry-run env parity in installer | `grep -c "VALOR_LAUNCHD=1 .*-m reflections --dry-run\|VALOR_LAUNCHD=1.*reflections" scripts/install_reflection_worker.sh` | > 0 |
| Live endpoint surfaces flattened fields (UI running) | `curl -s localhost:8500/dashboard.json \| python3 -c "import json,sys; print('reflection_scheduler_status' in json.load(sys.stdin)['health'])"` | True |
| Lint clean | `python -m ruff check reflections/ worker/ ui/` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ worker/ ui/` | exit code 0 |
| No Popoto migration added | `git diff --name-only main -- scripts/update/migrations.py \| wc -l` | 0 |

## Open Questions

_None blocking._ The two questions from the prior revision are resolved in-plan:

**RESOLVED — gate on worker presence, not bridge-role.** The prior draft's OQ1 asked
whether to concentrate reflections on bridge machines. That framing risked the #1379
over-narrow-gating failure (which gated calendar on `session.slug` and DROPPED all
non-slug work). Grounding: the worker installs on every /update machine, ungated by
role (`run.py:1341` guarded only by `plist.exists()`; `install_worker.sh` has no
machine gate). To run reflections exactly where the worker runs, the installer gates
on **`has_worker_role()`** — `has_bridge_role()` with the `if proj.get("telegram")`
clause dropped (qualify if the host owns ANY project). This preserves current behavior
and keeps the self-skip + stale-plist-removal hygiene. Reflected in Solution, Update
System, Risk 3, Success Criteria, and Verification. No PM confirmation needed.

**RESOLVED — plist sources `.env` in `ProgramArguments`.** Chosen over install-time
env-injection: it matches the `com.valor.sdlc-reflection.plist:9-11` `/bin/bash -c` idiom, is TCC-safe
under `VALOR_LAUNCHD=1` (no vault/iCloud read), keeps the plist self-contained, and
makes the installer's `--dry-run` env parity trivial (the installer sources the same
`.env` and exports `VALOR_LAUNCHD=1` before probing — Technical Approach steps 3-4). If
a build-time launchd `.env`-read hang appears, fall back to the worker's env-injection
pattern (`install_worker.sh`) — a localized installer change, no plan-level dependency.
