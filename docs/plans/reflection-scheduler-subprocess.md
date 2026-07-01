---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1828
last_comment_id:
revision_applied: false
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
must not become invisible).

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
  (bootout + `rm -f`). This is the exact self-skip pattern the new installer copies.
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
- **Operator surface:** `ui/app.py:341` `_get_worker_health()` reads
  `data/last_worker_connected` file freshness; `ui/app.py:514-541` `/dashboard.json`
  route surfaces reflection state via `ui/data/reflections.get_all_reflections()`
  (per-`Reflection`-record `last_status`/`ran_at`). There is **no scheduler-level
  heartbeat today** — if the whole scheduler dies, individual records just go stale
  with no single "scheduler alive" signal. This gap is what moving out-of-process
  makes worse, and what this plan closes.
- Data dir today: `data/last_worker_connected`, `data/last_connected` — the
  file-freshness heartbeat convention the new scheduler heartbeat mirrors.

**Tests re-checked:** `tests/unit/test_worker_supervisor.py` has **0** references to
`reflection-scheduler`/`reflection_task` (it tests `supervise()` generically, not the
reflection wiring) — deleting the worker's reflection block does not break it.
`tests/unit/test_reflection_scheduler.py` tests the `ReflectionScheduler` class
directly (unchanged by this move). No test asserts the worker *owns* the scheduler.

**Notes:** No drift. The class is reused unchanged; the whole change is *relocation +
supervision + role-gated install + a heartbeat surface + deletion of the in-worker
wiring*.

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
  role-gated-install + self-skip + stale-plist-removal precedents this installer copies.
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
      → ReflectionScheduler().start()   [same class, same tick loop]
          → each tick: write data/last_reflection_tick  (heartbeat file, mtime)
          → reads config/reflections.yaml (local copy; VALOR_LAUNCHD skips vault)
          → Reflection / ReflectionRun records in Redis  ← unchanged contract
          → agent reflections enqueue AgentSession → picked up by the WORKER process

worker/__main__.py:_run_worker
  → (reflection block DELETED — no scheduler here)
  → still executes the AgentSession records the scheduler enqueues (unchanged)

ui/app.py /dashboard.json
  → _get_reflection_scheduler_health()  reads data/last_reflection_tick freshness
  → surfaces {running, last_tick_at, age_seconds} in the payload  ← NEW operator surface
```

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
  `asyncio.run(scheduler.start())`, heartbeat). New `ui/app.py`
  `_get_reflection_scheduler_health()` + one additive `/dashboard.json` field.
- **No Popoto schema change** — communication is via existing `Reflection` records.
- **Ownership move:** the `config/reflections.yaml` copy + `reflection_machine_filter`
  step moves from `install_worker.sh` into `install_reflection_worker.sh` (the new
  owner), so exactly one installer prepares the reflection registry (no parallel copy).
- **Reversibility:** medium. Reverting means restoring the ~20 worker lines and
  uninstalling the plist. The class is unchanged, so revert risk is low.

## Appetite

**Size:** Medium

**Team:** Solo dev. This is a *relocation* of an unchanged class plus a new
launchd install (well-trodden pattern) and a small dashboard surface — lower
blast-radius than the parent slug's in-loop refactors, but touches deploy wiring
across machines (careful role-gating + cutover ordering required).

**Interactions:**
- PM check-ins: 1 (confirm the bridge-role gate is the intended machine set — see
  Open Question 1: dev-only worker machines).
- Review rounds: 1–2 (cutover ordering / double-vs-zero-scheduler; role-gate
  self-skip; heartbeat surface).

**PR strategy:** single PR. The delete + add + install + heartbeat are one atomic
cutover (no-parallel-migration rule forbids splitting the delete from the add).

## Prerequisites

Builds on #1816 primitives, already merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ReflectionScheduler` present | `grep -c "class ReflectionScheduler" agent/reflection_scheduler.py` | The class the subprocess reuses |
| Worker still constructs it (pre-change) | `grep -c "ReflectionScheduler()" worker/__main__.py` | Confirms the create_task site to delete exists |
| Role-gate precedent present | `grep -c "has_bridge_role" scripts/install_nightly_tests.sh` | The self-skip pattern to copy |
| Worker plist lifecycle template | `grep -c "KeepAlive" com.valor.worker.plist` | The `KeepAlive`+`ThrottleInterval` template |

## Solution

