---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-06-30
tracking: https://github.com/yudame/ai/issues/1816
last_comment_id:
---

# Worker Fault Containment

## Problem

The worker runs **six long-lived asyncio tasks on one event loop** (`worker/__main__.py`), plus per-project worker loops and an off-loop heartbeat thread. A synchronous freeze in any one callback freezes all of them, and the done-callbacks **only log** on task death — there is **no respawn**. A silently-dead `session-health-monitor` leaves the worker blind until a full process restart. Three named hazards compound this:

1. **ollama hard-gate** — `worker/__main__.py:638` exits the whole worker (`sys.exit(1)`) when `ensure_granite_model()` fails, even though granite has **no runtime routing role** (PM/Dev TUIs run on the Claude OAuth subscription; `classify_pm_prefix` is pure regex). A slow or restarting ollama daemon kills everything.
2. **Machine-wide pkill** — `agent/granite_container/container.py:769` runs `pkill -f "claude --permission-mode bypassPermissions"`, which the code's own comment admits matches the operator's personal interactive `claude` and other pool slots.
3. **Reflections share the default thread pool** — `agent/reflection_scheduler.py:442` runs sync reflections via `run_in_executor(None, …)`; the `wait_for` timeout cannot cancel the thread. Enough wedged reflections saturate the shared pool and starve any critical-path `run_in_executor(None, …)`. Several reflections also do heavy synchronous Redis scans on the loop.

**Current behavior:** One component's freeze or crash silently degrades or halts the entire worker; teardown can kill bystander processes; a dependency with no runtime role can prevent startup.

**Desired outcome:** Every concern is a bulkhead. ollama down → only granite-dependent sessions pause (everything else serves). Teardown kills only the target container's process group. A wedged reflection cannot starve the critical path. A crashed monitor respawns with backoff; a restart storm escalates to a clean launchd recycle. Scope guardrail: **no VM/OS-sandbox isolation** — every fix is an in-process/in-machine bulkhead, circuit breaker, precise-targeting, or graceful-degradation change.

## Freshness Check

**Baseline commit:** 4a66f506d245e4892440bec0973c65d527e413b4
**Issue filed at:** 2026-06-29T09:22:00Z
**Disposition:** Minor drift — claims all hold; issue line numbers predate recent edits. Corrected numbers below and recorded in the issue's Recon Summary.

**File:line references re-verified (against baseline):**
- `worker/__main__.py` six `create_task` sites — issue said 537–630; **now 691 (loop-tick), 717 (session-health-monitor), 734 (session-tool-timeout-monitor), 753 (reflection-scheduler), 770 (session-notify-listener), 788 (idle-sweeper)**. Every `add_done_callback` only logs `t.exception()` — no respawn confirmed.
- ollama hard-gate — issue said 476–486; **now `ensure_granite_model()` → `sys.exit(1)` at 638** (Step 4b.5). Still holds.
- `agent/granite_container/container.py` pkill — issue said 757–775; **now `_run_pkill_fallback` at 757, `pkill` at 769, callsites at 1080, 1522, 1958**. Still holds.
- `agent/reflection_scheduler.py` — `run_in_executor(None, …)` **at 442** (issue said 434); detection-only `wait_for` **at 485/492** (issue said 474–475). Still holds.
- `agent/granite_container/pty_driver.py` — setsid-via-`pty.fork()` confirmed at **386–388**; `PTYDriver.pid` property exists at **611–612**. Fix #2 is viable.
- `reflections/audits/redis_quality_audit.py` — on-loop scans confirmed at **39, 53, 68 (`*.query.all()`)** and **114 (`[:10000]`)** (issue said 23).

**Cited sibling issues/PRs re-checked:** none cited by number (the issue references external precedent: omnigent, jcode — not repo issues).

**Commits on main since issue was filed:** baseline is one day newer than the issue; mostly line drift, no semantic change to the hazards. One commit is load-bearing for Fix #4: `657ac2be` (#1815, "liveness-wedge recovery") added an off-loop dead-man's-switch — `worker/__main__.py::_self_kill()` (SIGABRT → launchd respawn), the `WORKER_DEADMAN_*` constants, a `loop-tick` beacon task (`agent/session_state.py::bump_loop_tick`), and bounded PTY-pool waits. **Fix #4 must reuse this existing seam, not reimplement it:** the deadman only fires on a *synchronous loop freeze* (stale beacon); it does NOT catch a background task that dies silently while the loop keeps ticking — that gap is exactly what Fix #4's per-task `supervise()` respawn closes. The "restart-storm → launchd recycle" escalation should funnel through `_self_kill()` (or a sibling guarded by `WORKER_DEADMAN_ENABLED`) rather than a bare `sys.exit`, so both paths share one recycle mechanism and kill switch.

