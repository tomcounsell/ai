---
status: Planning
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-22
tracking: https://github.com/tomcounsell/ai/issues/1098
last_comment_id: 4291256483
---

# Worker Kickstart Race — Fix False "System Degraded" Alarm

## Problem

Every `/update` run logs a false-positive error even though the worker starts successfully:

```
ERROR: Worker not running after kickstart retry — system degraded
```

The alarm fires on machines where the worker's post-`bootstrap` launch is delayed by launchd's `ThrottleInterval` (10 seconds), the fallback `kickstart -k` then races against the same throttle window, and the 16-second retry poll expires before the new worker writes its heartbeat.

**Current behavior:**
- `/update` orchestrator runs `install_worker()` (bootout + bootstrap), waits 30s for heartbeat
- If no process yet (launchd still holding throttle), it fires a `kickstart -k` fallback at line 834
- Retries for 16s (8 iterations × 2s sleep)
- If heartbeat still isn't fresh, logs `ERROR: Worker not running after kickstart retry — system degraded` and sets `result.success = False`
- Manual `./scripts/valor-service.sh worker-restart` a few seconds later succeeds — the worker was healthy the whole time

**Desired outcome:**
The orchestrator waits long enough for the worker to fully start before declaring failure. False alarms are eliminated; genuine startup failures are still surfaced correctly.

## Freshness Check

**Baseline commit:** `b432223c70e7dec62ed5cb81c9642efb8f1a5a7c`
**Issue filed at:** 2026-04-21T10:04:57Z
**Disposition:** Minor drift — file:line references corrected during recon, no change to premise

**File:line references re-verified:**
- `scripts/update/run.py:801–860` (issue claim) — actual poll loop spans lines **788–867**; outer loop at 800, fallback kickstart at 833–836, retry at 840–857, error log at 859–863. Claim still holds with corrected line numbers.
- `scripts/update/run.py:807` — heartbeat freshness check `heartbeat_file.stat().st_mtime > install_ts` present as described
- `scripts/update/service.py:196–225` — `install_worker()` does `bootout` + `bootstrap` (not `valor-service.sh worker-restart` as the issue body originally claimed). Corrected in the issue body during recon.
- `scripts/valor-service.sh:671–688` — `restart_worker()` uses `launchctl kickstart -k` followed by `sleep 2`; this is the shell-side path invoked by `worker-restart` but NOT by `install_worker`
- `worker/__main__.py:323` — `_write_worker_heartbeat()` called AFTER Redis verify, claude binary check, Popoto index rebuild, session cleanup, session recovery, orphan kill, and worker loop init. Heartbeat is NOT written early in startup as the issue originally speculated.
- `com.valor.worker.plist:32–33` — `ThrottleInterval: 10` confirmed; this is the launchd-enforced minimum delay between successive starts.

**Cited sibling issues/PRs re-checked:**
- #999 (closed 2026-04-16) — original bug filed when orchestrator silently left worker stopped; fixed by PR #1003 which added the very kickstart fallback logic this issue now complains about
- PR #1003 (merged 2026-04-16) — added 30s poll + kickstart fallback + 15s retry + error escalation (`result.success = False`). This is the exact code we are now tuning.

**Commits on main since issue was filed (touching referenced files):**
- `9e3a64f5 fix(utc): treat naive datetimes as UTC in all age/timestamp calculations` — touches age calculations in worker/bridge; not related to the orchestrator poll loop. Irrelevant.
- No other commits touch `scripts/update/run.py`, `scripts/update/service.py`, or `scripts/valor-service.sh` since issue filing.

**Active plans in `docs/plans/` overlapping this area:** None. No current plan touches the update orchestrator or worker launchd lifecycle.

**Notes:** The issue body incorrectly attributed the call chain as `install_worker → worker-restart → kickstart -k`. Reality: `install_worker()` uses `launchctl bootstrap` (which triggers auto-start via `RunAtLoad=true`); `kickstart -k` is only invoked in the fallback path at line 834 AFTER the 30s initial window expires with no running process. The issue body has been updated to reflect this during recon.

