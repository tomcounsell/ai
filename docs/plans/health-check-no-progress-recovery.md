---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/944
last_comment_id:
revision_applied: true
---

# Health Check No-Progress Recovery

## Problem

The periodic session health check in `agent/agent_session_queue.py` fails to recover stuck slugless dev sessions within the expected 5-minute cycle when a PM session is running under the same project key. The stuck session is held hostage by the PM's liveness signal and is only recovered after the 45-minute timeout path — 9x longer than the intended health-check cadence.

**Current behavior:**

`_agent_session_health_check()` resolves liveness at `agent/agent_session_queue.py:1393-1394`:

```python
worker = _active_workers.get(worker_key)   # keyed by project, e.g. "valor"
worker_alive = worker is not None and not worker.done()
```

For slugless dev sessions, `worker_key = project_key` (see `models/agent_session.py:274-277`). If a PM session is running under that key, `worker_alive = True` — the `not worker_alive` branch is skipped, the `elif started_ts is not None` branch only checks the 45-minute timeout, and the stuck session falls through with nothing to recover it.

**Observed evidence (2026-04-14):** Two local `"test"` dev sessions were created at ~04:09–04:10 UTC, got stuck in `running` with `turn_count=0` and empty `log_path`, and were not recovered by health checks at 04:13 and 04:15 — because a PM session for `"valor"` had started at 04:11 and kept `worker_alive = True`.

**Desired outcome:**

A stuck slugless dev session (no `turn_count`, no `log_path`) is recovered within one health-check cycle (≤5 minutes) after the 300-second startup guard expires, even when a PM session is actively running under the same `worker_key`. Sessions with real progress are untouched.

## Freshness Check

**Baseline commit:** `536318c37b668271abd34d04dbcb4cd4f67d1aec` (main)
**Issue filed at:** 2026-04-14T04:22:11Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:1353` — `_agent_session_health_check` entry point — still present at stated line.
- `agent/agent_session_queue.py:1402-1427` — `worker_alive` branch — confirmed as the exact failure point; no `progress` signal exists anywhere in this block.
- `agent/agent_session_queue.py:1456` — `is_local` branch — confirmed preserved; fix must route through this same branch.
- `agent/agent_session_queue.py:67` — `AGENT_SESSION_HEALTH_MIN_RUNNING = 300` — still 300s.
- `models/agent_session.py:262` — `worker_key` property — confirmed: slugless dev sessions return `project_key` at line 277.
- `models/agent_session.py:174,176` — `turn_count = IntField(default=0)` and `log_path = Field(null=True)` — both present, matching the progress signal the issue proposes.

**Cited sibling issues/PRs re-checked:**
- #727 — CLOSED — introduced the 300s startup guard; windows this bug exploits. Still relevant context.
- #917 — CLOSED — added `response_delivered_at` guard (`agent/agent_session_queue.py:1432-1454`); the new no-progress path must preserve this guard.
- #871 — CLOSED — documented split recovery ownership; `running` is still owned by the worker health check.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-14T04:22:11Z" -- agent/agent_session_queue.py` returned zero commits.

**Active plans in `docs/plans/` overlapping this area:** None — `grep -r "_agent_session_health_check" docs/plans/` returned no matches.

**Notes:** Issue filed ~5 minutes before plan time. No drift. Proceed to Phase 1 against baseline commit above.

## Prior Art

- **PR #745 (merged 2026-04-06)**: "fix: startup recovery timing guard to prevent worker race" — introduced `AGENT_SESSION_HEALTH_MIN_RUNNING = 300`. This guard is the reason a stuck session survives the first 5 minutes. The current fix is complementary: we respect the guard, then after it expires, allow no-progress sessions to be recovered regardless of `worker_alive`.
- **Issue #918 (closed 2026-04-12)**: "Bridge delivers same message multiple times to same session" — the upstream delivery-duplication bug that introduced `response_delivered_at`. The docstring at `agent/agent_session_queue.py:1371` cites this issue as the rationale for the delivery guard. Referenced here for completeness; the follow-up tracking is #917.
- **Issue #917 (closed 2026-04-13)**: "health-check-recovered sessions not finalized — causes duplicate Telegram delivery". Added the `response_delivered_at` delivery guard logic at `agent/agent_session_queue.py:1432-1454`. This guard fires before recovery and finalizes already-delivered sessions as `completed` to prevent duplicate delivery. The new no-progress path must run *through* this same guard, not around it — no changes to delivery semantics. **Reconciliation note (A1 concern):** #918 and #917 are sibling issues — #918 is the upstream user-visible duplication bug; #917 is the specific health-check-path follow-up that landed the guard code. Both references are correct. No docstring change required.
- **Issue #871 (closed)**: Documented which systems recover which statuses. The worker owns `running` via `_agent_session_health_check`; the bridge watchdog owns `active/dormant/paused/paused_circuit`. This fix stays entirely within the worker's ownership.

