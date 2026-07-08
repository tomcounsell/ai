---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-08
tracking: https://github.com/tomcounsell/ai/issues/1938
last_comment_id:
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

- **New dependencies:** none.
- **Interface changes:** `_confirm_subprocess_dead` gains process-group awareness (accept/derive a pgid, or a small `SubprocessIdentity`); the runner's spawn callback additionally records the confirm-target PID (and pgid) where the safety machinery reads it. Minimal signature growth.
- **Coupling:** slightly reduces coupling by making one field the single source of truth for "the session's live subprocess" that both the kill path and the reaper read. Removes the implicit dependency on the false "cancel kills the child" assumption.
- **Data ownership:** the runner (`agent/session_runner/`) becomes the authoritative writer of the subprocess-identity field(s) at spawn; the health check and reaper are readers. This matches the #1935-discussion direction (runner owns subprocess lifecycle).
- **Reversibility:** high — all changes are localized to the kill/cleanup/spawn-record paths and are guarded by tests.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in, code reviewer (async/subprocess-lifecycle domain)

**Interactions:**
- PM check-ins: 1-2 (confirm the PID-field-unification direction and the reaper-backstop decision)
- Review rounds: 1 (correctness of the kill ordering and process-group semantics)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal to `agent/` and `models/`, exercised by unit tests with monkeypatched `os.kill`/`os.killpg`.

## Solution

### Key Elements

- **Single subprocess-identity source of truth (spawn side):** the runner records the confirm-target PID (and process-group id) into the field(s) the safety machinery reads, at spawn, for every worker-executed session — closing the `claude_pid`-vs-`pm_pid` gap.
- **Process-group kill (recovery/failure side):** `_confirm_subprocess_dead` signals the process **group** (the runner spawns with `start_new_session=True`, so the `claude -p` and its MCP/subagent children share one group), not a single PID, and confirms the group leader is gone.
- **Explicit ordering guarantee:** worktree/branch cleanup is gated on OS-process-confirmed-dead, not on a DB-status timing coincidence. Cleanup only runs after `_confirm_subprocess_dead` returns `confirmed_dead=True` for the session's subprocess.
- **Harness cancel cleanup (defense in depth):** `_run_harness_subprocess` terminates its `proc` on `CancelledError` in a `finally`, so a cancelled turn cannot leak the child even on a path that bypasses the confirm-dead call.
- **Reaper backstop decision:** answer the issue's open question — either extend the reaper to match worker-parented `claude -p` whose owning session (via a survive-terminal PID field) is terminal, or record an explicit no-go with justification.

### Flow

Health/deadline check flags stuck session → resolve the session's subprocess identity (PID + pgid from the runner-written field) → SIGTERM→SIGKILL the process group and confirm the leader is gone → only then: `running→pending` (requeue) OR `running→failed` + worktree/branch cleanup → reaper backstop sweeps any residual worker-parented process whose session is terminal.

### Technical Approach

**Decision D1 (recommended, flag for critique): unify on one confirm-target field the runner writes.** In `runner._on_turn_spawn` (`runner.py:495-527`), alongside `pm_pid`, populate the field the safety machinery already reads (`claude_pid`) plus a new persisted `claude_pgid` (from `os.getpgid(pid)`, already computed for the in-memory `_TurnHandle`). This makes the entire #1537 confirm-dead path and #1271 reaper work unchanged, with the lowest blast radius. Alternative D2 (health check reads `pm_pid or claude_pid`) spreads field knowledge into `session_health` and still needs pgid plumbing — D1 is cleaner. The exact field name/unification is the one open question for critique.