## Prior Art

- **Issue #999 (closed 2026-04-16)**: "update orchestrator silently leaves worker stopped when launchd fails to restart within 30s". Added the 30s poll + kickstart fallback that this plan is now tuning. Different failure mode: #999 was about silent failure (no error escalation); #1098 is about false-positive escalation when worker is actually healthy.
- **PR #1003 (merged 2026-04-16)**: "fix(#999): worker restart kickstart fallback + resume hydration field name". Introduced the current 30s + 15s window and the `result.success = False` escalation. No tests exist for this path — the PR noted "greenfield coverage" but didn't add any, deferring that as out of scope.
- **Issue #1098 comment (@romanobichi, 2026-04-21)**: Suggested "extend the orchestrator's health-check window (e.g., 15–20s) after kickstart -k so the worker has enough time to fully start". This plan extends that idea: we increase BOTH windows (60s initial + 30s retry), ADD a 12s throttle-aware sleep between kickstart and retry polling, AND downgrade the final escalation from error to warning. The commenter's direction is correct; the details account for launchd ThrottleInterval stacking that the comment didn't surface.

## Research

**External research:** Skipped — this is purely internal launchd/Python service-lifecycle code. No new libraries, APIs, or ecosystem patterns involved. Training data on macOS `launchctl` behavior is sufficient; `ThrottleInterval` semantics are documented in the plist and validated by empirical log data (Spike Results below).

## Spike Results

### spike-1: Measure actual worker startup timing from production logs
- **Assumption**: "Worker startup sequence (Redis verify, Popoto index rebuild, session recovery, worker loop init) takes ~5 seconds under normal conditions; a 15-second ceiling provides headroom."
- **Method**: code-read + log-read
- **Finding**: Popoto index rebuild on this machine consistently takes **0.6–0.7 seconds** (not 5s). From `logs/worker.log`, "Rebuilt indexes" → "Worker started" is ~1 second. Total startup from process launch to heartbeat write is typically under 3 seconds on a healthy machine. However, launchd `ThrottleInterval: 10` in `com.valor.worker.plist` means that after a recent exit, launchd will not restart the service for up to 10 seconds — this is the dominant source of startup delay.
- **Confidence**: high (empirical data from 30+ restart cycles in logs)
- **Impact on plan**: The bottleneck is NOT worker startup time — it is launchd's `ThrottleInterval` compounded with two back-to-back exits (bootout during install, then kickstart -k during retry). Each `-k` restart counts as an exit, triggering a fresh throttle window. The fix should account for throttle behavior, not just extend windows.

### spike-2: Confirm install_worker() call path does not invoke kickstart
- **Assumption**: "Issue body is correct that `install_worker()` invokes `valor-service.sh worker-restart` which then invokes `kickstart -k`."
- **Method**: code-read
- **Finding**: **Issue body was incorrect.** `scripts/update/service.py:196–225` `install_worker()` does `launchctl bootout` + `launchctl bootstrap` directly — no shell script, no `kickstart`. The plist's `RunAtLoad=true` causes launchd to auto-start after bootstrap. `restart_worker()` in `scripts/valor-service.sh:671` is a separate helper invoked only by the `worker-restart` CLI command, which is NOT part of the `/update` orchestrator path.
- **Confidence**: high
- **Impact on plan**: The fix lives entirely in `scripts/update/run.py`. No changes to `scripts/valor-service.sh` are strictly required. Optional coupling: if we make the timeout configurable via env var, both scripts can read the same var for consistency, but that is deferred to the Rabbit Holes section.

## Data Flow

1. **Entry point**: User runs `/update`, which invokes `scripts/update/run.py` (or `scripts/remote-update.sh` → `run.py`)
2. **install_worker() (service.py:196)**:
   - `launchctl bootout gui/$UID/com.valor.worker` (kills existing worker)
   - `launchctl bootstrap gui/$UID /path/to/com.valor.worker.plist` (reloads plist; launchd auto-starts via `RunAtLoad=true`)
   - Returns True immediately (no wait)