## Data Flow

1. **Entry point**: Health check scheduler in the worker loop wakes every 300s and calls `_agent_session_health_check()`.
2. **Query**: `AgentSession.query.filter(status="running")` returns all `running` sessions for this process.
3. **Per-session evaluation**: For each session, compute `worker_key`, look up `_active_workers[worker_key]`, and set `worker_alive`.
4. **Today's decision tree** (`agent/agent_session_queue.py:1399-1427`):
   - `not worker_alive` → recovery if past guard.
   - `worker_alive and started_ts` → recovery only if past 45-min timeout.
   - `worker_alive and no progress and past guard` → **currently unhandled** (the bug).
5. **Recovery branch** (`agent/agent_session_queue.py:1429-1499`): delivery guard → `is_local` split → `finalize_session("abandoned")` for local, `transition_status("pending")` + `_ensure_worker()` for project-keyed.
6. **Output**: The stuck session either becomes `abandoned` (local CLI) or `pending` with priority bumped to `high` and a fresh worker ensured. **AD2 verification:** `_pop_agent_session` at `agent/agent_session_queue.py:620` filters only by `project_key`/`worker_key` and status — there is no `session_type` filter (confirmed at lines 670-677). The existing PM-associated worker loop will therefore pop and execute the recovered slugless Dev session on its next iteration. A regression test (`test_recovered_dev_session_popped_by_shared_pm_worker`) in Task 1 locks this assumption in so future refactors of `_pop_agent_session` cannot silently regress it.

The fix inserts one new condition into step 4 (the decision tree) between the existing `not worker_alive` branch and the existing `elif started_ts is not None` branch. Steps 5 and 6 remain unchanged.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None — only internal logic in `_agent_session_health_check`.
- **Coupling**: Unchanged. The fix reuses existing fields (`turn_count`, `log_path`, `claude_session_uuid`) already persisted on `AgentSession`.
- **Data ownership**: Unchanged. `running` status remains owned by the worker health check.
- **Reversibility**: Trivial — single-file revert covering the new `_has_progress` helper, the new `elif` branch in `_agent_session_health_check`, and a docstring renumbering.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (straightforward localized bug fix)
- Review rounds: 1 (standard PR review on a recovery path)

The fix is ~15 lines of code in one function plus a focused unit test. No API changes, no new dependencies, no migration.

## Prerequisites

No prerequisites — this work modifies an existing function in-place using fields that already exist on `AgentSession`. No environment variables, external services, or new deps.

## Solution

### Key Elements

- **Progress signal**: A running session is considered "in flight" iff `turn_count > 0` OR `log_path` is a non-empty string OR `claude_session_uuid` is set. This reuses existing fields already populated by the worker loop when real work happens. **S1 resolution:** `claude_session_uuid` is populated by the SDK subprocess as soon as it authenticates with the Claude API — well before `turn_count` reaches 1 on slow-starting BUILD sessions. Including it in the progress signal means a healthy SDK-subprocess session that happens to take >300s to emit its first turn is NOT misclassified as no-progress. The three fields together cover the full warmup-to-execution arc: uuid set at auth, log_path written at first tool call, turn_count incremented at first full agent turn.
- **No-progress recovery condition**: When `worker_alive = True`, `running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING`, AND the session has no progress → treat as orphaned and recover via the existing recovery branch (delivery guard → local/project split).
- **Preserved invariants**: The `response_delivered_at` delivery guard still fires first. The `is_local` branch still routes local sessions to `abandoned` and project-keyed sessions to `pending`. The 45-minute timeout path still applies to sessions that *are* making progress.

### Flow