- **Kill path:** upgrade `_confirm_subprocess_dead` (`session_health.py:1490`) to prefer `os.killpg(pgid, sig)` when a pgid is available, falling back to `os.kill(pid, sig)`. Confirm-exit polls the group-leader PID via `os.kill(pid, 0)`. Keep the existing SIGTERM→grace→SIGKILL escalation and the `run_in_executor` offload. `_apply_recovery_transition` (`:2292`) passes the resolved identity instead of `getattr(entry, "claude_pid", None)`.
- **Ordering:** the queue-supervisor path already calls `_apply_recovery_transition` (which confirms death) BEFORE `exec_task.cancel()` — once confirm-dead actually kills the process, that ordering is correct. Make the guarantee explicit and defensive: in the executor `finally` synthetic-slug cleanup (`session_executor.py:2308`), before `remove_worktree`, assert the session's subprocess PID is gone (probe `os.kill(pid,0)`); if still alive, escalate a kill-and-confirm rather than deleting the worktree. `worktree_busy_check` (`worktree_manager.py:471`) additionally probes OS-process liveness for the owning session's recorded PID, not only DB status.
- **Harness cancel cleanup:** wrap the `_run_harness_subprocess` body (`sdk_client.py:2752-3083`) so a `CancelledError` (or any exit) runs `proc.terminate()` → bounded `await proc.wait()` → `proc.kill()` in a `finally`. Delete the false comment at `agent_session_queue.py:1796-1799` and replace it with the accurate invariant (per NO LEGACY CODE TOLERANCE).
- **Reaper backstop:** `pm_pid` survives the terminal transition (unlike `claude_pid`), so a backstop keyed on "worker-parented `claude -p` whose owning session (resolved via a survive-terminal PID field) is in a terminal state, past a grace window" is feasible. Recommended: implement a bounded backstop gated on terminal-status + grace (not a blanket PPID-gate relaxation, which risks killing live worker children). If the primary fix + explicit ordering fully close the leak in tests, the backstop may be recorded as an explicit no-go instead — critique decides.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_run_harness_subprocess` new `finally` must not swallow the terminate/wait errors silently — assert a `logger.debug/warning` fires when `proc.wait()` times out and SIGKILL is used. Test asserts observable behavior, not `pass`.
- [ ] `_confirm_subprocess_dead` `PermissionError`/`OSError` branches already return `confirmed_dead=False`; extend tests to the killpg variants.

### Empty/Invalid Input Handling
- [ ] `_confirm_subprocess_dead` with `pid=None` AND `pgid=None` must still return `confirmed_dead=True` only when there is genuinely nothing to kill — but the whole point of this fix is that runner sessions now HAVE a pid, so add a regression test that a runner-shaped session (pid set, pgid set) never reaches the `None` short-circuit.
- [ ] Worktree cleanup with a still-alive PID must NOT delete the directory — assert `remove_worktree` refuses (or kills-then-deletes) when `os.kill(pid,0)` succeeds.

### Error State Rendering
- [ ] Not user-facing. The observable "error state" is the log/counter surface: assert the `{project_key}:session-health:*` kill counters increment on the real-PID kill path (they currently never fire for runner sessions).

## Test Impact

- [ ] `tests/unit/test_session_health_subprocess_kill.py::test_none_pid_returns_confirmed_no_signal` — UPDATE: keep as a unit of `_confirm_subprocess_dead` semantics, but it must no longer stand in for real-session behavior; add a sibling asserting runner sessions never hit the None path.
- [ ] `tests/unit/test_session_health_subprocess_kill.py::test_no_pid_recorded_requeues_normally` (`:305`) — REPLACE: this test encodes the BUG (no pid → requeue without killing). Rewrite so a runner-shaped session resolves its pid/pgid, kills the group, confirms exit, then requeues.
- [ ] `tests/unit/test_session_health_subprocess_kill.py` (`_make_entry`, `:202`) — UPDATE: the fixture builds entries with `claude_pid=` only; add `pm_pid`/`pgid` (or the unified field) so it models a runner session.
- [ ] `tests/unit/test_session_health_subprocess_kill.py::test_subprocess_survives_escalates_to_failed` / `test_subprocess_confirmed_dead_requeues_to_pending` — UPDATE: assert process-group signalling (`os.killpg`) rather than single `os.kill`.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: recovery/finalization assertions must reflect that confirm-dead now actually signals for runner sessions.
- [ ] `tests/unit/test_worktree_manager.py` — UPDATE: `worktree_busy_check`/`remove_worktree` tests gain an OS-process-liveness case (alive PID under the worktree → refuse/kill-first).
- [ ] `tests/unit/test_never_started_recovery.py`, `tests/unit/test_session_health_tool_timeout.py` — UPDATE if their entry fixtures assume `claude_pid`-only; audit during build.
- [ ] `tests/unit/test_messenger_callbacks.py::TestMessengerArchitecturalBoundary` — VERIFY unaffected: the runner spawn-record change must keep the messenger ORM-free boundary intact (the runner writes ORM fields, not the messenger).

## Rabbit Holes

- **Rewriting the runner into a standalone `kill()`/`shutdown()` API the health check calls in-process.** The health check runs in a different task and cannot hold the live `_TurnHandle`; a cross-task API needs the persisted PID/pgid anyway. Signal the persisted identity — do not build a new IPC/handle-passing layer.
- **Tracking PID generations to defeat PID reuse.** #1537 already accepted the sub-second-window PID-reuse residual risk. Do not add generation counters; stay consistent with the existing reaper assumptions.
- **Refactoring the two recovery producers (queue supervisor vs health-check loop) into one.** Tempting but out of scope — both already funnel through `_apply_recovery_transition`; fix the shared helper, leave the producers.
- **Broadening `pm_pid`/`claude_pid`/`harness_pid` semantics beyond this fix.** Three PID fields exist for real reasons (dashboard liveness, reaper, heartbeat). Unify only the confirm-target read, do not collapse all three.

## Risks

### Risk 1: Process-group kill hits a group it shouldn't
**Impact:** `os.killpg` on a wrong/reused pgid could signal unrelated worker children.
**Mitigation:** derive pgid at spawn from the child's own PID (`os.getpgid(pid)` under `start_new_session=True`), persist it, and confirm the specific leader PID is gone after signalling. Same sub-second window as #1537; guarded by tests that assert the exact pgid signalled.

### Risk 2: Populating `claude_pid` for runner sessions changes reaper/dashboard behavior
**Impact:** the #1271 reaper and dashboard liveness probe start seeing a populated `claude_pid` for runner sessions.
**Mitigation:** this is a correctness improvement (the field becomes accurate), but audit every `find_by_claude_pid` and dashboard consumer during build; add a test that a live runner session is NOT mis-reaped (heartbeat gate + non-terminal status still protects it).

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
**Data prerequisite:** the session's subprocess identity (pid + pgid) must be persisted by the runner BEFORE any recovery can read it (`_on_turn_spawn` writes it at spawn, before the turn await — same Race-2 ordering #1924 already established).
**State prerequisite:** `_confirm_subprocess_dead` must return `confirmed_dead=True` only after the group leader is actually gone.
**Mitigation:** confirm-dead (process-group SIGTERM→SIGKILL + leader-gone poll) completes before the `running→pending` transition; the requeue cannot proceed until the old group is confirmed dead. This is exactly the #1537 invariant, now pointed at the right PID.

### Race 2: Worktree deleted between confirm-dead and the harness's last filesystem op
**Location:** `agent/session_executor.py:2308-2327` (cleanup) vs the live subprocess's cwd usage.
**Trigger:** cleanup runs while the subprocess still has the worktree as cwd.
**Data prerequisite:** subprocess confirmed exited (Race 1) before `remove_worktree` runs.
**State prerequisite:** no process has the worktree as cwd at removal time.
**Mitigation:** gate cleanup on confirmed-death; `remove_worktree`/`worktree_busy_check` additionally probe OS-process liveness for the owning session's PID before deleting.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1935] Fixing the FALSE `no_progress` classification that triggered these paths (healthy toolless-but-streaming turns misclassified as stuck). Tracked and planned separately in `docs/plans/headless-runner-zombie-liveness.md`. This plan handles what any trigger (false or genuine) does to the live process, independent of #1935.
- Collapsing the three PID fields (`pm_pid`, `claude_pid`, `harness_pid`) into one. Only the confirm-target read is unified here; a broader field-model cleanup is not required to close the leak and would inflate blast radius.

## Update System

No update system changes required for the core fix — it is internal worker/agent logic. **If D1 adds a new persisted AgentSession field (`claude_pgid`):** add an idempotent migration to `scripts/update/migrations.py` and register it in `MIGRATIONS` (per the Popoto Schema Migration Requirement in `docs/sdlc/do-plan.md`). The field is nullable with a `None` default, so existing rows need no backfill (the migration is a no-op index rebuild if required by Popoto). No new dependencies to propagate.

## Agent Integration

No agent integration required — this is a worker-internal subprocess-lifecycle fix. No new CLI entry point, no MCP surface, no `bridge/telegram_bridge.py` change. The behavior is exercised by the worker's health-check and executor paths and verified by unit tests, not by an agent-invokable tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` — add a "Subprocess lifecycle on recovery/failure" subsection documenting: the runner is the authoritative writer of the subprocess identity (pid + pgid); recovery/failure signals the process group and confirms exit before requeue/cleanup; worktree cleanup is gated on confirmed-death.
- [ ] Update `docs/features/bridge-self-healing.md` (or the recovery/reaper doc) — note the reaper-backstop decision (implemented bounded backstop, or explicit no-go) and why worker-parented leaks needed a distinct path from the PPID==1 net.
- [ ] Verify the `docs/features/README.md` index entries still describe these accurately.