3. **Heartbeat poll loop (run.py:795–812)**:
   - Captures `install_ts = time.time()`
   - 15 iterations of `sleep(2)` + `is_worker_running()` + heartbeat mtime check
   - Exits loop when heartbeat is fresh OR after 30 seconds
4. **First-window outcome (run.py:813–825)**:
   - If worker PID exists but heartbeat stale → **warn only** (graceful); does NOT trigger the error path
   - If no worker PID → proceeds to kickstart fallback
5. **Kickstart fallback (run.py:827–838)**:
   - `launchctl kickstart -k gui/$UID/com.valor.worker` (force kill+restart; triggers fresh throttle window)
6. **Retry loop (run.py:840–857)**:
   - 8 iterations of `sleep(2)` + heartbeat freshness check
   - Exits when heartbeat is fresh OR after 16 seconds
7. **Output (run.py:858–867)**:
   - If still not healthy → log "ERROR: Worker not running after kickstart retry — system degraded", append warning, set `result.success = False`
   - Otherwise → silent success

The race: launchd's `ThrottleInterval: 10` delays each start by up to 10 seconds. Bootstrap+auto-start can take most of the 30s window if the previous worker exited recently. Kickstart -k at line 834 counts as another exit event, potentially triggering another 10s throttle. The 16s retry window is then too short.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1003 | Added 30s + 15s poll windows and kickstart fallback | Addressed the silent-failure case (#999) but did not account for launchd `ThrottleInterval: 10` stacking with the orchestrator's own bootout → bootstrap → kickstart sequence. Treated worker startup as deterministic when it is actually gated by launchd throttling. No tests were added to cover the path, so the false-positive edge case went undetected. |

**Root cause pattern:** The orchestrator treats the worker startup window as a fixed time budget, but launchd's throttle behavior makes startup non-deterministic. The fix must either (a) extend the window enough to absorb worst-case throttle stacking, or (b) detect throttle state and wait accordingly, or (c) suppress the escalation when the worker eventually does come up on the next update cycle.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None (internal orchestrator change)
- **Coupling**: Slightly reduced — if we unify the shell script and orchestrator on the same timeout constant, we reduce the "two sources of truth" problem
- **Data ownership**: No change
- **Reversibility**: Trivial — this is a constant adjustment and a minor flow tweak; revert is one commit

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a narrowly scoped bug fix in one function. The hard part is diagnosis (done in recon + spikes); the fix itself is a few lines of Python.

## Prerequisites

No prerequisites — this work has no external dependencies beyond what `/update` already uses.

## Solution

### Key Elements

- **Extended first-window poll**: Expand the 30s initial window to 60s to absorb launchd `ThrottleInterval` stacking on slow machines.
- **Throttle-aware kickstart retry**: Sleep 12 seconds after the `kickstart -k` call (past the 10s throttle) before starting the retry poll, and extend retry window from 16s to 30s.
- **Graceful escalation**: Downgrade the final "system degraded" escalation from `result.success = False` to a warning. The worker watchdog (`monitoring/worker_watchdog.py`) and launchd's `KeepAlive=true` will bring the worker up eventually; the next `/update` or health check will verify. Failing the whole update because a 60-second window expired is disproportionate.

### Flow

```
/update runs
  → install_worker() — launchctl bootout + bootstrap (returns immediately)
  → First-window poll (60s, 30 iterations × 2s)
      → worker starts writing heartbeat, loop breaks with success
      OR timeout
  → If worker PID exists but heartbeat stale → warn, done (existing behavior, unchanged)
  → If no worker PID → kickstart -k fallback
      → sleep 12s (past launchd throttle)
      → Retry poll (30s, 15 iterations × 2s)
          → heartbeat appears, success
          OR still missing
  → On final failure: warn (not error), do NOT set result.success = False
```