### Key Elements

- **`reflections/__main__.py`** — new module entry (`python -m reflections`). Sets up
  logging, installs SIGTERM/SIGINT handlers for clean launchd shutdown, constructs
  `ReflectionScheduler()`, and runs `asyncio.run(scheduler.start())`. Reuses the class
  verbatim. Writes a `data/last_reflection_tick` heartbeat file each tick (see below).
  Accepts `--dry-run` (load registry, print status, exit 0) mirroring `python -m
  worker --dry-run` so the installer can validate before bootstrapping.
- **Heartbeat surface** — the scheduler writes `data/last_reflection_tick` (touch the
  file / write `time.time()`) at the top of every `tick()`. Two implementation
  options: (a) a thin wrapper in `reflections/__main__.py` that wraps
  `scheduler.tick`, or (b) a `heartbeat_path` hook on `ReflectionScheduler`. **Prefer
  (a)** — keeps the class free of process-specific I/O (the class is also imported by
  tests and, historically, the worker). The `__main__` wraps the tick loop or passes
  an `on_tick` callback.
- **`com.valor.reflection-worker.plist`** — long-lived launchd agent modeled on
  `com.valor.worker.plist`: `RunAtLoad=true`, `KeepAlive=true`, `ThrottleInterval`
  (restart-storm cap), `VALOR_LAUNCHD=1` (skip iCloud/TCC config paths),
  `WorkingDirectory=__PROJECT_DIR__`, stdout/stderr → `logs/reflection_worker.log` /
  `logs/reflection_worker_error.log`. `ProgramArguments` runs
  `.venv/bin/python -m reflections` (sourcing `.env` like the sdlc-reflection plist so
  Redis/GitHub creds are present; or env-injection like `install_worker.sh` — see
  Update System for the TCC rationale and the chosen approach).
- **`scripts/install_reflection_worker.sh`** — modeled on `install_sdlc_reflection.sh`
  (sed path-substitution, bootout/bootstrap) + the `has_bridge_role()` gate from
  `install_nightly_tests.sh` (self-skip + stale-plist removal on non-bridge machines)
  + the `config/reflections.yaml` copy and `reflection_machine_filter` invocation
  **moved** from `install_worker.sh`.
- **Delete the in-worker scheduler** — remove `worker/__main__.py:1020-1034`
  (construction) and `:1136-1142` (shutdown cancel) in the SAME change. Grep-verify
  zero residual `ReflectionScheduler(` in `worker/`.
- **Update wiring** — `scripts/update/service.install_reflection_worker()` delegating
  to the self-gating script, invoked from `scripts/update/run.py` **after the worker
  install/restart block** (cutover ordering — see below).
- **Dashboard surface** — `ui/app.py` `_get_reflection_scheduler_health()` reads
  `data/last_reflection_tick` freshness; `/dashboard.json` gains an additive
  `reflection_scheduler` health block.

### Flow

/update on a bridge machine → git pull (new worker code w/o scheduler + new
installer) → uv sync → **worker install/restart** (new worker starts, no in-process
scheduler → brief zero-scheduler window) → **`install_reflection_worker.sh`**
(has_bridge_role → copy `config/reflections.yaml` + machine-filter → bootstrap plist,
`RunAtLoad` starts the subprocess → scheduler resumes). On a non-bridge machine the
installer self-skips and removes any stale plist. Dashboard reads
`data/last_reflection_tick` to show the subprocess is alive.

### Technical Approach

