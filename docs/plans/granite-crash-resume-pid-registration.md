---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1851
last_comment_id:
revision_applied: true
---

# Granite crash-resume PID registration

## Problem

A granite session runs on a PM+Dev `claude` PTY pair leased from the `PTYPool`. When one of those PTYs crashes mid-run (pexpect EOF, no `turn_end`), `Container._resume_crashed_pty` spawns a **fresh** `PTYDriver` that resumes the dead session via `--resume <uuid>`. This fresh process has a brand-new OS PID.

The worker-startup orphan sweep (`_kill_orphaned_pty_pids`, run before the pool is built on every worker boot) reaps leaked granite `claude` processes by reading `data/granite_pty_pids.json` and SIGKILLing the listed PIDs. That registry is populated only by the pool's own spawn paths (`_spawn_session_pair`, `_spawn_slot`/`_respawn_slot`). The container's crash-resume spawn happens **outside** the pool, so the resumed PID is never registered.

**Current behavior:**
When a crash-resumed session's worker later dies without a clean teardown (crash, SIGKILL, machine sleep), the resumed `claude` process and its grandchildren survive the next worker's startup sweep as orphans, because their PID was never written to the registry the sweep reads. Over repeated crash-resume-then-worker-death cycles this leaks granite PTY processes.

**Desired outcome:**
The crash-resume PTY spawn registers its new PID in the pool's tracked/persisted PID set (and drops the dead PID it replaced), so the worker-startup sweep sees it and SIGKILLs it. The sweep is `PTYPool.kill_orphans`, which does `os.kill(pid, SIGKILL)` on each listed **parent** PID (`pty_pool.py:285`) — it does **not** `killpg` the process group. #1820's `killpg` teardown lives on the graceful-release path (`_close_pair_and_reap`), not on this startup sweep, so the concrete payoff of this fix is reaping the resumed `claude` **parent** process on the next worker boot. Grandchild reaping remains #1820's job on the graceful path.

## Freshness Check

**Baseline commit:** `2c681fdd` (main at plan time; recon verified on branch tip `f8eac988`, same crash-resume code)
**Issue filed at:** 2026-07-02T10:56:17Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/granite_container/container.py:1450-1513` (`_resume_crashed_pty`) — spawns fresh `PTYDriver` at line 1476, `.spawn()` at 1484 with no PID registration. Still holds.
- `agent/granite_container/pty_pool.py:716-743` (`_kill_orphaned_pty_pids`) — reads `data/granite_pty_pids.json`, SIGKILLs listed PIDs. Still holds.
- `agent/granite_container/pty_pool.py:555,627-629` — the only sites that add PIDs to `_spawned_pids`. Still holds. No `register_pid`/`unregister_pid` public method exists yet.
- `agent/granite_container/bridge_adapter.py:448,633` — `BridgeAdapter` holds `self._pool` and constructs `Container` wiring `on_turn`/`on_pty_read` callbacks. Still holds.

**Cited sibling issues/PRs re-checked:**
- #1847 — MERGED. Landed `_resume_crashed_pty` (the crash-resume path). This bug is a deferred handoff #2 from its review.
- #1688 — CLOSED (hook-driven turn returns). Parent design.
- #1843 — CLOSED. Added no kill/reap path by design; deferred this to #1820/#1816.
- #1816 — CLOSED. Shipped `container._close_pair_and_reap` (self-spawned-path process-group teardown).
- #1820 — OPEN. Slot-lease + progress-deadline cancel scope + `killpg` PTY process-group teardown. Active plan `docs/plans/slot-lease-progress-deadline.md`.

**Commits on main since issue was filed (touching referenced files):**
- `b624627` per-role transport hedge (#1848) — touches `container.py`, does not change the crash-resume spawn or `_spawned_pids`. Irrelevant to root cause.
- `e62dac76` ANSI-strip perf (#1849 review) — touches `container.py` read loop, not crash-resume. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:** `slot-lease-progress-deadline.md` (#1820). Grepped for `_resume_crashed_pty` / `_spawned_pids` / `register_pid` / `on_pty_spawn` — **zero matches**. #1820 owns process-group teardown and lease ownership; it does NOT register the crash-resume PID. No overlap on this fix; coordinate on the shared PID it will `killpg`.

**Notes:** Bug confirmed present and reachable at both `main` (`2c681fdd`) and branch tip (`f8eac988`).

## Prior Art

- **#1847 (MERGED)**: hook-driven turn returns. Introduced `_resume_crashed_pty` and its critique concerns #1/#6 (reapply `--settings`; resume-owner window). This orphan-evasion is that PR's review handoff #2, deferred out of #1843's wiring scope.
- **#1816 (CLOSED)**: added `_close_pair_and_reap` — process-group teardown on the **self-spawned** path (tests, ping-pong). Establishes the `killpg` pattern this issue's PID feeds, but only covers self-spawned containers, not the pool crash-resume path.
- **#1572 (context)**: the original orphan-leak acceptance criterion that made the pool reuse prewarmed pairs and record PIDs to `data/granite_pty_pids.json`. This fix extends that same registry to cover the one spawn path that bypasses the pool.

No prior attempt fixed crash-resume PID registration. No `## Why Previous Fixes Failed` section needed.

