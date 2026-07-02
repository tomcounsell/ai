---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1851
last_comment_id:
---

# Granite crash-resume PID registration

## Problem

A granite session runs on a PM+Dev `claude` PTY pair leased from the `PTYPool`. When one of those PTYs crashes mid-run (pexpect EOF, no `turn_end`), `Container._resume_crashed_pty` spawns a **fresh** `PTYDriver` that resumes the dead session via `--resume <uuid>`. This fresh process has a brand-new OS PID.

The worker-startup orphan sweep (`_kill_orphaned_pty_pids`, run before the pool is built on every worker boot) reaps leaked granite `claude` processes by reading `data/granite_pty_pids.json` and SIGKILLing the listed PIDs. That registry is populated only by the pool's own spawn paths (`_spawn_session_pair`, `_spawn_slot`/`_respawn_slot`). The container's crash-resume spawn happens **outside** the pool, so the resumed PID is never registered.

**Current behavior:**
When a crash-resumed session's worker later dies without a clean teardown (crash, SIGKILL, machine sleep), the resumed `claude` process and its grandchildren survive the next worker's startup sweep as orphans, because their PID was never written to the registry the sweep reads. Over repeated crash-resume-then-worker-death cycles this leaks granite PTY processes.

**Desired outcome:**
The crash-resume PTY spawn registers its new PID in the pool's tracked/persisted PID set (and drops the dead PID it replaced), so the worker-startup sweep sees it and reaps it (and, once #1820's `killpg` teardown lands, its process group).

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
5. **Output (post-fix)**: `_resume_crashed_pty` invokes an injected `on_pty_spawn(new_pid)` callback (wired by `BridgeAdapter` to `pool.register_pid`) and `on_pty_despawn(dead_pid)` (→ `pool.unregister_pid`). The registry now lists the live resumed PID; the sweep reaps it.

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

- **`PTYPool.register_pid(pid)` / `unregister_pid(pid)`** (public): add/discard a PID in `_spawned_pids` and persist. Wraps the pattern the private spawn paths already use, so crash-resume can register through the pool's owned registry rather than writing the JSON file directly (avoids a second writer racing `_persist_pids`).
- **`Container` callbacks `on_pty_spawn` / `on_pty_despawn`** (`Callable[[int], None] | None`, default `None`): the established injection seam (mirrors `on_turn`, `on_pty_read`). `None` on the self-spawned/test/CLI path — no behavior change there (that path already reaps via `_close_pair_and_reap`).
- **`_resume_crashed_pty` registration**: after `new_pty.spawn()`, extract `new_pty._child.pid` and call `on_pty_spawn(pid)`; extract the dead PTY's PID before closing it and call `on_pty_despawn(dead_pid)`. Fail-silent (a raising callback must never crash the resume — mirrors the existing `on_turn` guard in the same method).
- **`BridgeAdapter` wiring**: pass `on_pty_spawn=self._pool.register_pid, on_pty_despawn=self._pool.unregister_pid` into the `Container(...)` construction at `bridge_adapter.py:633`.

### Flow

Crash detected → `_resume_crashed_pty` captures dead PID → closes dead PTY → `on_pty_despawn(dead_pid)` (pool drops it from registry) → spawns `new_pty` → `on_pty_spawn(new_pty._child.pid)` (pool adds + persists) → registry now sweep-visible → worker-startup sweep reaps the resumed PID on the next boot.

### Technical Approach