**Active plans in `docs/plans/` overlapping this area:**
- `gemma4_ollama_consolidation.md` (status: Ready) — consolidates ollama model usage (granite classifier + ollama cloud generation). **Adjacent, not conflicting**: that plan is about *which models* run on ollama; this plan is about *fault containment* when ollama is unavailable. Fix #1/#6 should respect its model-routing decisions but does not change them. Coordinate, don't merge.

**Notes:** Line drift only; the plan's premises are intact.

## Prior Art

No prior issues/PRs found for "fault containment / supervisor respawn / circuit breaker ollama / machine-wide pkill". This is greenfield resilience hardening. Reusable in-repo patterns:

- **Circuit-breaker / degraded-state precedent**: `agent/sustainability.py`, `agent/sdk_client.py`, `agent/session_pickup.py`, and the `paused_circuit` lifecycle state in `models/session_lifecycle.py` already model "open the circuit, defer pickup, re-probe". Fix #1/#6 should mirror these rather than invent a new shape.
- **PID-targeted teardown precedent**: `agent/granite_container/pty_pool.py::_kill_orphaned_pty_pids` (called at `worker/__main__.py` Step 4b) already kills PTYs by recorded PID, deliberately avoiding `pkill -f`. Fix #2 extends the same philosophy to the live-teardown path.
- **launchd install precedent**: `scripts/install_sdlc_reflection.sh` + `com.valor.sdlc-reflection.plist` and `scripts/install_worker.sh` are the templates for fix #5's `install_reflection_worker.sh` + `com.valor.reflection-worker.plist`.

## Research

No relevant external findings needed — purely internal. Every mechanism (asyncio task supervision, `concurrent.futures.ThreadPoolExecutor`, `os.killpg`/`os.getpgid`, `signal.SIGTERM`/`SIGKILL`, launchd `KeepAlive`/`ThrottleInterval`) is Python stdlib or OS-level and already used elsewhere in this repo. Proceeding with codebase context and the resilience canon (bulkheads, circuit breakers, supervised restart with backoff) cited in the issue.

## Data Flow

How a fault propagates today, and where each fix interrupts it:

1. **Entry point**: `worker/__main__.py::_run_worker` boots → Step 4b.5 `ensure_granite_model()` gate → six `create_task` background tasks + per-project loops on one loop. **Fix #1** turns the gate into a non-fatal `GRANITE_AVAILABLE` flag.
2. **Background task freezes/crashes**: a sync block inside any task body (or a wedged `run_in_executor(None, …)`) stalls the shared loop/pool. Done-callback logs but does not respawn. **Fix #3** moves reflection sync work to a dedicated bounded pool; **Fix #4** wraps the spawn sites in a `supervise()` helper that respawns with backoff.
3. **Reflection scheduler** (`reflection-scheduler` task → `run_reflection` → `execute_function_reflection` → `run_in_executor(None, call)`): heavy Redis scans run on-loop or saturate the default pool. **Fix #3** (bulkhead pool) contains the saturation; **Fix #5** relocates the entire scheduler to a supervised subprocess communicating via Redis, removing 31 jobs of coupling from the hot loop.
4. **Session teardown** (`container.py` → `_run_pkill_fallback`): broad `pkill` kills bystanders. **Fix #2** replaces it with `os.killpg(os.getpgid(self._pm_pty.pid), SIGTERM→SIGKILL)` scoped to this container's two PTYs.
5. **Runtime granite call** (`granite_classifier.py` probe/call): a mid-session ollama wedge causes cascading 60s stalls. **Fix #6** wraps the call path in a circuit breaker that fast-fails after N timeouts and falls back to degraded routing for a cooldown.
6. **Output**: worker continues serving non-granite sessions throughout; granite-dependent work pauses and auto-resumes when ollama returns.

## Architectural Impact

