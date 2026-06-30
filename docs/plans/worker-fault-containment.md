---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-06-30
tracking: https://github.com/yudame/ai/issues/1816
last_comment_id:
revision_applied: true
deferred_followups:
  - "#1828 — Reflection scheduler subprocess split (was Fix #5)"
---

# Worker Fault Containment

## Problem

The worker runs **six long-lived asyncio tasks on one event loop** (`worker/__main__.py`), plus per-project worker loops and an off-loop heartbeat thread. A synchronous freeze in any one callback freezes all of them, and the done-callbacks **only log** on task death — there is **no respawn**. A silently-dead `session-health-monitor` leaves the worker blind until a full process restart. Three named hazards compound this:

1. **ollama hard-gate** — `worker/__main__.py:638` exits the whole worker (`sys.exit(1)`) when `ensure_granite_model()` fails, even though granite has **no runtime routing role** (PM/Dev TUIs run on the Claude OAuth subscription; `classify_pm_prefix` is pure regex). A slow or restarting ollama daemon kills everything.
2. **Machine-wide pkill** — `agent/granite_container/container.py:769` runs `pkill -f "claude --permission-mode bypassPermissions"`, which the code's own comment admits matches the operator's personal interactive `claude` and other pool slots.
3. **Reflections share the default thread pool** — `agent/reflection_scheduler.py:442` runs sync reflections via `run_in_executor(None, …)`; the `wait_for` timeout cannot cancel the thread. Enough wedged reflections saturate the shared pool and starve any critical-path `run_in_executor(None, …)`. Several reflections also do heavy synchronous Redis scans on the loop.

**Current behavior:** One component's freeze or crash silently degrades or halts the entire worker; teardown can kill bystander processes; a dependency with no runtime role can prevent startup.

**Desired outcome:** Every concern is a bulkhead. ollama down → only granite-dependent sessions pause (everything else serves), and the startup/re-probe loop that owns the ONLY ollama call doubles as a circuit breaker (consecutive-timeout counter → open/half-open/closed) so a wedged ollama daemon cannot block boot indefinitely. Teardown kills only the target container's process group. A wedged reflection cannot starve the critical path. A crashed monitor respawns with backoff; a restart storm escalates to a clean launchd recycle via the existing `_self_kill()` SIGABRT seam. Scope guardrail: **no VM/OS-sandbox isolation** — every fix is an in-process/in-machine bulkhead, circuit breaker, precise-targeting, or graceful-degradation change.

**Slug boundary (post-critique):** This slug ships **Stages A–C only** — Fix #1 (ollama graceful degradation, now carrying the folded-in breaker logic), Fix #2 (process-group teardown), Fix #3 (reflection bulkhead pool), and Fix #4 (background-task supervisor). The former **Fix #6** (standalone runtime ollama breaker) is **folded into Fix #1** — it guarded a runtime path that does not exist (`classify_pm_prefix` is pure regex; the only ollama call is the startup/re-probe `ensure_granite_model`). The former **Fix #5** (reflection subprocess split) is **deferred to follow-up issue #1828**; after Fix #3 caps the pool and Fix #4 supervises respawn, a wedged reflection can no longer starve the critical path, so #5 is a structural decoupling win, not a prerequisite. See **## Downstream / Deferred**.

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

## Spike Results

Three read-only code spikes run at plan time to validate the riskier assumptions.

### spike-1: process-group teardown safety (Fix #2)
- **Assumption**: "`os.killpg(os.getpgid(driver.pid), …)` scoped to the container's own PTY children is a safe replacement for the machine-wide `pkill`."
- **Method**: code-read
- **Finding**: Confirmed. `PTYDriver.pid` (`pty_driver.py:612`) returns `self._child.pid`; pexpect `pty.fork()` `setsid`s each child (`pty_driver.py:386-388`) so each is its own pgid leader. All three `_run_pkill_fallback()` callsites (`container.py:1080,1522,1958`) are reachable ONLY on the self-spawned single-container path — pool-backed runs already early-return via `_uses_pool_pair()` and tear down via PID-targeted kills in `pty_pool.py`. Existing `tests/unit/granite_container/test_container_pkill_gating.py` guards the gating and must be updated for the new killpg path.
- **Confidence**: high
- **Impact on plan**: Fix #2 technical approach confirmed as-written; guard `os.killpg` for `ProcessLookupError`/`PermissionError` and `pid is None`.