- Add the two public pool methods next to `get_spawned_pids`/`clear_spawned_pids`. `register_pid` does `self._spawned_pids.add(int(pid)); self._persist_pids()`; `unregister_pid` does `self._spawned_pids.discard(int(pid)); self._persist_pids()`. Both are sync (the pool's PID set is only touched from the event-loop thread; existing add/discard sites are already sync under a lock — these are called from the container's sync resume path, consistent with `_spawn_slot`'s pattern).
- Add `on_pty_spawn` / `on_pty_despawn` ctor params to `Container.__init__`, store as `self._on_pty_spawn` / `self._on_pty_despawn`.
- In `_resume_crashed_pty`: guard PID extraction with `getattr(getattr(pty, "_child", None), "pid", None)` (matches the pool's existing extraction and tolerates the fake drivers in existing tests that have no `_child`). Wrap each callback in try/except with a `logger.warning` (fail-silent, never crash resume).
- Order: `on_pty_despawn(dead_pid)` after capturing the dead PID but the register of the new PID must happen only on a successful spawn (inside the existing `try` before the return path). If the spawn raises, the method already returns `None` and no new PID exists to register — the dead PID drop is still correct because the dead PTY was closed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `on_pty_spawn`/`on_pty_despawn` call sites are wrapped in `try/except Exception` with `logger.warning` (mirroring the existing `on_turn` guard at `container.py:1498-1502`). Add a test asserting a raising `on_pty_spawn` does not propagate out of `_resume_crashed_pty` (resume still returns the new PTY; `crash_resume_in_flight()` is cleared).
- [ ] `register_pid`/`unregister_pid` persistence failure already flows through `_persist_pids`'s existing `except OSError: logger.warning` — no new swallow introduced.

### Empty/Invalid Input Handling
- [ ] Callbacks default `None`: `_resume_crashed_pty` must no-op the registration when the callback is `None` (self-spawned/CLI/test path). Covered by the existing `test_crash_resume_settings.py` tests, which pass no callback.
- [ ] `new_pty._child` absent (fake driver): `getattr(...)` yields `None`, registration is skipped without error. Assert the existing fake-driver tests still pass.

### Error State Rendering
- [ ] No user-visible output changes — this is process-lifecycle bookkeeping. N/A for user-facing error rendering.

## Test Impact

- [ ] `tests/unit/granite_container/test_crash_resume_settings.py::TestContainerResumeThreadsSettingsPath` — UPDATE (verify-only): its `_FakeDriver` has no `_child` and the `Container` is built with no `on_pty_spawn`, so the new registration path no-ops. Confirm both existing tests still pass unchanged; optionally extend `_FakeDriver` with a `_child.pid` to add positive-registration coverage here.
- [ ] `tests/unit/granite_container/test_pty_pool.py` — UPDATE: add coverage for `register_pid`/`unregister_pid` (add-then-persist, discard-then-persist) alongside the existing `get_spawned_pids`/`clear_spawned_pids` tests.
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` — UPDATE (verify-only): confirm the added `Container(...)` kwargs don't break the adapter's construction assertions; extend if it asserts the exact kwarg set.

New test file `tests/unit/granite_container/test_crash_resume_pid_registration.py` drives the acceptance criteria (see Success Criteria).

## Rabbit Holes

- **Do NOT implement `killpg`/process-group teardown here.** Grandchild reaping via `killpg` is #1820's territory. This fix only makes the resumed PID *sweep-visible*; the sweep's kill mechanism (currently `os.kill` on the parent PID) is out of scope.
- **Do NOT fix the graceful-release teardown gap.** After crash-resume swaps in `new_pty`, the pool's `slot.pty_pair` still references the dead PTY, so `_release_pair` never closes `new_pty` on a clean release. That is a real leak but is #1816/#1820 teardown territory — the worker-startup sweep (this fix) is its safety net, not its cure.
- **Do NOT give `Container` a direct `PTYPool` reference.** Keep the callback seam — a hard pool reference would break the container's test/CLI standalone path and invert the existing dependency direction (adapter owns pool, container is pool-agnostic).
- **Do NOT write `data/granite_pty_pids.json` from the container.** A second writer would race the pool's `_persist_pids`. Route through the pool's public methods only.

## Risks

### Risk 1: Double-registration or stale registry entries
**Impact:** The dead PID lingers in the registry after crash-resume, or the new PID is registered twice.
**Mitigation:** `_spawned_pids` is a `set` — `add` is idempotent, `discard` is a no-op on absent PIDs. `unregister_pid(dead_pid)` drops the replaced PID; a lingering dead PID would only cause a harmless `ProcessLookupError` (swallowed by `kill_orphans`) on the next sweep. Test asserts the registry contains the new PID and not the dead PID after a resume.

### Risk 2: Callback raises and re-wedges the crash path
**Impact:** A dropped `continue` nudge or an unhandled exception in the resume path re-wedges the exact path #1688 fixed.
**Mitigation:** Both callbacks are fail-silent (try/except + `logger.warning`), placed so registration failure never blocks the `continue` write or the PTY swap. Test asserts a raising `on_pty_spawn` still returns the new PTY.

## Race Conditions

### Race 1: Registry write vs. worker-startup sweep read
**Location:** `pty_pool.py` `_persist_pids` (writer) vs. `_kill_orphaned_pty_pids` (reader), across worker processes.
**Trigger:** A crash-resume registers a PID while a new worker boots and reads the registry.
**Data prerequisite:** The resumed PID must be persisted to `data/granite_pty_pids.json` before the *next* worker's sweep reads it.
**State prerequisite:** Cross-process: the old worker persists on resume; the new worker reads on boot. These are strictly ordered by the old worker dying before the new one starts — there is no concurrent same-process read/write.
**Mitigation:** Registration persists synchronously at resume time (same pattern as `_spawn_session_pair`). Within one process the PID set is only touched from the event-loop thread. Cross-process ordering is inherent (a worker only sweeps at startup, after the prior worker is gone). No new lock required; this matches the existing pool persistence model.

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
- [ ] Docstrings on `PTYPool.register_pid`/`unregister_pid` explaining the crash-resume caller and the "route through the pool, not the file" rule.
- [ ] Comment in `_resume_crashed_pty` at the registration call sites explaining sweep-visibility and the fail-silent contract.

## Success Criteria

- [ ] `_resume_crashed_pty` registers the resumed PTY's PID via `on_pty_spawn` and drops the dead PID via `on_pty_despawn` (acceptance criterion 1).
- [ ] `PTYPool` exposes public `register_pid(pid)` / `unregister_pid(pid)` that add/discard in `_spawned_pids` and persist to the registry.
- [ ] `BridgeAdapter` wires `on_pty_spawn=self._pool.register_pid` and `on_pty_despawn=self._pool.unregister_pid` into `Container`.
- [ ] New test `tests/unit/granite_container/test_crash_resume_pid_registration.py` drives the crash-resume path against a real `PTYPool` (or its `register_pid` bound method) and asserts the resumed PID appears in `pool.get_spawned_pids()` and the persisted registry — i.e. sweep-visible (acceptance criterion 2).
- [ ] A raising `on_pty_spawn` does not propagate out of `_resume_crashed_pty` (fail-silent) — asserted by test.
- [ ] Existing `test_crash_resume_settings.py` tests pass unchanged (no-callback path no-ops cleanly).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `bridge_adapter.py` references `register_pid` (Agent Integration wiring check).

## Team Orchestration

### Team Members

- **Builder (pid-registration)**
  - Name: pid-builder
  - Role: Implement pool public methods, container callbacks + wiring, and the crash-resume registration
  - Agent Type: builder
  - Domain: async/concurrency (cross-process PID registry, event-loop thread ownership)
  - Resume: true

- **Test-engineer (crash-resume-test)**
  - Name: resume-tester
  - Role: Author `test_crash_resume_pid_registration.py` and extend pool/adapter tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator (pid-registration)**
  - Name: pid-validator
  - Role: Verify acceptance criteria and sweep-visibility end to end
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See template. This plan uses `builder`, `test-engineer`, `validator`, `documentarian`.

## Step by Step Tasks

### 1. Add pool public PID registration methods
- **Task ID**: build-pool-methods
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_pty_pool.py
- **Assigned To**: pid-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `register_pid(pid: int)` and `unregister_pid(pid: int)` to `PTYPool` (add/discard in `_spawned_pids` + `_persist_pids()`), with docstrings.

### 2. Add Container callbacks and crash-resume registration
- **Task ID**: build-container-callbacks
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_crash_resume_settings.py
- **Assigned To**: pid-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: true
- Add `on_pty_spawn` / `on_pty_despawn` ctor params (default `None`) and store them.
- In `_resume_crashed_pty`: capture dead PID before close → `on_pty_despawn(dead_pid)`; after `new_pty.spawn()` → `on_pty_spawn(new_pty._child.pid)`. Both fail-silent with `logger.warning`, guarded by `getattr(getattr(pty,"_child",None),"pid",None)`.

### 3. Wire BridgeAdapter callbacks
- **Task ID**: build-adapter-wiring
- **Depends On**: build-pool-methods, build-container-callbacks
- **Validates**: tests/unit/granite_container/test_bridge_adapter.py
- **Assigned To**: pid-builder
- **Agent Type**: builder
- **Parallel**: false
- Pass `on_pty_spawn=self._pool.register_pid, on_pty_despawn=self._pool.unregister_pid` into the `Container(...)` construction (`bridge_adapter.py:633`).

### 4. Author acceptance test
- **Task ID**: build-tests
- **Depends On**: build-container-callbacks, build-pool-methods
- **Validates**: tests/unit/granite_container/test_crash_resume_pid_registration.py (create)
- **Assigned To**: resume-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Drive `_resume_crashed_pty` with a fake dead PTY + fake driver exposing `_child.pid`, using a real `PTYPool`'s `register_pid`/`unregister_pid` bound methods (patch `PTYDriver` so no real `claude` spawns). Assert the resumed PID is in `pool.get_spawned_pids()` and the persisted registry, and the dead PID was dropped. Add the raising-callback fail-silent test. Extend `test_pty_pool.py` for the new public methods.

### 5. Validation
- **Task ID**: validate-pid
- **Depends On**: build-adapter-wiring, build-tests
- **Assigned To**: pid-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the granite unit suite; verify all success criteria; confirm sweep-visibility assertion and no regression in existing crash-resume/pool/adapter tests.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pid
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` orphan-cleanup section per the Documentation checklist.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pid-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm every success criterion (including docs).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Granite unit tests pass | `pytest tests/unit/granite_container/ -n0 -q` | exit code 0 |
| New test present | `test -f tests/unit/granite_container/test_crash_resume_pid_registration.py` | exit code 0 |
| Pool exposes register_pid | `grep -c "def register_pid" agent/granite_container/pty_pool.py` | output contains 1 |
| Adapter wires register_pid | `grep -c "register_pid" agent/granite_container/bridge_adapter.py` | output > 0 |
| Container registers on resume | `grep -c "_on_pty_spawn" agent/granite_container/container.py` | output > 0 |
| No direct pool ref in container | `grep -c "get_pty_pool\|PTYPool(" agent/granite_container/container.py` | match count == 0 |
| No registry file write from container | `grep -c "granite_pty_pids" agent/granite_container/container.py` | match count == 0 |
| Lint clean | `python -m ruff check agent/granite_container/ tests/unit/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/ tests/unit/granite_container/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Should crash-resume PID registration also cover the **self-spawned** container path (tests, `run_ping_pong_test`), or is that path adequately covered by `_close_pair_and_reap`'s process-group teardown (#1816)? Current plan leaves it `None` there (no pool exists), relying on the existing self-spawned reap. Confirm that is acceptable.
2. Is a synchronous `register_pid`/`unregister_pid` acceptable given `_resume_crashed_pty` runs on the event-loop thread, matching the existing sync `_spawned_pids` mutation sites — or is an async variant preferred for consistency with `acquire_pair`? Plan assumes sync is correct (the private mutation sites are already sync under a per-slot lock).
