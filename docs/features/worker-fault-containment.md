# Worker Fault Containment

**Issue:** #1816  
**Status:** Shipped (Stages A–B, Fixes #1–#4)  
**Deferred:** Fix #5 (reflection subprocess split) → #1828 — **now shipped**, see
[Reflection Scheduler Subprocess](reflection-scheduler-subprocess.md). The scheduler runs
out-of-process (`python -m reflections`, `com.valor.reflection-worker`); the worker no
longer constructs it.

## Problem

The worker ran **six long-lived asyncio tasks on one event loop** with no respawn logic — a silently-dead monitor would leave the worker blind until a full process restart. Three compounding hazards:

1. **ollama hard-gate** — `ensure_granite_model()` failure exited the entire worker even though ollama has no runtime routing role (`classify_pm_prefix` is pure regex, not an ollama call).
2. **Machine-wide pkill** — teardown ran `pkill -f "claude --permission-mode bypassPermissions"`, which the code's own comment admitted could kill bystander sessions.
3. **Shared thread pool** — sync reflections and event-loop-blocking Redis scans competed with critical-path `run_in_executor` work (Telegram message classification, media transcription).

## Fixes Shipped (Stages A–B)

### Fix #1 — ollama graceful degradation — removed by the headless cutover (D2, issue #1924)

Session dispatch has no ollama dependency at all as of the headless
session-runner cutover: the `granite_available` flag, the `ensure_granite_model`
startup probe, the `_granite_reprobe_loop` circuit breaker, and the
`agent/session_pickup` pickup gate are deleted outright rather than degraded.
There is nothing to probe or breaker-guard on the session-execution path, so
"graceful degradation" is moot — the worker starts straight into recovery and
queue pickup. Bridge routing and email triage keep their own direct ollama
calls for classification, untouched by this cutover; see [Local Ollama Model
Policy](local-model-policy.md) and [Headless Session
Runner](headless-session-runner.md).

### Fix #2 — process-group teardown (superseded location: `agent/session_runner/runner.py`)

- **Deleted `_run_pkill_fallback`**: the machine-wide `pkill -f "claude --permission-mode bypassPermissions"` is gone.
- The scoped-teardown pattern this fix established — `os.killpg` against the child's own process group rather than a machine-wide `pkill` — is the same discipline the headless session runner uses today: each turn's `claude -p` subprocess spawns in its own process group (`start_new_session=True`), and the steer-preempt / timeout paths SIGTERM → grace → SIGKILL that group specifically (`agent/session_runner/runner.py`), never a machine-wide pattern match. See [Headless Session Runner](headless-session-runner.md).
- `isalive()` / liveness check before kill prevents killing an already-dead pid whose pgid might have been recycled by the OS.

### Fix #3 — reflection bulkhead pool (`agent/reflection_scheduler.py`, `reflections/audits/redis_quality_audit.py`)

- **Dedicated `ThreadPoolExecutor(max_workers=REFLECTION_POOL_WORKERS, default 2)`** owned by `reflection_scheduler.py`. Sync reflections use `run_in_executor(_reflection_pool, call)` instead of the shared default pool, so N wedged reflections cannot starve critical-path `run_in_executor` calls (Telegram routing, media transcription).
- **Off-loop async-audit fix** (`redis_quality_audit.py`): the `.query.all()` scans inside the `async def run()` method are wrapped in `asyncio.to_thread(...)` so they run off the event loop. Without this fix the bulkhead pool would not help (the audit is awaited directly, bypassing `run_in_executor`).

### Fix #4 — background-task supervisor (`worker/__main__.py`)

- **`supervise(name, factory, *, max_restarts, window_s, base_backoff_s)`** helper (~90 lines): wraps `asyncio.create_task(factory())` with a done-callback that respawns on unexpected death.
- **Exponential backoff**: first respawn waits `base_backoff_s` (default 1 s), second `2×`, and so on (capped at `window_s / 2`). Restart timestamps are tracked in a rolling window.
- **Storm cap**: exceeding `WORKER_SUPERVISOR_MAX_RESTARTS` (default 5) within `WORKER_SUPERVISOR_WINDOW_S` (default 300 s) triggers **`_self_kill()`**, which emits an all-thread Python stack dump via `faulthandler.dump_traceback(all_threads=True)` to stderr and then delivers `SIGKILL` to the process. This is the same seam used by the dead-man's-switch (#1815). SIGKILL replaced an earlier abort-based design that triggered the macOS crash reporter (a crash dialog plus a `Python-*.ips` file) on every recycle; SIGKILL is equally unswallowable but produces no dialog and no `.ips` file. `sys.exit(1)` is explicitly avoided — a `SystemExit` raised inside an asyncio done-callback is swallowed by the event loop's callback-exception handler, so the process would keep running and the cap would silently fail.
- **Shutdown guard**: cancelled tasks and `_shutdown_requested=True` suppress respawn.
- **Wrapped tasks**: `session-health-monitor`, `session-tool-timeout-monitor`, `reflection-scheduler`, `session-notify-listener`, `idle-sweeper` (the `granite-reprobe` task wrapped here was deleted along with Fix #1, D2).

## Deferred: Fix #5 → #1828

The reflection scheduler subprocess split (moving the 31 reflection jobs out of the worker's event loop into a separate `python -m reflections` process) is deferred to follow-up issue #1828. With Fix #3 (bulkhead pool + off-loop async-audit) and Fix #4 (supervisor with storm cap) in place, a wedged reflection can no longer starve the critical path or silently kill monitors — so #5 is a structural decoupling win, not a prerequisite.

## Configuration

All new constants are NAMED, env-overridable, and marked provisional/tunable in `.env.example` and `config/settings.py`:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `REFLECTION_POOL_WORKERS` | 2 | Bulkhead pool size for sync reflections |
| `WORKER_SUPERVISOR_MAX_RESTARTS` | 5 | Storm-cap restart count within window |
| `WORKER_SUPERVISOR_WINDOW_S` | 300 | Rolling window for restart count |
| `WORKER_SUPERVISOR_BASE_BACKOFF_S` | 1.0 | Base backoff (s), doubles each restart |

(The Fix #1 breaker constants — `GRANITE_REPROBE_INTERVAL_S`, `GRANITE_BREAKER_OPEN_THRESHOLD`, `GRANITE_BREAKER_COOLDOWN_S` — were deleted with Fix #1 itself.)

## Tests

- `tests/unit/test_reflection_pool_bulkhead.py` — Fix #3: dedicated pool, saturation isolation, event-loop responsiveness (redis_quality_audit off-loop)
- `tests/unit/test_worker_supervisor.py` — Fix #4: respawn on crash, no respawn on cancel, backoff grows, shutdown guard, **real subprocess SIGKILL assertion**
- Process-group teardown (Fix #2's successor) is covered by the session-runner preempt test suite — see [Headless Session Runner](headless-session-runner.md).

## Architecture Impact

- **Coupling decreases**: each fix reduces blast radius within the single process.
- **No new external dependencies**: all new mechanisms use Python stdlib (`os`, `asyncio`, `concurrent.futures`, `signal`).
- **No Popoto schema change**: the `Reflection` model is unchanged.
- **Data ownership unchanged**: reflection-scheduler ownership move to subprocess is deferred to #1828.
- **Reversibility**: high — all fixes are additive wrappers, flag flips, or kill-call swaps.

## See Also

- `docs/features/bridge-worker-architecture.md` — worker background-task topology
- `docs/features/headless-session-runner.md` — current process-group teardown location (pkill → killpg → runner-owned SIGTERM/SIGKILL)
- `docs/plans/worker-fault-containment.md` — full plan with spike results, risk analysis, race conditions
- Issue #1828 — deferred reflection subprocess split (Fix #5)