### spike-2: shared default-executor saturation (Fix #3)
- **Assumption**: "Sync reflections share the process-wide default thread pool with critical-path work, so a wedged reflection can starve it."
- **Method**: code-read
- **Finding**: VALIDATED, with one correction. Default pool = `min(32, cpu+4) = 14` on this 10-core Air. Sync reflections (`run_in_executor(None, …)` at `reflection_scheduler.py:442`) share it with critical-path `bridge/routing.py:657` (message classification) and `bridge/media.py:341,387` (transcription, vision). The per-tick dispatch cap (4) does NOT bound concurrent in-flight reflections across ticks. **Correction:** `redis_quality_audit.py::run()` is `async def`, so its heavy `.query.all()` scans block the event loop directly — they are NOT covered by the executor bulkhead and need separate `asyncio.to_thread` wrapping (folded into Fix #3 scope above).
- **Confidence**: high
- **Impact on plan**: Fix #3 scope widened to cover the async-blocking audit; saturation test sized against the named critical-path victims.

### spike-3: ollama surface — is Fix #6 needed? (Fix #1/#6)
- **Assumption**: "A mid-session ollama wedge causes cascading stalls on the granite path (the issue's Fix #6 premise)."
- **Method**: code-read
- **Finding**: INVALIDATED for the granite path. Zero ollama calls on the PM/Dev TUI hot path — `classify_pm_prefix` is pure regex (`granite_classifier.py:160-250`; container.py:1316/1454/1868). The only blocking ollama interaction is the startup `ensure_granite_model()` probe (60s) / pull (900s), already wrapped by Fix #1's re-probe circuit. Other ollama callers (`bridge/routing.py`, `tools/email_cs/triage.py`, `bridge/agent_catchup.py`, knowledge indexer, memory title) are bridge/background, out of this slug.
- **Confidence**: high
- **Impact on plan**: Fix #6 is redundant with Fix #1 for the in-scope path. **CRITIQUE resolution (unanimous BLOCKER):** the standalone Fix #6 / Stage C / breaker-builder is **deleted**; the consecutive-timeout/open/half-open/closed counter logic is **folded into Fix #1's `_granite_reprobe_loop`** (the re-probe loop IS the breaker, wrapping the single `ensure_granite_model` call). The untestable "fast-fails mid-session" acceptance criterion is removed.

## Data Flow

How a fault propagates today, and where each fix interrupts it:

1. **Entry point**: `worker/__main__.py::_run_worker` boots → Step 4b.5 `ensure_granite_model()` gate → six `create_task` background tasks + per-project loops on one loop. **Fix #1** turns the gate into a non-fatal `GRANITE_AVAILABLE` flag.
2. **Background task freezes/crashes**: a sync block inside any task body (or a wedged `run_in_executor(None, …)`) stalls the shared loop/pool. Done-callback logs but does not respawn. **Fix #3** moves reflection sync work to a dedicated bounded pool; **Fix #4** wraps the spawn sites in a `supervise()` helper that respawns with backoff.
3. **Reflection scheduler** (`reflection-scheduler` task → `run_reflection` → `execute_function_reflection` → `run_in_executor(None, call)`): heavy Redis scans run on-loop or saturate the default pool. **Fix #3** (bulkhead pool) contains the saturation. (The structural relocation of the scheduler to its own subprocess — formerly Fix #5 — is **deferred to #1828**; with Fix #3 + Fix #4 in place a wedged reflection can no longer starve the critical path.)
4. **Session teardown** (`container.py` → `_run_pkill_fallback`): broad `pkill` kills bystanders. **Fix #2** replaces it with `os.killpg(os.getpgid(self._pm_pty.pid), SIGTERM→SIGKILL)` scoped to this container's two PTYs, and preserves an explicit orphan-reap path for the spawn-FAILURE callsite (where a PTY may be half-created with a `None` pid).
5. **Startup / re-probe ollama call** (`ensure_granite_model` probe, the ONLY ollama call in scope): a slow or restarting ollama daemon blocks the 60s probe. **Fix #1**'s `_granite_reprobe_loop` doubles as the circuit breaker — it counts consecutive probe timeouts (open → fast-fail to degraded routing → cooldown → half-open re-probe → closed) so neither boot nor the re-probe loop blocks indefinitely. There is no separate mid-session breaker because there is no mid-session ollama call (`classify_pm_prefix` is pure regex).
6. **Output**: worker continues serving non-granite sessions throughout; granite-dependent work pauses and auto-resumes when ollama returns.

## Architectural Impact

- **New dependencies**: none external. New internal modules: a `supervise()` helper in `worker/`, a dedicated `ThreadPoolExecutor` owned by the reflection scheduler, and a `GRANITE_AVAILABLE` module flag + re-probe/breaker coroutine. (The `reflections/__main__.py` entry point + launchd plist are **deferred to #1828**, not part of this slug.)
- **Interface changes**: `_run_pkill_fallback()` is deleted and replaced by a process-group kill on the container's PTYs (internal), with the spawn-failure callsite reaping any partially-created PTY pid. Session pickup gains a `GRANITE_AVAILABLE` check before claiming granite-routed (PM/Dev SDLC) work.
- **Coupling**: **decreases** within the single process. Fixes #1/#3/#4 reduce blast radius. (The biggest decoupling win — removing the 31 reflection jobs from the worker hot loop — lands later in #1828.)
- **Data ownership**: unchanged in this slug. (The reflection-scheduler ownership move to a separate process is deferred to #1828; it communicates via Redis `Reflection` records, so no new shared-state surface is introduced when it lands.)
- **Reversibility**: high for all four in-scope fixes (flag flips, helper wrappers, pool swap, kill-call swap).

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2 (staging/sequencing alignment)
- Review rounds: 2+ (concurrency-sensitive; teardown blast-radius test is load-bearing)

This is four in-scope fixes totaling ~4–6 dev-days. It is explicitly **staged** (see Step by Step Tasks): Stage A = Fixes #1,#2,#3 (parallel), Stage B = Fix #4 (sequential gate). Each stage is independently shippable and de-risks the next. The former Fix #6 is folded into Fix #1; the former Fix #5 is deferred to #1828.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| ollama present (for #1 manual verification) | `command -v ollama` | Exercise the graceful-degradation + folded-in breaker path locally |
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Worker session-pickup + deferred-granite lifecycle run through Redis |
| launchd available (macOS) | `command -v launchctl` | The storm-cap recycle relies on launchd respawn (KeepAlive); the reflection-worker plist install is deferred to #1828 |

Run via `python scripts/check_prerequisites.py docs/plans/worker-fault-containment.md`.

## Solution

### Key Elements

- **`GRANITE_AVAILABLE` flag + re-probe/breaker (Fix #1, with folded-in Fix #6)**: A module-level flag in `worker/__main__.py` (default false until the startup probe passes). Startup no longer exits on ollama failure; instead it boots, logs degraded mode, and starts a `_granite_reprobe_loop` coroutine that re-probes `ensure_granite_model` on a timer and flips the flag when ollama returns. **This loop IS the circuit breaker**: it carries a consecutive-timeout counter with open/half-open/closed states and a cooldown, so a wedged ollama daemon neither blocks boot nor hammers a re-probe every tick. All thresholds/intervals are NAMED, env-overridable module constants with provisional/tunable comments (`GRANITE_REPROBE_INTERVAL_S`, `GRANITE_BREAKER_OPEN_THRESHOLD`, `GRANITE_BREAKER_COOLDOWN_S`). Session pickup defers granite-routed (PM/Dev SDLC) work while the flag is false; everything else serves normally.
- **Process-group teardown (Fix #2)**: Delete `_run_pkill_fallback`. Replace its three callsites with a kill scoped to the container's own PTY process groups via `os.killpg(os.getpgid(pty.pid), SIGTERM)` then `SIGKILL` after a grace window, for each of `self._pm_pty` / `self._dev_pty`. **Preserve an explicit orphan-reap path at the spawn-FAILURE callsite (`container.py:1080`)**: when `_spawn_pair()` raises after partially creating a child, iterate BOTH PTYs, read each `.pid` (which may be `None` / half-created), and `killpg` each non-None group inside `try/except ProcessLookupError` — so deleting the pkill net does not lose the only orphan reaper on the self-spawned path.
- **Bulkhead reflection pool (Fix #3)**: The reflection scheduler owns a dedicated `ThreadPoolExecutor(max_workers=REFLECTION_POOL_WORKERS)` (default 2, env-overridable, marked provisional). `execute_function_reflection` uses `run_in_executor(reflection_pool, call)` instead of the shared default pool.
- **Background-task supervisor (Fix #4)**: A `supervise(name, factory)` helper wraps each `create_task` site. On non-cancelled task death it respawns via `factory()` after an exponential backoff, capped at a NAMED, env-overridable, conservative-provisional `WORKER_SUPERVISOR_MAX_RESTARTS` within `WORKER_SUPERVISOR_WINDOW_S` (erring toward NOT killing legitimate work). On exceeding the cap it triggers a controlled recycle via the existing `_self_kill()` seam from #1815 (SIGABRT → launchd respawn, gated by `WORKER_DEADMAN_ENABLED`); if the deadman is disabled it falls back to `os.abort()` (SIGABRT) — **never a bare `sys.exit(1)`, which a `SystemExit` raised inside an asyncio done-callback would silently swallow**. The per-task respawn layer and the loop-freeze deadman thus share one real-process-death recycle path and one kill switch. This closes the complementary gap the deadman cannot see: a silently-dead monitor while the loop still ticks.
- **(Folded) ollama circuit breaker — formerly Fix #6**: There is no standalone breaker. The "open after N consecutive timeouts → fast-fail + degraded fallback + cooldown → half-open" logic lives inside Fix #1's `_granite_reprobe_loop`, wrapping the single in-scope ollama call (`ensure_granite_model`). The premise of a separate mid-session breaker — a per-turn ollama call — does not exist (`classify_pm_prefix` is pure regex).
- **(Deferred) reflection subprocess — formerly Fix #5 → #1828**: Out of scope for this slug. Tracked in follow-up issue #1828.

### Flow

Worker boot → granite probe (non-fatal) → set `GRANITE_AVAILABLE` → start supervised background tasks → start `_granite_reprobe_loop` (which also carries the breaker state) → serve sessions (granite-routed deferred while flag false) → on ollama return, flag flips and deferred work resumes; on task death, supervisor respawns with backoff (storm cap → `_self_kill()`/`os.abort()` SIGABRT recycle); on teardown, only this container's pgroup is killed (spawn-failure path reaps any half-created PTY).

### Technical Approach

- **Fix #1 (carries the folded-in breaker)**: Convert Step 4b.5 (`worker/__main__.py:617-638`) from `sys.exit(1)` to setting `GRANITE_AVAILABLE = False` + a warning. Add a `_granite_reprobe_loop` background task (itself supervised by #4) that calls `ensure_granite_model()` on a timer and flips the flag. **The loop carries the breaker state directly**: a consecutive-timeout counter (open after `GRANITE_BREAKER_OPEN_THRESHOLD`), a cooldown (`GRANITE_BREAKER_COOLDOWN_S`) before half-open re-probe, and a base interval (`GRANITE_REPROBE_INTERVAL_S`) — all NAMED, env-overridable module constants with provisional/tunable grain-of-salt comments (no bare literals). Breaker state is process-global (the startup probe runs via `asyncio.to_thread`). Gate granite pickup in session-pickup logic (the path that claims PM/Dev SDLC sessions) on `GRANITE_AVAILABLE`. Reuse the `paused_circuit` / deferred-pickup vocabulary from `models/session_lifecycle.py`. **Reconciliation task:** correct the stale in-code comments that assert a runtime ollama routing role (see Inline Documentation) — they contradict this fix's premise.
- **Fix #2**: Use `PTYDriver.pid` (already exists, `pty_driver.py:611`). Each PTY child is a session leader (pexpect `pty.fork()` → `setsid`, `pty_driver.py:386-388`), so `os.getpgid(pid)` returns the child's own pgid. Kill order: SIGTERM, wait grace, SIGKILL. Wrap in try/except for already-dead pids (`ProcessLookupError`). **Spawn-failure orphan reap (`container.py:1080`):** `_spawn_pair()` may have set `self._pm_pty` but not `self._dev_pty` (or vice versa) before raising. At that callsite, iterate BOTH PTYs, read each `.pid` (`None` if dead/unspawned), and `killpg` each non-None group inside `try/except ProcessLookupError`. Do NOT rely on a single None-check that short-circuits both PTYs — that would leak a half-spawned `claude` orphan, exactly the leak the old pkill caught. A dedicated test ("spawn raises after partial child creation → no orphan survives") guards this.
- **Fix #3**: Define the executor at module scope in `reflection_scheduler.py`; size from `REFLECTION_POOL_WORKERS` env (default 2 — provisional, tunable). Pass it as the first arg to `run_in_executor`. Note: this contains pool *saturation* but does not make sync reflections cancellable — that is a known limitation (the `wait_for` is still detection-only); the deferred subprocess split (#1828) is the structural answer for freeze isolation. **Spike-verified scope gap:** the dedicated pool ONLY covers *sync* reflections routed through `run_in_executor`. `reflections/audits/redis_quality_audit.py::run()` is `async def` (line 23), so `inspect.iscoroutinefunction` awaits it directly — its `.query.all()` scans (lines 39/53/68/114) block the **event loop itself**, NOT the thread pool, so the bulkhead does nothing for them. Fix #3 must ALSO wrap those scans in `asyncio.to_thread(...)` (or paginate) — otherwise the "a wedged reflection cannot freeze the loop" criterion fails for this audit. The concrete critical-path victims a saturated default pool starves are `bridge/routing.py:657` (message-needs-response classification, on the ingestion path) and `bridge/media.py:341,387` (transcription, Haiku vision); the default pool is `min(32, cpu+4) = 14` on this 10-core Air — size the saturation test against those.
- **Fix #4**: `supervise(name, factory, *, max_restarts, window_s, base_backoff_s)` returns the initial task and installs a done-callback that, on unexpected death, schedules `factory()` after backoff. `max_restarts`/`window_s`/`base_backoff_s` default from NAMED, env-overridable, conservative-provisional constants (`WORKER_SUPERVISOR_MAX_RESTARTS`, `WORKER_SUPERVISOR_WINDOW_S`, `WORKER_SUPERVISOR_BASE_BACKOFF_S`) — erring toward NOT killing legitimate work. Track restart timestamps; on exceeding the cap → `logger.critical` then **`_self_kill()` (the #1815 SIGABRT seam, gated by `WORKER_DEADMAN_ENABLED`); if the deadman is disabled, `os.abort()` (also SIGABRT)**. **Never a bare `sys.exit(1)`** — a `SystemExit` raised inside an asyncio done-callback is caught by the loop's callback-exception handler and logged, not propagated, so the storm cap would silently fail to recycle. launchd (`KeepAlive`/`ThrottleInterval`) respawns after the SIGABRT. Apply to all five non-loop-tick spawn sites (and the #1 re-probe loop).
- **(Folded) breaker — formerly Fix #6**: Deleted as a standalone fix. Its consecutive-timeout/open/half-open/closed/cooldown logic now lives inside Fix #1's `_granite_reprobe_loop`, wrapping the single in-scope ollama call (`ensure_granite_model`, `granite_classifier.py:40`). **Spike-verified (unanimous CRITIQUE BLOCKER):** there is NO ollama call on the mid-session granite path — `classify_pm_prefix` is pure regex (container.py:1316/1454/1868 → zero-LLM), so the "mid-session ollama wedge → cascading 60s stalls" premise does not exist. The breaker IS the re-probe loop; there is one source of truth for "ollama is down" (`GRANITE_AVAILABLE`), so nothing can fight. (Other ollama callers — `bridge/routing.py` classify, `tools/email_cs/triage.py`, `bridge/agent_catchup.py`, knowledge indexer — are on bridge/background paths, out of this worker-core slug.)
- **(Deferred) reflection subprocess — formerly Fix #5 → #1828**: Out of scope. `reflections/__main__.py`, `com.valor.reflection-worker.plist`, `scripts/install_reflection_worker.sh`, the `scripts/update/run.py` wiring, and the deletion of the worker `create_task` are all tracked in follow-up issue #1828.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new process-group kill (#2) wraps `os.killpg` in try/except for `ProcessLookupError`/`PermissionError` — test asserts a missing pid is swallowed with a `logger.debug`/`warning`, not raised.
- [ ] The spawn-failure orphan-reap (#2, `container.py:1080`) must reap BOTH PTYs when one is half-created (`pid` set) and the other is `None` — test: "spawn raises after partial child creation → no orphan survives" asserts the partially-spawned `claude` child is killed, not leaked.
- [ ] The `supervise()` respawn (#4) must log every respawn (`logger.warning` with task name + restart count) and the storm-cap recycle (`logger.critical`) — tests assert these are emitted, not silent. **The storm-cap test must assert REAL process death** (run the worker under a subprocess and assert it exits via SIGABRT), not merely that the log line was emitted — a swallowed `SystemExit` would pass a log-only assertion while the process kept running.
- [ ] The re-probe/breaker loop (#1) must log state transitions (degraded→available, open→half-open→closed) — assert observable log/metric on each flip.
- [ ] Existing `except Exception: pass` in `_run_pkill_fallback` is **deleted with the method** — no swallow remains.

### Empty/Invalid Input Handling
- [ ] `os.getpgid(None)` / dead pid (#2): test the path where `pty.pid` is `None` (never spawned) — must skip the kill, not crash.
- [ ] `supervise()` with a `factory()` that itself raises immediately (#4): must count as a restart and not spin-loop without backoff.
- [ ] Reflection pool (#3) with `max_workers` env set to invalid/zero: clamp to a sane minimum (≥1) rather than crash.

### Error State Rendering
- [ ] Degraded-mode startup (#1) must surface a clear log line and (if surfaced) a dashboard/health signal that granite work is paused — not a silent skip. Verify a granite-routed session enqueued during degraded mode is visibly *deferred*, not dropped or failed.

## Test Impact

- [ ] `tests/` — search for tests asserting the ollama hard-gate `sys.exit(1)` on `ensure_granite_model` failure — UPDATE: assert degraded-boot (flag false, no exit) instead. (grep `ensure_granite_model` in tests/ during build.)
- [ ] `tests/unit/granite_container/test_container_pkill_gating.py` — guards the pkill gating — REPLACE: rewrite against the process-group kill (assert only the target pgid is signalled) and add the spawn-failure partial-child orphan-reap case. (Also grep `_run_pkill_fallback`/`pkill` across tests/.)
- [ ] `tests/` — reflection scheduler tests touching `run_in_executor`/default pool — UPDATE: assert the dedicated executor is used; add a saturation test (N wedged reflections do not block a critical-path executor call).
- [ ] New tests required (greenfield): supervisor respawn-with-backoff + storm-cap (asserting REAL SIGABRT process death, not a swallowed exception); re-probe/breaker open/half-open/closed; process-group teardown bystander-survival; spawn-failure partial-child orphan-reap; degraded-boot pickup deferral. (Reflection-subprocess freeze-isolation tests move to #1828.)

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
**Mitigation:** A conservative K-restarts-in-window storm cap (NAMED env-overridable constants, erring toward NOT killing legitimate work) → controlled recycle via **`_self_kill()` (SIGABRT, gated by `WORKER_DEADMAN_ENABLED`); `os.abort()` if the deadman is disabled — NEVER a bare `sys.exit(1)`**, because a `SystemExit` raised inside an asyncio done-callback is caught by the loop's callback-exception handler and logged, not propagated, so the process would keep running and the cap would silently fail. launchd recycles the whole process (visible, throttled) after the SIGABRT. Every respawn logs at WARNING with a running count; the cap recycle logs CRITICAL. The storm-cap test asserts REAL subprocess death, not just the log line.

### Risk 4: Two degraded-state signals could fight over "ollama is down"
**Impact:** If a standalone breaker and the re-probe flag were independent, they could oscillate (one says available, the other open).
**Mitigation:** Eliminated by folding the breaker into Fix #1 — there is exactly ONE state machine (`_granite_reprobe_loop`) and ONE flag (`GRANITE_AVAILABLE`). The startup probe seeds the initial state; the loop owns every subsequent transition. Nothing to reconcile.

### Risk 5: Deferred reflection subprocess (#1828) double-runs jobs at rollout
**Impact:** When #1828 lands, if both the worker and the new subprocess construct a scheduler during rollout, reflections could run twice.
**Mitigation (carried to #1828's plan):** Delete the worker's `create_task` in the **same** change that adds the subprocess (no parallel-run window — per the no-parallel-migrations rule). The scheduler already coordinates via Redis `Reflection` records (single-owner claim). Out of scope for this slug; recorded here so the follow-up inherits the constraint.

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
- [DEFERRED → #1828] Reflection scheduler subprocess split (formerly Fix #5) — split to its own follow-up issue per the CRITIQUE coordinator. With Fix #3 (bulkhead pool) + Fix #4 (supervisor) landed, a wedged reflection can no longer starve the critical path, so the structural relocation is a follow-up, not a prerequisite. NOT chained behind anything in this slug.
- [FOLDED → Fix #1] Standalone runtime ollama circuit breaker (formerly Fix #6) — there is no mid-session ollama call to guard (`classify_pm_prefix` is pure regex). The breaker logic is folded into Fix #1's `_granite_reprobe_loop`, which wraps the only in-scope ollama call (`ensure_granite_model`). No separate Stage C, breaker-builder, or `build-breaker` task.
- In scope for this slug: Fixes #1–#4 only (Stages A–B).

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
  - Role: Fix #4 — `supervise()` helper with backoff + SIGABRT storm cap (`_self_kill()`/`os.abort()`, never `sys.exit`), wrap five spawn sites + the #1 re-probe loop
  - Agent Type: builder
  - Domain: async/concurrency
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
- **Validates**: tests/ (degraded-boot pickup-deferral test + re-probe/breaker open/half-open/closed test, create); worker startup smoke
- **Assigned To**: granite-degrade-builder
- **Agent Type**: builder
- **Parallel**: true
- Convert `worker/__main__.py:617-638` from `sys.exit(1)` to `GRANITE_AVAILABLE = False` + warning.
- Add supervised `_granite_reprobe_loop` that re-probes `ensure_granite_model()` on `GRANITE_REPROBE_INTERVAL_S` and flips the flag on success. **The loop carries the folded-in breaker** (formerly Fix #6): consecutive-timeout counter, open after `GRANITE_BREAKER_OPEN_THRESHOLD`, half-open re-probe after `GRANITE_BREAKER_COOLDOWN_S`. All NAMED env-overridable constants with provisional/tunable comments; breaker state process-global.
- Gate granite-routed (PM/Dev SDLC) session pickup on `GRANITE_AVAILABLE`, reusing the `paused_circuit`/deferred vocabulary.
- Reconcile the stale in-code comments (`worker/__main__.py:617-625`, `granite_classifier.py:49-58` docstring) so neither claims a runtime ollama routing role.

### 2. Fix #2 — process-group teardown
- **Task ID**: build-pgroup-teardown
- **Depends On**: none
- **Validates**: tests/ (bystander-survival test + spawn-failure partial-child orphan-reap test, create)
- **Assigned To**: teardown-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `_run_pkill_fallback` (`container.py:757-775`).
- Replace callsites 1080, 1522, 1958 with `os.killpg(os.getpgid(pty.pid), SIGTERM)`→`SIGKILL` over `self._pm_pty`/`self._dev_pty`, guarded for dead/None pids + `isalive()` liveness check.
- **Spawn-failure callsite (1080):** iterate BOTH PTYs and `killpg` each non-None group inside `try/except ProcessLookupError` — do NOT short-circuit both PTYs on a single None-check, so a half-created child is reaped, not leaked. Add the "spawn raises after partial child creation → no orphan survives" test.

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
- **Validates**: tests/ (respawn-with-backoff + storm-cap test asserting REAL SIGABRT process death, create)
- **Assigned To**: supervisor-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `supervise(name, factory, *, max_restarts, window_s, base_backoff_s)` in `worker/`, defaults from NAMED env-overridable conservative-provisional constants (`WORKER_SUPERVISOR_MAX_RESTARTS`/`WORKER_SUPERVISOR_WINDOW_S`/`WORKER_SUPERVISOR_BASE_BACKOFF_S`).
- Wrap the five `create_task` sites (717, 734, 753, 770, 788) + the #1 re-probe loop; respawn on unexpected death.
- **Storm-cap recycle uses SIGABRT, never `sys.exit(1)`**: on exceeding the cap, `logger.critical` then `_self_kill()` (#1815 seam, gated by `WORKER_DEADMAN_ENABLED`); if the deadman is disabled, `os.abort()`. The storm-cap test runs the worker under a subprocess and asserts it actually dies (a swallowed `SystemExit` would falsely pass a log-only check).

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-supervisor
- **Assigned To**: fc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-fault-containment.md`; add to `docs/features/README.md`; cross-reference granite-pty + bridge-worker docs. Note the deferred reflection subprocess split (#1828) as future work.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-supervisor, document-feature
- **Assigned To**: fc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all new tests + full success criteria; confirm grep anti-criteria (no `pkill`, no `_run_pkill_fallback`, no bare `sys.exit` in the storm-cap path); generate final report.

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
| Supervisor helper present | `grep -c 'def supervise' worker/__main__.py` | output contains 1 |
| Storm-cap uses SIGABRT not sys.exit (anti-criterion) | `grep -n '_self_kill\|os.abort' worker/__main__.py` | matches present in supervisor path |
| Re-probe/breaker constants are named (no bare literals) | `grep -c 'GRANITE_REPROBE_INTERVAL_S\|GRANITE_BREAKER_OPEN_THRESHOLD\|GRANITE_BREAKER_COOLDOWN_S' config/settings.py` | output ≥ 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). FULL roster: Risk & Robustness, Scope & Value, History & Consistency. Verdict: NEEDS REVISION. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk&Robustness (Skeptic), Scope&Value (Simplifier), History&Consistency (Consistency Auditor) — unanimous | Fix #6 guards a runtime ollama call that does not exist. Data Flow step 5 (L94), Key Element (L135), and Success Criterion (L270) assert a "mid-session granite call → cascading 60s stalls," but `classify_pm_prefix` is pure regex (`granite_classifier.py:277`, docstring L143 "No ollama call on the routing path"). The ONLY ollama call is `ensure_granite_model`'s 60s probe (`granite_classifier.py:210-220`, `probe_timeout=60.0` L182) — the startup/re-probe path Fix #1 already wraps. Fix #6 as a standalone Stage C breaker protects nothing Fix #1 doesn't; the plan internally contradicts itself (Technical Approach L148 + Open Question 3a already concede this). | Resolve the contradiction in the plan text: fold Fix #6 into Fix #1 (the `_granite_reprobe_loop` carries the consecutive-timeout/open/half-open counters — the breaker IS the re-probe loop). Rewrite Data Flow step 5, Key Element L135, and Success Criterion L270 to drop "mid-session"/"per-turn"/"cascading 60s stall" framing. Remove the standalone Stage C / `build-breaker` task (#6) and the `breaker-builder`; re-point Task 7's `Depends On` accordingly. | Both Fix #1's re-probe and the "breaker" wrap only `ensure_granite_model` (`granite_classifier.py:178`). Breaker state must be process-global (startup probe runs via `asyncio.to_thread`). If a genuine per-turn ollama call exists, cite its file:line — none appears in SOURCE_FILES. |
| CONCERN | Risk&Robustness (Adversary) | Internal contradiction on the storm-cap recycle mechanism. Technical Approach Fix #4 (L147) and Risk 3 (L196) say `logger.critical` + `sys.exit(1)` inside the supervisor done-callback, but Freshness Check (L42) and Key Elements (L134) correctly say funnel through `_self_kill()` (SIGABRT). A `SystemExit` raised in an asyncio done-callback is caught by the loop's callback-exception handler and logged, NOT propagated — the process keeps running and the storm cap silently fails to recycle. | Standardize on the SIGABRT path everywhere: the storm cap calls `_self_kill()` (SIGABRT → launchd respawn) gated by `WORKER_DEADMAN_ENABLED`, never bare `sys.exit(1)`. Fix the contradiction in Technical Approach Fix #4 and Risk 3. | In the `add_done_callback` body, after the K-in-window check, call the #1815 `_self_kill()` seam; if `WORKER_DEADMAN_ENABLED` is off, fall back to `os.abort()`, not `sys.exit`. The storm-cap test must assert actual process death (subprocess exit), not just that the log line was emitted. |
| CONCERN | Risk&Robustness (Operator) | Deleting `_run_pkill_fallback` removes the only orphan-PTY net on the self-spawned path. Callsite 1080 fires in the spawn-FAILURE path (`_spawn_pair()` raised), where `self._pm_pty.pid` may be `None` or a half-established child. The new `os.killpg` guard is explicitly skipped when `pid is None` (L132/L360), so a half-spawned `claude` orphan leaks with no fallback — exactly the leak the old pkill caught for the tests/ping-pong path. | Before deleting the pkill net, confirm the spawn-failure orphan is reaped another way (capture any pid `_spawn_pair` assigned before raising and `killpg` it; or extend worker-startup `_kill_orphaned_pty_pids` to cover the self-spawned path). Add an explicit test: "spawn raises after partial child creation → no orphan survives." | At callsite 1080, `_spawn_pair()` may have set `self._pm_pty` but not `self._dev_pty` (or vice versa) before raising; iterate BOTH PTYs, read `.pid` (None if dead/unspawned), and `killpg` each non-None group inside `try/except ProcessLookupError`. Do not rely on a single None-check that short-circuits both PTYs. |
| CONCERN | Scope&Value (User) | Fix #5 (reflection subprocess split, Task 7) is DAG-chained behind `build-breaker` (Fix #6) despite sharing zero source files with it. After Fix #3 caps the reflection pool and Fix #4 supervises respawn, a wedged reflection can no longer starve the critical path — Fix #5's only real prerequisite is that #3/#4 de-risked the loop, not the breaker. | Re-point Task 7's `Depends On` from `build-breaker` to `validate-stage-a` (or `build-supervisor`). The BLOCKER's removal of `build-breaker` forces this anyway. (Coordinator already resolved the ship/no-ship of Fix #5 — this is purely the DAG dependency.) | Fix #5 touches `reflections/__main__.py` (new), `com.valor.reflection-worker.plist` (new), `scripts/install_reflection_worker.sh` (new), `scripts/update/run.py`, and deletes the `create_task` at `worker/__main__.py:753` — no overlap with Fixes #4/#6. The `build-breaker` dependency is ordering-by-convenience, not a coupling. |
| CONCERN | History&Consistency (Consistency Auditor) | Stale in-code comments contradict Fix #1's safety premise. `worker/__main__.py:113-114` ("every PM/Dev turn is routed by an ollama call against it") and `ensure_granite_model`'s docstring `granite_classifier.py:189-191` ("the PM/Dev PTY sessions themselves use the model via the TUI") assert a runtime ollama role that Fix #1 denies. If those comments are correct, Fix #1 would let granite-routed sessions boot against a missing model and mis-route. | Add an explicit reconciliation task: confirm (with file:line) that these comments are stale, and correct them so the codebase no longer claims a runtime ollama routing role. Add to the Inline Documentation section a task to rewrite `granite_classifier.py:189-191` and `worker/__main__.py:113-114` to state granite is classification-only. | The plan's premise (PTYs on Claude OAuth; ollama only for the now-regex classifier) is authoritative, but the plan must make it authoritative by deleting the contradicting comments — otherwise Fix #1 ships a non-fatal gate while surrounding code still claims every turn needs ollama. |

### Revision Resolution (applied 2026-06-30)

All five findings addressed in this revision (`revision_applied: true`):

1. **BLOCKER (Fix #6 redundancy)** — Standalone Fix #6 / Stage C / `build-breaker` task / `breaker-builder` **deleted**. Breaker logic (consecutive-timeout/open/half-open/closed/cooldown) folded into Fix #1's `_granite_reprobe_loop`. "Mid-session"/"per-turn"/"cascading 60s stall" framing removed from Data Flow step 5, Key Elements, Technical Approach, and Success Criteria. The untestable "fast-fails mid-session" criterion is gone.
2. **CONCERN (storm-cap swallowed `sys.exit`)** — Standardized on SIGABRT everywhere: storm cap calls `_self_kill()` (gated by `WORKER_DEADMAN_ENABLED`), `os.abort()` fallback, never bare `sys.exit(1)`. Fixed in Technical Approach Fix #4, Key Elements, Risk 3, and the task. Storm-cap test now asserts REAL subprocess death.
3. **CONCERN (spawn-failure orphan reap)** — Fix #2 preserves an explicit reap at callsite `container.py:1080`: iterate BOTH PTYs, `killpg` each non-None group in `try/except ProcessLookupError`. Added the "spawn raises after partial child creation → no orphan survives" test to Test Impact + Failure Path Test Strategy + the task.
4. **CONCERN (DAG dependency for Fix #5)** — Moot: Fix #5 split to follow-up **#1828** (no longer chained behind anything); the `build-breaker` dependency is gone with the task.
5. **CONCERN (stale in-code comments)** — Added reconciliation task. Corrected the critique's drifted line numbers to the actual locations: `worker/__main__.py:617-625` (Step 4b.5 comment block) and `granite_classifier.py:49-58` (`ensure_granite_model` docstring). Task recorded in Inline Documentation, Fix #1 task, and Fix #1 Technical Approach.

Coordinator resolutions to the four prior Open Questions folded in (named env-overridable provisional constants for re-probe/breaker and supervisor cap; reflection-worker gating follows the bridge-role launchd pattern in #1828). See **## Open Questions** (all marked RESOLVED) and **## Downstream / Deferred**.

---

## Downstream / Deferred

- **#1828 — Reflection scheduler subprocess split (formerly Fix #5).** Split to its own follow-up issue per the CRITIQUE coordinator (labels: `bug`, `reflections`; parent #1816). Ships once Stages A–B are in production and observed. Carries forward: `reflections/__main__.py` entry point, `com.valor.reflection-worker.plist` + `scripts/install_reflection_worker.sh` (idempotent, machine-role-gated following the existing bridge-role launchd gating pattern), `scripts/update/run.py` wiring, same-change deletion of the worker `create_task` (no parallel-run window), and reflection-freeze isolation tests. A full plan should be produced via `/do-plan` when scheduled.
- **(Folded, not deferred) Former Fix #6 — runtime ollama breaker.** Absorbed into Fix #1's `_granite_reprobe_loop`. No separate issue; nothing to track.

---

## Open Questions

_All open questions were RESOLVED by the CRITIQUE coordinator. Retained here as a settled record._

1. **Re-probe / breaker interval defaults — RESOLVED.** Defaults live as NAMED, env-overridable module constants with provisional/tunable grain-of-salt comments — no bare literals: `GRANITE_REPROBE_INTERVAL_S`, `GRANITE_BREAKER_OPEN_THRESHOLD`, `GRANITE_BREAKER_COOLDOWN_S`. Tunable in the field; not hardcoded.
2. **Supervisor storm cap — RESOLVED.** A conservative provisional NAMED env-overridable constant (`WORKER_SUPERVISOR_MAX_RESTARTS` / `WORKER_SUPERVISOR_WINDOW_S`), erring toward NOT killing legitimate work. Recycle is via SIGABRT (`_self_kill()`/`os.abort()`), never a swallowed `sys.exit`.
3. **Fix #5 scheduling — RESOLVED.** Split to follow-up issue **#1828**; NOT shipped in this slug. This slug ships Stages A–C (Fixes #1–#4; #6 folded into #1). See **## Downstream / Deferred**.
3a. **Fix #6 redundancy — RESOLVED.** Folded into Fix #1's re-probe loop (the loop IS the breaker, wrapping the single `ensure_granite_model` call). No standalone breaker; no separate Stage C/task/builder. Confirmed there is no mid-session ollama call to guard.
4. **Reflection-worker role gating — RESOLVED (deferred to #1828).** When the subprocess lands in #1828, its installer follows the existing bridge-role launchd gating pattern (only bridge machines that run the worker run the reflection subprocess). No machine-gate decision is needed in this slug.
