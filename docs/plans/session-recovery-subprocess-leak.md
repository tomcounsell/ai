---
status: Planning
type: bug
appetite: Large
owner: Dev
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1938
last_comment_id:
revision_applied: true
---

# Session recovery/failure must terminate the runner's `claude -p` before requeue or worktree cleanup

## Problem

When the worker's health check decides a headless-runner session is stuck
(`no_progress`), it "recovers" it (`running → pending`), and after
`MAX_RECOVERY_ATTEMPTS=2` finalizes it `failed`; the executor then removes the
session's synthetic-slug worktree and `session/dev-*` branch. Two independent
defects let a live `claude -p` subprocess survive these transitions.

**Current behavior:**

- **Defect #1 — the recovery/failure path never terminates the runner subprocess.**
  The headless runner spawns `claude -p` with `start_new_session=True` (its own
  process group, `agent/sdk_client.py:2841`) and records the PID to
  `AgentSession.pm_pid` (`agent/session_runner/runner.py:522`), never to
  `claude_pid`. The recovery path (`agent/session_health.py::_apply_recovery_transition`)
  cancels `SessionHandle.task` and calls `_confirm_subprocess_dead(entry.claude_pid)`
  — but `claude_pid` is unset, so `_confirm_subprocess_dead(None)` short-circuits
  to `confirmed_dead=True, signal_sent=False` (`session_health.py:1523-1524`): a
  **false "confirmed dead."** Cancelling `SessionHandle.task` unwinds the runner
  coroutine but `_run_one_turn`'s `finally` (`runner.py:732-736`) never cancels
  `turn_task` or kills the process group, so the detached `claude -p` keeps
  running, parented to the live worker (PPID=worker). The PPID==1 reaper gate
  (`session_health.py:4713`, `:4851`) never matches it. Observed 2026-07-07: PM
  pid 52408 ran 28 minutes past its session's `running → failed`, self-repaired
  its deleted worktree, ran the SDLC pipeline unsupervised, and pushed a commit
  to main.

- **Defect #2 — failure cleanup deletes the worktree under the live subprocess.**
  The executor `finally` (`agent/session_executor.py:2292`) calls
  `cleanup_after_merge → remove_worktree` (`:2323`) gated only by
  `worktree_busy_check` (`agent/worktree_manager.py:419`, called `:1021`), which
  keys on AgentSession **row status**, not process liveness (`:472`). Once the row
  is `failed`, the guard reports "not busy" and the worktree + `session/dev-*`
  branch are deleted while the subprocess still holds that directory as its cwd.
  Claude Code pins `CLAUDE_PROJECT_DIR` for the process lifetime, so every
  subsequent Bash/Write/Edit in the survivor dies with ENOENT on the hook
  validators.

**Desired outcome:**