Health check tick → Query `running` sessions → For each session, compute `worker_key`, `worker_alive`, `running_seconds`, and `has_progress` → Decision:

- `not worker_alive` AND past guard → recover (existing).
- `worker_alive` AND past guard AND NOT `has_progress` → recover (**new**).
- `worker_alive` AND past 45-min timeout → recover (existing).
- Otherwise → skip.

All recovery paths route through the same downstream block: delivery guard → `is_local` split → finalize or transition.

### Technical Approach

- Add a module-level helper `_has_progress(entry: AgentSession) -> bool` that returns `(entry.turn_count or 0) > 0 or bool((entry.log_path or "").strip()) or bool(entry.claude_session_uuid)`. Place it near the other module-level helpers above `_agent_session_health_check`. The three-field signal covers the SDK subprocess warmup arc (uuid → log_path → turn_count) so long-starting BUILD sessions are not misclassified.
- Extend the decision tree in `_agent_session_health_check`. The cleanest shape is a new `elif` branch between the existing `if not worker_alive:` block (ending at `agent/agent_session_queue.py:1422`) and the existing `elif started_ts is not None:` block (line 1423). The new branch:

  ```python
  elif (
      running_seconds is not None
      and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING
      and not _has_progress(entry)
  ):
      should_recover = True
      reason = (
          f"worker alive but no progress signal, running for "
          f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard, "
          f"turn_count={entry.turn_count}, log_path={entry.log_path!r}, "
          f"claude_session_uuid={entry.claude_session_uuid!r})"
      )
  ```

  After this new branch, the original `elif started_ts is not None:` (45-min timeout) remains — it now acts as the fallback for sessions that have `worker_alive`, are past the guard, but do have progress. Keep its shape identical.

- Leave the delivery guard (`response_delivered_at`, lines 1432-1454) and the `is_local` branch (line 1456) untouched. The new path funnels into the same downstream code.

- Log at `warning` level when recovering a no-progress session (consistent with the existing recovery log at line 1457).

- **Optional O1 observability hook:** At the single `should_recover` exit point (just before `transition_status`/`finalize_session` dispatch), increment a project-scoped Redis counter to distinguish recovery reasons. Use the existing Popoto Redis connection (`from popoto.redis_db import POPOTO_REDIS_DB as _R`) and increment keyed by reason kind: `_R.incr(f"{project_key}:session-health:recoveries:{reason_kind}")` where `reason_kind ∈ {"worker_dead", "no_progress", "timeout"}`. This is ONE `INCR` per recovery (a rare path — recoveries are minutes apart at most). Failure of the counter write must not affect recovery — wrap in `try/except`. Dashboards can then plot recoveries-by-reason over time to catch misfires early. If this hook is skipped, Task 3 Documentation must include an explicit grep pattern + expected-rate-ceiling note in `docs/features/bridge-self-healing.md` so operators can diagnose a misfire from logs alone.

## Failure Path Test Strategy

### Exception Handling Coverage