**1. `reflections/__main__.py` (new).**
```python
# python -m reflections   → long-lived launchd process (KeepAlive)
import argparse, asyncio, logging, signal, time
from pathlib import Path
from agent.reflection_scheduler import ReflectionScheduler

_HEARTBEAT = Path(__file__).parent.parent / "data" / "last_reflection_tick"

def _write_heartbeat() -> None:
    try:
        _HEARTBEAT.parent.mkdir(exist_ok=True)
        _HEARTBEAT.write_text(str(time.time()))
    except OSError as e:
        logging.getLogger("reflections").warning("heartbeat write failed: %s", e)

async def _run(dry_run: bool) -> None:
    scheduler = ReflectionScheduler()
    if dry_run:
        scheduler.load(); print(scheduler.format_status()); return
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
change Label to `__SERVICE_LABEL__` (→ `com.valor.reflection-worker`),
`ProgramArguments` to `[.venv/bin/python, -m, reflections]`, keep `RunAtLoad`,
`KeepAlive=true`, `ThrottleInterval` (use the worker's 10 or a longer value, e.g. 30 —
provisional, commented as tunable), set `VALOR_LAUNCHD=1`, redirect logs to
`logs/reflection_worker.log` / `_error.log`.

**4. `scripts/install_reflection_worker.sh` (new).** Structure:
- Header + `set -euo pipefail` + `SCRIPT_DIR`/`PROJECT_DIR` + source `.env` +
  `SERVICE_LABEL_PREFIX` (copy from `install_sdlc_reflection.sh:1-18`).
- **`has_bridge_role()` gate** copied verbatim from `install_nightly_tests.sh:20-72`
  (self-skip + `rm -f` stale plist on non-bridge machines).
- **Config prep (moved from `install_worker.sh:47-62`):** `_copy_config_file
  "$HOME/Desktop/Valor/reflections.yaml" "$PROJECT_DIR/config/reflections.yaml"`, then
  run `tools.reflection_machine_filter` against the copied yaml + `projects.json`.
  (This is the MOVE — delete the same block from `install_worker.sh`; the worker no
  longer runs reflections so it no longer needs the reflections.yaml copy.)
- Verify subprocess starts: `.venv/bin/python -m reflections --dry-run`.
- bootout existing → sed path-substitution into `$PLIST_DST` → (env-injection like
  `install_worker.sh:100-167` if the plist doesn't source `.env`) → `plutil -lint` →
  `launchctl bootstrap`.

**5. Wire into `scripts/update/`.**
- `scripts/update/service.py`: add `install_reflection_worker(project_dir)` modeled on
  `install_nightly_tests()` (`:360-388`) — locate `scripts/install_reflection_worker.sh`,
  run it, log rc; the shell script self-gates so the Python wrapper stays dumb.
- `scripts/update/run.py`: call `service.install_reflection_worker(project_dir)`
  **after** the worker install/restart block (ends ~`:1443`) and near the nightly-tests
  block (`:1447-1450`), under the existing `if has_bridge:` guard (defense-in-depth
  with the script's own gate). Placing it AFTER the worker restart guarantees the
  worker (new code, no scheduler) is up before the subprocess starts — see Cutover.

**6. Cutover ordering (avoid double/zero scheduler).**
- **Ordering rule:** worker restart FIRST (new worker has no in-process scheduler),
  THEN `install_reflection_worker.sh` bootstraps the plist (`RunAtLoad` starts the
  subprocess). This yields at most a **brief zero-scheduler window** (seconds, while
  the plist loads) — reflections are periodic (tick interval ≥ 60s, job intervals
  hours/days), so a few-seconds gap is harmless.
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

**7. Operator surface (heartbeat → dashboard).**
- `data/last_reflection_tick` written every tick (step 1).
- `ui/app.py`: add `_get_reflection_scheduler_health()` mirroring
  `_get_worker_health()` (`:341`) — read the file mtime, compute age, flag `running`
  if age < 2× `SCHEDULER_TICK_INTERVAL` (stale threshold). Add a `reflection_scheduler`
  block to the `/dashboard.json` payload (`:514-541`). Additive field only — the rest
  of the dashboard contract is unchanged.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/__main__.py` heartbeat write failure (read-only `data/`, OSError)
  must NOT crash the tick loop — it logs a WARNING and the scheduler keeps ticking.
  Test: patch `Path.write_text` to raise, assert the tick still returns and a WARNING
  is captured.
- [ ] SIGTERM during a tick shuts the process down cleanly (cancel + await the
  scheduler task, no traceback) so launchd `KeepAlive` restarts are graceful. Test:
  drive `_run`, fire the SIGTERM handler, assert clean return.
- [ ] `has_bridge_role()` fails OPEN on unreadable `projects.json` / missing venv /
  `scutil` error (installs rather than silently skipping) — mirrors the nightly-tests
  contract. Test the shell gate via a `projects.json` fixture matrix (bridge machine →
  install; non-bridge → skip + remove stale plist; missing config → fail-open install).

### Empty/Invalid Input Handling
- [ ] `python -m reflections --dry-run` with an empty/absent `config/reflections.yaml`
  loads zero entries, prints status, exits 0 (never bootstraps a broken plist). Test.