Recovery and failure transitions positively terminate the session's `claude -p`
process group and **confirm exit** before re-queueing; worktree/branch cleanup
runs only after the subprocess is confirmed dead. No process the system considers
failed can keep executing, and no cleanup mutates the filesystem under a live
child. The runner owns its subprocess lifecycle (per #1935 direction); the
teardown paths verify it.

## Freshness Check

**Baseline commit:** `f7bc0f5e` (Bump deps: claude-agent-sdk 0.2.111->0.2.112)
**Issue filed at:** 2026-07-07T08:35:02Z
**Disposition:** Unchanged

**File:line references re-verified (2026-07-08 against `f7bc0f5e`):**
- `agent/session_health.py:2055` — `_apply_recovery_transition` present; cancels `handle.task` (`:2265`), calls `_confirm_subprocess_dead(entry.claude_pid, ...)` (`:2292`). Still holds.
- `agent/session_health.py:1490,1523-1524` — `_confirm_subprocess_dead` `pid is None → confirmed_dead=True` short-circuit. Still holds.
- `agent/session_executor.py:1471` — `session.claude_pid = pid` inside `_on_sdk_started`; wired via `BossMessenger(on_sdk_started=...)` (`:1524`) whose `notify_sdk_started` has zero callers (dead legacy SDK-client path). Still holds.
- `agent/session_runner/runner.py:495-527` — `_on_turn_spawn` records `handle.pid/pgid` and `AgentSession.pm_pid`, NOT `claude_pid`. Still holds.
- `agent/session_runner/runner.py:706-737` — `_run_one_turn` `finally` cancels the watcher and nulls `_current_handle` only; never cancels `turn_task` / kills the pgid. Still holds.
- `agent/sdk_client.py:2841` — `create_subprocess_exec(..., start_new_session=True)`; `_run_harness_subprocess` (`:2752`) has no `except CancelledError` / `finally` proc cleanup. Still holds.
- `agent/session_executor.py:2300-2327` — synthetic-slug cleanup in `finally`, gated only on slug shape, log `[synthetic-slug] Cleaned up worktree+branch` (`:2325`). Still holds.
- `agent/worktree_manager.py:419,472,1021,1357` — `worktree_busy_check` (status-based), `cleanup_after_merge`, `remove_worktree`. Still holds.
- `agent/session_health.py:4586,4713,4802,4851` — both reaper passes gate strictly on PPID==1. Still holds.

**Cited sibling issues/PRs re-checked:**
- #1935 — OPEN. Companion (healthy runner turns misclassified as `no_progress`, which *triggers* these paths falsely). This issue depends on nothing in #1935 and stands alone: it stops any trigger (false or genuine) from leaking a live process. Its plan lives at `docs/plans/headless-runner-zombie-liveness.md`.
- #1537 — the source of the existing cancel → confirm-dead → requeue ordering in `_apply_recovery_transition`. This plan extends that machinery to the runner path rather than replacing it.
- #1271 — the PPID==1 orphan reaper. This plan deliberately does NOT extend it (Fix 4 no-go); the existing PPID==1 net remains the reaper-level safety for genuinely-orphaned processes.

**Commits on main since issue was filed (touching referenced files):** none material — `f7bc0f5e` is a dependency bump only; the referenced code paths are unchanged.

**Active plans in `docs/plans/` overlapping this area:** `headless-runner-zombie-liveness.md` (#1935) overlaps the *runner liveness* area but targets the false-trigger classifier, not subprocess termination. No conflict: #1935 changes `_has_progress`/classification; this changes teardown/kill/cleanup ordering. Coordinate at merge if both land in the same window.

## Prior Art

- **#1537** (merged): built `_confirm_subprocess_dead` + the "escalate to `failed` if subprocess survives cancel" ordering in `_apply_recovery_transition`. Correct design, but keyed on `claude_pid`, which the headless-runner cutover left unset — this plan closes that gap.
- **#1271** (merged): the cross-process orphan reaper with the PPID==1 + heartbeat net and the `worker:registered_pid:*` self-protection skip-set. This plan leaves it unchanged; Fix 4 (a worker-parented backstop leg) was examined and rejected — see the No-Gos section.
- **#1269** (merged): `harness_pid` subprocess-scoped field + `_on_sdk_finished` clear. Establishes the "set on spawn / clear on exit" pattern this plan mirrors for `claude_pid` on the runner path.
- **#1272** (merged): synthetic-slug worktree provisioning + the `finally`-block cleanup that Defect #2 lives in.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1537 | Added cancel → `_confirm_subprocess_dead(claude_pid)` → requeue-only-if-dead / escalate-to-failed in the recovery path | Keyed entirely on `AgentSession.claude_pid`. The headless-runner cutover routes spawn identity to `pm_pid`, leaving `claude_pid` unset — so the confirm-dead helper no-ops on `None` and the machinery silently protects nothing for runner sessions. |
| PR #1272 | Synthetic-slug worktree cleanup on session exit | Placed cleanup in `finally` gated only on slug shape; the only liveness guard (`worktree_busy_check`) is row-status-based, so a terminal row lets cleanup delete the worktree under a live subprocess. |

**Root cause pattern:** both teardown paths reason about liveness from the
AgentSession row (`claude_pid` presence, row status) instead of from the actual
process, and the headless runner never surfaces its detached process group to
either path.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `agent/session_runner/runner.py::_run_one_turn`'s `finally` becomes self-reaping (kills + confirms its process group on any teardown). `SessionRunner` also gains a convenience `terminate_current_turn()` coroutine (no gate depends on it). `_confirm_subprocess_dead` gains process-group awareness (derives the group from the pid via `os.getpgid`). No new `SessionHandle` field; no change to `agent/worktree_manager.py` or the executor `finally` cleanup.
- **Coupling:** the runner becomes the single owner of subprocess teardown — the reap lives in its own `finally`, so no external module reaches around it. The health checker keeps only its existing `SessionHandle.task.cancel()` + `_confirm_subprocess_dead(pid_snapshot)` backstop; it does not call into the runner. The executor cleanup is unchanged and correct by the finally-ordering guarantee (Fix 1/Fix 3).
- **Data ownership:** the runner is the single writer of the **live (non-terminal)** subprocess identity for runner sessions — `claude_pid` set on spawn, cleared on turn exit. The terminal clearer is unchanged: `models/session_lifecycle.py::finalize_session` (`:479`) nulls `claude_pid` on every terminal transition (a deliberate #1271 behavior so the PPID==1 reaper's `find_by_claude_pid` falls through). No new post-terminal reader is added (Fix 4 no-go).
- **Reversibility:** high — each fix is an additive guard/kill at a named site; reverting any one restores prior behavior without schema cleanup.

## Appetite

**Size:** Large

**Team:** Solo dev, PM (scope alignment), code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment; both plan Open Questions are now resolved in-plan)
- Review rounds: 2+ (async/subprocess correctness + race review)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are
internal to `agent/` and its tests; no secrets, services, or API keys involved.

## Solution

### Key Elements

- **Runner reaps on teardown (the load-bearing gate)**: `_run_one_turn`'s
  `finally` cancels `turn_task` and SIGTERM→SIGKILLs + confirms its process group
  whenever the coroutine is torn down (external cancel, exception, normal exit), so
  a cancelled `SessionHandle.task` no longer orphans a detached `claude -p`.
  Because the `finally` completes before control returns to the awaiting executor
  body, the group is provably dead before both the recovery-path confirm and the
  executor cleanup — the ordering guarantee that closes both defects.
- **Runner convenience API (`SessionRunner.terminate_current_turn`)**: a public
  coroutine to preempt the current turn's group; no gate depends on it (the
  executor `finally` cannot use it — `_current_handle` is already `None` by then).
- **Generic-harness backstop**: `_run_harness_subprocess` kills its process group
  in a `finally` if the awaiting coroutine is cancelled while the process is
  alive — protects the non-runner harness callers.
- **Live-identity surfacing**: the runner writes `AgentSession.claude_pid` on
  spawn (in addition to `pm_pid`) and clears it on turn exit, so the recovery
  path's `_confirm_subprocess_dead` targets the real live process. The recovery
  path snapshots the pid BEFORE cancelling (the teardown clears it on the same
  unwind). The group is derived from the pid via `os.getpgid` at kill time.
- **Cleanup correct by construction (no new gate)**: the executor cleanup and
  `worktree_manager` are unchanged; Fix 1's finally-ordering guarantees the
  subprocess is already dead when the executor cleanup runs. Earlier "confirm
  before cleanup" gates were inert (read already-nulled state) and were dropped.
- **No reaper backstop**: the orphan reaper is left unchanged (Fix 4 no-go). The
  primary fixes make a live-but-terminal process unreachable; a worker-parented
  reaper leg was examined and rejected as net-negative risk (see No-Gos).

### Flow

Health check flags `no_progress` → `_apply_recovery_transition` snapshots
`claude_pid`, cancels `SessionHandle.task` → runner `_run_one_turn` `finally`
reaps + confirms the turn's process group (completes before the awaiting body
returns) → recovery-path `_confirm_subprocess_dead(pid_snapshot)` verifies the
group is gone → **only if confirmed dead** requeue `pending` (else escalate
`failed`) → on terminal exit the executor `finally` runs `cleanup_after_merge`,
which is safe because the runner `finally` already reaped the group. In the
pathological unkillable case the runner `finally` logs a WARNING naming the
session for manual reclamation (no auto-reaper).
### Technical Approach