### Technical Approach

1. **In `scripts/update/run.py` around line 800**: change `range(15)` to `range(30)` (60s window). This is the simplest absorption of throttle stacking.
2. **After the kickstart at line 836**: add a `_time.sleep(12)` before entering the retry poll, to allow launchd's throttle window to expire before we start checking.
3. **Retry poll at line 840**: change `range(8)` to `range(15)` (30s window).
4. **At line 867**: remove `result.success = False`. Keep the ERROR log and warning append so operators still see the signal, but don't fail the entire update. The worker watchdog + launchd `KeepAlive` will recover automatically.
5. **Rename log message** from "ERROR: Worker not running after kickstart retry — system degraded" to "WARN: Worker not running within startup window — launchd will retry; check `worker-status`" to reflect that this is no longer a hard failure.

No shell script changes, no env var, no configuration file changes. Pure Python orchestrator tweak.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `try: subprocess.run(...) except Exception as e: log(f"launchctl kickstart failed: {e}", ...)` at line 837–838 — existing handler; behavior unchanged
- [ ] `try: ... except OSError: pass` at lines 811–812 and 856–857 — silent catches on heartbeat file stat; behavior unchanged, but we will add a one-line unit test asserting the OSError path doesn't raise

### Empty/Invalid Input Handling
- Heartbeat file may not exist → `heartbeat_file.exists()` guard present; behavior unchanged
- `install_ts` is always a valid float from `_time.time()`; no empty-input concern

### Error State Rendering
- The WARN log message after window expiry is the user-visible rendering — new message text must be clear that launchd will retry automatically
- `result.warnings.append(...)` already surfaces to the user via the update summary

## Test Impact

No existing tests affected — the orchestrator's service-restart logic is not covered by unit or integration tests. `tests/unit/test_worker_health_check.py` tests the in-process `_get_worker_health()` reader (separate code path). PR #1003 noted this as "greenfield coverage" and did not add tests.

**New test coverage proposed:**
- [ ] `tests/unit/test_update_worker_poll.py` (CREATE) — unit test for the poll logic using mocked `is_worker_running()`, `get_worker_pid()`, and a fake heartbeat file. Covers:
  - Happy path: heartbeat fresh within first window → success, no warnings
  - Heartbeat stale after first window, PID exists → warn only (no error, no kickstart)
  - No PID after first window → kickstart fired, sleep 12s, retry succeeds
  - No PID after kickstart + retry → WARN logged, `result.success` remains True, warning appended
  - OSError on `stat()` → caught silently, loop continues

## Rabbit Holes

- **Making the timeout configurable via env var**: Tempting for "flexibility" but adds a runtime dependency. The orchestrator runs once per update; hard-coded 60s + 30s is fine. Revisit only if operators report persistent failures on genuinely slow machines.
- **Unifying shell script and orchestrator on shared constants**: The shell script's `sleep 2` after `kickstart -k` is for a different code path (`worker-restart` CLI) and doesn't affect the update orchestrator. Keep them independent.
- **Moving the heartbeat write earlier in worker startup**: Would reduce the time-to-heartbeat but invalidates the semantics — the heartbeat currently means "worker is fully initialized and ready to dequeue work", which is what the dashboard and watchdog rely on. Changing this would require coordinated changes in 3+ other files. Out of scope.
- **Refactoring the poll loop**: Code is a bit repetitive (two similar loops with different ranges) but cleanup is deferred — the smallest change is to tweak the constants.

## Risks

### Risk 1: Genuinely failed worker startup now produces only a warning, not an error
**Impact:** An operator running `/update` might not notice that the worker genuinely failed to start (e.g., plist corruption, missing .venv). The update would report "success" when the system is actually degraded.
**Mitigation:**
- The `monitoring/worker_watchdog.py` launchd service (`com.valor.worker-watchdog`) checks heartbeat freshness every 120s and will escalate independently. Genuine failures are still surfaced — just not as an update-blocking error.
- The WARN log message still contains "Worker not running" which is greppable in logs.
- The `result.warnings` list is surfaced in the update summary shown to the operator.
- Next `/update` cycle will retry the install and either succeed or warn again, providing a natural escalation path.