- [ ] Reflection registry with a disabled/invalid entry is skipped by the existing
  `load_registry` validation (unchanged) — subprocess start is unaffected. Covered by
  existing `test_reflection_scheduler.py`; add a smoke test that `python -m reflections
  --dry-run` exits 0 against the in-repo fallback yaml.

### Error State Rendering
- [ ] A dead scheduler is VISIBLE: with a stale/absent `data/last_reflection_tick`,
  `_get_reflection_scheduler_health()` reports `running=False` with the age, and
  `/dashboard.json` shows the stale block. Test the health helper with a stale mtime.
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
  with a WARNING; SIGTERM triggers clean shutdown.
- `tests/unit/test_reflection_scheduler_health.py` — `_get_reflection_scheduler_health()`
  reports `running` for a fresh `data/last_reflection_tick` and stale/dead for an old
  or absent file; `/dashboard.json` includes the additive `reflection_scheduler` block.
- `tests/integration/test_install_reflection_worker.py` (or a shell-level fixture test)
  — `has_bridge_role()` install/skip matrix: bridge machine installs, non-bridge skips
  and removes a pre-seeded stale plist, unreadable config fails-open.

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
- **Do NOT gate on a bespoke "worker-role" check** — reuse `has_bridge_role()` (the
  established, tested precedent). See Open Question 1 for the behavior nuance.

## Risks

### Risk 1: Zero-scheduler window during cutover
**Impact:** Between the worker restart (new code, no in-process scheduler) and the
plist bootstrap, no scheduler ticks.
**Mitigation:** The window is seconds; reflection tick interval is ≥60s and job
intervals are hours/days, so no reflection misses its due window. Ordering
(worker-restart-then-install) is deterministic in `run.py`. Acceptable by design.

### Risk 2: Transient double-scheduler if ordering slips
**Impact:** Two schedulers tick simultaneously → a reflection could be enqueued twice.
**Mitigation:** `is_reflection_running(state)` status-skip + single-Redis-record
source of truth + `reap_stale_running()` make double-enqueue idempotent; the second
tick finds `running` and skips. Deterministic ordering in `run.py` avoids the overlap
in the first place; the idempotency is defense-in-depth.

### Risk 3: Dev-only worker machines lose reflections under the bridge-role gate
**Impact:** A machine that runs the worker but has NO `telegram`-configured project
(a dev workstation) currently runs the in-process scheduler; after the split,
`has_bridge_role()` skips the reflection subprocess there → those machines stop
running reflections.
**Mitigation:** This matches the issue's explicit "only bridge machines that run the
worker" language and concentrates maintenance reflections on the canonical bridge
machine (single-machine-ownership already scopes project audits via
`reflection_machine_filter`). Flagged as **Open Question 1** for PM confirmation. If
"preserve exact current behavior" is required, swap the gate for a worker-role check
(machine has any assigned project) — a one-function change, noted in OQ1.