## Research

No relevant external findings — this is purely internal process/PID lifecycle wiring. Proceeding with codebase context.

## Data Flow

1. **Entry point**: A leased PM/Dev `PTYDriver` crashes mid-turn (`pexpect.EOF`, `!isalive`). The container's turn loop calls `_resume_crashed_pty(dead_pty, role)`.
2. **`_resume_crashed_pty`** (`container.py:1450`): captures `dead_pty.last_resume_uuid()`, closes `dead_pty`, constructs a new `PTYDriver(resume_uuid=...)`, calls `new_pty.spawn()` → **new OS PID** at `new_pty._child.pid`. Swaps `self._pm_pty`/`self._dev_pty`.
   - **GAP (this fix)**: the new PID is not communicated to the pool, so `PTYPool._spawned_pids` and `data/granite_pty_pids.json` never learn of it.
3. **PID registry** (`data/granite_pty_pids.json`): written by `PTYPool._persist_pids()`; today only pool spawn paths call it.
4. **Worker death → restart**: new worker's `worker/__main__.py:883` calls `_kill_orphaned_pty_pids()`, which reads the registry and SIGKILLs. The resumed PID is absent → **orphan survives**.
5. **Output (post-fix)**: `_resume_crashed_pty` invokes an injected `on_pty_spawn(new_pid)` callback (wired by `BridgeAdapter` to `pool.register_pid`) **immediately after `new_pty.spawn()` returns and BEFORE `new_pty.write(CRASH_RESUME_CONTINUE)`** — the process is already live at `spawn()`, and `write()` can raise (dropping into the `except` that returns `None`), so registering after `write()` would leak a live-but-unregistered PID on the spawn-ok/write-fail path. It drops the dead PID via `on_pty_despawn(dead_pid)` **only when `dead_pty.close(force=True)` actually succeeded** (a swallowed close failure means the old process may still be alive, so it must stay registered). The `on_pty_despawn(dead_pid)` call lives in the method's outer `finally` gated on `closed_ok`, so a confirmed-dead PID is dropped on **every** exit path — including the spawn/write-fail path that `return None`s (close succeeded, but `new_pty.spawn()`/`write()` then raised). Placing the drop only after the successful swap would strand a confirmed-dead PID in the registry forever on that path. The registry now lists the live resumed PID; the next worker-startup sweep SIGKILLs it.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (no new secret, service, or config).

## Solution

### Key Elements

- **`PTYPool.register_pid(pid)` / `unregister_pid(pid)`** (public, plain sync): add/discard a PID in `_spawned_pids` under a new `threading.Lock` and persist. Crash-resume registers through the pool's owned registry rather than writing the JSON file directly (avoids a second writer racing `_persist_pids`).
- **Thread-safety fix for `_spawned_pids`** (root cause of Concern 1): the crash-resume callbacks fire from the **container's session thread**, not the pool's event-loop thread, so `register_pid`/`unregister_pid` mutate `_spawned_pids` concurrently with the pool's own spawn-thread `.add()`/`.discard()` sites (`pty_pool.py:262,520,555,627-629`) and with `_persist_pids`'s `sorted(self._spawned_pids)` (`pty_pool.py:271`). Today `_persist_pids` catches only `OSError`, so a concurrent `.add()` during that `sorted()` iteration raises an **uncaught `RuntimeError: Set changed size during iteration`**. Fix: introduce `self._pids_lock = threading.Lock()`. **`_persist_pids` takes its snapshot under the lock itself** — `with self._pids_lock: snapshot = sorted(self._spawned_pids)` — then writes the JSON from `snapshot` outside the lock, so the write happens off a stable list and the lock scope stays tiny. Keep `_persist_pids`'s `except OSError` unchanged: the lock removes the `RuntimeError` at its source (no concurrent iteration is possible), so only OS/file errors remain, and swallowing anything broader would silently hide future bugs.
- **NON-NEGOTIABLE INVARIANT (deadlock guard, BLOCKER fix): `_persist_pids()` must NEVER be called while `_pids_lock` is held.** `threading.Lock` is non-reentrant, and `_persist_pids` re-acquires `_pids_lock` for its snapshot; a caller that holds the lock across a `_persist_pids()` call self-deadlocks the calling thread forever. At the two existing add-then-persist spans (`pty_pool.py:550-556` and `620-630`) the `.add()` is immediately followed by `self._persist_pids()`. Guard **only the bare mutation** in a `with self._pids_lock:` block, then call `self._persist_pids()` as a **separate un-indented statement outside** that block. Same rule for `register_pid`/`unregister_pid`: lock the `.add()`/`.discard()`, release, then persist. Every code-change instruction touching these sites states this explicitly.
- **Resolves Open Question 2**: with the lock, `register_pid`/`unregister_pid` are plain sync methods, safe to call from any thread; no async variant is needed.
- **`Container` callbacks `on_pty_spawn` / `on_pty_despawn`** (`Callable[[int], None] | None`, default `None`): the established injection seam (mirrors `on_turn`, `on_pty_read`). `None` on the self-spawned/test/CLI path — no behavior change there (that path already reaps via `_close_pair_and_reap`).
- **`_resume_crashed_pty` registration**: initialize `dead_pid` and `closed_ok = False` at the top of the outer `try` (before `dead_pty.close(force=True)`) so both are in scope for the outer `finally`. Capture the dead PTY's PID *before* closing it, and set `closed_ok = True` when `close(force=True)` returns without raising. Register the new PID by calling `on_pty_spawn(new_pty._child.pid)` **immediately after `new_pty.spawn()` returns, BEFORE `new_pty.write(CRASH_RESUME_CONTINUE)`**, inside the existing spawn `try` — the process is live at `spawn()` and `write()` can raise. Drop the dead PID from the outer `finally` (alongside the existing `_crash_resume_in_flight = False`): `if closed_ok: on_pty_despawn(dead_pid)`. Placing the drop in the `finally` (not after the successful swap) is the **Concern-2 fix**: it fires on **every** exit path, so the spawn/write-fail path that `return None`s (line 1488) still drops a confirmed-dead PID instead of stranding it in the registry forever. The `closed_ok` gate keeps a dead PID registered only when `close` failed (the old process may survive; the sweep must still reap it). Both callbacks are fail-silent (a raising callback must never crash the resume — mirrors the existing `on_turn` guard in the same method).
- **`BridgeAdapter` wiring**: pass `on_pty_spawn=self._pool.register_pid, on_pty_despawn=self._pool.unregister_pid` into the `Container(...)` construction at `bridge_adapter.py:633`.