### Inline Documentation
- [ ] Replace the false comment at `agent/session_queue.py:1796-1799` with the accurate cancel/terminate invariant.
- [ ] Docstring on `_confirm_subprocess_dead` updated for process-group semantics and the identity it now receives.

## Success Criteria

- [ ] After a `no_progress` recovery, exactly one `claude -p` subprocess exists for the session — the old process group is confirmed exited before respawn (test with monkeypatched `os.killpg`/`os.kill`).
- [ ] After `running → failed`, the session's subprocess is confirmed exited before worktree/branch cleanup runs (test asserts kill-confirm precedes `remove_worktree`).
- [ ] A regression test covering the incident: a kill decision while the subprocess is alive produces no ENOENT-wedged survivor and no ghost pipeline (worktree not removed under a live PID; requeue/fail only after confirmed death).
- [ ] A runner-shaped session never reaches the `_confirm_subprocess_dead(None)` short-circuit — the confirm-target field is populated at spawn.
- [ ] `_run_harness_subprocess` terminates its `proc` on `CancelledError` (test cancels a turn mid-stream, asserts the child is signalled).
- [ ] Reaper-backstop question answered: either a bounded terminal-session backstop is implemented and tested, or an explicit no-go with justification is recorded.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `_apply_recovery_transition` no longer reads bare `getattr(entry, "claude_pid", None)` as the sole kill target.
- [ ] The false "harness kills its own child on cancellation" comment is removed.