**Fix 1 — Runner reaps its subprocess group on teardown (THE load-bearing fix for both defects).**
- `agent/session_runner/runner.py::_run_one_turn` `finally` (`:732`): if
  `turn_task` is not done, cancel it and await it suppressing `CancelledError`;
  if `handle.pid` is set and the group is still alive, SIGTERM→short-grace→SIGKILL
  the pgid and **confirm exit** (bounded `os.killpg(pgid, 0)` poll) before the
  `finally` returns. Because this `finally` runs as the runner coroutine unwinds —
  and Python guarantees it completes before control returns to the awaiting
  executor body — the subprocess group is provably dead before BOTH the recovery
  path's post-cancel confirm AND the executor's own cleanup `finally`
  (`session_executor.py:2292`) run. This single ordering guarantee closes Defect #1
  (no orphan survives the cancelled `handle.task`) and Defect #2 (the executor
  cleanup that runs later in the same unwind can only run after the group is dead).
  It is the sole load-bearing gate; Fix 2 is the recovery-path confirm/escalate
  backstop, and there is no executor-side or worktree-side gate (see Fix 3).
- **Cancellation-path timing:** the health checker's
  `await asyncio.wait_for(handle.task, TASK_CANCEL_TIMEOUT)` (`session_health.py:2265`,
  0.25s) may time out if this `finally`'s SIGTERM grace + confirm exceeds 0.25s —
  that is fine: on timeout the recovery path proceeds to its own bounded
  `_confirm_subprocess_dead` (Fix 2) which finishes the job. Keep the `finally`'s
  grace short (sub-second) so the common path stays inside the budget. Steer/
  timeout preempts (the internal `_preempt_watcher` path) keep the existing
  graceful `_kill_turn` grace unchanged.
- Add public `async def terminate_current_turn(self)`: kills `_current_handle`'s
  group (graceful SIGTERM→grace→SIGKILL) and polls for confirmed exit. No
  `confirm` parameter (critique nit). This is a convenience entry point for a
  caller that holds a runner ref and wants to preempt the current turn; note that
  the teardown reaping above does NOT depend on anyone calling it (the executor
  `finally` cannot use it — `_current_handle` is already `None` by then; see Fix 3).
- Generic-harness backstop at the spawn site (NOT runner-path defense — the runner
  `finally` already covers the runner path): `agent/sdk_client.py::_run_harness_subprocess`
  wrap `await proc.communicate()` (`:3012`) with `try/finally` that, if the
  coroutine is cancelled while `proc.returncode is None`,
  `os.killpg(os.getpgid(proc.pid), SIGTERM)` → short grace → `SIGKILL`, swallowing
  `ProcessLookupError` (group already gone) and re-raising `CancelledError` after
  signalling. This protects the OTHER two `_run_harness_subprocess` call sites
  (`sdk_client.py:2534,2576,2632`) that are not the runner (critique nit).