### Flow

Crash detected → `_resume_crashed_pty` captures dead PID → closes dead PTY (`closed_ok = True` on success) → spawns `new_pty` → **`on_pty_spawn(new_pty._child.pid)` (pool adds + persists) BEFORE `new_pty.write(continue)`** → `write(continue)` → swap → **outer `finally`: `if closed_ok: on_pty_despawn(dead_pid)`** (pool drops the replaced PID on every exit path, including the spawn/write-fail `return None`) → registry now sweep-visible → worker-startup sweep SIGKILLs the resumed parent PID on the next boot.

### Technical Approach

- **Add `self._pids_lock = threading.Lock()`** in `PTYPool.__init__` (add `import threading` at module top). Guard every `_spawned_pids` **bare mutation** site (`pty_pool.py:262,520,555,627-629`, `clear_spawned_pids`) in its own `with self._pids_lock:` block. In `_persist_pids`, take `snapshot = sorted(self._spawned_pids)` **inside** `with self._pids_lock:` then write JSON from `snapshot` outside the lock; **keep its `except OSError` unchanged** (the lock removes the `RuntimeError` at source; do not broaden the except — a broader catch would silently swallow future bugs). This closes the `RuntimeError: Set changed size during iteration` race between the container's session thread and the pool's spawn thread.
- **BLOCKER — deadlock guard: `_persist_pids()` must NEVER run while `_pids_lock` is held.** The lock is non-reentrant and `_persist_pids` re-acquires it. At the existing add-then-persist spans (`pty_pool.py:550-556`, `620-630`), wrap **only the `.add()` loop** in `with self._pids_lock:` and place `self._persist_pids()` as a **separate un-indented statement after** the `with` block closes. Do NOT wrap the contiguous `add()`+`_persist_pids()` span in a single `with` — that self-deadlocks the pool's event-loop thread forever.
- Add the two public pool methods next to `get_spawned_pids`/`clear_spawned_pids`. `register_pid(pid)` does `with self._pids_lock: self._spawned_pids.add(int(pid))` (lock closes) then `self._persist_pids()` on the next line; `unregister_pid(pid)` does `with self._pids_lock: self._spawned_pids.discard(int(pid))` (lock closes) then `self._persist_pids()` on the next line. `_persist_pids()` is always outside the `with`. Plain sync (safe from any thread because of the lock) — resolves Open Question 2; no async variant.
- Add `on_pty_spawn` / `on_pty_despawn` ctor params to `Container.__init__`, store as `self._on_pty_spawn` / `self._on_pty_despawn`.
- In `_resume_crashed_pty`: guard PID extraction with `getattr(getattr(pty, "_child", None), "pid", None)` (matches the pool's existing extraction and tolerates the fake drivers in existing tests that have no `_child`). Wrap each callback in try/except with a `logger.warning` (fail-silent, never crash resume).
- **Order (round-1 BLOCKER fix):** the raising call in the spawn `try` is `new_pty.write(CRASH_RESUME_CONTINUE)` at `container.py:1485`, **not** `spawn()`. `new_pty.spawn()` (line 1484) creates the live OS process; a spawn-success/write-fail path drops into the `except` and `return None` with a **live claude PID that would never be registered**. Therefore register the new PID with `on_pty_spawn(new_pid)` **immediately after `spawn()` returns and before `write()`**, inside the existing spawn `try`. If `spawn()` itself raises, no process was created and there is nothing to register.
- **Despawn placement (round-2 Concern-2 fix):** initialize `dead_pid` and `closed_ok = False` at the top of the outer `try` (before `dead_pty.close(force=True)` at line 1472), and set `closed_ok = True` immediately after that `close` returns. Put the drop in the **outer `finally`** (the block at line 1510-1513 that already clears `_crash_resume_in_flight`): `if closed_ok: on_pty_despawn(dead_pid)`. This is the only placement that fires on **all three** reachable exits — success, spawn-fail `return None`, and **close-ok/write-fail `return None`** (line 1488, which the round-1 plan missed). A drop placed only after the successful swap would leave a **confirmed-dead** PID (close succeeded) permanently registered on the write-fail path, contradicting Risk 1. The `closed_ok` gate means a **swallowed close failure keeps** the old PID registered — the old process may still be alive and the sweep must reap it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `on_pty_spawn`/`on_pty_despawn` call sites are wrapped in `try/except Exception` with `logger.warning` (mirroring the existing `on_turn` guard at `container.py:1498-1502`). Add a test asserting a raising `on_pty_spawn` does not propagate out of `_resume_crashed_pty` (resume still returns the new PTY; `crash_resume_in_flight()` is cleared).
- [ ] `register_pid`/`unregister_pid` persistence failure flows through `_persist_pids`, whose `except OSError` is **kept unchanged**. The `_pids_lock` prevents the `RuntimeError: Set changed size during iteration` at its source (the snapshot is taken under the lock, so no concurrent mutation can occur mid-iteration), so no broader catch is warranted — only genuine OS/file errors remain, and swallowing anything wider would hide future bugs. A test asserts an `OSError` from a bad registry path is swallowed and does not propagate out of `register_pid`.

### Empty/Invalid Input Handling
- [ ] Callbacks default `None`: `_resume_crashed_pty` must no-op the registration when the callback is `None` (self-spawned/CLI/test path). Covered by the existing `test_crash_resume_settings.py` tests, which pass no callback.
- [ ] `new_pty._child` absent (fake driver): `getattr(...)` yields `None`, registration is skipped without error. Assert the existing fake-driver tests still pass.

### Error State Rendering
- [ ] No user-visible output changes — this is process-lifecycle bookkeeping. N/A for user-facing error rendering.

## Test Impact

- [ ] `tests/unit/granite_container/test_crash_resume_settings.py::TestContainerResumeThreadsSettingsPath` — UPDATE (verify-only): its `_FakeDriver` has no `_child` and the `Container` is built with no `on_pty_spawn`, so the new registration path no-ops. Confirm both existing tests still pass unchanged; optionally extend `_FakeDriver` with a `_child.pid` to add positive-registration coverage here.
- [ ] `tests/unit/granite_container/test_pty_pool.py` — UPDATE: add coverage for `register_pid`/`unregister_pid` (lock-guarded add-then-persist, discard-then-persist) alongside the existing `get_spawned_pids`/`clear_spawned_pids` tests.
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` — UPDATE (verify-only): confirm the added `Container(...)` kwargs don't break the adapter's construction assertions; extend if it asserts the exact kwarg set.

New test file `tests/unit/granite_container/test_crash_resume_pid_registration.py` drives the acceptance criteria (see Success Criteria): register-visible, **closing-the-loop sweep reap** (round-1 Concern 3), **despawn `closed_ok` gating** (round-1 Concern 2), **spawn-ok/write-fail registration** (round-1 BLOCKER), **close-ok/write-fail despawn** (round-2 Concern 2 — close succeeds then `write()` raises → assert `on_pty_despawn(dead_pid)` still fired despite the `return None`), and fail-silent callbacks.

## Rabbit Holes

- **Do NOT implement `killpg`/process-group teardown here.** Grandchild reaping via `killpg` is #1820's territory. This fix only makes the resumed PID *sweep-visible*; the sweep's kill mechanism (currently `os.kill` on the parent PID) is out of scope.
- **Do NOT fix the graceful-release teardown gap.** After crash-resume swaps in `new_pty`, the pool's `slot.pty_pair` still references the dead PTY, so `_release_pair` never closes `new_pty` on a clean release. That is a real leak but is #1816/#1820 teardown territory — the worker-startup sweep (this fix) is its safety net, not its cure.
- **Do NOT give `Container` a direct `PTYPool` reference.** Keep the callback seam — a hard pool reference would break the container's test/CLI standalone path and invert the existing dependency direction (adapter owns pool, container is pool-agnostic).
- **Do NOT write `data/granite_pty_pids.json` from the container.** A second writer would race the pool's `_persist_pids`. Route through the pool's public methods only.

## Risks

### Risk 1: Stale dead-PID entry survives into a later worker's sweep (cross-worker PID reuse)
**Impact:** If a dead PID is *not* dropped from the registry (e.g., the `closed_ok` gate keeps it because close failed), it persists to `data/granite_pty_pids.json`. The OS recycles PIDs; by the time a later worker boots and sweeps, that integer may name an **entirely unrelated live process** (another worker's `claude`, or any process). `kill_orphans` would then `SIGKILL` the wrong process. This is the real hazard — a dangling dead PID is not "harmless": the failure mode is not a swallowed `ProcessLookupError`, it is killing an innocent reused PID.
**Mitigation:** The `closed_ok` gate only *retains* a dead PID when `close(force=True)` failed to reap it — i.e., precisely when the old process may still be alive and *should* be killed. Whenever `close` succeeds the dead PID is dropped via `on_pty_despawn` from the outer `finally`, so it never lingers **on any exit path** — including the spawn/write-fail `return None` (this is the round-2 Concern-2 fix; the round-1 placement dropped it only after the successful swap, stranding a confirmed-dead PID on the write-fail path). The sweep runs at worker startup, seconds after the prior worker died, minimizing the PID-reuse window. Residual cross-worker reuse risk is inherent to the existing PID-registry design (any orphan entry carries it) and is not widened by this fix. Tests assert the registry contains the new PID and, on the close-success path (both the success and the write-fail exits), not the dead PID after a resume.

### Risk 2: Callback raises and re-wedges the crash path
**Impact:** A dropped `continue` nudge or an unhandled exception in the resume path re-wedges the exact path #1688 fixed.
**Mitigation:** Both callbacks are fail-silent (try/except + `logger.warning`), placed so registration failure never blocks the `continue` write or the PTY swap. Test asserts a raising `on_pty_spawn` still returns the new PTY.

## Race Conditions

### Race 1: Cross-thread `_spawned_pids` mutation vs. `_persist_pids` iteration (in-process)
**Location:** `pty_pool.py` — container session thread (`register_pid`/`unregister_pid` via the crash-resume callbacks) vs. pool spawn thread (`_spawn_slot`/`_respawn_slot` `.add()`/`.discard()` and `_persist_pids`'s `sorted(self._spawned_pids)` at line 271).
**Trigger:** A crash-resume registers a PID from the container's session thread while the pool's spawn thread is mid-`sorted()` inside `_persist_pids`, or vice versa.
**Data prerequisite:** `_spawned_pids` must not be mutated by one thread while another iterates it.
**State prerequisite:** These threads are genuinely concurrent — the callback seam introduced by this fix is the first cross-thread writer of `_spawned_pids`. Without a lock, `sorted()` raises the uncaught `RuntimeError: Set changed size during iteration`.
**Mitigation:** New `self._pids_lock = threading.Lock()` guards every bare mutation site; `_persist_pids` copies `sorted(...)` under the lock and writes JSON outside it. Because the snapshot is taken under the lock, no thread can mutate `_spawned_pids` mid-iteration, so `_persist_pids` keeps its narrow `except OSError` (no broadening). The lock is non-reentrant, so `_persist_pids()` is always called **outside** any `_pids_lock` block (see the deadlock-guard invariant in Technical Approach). This is the direct fix for Concern 1.

### Race 2: Registry write vs. worker-startup sweep read (cross-process)
**Location:** `pty_pool.py` `_persist_pids` (writer) vs. `_kill_orphaned_pty_pids` (reader), across worker processes.
**Trigger:** A crash-resume registers a PID while a new worker boots and reads the registry.
**Data prerequisite:** The resumed PID must be persisted to `data/granite_pty_pids.json` before the *next* worker's sweep reads it.
**State prerequisite:** Cross-process: the old worker persists on resume; the new worker reads on boot. These are strictly ordered by the old worker dying before the new one starts — there is no concurrent cross-process read/write of the same file.
**Mitigation:** Registration persists synchronously at resume time (same pattern as `_spawn_session_pair`). Cross-process ordering is inherent (a worker only sweeps at startup, after the prior worker is gone). This matches the existing pool persistence model — the only new synchronization needed is the in-process lock in Race 1.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1820] `killpg` PTY process-group teardown / grandchild reaping and progress-deadline cancel scope — owned by #1820 (active plan `slot-lease-progress-deadline.md`). This fix supplies the registered PID it will target.
- [SEPARATE-SLUG #1816] Graceful-release teardown of the crash-resumed PTY (pool `slot.pty_pair` still points at the dead PTY after swap) — teardown territory tracked under #1816's `_close_pair_and_reap` lineage / #1820. This fix's registry entry is the worker-startup safety net for it.

## Update System

No update system changes required — this feature is purely internal. No new dependency, config file, or migration. The PID registry path (`data/granite_pty_pids.json`) is unchanged; no Popoto model is touched.

## Agent Integration

No agent integration required — this is a worker-internal process-lifecycle fix in `agent/granite_container/`. No MCP surface, `.mcp.json`, or `bridge/telegram_bridge.py` change. The agent reaches granite via the existing `BridgeAdapter → Container` path, which is modified only by adding two internal callback kwargs.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` orphan-cleanup section: document that crash-resumed PTYs register their PID via the pool's `register_pid`/`unregister_pid` seam so the worker-startup sweep covers them (previously only pool-spawned pairs were tracked).
- [ ] No new `docs/features/README.md` index entry needed — this extends an existing documented feature.

### Inline Documentation
- [ ] Docstrings on `PTYPool.register_pid`/`unregister_pid` explaining the crash-resume caller, the "route through the pool, not the file" rule, and that they are cross-thread-safe via `_pids_lock`.
- [ ] Comment at `_pids_lock` and the `_persist_pids` snapshot explaining the cross-thread (container session thread vs. pool spawn thread) mutation hazard it prevents, plus the deadlock-guard invariant (`_persist_pids()` is never called while `_pids_lock` is held; the lock is non-reentrant).
- [ ] Comment in `_resume_crashed_pty` at the registration call sites explaining: register after `spawn()` / before `write()` (live-PID-on-write-fail), the `closed_ok` gate on `on_pty_despawn` and why the drop lives in the outer `finally` (fires on every exit, including close-ok/write-fail `return None`), sweep-visibility, and the fail-silent contract.

## Success Criteria

- [ ] `_resume_crashed_pty` registers the resumed PTY's PID via `on_pty_spawn` and drops the dead PID via `on_pty_despawn` (acceptance criterion 1).
- [ ] `PTYPool` exposes public `register_pid(pid)` / `unregister_pid(pid)` that add/discard in `_spawned_pids` and persist to the registry.
- [ ] `BridgeAdapter` wires `on_pty_spawn=self._pool.register_pid` and `on_pty_despawn=self._pool.unregister_pid` into `Container`.
- [ ] New test `tests/unit/granite_container/test_crash_resume_pid_registration.py` drives the crash-resume path against a real `PTYPool` (or its `register_pid` bound method) and asserts the resumed PID appears in `pool.get_spawned_pids()` and the persisted registry — i.e. sweep-visible (acceptance criterion 2).
- [ ] **Closing-the-loop sweep test** (Concern 3): after registering a synthetic resumed PID (an integer that is not a real process), the test runs the actual worker-startup sweep `_kill_orphaned_pty_pids()` with `os.kill` monkeypatched to a collector, and asserts the registered PID was passed to `os.kill(pid, SIGKILL)`. This proves registration is not just registry membership but is actually consumed by the reaper. (The register-side is exercised via the real `_persist_pids` → `data/granite_pty_pids.json` round-trip so the sweep reads what the container wrote; use a tmp registry path to avoid touching the real file.)
- [ ] **`on_pty_despawn` gating test** (round-1 Concern 2): a crash-resume where `dead_pty.close(force=True)` raises must **keep** the dead PID registered (`on_pty_despawn` NOT called), while the close-success path drops it. Asserted by test.
- [ ] **Close-ok/write-fail despawn test** (round-2 Concern 2): a crash-resume where `close(force=True)` succeeds and then `new_pty.write(...)` raises (method `return None`s) must **still** drop the dead PID — `on_pty_despawn(dead_pid)` fired from the outer `finally`. Asserts the confirmed-dead PID is not stranded in the registry on the write-fail exit. Asserted by test.
- [ ] **Spawn-ok/write-fail test** (round-1 BLOCKER): a crash-resume where `new_pty.write(...)` raises after `spawn()` succeeds must still have registered the new PID (`on_pty_spawn` called before `write`), even though the method returns `None`. Asserted by test.
- [ ] A raising `on_pty_spawn` does not propagate out of `_resume_crashed_pty` (fail-silent) — asserted by test.
- [ ] Existing `test_crash_resume_settings.py` tests pass unchanged (no-callback path no-ops cleanly).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `bridge_adapter.py` references `register_pid` (Agent Integration wiring check).

## Team Orchestration

**Right-sizing note:** This is a Small-appetite ~3-method fix (two pool methods + one lock, two container callbacks + resume-registration edit, one adapter kwarg pair). The implementation is tightly coupled (the callbacks are meaningless without the pool methods, and the lock touches the same file), so it lands as one builder task rather than fanning out. Two roles total: one builder (implementation + tests + docs), one validator.

### Team Members

- **Builder (pid-registration)**
  - Name: pid-builder
  - Role: Implement the pool lock + public methods, container callbacks + crash-resume registration, adapter wiring, tests, and the docs update
  - Agent Type: builder
  - Domain: async/concurrency (cross-thread PID registry, `threading.Lock`)
  - Resume: true

- **Validator (pid-registration)**
  - Name: pid-validator
  - Role: Verify acceptance criteria and sweep-visibility end to end (including the closing-the-loop sweep test)
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See template. This plan uses `builder` and `validator`.

## Step by Step Tasks

### 1. Implement pool lock, public methods, container callbacks, and adapter wiring
- **Task ID**: build-implementation
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_pty_pool.py, test_crash_resume_settings.py, test_bridge_adapter.py
- **Assigned To**: pid-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- `PTYPool`: add `import threading`, `self._pids_lock = threading.Lock()` in `__init__`; guard every `_spawned_pids` **bare mutation** site (`pty_pool.py:262,520,555,627-629`, `clear_spawned_pids`) in its own `with self._pids_lock:` block; in `_persist_pids` snapshot `sorted(...)` under the lock and write JSON outside it, **keeping `except OSError` unchanged (do NOT broaden)**. **DEADLOCK GUARD (non-negotiable): `_persist_pids()` must never be called while `_pids_lock` is held** — the lock is non-reentrant and `_persist_pids` re-acquires it. At the add-then-persist spans (`pty_pool.py:550-556`, `620-630`) wrap only the `.add()` loop in the `with` block, then call `self._persist_pids()` as a separate un-indented statement after the block closes. Add public `register_pid(pid)` / `unregister_pid(pid)`: `with self._pids_lock: <add/discard>` then `self._persist_pids()` on the next line (outside the `with`), with docstrings.
- `Container`: add `on_pty_spawn` / `on_pty_despawn` ctor params (default `None`), store them. In `_resume_crashed_pty`: initialize `dead_pid` and `closed_ok = False` at the top of the outer `try`; capture `dead_pid` before `close(force=True)` and set `closed_ok = True` after it returns; call `on_pty_spawn(new_pty._child.pid)` **after `spawn()` and before `write()`**; put `if closed_ok: on_pty_despawn(dead_pid)` in the **outer `finally`** (beside `_crash_resume_in_flight = False`) so it fires on every exit — including the close-ok/write-fail `return None`. Both callbacks fail-silent with `logger.warning`, PID guarded by `getattr(getattr(pty,"_child",None),"pid",None)`.
- `BridgeAdapter`: pass `on_pty_spawn=self._pool.register_pid, on_pty_despawn=self._pool.unregister_pid` into the `Container(...)` construction (`bridge_adapter.py:633`).

### 2. Author tests
- **Task ID**: build-tests
- **Depends On**: build-implementation
- **Validates**: tests/unit/granite_container/test_crash_resume_pid_registration.py (create)
- **Assigned To**: pid-builder
- **Agent Type**: builder
- **Parallel**: false
- New `test_crash_resume_pid_registration.py`: drive `_resume_crashed_pty` with a fake dead PTY + fake driver exposing `_child.pid`, patching `PTYDriver` so no real `claude` spawns, wired to a real `PTYPool`'s `register_pid`/`unregister_pid` with a **tmp registry path**. Assert (a) resumed PID in `pool.get_spawned_pids()` and the persisted JSON; (b) **closing-the-loop**: `_kill_orphaned_pty_pids()` with `os.kill` monkeypatched to a collector passes the registered synthetic PID to SIGKILL (round-1 Concern 3); (c) **despawn gating**: close-failure keeps the dead PID, close-success drops it (round-1 Concern 2); (d) **spawn-ok/write-fail**: `write()` raising still registers the new PID (round-1 BLOCKER); (e) **close-ok/write-fail despawn**: close succeeds then `write()` raises → the dead PID is still dropped (`on_pty_despawn` fired from the `finally`) even though the method `return None`s (round-2 Concern 2); (f) a raising `on_pty_spawn` is fail-silent. Extend `test_pty_pool.py` for the new public methods (add-then-persist, discard-then-persist) and add a test that `register_pid` completes without hanging (deadlock guard: `_persist_pids` re-acquiring `_pids_lock` must not self-deadlock — the test simply calls `register_pid` and asserts it returns).

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-implementation
- **Assigned To**: pid-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` orphan-cleanup section per the Documentation checklist.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: pid-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the granite unit suite and all Verification commands; confirm every success criterion including the closing-the-loop sweep assertion, the despawn gate, the spawn-ok/write-fail registration, docs, and no regression in existing crash-resume/pool/adapter tests.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Granite unit tests pass | `pytest tests/unit/granite_container/ -n0 -q` | exit code 0 |
| New test present | `test -f tests/unit/granite_container/test_crash_resume_pid_registration.py` | exit code 0 |
| Closing-the-loop sweep test present | `grep -c "_kill_orphaned_pty_pids" tests/unit/granite_container/test_crash_resume_pid_registration.py` | output ≥ 1 |
| Pool exposes both public methods | `grep -cE "def (register\|unregister)_pid" agent/granite_container/pty_pool.py` | output == 2 |
| Pool has the thread lock | `grep -c "_pids_lock" agent/granite_container/pty_pool.py` | output ≥ 3 (init + guarded sites) |
| Adapter wires spawn callback | `grep -c "on_pty_spawn=self._pool.register_pid" agent/granite_container/bridge_adapter.py` | output == 1 |
| Adapter wires despawn callback | `grep -c "on_pty_despawn=self._pool.unregister_pid" agent/granite_container/bridge_adapter.py` | output == 1 |
| Container invokes both callbacks on resume | `grep -cE "self\._on_pty_(spawn\|despawn)\(" agent/granite_container/container.py` | output == 2 |
| No direct pool ref in container | `grep -cE "get_pty_pool\|PTYPool\(" agent/granite_container/container.py` | match count == 0 |
| No registry file write from container | `grep -c "granite_pty_pids" agent/granite_container/container.py` | match count == 0 |
| Lint clean | `python -m ruff check agent/granite_container/ tests/unit/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/ tests/unit/granite_container/` | exit code 0 |

## Critique Results

<!-- Revision 1 (2026-07-02): NEEDS REVISION findings addressed below. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Register-PID placement wrong: the raising call is `new_pty.write()` (1485), not `spawn()` (1484); spawn-ok/write-fail returns `None` with a live unregistered PID | Solution / Technical Approach / Flow / Data Flow | Register `on_pty_spawn(new_pid)` immediately after `spawn()` returns and BEFORE `write()`, inside the `try`; corrected the line-105 order-note. New test (d) covers spawn-ok/write-fail. |
| CONCERN 1 | critique | Cross-thread `_spawned_pids` mutation races `_persist_pids`'s `sorted()` → uncaught `RuntimeError: Set changed size during iteration` (only `OSError` caught) | Solution (thread-safety element) / Technical Approach / Race 1 | Added `threading.Lock`, guarded all bare-mutation sites and the `_persist_pids` snapshot. (Round-2 note: the round-1 `except`-broadening was reverted — the lock alone eliminates the `RuntimeError`; see the round-2 NIT row.) Resolves OQ2 (plain sync methods). |
| CONCERN 2 | critique | Unconditional `unregister_pid(dead_pid)` after swallowed `close` failure → alive-but-unregistered old PID | Technical Approach / Flow / Success Criteria | Track `closed_ok`; call `on_pty_despawn` only when close succeeded. New test (c) covers the gate. |
| CONCERN 3 | critique | Success criteria assert registry membership only; nothing drives the sweep | Success Criteria / Test Impact / Verification | Added closing-the-loop test: `_kill_orphaned_pty_pids()` with monkeypatched `os.kill` collector proves the registered synthetic PID is SIGKILLed. |
| CONCERN | critique | Over-orchestration (4 roles/7 tasks) vs. Small appetite | Team Orchestration / Step by Step Tasks | Right-sized to 2 roles (builder, validator), 4 tasks. |
| CONCERN | critique | Plan oversells #1820 `killpg` coordination; sweep reaps parent PID only | Desired outcome / Rabbit Holes | Corrected: `kill_orphans` does `os.kill` on the parent PID; #1820's `killpg` is on the graceful path. |
| CONCERN | critique | Risk 1 mislabels lingering dead PID "harmless" | Risk 1 | Reframed: real hazard is cross-worker PID reuse (SIGKILL of an innocent reused PID). |
| NIT | critique | Verification greps spawn-side only and substring-ambiguous | Verification | Tightened: exact-string/`grepE` patterns, added despawn-side + lock + sweep-test checks. |
| OQ1 | critique | Self-spawned-path coverage unresolved | Resolved Questions | Resolved: leave callbacks `None`; self-spawned path reaps via `_close_pair_and_reap` (#1816), no pool registry to back a sweep. |
| OQ2 | critique | Sync vs. async register method unresolved | Resolved Questions | Resolved by Concern 1 lock: plain sync, thread-safe from any caller. |

<!-- Revision 2 (2026-07-02): re-critique NEEDS REVISION — new findings introduced by revision 1, addressed below. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | re-critique | Non-reentrant lock deadlock: wrapping the contiguous `.add()`+`self._persist_pids()` spans (`pty_pool.py:550-556, 620-630`) in one `with self._pids_lock:` self-deadlocks, since `_persist_pids` re-acquires the same non-reentrant lock | Solution (deadlock-guard invariant) / Technical Approach / Step by Step Task 1 | Added named non-negotiable invariant: `_persist_pids()` is NEVER called while `_pids_lock` is held. Guard only the bare mutation; call `_persist_pids()` as a separate un-indented statement outside the lock. Stated in every touching instruction. Test (f)/pool-test asserts `register_pid` returns (no hang). |
| CONCERN | re-critique | Despawn-gating missed the close-ok/write-fail exit: `close` succeeds then `spawn()`/`write()` raises → `return None` (line 1488) before the post-swap despawn, stranding a confirmed-dead PID forever (contradicts Risk 1) | Data Flow / Solution / Technical Approach / Flow / Risk 1 / Success Criteria / Test Impact | Moved the drop to the outer `finally`: `if closed_ok: on_pty_despawn(dead_pid)` fires on every exit path. Added test (e): close-ok + `write()` raises → assert `on_pty_despawn(dead_pid)` still fired. |
| NIT | re-critique | Broadening `_persist_pids`'s `except OSError` → `except Exception` folds a separable change into a Small bugfix and silently swallows future bugs | Technical Approach / Failure Path Test Strategy / Race 1 / Step by Step Task 1 | Reverted: keep `except OSError`. The `_pids_lock` eliminates the `RuntimeError` at source, so no broadening is warranted. |

---

## Resolved Questions

Both open questions from the initial draft are now resolved and folded into the plan above.

1. **Self-spawned-path coverage — RESOLVED: leave `on_pty_spawn`/`on_pty_despawn` `None` on the self-spawned path; no coverage gap.** The self-spawned container path (tests, `run_ping_pong_test`, standalone CLI) has no `PTYPool`, so there is nothing to register into and the callbacks stay `None` (a no-op, matching the default). This is safe because the worker-startup sweep exists to reap orphans left by a **dead long-lived worker** that owned a pool. Self-spawned containers are ephemeral, in-process, and tear their PTYs down synchronously via `_close_pair_and_reap` (#1816) before the owning process exits — there is no worker-startup-sweep dependency for them because there is no persistent pool registry backing that path. Registering their PIDs into a pool registry they never share would be meaningless (and there is no pool object to call). The existing self-spawned reap is the correct and sufficient mechanism. The unit tests explicitly assert the `None`-callback path no-ops cleanly (see Empty/Invalid Input Handling).

2. **Sync vs. async `register_pid`/`unregister_pid` — RESOLVED: plain sync, guarded by `self._pids_lock`.** The crash-resume callbacks fire from the container's session thread, so the methods must be safe to call off the pool's event-loop thread. A `threading.Lock` (Concern 1 fix) makes plain sync methods thread-safe from any caller — this is simpler and more correct than an async variant, which would force the sync `_resume_crashed_pty` path to schedule onto the loop and reintroduce cross-thread hazards. No async variant is added.
