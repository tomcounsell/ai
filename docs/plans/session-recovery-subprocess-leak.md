---
status: Planning
type: bug
appetite: Large
owner: Dev
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1938
last_comment_id:
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
- #1271 — the PPID==1 orphan reaper this plan adds a narrow worker-parented backstop leg to.

**Commits on main since issue was filed (touching referenced files):** none material — `f7bc0f5e` is a dependency bump only; the referenced code paths are unchanged.

**Active plans in `docs/plans/` overlapping this area:** `headless-runner-zombie-liveness.md` (#1935) overlaps the *runner liveness* area but targets the false-trigger classifier, not subprocess termination. No conflict: #1935 changes `_has_progress`/classification; this changes teardown/kill/cleanup ordering. Coordinate at merge if both land in the same window.

## Prior Art

- **#1537** (merged): built `_confirm_subprocess_dead` + the "escalate to `failed` if subprocess survives cancel" ordering in `_apply_recovery_transition`. Correct design, but keyed on `claude_pid`, which the headless-runner cutover left unset — this plan closes that gap.
- **#1271** (merged): the cross-process orphan reaper with the PPID==1 + heartbeat net and the `worker:registered_pid:*` self-protection skip-set. This plan reuses its `find_by_claude_pid`, `_session_is_alive` terminal check, and create-time-verified SIGKILL staging for the new backstop leg.
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
- **Interface changes:** `SessionHandle` gains an in-memory `pgid: int | None` field (dataclass, not Popoto — no migration). `SessionRunner` gains a public `terminate_current_turn(confirm: bool)` coroutine so teardown paths ask the owner to kill rather than reaching around it. `_confirm_subprocess_dead` gains process-group awareness.
- **Coupling:** decreases the health-checker's reach-around coupling by giving the runner an authoritative kill entry point; the health checker and executor call it (or the shared `_confirm_subprocess_dead`) instead of guessing at `claude_pid`.
- **Data ownership:** the runner becomes the single writer of live-subprocess identity for runner sessions (`claude_pid` set on spawn, cleared on turn exit), matching the #1935 "runner is the authoritative subprocess-lifecycle module" direction.
- **Reversibility:** high — each fix is an additive guard/kill at a named site; reverting any one restores prior behavior without schema cleanup.

## Appetite

**Size:** Large

**Team:** Solo dev, PM (scope alignment on the reaper-backstop decision), code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the reaper-backstop implement-vs-no-go call, confirm no Popoto field is added)
- Review rounds: 2+ (async/subprocess correctness + race review)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are
internal to `agent/` and its tests; no secrets, services, or API keys involved.

## Solution

### Key Elements

- **Runner-owned termination (`SessionRunner.terminate_current_turn`)**: an
  authoritative, external-callable coroutine that SIGTERM→grace→SIGKILLs the
  current turn's process group (reusing `_signal_turn`/`_kill_turn`) and confirms
  exit. The recovery path and executor call this instead of reaching around the
  runner.
- **Runner reaps on teardown**: `_run_one_turn`'s `finally` cancels `turn_task`
  and reaps the live process group whenever the coroutine is torn down
  (external cancel or exception), so a cancelled `SessionHandle.task` no longer
  orphans a detached `claude -p`.
- **Spawn-site backstop**: `_run_harness_subprocess` kills its process group in a
  `finally`/`except CancelledError` if the awaiting coroutine is cancelled while
  the process is alive — protects every harness caller.
- **Live-identity surfacing**: the runner writes `AgentSession.claude_pid` (and
  `SessionHandle.pgid`) on spawn and clears `claude_pid` on turn exit, so the
  recovery path's `_confirm_subprocess_dead` targets the real process and
  `find_by_claude_pid` can resolve it.
- **Cleanup gated on confirmed death**: the executor confirms the subprocess dead
  before worktree/branch cleanup; `worktree_busy_check`/`remove_worktree` refuse
  to delete a worktree whose owning session's subprocess group is still alive.
- **Reaper backstop leg**: the orphan reaper additionally matches a worker-parented
  `claude --print` whose owning AgentSession is terminal (create-time verified).

### Flow