### Risk 2: 60-second initial poll extends total update time noticeably
**Impact:** Users running `/update` will see up to an extra 30 seconds of waiting in the worst case (when the worker is slow to start but does eventually start).
**Mitigation:** The loop breaks early on success, so the 60s is only hit when the worker is actually slow — and in that case we NEED to wait. Fast-path (worker already running) is unchanged.

### Risk 3: Stacking `kickstart -k` with launchd throttle causes cascading delays
**Impact:** If `kickstart -k` fires and throttle is still active, the worker might not start until the throttle window expires. The added `sleep(12)` before retry polling assumes a 10s throttle plus 2s slack.
**Mitigation:** Empirical plist setting is `ThrottleInterval: 10`. The 12-second sleep gives 2 seconds of headroom. If this proves insufficient in practice, bump to 15s.

## Race Conditions

### Race 1: Heartbeat file mtime vs install_ts capture
**Location:** `scripts/update/run.py:798–807`
**Trigger:** `install_ts = _time.time()` captured after `install_worker()` returns but while launchd is still spinning up the new process. If the worker writes its heartbeat with nanosecond-scale mtime resolution issues, `st_mtime > install_ts` could theoretically fail.
**Data prerequisite:** Heartbeat mtime must be greater than install_ts for the check to succeed.
**State prerequisite:** Worker must have fully started (reached line 323 in `worker/__main__.py`) before heartbeat file is written.
**Mitigation:** The worker writes its heartbeat AFTER Redis verify, Popoto index rebuild, and session recovery — a sequence that takes 1–3 seconds minimum. Even on the fastest hardware, worker heartbeat write always happens well after install_ts was captured. No change needed; this race is benign.

### Race 2: Throttle window stacking on repeat kickstart
**Location:** `scripts/update/run.py:833–836`
**Trigger:** `kickstart -k` kills and restarts; launchd applies a fresh `ThrottleInterval` before the new process is allowed to launch. If the previous `bootout`+`bootstrap` already consumed throttle time and `kickstart -k` triggers a new throttle window, total wait could exceed the retry poll.
**Data prerequisite:** None.
**State prerequisite:** launchd must allow the new worker to start (throttle window must expire).
**Mitigation:** Add `_time.sleep(12)` after `kickstart -k` and before retry polling. 12 > 10 (ThrottleInterval) with 2s slack. Ensures the retry poll only starts after launchd could have launched the worker.

## No-Gos (Out of Scope)

- Moving `_write_worker_heartbeat()` earlier in `worker/__main__.py` startup sequence
- Adding configurable env vars for timeouts
- Refactoring the poll loop into a shared helper
- Adding launchd `ThrottleInterval` adjustment (keeping 10s — it exists for good reason)
- Changing the `worker_watchdog.py` threshold (360s) or any dashboard health check logic
- Fixing unrelated warnings in the `/update` output (those are separate issues)

## Update System

No update system changes required — this is a fix to the update orchestrator itself. The fix will propagate to other machines naturally via the next `/update` run that pulls this commit. No new dependencies, no config file changes, no migration steps.

## Agent Integration

No agent integration required — this is an orchestrator-internal change. The agent does not invoke `/update` directly; `/update` is run by the human operator or launchd (via `com.valor.update` cron). No MCP tools are exposed or affected. No `.mcp.json` changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` — specifically the "Update Orchestrator: Worker Start Verification" section added by PR #1003. Update the documented windows (60s + 30s instead of 30s + 15s), document the 12s throttle-aware delay after kickstart, and clarify that the final state is a warning not an error.
- [ ] No changes to `docs/features/README.md` index needed (entry already exists for bridge-worker-architecture).

### External Documentation Site
- No external docs site for this repo — internal docs only.

### Inline Documentation
- [ ] Update the inline comment at `scripts/update/run.py:792–794` to reflect the corrected understanding (heartbeat is written AFTER full startup, not before health loop; worker startup can take up to launchd ThrottleInterval + startup time).
- [ ] Update the comment at line 800 `# 30s window` to `# 60s window` after the constant change.
- [ ] Update the comment at line 839 `# Re-poll for 15 more seconds` to `# Sleep past launchd throttle, then re-poll for 30 more seconds`.