The touched block already has a surrounding `try/except` at `agent/agent_session_queue.py:1391` (caught at the per-session level so one bad session doesn't abort the whole check). No new exception handlers are introduced. The new branch reads only existing fields (`turn_count`, `log_path`, `claude_session_uuid`) with safe fallbacks.

- [x] No new `except Exception: pass` blocks introduced in scope.
- [x] Existing exception handling at line 1391 already covers the per-session loop body.
- [x] The optional O1 counter `_R.incr(...)` is wrapped in `try/except` so counter failures cannot block recovery.

### Empty/Invalid Input Handling

- [x] `turn_count` is `IntField(default=0)` — `(entry.turn_count or 0) > 0` handles default, negative (impossible), and None defensively.
- [x] `log_path` is `Field(null=True)` — `(entry.log_path or "").strip()` handles None, empty string, and whitespace-only.
- [x] `claude_session_uuid` is a nullable field — `bool(entry.claude_session_uuid)` handles None, empty string, and non-empty uuid correctly.
- [x] `running_seconds` is computed as `now - started_ts` when `started_ts` is truthy, else `None`. The new branch's guard `running_seconds is not None` prevents `TypeError` on None compare. This mirrors the existing branch at line 1406.

### Error State Rendering

- [x] Recovery emits a `logger.warning` at line 1457 with session ID, chat/worker key, and reason. The new reason string includes `turn_count`, `log_path`, and `claude_session_uuid` so an operator grepping logs can confirm the session was picked up by the no-progress path, not the legacy worker-dead path.
- [x] No user-visible output — this is a background health check.
- [x] Optional O1 Redis counter: `session-health:recoveries:no_progress` counter incremented on each no-progress recovery; `session-health:recoveries:worker_dead` and `session-health:recoveries:timeout` counters incremented on the existing paths for comparability.

## Test Impact

- [x] `tests/unit/test_agent_session_queue.py::TestHealthCheckDeliveryGuard` — UPDATE: add a new test class `TestHealthCheckNoProgressRecovery` alongside it with the 7 cases listed in Task 1 (including the AD2 regression `test_recovered_dev_session_popped_by_shared_pm_worker` and the AD1 race-acceptance `test_progress_written_between_check_and_transition_is_lost_but_session_retries`). The existing delivery-guard tests should remain green (they use sessions with `worker_alive=False`; the new branch only fires when `worker_alive=True`).
- [x] `tests/unit/test_health_check_recovery_finalization.py` — VERIFY: this file covers `running → abandoned/completed` transitions. Confirm the existing tests still pass after the fix; no changes needed unless a test happens to exercise a session with `worker_alive=True`, past the guard, and no progress — in which case the outcome changes (which is the intent).
- [x] `tests/integration/test_agent_session_health_monitor.py` — VERIFY: integration test; confirm the assertions still hold and add one new assertion that a `turn_count=0`/empty-`log_path` running session with a live project-keyed worker is recovered after the guard window.
- [x] `tests/unit/test_recovery_respawn_safety.py`, `tests/unit/test_stall_detection.py`, `tests/integration/test_agent_session_queue_race.py` — VERIFY: these also exercise the health-check code path. Run them after the fix and confirm no regressions.

No tests are deleted. No existing assertions are inverted. The fix is additive in its recovery surface — it widens the set of sessions recovered by adding a new qualifying condition, without changing outcomes for any session that was already being handled.

## Rabbit Holes

- **Changing `worker_key` semantics for slugless dev sessions.** Tempting fix: give them a distinct `worker_key` so `worker_alive` correctly reflects only their own process. Out of scope — would alter serialization semantics project-wide and require migration reasoning. The issue itself explicitly puts this in the "Dropped" bucket.
- **Adding `updated_at` as a progress signal.** The health check writes `updated_at` on each tick, so using it as a liveness signal is circular. The issue's recon explicitly rejected this; do not revisit.
- **Overhauling the worker-keying architecture.** PM + slugless dev sharing `project_key` is an intentional serialization design. Do not try to decouple them here.
- **Broadening the progress signal beyond the three chosen fields.** `turn_count`, `log_path`, and `claude_session_uuid` together cover the SDK warmup arc (S1 resolution). Do not add `tool_call_count`, event counts, `updated_at`-adjacent fields, or other signals — extra fields without a concrete failure case is scope creep.

## Risks

### Risk 1: A legitimate slow-start session is misclassified as no-progress and recovered
**Impact:** A dev session that genuinely hasn't reached `turn_count > 0` yet (e.g. SDK subprocess taking > 300s to emit its first turn) gets recovered mid-flight, orphaning the subprocess.
**Mitigation (S1 concern resolution):** `_has_progress` checks three fields — `turn_count`, `log_path`, and `claude_session_uuid`. The SDK subprocess writes `claude_session_uuid` at the moment it authenticates with the Claude API, which happens within seconds of subprocess launch and well before the first turn completes. A long-warmup BUILD session that takes 600s to produce its first turn will still have `claude_session_uuid` set within the first 30 seconds, so `_has_progress` returns `True` throughout its warmup and the no-progress branch does not fire. This widens the safe window from "first turn must complete in 300s" to "SDK must authenticate in 300s" — a much safer threshold, since auth is an order of magnitude faster than turn production. The 300s `AGENT_SESSION_HEALTH_MIN_RUNNING` guard remains as belt-and-suspenders. If `claude_session_uuid` is somehow not set within 300s on a real session (e.g. an API credential failure hanging the auth handshake), that's a legitimate stuck session — the recovery is correct.

### Risk 2: Interaction with the `response_delivered_at` delivery guard (#917)
**Impact:** A no-progress session with `response_delivered_at` set would be wrongly finalized as `completed` instead of `abandoned`.
**Mitigation:** A no-progress session has `turn_count=0` and no log path. Such a session cannot have delivered a response — the delivery guard path is unreachable for it in practice. Nonetheless, the fix routes through the same delivery guard block (`agent/agent_session_queue.py:1432-1454`) for defense in depth. The guard still fires first; if it ever trips, `finalize_session("completed")` is correct because `response_delivered_at` being set is authoritative — it means the response made it out regardless of turn count.

### Risk 3: Local vs. project-keyed classification
**Impact:** A local CLI session gets reset to `pending` and loops forever (no worker to pick it up), or a project-keyed session gets `abandoned` and is never retried.
**Mitigation:** The fix reuses the existing `is_local = worker_key.startswith("local")` branch at `agent/agent_session_queue.py:1456` — routing is unchanged. Test: add explicit assertions for both the local ("abandoned") and project-keyed ("pending") outcomes with a no-progress session.

## Race Conditions

### Race 1: Progress signal updated between check and recovery
**Location:** `agent/agent_session_queue.py:1392-1499`
**Trigger:** The health check reads `entry.turn_count`, `entry.log_path`, and `entry.claude_session_uuid` at line 1389 (once per iteration), decides `no progress`, then the real worker writes one of those fields (e.g. `turn_count = 1`) before `transition_status("pending")` executes at line 1489.
**Data prerequisite:** `entry` is loaded once at the top of the per-session loop iteration (line 1389 from the query result). The session's true state in Redis may diverge from the in-memory `entry` during the recovery window.
**State prerequisite:** `transition_status` at `models/session_lifecycle.py:391` performs a CAS (compare-and-set) re-read **on the `status` field only**. If the status has already moved from `running` (e.g. the worker completed it legitimately), the CAS fails and the transition is skipped. This protects against the "session completed between check and recovery" race, but NOT against the "session wrote progress but is still running" race — progress fields (`turn_count`, `log_path`, `claude_session_uuid`) are not part of the CAS comparison.
**Mitigation — honest accounting:** We accept a rare false-positive re-queue window. In the tight span (milliseconds) between reading `entry` and calling `transition_status`, a worker that just started producing progress may have its in-flight work re-queued. The status CAS does not protect the progress fields — only the status field. The probability is low because (a) the 300s startup guard covers typical warmup, (b) the SDK writes `claude_session_uuid` at auth time, much earlier than turn_count increments, making the detection window for "actually starting" much larger, and (c) the health-check cadence is 5 minutes so the same session won't be evaluated twice in quick succession. The fallback behavior (session gets re-queued with `priority=high` and a fresh `_ensure_worker` call) is benign — the worker loop pops it again and runs it from scratch. Acceptable risk; no code-level mitigation beyond the broadened progress signal.

A test in Task 1 (`test_progress_written_between_check_and_transition_is_lost_but_session_retries`) documents the acceptance by explicitly simulating the race and asserting the session is re-queued (confirming the chosen behavior is intentional, not accidental).

## No-Gos (Out of Scope)

- Changing `worker_key` semantics for slugless dev sessions (per issue's Dropped bucket).
- Using `updated_at` as a progress signal (per issue's Revised bucket — health check itself writes it, circular).
- Tuning `AGENT_SESSION_HEALTH_MIN_RUNNING`. The current 300s guard is untouched.
- Recovering sessions that ARE making progress but whose worker happens to be dead. That's already handled by the existing `not worker_alive` branch at `agent/agent_session_queue.py:1402`.
- Refactoring `_agent_session_health_check` beyond the minimum needed to add the new branch. The function is long, but the fix is localized; broader refactoring is a separate concern.
- Bridge-watchdog code (`monitoring/session_watchdog.py`). The worker owns `running` recovery per #871.

## Update System

No update system changes required. The fix modifies one function in existing Python code. No new dependencies, config files, env vars, or migration steps. The standard `/update` pull-and-restart flow is sufficient.

## Agent Integration

No agent integration required. This is a worker-internal change. The health check is not exposed via MCP and is not invoked by the agent. The bridge is not affected.

## Documentation

### Feature Documentation
- [x] Update `docs/features/bridge-self-healing.md` (or the closest doc on the health-check system) with one paragraph describing the no-progress recovery path. If no existing doc describes `_agent_session_health_check` in detail, add a short section to `docs/features/bridge-worker-architecture.md` under the worker's responsibilities.
- [x] Grep `docs/` for references to `AGENT_SESSION_HEALTH_MIN_RUNNING` and `_agent_session_health_check` and update any that describe the recovery decision tree.

### Inline Documentation
- [x] Update the docstring of `_agent_session_health_check` at `agent/agent_session_queue.py:1353-1381` to list the new no-progress branch as item 2 (renumber existing items).
- [x] Add a one-line comment above the new branch explaining the rationale: "Project-keyed dev sessions share worker_key with PM; without a progress signal, worker_alive alone doesn't prove the dev session is being handled."

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs site.

## Success Criteria

- [x] **User-framed outcome:** Time-to-recovery for a stuck slugless Dev session drops from ~45 min to ≤5 min when a PM co-runs on the same project key. Verified by reproducing the 2026-04-14 scenario (two local `"test"` dev sessions stuck with `turn_count=0`/empty `log_path` while a `"valor"` PM is active) in a test fixture or staging run.
- [x] A slugless dev session stuck in `running` with `turn_count=0`, empty `log_path`, and empty `claude_session_uuid` is recovered (→ `abandoned` for local sessions, → `pending` for project-keyed sessions) within one health-check cycle after the startup guard expires, even when a PM session is actively running under the same `worker_key`.
- [x] A legitimately active dev session (ANY of `turn_count > 0`, non-empty `log_path`, or non-empty `claude_session_uuid`) is NOT incorrectly recovered while its worker is alive.
- [x] New unit test: mock `_active_workers` with a live project-keyed task, create a session with all three progress fields empty, advance time past the guard, assert the health check recovers it via the expected path (local → abandoned, project-keyed → pending).
- [x] New unit test: parametrized over the progress-field truth table, assert non-recovery whenever ANY of `turn_count`, `log_path`, or `claude_session_uuid` is set.
- [x] New unit test `test_recovered_dev_session_popped_by_shared_pm_worker`: asserts `_pop_agent_session(worker_key, is_project_keyed=True)` returns the recovered Dev session even when a PM owns the worker task (AD2 regression lock-in).
- [x] New unit test `test_progress_written_between_check_and_transition_is_lost_but_session_retries`: asserts the AD1 race behavior — a session that writes progress AFTER `entry` is loaded but BEFORE `transition_status` runs is re-queued (documented acceptable false-positive).
- [x] All existing tests in `tests/unit/test_agent_session_queue.py`, `tests/unit/test_health_check_recovery_finalization.py`, `tests/unit/test_recovery_respawn_safety.py`, `tests/unit/test_stall_detection.py`, `tests/integration/test_agent_session_health_monitor.py`, and `tests/integration/test_agent_session_queue_race.py` still pass.
- [x] No regression to the 45-minute timeout path — sessions with progress still hit that fallback when their worker appears alive for longer than the timeout.
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (health-check-no-progress)**
  - Name: `health-check-builder`
  - Role: Implement the no-progress branch in `_agent_session_health_check` and add unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (health-check-no-progress)**
  - Name: `health-check-validator`
  - Role: Verify new branch behavior against Success Criteria; run existing health-check tests and confirm no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian (health-check-no-progress)**
  - Name: `health-check-documentarian`
  - Role: Update inline docstring and feature docs per the Documentation section
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement no-progress branch and tests

- **Task ID**: build-no-progress-branch
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py` (new `TestHealthCheckNoProgressRecovery` class), all existing health-check tests.
- **Informed By**: Issue #944 Solution Sketch, prior art #727 (startup guard) and #917 (delivery guard).
- **Assigned To**: `health-check-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `agent/agent_session_queue.py`, add a module-level helper `def _has_progress(entry: AgentSession) -> bool:` returning `(entry.turn_count or 0) > 0 or bool((entry.log_path or "").strip()) or bool(entry.claude_session_uuid)`. Place it near the other module-level helpers above `_agent_session_health_check`.
- In `_agent_session_health_check`, insert a new `elif` branch between the existing `if not worker_alive:` block and the existing `elif started_ts is not None:` block. The new branch fires when `running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING` AND `not _has_progress(entry)`. Set `should_recover = True` with a `reason` string that includes `turn_count`, `log_path`, and `claude_session_uuid` for operator visibility.
- Optional O1 observability hook: at the `should_recover` dispatch point, classify the reason (`worker_dead` / `no_progress` / `timeout`) and increment `f"{project_key}:session-health:recoveries:{reason_kind}"` via `popoto.redis_db.POPOTO_REDIS_DB.incr`. Wrap in `try/except` — counter failure must not affect recovery. If this hook is skipped, ensure Task 3 documents the log grep pattern for operators.
- Update the docstring at `agent/agent_session_queue.py:1353-1381`: add the new branch as item 2 under "For RUNNING sessions" and renumber.
- Add a new test class `TestHealthCheckNoProgressRecovery` in `tests/unit/test_agent_session_queue.py` with these cases:
  - `test_no_progress_project_keyed_recovered_to_pending`: slugless dev session, `worker_alive=True` via a live mock task, `turn_count=0`, empty `log_path`, empty `claude_session_uuid`, `started_at` > 300s ago → assert `transition_status` called with `"pending"` and `finalize_session` NOT called.
  - `test_no_progress_local_session_abandoned`: local session (worker_key starts with `"local"`), same preconditions → assert `finalize_session("abandoned", ...)`.
  - `test_with_progress_not_recovered_parametrized` (SI1 NIT — parametrized truth table): `@pytest.mark.parametrize("turn_count,log_path,claude_session_uuid", [(2, None, None), (2, "/tmp/x.jsonl", None), (0, "/tmp/x.jsonl", None), (0, None, "uuid-abc-123"), (0, "", "uuid-abc-123")])` — `worker_alive=True`, past guard but under timeout → assert neither `transition_status` nor `finalize_session` called. Covers "any one of the three progress fields suffices".
  - `test_no_progress_under_guard_not_recovered`: all progress fields empty, `started_at` only 60s ago → under the guard, not recovered.
  - `test_no_progress_with_delivered_response_finalized_completed`: no-progress path intersects with `response_delivered_at` → must hit the delivery guard first and finalize as `completed` (defensive test for Risk 2).
  - **`test_recovered_dev_session_popped_by_shared_pm_worker`** (AD2 regression lock-in): create a slugless dev session sharing `worker_key = project_key` with a live PM task in `_active_workers`. Trigger the no-progress recovery. Assert (a) the session status becomes `"pending"`, and (b) `await _pop_agent_session(worker_key, is_project_keyed=True)` returns the same session — confirming `_pop_agent_session` does NOT filter by `session_type` and the existing PM-associated worker loop will execute the recovered dev session.
  - **`test_progress_written_between_check_and_transition_is_lost_but_session_retries`** (AD1 race acceptance): start with a no-progress session past the guard. Patch `transition_status` via `side_effect` to set `entry.turn_count = 1` on the in-memory `entry` just before its call (simulating a concurrent progress write). Assert the session is still transitioned to `"pending"` (the status CAS does not protect progress fields). This locks in the documented acceptable false-positive behavior.
- Run the new tests and confirm they pass. Run the existing full file: `pytest tests/unit/test_agent_session_queue.py -v`.

### 2. Validate

- **Task ID**: validate-no-progress-branch
- **Depends On**: build-no-progress-branch
- **Assigned To**: `health-check-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_queue.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_recovery_respawn_safety.py tests/unit/test_stall_detection.py -v`.
- Run `pytest tests/integration/test_agent_session_health_monitor.py tests/integration/test_agent_session_queue_race.py -v`.
- Run `python -m ruff check agent/agent_session_queue.py tests/unit/test_agent_session_queue.py` and `python -m ruff format --check agent/agent_session_queue.py tests/unit/test_agent_session_queue.py`.
- Verify each item in Success Criteria against the test results. Report pass/fail.

### 3. Documentation

- **Task ID**: document-no-progress-branch
- **Depends On**: validate-no-progress-branch
- **Assigned To**: `health-check-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update the docstring rename noted in task 1 (confirm it landed).
- Update `docs/features/bridge-self-healing.md` or `docs/features/bridge-worker-architecture.md` with a short paragraph describing the no-progress recovery branch: what it detects, why it's needed (slugless dev + PM share `worker_key`), and the `turn_count`/`log_path`/`claude_session_uuid` progress signal.
- **A1 reconciliation (from critique):** The docstring at `agent/agent_session_queue.py:1371` correctly cites #918 (the upstream delivery-duplication bug). Prior Art cites both #918 and #917 (health-check follow-up). No docstring change needed for this concern — confirm both references are consistent and leave as-is.
- **O1 documentation (if counter hook skipped):** If the optional Redis counter was not implemented in Task 1, add a subsection to `docs/features/bridge-self-healing.md` titled "Diagnosing no-progress recoveries" with: (a) the log grep pattern `grep "worker alive but no progress signal" logs/worker.log`, (b) expected rate ceiling (≤ 1 per project per hour under normal operation), and (c) what a misfire looks like (bursts of no-progress recoveries for sessions that should be healthy — indicates `AGENT_SESSION_HEALTH_MIN_RUNNING` is too short or the progress signal is too narrow).
- Grep `docs/` for `_agent_session_health_check` and `AGENT_SESSION_HEALTH_MIN_RUNNING` — update any doc that describes the recovery decision tree to include the new branch.

### 4. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-no-progress-branch
- **Assigned To**: `health-check-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -n auto -q` and confirm green.
- Run `python -m ruff check . && python -m ruff format --check .` and confirm clean.
- Confirm all Success Criteria boxes can be checked.
- Generate the final report for the PR body.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_agent_session_queue.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_recovery_respawn_safety.py tests/unit/test_stall_detection.py -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_agent_session_health_monitor.py tests/integration/test_agent_session_queue_race.py -q` | exit code 0 |
| Full unit suite green | `pytest tests/unit/ -n auto -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py tests/unit/test_agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py tests/unit/test_agent_session_queue.py` | exit code 0 |
| New branch exists | `grep -n "_has_progress" agent/agent_session_queue.py` | output > 0 |
| New test class exists | `grep -n "TestHealthCheckNoProgressRecovery" tests/unit/test_agent_session_queue.py` | output > 0 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — war-room run 2026-04-14. 0 blockers, 4 concerns, 4 nits. All concerns resolved via revision pass; `revision_applied: true` set in frontmatter.

**Concerns — resolutions embedded in plan text:**

| # | Critic | Concern | Resolution (location in plan) |
|---|--------|---------|-------------------------------|
| S1 | Skeptic | 300s guard may be too short for BUILD-session time-to-first-turn | Broadened `_has_progress` to include `claude_session_uuid` (auth-time signal). See **Solution → Key Elements** and **Risk 1** mitigation. |
| A1 | Archaeologist | Docstring #918 vs Prior Art #917 citation mismatch | Verified via `gh issue view`: #918 (upstream) and #917 (follow-up) are distinct. Both references correct. Added Prior Art row for #918. See **Prior Art** reconciliation note. |
| AD1 | Adversary | Race mitigation conflates status CAS with progress CAS | Rewrote Race 1 mitigation honestly: status CAS does NOT protect progress fields; accept rare false-positive re-queue. Added test `test_progress_written_between_check_and_transition_is_lost_but_session_retries` to lock in the behavior. See **Race Conditions → Race 1**. |
| AD2 | Adversary | Recovery path assumes same-key PM worker pops Dev pending sessions | Verified `_pop_agent_session` (`agent/agent_session_queue.py:620-677`) filters only by `project_key`/`worker_key`/`status` — no `session_type` filter. Fix is sound. Added regression test `test_recovered_dev_session_popped_by_shared_pm_worker` to lock in the assumption. See **Data Flow → step 6**. |

**Nits — all applied:**
- SI1: Redundant tests collapsed into parametrized `test_with_progress_not_recovered_parametrized`.
- SI2: Team Orchestration left as-is (template-required structure).
- Skeptic hedge: "inline is fine" sentence removed from Technical Approach.
- Operator: Reversibility rephrased as "single-file revert".
- Operator O1: Optional Redis counter hook added to Technical Approach; fallback documentation subtask added to Task 3.
- User U1: Added user-framed Success Criterion (time-to-recovery drops from 45min to ≤5min).

---

## Open Questions

No open questions — all critique concerns resolved via revision pass; plan is ready for `/do-build`.