Health check flags `no_progress` → `_apply_recovery_transition` cancels
`SessionHandle.task` → runner `_run_one_turn` finally reaps the turn's process
group → `terminate_current_turn`/`_confirm_subprocess_dead` confirms exit →
**only if confirmed dead** requeue `pending` (else escalate `failed`) → on
terminal exit the executor `finally` confirms dead again → **only then**
`cleanup_after_merge` removes worktree + branch. If any path still leaks, the
reaper's worker-parented-terminal leg reaps it on the next tick.

### Technical Approach

**Fix 1 — Runner reaps its subprocess on cancellation (defect #1, root cause).**
- `agent/session_runner/runner.py::_run_one_turn` `finally` (`:732`): if
  `turn_task` is not done, cancel it and await it suppressing `CancelledError`;
  if `handle.pid` is set and `handle.pgid`'s group is still alive, escalate
  SIGTERM→grace→SIGKILL on the pgid via the existing `_signal_turn`/`_kill_turn`
  (`runner.py:799/834`) and confirm exit. This guarantees teardown of the
  detached group whenever the runner coroutine unwinds.
- Add public `async def terminate_current_turn(self, *, confirm: bool = True)`:
  kills `_current_handle`'s group and (optionally) polls for exit. This is the
  external kill entry point the teardown paths call.
- Defense-in-depth at the spawn site: `agent/sdk_client.py::_run_harness_subprocess`
  wrap `await proc.communicate()` (`:3012`) with `try/finally` (or
  `except asyncio.CancelledError`) that, if the coroutine is cancelled while
  `proc.returncode is None`, `os.killpg(os.getpgid(proc.pid), SIGTERM)` → short
  grace → `SIGKILL`. Protects any caller, not just the runner.

**Fix 2 — Recovery path targets the real process (defect #1, confirmation).**
- `agent/session_runner/runner.py::_on_turn_spawn` (`:495`): additionally set
  `AgentSession.claude_pid = pid` (alongside `pm_pid`) and publish `pid`/`pgid`
  onto the `SessionHandle` in `_active_sessions` (extend `SessionHandle` with an
  in-memory `pgid` field in `agent/session_state.py`). Clear `claude_pid` at
  turn exit (mirroring the `_on_sdk_finished`/`harness_pid` pattern) so a stale
  finished-turn PID is never confirmed-dead-falsely while the next turn runs.
- `agent/session_health.py::_confirm_subprocess_dead` (`:1490`): when a process
  group is known, signal the GROUP (`os.killpg`) rather than the bare PID so a
  detached group with grandchildren (MCP servers) is fully reaped; `pgid == pid`
  under `start_new_session`, so `killpg(pid)` is correct and safe. Confirm via
  `os.killpg(pgid, 0)`.
- Result: the existing #1537 ordering (cancel → confirm-dead → requeue-only-if-dead
  / escalate-`failed`) now protects runner sessions — AC#1.

**Fix 3 — Executor confirms death before worktree cleanup (defect #2).**
- `agent/session_executor.py::_execute_agent_session` `finally` (`:2292`): before
  the synthetic-slug cleanup block (`:2300-2327`), confirm the session's
  subprocess/group is dead (call the runner's `terminate_current_turn` if the
  runner ref is in scope, else `_confirm_subprocess_dead(session.claude_pid)`).
  Only run `cleanup_after_merge` after confirmed exit. With Fix 1 reaping on the
  runner-coroutine teardown that precedes this `finally`, confirmation is normally
  instantaneous; it is a hard gate for the race.
- Defense-in-depth: `agent/worktree_manager.py::worktree_busy_check`/`remove_worktree`
  add a process-liveness check — if the owning session's subprocess group is
  alive, refuse deletion (return busy / raise) with a clear log. Makes AC#2 an
  invariant enforced at the deletion site, not only at the caller.

**Fix 4 — Reaper backstop (AC#4) — IMPLEMENT (narrow leg).**
- `agent/session_health.py::_reap_orphan_session_processes` (`:4586`): add a
  bounded leg — for a `claude --print` process whose parent PID is a REGISTERED
  worker (present in the `worker:registered_pid:*` skip-set, i.e. worker-parented
  rather than PPID==1) AND whose owning `AgentSession` (via `find_by_claude_pid(pid)`,
  enabled by Fix 2) is in a TERMINAL status AND the process is older than a short
  grace window, reap it (SIGTERM→SIGKILL, create-time-verified via the existing
  `_pending_sigkill_orphans` staging). See the No-Gos / Open Questions for the
  implement-vs-no-go rationale; default is implement.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_run_one_turn` `finally` and `terminate_current_turn` must not swallow the reaping failure silently — assert a `logger.warning`/kill-counter side effect when a signalled group refuses to die within the grace window.
- [ ] `_run_harness_subprocess` cancellation cleanup: assert the `except CancelledError`/`finally` re-raises `CancelledError` after killing the group (cancellation semantics preserved), and logs at debug/warning on `killpg` error rather than swallowing.
- [ ] The executor pre-cleanup confirm must log and still finalize the row if confirm times out (never block session finalization on a stuck kill) — assert the observable log + that cleanup is SKIPPED (worktree left for the reaper), not forced.

### Empty/Invalid Input Handling
- [ ] `_confirm_subprocess_dead` with `pid=None`, `pid<=0`, and an already-dead PID — assert the group path degrades to the existing PID/None behavior (no crash, correct `confirmed_dead`).
- [ ] `worktree_busy_check` with a session whose `claude_pid`/`pgid` is None — assert it falls back to the existing status-based decision (no false "busy").
- [ ] Reaper leg with `find_by_claude_pid(pid)` returning None — assert it does NOT reap (no owning session ⇒ not our leak to claim here).

### Error State Rendering
- [ ] When the recovery path escalates to `failed` because the group would not die, assert the existing terminal user-facing notice path still fires exactly once (no regression to the #1537 single-send guarantee).

## Test Impact

- [ ] `tests/unit/test_session_health_subprocess_kill.py::TestConfirmSubprocessDead` — UPDATE: cover the process-group (`killpg`) path in addition to the PID path; keep None/already-dead cases.
- [ ] `tests/unit/test_session_health_subprocess_kill.py::TestRecoveryBranching::test_no_pid_recorded_requeues_normally` — UPDATE: for runner sessions `claude_pid` is now SET on spawn, so this "no pid" case must be re-scoped to genuinely-absent-pid sessions; add a sibling asserting a runner session with a live group escalates to `failed`, not requeues.
- [ ] `tests/unit/test_worktree_manager.py::TestCleanupAfterMerge` — UPDATE: `worktree_busy_check`/`remove_worktree` now consult process liveness; add a case where a live owning-session group blocks deletion and a dead one permits it.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py::TestOrphanProcessReap` — UPDATE/EXTEND: add cases for the worker-parented + terminal-session backstop leg (reaped) and worker-parented + non-terminal (skipped) and worker-parented + no owning session (skipped).
- [ ] `tests/unit/session_runner/test_runner_preempt.py` — UPDATE: add a case that external cancellation of the run task reaps the current turn's process group (new `_run_one_turn` finally behavior) without regressing the steer/timeout preempt cases.

## Rabbit Holes

- Do NOT rewrite the runner's preempt/turn architecture or the SDK-client harness loop. Reuse `_signal_turn`/`_kill_turn` and add a `finally`; do not refactor the turn state machine.
- Do NOT add a Popoto field for pgid. Derive pgid from the live pid via `os.getpgid` and carry it in the in-memory `SessionHandle`; reuse the existing `claude_pid` field. This avoids a schema migration entirely.
- Do NOT generalize the reaper to match all worker-parented `claude` processes — only the narrow `--print` + terminal-owning-session + registered-worker-parent + stale-age leg. Broadening the PPID==1 gate risks killing in-flight legitimate turns.
- Do NOT try to fix the #1935 false-trigger classifier here — that is #1935's job. This plan makes any trigger safe, not rarer.

## Risks

### Risk 1: Killing the process group kills more than intended
**Impact:** `killpg` on the runner's session group could, in theory, signal a co-located sibling if group isolation were wrong.
**Mitigation:** the runner spawns with `start_new_session=True`, so each `claude -p` is its own session/group leader (`pgid == pid`); killpg targets exactly that group. Tests assert only the target group receives the signal; the existing `_signal_turn` already uses pgid and is battle-tested by the preempt tests.

### Risk 2: Confirm-dead stalls the worker event loop
**Impact:** a hung subprocess could block the health tick or the executor finally while polling for exit.
**Mitigation:** keep `_confirm_subprocess_dead` synchronous but offloaded via `run_in_executor` (already the pattern at `session_health.py:2292`); bound polling by `SUBPROCESS_KILL_TIMEOUT` (3.0s). The executor pre-cleanup confirm uses the same bounded, offloaded call and SKIPS cleanup (leaves it to the reaper) rather than blocking finalization if the group will not die.

### Risk 3: claude_pid set-on-spawn / clear-on-exit races with recovery
**Impact:** a recovery firing in the window between turn-exit clear and next-turn set could read a stale/None `claude_pid`.
**Mitigation:** clear happens at confirmed turn exit only; between turns there is no live subprocess to leak, so a None read is correct (nothing to kill). The `SessionHandle.pgid` is updated under the same spawn callback; the recovery path prefers the handle's live pgid when present and falls back to `claude_pid`.

## Race Conditions

### Race 1: Health-check cancel vs. runner turn spawn
**Location:** `agent/session_health.py:2264-2300` (cancel + confirm) vs. `agent/session_runner/runner.py:495-527,706-737` (spawn + turn finally).
**Trigger:** the health checker cancels `SessionHandle.task` at the same instant the runner is spawning the next turn's `claude -p`.
**Data prerequisite:** `SessionHandle.pgid`/`AgentSession.claude_pid` must be written by `_on_turn_spawn` BEFORE the subprocess can do observable work, so a concurrent recovery can target it.
**State prerequisite:** the spawn callback writes pid/pgid before the first `await` that yields control back to the loop.
**Mitigation:** `_on_turn_spawn` is invoked synchronously by the harness on spawn (before the awaited `communicate`); the `_run_one_turn` finally reaps whatever `_current_handle` points at, so even a cancel-during-spawn reaps the just-spawned group. Confirm-dead is idempotent.

### Race 2: Executor cleanup vs. runner still tearing down
**Location:** `agent/session_executor.py:2292-2327`.
**Trigger:** the executor `finally` runs cleanup while the runner coroutine's own reaping is still in flight.
**Data prerequisite:** the subprocess must be confirmed dead before `cleanup_after_merge`.
**State prerequisite:** the pre-cleanup confirm gate must observe the runner's reap outcome.
**Mitigation:** the runner-coroutine teardown (Fix 1) completes before the executor `finally` for the same task (the finally runs after the awaited body returns/raises); the pre-cleanup confirm is a second, bounded gate; the `worktree_busy_check` process-liveness check is the last line.

### Race 3: PID reuse in the reaper backstop
**Location:** `agent/session_health.py:4586+` (new leg) and the staged-SIGKILL drain (`:4619-4646`).
**Trigger:** macOS recycles a reaped PID to an unrelated process between decision and SIGKILL.
**Mitigation:** reuse the existing create-time-verified `_pending_sigkill_orphans` staging (verify `proc.create_time()` before SIGKILL); require terminal owning session AND registered-worker parent AND stale age before staging.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1935] The `no_progress` false-trigger misclassification of healthy runner turns — owned by #1935's plan (`headless-runner-zombie-liveness.md`). This plan makes any trigger safe, not rarer.
- The reaper-backstop leg (Fix 4) is IN scope and implemented by default. It is NOT deferred. If PM review elects the no-go path instead, the rationale would be: "the primary fixes (1-3) make a leaked live process unreachable, so the backstop guards only against a future regression; matching worker-parented processes carries residual risk." Default remains implement, because the enabling infra (`find_by_claude_pid`, terminal check, create-time-verified SIGKILL) already exists and the leg is narrow.

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
- [ ] Update `docs/features/headless-session-runner.md` with the subprocess-lifecycle contract: the runner owns termination (`terminate_current_turn`), reaps its process group on teardown, and publishes live pid/pgid to `claude_pid`/`SessionHandle`.
- [ ] Update `docs/features/pm-session-liveness.md` (or `session-lifecycle.md`) to document the "confirm subprocess dead before requeue AND before worktree cleanup" ordering guarantee and the reaper's worker-parented-terminal backstop leg.
- [ ] Add an entry to `docs/features/README.md` index if a new doc section is introduced (keep the table sorted).

### Inline Documentation
- [ ] Docstrings on `SessionRunner.terminate_current_turn`, the `_run_one_turn` finally reaping, the `_confirm_subprocess_dead` group path, and the executor pre-cleanup confirm, each citing #1938.

## Success Criteria

- [ ] After a `no_progress` recovery of a headless-runner session, exactly one `claude -p` subprocess exists for the session: the old group is confirmed exited before respawn (AC#1).
- [ ] After `running → failed`, the session's subprocess group is confirmed exited before `cleanup_after_merge`/`remove_worktree` runs (AC#2).
- [ ] A test reproduces "kill decision while subprocess alive" and asserts: no ENOENT-wedged survivor (worktree not deleted under a live child), and no ghost pipeline (no post-terminal turn/commit) (AC#3).
- [ ] Reaper backstop implemented: a worker-parented `claude --print` whose owning AgentSession is terminal and is stale gets reaped, with tests for the reaped/skipped cases (AC#4).
- [ ] `grep -n "claude_pid" agent/session_runner/runner.py` shows the runner writes `claude_pid` on spawn (Agent Integration invariant that Fix 2 landed).
- [ ] Tests pass (`/do-test`, narrow scope).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Solo dev builds; a code reviewer does the async/subprocess correctness pass. No
parallel builders needed — the fixes are sequenced and share the same files
(`session_health.py`, `session_executor.py`, `runner.py`).

### Team Members

- **Builder (subprocess-lifecycle)**
  - Name: runner-teardown-builder
  - Role: implement Fixes 1-4 and their tests
  - Agent Type: builder
  - Domain: async/concurrency (see DOMAIN_FRAMING.md — subprocess groups, CancelledError propagation, event-loop offload)
  - Resume: true

- **Validator (correctness)**
  - Name: teardown-validator
  - Role: verify AC#1-4 against the diff, run narrow tests, confirm no ENOENT/ghost-pipeline survivor
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
- `_on_turn_spawn` writes `claude_pid` + `SessionHandle.pgid`; clear `claude_pid` on turn exit. Add `SessionHandle.pgid` field.
- Extend `_confirm_subprocess_dead` with the `killpg` group path.

### 3. Executor + worktree cleanup gated on confirmed death
- **Task ID**: build-cleanup-gate
- **Depends On**: build-live-identity
- **Validates**: tests/unit/test_worktree_manager.py
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- Executor `finally` confirms dead before `cleanup_after_merge`; add process-liveness check to `worktree_busy_check`/`remove_worktree`.

### 4. Reaper backstop leg
- **Task ID**: build-reaper-backstop
- **Depends On**: build-live-identity
- **Validates**: tests/unit/test_session_health_orphan_process_reap.py
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the worker-parented + terminal-session leg with create-time-verified SIGKILL staging.

### 5. Integration test: kill-while-alive → no survivor, no ghost pipeline
- **Task ID**: build-integration-ac3
- **Depends On**: build-cleanup-gate
- **Validates**: tests/integration (new test)
- **Assigned To**: runner-teardown-builder
- **Agent Type**: builder
- **Parallel**: false
- Reproduce a recovery/failure while a fake long-lived subprocess (own process group) is alive; assert it is confirmed dead before requeue and before worktree cleanup, and that the worktree survives (cleanup skipped) until the process exits.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-integration-ac3, build-reaper-backstop
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/headless-session-runner.md`, `docs/features/pm-session-liveness.md`, and the README index.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: teardown-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify AC#1-4; run narrow tests; confirm no ENOENT/ghost-pipeline survivor.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Subprocess-kill tests pass | `pytest tests/unit/test_session_health_subprocess_kill.py -q` | exit code 0 |
| Worktree manager tests pass | `pytest tests/unit/test_worktree_manager.py -q` | exit code 0 |
| Orphan-reap tests pass | `pytest tests/unit/test_session_health_orphan_process_reap.py -q` | exit code 0 |
| Runner preempt tests pass | `pytest tests/unit/session_runner/test_runner_preempt.py -q` | exit code 0 |
| Runner writes claude_pid on spawn | `grep -c "claude_pid" agent/session_runner/runner.py` | output > 0 |
| Executor confirms dead before cleanup | `grep -n "confirm" agent/session_executor.py \| grep -in "cleanup\|dead"` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ tests/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Reaper backstop (Fix 4): implement or no-go?** The plan defaults to
   IMPLEMENT (narrow, worker-parented + terminal-session + create-time-verified
   leg) because the enabling infra already exists and it directly closes the
   "invisible to the reaper" gap. Confirm this is the desired call, or elect the
   no-go with the rationale in No-Gos.
2. **Runner-owned kill API vs. shared helper:** the plan adds
   `SessionRunner.terminate_current_turn` AND keeps `_confirm_subprocess_dead` as
   the executor/health-checker fallback (when the runner ref is not in scope).
   Confirm both surfaces are wanted, or standardize on one.