## Success Criteria

- [ ] `/update` no longer prints `ERROR: Worker not running after kickstart retry` when the worker starts successfully within the extended window
- [ ] If the worker genuinely fails to start within the extended windows, a WARN-level message is logged and the warning is appended to `result.warnings` (but `result.success` remains True unless other failures occurred)
- [ ] No regression to the bridge or other services managed by the same orchestrator step
- [ ] `worker_watchdog.py` heartbeat-freshness threshold (360s) remains unaffected
- [ ] New unit tests in `tests/unit/test_update_worker_poll.py` pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (orchestrator-poll)**
  - Name: orchestrator-poll-builder
  - Role: Adjust the poll loop constants, add throttle-aware sleep, downgrade escalation, update comments
  - Agent Type: builder
  - Resume: true

- **Test-engineer (orchestrator-poll)**
  - Name: orchestrator-poll-tester
  - Role: Create `tests/unit/test_update_worker_poll.py` covering all branches of the poll logic
  - Agent Type: test-engineer
  - Resume: true

- **Validator (orchestrator-poll)**
  - Name: orchestrator-poll-validator
  - Role: Verify the changes match the plan, run tests, check for regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: orchestrator-poll-docs
  - Role: Update `docs/features/bridge-worker-architecture.md` and inline comments
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Adjust orchestrator poll logic
- **Task ID**: build-orchestrator-poll
- **Depends On**: none
- **Validates**: `tests/unit/test_update_worker_poll.py` (will be created by test task)
- **Informed By**: spike-1 (confirmed Popoto rebuild is 0.6s not 5s; launchd throttle is dominant), spike-2 (confirmed install_worker uses bootstrap not kickstart)
- **Assigned To**: orchestrator-poll-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `scripts/update/run.py`:
  - Line 800: change `range(15)` to `range(30)` (60s first window)
  - Line 837 (after the try/except block around subprocess.run kickstart): add `_time.sleep(12)` before the retry loop
  - Line 840: change `range(8)` to `range(15)` (30s retry window)
  - Line 859–863: change log message from "ERROR: Worker not running after kickstart retry — system degraded" to "WARN: Worker not running within startup window — launchd will retry; check `./scripts/valor-service.sh worker-status`"
  - Line 867: remove `result.success = False`
  - Lines 792–794, 800, 839: update inline comments to reflect new behavior
- Run `python -m ruff format scripts/update/run.py` and `python -m ruff check scripts/update/run.py`