## Team Orchestration

The lead agent orchestrates; it deploys builders/validators and coordinates.

### Team Members

- **Builder (kill-path)**
  - Name: kill-path-builder
  - Role: subprocess-identity source of truth + process-group confirm-dead + `_apply_recovery_transition` re-pointing
  - Agent Type: builder
  - Domain: async/subprocess-lifecycle
  - Resume: true

- **Builder (ordering-and-harness)**
  - Name: ordering-builder
  - Role: worktree-cleanup confirmed-death gate + `worktree_busy_check` OS-liveness probe + `_run_harness_subprocess` cancel `finally`
  - Agent Type: builder
  - Domain: async/subprocess-lifecycle
  - Resume: true

- **Builder (reaper-backstop)**
  - Name: reaper-builder
  - Role: implement bounded terminal-session reaper backstop OR record the no-go (per critique decision)
  - Agent Type: builder
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

### 1. Subprocess-identity source of truth + process-group confirm-dead
- **Task ID**: build-kill-path
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_subprocess_kill.py, tests/unit/test_health_check_recovery_finalization.py
- **Assigned To**: kill-path-builder
- **Agent Type**: builder
- **Domain**: async/subprocess-lifecycle
- **Parallel**: true
- Record the confirm-target identity (pid + pgid) at spawn in `runner._on_turn_spawn` per decision D1; persist pgid (new nullable field if needed) and register the migration.
- Upgrade `_confirm_subprocess_dead` to prefer `os.killpg` with single-`os.kill` fallback; confirm leader PID gone.
- Re-point `_apply_recovery_transition` to pass the resolved identity, not bare `claude_pid`.

### 2. Ordering guarantee + harness cancel cleanup
- **Task ID**: build-ordering
- **Depends On**: none
- **Validates**: tests/unit/test_worktree_manager.py, tests/unit/test_session_health_subprocess_kill.py
- **Assigned To**: ordering-builder
- **Agent Type**: builder
- **Domain**: async/subprocess-lifecycle
- **Parallel**: true
- Gate synthetic-slug cleanup (`session_executor.py:2308`) on confirmed-death; add OS-liveness probe to `worktree_busy_check`/`remove_worktree`.
- Add `try/finally` terminate to `_run_harness_subprocess`; delete and correct the false `agent_session_queue.py:1796-1799` comment.

### 3. Reaper backstop (or no-go)
- **Task ID**: build-reaper
- **Depends On**: build-kill-path
- **Validates**: tests/unit/ (reaper backstop test if implemented)
- **Assigned To**: reaper-builder
- **Agent Type**: builder
- **Parallel**: false
- Per critique decision: implement bounded terminal-session backstop keyed on a survive-terminal PID field + grace, OR record the explicit no-go with justification.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-kill-path, build-ordering, build-reaper
- **Assigned To**: leak-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite; verify every success criterion, especially the incident-regression test.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/headless-session-runner.md` and the self-healing doc.

### 6. Final Validation
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
| Kill path no longer keys solely on claude_pid | `grep -n "getattr(entry, \"claude_pid\", None)" agent/session_health.py` | exit code 1 |
| False cancel comment removed | `grep -rn "harness kills its own" agent/agent_session_queue.py` | exit code 1 |
| Process-group kill present | `grep -n "killpg" agent/session_health.py` | output contains killpg |
| Harness cancel finally present | `grep -n "proc.terminate\|proc.kill" agent/sdk_client.py` | output contains proc |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **PID-field unification direction (D1 vs D2).** Recommended D1: the runner populates `claude_pid` + new `claude_pgid` at spawn so all existing #1537/#1271 machinery works unchanged. D2 keeps the runner as-is and makes `session_health` read `pm_pid or claude_pid`. D1 reuses more, D2 is a smaller runner diff. Confirm D1?
2. **Reaper backstop: implement or no-go?** `pm_pid` survives terminal transitions, making a bounded terminal-session backstop feasible. Is it worth the risk of relaxing the reaper's net (mitigated by terminal-status + grace gating), or does the primary fix + explicit ordering close the leak sufficiently to record it as a no-go?
3. **Process-group vs single-PID kill.** The `claude -p` spawns MCP/subagent children in its own group (`start_new_session=True`). Confirm we should `killpg` the whole group (recommended) rather than only the leader — the leader-only kill is what leaves orphaned MCP servers.