**Fix 2 — Recovery path confirms/escalates against a pre-cancel pid snapshot (defect #1 backstop, gates AC#1 requeue).**
- `agent/session_runner/runner.py::_on_turn_spawn` (`:495`): additionally set
  `self._agent_session.claude_pid = pid` (alongside the existing `pm_pid` write at
  `:522`) and save it — same-object write, no cross-module reach. Do NOT write
  `SessionHandle.pid`/`_active_sessions` from the runner (the runner has no
  reference to them — critique Concern). Clear `claude_pid` at turn exit. No cached
  `pgid` anywhere — derive via `os.getpgid(pid)` at kill time (`pgid == pid` under
  `start_new_session`).
- **Snapshot-before-cancel (critique Concern — clear-vs-recovery-read race):**
  in `_apply_recovery_transition`, capture `pid_snapshot = getattr(entry, "claude_pid", None)`
  BEFORE `handle.task.cancel()`, and pass `pid_snapshot` (NOT a post-await re-read)
  into `_confirm_subprocess_dead`. The cancel triggers the runner teardown that
  clears `claude_pid` on the same unwind, so a post-await re-read would degenerate
  to `_confirm_subprocess_dead(None)` (a false confirm). The snapshot keeps the
  3.0s escalation meaningful: it verifies the group Fix 1 should have reaped is
  actually gone; if not, escalate to `failed` (existing #1537 branch).
- **Writer reconciliation:** two sites touch `claude_pid` — Fix 2 sets on spawn /
  clears on turn exit (live value); `models/session_lifecycle.py::finalize_session`
  (`:479`) clears on the terminal transition (unchanged #1271 behavior). No new
  post-terminal reader is added.
- `agent/session_health.py::_confirm_subprocess_dead` (`:1490`): derive the group
  from the (snapshotted) pid via `os.getpgid` and signal the GROUP (`os.killpg`)
  so a detached group with grandchildren (MCP servers) is fully reaped; `pgid == pid`
  under `start_new_session`. Confirm via `os.killpg(pgid, 0)`. Retain the existing
  `pid is None`/`pid<=0` short-circuit unchanged.
- Result: the existing #1537 ordering (cancel → confirm-dead → requeue-only-if-dead
  / escalate-`failed`) now protects runner sessions, and gates the requeue so
  "old confirmed exited before respawn" holds — AC#1.

**Fix 3 — No executor-side or worktree-side gate; rely on Fix 1's finally-ordering (defect #2).**
- Defect #2 is closed by Fix 1's ordering guarantee, NOT by a new gate. The two
  gates an earlier revision proposed here were **inert** (3-critic finding):
  (a) an executor-side `terminate_current_turn` in the `:2292` `finally` operates
  on `_current_handle`, which `_run_one_turn`'s `finally` already nulled
  (`runner.py:736`) as `_runner.run()` unwound — it would kill `None`; and
  (b) a `worktree_busy_check` liveness check at `:472` runs only on terminal rows,
  whose `claude_pid` `finalize_session` already nulled — `_confirm_subprocess_dead(None)`
  returns `confirmed_dead=True` and deletion proceeds (Defect #2 unchanged), while
  reading `pm_pid` there revives the PID-reuse live-kill hazard the Fix 4 no-go
  exists to avoid.
- Therefore: **do NOT add a gate in `agent/session_executor.py` and do NOT modify
  `agent/worktree_manager.py`.** The executor `finally` cleanup (`:2292-2327`) is
  left as-is; it is correct once Fix 1 guarantees the runner `finally` reaped the
  group earlier in the same unwind. The load-bearing invariant is the runner
  `finally`, not a downstream check that reads already-nulled state (critique
  BLOCKER, option a — scope-tightest, honest tests).
- **Pathological residual (critique Concern):** SIGKILL is uncatchable, so Fix 1's
  confirm virtually always succeeds. If a group is somehow unkillable, Fix 1's
  `finally` logs a WARNING naming the session; document a manual reclamation step
  (`git worktree prune` + directory removal) in the Documentation section so an
  operator has a break-glass path. No auto-reaper (Fix 4 no-go).

**Fix 4 — Reaper backstop (AC#4) — EXPLICIT NO-GO (answered, not implemented).**

The reaper-backstop question the issue requires answering is answered here with a
reasoned NO-GO. AC#4 permits "implemented **or** explicit no-go with rationale";
this plan elects the no-go. Rationale (see the No-Gos section for the durable
statement):

- **The primary fixes make the leak unreachable at its creation sites.** Fix 1
  reaps the process group on every runner-coroutine teardown (external cancel or
  exception) and at the spawn site; Fix 2 lets the recovery path confirm-kill the
  real live process; Fix 3 refuses to delete a worktree under a live group. A
  process the system considers terminal cannot stay alive past these gates. A
  reaper backstop is, by construction, "a cleanup utility that should never need
  to run."
- **Every backstop design the war room examined reintroduced the plan's own
  root-cause hazard.** Keying on `claude_pid` was impossible (cleared on terminal,
  round-1 blocker). Re-keying on `pm_pid` (never cleared) makes the reaper reason
  from a stale identifier under OS PID reuse: a long-dead terminal session's
  `pm_pid` can equal a PID a currently-`running` session legitimately holds, and
  "worker-parented + stale age" does not exclude a long legitimate SDLC turn —
  so the leg could SIGKILL a live, correct session (round-2 blocker). Making it
  safe requires layering a live-owner cross-check on top of a match that only
  exists to catch a case the primary fixes already prevent — net-negative
  complexity for a path that should never fire.
- **Defense-in-depth already exists elsewhere.** The existing PPID==1 orphan
  reaper still catches any leaked `claude --print` once its parent worker dies and
  it reparents to launchd (the genuinely-orphaned case). This plan does not need a
  second, riskier net for the worker-still-alive case that Fixes 1-3 close.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_run_one_turn` `finally` must not swallow the reaping failure silently — assert a `logger.warning`/kill-counter side effect when a signalled group refuses to die within the grace window.
- [ ] `_run_harness_subprocess` cancellation cleanup: assert the `except CancelledError`/`finally` re-raises `CancelledError` after killing the group (cancellation semantics preserved), and logs at debug/warning on `killpg` error rather than swallowing.
- [ ] The runner `_run_one_turn` `finally` must, in the pathological unkillable case, log a WARNING naming the session rather than swallowing the failure — assert the observable WARNING; the executor cleanup remains unchanged (its safety comes from the finally-ordering, not a new gate).

### Empty/Invalid Input Handling
- [ ] `_confirm_subprocess_dead` with `pid=None`, `pid<=0`, and an already-dead PID — assert the group path degrades to the existing None/already-dead behavior (no crash, correct `confirmed_dead`). `worktree_manager` and the executor `finally` are unchanged (no new liveness branch).
- [ ] Recovery path with `claude_pid` cleared mid-teardown — assert the pre-cancel snapshot keeps `_confirm_subprocess_dead` targeting the real pid (not `None`).

### Error State Rendering
- [ ] When the recovery path escalates to `failed` because the group would not die, assert the existing terminal user-facing notice path still fires exactly once (no regression to the #1537 single-send guarantee).

## Test Impact

- [ ] `tests/unit/test_session_health_subprocess_kill.py::TestConfirmSubprocessDead` — UPDATE: cover the process-group (`killpg`) path in addition to the PID path; keep None/already-dead cases.
- [ ] `tests/unit/test_session_health_subprocess_kill.py::TestRecoveryBranching::test_no_pid_recorded_requeues_normally` — UPDATE: for runner sessions `claude_pid` is now SET on spawn, so this "no pid" case must be re-scoped to genuinely-absent-pid sessions; add a sibling asserting a runner session with a live group escalates to `failed`, not requeues.
- [ ] `tests/unit/test_worktree_manager.py::TestCleanupAfterMerge` — NO CHANGE: `worktree_manager` is intentionally not modified (the earlier liveness-gate idea was inert — Fix 3). Listed so the audit records it was considered and left untouched; a new integration test (task 4) asserts the runner-finally reap precedes executor cleanup instead.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py::TestOrphanProcessReap` — NO CHANGE: the reaper is deliberately not modified (Fix 4 no-go). Listed for the reader's benefit so the audit records that these tests were considered and intentionally left untouched.
- [ ] `tests/unit/session_runner/test_runner_preempt.py` — UPDATE: add a case that external cancellation of the run task reaps the current turn's process group (new `_run_one_turn` finally behavior) without regressing the steer/timeout preempt cases.

## Rabbit Holes

- Do NOT rewrite the runner's preempt/turn architecture or the SDK-client harness loop. Reuse `_signal_turn`/`_kill_turn` and add a `finally`; do not refactor the turn state machine.
- Do NOT add a Popoto field for pgid, and do NOT add a cached `pgid` field to the in-memory `SessionHandle` either. Derive pgid from the live pid via `os.getpgid(pid)` at each kill site (`pgid == pid` under `start_new_session`); reuse the existing `claude_pid`/`pm_pid` fields. This avoids a schema migration and a second field to clear in lockstep.
- Do NOT touch the orphan reaper's PPID==1 gate at all (Fix 4 no-go). Broadening it to worker-parented processes risks killing in-flight legitimate turns under PID reuse; the primary fixes remove the need.
- Do NOT try to fix the #1935 false-trigger classifier here — that is #1935's job. This plan makes any trigger safe, not rarer.

## Risks

### Risk 1: Killing the process group kills more than intended
**Impact:** `killpg` on the runner's session group could, in theory, signal a co-located sibling if group isolation were wrong.
**Mitigation:** the runner spawns with `start_new_session=True`, so each `claude -p` is its own session/group leader (`pgid == pid`); killpg targets exactly that group. Tests assert only the target group receives the signal; the existing `_signal_turn` already uses pgid and is battle-tested by the preempt tests.

### Risk 2: Confirm-dead stalls the worker event loop
**Impact:** a hung subprocess could block the health tick or the executor finally while polling for exit.
**Mitigation:** keep the recovery-path `_confirm_subprocess_dead` synchronous but offloaded via `run_in_executor` (already the pattern at `session_health.py:2292`); bound polling by `SUBPROCESS_KILL_TIMEOUT` (3.0s). The runner `finally`'s reap uses a short grace so it does not stall the executor unwind; SIGKILL is uncatchable so confirmation is near-instant.

### Risk 3: claude_pid set-on-spawn / clear-on-exit races with recovery
**Impact:** a recovery firing in the window between turn-exit clear and next-turn set could read a stale/None `claude_pid`.
**Mitigation:** clear happens at confirmed turn exit only; between turns there is no live subprocess to leak, so a None read is correct (nothing to kill). `AgentSession.claude_pid` is set under the spawn callback; the recovery path reads `entry.claude_pid` and derives the group via `os.getpgid` at kill time.

## Race Conditions

### Race 1: Health-check cancel vs. runner turn spawn
**Location:** `agent/session_health.py:2264-2300` (cancel + confirm) vs. `agent/session_runner/runner.py:495-527,706-737` (spawn + turn finally).
**Trigger:** the health checker cancels `SessionHandle.task` at the same instant the runner is spawning the next turn's `claude -p`.
**Data prerequisite:** `AgentSession.claude_pid` must be written by `_on_turn_spawn` BEFORE the subprocess can do observable work, so a concurrent recovery can target it (the group is derived from the pid at kill time).
**State prerequisite:** the spawn callback writes pid/pgid before the first `await` that yields control back to the loop.
**Mitigation:** `_on_turn_spawn` is invoked synchronously by the harness on spawn (before the awaited `communicate`); the `_run_one_turn` finally reaps whatever `_current_handle` points at, so even a cancel-during-spawn reaps the just-spawned group. Confirm-dead is idempotent.

### Race 2: Executor cleanup vs. runner still tearing down
**Location:** `agent/session_executor.py:2292-2327`.
**Trigger:** the executor `finally` runs cleanup while the runner coroutine's own reaping is still in flight.
**Data prerequisite:** the subprocess must be confirmed dead before `cleanup_after_merge`.
**State prerequisite:** the runner `finally` must reap+confirm before the executor `finally` runs.
**Mitigation:** Python guarantees `_run_one_turn`'s `finally` (Fix 1) completes as the runner coroutine unwinds, and the executor `finally` runs only after the awaited `_runner.run()` body returns/raises — so the reap strictly precedes the cleanup within the same executor task. No separate executor/worktree gate is needed (and an earlier one was inert).

### Race 3: No reaper-backstop race (Fix 4 no-go)
**Location:** N/A — the plan does not add a reaper leg.
**Trigger:** N/A.
**Mitigation:** electing the Fix 4 no-go removes the PID-reuse hazard the round-2 critique surfaced (a stale never-cleared `pm_pid` matching a live session's recycled PID). No worker-parented reaper matching is introduced, so no such race exists.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1935] The `no_progress` false-trigger misclassification of healthy runner turns — owned by #1935's plan (`headless-runner-zombie-liveness.md`). This plan makes any trigger safe, not rarer.

**Reaper backstop (Fix 4) — decided NO-GO (this is the answer AC#4 requires).**
The plan does not add a worker-parented leg to the orphan reaper. This is a
deliberate design decision, not a deferral: the primary fixes (1-3) confirm-kill
the subprocess before requeue and before worktree cleanup, so a process the
system considers terminal cannot stay alive to be reaped. Every backstop keying
the war room examined reintroduced this plan's own root-cause hazard —
`claude_pid` is cleared on terminal (unmatchable), and `pm_pid` is never cleared
so under OS PID reuse a dead session's stale `pm_pid` can equal a live session's
current PID and the leg would SIGKILL a healthy session. The existing PPID==1
reaper still covers genuinely-orphaned (worker-dead) processes. Net: a reaper leg
here is a cleanup path that should never fire and carries a live-kill risk, so it
is out of scope by decision.

Nothing else deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required. All changes are internal to the worker/agent
runtime. No new dependencies, config files, or launchd services. No Popoto schema
change (pgid is carried in-memory on `SessionHandle`; `claude_pid` is an existing
field), so `scripts/update/migrations.py` needs no new migration.

## Agent Integration

No agent integration required. This is a worker-internal correctness fix to
session teardown; there is no new MCP tool, no `.mcp.json` change, and no bridge
call surface. The agent reaches nothing new. Integration coverage is the
worker-level test that a kill decision while a subprocess is alive produces no
ENOENT-wedged survivor and no ghost pipeline (AC#3).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` with the subprocess-lifecycle contract: the runner reaps + confirms its process group in `_run_one_turn`'s `finally` on every teardown, writes the live pid to `claude_pid` (cleared on turn exit), and the finally-ordering guarantees cleanup runs only after the group is dead. Include the manual reclamation runbook (`git worktree prune` + dir removal) for the pathological unkillable case.
- [ ] Update `docs/features/pm-session-liveness.md` (or `session-lifecycle.md`) to document the "confirm subprocess dead before requeue AND before worktree cleanup" ordering guarantee, and note the deliberate no-go on a worker-parented reaper leg.
- [ ] Add an entry to `docs/features/README.md` index if a new doc section is introduced (keep the table sorted).

### Inline Documentation
- [ ] Docstrings on `SessionRunner.terminate_current_turn`, the `_run_one_turn` finally reap+confirm (noting the finally-ordering guarantee), and the `_confirm_subprocess_dead` group path + pre-cancel snapshot, each citing #1938.

## Success Criteria

- [ ] After a `no_progress` recovery of a headless-runner session, exactly one `claude -p` subprocess exists for the session: the old group is confirmed exited before respawn (AC#1).
- [ ] After `running → failed`, the session's subprocess group is confirmed exited before `cleanup_after_merge` runs — guaranteed by the runner `finally` reaping earlier in the same executor-task unwind (AC#2).
- [ ] A test reproduces "kill decision while subprocess alive" and asserts: no ENOENT-wedged survivor (worktree not deleted under a live child), and no ghost pipeline (no post-terminal turn/commit) (AC#3).
- [ ] Reaper-backstop question answered: the plan records an explicit NO-GO with rationale (AC#4 is satisfied by "implemented or explicit no-go with rationale"). The orphan reaper is left unmodified.
- [ ] `grep -c "claude_pid" agent/session_runner/runner.py` shows the runner writes `claude_pid` on spawn (invariant that Fix 2 landed).
- [ ] Tests pass (`/do-test`, narrow scope).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Solo dev builds; a code reviewer does the async/subprocess correctness pass. No
parallel builders needed — the fixes are sequenced and share the same files
(`session_health.py`, `session_executor.py`, `runner.py`).

### Team Members

- **Builder (subprocess-lifecycle)**
  - Name: runner-teardown-builder
  - Role: implement Fixes 1-3 and their tests (Fix 4 is a no-go)
  - Agent Type: builder
  - Domain: async/concurrency (see DOMAIN_FRAMING.md — subprocess groups, CancelledError propagation, event-loop offload)
  - Resume: true

- **Validator (correctness)**
  - Name: teardown-validator
  - Role: verify AC#1-3 + the AC#4 no-go decision against the diff, run narrow tests, confirm no ENOENT/ghost-pipeline survivor
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Runner reaps on teardown + public kill API
- **Task ID**: build-runner-teardown
- **Depends On**: none
- **Validates**: tests/unit/session_runner/test_runner_preempt.py
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `terminate_current_turn` to `SessionRunner`; make `_run_one_turn` finally cancel `turn_task` and reap the live process group.
- Add the `_run_harness_subprocess` cancellation `finally` group-kill backstop in `agent/sdk_client.py`.

### 2. Surface live identity + group-aware confirm
- **Task ID**: build-live-identity
- **Depends On**: build-runner-teardown
- **Validates**: tests/unit/test_session_health_subprocess_kill.py
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- `_on_turn_spawn` writes `self._agent_session.claude_pid` on spawn; clear `claude_pid` on turn exit. No `SessionHandle`/`pgid` writes from the runner — derive the group via `os.getpgid(pid)` at kill time.
- Extend `_confirm_subprocess_dead` with the `killpg` group path.

### 3. Confirm the ordering invariant (no new gate)
- **Task ID**: verify-ordering-invariant
- **Depends On**: build-live-identity
- **Validates**: tests/unit/session_runner/test_runner_preempt.py
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm (by reading + a unit test) that `_run_one_turn`'s `finally` reap+confirm completes before the awaiting executor body returns, so `agent/session_executor.py`'s cleanup `finally` and `agent/worktree_manager.py` need NO change. Add a WARNING log in the runner `finally` for the pathological unkillable case (manual-reclamation break-glass).

### 4. Integration test: kill-while-alive → no survivor, no ghost pipeline
- **Task ID**: build-integration-ac3
- **Depends On**: verify-ordering-invariant
- **Validates**: tests/integration (new test)
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- Reproduce a recovery/failure while a fake long-lived subprocess (own process group) is alive; assert the runner `finally` reaps + confirms the group, that the recovery path requeues only after confirmed exit (else escalates `failed`), and that the executor cleanup runs only after the group is dead (no ENOENT-wedged survivor, no ghost pipeline).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-integration-ac3
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/headless-session-runner.md`, `docs/features/pm-session-liveness.md`, and the README index.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: teardown-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify AC#1-3 + the AC#4 no-go decision; run narrow tests; confirm no ENOENT/ghost-pipeline survivor.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Subprocess-kill tests pass | `pytest tests/unit/test_session_health_subprocess_kill.py -q` | exit code 0 |
| Worktree manager tests still pass (no regression) | `pytest tests/unit/test_worktree_manager.py -q` | exit code 0 |
| Orphan-reap tests still pass (reaper unchanged) | `pytest tests/unit/test_session_health_orphan_process_reap.py -q` | exit code 0 |
| Runner preempt tests pass | `pytest tests/unit/session_runner/test_runner_preempt.py -q` | exit code 0 |
| Runner writes claude_pid on spawn | `grep -c "claude_pid" agent/session_runner/runner.py` | output > 0 |
| Runner finally reaps its process group | `grep -c "killpg" agent/session_runner/runner.py` | output > 0 |
| worktree_manager NOT modified (Fix 3 no-gate) | `git diff --name-only origin/main -- agent/worktree_manager.py \| wc -l` | output contains 0 |
| Format clean | `python -m ruff format --check agent/ tests/` | exit code 0 |

## Critique Results

**Round 1 (NEEDS REVISION → addressed):**
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Fix 4 keyed on `find_by_claude_pid` resolving a TERMINAL session, but `finalize_session:479` nulls `claude_pid` on terminal — the leg can never match. | Rev 1 re-keyed on `pm_pid`; Rev 2 (below) elected the Fix 4 no-go entirely. | Superseded by the round-2 blocker + no-go. |
| CONCERN | Risk & Robustness | Fix 3 liveness check placed after `worktree_busy_check`'s terminal-row `continue` (`:472`) is dead for terminal rows. | Round-3 superseded: the whole `worktree_busy_check` gate was dropped (inert); Fix 1's finally-ordering covers AC#2 instead. | See Round 3. |
| CONCERN | Risk & Robustness | `TASK_CANCEL_TIMEOUT` (0.25s) can truncate the runner finally's SIGTERM grace. | Runner finally uses a short grace; on `wait_for` timeout the recovery-side `_confirm_subprocess_dead` (bounded 3.0s, offloaded) finishes the job. | Reconciled in Fix 1. |
| CONCERN | Scope & Value | Two kill surfaces unresolved while Steps commit to both. | Round-3 superseded: the executor no longer calls into the runner; `terminate_current_turn` is a convenience API, and the health checker keeps `_confirm_subprocess_dead`. | See Round 3. |
| CONCERN | History & Consistency | "Single writer of live-subprocess identity" omits `finalize_session`. | Claim scoped to "live (non-terminal) value"; `finalize_session:479` named as terminal clearer. | — |
| NIT | Scope & Value | `SessionHandle.pgid` caches a pure function of `pid`. | Dropped the cached field; derive via `os.getpgid(pid)`. | — |

**Round 2 (NEEDS REVISION → addressed):**
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency | `pm_pid`-keyed reaper leg can SIGKILL a live session: `pm_pid` is never cleared, so under OS PID reuse a dead terminal session's `pm_pid` can equal a live session's current PID. | Elected the Fix 4 **NO-GO** (durable rationale in No-Gos). Primary fixes 1-3 make the leak unreachable; the reaper is left unmodified. | Removes the PID-reuse hazard entirely; the PPID==1 net still covers worker-dead orphans. |
| CONCERN | History & Consistency | Fix 2 said `_on_turn_spawn` writes `SessionHandle.pid` into `_active_sessions` — a cross-module reach the runner cannot make. | Fix 2 now writes only `self._agent_session.claude_pid` (same object); the recovery path reads `entry.claude_pid`, so no registry write is needed. `_TurnHandle` vs `SessionHandle` terminology corrected. | — |
| CONCERN | Risk & Robustness | Fix 3's "leave the worktree for the reaper" is inaccurate. | Round-3 superseded: no executor SKIP branch; the runner `finally` reaps before cleanup, and the pathological-unkillable case is a WARNING + documented manual reclamation. | See Round 3. |
| CONCERN | Risk & Robustness | `_runner` may be unbound in the executor `finally`. | Round-3 superseded: the executor `finally` no longer calls into the runner at all, so there is no unbound-`_runner` risk. | See Round 3. |
| NIT | Scope & Value | `terminate_current_turn(confirm=True)` param is speculative. | Dropped the parameter; the only caller always confirms. | — |

**Round 3 (NEEDS REVISION → addressed):**
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency + Scope & Value | Fix 3's two "confirm-dead-before-cleanup" gates are inert: the executor's `terminate_current_turn` runs after `_run_one_turn` nulled `_current_handle`; `worktree_busy_check` reads `claude_pid` already nulled by `finalize_session:479`, and reading `pm_pid` there revives the Fix 4 PID-reuse hazard. | Adopted option (a): DROP both gates. `_run_one_turn`'s `finally` reaps + confirms the group, and Python's finally-ordering guarantees that completes before the executor cleanup `finally` in the same unwind — so AC#2 holds structurally with no downstream gate. `agent/worktree_manager.py` and the executor `finally` are unchanged. | Fix 1 is the single load-bearing gate; Fix 2 is the recovery-path confirm/escalate backstop. |
| CONCERN | Risk & Robustness | Recovery's authoritative confirm reads `claude_pid` after Fix 2 clears it on the same teardown → `_confirm_subprocess_dead(None)` false confirm. | Snapshot `pid_snapshot = entry.claude_pid` BEFORE `handle.task.cancel()` and confirm against the snapshot (Fix 2). Added a Race + test bullet. | The `wait_for(0.25s)` may time out; the 3.0s snapshot confirm finishes the job. |
| CONCERN | Risk & Robustness (Operator) | SKIP-cleanup-on-timeout leaks worktrees with no reclamation path (Fix 4 forbids a reaper). | No SKIP branch now (cleanup is unconditional-but-safe post-reap). For the theoretical unkillable case, the runner `finally` WARNs and the docs carry a manual `git worktree prune` runbook. | Documented in the Documentation section. |
| NIT | Scope & Value | Spawn-site backstop overlaps Fix 1 on the runner path. | Reframed as generic-harness protection for the two non-runner `_run_harness_subprocess` call sites; swallow `ProcessLookupError`, re-raise `CancelledError`. | Not load-bearing for AC#1. |

---

## Open Questions

No open questions — all prior questions are resolved in-plan:

1. **Reaper backstop (Fix 4): implement or no-go?** → RESOLVED: explicit NO-GO
   with rationale (two critique rounds showed every keying reintroduces the
   plan's own root-cause hazard). AC#4 permits this.
2. **Runner-owned kill API vs. shared helper?** → RESOLVED: the load-bearing reap
   lives in `_run_one_turn`'s `finally` (no caller needed). `terminate_current_turn`
   is a convenience preempt API; the health checker keeps its
   `SessionHandle.task.cancel()` + `_confirm_subprocess_dead(pid_snapshot)` backstop.
   The executor `finally` does NOT call into the runner (Round-3 correction).