- **New dependencies**: none external. New internal modules: a `supervise()` helper in `worker/`, a dedicated `ThreadPoolExecutor` owned by the reflection scheduler, a `GRANITE_AVAILABLE` module flag + re-probe coroutine, and (fix #5) a new `reflections/__main__.py` entry point + launchd plist.
- **Interface changes**: `_run_pkill_fallback()` is deleted and replaced by a process-group kill on the container's PTYs (internal). Session pickup gains a `GRANITE_AVAILABLE` check before claiming granite-routed (PM/Dev SDLC) work.
- **Coupling**: **decreases**. Fix #5 is the biggest decoupling win — it removes 31 reflection jobs from the worker's hot loop entirely. Fixes #1/#3/#4 reduce blast radius within the single process.
- **Data ownership**: fix #5 moves reflection scheduling state ownership to a separate process; it already communicates via Redis (`Reflection` records), so no new shared-state surface is introduced.
- **Reversibility**: high for #1–#4 (flag flips, helper wrappers, pool swap, kill-call swap). Fix #5 is the most involved to revert (delete plist + entry point, restore the `create_task` at line 753) but is cleanly bounded.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (staging/sequencing alignment; #5 go/no-go after #1–#4 land)
- Review rounds: 2+ (concurrency-sensitive; teardown blast-radius test is load-bearing)

This is six fixes totaling ~6–8 dev-days. It is explicitly **staged** (see Step by Step Tasks): the issue's suggested order is 1,2,3 → 4 → 6 → 5. Each stage is independently shippable and de-risks the next.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| ollama present (for #1/#6 manual verification) | `command -v ollama` | Exercise the graceful-degradation and breaker paths locally |
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Reflection subprocess (#5) communicates via Redis |
| launchd available (macOS) | `command -v launchctl` | Install the reflection-worker plist (#5) |

Run via `python scripts/check_prerequisites.py docs/plans/worker-fault-containment.md`.

## Solution

### Key Elements

- **`GRANITE_AVAILABLE` flag + re-probe (Fix #1)**: A module-level flag in `worker/__main__.py` (default false until the startup probe passes). Startup no longer exits on ollama failure; instead it boots, logs degraded mode, and starts a re-probe coroutine that flips the flag when ollama returns. Session pickup defers granite-routed (PM/Dev SDLC) work while the flag is false; everything else serves normally.
- **Process-group teardown (Fix #2)**: Delete `_run_pkill_fallback`. Replace its three callsites with a kill scoped to the container's own PTY process groups via `os.killpg(os.getpgid(pty.pid), SIGTERM)` then `SIGKILL` after a grace window, for each of `self._pm_pty` / `self._dev_pty`.
- **Bulkhead reflection pool (Fix #3)**: The reflection scheduler owns a dedicated `ThreadPoolExecutor(max_workers=REFLECTION_POOL_WORKERS)` (default 2, env-overridable, marked provisional). `execute_function_reflection` uses `run_in_executor(reflection_pool, call)` instead of the shared default pool.
- **Background-task supervisor (Fix #4)**: A `supervise(name, factory)` helper wraps each `create_task` site. On non-cancelled task death it respawns via `factory()` after an exponential backoff, capped at K restarts within a window → controlled recycle via the existing `_self_kill()` seam from #1815 (SIGABRT → launchd respawn, gated by `WORKER_DEADMAN_ENABLED`), NOT a bare `sys.exit`, so the per-task respawn layer and the loop-freeze deadman share one recycle path and one kill switch. This closes the complementary gap the deadman cannot see: a silently-dead monitor while the loop still ticks.
- **Runtime ollama circuit breaker (Fix #6)**: Wrap the `granite_classifier.py` probe/call path in a breaker (open after N consecutive timeouts → fast-fail + degraded-routing fallback for a cooldown, then half-open re-probe). Shares the degraded-state vocabulary with Fix #1.
- **Reflection subprocess (Fix #5)**: New `reflections/__main__.py` runs the 31-job scheduler in its own `setsid` child / launchd service (`com.valor.reflection-worker.plist`), communicating via Redis. Delete the `create_task` at `worker/__main__.py:753`. The worker loop then hosts only session execution + liveness monitors.

### Flow

Worker boot → granite probe (non-fatal) → set `GRANITE_AVAILABLE` → start supervised background tasks → start re-probe coroutine → serve sessions (granite-routed deferred while flag false) → on ollama return, flag flips and deferred work resumes; on task death, supervisor respawns with backoff; on teardown, only this container's pgroup is killed.

### Technical Approach

- **Fix #1**: Convert Step 4b.5 (`worker/__main__.py:620-638`) from `sys.exit(1)` to setting `GRANITE_AVAILABLE = False` + a warning. Add a `_granite_reprobe_loop` background task (itself supervised by #4) that calls `ensure_granite_model()` on a timer and flips the flag. Gate granite pickup in session-pickup logic (the path that claims PM/Dev SDLC sessions) on `GRANITE_AVAILABLE`. Reuse the `paused_circuit` / deferred-pickup vocabulary from `models/session_lifecycle.py`.
- **Fix #2**: Use `PTYDriver.pid` (already exists, `pty_driver.py:611`). Each PTY child is a session leader (pexpect `pty.fork()` → `setsid`, `pty_driver.py:386-388`), so `os.getpgid(pid)` returns the child's own pgid. Kill order: SIGTERM, wait grace, SIGKILL. Wrap in try/except for already-dead pids (`ProcessLookupError`).
- **Fix #3**: Define the executor at module scope in `reflection_scheduler.py`; size from `REFLECTION_POOL_WORKERS` env (default 2 — provisional, tunable). Pass it as the first arg to `run_in_executor`. Note: this contains pool *saturation* but does not make sync reflections cancellable — that is a known limitation (the `wait_for` is still detection-only); Fix #5 is the structural answer for freeze isolation.
- **Fix #4**: `supervise(name, factory, *, max_restarts, window_s, base_backoff_s)` returns the initial task and installs a done-callback that, on unexpected death, schedules `factory()` after backoff. Track restart timestamps; exceed K-in-window → `logger.critical` + `sys.exit(1)` (launchd respawns after `ThrottleInterval`). Apply to all five non-loop-tick spawn sites (and the #1 re-probe loop).
- **Fix #6**: A small breaker around `granite_classifier`'s probe/call (consecutive-timeout counter, open/half-open/closed states, cooldown). On open, callers fast-fail to degraded routing rather than blocking 60s. Coordinate the breaker's "ollama is down" signal with Fix #1's flag so they don't fight (single source of truth: the breaker can drive `GRANITE_AVAILABLE`).
- **Fix #5**: `reflections/__main__.py` reuses `ReflectionScheduler`; the worker stops constructing it. Communication is already via Redis `Reflection` records, so no new IPC. `install_reflection_worker.sh` + `com.valor.reflection-worker.plist` modeled on the sdlc-reflection pair, with `KeepAlive=true` + `ThrottleInterval`. Wire the installer into `scripts/update/run.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new process-group kill (#2) wraps `os.killpg` in try/except for `ProcessLookupError`/`PermissionError` — test asserts a missing pid is swallowed with a `logger.debug`/`warning`, not raised.
- [ ] The `supervise()` respawn (#4) must log every respawn (`logger.warning` with task name + restart count) and the storm-cap exit (`logger.critical`) — tests assert these are emitted, not silent.
- [ ] The re-probe loop (#1) and breaker (#6) must log state transitions (degraded→available, open→half-open→closed) — assert observable log/metric on each flip.
- [ ] Existing `except Exception: pass` in `_run_pkill_fallback` is **deleted with the method** — no swallow remains.

### Empty/Invalid Input Handling
- [ ] `os.getpgid(None)` / dead pid (#2): test the path where `pty.pid` is `None` (never spawned) — must skip the kill, not crash.
- [ ] `supervise()` with a `factory()` that itself raises immediately (#4): must count as a restart and not spin-loop without backoff.
- [ ] Reflection pool (#3) with `max_workers` env set to invalid/zero: clamp to a sane minimum (≥1) rather than crash.

### Error State Rendering
- [ ] Degraded-mode startup (#1) must surface a clear log line and (if surfaced) a dashboard/health signal that granite work is paused — not a silent skip. Verify a granite-routed session enqueued during degraded mode is visibly *deferred*, not dropped or failed.

## Test Impact

- [ ] `tests/` — search for tests asserting the ollama hard-gate `sys.exit(1)` on `ensure_granite_model` failure — UPDATE: assert degraded-boot (flag false, no exit) instead. (grep `ensure_granite_model` in tests/ during build.)
- [ ] `tests/` — any test referencing `_run_pkill_fallback` or `pkill` teardown — REPLACE: rewrite against the process-group kill (assert only the target pgid is signalled). (grep `_run_pkill_fallback`/`pkill` in tests/.)
- [ ] `tests/` — reflection scheduler tests touching `run_in_executor`/default pool — UPDATE: assert the dedicated executor is used; add a saturation test (N wedged reflections do not block a critical-path executor call).
- [ ] New tests required (greenfield): supervisor respawn-with-backoff + storm-cap; circuit-breaker open/half-open/closed; process-group teardown bystander-survival; degraded-boot pickup deferral; (#5) reflection-subprocess freeze isolation.

If a build-time grep finds none of the above in `tests/`, the dispositions collapse to "new tests only" — but the grep MUST run first; do not assume.

## Rabbit Holes

- **Making sync reflections cancellable in-process.** `wait_for` cannot kill a running thread; chasing true mid-flight cancellation (signals into worker threads, `ctypes` async-exc) is a tarpit. The bulkhead pool (#3) + subprocess relocation (#5) are the sanctioned answers — contain and relocate, don't try to cancel.
- **A general-purpose supervision framework.** Resist building a generic actor/supervisor library. `supervise(name, factory)` is a ~40-line helper for five known sites — keep it that small.
- **VM/OS sandbox isolation.** Explicitly out of scope per the issue's guardrail. The MacBook Air is the trust boundary by design.
- **Rewriting the granite PTY pool lifecycle.** Fix #2 only swaps the teardown *kill mechanism*; do not refactor pool acquire/release or PTY spawn.
- **Over-tuning pool sizes / backoff constants.** Pick provisional, env-overridable defaults with a grain-of-salt comment; do not run a tuning study.

## Risks

### Risk 1: Process-group kill signals the wrong group
**Impact:** Killing the worker's own process group (or a pool sibling) would be worse than the bug it fixes.
**Mitigation:** Use `os.getpgid(pty.pid)` from the container's *own* `self._pm_pty`/`self._dev_pty` only. Guard each call (skip if `pid is None`). Acceptance test runs an unrelated `claude` alongside, tears down a session, and asserts the bystander survives AND the worker survives. Demonstrate the test red-states against the old `pkill` first.

### Risk 2: Degraded-mode boot drops granite work instead of deferring it
**Impact:** PM/Dev SDLC sessions enqueued while ollama is down could be silently lost or failed instead of paused.
**Mitigation:** Reuse the existing `paused_circuit`/deferred-pickup lifecycle so deferred sessions stay enqueued and visible. Test: enqueue a granite session in degraded mode → assert it is deferred (not failed/dropped) → flip flag → assert it is picked up.

### Risk 3: Supervisor respawn masks a real, persistent crash (thrash)
**Impact:** A task that always crashes would respawn forever, hiding the root cause and burning CPU.
**Mitigation:** K-restarts-in-window storm cap → controlled `sys.exit(1)` so launchd recycles the whole process (visible, throttled). Every respawn logs at WARNING with a running count; the cap exit logs CRITICAL.

### Risk 4: Fix #1 and Fix #6 fight over the "ollama is down" signal
**Impact:** Two independent degraded-state machines could oscillate (one says available, the other open).
**Mitigation:** Single source of truth — the runtime breaker (#6) drives `GRANITE_AVAILABLE` (#1); the startup probe seeds the initial state. Land #1 first; #6 plugs into the same flag.

### Risk 5: Reflection subprocess (#5) double-runs jobs or races the worker
**Impact:** If both the worker and the new subprocess construct a scheduler during rollout, reflections could run twice.
**Mitigation:** Delete the worker's `create_task` in the **same** change that adds the subprocess (no parallel-run window — per the no-parallel-migrations rule). The scheduler already coordinates via Redis `Reflection` records (single-owner claim). Ship #5 last, after #1–#4 de-risk the loop.

## Race Conditions

### Race 1: Re-probe flips GRANITE_AVAILABLE while a pickup decision is mid-flight
**Location:** `worker/__main__.py` flag + session-pickup gate (#1).
**Trigger:** Re-probe sets the flag true between a pickup's flag-read and its session claim.
**Data prerequisite:** The flag must be readable atomically (a plain module-level bool read/write is atomic under CPython's GIL).
**State prerequisite:** A deferred session must remain enqueued so a missed flip is corrected on the next pickup tick.
**Mitigation:** Idempotent deferral — the flag is advisory; the worst case is one extra pickup-loop delay before a deferred granite session is claimed. No lock needed; deferred sessions are never dropped.

### Race 2: Teardown signals a pgid that was just recycled to another process
**Location:** `container.py` process-group kill (#2).
**Trigger:** The PTY child exits and the OS reuses its pgid before `killpg` fires.
**Data prerequisite:** `pty.pid` must reference a still-live child.
**State prerequisite:** Kill only while the container owns the PTY (before `close()` nulls `_child`).
**Mitigation:** Check `pty._child.isalive()` (or catch `ProcessLookupError`) immediately before `killpg`; the window is sub-millisecond and the worst case is a no-op on a dead pid. macOS pid reuse is slow enough that this is negligible, but the liveness check closes it.

### Race 3: Supervisor respawn races process shutdown
**Location:** `supervise()` done-callback (#4).
**Trigger:** A task dies during worker shutdown; the callback respawns it just as the loop is closing.
**Data prerequisite:** A shutdown flag must be observable by the callback.
**State prerequisite:** Respawn must be suppressed once shutdown begins.
**Mitigation:** The callback checks `t.cancelled()` (already the shutdown signal in existing done-callbacks) and a module shutdown flag before respawning; cancelled → no respawn.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1816] VM/OS-sandbox isolation of the worker — explicitly excluded by the issue's scope guardrail (the dedicated MacBook Air is the trust boundary by design). Tracked conceptually under the parent resilience review, not this slug.
- Nothing else deferred — fixes #1 through #6 are all in scope for this plan, staged per the issue's suggested order. Fix #5 is gated on #1–#4 landing but remains part of this plan, not a separate issue.

## Update System

- **Fix #5 requires update-system changes**: a new launchd plist (`com.valor.reflection-worker.plist`) + installer (`scripts/install_reflection_worker.sh`). Wire the installer into `scripts/update/run.py` so every bridge machine installs/refreshes the reflection-worker service on `/update` (model it on how the sdlc-reflection / worker plists are installed). The installer must be idempotent and machine-role-gated (only bridge machines that run the worker should run the reflection subprocess).
- **Fix #1/#6 graceful-degradation rollout**: no new dependency, but `/update`'s post-restart verification should confirm the worker comes up even when ollama is unavailable (degraded boot is the new expected state, not a failure). Update any update-script health assertion that currently treats a missing granite model as a hard failure.
- **New env vars** (`REFLECTION_POOL_WORKERS`, breaker thresholds, re-probe interval, supervisor `max_restarts`/`window`) must be added to `.env.example` with a comment line above each (completeness check) and to `config/settings.py`. All defaults are provisional/tunable with grain-of-salt comments.
- No Popoto schema change (the `Reflection` model is unchanged; #5 only relocates *who* constructs the scheduler), so no `migrations.py` entry is required.

## Agent Integration

- **No new MCP tool or `.mcp.json` change.** This is worker/bridge-internal infrastructure — fault containment of the existing session-execution engine. The agent surface (Telegram → bridge → worker) is unchanged.
- **Fix #5** adds a new process entry point (`python -m reflections`) but it is a background service, not an agent-invokable tool. It communicates with the worker only via existing Redis `Reflection` records.
- **Fix #1** changes worker *behavior* the agent indirectly experiences (granite-routed SDLC work pauses when ollama is down) but exposes no new tool. Integration coverage is via the worker/integration tests listed in Test Impact, not MCP tests.
- No entry point needs adding to `pyproject.toml [project.scripts]` beyond the `python -m reflections` module form (which needs no script declaration).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/worker-fault-containment.md` describing the fault-containment model: the six bulkheaded concerns, `GRANITE_AVAILABLE` graceful degradation, process-group teardown, the reflection bulkhead pool, the `supervise()` helper, the runtime circuit breaker, and the reflection subprocess split.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/granite-pty-production.md` (or the relevant granite teardown doc) to reflect the deletion of `_run_pkill_fallback` and the new process-group kill.
- [ ] Cross-reference from `docs/features/bridge-worker-architecture.md` (the worker's background-task topology changes: reflection scheduler moves off-process).

### External Documentation Site
- [ ] Not applicable — this repo has no external Sphinx/MkDocs site for worker internals.

### Inline Documentation
- [ ] Docstring on `supervise()` documenting backoff + storm-cap semantics.
- [ ] Grain-of-salt comments on all new provisional constants (pool size, backoff, breaker thresholds, re-probe interval).
- [ ] Comment at the (now non-fatal) granite probe explaining why a missing model no longer exits.

## Success Criteria

- [ ] Worker starts and serves non-granite work when ollama is unavailable; granite-routed work is *deferred* (not dropped/failed) and resumes automatically when ollama returns. (Fix #1)
- [ ] Teardown provably cannot kill processes outside the target container's process group — bystander `claude` survives a session teardown; the worker survives. Anti-criterion: `pkill -f` is gone from `container.py`. (Fix #2)
- [ ] A wedged reflection cannot exhaust the critical-path thread pool — saturation test proves a critical-path `run_in_executor` call still completes with N reflections wedged. (Fix #3)
- [ ] A crashed background monitor task is respawned with backoff; a restart storm escalates to a clean `sys.exit`/launchd recycle. (Fix #4)
- [ ] Runtime ollama wedge fast-fails via the breaker instead of cascading 60s stalls. (Fix #6)
- [ ] Reflections run in their own supervised process; a reflection freeze does not stall session execution. (Fix #5)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_run_pkill_fallback` and the `pkill -f "claude --permission-mode"` string are absent from `agent/granite_container/container.py`.
- [ ] grep confirms `worker/__main__.py` no longer constructs `ReflectionScheduler` (after #5).

## Team Orchestration

The lead agent orchestrates; it never builds directly. Work is staged — Stage A (fixes 1,2,3) can run as three parallel builder+validator pairs; Stage B (#4), Stage C (#6), Stage D (#5) are sequential gates.

### Team Members

- **Builder (ollama-degradation)**
  - Name: granite-degrade-builder
  - Role: Fix #1 — `GRANITE_AVAILABLE` flag, non-fatal probe, re-probe loop, pickup gate
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Builder (pgroup-teardown)**
  - Name: teardown-builder
  - Role: Fix #2 — delete pkill, process-group kill on container PTYs
  - Agent Type: builder
  - Resume: true

- **Builder (reflection-bulkhead)**
  - Name: reflection-pool-builder
  - Role: Fix #3 — dedicated bounded ThreadPoolExecutor
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Builder (supervisor)**
  - Name: supervisor-builder
  - Role: Fix #4 — `supervise()` helper with backoff + storm cap, wrap five spawn sites
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Builder (ollama-breaker)**
  - Name: breaker-builder
  - Role: Fix #6 — runtime circuit breaker around granite_classifier, drives the #1 flag
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Builder (reflection-subprocess)**
  - Name: reflection-split-builder
  - Role: Fix #5 — `reflections/__main__.py`, launchd plist, installer, `/update` wiring, delete worker create_task
  - Agent Type: builder
  - Resume: true

- **Validator (fault-containment)**
  - Name: fc-validator
  - Role: Verify each fix's success criteria + the bystander-survival and saturation tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: fc-documentarian
  - Role: feature doc + index + cross-references
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Per repo standard — Tier 1 `builder`/`validator`/`documentarian`; concurrency-heavy tasks carry a `Domain: async/concurrency` line with the matching framing pasted from `DOMAIN_FRAMING.md`.

## Step by Step Tasks

### 1. Fix #1 — ollama graceful degradation
- **Task ID**: build-granite-degrade
- **Depends On**: none
- **Validates**: tests/ (degraded-boot pickup-deferral test, create); worker startup smoke
- **Assigned To**: granite-degrade-builder
- **Agent Type**: builder
- **Parallel**: true
- Convert `worker/__main__.py:620-638` from `sys.exit(1)` to `GRANITE_AVAILABLE = False` + warning.
- Add supervised `_granite_reprobe_loop` that flips the flag when `ensure_granite_model()` succeeds.
- Gate granite-routed (PM/Dev SDLC) session pickup on `GRANITE_AVAILABLE`, reusing the `paused_circuit`/deferred vocabulary.

### 2. Fix #2 — process-group teardown
- **Task ID**: build-pgroup-teardown
- **Depends On**: none
- **Validates**: tests/ (bystander-survival test, create)
- **Assigned To**: teardown-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `_run_pkill_fallback` (`container.py:757-775`).
- Replace callsites 1080, 1522, 1958 with `os.killpg(os.getpgid(pty.pid), SIGTERM)`→`SIGKILL` over `self._pm_pty`/`self._dev_pty`, guarded for dead/None pids + `isalive()` liveness check.

### 3. Fix #3 — reflection bulkhead pool
- **Task ID**: build-reflection-pool
- **Depends On**: none
- **Validates**: tests/ (pool-saturation isolation test, create)
- **Assigned To**: reflection-pool-builder
- **Agent Type**: builder
- **Parallel**: true
- Add module-scope `ThreadPoolExecutor(max_workers=REFLECTION_POOL_WORKERS, default 2)` in `reflection_scheduler.py`.
- Swap `run_in_executor(None, call)` (line 442) to use it; clamp invalid sizes to ≥1.

### 4. Validate Stage A
- **Task ID**: validate-stage-a
- **Depends On**: build-granite-degrade, build-pgroup-teardown, build-reflection-pool
- **Assigned To**: fc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the three new tests + worker smoke; confirm no `pkill -f` remains; confirm degraded boot.

### 5. Fix #4 — background-task supervisor
- **Task ID**: build-supervisor
- **Depends On**: validate-stage-a
- **Validates**: tests/ (respawn-with-backoff + storm-cap test, create)
- **Assigned To**: supervisor-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `supervise(name, factory, *, max_restarts, window_s, base_backoff_s)` in `worker/`.
- Wrap the five `create_task` sites (717, 734, 753, 770, 788) + the #1 re-probe loop; respawn on unexpected death; storm-cap → `sys.exit(1)`.

### 6. Fix #6 — runtime ollama circuit breaker
- **Task ID**: build-breaker
- **Depends On**: build-supervisor
- **Validates**: tests/ (breaker open/half-open/closed test, create)
- **Assigned To**: breaker-builder
- **Agent Type**: builder
- **Parallel**: false
- Wrap `granite_classifier.py` probe/call in a breaker (open after N timeouts → fast-fail + degraded fallback + cooldown → half-open).
- Make the breaker the single source of truth driving `GRANITE_AVAILABLE`.

### 7. Fix #5 — reflection subprocess split
- **Task ID**: build-reflection-split
- **Depends On**: build-breaker
- **Validates**: tests/ (reflection-freeze isolation test, create); `/update` dry-run
- **Assigned To**: reflection-split-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/__main__.py` running `ReflectionScheduler`; delete `create_task` at `worker/__main__.py:753` in the same change.
- Add `com.valor.reflection-worker.plist` + `scripts/install_reflection_worker.sh` (idempotent, role-gated); wire into `scripts/update/run.py`.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: build-reflection-split
- **Assigned To**: fc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-fault-containment.md`; add to `docs/features/README.md`; cross-reference granite-pty + bridge-worker docs.

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-supervisor, build-breaker, build-reflection-split, document-feature
- **Assigned To**: fc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all new tests + full success criteria; confirm grep anti-criteria; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| pkill deleted (anti-criterion) | `grep -c 'pkill' agent/granite_container/container.py` | match count == 0 |
| pkill-fallback method gone (anti-criterion) | `grep -c '_run_pkill_fallback' agent/granite_container/container.py` | match count == 0 |
| Worker no longer hard-exits on granite (anti-criterion) | `grep -A3 'ensure_granite_model' worker/__main__.py \| grep -c 'sys.exit'` | match count == 0 |
| Reflection scheduler off worker loop (anti-criterion) | `grep -c 'ReflectionScheduler()' worker/__main__.py` | match count == 0 |
| Supervisor helper present | `grep -c 'def supervise' worker/__main__.py` | output contains 1 |
| Reflection entry point present | `test -f reflections/__main__.py && echo ok` | output contains ok |
| Reflection plist + installer present | `test -f scripts/install_reflection_worker.sh && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Re-probe / breaker interval defaults.** What re-probe cadence (#1) and breaker thresholds (#6) are acceptable — e.g. re-probe every 30s, breaker opens after 3 consecutive timeouts, 60s cooldown? Provisional defaults are proposed; confirm or adjust.
2. **Supervisor storm cap.** Is K=5 restarts in a 60s window before a launchd recycle the right aggressiveness, or should a wedged monitor be tolerated longer before recycling the whole process?
3. **Fix #5 scheduling.** Confirm #5 (reflection subprocess) should ship in *this* slug after #1–#4, vs. splitting it into a follow-up once Stage A/B are in production and observed. The issue lists it as part of this plan but explicitly "scheduled once 1–4 de-risk the loop."
4. **Reflection-worker role gating.** Should the reflection subprocess run on every worker machine, or only the single designated bridge machine? (Affects the installer's machine-gate in `/update`.)
