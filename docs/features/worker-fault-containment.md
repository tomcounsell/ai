# Worker Fault Containment

**Issue:** #1816  
**Status:** Shipped (Stages A–B, Fixes #1–#4)  
**Deferred:** Fix #5 (reflection subprocess split) → #1828

## Problem

The worker ran **six long-lived asyncio tasks on one event loop** with no respawn logic — a silently-dead monitor would leave the worker blind until a full process restart. Three compounding hazards:

1. **ollama hard-gate** — `ensure_granite_model()` failure exited the entire worker even though ollama has no runtime routing role (`classify_pm_prefix` is pure regex, not an ollama call).
2. **Machine-wide pkill** — teardown ran `pkill -f "claude --permission-mode bypassPermissions"`, which the code's own comment admitted could kill bystander sessions.
3. **Shared thread pool** — sync reflections and event-loop-blocking Redis scans competed with critical-path `run_in_executor` work (Telegram message classification, media transcription).

## Fixes Shipped (Stages A–B)

### Fix #1 — ollama graceful degradation (`worker/__main__.py`, `agent/session_pickup.py`)

- **`granite_available` flag** (`agent/session_state.py`): process-global bool, default False until the startup probe passes.
- **Non-fatal startup probe**: the `sys.exit(1)` is replaced by setting `granite_available = False` and logging a warning; the worker boots in degraded mode.
- **`_granite_reprobe_loop`** supervised background task: re-probes `ensure_granite_model` on `GRANITE_REPROBE_INTERVAL_S` (default 30 s) and flips the flag on success. **This loop IS the circuit breaker** — it carries a consecutive-timeout counter (open after `GRANITE_BREAKER_OPEN_THRESHOLD` failures, default 3), a cooldown (`GRANITE_BREAKER_COOLDOWN_S`, default 120 s) before half-open re-probe, and CLOSED/OPEN/HALF_OPEN state transitions with log events on each flip.
- **Pickup gate**: `agent/session_pickup._pop_agent_session` gates project-keyed (eng) session pickup on `granite_available`; sessions stay queued (not failed) while granite is unavailable and auto-resume when it recovers.
- **Stale comment reconciliation**: comments that incorrectly claimed a runtime ollama routing role (in `worker/__main__.py` and `granite_classifier.py`) have been corrected.

All thresholds are NAMED env-overridable constants with provisional/tunable comments (no bare literals).

### Fix #2 — process-group teardown (`agent/granite_container/container.py`)

- **Deleted `_run_pkill_fallback`**: the machine-wide `pkill -f "claude --permission-mode bypassPermissions"` is gone from `container.py`.
- **Replaced with `os.killpg(os.getpgid(pty.pid), SIGTERM→SIGKILL)`** (`_close_pair_and_reap`) scoped to each container's own `_pm_pty` / `_dev_pty` process groups. Each PTY child is a session leader (`pty.fork()` → `setsid`), so killing its pgid cannot affect bystander processes.
- **Pool-owned pairs are excluded** via `_uses_pool_pair()`: on the production path the PTY pool owns pair lifecycle (close-on-release + PID-targeted reap), so the scoped `killpg` fires only on the self-spawned path (tests, ping-pong runs).
- **Spawn-failure orphan reap** (callsite `container.py:~1080`): when `_spawn_pair()` raises after partially creating a child, **both** PTYs are iterated independently — a `None` pid skips silently, a non-None pid is `killpg`'d inside `try/except ProcessLookupError` so a half-created `claude` orphan cannot survive.
- `isalive()` check before kill prevents killing an already-dead pid whose pgid might have been recycled by the OS.

### Fix #3 — reflection bulkhead pool (`agent/reflection_scheduler.py`, `reflections/audits/redis_quality_audit.py`)

- **Dedicated `ThreadPoolExecutor(max_workers=REFLECTION_POOL_WORKERS, default 2)`** owned by `reflection_scheduler.py`. Sync reflections use `run_in_executor(_reflection_pool, call)` instead of the shared default pool, so N wedged reflections cannot starve critical-path `run_in_executor` calls (Telegram routing, media transcription).
- **Off-loop async-audit fix** (`redis_quality_audit.py`): the `.query.all()` scans inside the `async def run()` method are wrapped in `asyncio.to_thread(...)` so they run off the event loop. Without this fix the bulkhead pool would not help (the audit is awaited directly, bypassing `run_in_executor`).

### Fix #4 — background-task supervisor (`worker/__main__.py`)

- **`supervise(name, factory, *, max_restarts, window_s, base_backoff_s)`** helper (~90 lines): wraps `asyncio.create_task(factory())` with a done-callback that respawns on unexpected death.
- **Exponential backoff**: first respawn waits `base_backoff_s` (default 1 s), second `2×`, and so on (capped at `window_s / 2`). Restart timestamps are tracked in a rolling window.
- **Storm cap**: exceeding `WORKER_SUPERVISOR_MAX_RESTARTS` (default 5) within `WORKER_SUPERVISOR_WINDOW_S` (default 300 s) triggers **`_self_kill()` → `os.abort()` (SIGABRT)**. This is the same seam used by the dead-man's-switch (#1815). `sys.exit(1)` is explicitly avoided — a `SystemExit` raised inside an asyncio done-callback is swallowed by the event loop's callback-exception handler, so the process would keep running and the cap would silently fail.
- **Shutdown guard**: cancelled tasks and `_shutdown_requested=True` suppress respawn.
- **Wrapped tasks**: `session-health-monitor`, `session-tool-timeout-monitor`, `reflection-scheduler`, `session-notify-listener`, `idle-sweeper`, `granite-reprobe` (Fix #1 re-probe loop).

## Deferred: Fix #5 → #1828

The reflection scheduler subprocess split (moving the 31 reflection jobs out of the worker's event loop into a separate `python -m reflections` process) is deferred to follow-up issue #1828. With Fix #3 (bulkhead pool + off-loop async-audit) and Fix #4 (supervisor with storm cap) in place, a wedged reflection can no longer starve the critical path or silently kill monitors — so #5 is a structural decoupling win, not a prerequisite.

## Configuration

All new constants are NAMED, env-overridable, and marked provisional/tunable in `.env.example` and `config/settings.py`:

| Env Var | Default | Purpose |
|---------|---------|---------|
| `GRANITE_REPROBE_INTERVAL_S` | 30 | Re-probe interval when breaker CLOSED |
| `GRANITE_BREAKER_OPEN_THRESHOLD` | 3 | Consecutive failures before OPEN |
| `GRANITE_BREAKER_COOLDOWN_S` | 120 | Cooldown (s) before half-open re-probe |
| `REFLECTION_POOL_WORKERS` | 2 | Bulkhead pool size for sync reflections |
| `WORKER_SUPERVISOR_MAX_RESTARTS` | 5 | Storm-cap restart count within window |
| `WORKER_SUPERVISOR_WINDOW_S` | 300 | Rolling window for restart count |
| `WORKER_SUPERVISOR_BASE_BACKOFF_S` | 1.0 | Base backoff (s), doubles each restart |

## Tests

- `tests/unit/granite_container/test_container_pkill_gating.py` — Fix #2: pkill deleted, process-group teardown, bystander survival, spawn-failure partial-child orphan reap
- `tests/unit/test_reflection_pool_bulkhead.py` — Fix #3: dedicated pool, saturation isolation, event-loop responsiveness (redis_quality_audit off-loop)
- `tests/unit/test_worker_granite_degradation.py` — Fix #1: degraded boot, flag flip, reprobe loop, breaker OPEN, pickup deferral
- `tests/unit/test_worker_supervisor.py` — Fix #4: respawn on crash, no respawn on cancel, backoff grows, shutdown guard, **real subprocess SIGABRT assertion**

## Architecture Impact

- **Coupling decreases**: each fix reduces blast radius within the single process.
- **No new external dependencies**: all new mechanisms use Python stdlib (`os`, `asyncio`, `concurrent.futures`, `signal`).
- **No Popoto schema change**: the `Reflection` model is unchanged.
- **Data ownership unchanged**: reflection-scheduler ownership move to subprocess is deferred to #1828.
- **Reversibility**: high — all fixes are additive wrappers, flag flips, or kill-call swaps.

## See Also

- `docs/features/bridge-worker-architecture.md` — worker background-task topology
- `docs/features/granite-pty-production.md` — granite teardown (pkill → killpg)
- `docs/plans/worker-fault-containment.md` — full plan with spike results, risk analysis, race conditions
- Issue #1828 — deferred reflection subprocess split (Fix #5)