### 2. Write unit tests for orchestrator poll
- **Task ID**: build-orchestrator-poll-tests
- **Depends On**: build-orchestrator-poll
- **Validates**: the test file itself must execute (`pytest tests/unit/test_update_worker_poll.py -v`)
- **Informed By**: spike-1, spike-2
- **Assigned To**: orchestrator-poll-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_update_worker_poll.py` with mocked `service.is_worker_running`, `service.get_worker_pid`, `subprocess.run`, `time.sleep`, `time.time`, and a fake heartbeat file path via `tmp_path`
- Cover these scenarios:
  - `test_happy_path_heartbeat_fresh_in_first_window` — mock `is_worker_running` → True, heartbeat mtime > install_ts within first 3 iterations → success
  - `test_pid_exists_but_heartbeat_stale` — mock `is_worker_running` → True, heartbeat mtime < install_ts for all iterations → warn only, no kickstart
  - `test_no_pid_kickstart_succeeds` — mock `is_worker_running` → False for first window, True after kickstart → success
  - `test_no_pid_kickstart_fails` — mock `is_worker_running` → False throughout → WARN logged, `result.success` remains True, warning appended
  - `test_oserror_on_stat_is_silent` — mock heartbeat_file.stat() to raise OSError → loop continues, no exception propagates
  - `test_throttle_sleep_applied_after_kickstart` — assert that after the kickstart subprocess.run call, `time.sleep(12)` was called once before the retry poll starts
- Run `pytest tests/unit/test_update_worker_poll.py -v`

### 3. Validate implementation
- **Task ID**: validate-orchestrator-poll
- **Depends On**: build-orchestrator-poll, build-orchestrator-poll-tests
- **Assigned To**: orchestrator-poll-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `scripts/update/run.py` changes match the technical approach exactly (range values, sleep placement, log message, absence of `result.success = False`)
- Run `pytest tests/unit/test_update_worker_poll.py -v` — expect all tests pass
- Run `python -m ruff check scripts/update/run.py tests/unit/test_update_worker_poll.py` — expect exit code 0
- Run `python -m ruff format --check scripts/update/run.py tests/unit/test_update_worker_poll.py` — expect exit code 0
- Run `grep -n "system degraded\|result.success = False" scripts/update/run.py` — expect no hits in the worker-install block (lines 788–867)
- Report pass/fail status

### 4. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-orchestrator-poll
- **Assigned To**: orchestrator-poll-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` — in the "Update Orchestrator: Worker Start Verification" section (added by PR #1003):
  - Change "30-second heartbeat poll" to "60-second heartbeat poll (15 → 30 iterations × 2s)"
  - Change "15-second re-poll" to "30-second re-poll (8 → 15 iterations × 2s) with 12s pre-retry sleep to clear launchd ThrottleInterval"
  - Change "Error exit on persistent failure" to "Warning on persistent failure — the worker watchdog (`com.valor.worker-watchdog`) and launchd `KeepAlive=true` will retry independently; the `/update` no longer fails on this condition"
  - Add a sentence explaining `ThrottleInterval: 10` and why the pre-retry sleep is needed
- Verify inline comments in `scripts/update/run.py` lines 792–794, 800, 839 are updated

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-orchestrator-poll, build-orchestrator-poll-tests, validate-orchestrator-poll, document-feature
- **Assigned To**: orchestrator-poll-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` — expect exit code 0
- Run `python -m ruff check .` — expect exit code 0
- Run `python -m ruff format --check .` — expect exit code 0
- Verify `docs/features/bridge-worker-architecture.md` contains the updated "Update Orchestrator: Worker Start Verification" section
- Verify all Success Criteria checkboxes can be checked
- Report final status

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New unit tests pass | `pytest tests/unit/test_update_worker_poll.py -v` | exit code 0 |
| All unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update/run.py tests/unit/test_update_worker_poll.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update/run.py tests/unit/test_update_worker_poll.py` | exit code 0 |
| No error escalation in worker-install block | `grep -n "result.success = False" scripts/update/run.py` | output contains no hits between lines 788 and 867 |
| Throttle sleep present | `grep -n "_time.sleep(12)" scripts/update/run.py` | exit code 0 |
| Docs updated | `grep -n "60-second heartbeat poll\|ThrottleInterval" docs/features/bridge-worker-architecture.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Should the 60s initial window be even longer (e.g., 90s) to accommodate machines under heavy load? Current 60s is chosen to absorb ThrottleInterval + slow startup with ~30s headroom; 90s feels excessive but would further reduce false positives at the cost of slower updates on degraded machines.
2. Should the ERROR → WARN downgrade be guarded by an env var (e.g., `VALOR_UPDATE_STRICT=1` forces the old error behavior) for operators who want the old strict semantics? Default off; opt-in strictness.
3. Is it worth adding a one-shot launchd `list` check before the outer poll to see if the service is in a "waiting for throttle" state? This would let us skip ahead and only start polling after throttle clears, but adds code complexity. Probably over-engineered for a small fix.
