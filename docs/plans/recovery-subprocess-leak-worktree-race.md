---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1938
last_comment_id:
revision_applied: true
---

# Session recovery/failure leaks the live claude -p subprocess and deletes its worktree while it runs

## Problem

When the worker decides a running session is stuck (`no_progress` / progress-deadline), it "recovers" it: the AgentSession transitions `running → pending` and is picked up again with a fresh `claude -p` subprocess; after `MAX_RECOVERY_ATTEMPTS=2` it transitions to `failed` and the executor's `finally` block removes the synthetic-slug worktree and branch. On 2026-07-07 05:38–06:14 UTC (first two worker-executed sessions after the headless cutover, `sdlc-local-1933`/`sdlc-local-1934`), two defects fired:

1. **Recovery/failure never terminated the live `claude -p` subprocess.** `sdlc-local-1933`'s attempt-2 PM (pid 52408) survived 28 minutes past its `running → failed` transition — it self-repaired its deleted worktree, ran the SDLC pipeline unsupervised, and pushed a commit to main from a process the system considered dead. The leak was invisible to the hourly orphan reaper because the subprocess stayed parented to the live worker (PPID = worker, not 1). It was killed by hand.
2. **Failure cleanup deleted the worktree under the still-alive subprocess.** At 05:46:32 (same second as `sdlc-local-1934`'s hard-fail) the executor removed `.worktrees/dev-dd700d10/` while that session's PM was alive (Stop hook fired at 05:50:40). Because the harness pins cwd for the process lifetime, every subsequent Bash/Write/Edit died with ENOENT ("can't open file .worktrees/dev-dd700d10/.claude/hooks/validators/...") and the PM wedged.

**Current behavior:** The recovery/failure kill machinery exists (PR #1557 / #1537) but reads the wrong PID field, so it is a no-op for every worker-executed eng session. It "confirms death" of a subprocess it never signalled, then requeues or fails-and-cleans-up while the real process keeps running.

**Desired outcome:** Recovery and failure transitions positively terminate the session's subprocess (process group) and confirm exit BEFORE re-queueing or cleaning up. Worktree/branch cleanup runs only after the subprocess is confirmed dead. No process the system considers failed can keep executing; no cleanup mutates the filesystem under a live child.

## Freshness Check

**Baseline commit:** f7bc0f5e (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-07T08:35:02Z
**Disposition:** Minor drift — the issue's premise ("recovery/failure never terminate the subprocess", root cause "uncertain") is confirmed as a live bug, and recon sharpened the root cause from "uncertain ownership" to a specific PID-field mismatch. No line-number drift; no landed fix.

**File:line references re-verified (against f7bc0f5e):**
- `agent/session_health.py:2292-2299` — recovery kill path reads `getattr(entry, "claude_pid", None)`. Confirmed present. This is the crux.
- `agent/session_health.py:1533` — `_confirm_subprocess_dead(None)` returns `confirmed_dead=True`. Confirmed.
- `agent/session_runner/runner.py:522` — runner persists subprocess PID to `pm_pid` only. Confirmed.
- `agent/session_executor.py:1471` — the `claude_pid = pid` closure (`_on_sdk_started`) is wired to `BossMessenger` (line 1524), which the SessionRunner path does not use. Confirmed.
- `agent/session_executor.py:1739` — "All session types route through the headless session runner." Confirmed — all worker-executed sessions take the runner path.
- `agent/session_executor.py:2308-2327` — synthetic-slug worktree cleanup in the `finally` block, gated only on the `^dev-[0-9a-f]{8}$` slug shape. Confirmed.
- `agent/worktree_manager.py:419-502` — `worktree_busy_check` gates on AgentSession DB status, not OS-process liveness. Confirmed.
- `agent/agent_session_queue.py:1796-1799` — comment "the harness kills its own `claude -p` child on cancellation; the worker-startup orphan sweep reaps any survivor." Confirmed present and confirmed FALSE: `agent/sdk_client.py:2752-3083` (`_run_harness_subprocess`) has no `try/finally` terminating `proc` on `CancelledError`.
- `models/session_lifecycle.py:479` — `finalize_session` NULLs `claude_pid` on terminal transition; `pm_pid` is NOT cleared. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1935 — still OPEN. Companion issue: it stops the FALSE `no_progress` triggers; this issue stops ANY trigger (false or genuine) from leaking the live process. Independent, complementary.
- PR #1557 / #1537 — merged 2026-06-03. Added `_confirm_subprocess_dead` + kill-before-requeue. Correct machinery, aimed at `claude_pid`, which the later #1924 headless cutover left permanently `None` for these sessions.

**Commits on main since issue was filed (touching referenced files):** None material — `git log` since 2026-07-07T08:35Z shows only the #1937/#1940 interrupt-announcement work and a dep bump (f7bc0f5e), neither touching the recovery/kill/worktree paths.

**Active plans in `docs/plans/` overlapping this area:** `headless-runner-zombie-liveness.md` is the plan for companion #1935 (false-trigger side). No overlap in the fix surface (that plan changes the `no_progress` classifier; this plan changes the kill/cleanup path). Coordinate era, not code.

**Notes:** The recon-confirmed root cause is a PID-field mismatch, not a missing mechanism. This reframes the fix from "build subprocess termination" to "point the existing termination at the real PID and make process-group + ordering guarantees explicit."

## Prior Art

- **PR #1557 (#1537)**: "Liveness recovery confirms subprocess death before requeue" — added `_confirm_subprocess_dead` (SIGTERM→SIGKILL escalation) and made `_apply_recovery_transition` confirm death before `running→pending`, escalating to `failed` when a subprocess survives. Correct design; it keys on `claude_pid`. This plan fixes the field it reads and upgrades single-PID kill to process-group kill.
- **#1271**: the cross-process orphan reaper (`_reap_orphan_session_processes`) — kills leaked claude/MCP processes, gated on `PPID==1` + heartbeat, resolving the owning session via `find_by_claude_pid`. Cannot catch worker-parented leaks (PPID = worker) and loses its mapping when `finalize_session` NULLs `claude_pid`.
- **#1357 / #1246**: `BackgroundTask._watchdog` cancels the work task when its `working_dir` vanishes mid-run. This is the safety net that turns a deleted-worktree into a task cancel — it is downstream of the bug, not a fix for it.
- **#1924**: the headless session-runner cutover that routed all worker sessions through `SessionRunner` and, as a side effect, moved the subprocess PID from `claude_pid` to `pm_pid` — the change that silently defeated #1537.
- No closed issue previously targeted this specific leak. No prior failed fix for this exact defect.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1557 (#1537) | Confirm subprocess death (SIGTERM→SIGKILL on `claude_pid`) before requeue; escalate to `failed` if it survives | Keys on `claude_pid`. The later #1924 cutover records the runner subprocess PID in `pm_pid` and never populates `claude_pid`, so `_confirm_subprocess_dead(None)` returns `confirmed_dead=True` — the guard is a no-op for every worker-executed eng session. |
| #1924 (headless cutover) | Route all worker sessions through `SessionRunner`; persist PID to `pm_pid` | Did not re-wire the #1537 kill path (still reading `claude_pid`) or the #1271 reaper (`find_by_claude_pid`). Left the subprocess PID in a field the safety machinery does not read. |
| `agent_session_queue.py:1796-1799` comment/assumption | "the harness kills its own `claude -p` child on cancellation" | `_run_harness_subprocess` has no `try/finally` terminating `proc` on `CancelledError` — a cancelled turn leaks the subprocess, parented to the live worker (so the PPID==1 startup sweep never reaps it). |

**Root cause pattern:** The subprocess-lifecycle safety machinery (kill-before-requeue, orphan reaper) was built against `claude_pid` and a "cancellation terminates the child" assumption. The headless cutover moved the ground truth (PID now in `pm_pid`) and the assumption was never true for this harness. The fixes each addressed the layer they owned without re-pointing the safety machinery at the new source of truth.

## Data Flow

1. **Trigger** — either the queue supervisor's progress-deadline watcher (`agent_session_queue.py:1762+`) or the periodic health-check loop (`session_health.py:_agent_session_health_check`) classifies a running session as stuck (`_has_progress`/`_should_kill_no_progress`).
2. **Recovery decision** — both route to `_apply_recovery_transition` (`session_health.py:2055`). It calls `_confirm_subprocess_dead(getattr(entry, "claude_pid", None), ...)` (`:2292`). **Today `claude_pid is None` → vacuous `confirmed_dead=True` (`:1533`).**
3. **Transition** — `running → pending` (`:2576`, requeue) or `running → failed` (`:2412` after MAX attempts, or `:2468` on non-confirmed death). `finalize_session` NULLs `claude_pid` (already None).
4. **Task teardown** — the queue supervisor path then calls `exec_task.cancel()` (`agent_session_queue.py`), running `_execute_agent_session`'s `finally` (`session_executor.py:2292-2332`): synthetic-slug worktree + branch removal via `cleanup_after_merge` → `remove_worktree` (`worktree_manager.py:978`). The cancel propagates into `_run_harness_subprocess` but **does not terminate the OS subprocess** (no cleanup) — it survives, still holding the just-deleted worktree as cwd.
5. **Output** — (defect 1) requeue path: a second subprocess spawns alongside the survivor. (defect 2) fail path: the survivor's next Bash/Write/Edit hits ENOENT under the removed cwd and wedges. Both leaks are invisible to the reaper (PPID = worker).

## Architectural Impact

- **New dependencies:** none. **No new persisted fields, no migration** (D2 reads the existing `pm_pid`).
- **Interface changes:** `_confirm_subprocess_dead` gains process-group awareness (`os.killpg` on the leader pid, since `pgid == pid` under `start_new_session=True`) and a zombie-safe confirm-exit (`os.waitpid(WNOHANG)`/ECHILD). `_apply_recovery_transition` resolves `pm_pid` instead of `claude_pid`. The executor `finally` gains a `pm_pid` liveness guard. Minimal signature growth.
- **Coupling:** unchanged field model — `pm_pid` (already the runner's spawn-written, survive-terminal PID) becomes the single confirm target for both the kill path and the executor cleanup probe. `claude_pid` reaper/dashboard semantics stay exactly as-is.
- **Data ownership:** the runner (`agent/session_runner/`) is already the authoritative writer of `pm_pid` at spawn; the health check and executor cleanup are readers. No ownership change.
- **Reversibility:** high — all changes are localized to the kill/cleanup/harness-cancel paths (no spawn-side change under D2) and are guarded by tests.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in, code reviewer (async/subprocess-lifecycle domain)

**Interactions:**
- PM check-ins: 1 (both open questions are resolved: D2 reads `pm_pid`; the reaper backstop is a no-go)
- Review rounds: 1 (correctness of the kill ordering, process-group + self-pgrp guard, and zombie-safe confirm)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal to `agent/` and `models/`, exercised by unit tests with monkeypatched `os.kill`/`os.killpg`.

## Solution

### Key Elements

- **Read the survive-terminal PID (no new state):** the kill path and the executor-`finally` probe both resolve the subprocess via `pm_pid` — the field the runner already writes at spawn and `finalize_session` does NOT null. This closes the `claude_pid`-is-`None`-at-cleanup gap without a new field or migration.
- **Process-group kill (recovery/failure side):** `_confirm_subprocess_dead` signals the process **group** via `os.killpg(pm_pid, sig)` (the runner spawns with `start_new_session=True`, so `pgid == pid` and the `claude -p` + its MCP/subagent children share one group), guarded by a self-pgrp break-glass (`pid != os.getpgrp()`), and confirms the group leader is gone with a zombie-safe `os.waitpid(WNOHANG)` / ECHILD check before falling back to `os.kill(pid, 0)`.
- **Explicit ordering guarantee:** worktree/branch cleanup is gated on OS-process-confirmed-dead resolved from `pm_pid`, not on a DB-status timing coincidence. Cleanup refuses (preserves the worktree, increments a counter) when `pm_pid` is still alive.
- **Harness cancel cleanup (defense in depth):** `_run_harness_subprocess` terminates its `proc` on `CancelledError` in a `finally`, so a cancelled turn cannot leak the child even on a path that bypasses the confirm-dead call.
- **Terminal-with-live-subprocess counter:** the hourly sweep emits a prod counter when a terminal session still has a live subprocess — the detection signal the original 28-minute leak lacked. The active reaper backstop is an explicit no-go (see No-Gos).

### Flow

Health/deadline check flags stuck session → resolve the session's subprocess pid from `pm_pid` → SIGTERM→SIGKILL the process group (`os.killpg`, self-pgrp guarded) and confirm the leader is gone (zombie-safe waitpid/ECHILD) → only then: `running→pending` (requeue) OR `running→failed` + worktree/branch cleanup (which itself re-probes `pm_pid` liveness and refuses to delete under a live process) → hourly sweep emits the `terminal-with-live-subprocess` counter for any residual leak.

### Technical Approach

**Decision D2 (adopted, per critique): read the survive-terminal `pm_pid` field the runner already writes — no new state.** The runner spawns the `claude -p` child with `start_new_session=True` (`role_driver.py:397`), so the child is its own process-group leader: **`pgid == pid`**. The runner already persists that pid to `pm_pid` at spawn (`runner._on_turn_spawn`, `runner.py:522`), and `finalize_session` NULLs only `claude_pid`, so `pm_pid` survives terminal transitions and is a live confirm target at cleanup time. The kill path and the executor-`finally` probe both resolve the subprocess via `getattr(entry_or_session, "pm_pid", None)` and signal the process group with `os.killpg(pm_pid, sig)`. This adds **zero new persisted fields**, needs **no migration**, and keeps the #1271 reaper + dashboard `claude_pid` semantics untouched (Risk 2 evaporates). D1 (repopulate `claude_pid` + new persisted `claude_pgid`) is rejected: it dragged the reaper and dashboard into scope and required a migration for state that `pm_pid` already carries. Because `pgid == pid` holds **only** under `start_new_session=True`, the code asserts that invariant in a comment at the killpg call site.

- **Kill path:** upgrade `_confirm_subprocess_dead` (`session_health.py:1490`) to signal the process **group**: `if pid and pid != os.getpgrp(): os.killpg(pid, sig)` (pid doubles as pgid under `start_new_session=True`), wrapped in `try/except ProcessLookupError` (== already dead). The `pid != os.getpgrp()` self-pgrp guard is the operator break-glass so the worker can never signal its own group. Keep the existing SIGTERM→grace→SIGKILL escalation and the `run_in_executor` offload. `_apply_recovery_transition` (`session_health.py:2292`) resolves `pm_pid` (not `claude_pid`) and passes it as the kill target.
- **Zombie-safe confirm-exit:** the subprocess is a direct child of the worker (PPID==worker), so after SIGKILL it becomes a zombie and `os.kill(pid, 0)` keeps returning success until the child is reaped — which would burn the full confirm timeout and false-escalate. In `_is_dead`, first attempt `wpid, _ = os.waitpid(pid, os.WNOHANG)` guarded for `ChildProcessError`/`OSError` with `errno.ECHILD`; return `True` on `wpid == pid` (reaped now) or `ECHILD` (asyncio's child watcher already reaped it), then fall back to the `os.kill(pid, 0)` existence probe. Document that asyncio's child watcher may own reaping of `proc`.
- **Ordering (defect 2 fix):** the queue-supervisor path already calls `_apply_recovery_transition` (which confirms death) BEFORE `exec_task.cancel()`. Make the guarantee explicit and defensive in the executor `finally` synthetic-slug cleanup (`session_executor.py:2308`): resolve `pm_pid = getattr(entry_or_session, "pm_pid", None)` — **never `claude_pid`, which `finalize_session` has already NULLed by this point** — and guard `cleanup_after_merge` with `if pm_pid and _pid_alive(pm_pid): log + increment counter; return` (fail-safe = preserve the worktree, do not delete under a live process). Do **not** thread a pid through `worktree_busy_check` (`worktree_manager.py:419`): it serves other callers and would gain a wider signature for one caller, and the queue path already confirms death upstream — a cheap executor-side `_pid_alive` probe is sufficient.
- **Harness cancel cleanup:** wrap the `_run_harness_subprocess` body (`sdk_client.py:2752-3083`) so a `CancelledError` (or any exit) runs `proc.terminate()` → bounded `await proc.wait()` → `proc.kill()` in a `finally`. Delete the false comment at `agent_session_queue.py:1796-1799` and replace it with the accurate invariant (per NO LEGACY CODE TOLERANCE).
- **Terminal-with-live-subprocess counter (monitoring, replaces the reaper backstop):** the active reaper backstop is a **no-go** (see No-Gos — the survivor is PPID==worker, invisible to the PPID==1 reaper, and relaxing that gate risks killing live worker children). In its place, emit a `{project_key}:session-health:terminal-with-live-subprocess` counter from the hourly health/reaper sweep: resolve `pm_pid`, probe `os.kill(pm_pid, 0)`, gate on terminal status + a grace window, and increment when a terminal session still has a live subprocess. This is the prod signal the original 28-minute leak lacked. The incident-regression test asserts this counter goes nonzero on the leak scenario.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_run_harness_subprocess` new `finally` must not swallow the terminate/wait errors silently — assert a `logger.debug/warning` fires when `proc.wait()` times out and SIGKILL is used. Test asserts observable behavior, not `pass`.
- [ ] `_confirm_subprocess_dead` `PermissionError`/`OSError` branches already return `confirmed_dead=False`; extend tests to the `killpg` variants (including `ProcessLookupError` on `killpg` == confirmed-dead, and the `ECHILD` `waitpid` branch == confirmed-dead).

### Empty/Invalid Input Handling
- [ ] `_confirm_subprocess_dead` with `pid=None` must still return `confirmed_dead=True` only when there is genuinely nothing to kill — but the whole point of this fix is that runner sessions now resolve `pm_pid`, so add a regression test that a runner-shaped session (`pm_pid` set) never reaches the `None` short-circuit.
- [ ] Worktree cleanup with a still-alive `pm_pid` must NOT delete the directory — assert the executor-side guard refuses (preserves) and increments the counter when `os.kill(pm_pid, 0)` succeeds.

### Error State Rendering
- [ ] Not user-facing. The observable "error state" is the log/counter surface: assert the `{project_key}:session-health:*` kill counters increment on the real-PID kill path (they currently never fire for runner sessions).

## Test Impact

- [ ] `tests/unit/test_session_health_subprocess_kill.py::test_none_pid_returns_confirmed_no_signal` — UPDATE: keep as a unit of `_confirm_subprocess_dead` semantics, but it must no longer stand in for real-session behavior; add a sibling asserting runner sessions (`pm_pid` set) never hit the None path.
- [ ] `tests/unit/test_session_health_subprocess_kill.py::test_no_pid_recorded_requeues_normally` (`:305`) — REPLACE: this test encodes the BUG (no pid → requeue without killing). Rewrite so a runner-shaped session resolves `pm_pid`, kills the group (`os.killpg`), confirms exit, then requeues.
- [ ] `tests/unit/test_session_health_subprocess_kill.py` (`_make_entry`, `:202`) — UPDATE: the fixture builds entries with `claude_pid=` only; add `pm_pid=` so it models a runner session (no new field — `pgid == pid`).
- [ ] `tests/unit/test_session_health_subprocess_kill.py::test_subprocess_survives_escalates_to_failed` / `test_subprocess_confirmed_dead_requeues_to_pending` — UPDATE: assert process-group signalling (`os.killpg`, self-pgrp guarded) rather than single `os.kill`; add a zombie case (SIGKILL'd-but-unreaped child → `confirmed_dead=True` within timeout via `waitpid`/ECHILD).
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: recovery/finalization assertions must reflect that confirm-dead now actually signals for runner sessions via `pm_pid`.
- [ ] `tests/unit/test_worktree_manager.py` — VERIFY unaffected: D2 does NOT widen `worktree_busy_check`; the liveness guard lives executor-side. Confirm no `worktree_busy_check` signature change is required.
- [ ] `tests/unit/test_never_started_recovery.py`, `tests/unit/test_session_health_tool_timeout.py` — UPDATE if their entry fixtures assume `claude_pid`-only; audit during build.
- [ ] `tests/unit/test_messenger_callbacks.py::TestMessengerArchitecturalBoundary` — VERIFY unaffected: no runner spawn-record change in D2 (the runner already writes `pm_pid`); the messenger ORM-free boundary is untouched.
- [ ] NEW: incident-regression test (executor cleanup) — session in `failed` status with `claude_pid=None` and `pm_pid=<live>` asserts `cleanup_after_merge`/`remove_worktree` does NOT delete the worktree and increments the `terminal-with-live-subprocess` counter (nonzero).

## Rabbit Holes

- **Rewriting the runner into a standalone `kill()`/`shutdown()` API the health check calls in-process.** The health check runs in a different task and cannot hold the live `_TurnHandle`; a cross-task API needs the persisted PID/pgid anyway. Signal the persisted identity — do not build a new IPC/handle-passing layer.
- **Tracking PID generations to defeat PID reuse.** #1537 already accepted the sub-second-window PID-reuse residual risk. Do not add generation counters; stay consistent with the existing reaper assumptions.
- **Refactoring the two recovery producers (queue supervisor vs health-check loop) into one.** Tempting but out of scope — both already funnel through `_apply_recovery_transition`; fix the shared helper, leave the producers.
- **Broadening `pm_pid`/`claude_pid`/`harness_pid` semantics beyond this fix.** Three PID fields exist for real reasons (dashboard liveness, reaper, heartbeat). Unify only the confirm-target read, do not collapse all three.

## Risks

### Risk 1: Process-group kill hits a group it shouldn't
**Impact:** `os.killpg` on a wrong/reused pgid could signal unrelated worker children — worst case, the worker's own group.
**Mitigation:** the kill target is the child's own PID, which equals its pgid because the runner spawns with `start_new_session=True` (`role_driver.py:397`) — the child is its own group leader, never sharing the worker's group. The self-pgrp break-glass `if pid != os.getpgrp()` refuses to signal the worker's group even if a stale PID were somehow reused. Same sub-second PID-reuse window as #1537; guarded by tests that assert the exact pid signalled and that the self-pgrp guard blocks `os.getpgrp()`.

### Risk 2 (retired under D2): no `claude_pid` repopulation
D2 reads the existing `pm_pid` and never repopulates `claude_pid` for runner sessions, so the #1271 reaper and dashboard `claude_pid` consumers see no behavior change. The original D1 risk (dragging the reaper/dashboard into scope) is eliminated by design — there is nothing to audit here.

### Risk 3: The harness `finally` terminate slows down normal turn completion
**Impact:** an added `proc.terminate()`/`wait()` in the hot path could add latency on every turn.
**Mitigation:** the `finally` only force-terminates when the process is still alive at exit (normal completion already drained `proc.communicate()`); on the happy path `proc` is already exited and terminate is a no-op ProcessLookupError. Bounded `wait()` timeout.

### Risk 4: Ordering assertion in worktree cleanup deadlocks on a genuinely-hung SIGKILL-immune process
**Impact:** if a PID cannot be killed (uninterruptible sleep), gating cleanup on confirmed-death could block the executor `finally`.
**Mitigation:** bounded confirm timeout (reuse `SUBPROCESS_KILL_TIMEOUT`); on non-confirmed death, do NOT delete the worktree, log + increment a counter, and leave the worktree for the reaper/next-startup sweep (fail safe = preserve, not delete).

## Race Conditions

### Race 1: Requeue spawns a second subprocess before the first is confirmed dead
**Location:** `agent/session_health.py:2292-2576` (confirm-dead → transition `running→pending`), then the worker picks up the `pending` row.
**Trigger:** recovery fires while the old subprocess is alive; if confirm-dead is a no-op, the requeue races a live process.
**Data prerequisite:** the session's `pm_pid` must be persisted by the runner BEFORE any recovery can read it (`_on_turn_spawn` writes it at spawn, before the turn await — same Race-2 ordering #1924 already established). No new field is needed; `pm_pid` already carries this.
**State prerequisite:** `_confirm_subprocess_dead` must return `confirmed_dead=True` only after the group leader is actually gone (zombie-safe: `waitpid`/ECHILD, then `os.kill(pid,0)`).
**Mitigation:** confirm-dead (process-group SIGTERM→SIGKILL + zombie-safe leader-gone poll) completes before the `running→pending` transition; the requeue cannot proceed until the old group is confirmed dead. This is exactly the #1537 invariant, now pointed at `pm_pid`.

### Race 2: Worktree deleted between confirm-dead and the harness's last filesystem op
**Location:** `agent/session_executor.py:2308-2327` (cleanup) vs the live subprocess's cwd usage.
**Trigger:** cleanup runs while the subprocess still has the worktree as cwd.
**Data prerequisite:** subprocess confirmed exited (Race 1) before `remove_worktree` runs.
**State prerequisite:** no process has the worktree as cwd at removal time.
**Mitigation:** gate cleanup on confirmed-death; the executor-side cleanup guard probes `pm_pid` liveness (`os.kill(pm_pid, 0)`) before deleting and preserves the worktree if the process is alive. No change to `worktree_busy_check` (D2 keeps the probe executor-side, not threaded through the shared helper).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1935] Fixing the FALSE `no_progress` classification that triggered these paths (healthy toolless-but-streaming turns misclassified as stuck). Tracked and planned separately in `docs/plans/headless-runner-zombie-liveness.md`. This plan handles what any trigger (false or genuine) does to the live process, independent of #1935.
- Collapsing the three PID fields (`pm_pid`, `claude_pid`, `harness_pid`) into one. Only the confirm-target read is unified here; a broader field-model cleanup is not required to close the leak and would inflate blast radius.
- **An active orphan-reaper backstop for worker-parented survivors.** Explicit no-go (per critique). The survivor is PPID==worker, so the existing PPID==1 orphan reaper (`_reap_orphan_session_processes`, #1271) structurally cannot see it, and relaxing that gate to sweep worker-parented processes risks killing live worker children. The primary fix (kill-before-requeue on the real PID + confirmed-death-gated cleanup + harness cancel `finally`) closes the leak at its source, and the incident-regression test is the ship gate. The residual detection need is met by the passive `terminal-with-live-subprocess` counter (Technical Approach), not an active killer.

## Update System

No update system changes required — the fix is internal worker/agent logic. D2 reads the existing `pm_pid` field, so there is **no new persisted AgentSession field and no `scripts/update/migrations.py` entry** (the D1 migration is dropped). No new dependencies to propagate.

## Agent Integration

No agent integration required — this is a worker-internal subprocess-lifecycle fix. No new CLI entry point, no MCP surface, no `bridge/telegram_bridge.py` change. The behavior is exercised by the worker's health-check and executor paths and verified by unit tests, not by an agent-invokable tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` — add a "Subprocess lifecycle on recovery/failure" subsection documenting: `pm_pid` is the survive-terminal confirm target the runner writes at spawn; recovery/failure signals the process group (`os.killpg`, `pgid == pid` under `start_new_session=True`, self-pgrp guarded) and confirms exit (zombie-safe `waitpid`) before requeue/cleanup; worktree cleanup re-probes `pm_pid` liveness and is gated on confirmed-death.
- [ ] Update `docs/features/bridge-self-healing.md` (or the recovery/reaper doc) — note the reaper-backstop **no-go** (worker-parented survivors are invisible to the PPID==1 reaper; relaxing the gate risks live worker children) and the `terminal-with-live-subprocess` counter that replaces it as the passive detection signal.
- [ ] Verify the `docs/features/README.md` index entries still describe these accurately.

### Inline Documentation
- [ ] Replace the false comment at `agent/agent_session_queue.py:1796-1799` with the accurate cancel/terminate invariant.
- [ ] Docstring on `_confirm_subprocess_dead` updated for process-group semantics, the `pm_pid` identity it now receives, the `pgid == pid` (`start_new_session=True`) assertion, and the zombie-safe `waitpid` confirm-exit.

## Success Criteria

- [ ] After a `no_progress` recovery, exactly one `claude -p` subprocess exists for the session — the old process group is confirmed exited before respawn (test with monkeypatched `os.killpg`/`os.kill`).
- [ ] After `running → failed`, the session's subprocess (resolved from `pm_pid`, since `claude_pid` is already `None`) is confirmed exited before worktree/branch cleanup runs; cleanup preserves the worktree when `pm_pid` is still alive (test asserts the failed-status + `claude_pid=None` + `pm_pid=live` case leaves the worktree in place).
- [ ] A regression test covering the incident: a kill decision while the subprocess is alive produces no ENOENT-wedged survivor and no ghost pipeline (worktree not removed under a live `pm_pid`; requeue/fail only after confirmed death; `terminal-with-live-subprocess` counter goes nonzero).
- [ ] A runner-shaped session never reaches the `_confirm_subprocess_dead(None)` short-circuit — `_apply_recovery_transition` resolves `pm_pid`.
- [ ] A SIGKILL'd-but-unreaped (zombie) child returns `confirmed_dead=True` within the timeout (via `waitpid(WNOHANG)`/ECHILD), not after burning the full grace window.
- [ ] `_run_harness_subprocess` terminates its `proc` on `CancelledError` (test cancels a turn mid-stream, asserts the child is signalled).
- [ ] The reaper backstop is recorded as an explicit no-go; the `terminal-with-live-subprocess` counter is the passive detection signal in its place.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `_apply_recovery_transition` reads `pm_pid`, not bare `getattr(entry, "claude_pid", None)`, as the kill target.
- [ ] The false "harness kills its own child on cancellation" comment is removed.

## Team Orchestration

The lead agent orchestrates; it deploys builders/validators and coordinates.

### Team Members

- **Builder (kill-path)**
  - Name: kill-path-builder
  - Role: `_apply_recovery_transition` reads `pm_pid`; process-group + zombie-safe confirm-dead in `_confirm_subprocess_dead`; `terminal-with-live-subprocess` counter in the hourly sweep
  - Agent Type: builder
  - Domain: async/subprocess-lifecycle
  - Resume: true

- **Builder (ordering-and-harness)**
  - Name: ordering-builder
  - Role: executor-`finally` `pm_pid`-liveness cleanup guard + `_run_harness_subprocess` cancel `finally` + delete/correct the false `agent_session_queue.py` comment
  - Agent Type: builder
  - Domain: async/subprocess-lifecycle
  - Resume: true

- **Validator**
  - Name: leak-validator
  - Role: verify all success criteria, especially the incident-regression test and no-double-subprocess
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: headless-session-runner + self-healing docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Kill path: read `pm_pid` + process-group + zombie-safe confirm-dead + counter
- **Task ID**: build-kill-path
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_subprocess_kill.py, tests/unit/test_health_check_recovery_finalization.py
- **Assigned To**: kill-path-builder
- **Agent Type**: builder
- **Domain**: async/subprocess-lifecycle
- **Parallel**: true
- Re-point `_apply_recovery_transition` (`session_health.py:2292`) to resolve `pm_pid` (not `claude_pid`) as the kill target.
- Upgrade `_confirm_subprocess_dead`: `os.killpg(pid, sig)` guarded by `pid != os.getpgrp()` with `os.kill(pid, sig)` fallback (pgid==pid under `start_new_session=True`; assert in a comment), wrapped in `try/except ProcessLookupError`. Add the zombie-safe `os.waitpid(pid, WNOHANG)`/ECHILD confirm in `_is_dead` before the `os.kill(pid, 0)` probe.
- Emit the `{project_key}:session-health:terminal-with-live-subprocess` counter from the hourly sweep (resolve `pm_pid`, probe `os.kill(pm_pid, 0)`, gate on terminal status + grace).

### 2. Ordering guarantee + harness cancel cleanup
- **Task ID**: build-ordering
- **Depends On**: none
- **Validates**: tests/unit/test_worktree_manager.py, tests/unit/test_session_health_subprocess_kill.py
- **Assigned To**: ordering-builder
- **Agent Type**: builder
- **Domain**: async/subprocess-lifecycle
- **Parallel**: true
- Guard the synthetic-slug cleanup at `session_executor.py:2308`: resolve `pm_pid = getattr(entry_or_session, "pm_pid", None)` and `if pm_pid and _pid_alive(pm_pid): log + increment counter; return` (fail-safe = preserve). Do NOT widen `worktree_busy_check`.
- Add `try/finally` terminate (`proc.terminate()` → bounded `wait()` → `proc.kill()`) to `_run_harness_subprocess`; delete and correct the false `agent_session_queue.py:1796-1799` comment.

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-kill-path, build-ordering
- **Assigned To**: leak-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite; verify every success criterion, especially the incident-regression test (failed status, `claude_pid=None`, `pm_pid=live` → worktree NOT removed, counter nonzero).

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/headless-session-runner.md` and the self-healing doc.

### 5. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: leak-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all criteria (including docs) met; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Subprocess-kill tests pass | `pytest tests/unit/test_session_health_subprocess_kill.py -q` | exit code 0 |
| Recovery finalization tests pass | `pytest tests/unit/test_health_check_recovery_finalization.py -q` | exit code 0 |
| Worktree manager tests pass | `pytest tests/unit/test_worktree_manager.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Recovery kill target is pm_pid | `grep -n "pm_pid" agent/session_health.py` | output contains pm_pid in `_apply_recovery_transition` |
| False cancel comment removed | `grep -rn "harness kills its own" agent/agent_session_queue.py` | exit code 1 |
| Process-group kill present | `grep -n "killpg" agent/session_health.py` | output contains killpg |
| Zombie-safe confirm present | `grep -n "waitpid" agent/session_health.py` | output contains waitpid |
| Terminal-with-live-subprocess counter present | `grep -rn "terminal-with-live-subprocess" agent/session_health.py` | output contains the counter key |
| No new pgid field / migration | `grep -rn "claude_pgid" agent/ models/ scripts/update/migrations.py` | exit code 1 |
| Harness cancel finally present | `grep -n "proc.terminate\|proc.kill" agent/sdk_client.py` | output contains proc |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-07-08. Verdict: NEEDS REVISION (1 blocker, 6 concerns, 1 nit). FULL depth. Revision applied 2026-07-08 (D2 adopted; reaper backstop → no-go) — see Addressed By column; re-armed for re-critique. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency (+ Scope & Value) | The executor-`finally` worktree cleanup and any kill/probe that fires post-terminal run AFTER `finalize_session` NULLs `claude_pid` (session_lifecycle.py:479). D1 unifies the confirm target on `claude_pid`, so by cleanup time it is `None`, the ordering probe is vacuous, and defect 2 (worktree deleted under a live process) survives. | ✅ RESOLVED — Technical Approach "Ordering (defect 2 fix)" + Step 2 + NEW incident-regression test | In session_executor.py:2308 cleanup, resolve `pid = getattr(entry_or_session, "pm_pid", None)` (survive-terminal) before `os.kill(pid, 0)` — never rely on `claude_pid`, which is `None` by that point. Add a regression test: session in `failed` status (claude_pid=None, pm_pid=live) asserts the worktree is NOT removed. |
| CONCERN | Scope & Value (Simplifier) | D1's new persisted `claude_pgid` field + migration is unnecessary: under `start_new_session=True` (sdk_client.py:2752) the child is its own group leader so pgid == pid, and repopulating `claude_pid` drags the #1271 reaper + dashboard into scope (plan Risk 2). Prefer D2. | ✅ RESOLVED — D2 adopted (Technical Approach); Update System no migration; Risk 2 retired; `role_driver.py:397` `start_new_session=True` verified | In `_apply_recovery_transition` (session_health.py:2292) read `pm_pid` not `claude_pid`; `_confirm_subprocess_dead` calls `os.killpg(pid, sig)` (pgid==pid holds only because start_new_session=True — assert in a comment). No `claude_pgid` field, no MIGRATIONS entry. Wrap killpg in `try/except ProcessLookupError` = confirmed-dead. |
| CONCERN | Risk & Robustness (Adversary) | The subprocess is a direct child of the worker (PPID=worker); after SIGKILL it is a zombie and `os.kill(pid,0)` returns success until reaped, so confirm-dead can burn the full timeout, false-escalate to `failed`, and (Risk 4 fail-safe) refuse cleanup — reproducing the wedge. | ✅ RESOLVED — Technical Approach "Zombie-safe confirm-exit" + Step 1 + zombie test case | In `_is_dead`, try `wpid,_ = os.waitpid(pid, os.WNOHANG)` guarded for `ChildProcessError`/`OSError(ECHILD)` (asyncio child-watcher may already own reaping); return True on `wpid==pid` or ECHILD. Regression test: a SIGKILL'd-but-unreaped child returns confirmed_dead=True within timeout. Document which asyncio child watcher reaps `proc`. |
| CONCERN | Risk & Robustness (Adversary) | No `finalize_session` clear for `claude_pgid` (if D1 kept) leaves a stale pgid a backstop could `killpg` after pgid reuse, and no guard prevents signalling the worker's own group. | ✅ RESOLVED — D2 persists no pgid (half evaporates); self-pgrp guard retained (Technical Approach + Risk 1) | If any pgid is persisted, add `session.claude_pgid = None` in the same try-block that NULLs `claude_pid`. In the killpg path guard `if pgid and pgid != os.getpgrp(): os.killpg(...)` else fall back to `os.kill(pid,sig)` — the self-pgrp check is the operator break-glass. (If D2 adopted, the persisted-pgid half evaporates; keep the self-pgrp guard.) |
| CONCERN | Scope & Value (Simplifier) | Adding a general OS-liveness probe API to `worktree_busy_check` (worktree_manager.py:419) is redundant once the kill works; the queue path already confirms death before `exec_task.cancel()`. | ✅ RESOLVED — executor-side guard only (Technical Approach "Ordering" + Step 2); `worktree_busy_check` unchanged | Guard `cleanup_after_merge` at session_executor.py:2308 with `if pm_pid and _pid_alive(pm_pid): log+increment counter; return` (fail-safe = preserve). Do NOT thread a pid through `worktree_busy_check`, which serves other callers and would gain a wider signature for one caller. |
| CONCERN | Scope & Value (User) | The confirmed root cause is narrow (kill reads the wrong PID field), but the plan reframes it as a lifecycle overhaul (3 builders, migration, Update-System change, open reaper-backstop question). The reaper backstop adds no coverage the incident-regression test doesn't. | ✅ RESOLVED — reaper backstop → explicit No-Go; Team collapsed to 2 builders; Step by Step now 5 steps | Record the reaper backstop as an explicit no-go now: the survivor is PPID==worker so the existing PPID==1 reaper cannot see it and relaxing its gate risks killing live worker children (plan Risk). Gate the ship on the incident-regression test (no double-subprocess, no under-live-PID worktree delete). Collapse to two builders (kill-path, ordering-and-harness). |
| CONCERN | Risk & Robustness (Operator) | All verification is monkeypatched unit tests; the reaper backstop may be a no-go. The original 28-min leak was invisible to every detector and caught by hand — no prod signal for "terminal session with a live subprocess" remains. | ✅ RESOLVED — `terminal-with-live-subprocess` counter (Technical Approach + Step 1 + Success Criteria); asserted nonzero in incident-regression test | Emit a `{project_key}:session-health:terminal-with-live-subprocess` counter from the hourly health/reaper sweep (resolve pm_pid, probe `os.kill(pm_pid,0)`, gate on terminal status + grace). Keep it even if the active backstop is a no-go; assert a nonzero value in the incident-regression test. |
| NIT | History & Consistency | The Documentation section (line 208) cites `agent/session_queue.py:1796-1799`, which does not exist; the correct path `agent/agent_session_queue.py` is used everywhere else in the plan. | ✅ RESOLVED — Inline Documentation now cites `agent/agent_session_queue.py:1796-1799` | Path corrected. |

---

## Open Questions

All three questions are resolved by the 2026-07-08 critique revision (recorded here for the re-critique's audit trail):

1. **PID-field unification direction (D1 vs D2). → RESOLVED: D2.** Read the existing survive-terminal `pm_pid` — no new persisted field, no migration, and the #1271 reaper + dashboard `claude_pid` semantics stay untouched. D1's `claude_pid` repopulation + new `claude_pgid` field was rejected as unnecessary state that dragged the reaper/dashboard into scope. `pm_pid` is a live confirm target at cleanup time precisely because `finalize_session` NULLs only `claude_pid`.
2. **Reaper backstop: implement or no-go? → RESOLVED: explicit no-go.** The survivor is PPID==worker, invisible to the PPID==1 reaper, and relaxing that gate risks killing live worker children. The primary fix closes the leak at its source; the passive `terminal-with-live-subprocess` counter (emitted from the hourly sweep) is the residual detection signal. Recorded in No-Gos.
3. **Process-group vs single-PID kill. → RESOLVED: killpg the group.** `os.killpg(pm_pid, sig)` (pgid==pid under `start_new_session=True`), self-pgrp guarded, so orphaned MCP/subagent children in the group die with the leader.