### Risk 4: Subprocess can't read config under launchd (TCC)
**Impact:** launchd agents can't read `~/Desktop` iCloud files; a naive read of the
vault `reflections.yaml`/`.env` hangs.
**Mitigation:** Set `VALOR_LAUNCHD=1` in the plist (skips the vault path,
`reflection_scheduler.py:90`), rely on the local `config/reflections.yaml` the
installer copies, and source `.env` in the plist `ProgramArguments` (or env-inject
like `install_worker.sh`) so Redis/GitHub creds are present without a runtime iCloud read.

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
**Trigger:** two bridge machines both qualify via `has_bridge_role()`.
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
- **[ORDERED] Tuning `ThrottleInterval` / the heartbeat stale-threshold** to
  production-observed values — ships conservative, tuned after observing restart rates
  on the live bridge machine (same posture as #1815's threshold tuning).
- **[SEPARATE] Cross-machine reflection failover / warm standby** — out of scope;
  single-machine-ownership stands.

## Update System

**This is the central complexity of the plan.** The reflection subprocess is a new
launchd service that must be installed on the right machines, self-skip on the wrong
ones, and be wired into the multi-machine `/update` flow (`scripts/remote-update.sh`
→ `scripts/update/run.py`).

**New installer, role-gated + self-healing (mirrors nightly-tests exactly):**
- `scripts/install_reflection_worker.sh` contains a `has_bridge_role()` gate copied
  verbatim from `scripts/install_nightly_tests.sh:20-72`. On a machine with no
  `telegram`-configured owned project it: prints "Skipping reflection-worker install",
  and if a stale `com.valor.reflection-worker.plist` exists in
  `~/Library/LaunchAgents/`, `launchctl bootout` + `rm -f` it, then `exit 0`. This is
  the **self-skip + stale-plist-removal** contract that keeps non-bridge machines
  clean when a machine changes role.
- Fail-open: unreadable `projects.json`, missing venv, or `scutil` error → install
  (matches nightly-tests, so a config hiccup never silently drops reflections on the
  real bridge machine).

**Wiring into `scripts/update/run.py`:**
- Add `scripts/update/service.py::install_reflection_worker(project_dir)` modeled on
  `install_nightly_tests()` (`:360-388`): resolve `scripts/install_reflection_worker.sh`,
  run it, log rc; the shell script owns the gate so the Python wrapper is dumb.
- Call it in `run.py` from the service-restart branch, **after** the worker
  install/restart block (~`:1341-1443`) and adjacent to the nightly-tests install
  (`:1447-1450`), under the existing `if has_bridge:` guard (defense-in-depth over the
  script's own gate). **Placement after the worker restart is load-bearing for
  cutover ordering** (worker-first → at most a brief zero-scheduler window, never
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

**Migration for existing installations:** on the next `/update` on the (single) live
bridge machine, the new worker code (no in-process scheduler) lands, the worker
restarts, then `install_reflection_worker.sh` bootstraps the new plist. No manual
step. On any non-bridge machine the installer self-skips. No stale
`com.valor.reflection-worker.plist` can exist yet (new label), so no cleanup needed
on first deploy; the stale-removal path is for future role changes.

**Logs:** add `logs/reflection_worker.log` / `logs/reflection_worker_error.log` to the
same `logs/` dir the log-rotate plist already covers (verify the glob covers the new
files; if it enumerates explicitly, add them).

**No `.env` secret additions required** — the subprocess uses the existing Redis /
GitHub creds sourced from `.env` (or env-injected) exactly as the worker does.
`SCHEDULER_TICK_INTERVAL` and any new stale-threshold constant are optional with safe
defaults; if surfaced as env, add to `.env.example` with a comment line above each
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
  `has_bridge_role()` install gate + self-skip, the cutover ordering
  (worker-restart-then-install; zero-window-not-double), the `data/last_reflection_tick`
  heartbeat + `/dashboard.json` `reflection_scheduler` surface, and the moved
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
- [ ] Comment the `VALOR_LAUNCHD=1` requirement + `data/last_reflection_tick`
  heartbeat rationale in `reflections/__main__.py`.
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
- [ ] **Role-gated + self-healing install:** `grep -c "has_bridge_role"
  scripts/install_reflection_worker.sh > 0` and the script bootout+rm's a stale plist
  on a non-bridge machine (test).
- [ ] **Wired into /update:** `grep -c "install_reflection_worker"
  scripts/update/service.py > 0` and `grep -c "install_reflection_worker"
  scripts/update/run.py > 0`, called after the worker install block.
- [ ] **Config ownership moved (no duplicate):** the `reflections.yaml` copy +
  `reflection_machine_filter` block is present in `install_reflection_worker.sh` and
  removed from `install_worker.sh` — `grep -c "reflection_machine_filter"
  scripts/install_worker.sh == 0` and `> 0` in the new installer.
- [ ] **Operator-visible heartbeat:** `grep -c "last_reflection_tick"
  reflections/__main__.py > 0` and `grep -c "reflection_scheduler" ui/app.py > 0`
  (dashboard block), so a dead/crash-looping scheduler is visible via
  `localhost:8500/dashboard.json`.
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
- **Validator** — Name: refl-validator; Role: verify success criteria + failure-path +
  cutover-ordering + role-gate matrix; Agent Type: validator; Resume: true.
- **Documentarian** — Name: refl-doc; Role: feature doc + README index + CLAUDE.md
  service table + forward-links; Agent Type: documentarian; Resume: true.

## Step by Step Tasks

### 1. Create the subprocess entry + heartbeat
- **Task ID**: build-subprocess
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_main.py (create)
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/__main__.py` (`python -m reflections`) reusing
  `ReflectionScheduler` verbatim: `--dry-run` (load + `format_status` + exit 0), clean
  SIGTERM/SIGINT shutdown, and a `data/last_reflection_tick` heartbeat written each
  tick (wrap `scheduler.tick` in `__main__`, do NOT modify the class). Heartbeat
  OSError is logged, never fatal.

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

### 3. Create the plist + role-gated installer + move config prep
- **Task ID**: build-install
- **Depends On**: build-subprocess
- **Validates**: tests/integration/test_install_reflection_worker.py (create)
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `com.valor.reflection-worker.plist` from `com.valor.worker.plist`
  (`KeepAlive=true` + `ThrottleInterval`, `VALOR_LAUNCHD=1`, `-m reflections`, logs to
  `logs/reflection_worker*.log`).
- Create `scripts/install_reflection_worker.sh` from `install_sdlc_reflection.sh` +
  the `has_bridge_role()` gate (verbatim from `install_nightly_tests.sh`) +
  `-m reflections --dry-run` verify + bootout/bootstrap.
- MOVE the `reflections.yaml` copy + `reflection_machine_filter` block from
  `install_worker.sh:47-62` into the new installer; delete it from `install_worker.sh`.

### 4. Wire into /update
- **Task ID**: wire-update
- **Depends On**: build-install
- **Assigned To**: refl-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `service.install_reflection_worker()` (modeled on `install_nightly_tests`) and
  call it in `run.py` **after** the worker install/restart block, under `if has_bridge:`.
  Comment the worker-first cutover-ordering rule at the call site.

### 5. Dashboard heartbeat surface
- **Task ID**: build-dashboard
- **Depends On**: build-subprocess
- **Validates**: tests/unit/test_reflection_scheduler_health.py (create)
- **Assigned To**: dash-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_get_reflection_scheduler_health()` (mirror `_get_worker_health()`,
  `ui/app.py:341`) reading `data/last_reflection_tick`; add an additive
  `reflection_scheduler` block to `/dashboard.json` (`:514-541`).

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: delete-worker-block, wire-update, build-dashboard
- **Assigned To**: refl-validator
- **Agent Type**: validator
- **Parallel**: false
- Run new + existing tests; verify every Success Criteria grep; verify the role-gate
  install/skip matrix and the worker-first cutover ordering.

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
| Role gate present | `grep -c "has_bridge_role" scripts/install_reflection_worker.sh` | > 0 |
| Stale-plist self-removal present | `grep -c "rm -f" scripts/install_reflection_worker.sh` | > 0 |
| Update service wrapper | `grep -c "install_reflection_worker" scripts/update/service.py` | > 0 |
| Update run.py call | `grep -c "install_reflection_worker" scripts/update/run.py` | > 0 |
| Config prep moved OUT of worker installer | `grep -c "reflection_machine_filter" scripts/install_worker.sh` | 0 |
| Config prep moved INTO reflection installer | `grep -c "reflection_machine_filter" scripts/install_reflection_worker.sh` | > 0 |
| Heartbeat written | `grep -c "last_reflection_tick" reflections/__main__.py` | > 0 |
| Dashboard surface | `grep -c "reflection_scheduler" ui/app.py` | > 0 |
| Lint clean | `python -m ruff check reflections/ worker/ ui/` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ worker/ ui/` | exit code 0 |
| No Popoto migration added | `git diff --name-only main -- scripts/update/migrations.py \| wc -l` | 0 |

## Open Questions

1. **Bridge-role vs worker-role gate.** The issue says "only bridge machines that run
   the worker run the reflection subprocess" and "follow the existing bridge-role
   launchd gating pattern," so the plan uses `has_bridge_role()`. But a dev workstation
   runs the worker WITHOUT a `telegram` project (per `CLAUDE.md`: "Dev workstations run
   [the worker] instead of the bridge"), so under this gate those machines stop running
   reflections that the in-process scheduler runs today (Risk 3). **Confirm** that
   concentrating reflections on bridge machines is intended (single-machine-ownership
   already scopes project audits). If exact behavior preservation is required, swap the
   gate for a worker-role check (machine has any assigned project) — a one-function
   change isolated to `install_reflection_worker.sh`.
2. **Plist creds delivery — source `.env` vs env-inject.** The sdlc-reflection plist
   sources `.env` in `ProgramArguments`; the worker plist env-injects at install time
   (TCC-safe). Both work under `VALOR_LAUNCHD=1`. Recommend sourcing `.env` (simpler,
   matches sdlc-reflection) unless the builder finds a launchd `.env`-read hang — then
   fall back to env-injection. Confirm the simpler path is acceptable.
