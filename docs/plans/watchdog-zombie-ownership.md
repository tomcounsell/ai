---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-30
tracking: https://github.com/tomcounsell/ai/issues/3592
last_comment_id:
---

# Bridge watchdog stops killing `claude` processes by age

## Problem

The bridge watchdog (`monitoring/bridge_watchdog.py`, launchd, every 60s) lists every process
whose `ps` line contains `"claude "` or `"pyright"` and SIGTERMs (then SIGKILLs) any older than
2 hours. Age is the only evidence. It does not check parent, TTY, CPU, output, or whether an
AgentSession owns the process.

On 2026-09-29 an interactive Claude Code session supervising the `/do-sdlc` pipeline for #3588
was killed three times, two hours apart, and each time its in-flight pipeline subagent died
with it. The kills continued on 2026-09-30 (00:21, 02:22 local). Each one is logged as
"1 zombie process(es) cleaned up" and recovery "level 0 (no action needed)", so a human's
working session dying reads as routine hygiene.

**Current behavior:** any `claude` or `pyright` process alive for 2h is killed, working or not.
Interactive sessions, SDLC supervisors, IDE language servers, and any worker turn past 2h are all
cut off. The kill log records PID, age, and RSS only.

**Desired outcome:** the watchdog never signals a `claude` or `pyright` process. Orphan cleanup
stays with the one component that can prove ownership: the worker's orphan reapers, which
already require PPID==1 (or an orphaned `sh -c` parent) plus no live owning AgentSession. Every
orphan kill log line carries the command, parent PID, and the evidence behind the verdict.

## Freshness Check

**Baseline commit:** `d0c93ce7c` (origin/main at plan time)
**Issue filed at:** 2026-09-29T15:22:46Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `monitoring/bridge_watchdog.py:133` — `ZOMBIE_THRESHOLD_SECONDS = 7200` — still holds
- `monitoring/bridge_watchdog.py:166` — `ZOMBIE_PROCESS_PATTERNS = ("claude ", "pyright")` — still holds
- `monitoring/bridge_watchdog.py:169` — `ZOMBIE_PROCESS_EXCLUDES = ("Claude.app", "Claude Helper")` — still holds
- `monitoring/bridge_watchdog.py:632` — `classify_zombies`, age >= threshold is the sole test — still holds
- `monitoring/bridge_watchdog.py:653` — `kill_zombie_processes`, SIGTERM, 3s poll, SIGKILL — still holds
- Not cited by the issue but part of the same path: `_kill_detected_zombies` (`:958`) runs at recovery levels 2, 3 and 4 (`:1018`, `:1025`, `:1037`); `--check-only` prints zombie and instance-count fields (`:1246-1255`).

**Cited sibling issues/PRs re-checked:** the issue cites no sibling issues. Related: #426 / PR #427 (added this sweep, closed/merged 2026-03-17) and #1271 / PR #1284 (worker orphan reaper, merged 2026-05-05). Neither has changed since.

**Commits on main since issue was filed (touching referenced files):**
- `a8bbfeab1` Reflection human comms through the persona (#3588) — touched `agent/session_health.py` only in the deferred self-draft flush path; irrelevant to the reapers.

**Active plans in `docs/plans/` overlapping this area:** none active. `resilience-simplification-three-tier.md` (status: draft, no tracking issue) mentions an ownership lease for the orphan net (T3.3); this plan does not touch the worker reaper's ownership model, only its log lines, so there is no conflict.

**Notes:** the defect is live. `logs/watchdog.log` shows kills at 2026-09-30 00:21 and 02:22 (each at ~7200s age) after the issue was filed; 186 zombie SIGTERMs in the current log.

## Prior Art

- **#426 / PR #427** "Add zombie process detection and cleanup to bridge watchdog" (merged 2026-03-17). Added the age-only sweep after 3 idle `claude` processes (6-9 days old, 1.75 GB) plus a 635 MB stale Pyright starved a 16 GB machine. At that time the bridge itself spawned Claude Code via the SDK client, and no ownership-aware reaper existed. Its own AC said "No false positives: actively working Claude Code sessions are not killed", which the age-only design could not guarantee.
- **#1271 / PR #1284** "cross-process orphan reaper with worker self-suicide guard" (merged 2026-05-05). Added `agent/session_health.py::_reap_orphan_session_processes`: PPID==1 + Claude/MCP signature + per-PID owning-session heartbeat gate + descendant walk + psutil `create_time` PID-reuse fence. Runs at worker startup and in the hourly `agent-session-cleanup` pass. This is the ownership-aware replacement the watchdog sweep never got retired for.
- **#1632** (mode 1b/1c) extended the worker reaper to orphaned `sh -c` wrapper parents and added `_fast_reap_stale_print_oneshots`, run every worker health-loop tick.
- **#2149** removed an age-only fast-kill branch from the worker reaper after it SIGTERM'd a live PM turn on 2026-07-17. Same lesson as this issue, learned once already on the worker side: age is a candidacy filter, never a verdict.

## Research

No relevant external findings — this is purely internal (deleting an in-repo sweep and enriching two log lines); no external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

Today:

1. **launchd** runs `python monitoring/bridge_watchdog.py` every 60s.
2. **`check_bridge_health()`** calls `_enumerate_claude_processes()` (`ps -eo pid,etime,rss,command`, substring match on `"claude "` / `"pyright"`), then `classify_zombies()` (age >= 7200s).
3. If the bridge is healthy, **`kill_zombie_processes()`** signals every "zombie" immediately and appends an issue string; `run_health_check()` then logs "Bridge unhealthy" and calls `execute_recovery(0)`, a no-op. If the bridge is unhealthy, the zombie count pushes `recovery_level` to 2, and levels 2-4 call `_kill_detected_zombies()` before restarting the bridge.
4. **Output:** a SIGTERM/SIGKILL to an arbitrary user-owned process; a log line with PID, age, RSS.

After this change:

1. The watchdog enumerates and signals no `claude`/`pyright` process. Its health verdict and recovery levels are driven by bridge process, log freshness, crash pattern, crash count, update-flow and scan-health checks only.
2. Orphaned worker-spawned processes are handled solely by the worker: `worker/__main__.py:903` (startup) and the hourly `agent-session-cleanup` pass call `_reap_orphan_session_processes()`; the health loop (`agent/session_health.py:5687`) calls `_fast_reap_stale_print_oneshots()` every tick. Both require PPID==1 (or an orphaned `sh -c` parent) and no live owning AgentSession before signalling.
3. **Output:** orphan kill log lines that name the command, parent PID, and the evidence (orphan route, owning-session lookup result, age).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `HealthStatus` loses `zombie_count`, `zombie_pids`, `zombie_memory_mb`, `active_claude_count`. `--check-only` output loses the zombie and active-instance lines. Removed functions: `_parse_elapsed_time`, `_enumerate_claude_processes`, `classify_zombies`, `kill_zombie_processes`, `_kill_detected_zombies`; removed constants `ZOMBIE_THRESHOLD_SECONDS`, `SOFT_INSTANCE_LIMIT`, `ZOMBIE_PROCESS_PATTERNS`, `ZOMBIE_PROCESS_EXCLUDES`. No code outside `monitoring/bridge_watchdog.py` and its tests references any of them (verified with `git grep`).
- **Coupling:** decreases. Process-ownership judgment lives in one place (the worker), consistent with "single authoritative liveness".
- **Data ownership:** unchanged; the worker already owns its subprocesses.
- **Reversibility:** trivial (a revert), though nothing should want it back.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Delete the watchdog zombie sweep.** The watchdog stops enumerating, classifying, counting, or signalling `claude`/`pyright` processes, both in the per-tick health check and in recovery levels 2-4. This answers the issue's open question: the cleanup is no longer needed in the watchdog, because the worker is the sole execution engine and already reaps its own orphans behind ownership gates.
- **Worker reapers stay the sole owner of orphan cleanup, unchanged in what they kill.** `_reap_orphan_session_processes` and `_fast_reap_stale_print_oneshots` keep their existing gates (PPID==1 or orphaned `sh -c` parent, Claude/MCP or stale-one-shot signature, no live owning AgentSession, `create_time` PID-reuse fence).
- **Evidence-bearing kill logs.** Every signal the worker reapers send is logged with the full command (capped by a named constant), the parent PID, and the evidence behind the verdict.

### Flow

Interactive `claude` session runs for 5h → watchdog ticks every 60s → watchdog checks bridge process, logs, crash pattern, update flow, scan health → never looks at the `claude` process → session keeps running.

Worker dies mid-turn → its `claude -p` reparents to launchd (PPID==1) → worker restarts (launchd KeepAlive) → startup orphan reap finds PPID==1 + no live owning session → SIGTERM, staged SIGKILL → log line names command, `ppid=1`, and "no live owning session".

### Technical Approach

- In `monitoring/bridge_watchdog.py`, delete: `ZOMBIE_THRESHOLD_SECONDS`, `SOFT_INSTANCE_LIMIT`, `ZOMBIE_PROCESS_PATTERNS`, `ZOMBIE_PROCESS_EXCLUDES`, `_parse_elapsed_time` (its only caller is `_enumerate_claude_processes`), `_enumerate_claude_processes`, `classify_zombies`, `kill_zombie_processes`, `_kill_detected_zombies`; the "Check 4: Zombie process detection" block in `check_bridge_health()`; the four `HealthStatus` zombie/instance fields and the `__post_init__` that only defaults `zombie_pids`; the `_kill_detected_zombies()` calls in `execute_recovery()` levels 2, 3, 4; the zombie/instance lines in `--check-only`. Update the module docstring's level list ("Kill stale processes + restart" stays; it refers to `kill_stale_processes`, the bridge-PID sweep, which is untouched) and the inline level comments ("Kill stale + zombie processes" becomes "Kill stale bridge processes").
- Consequence to accept, not paper over: with the sweep gone, a dead bridge is level 1 (restart) rather than being bumped to level 2 by an unrelated old `claude` process. That is the correct level; the bridge spawns no `claude` subprocess (only `bridge/routing.py:1735`, a customer-id resolver), so no `claude` process has any bearing on bridge health.
- In `agent/session_health.py::_reap_orphan_session_processes`, extend the `[orphan-reap] Killed PID` log line to include `ppid`, the orphan route (`ppid==1` or `orphaned sh -c wrapper ppid=<N>`), and the ownership evidence (`no owning session`, or `owning session <id> status=<s> heartbeat_age=<n>s` when a session was found but failed `_session_is_alive`). Capture the orphan route as a local when the gate at the PPID check passes, so the log reports what the gate actually decided rather than re-deriving it.
- In `_fast_reap_stale_print_oneshots`, extend both the SIGTERM and SIGKILL log lines with `ppid=1`, the process age in seconds, and `owner=not-live` (the `_oneshot_owner_is_live` result). That helper deliberately folds "no live owner" and "lookup timed out" into one `False` (fail toward reapable); the log reports that single verdict and the helper's contract stays as is.
- Command text in both reapers' log lines uses a new module constant `ORPHAN_KILL_LOG_CMD_CHARS = 500` instead of the current `[:100]` slice, so the command is recognisable (the 100-char cut drops the flags that distinguish an interactive session from a harness).
- `pyright`: no sweep matches it after this change. A `pyright` spawned under a worker harness dies with the harness (the session runner signals the whole process group, and the hourly reaper walks descendants before killing a parent). A `pyright` under a human's editor or terminal session is the human's process. This satisfies the "pyright reviewed under the same rule" criterion: the system kills only what it owns.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The watchdog code being deleted contains the only exception handlers in scope on the watchdog side; no handler is added. The worker reapers' existing per-PID `except` blocks are unchanged; the log-line edits sit inside the existing `try` bodies and add no new handler. Add no test for unchanged handlers.

### Empty/Invalid Input Handling
- [ ] The enriched log lines must not raise when the owning session is `None`, when `last_heartbeat_at` is `None`, or when `cmdline` is empty. Covered by the reaper log-line tests below (session `None` case and session-with-no-heartbeat case).

### Error State Rendering
- [ ] No user-visible output changes except `--check-only`, whose zombie lines are removed. `TestCheckOnlyOutput` asserts the remaining fields still print and that no "Zombie" / "Active claude instances" text appears.

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py::TestParseElapsedTime` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestEnumerateClaudeProcesses` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestClassifyZombies` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestKillZombieProcesses` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestHealthStatus::test_default_zombie_fields` and `::test_zombie_fields_populated` — DELETE: fields removed. `::test_alert_signal_fields_settable` — UPDATE only if it passes zombie kwargs.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCheckBridgeHealthZombieIntegration` — REPLACE: `test_populates_zombie_data` and `test_no_zombies_still_populates` become the new never-signals-claude regression test (see Step 2); `test_a_wedged_update_flow_makes_the_bridge_unhealthy` — UPDATE: drop the `kill_zombie_processes` / `_enumerate_claude_processes` patches, keep the assertion, and move it to a class whose name does not mention zombies.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCheckOnlyOutput` (3 tests) — UPDATE: drop zombie/instance kwargs; replace `test_check_only_includes_zombie_section`, `test_check_only_with_zombies`, `test_check_only_instance_limit_warning` with one test asserting the zombie and active-instance lines are absent.
- [ ] `tests/unit/test_bridge_watchdog.py::TestRecoveryExhaustedFallback::test_revert_failure_routes_to_recovery_exhausted` — UPDATE: drop the `_kill_detected_zombies` patch.
- [ ] `tests/unit/test_reconciler_scan_health.py` (lines ~327 and ~541) — UPDATE: drop the `_enumerate_claude_processes` patch from both `with` blocks (patching a deleted attribute raises `AttributeError`).
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE (extend): add log-evidence tests for both reapers. No existing test asserts on the `[orphan-reap] Killed PID` or `[fast-oneshot-reap] SIG...` text (verified: `git grep` over `tests/` returns nothing), so no existing assertion breaks.

## Rabbit Holes

- **Making the watchdog ownership-aware instead of deleting the sweep** (reading `find_live_session_by_pid`, walking process trees, checking TTYs). It would duplicate the worker's gates in a second process with a second liveness opinion, the exact pattern the issue calls out. Delete, don't patch.
- **Tightening the worker reaper's `_CLAUDE_CMDLINE_RE`.** Its second branch matches any non-`-p` `claude ... --permission-mode bypassPermissions`, which includes interactive sessions. It is gated on PPID==1 (or an orphaned wrapper) plus no live owning session. A terminal or tmux `claude` has a live shell parent and is never a candidate. Changing that regex is a separate design question with its own history (#1271, #1632); leave it.
- **Recovering the true identity of the 186 historical kills.** The log never recorded command lines; there is nothing to recover.
- **Adding a memory-pressure alarm to replace the "active instance" warning.** `SOFT_INSTANCE_LIMIT` counted human sessions as load and was never acted on. If memory pressure needs watching, that is its own issue.

## Risks

### Risk 1: A genuinely orphaned worker `claude -p` lingers longer
**Impact:** with the watchdog gone, an orphaned harness waits for the worker's next reap. The fast reaper runs every health-loop tick for stale one-shots; the full reaper runs at worker startup and hourly. If the worker is down (e.g. `worker-disable`), orphans persist until it returns.
**Mitigation:** the worker going down is itself the event that orphans harnesses, and launchd `KeepAlive` restarts it, whereupon the startup reap runs. A deliberately disabled worker is an operator choice. The old sweep would also have left orphans for up to 2h, so the worst case for an enabled worker (next tick) is faster than today.

### Risk 2: Test or tooling still patches a deleted attribute
**Impact:** `patch("monitoring.bridge_watchdog._enumerate_claude_processes")` raises `AttributeError` at test time.
**Mitigation:** Test Impact enumerates both known files; Verification includes a `git grep` sweep for every removed symbol across the repo, which must return nothing.

### Risk 3: A human process with PPID==1 matches the worker reaper
**Impact:** an interactive `claude --permission-mode bypassPermissions` whose shell parent died (reparented to launchd, no TTY) could be reaped by the worker.
**Mitigation:** this is unchanged by the plan and matches the issue's own standard of positive orphan evidence (parent gone, no owning session). The enriched log line makes any such kill attributable. Noted, not changed (see Rabbit Holes).

## Race Conditions

No race conditions introduced. The change removes a second, uncoordinated killer (the watchdog) that raced the worker reapers over the same PIDs; after it, only the worker signals these processes. The log-line edits read values already captured in the same loop iteration (`ppid`, `create_time`, `cmdline`, the resolved `session`) and add no new shared state.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this change deletes code from a launchd-run script that
`/update` already restarts; there are no new dependencies, config keys, or migrations.

## Agent Integration

No agent integration required — this is a watchdog/worker-internal change with no new tool or
CLI surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md`: delete the "Zombie process detection" bullet and the whole "Zombie Process Detection" subsection (currently ~lines 69-81); change the recovery-level table rows 2-4 from "kill zombies" to the actions that remain; delete the "Zombie cleanup is integrated into recovery levels 2+" sentence; rewrite the "vs. `kill_zombie_processes()`" comparison bullet (~line 239) to state that the worker reapers are the only component that signals `claude` processes, and why (ownership gates: PPID==1 or orphaned wrapper, no live owning AgentSession). Describe the new state only, no history.
- [ ] Update `docs/features/agent-session-health-monitor.md` (~line 63): same comparison bullet, same rewrite; mention that kill log lines carry command, parent PID, and evidence.
- [ ] No `docs/features/README.md` change: no feature is added or removed from the index.

### Inline Documentation
- [ ] Update the `monitoring/bridge_watchdog.py` module docstring and `execute_recovery` level comments so none mention zombies.
- [ ] Add a comment at the watchdog health check (where Check 4 was) only if the builder judges a reader would otherwise look for it; if added, it states the rule ("the watchdog never signals `claude` processes; the worker's orphan reapers own that") without narrating the removal.

## Success Criteria

- [ ] An interactive `claude` process older than 2h, not descended from the bridge or worker, is never signalled by the watchdog, from `check_bridge_health()` or from `execute_recovery()` at levels 2, 3, or 4. Covered by a regression test that is **proven red** against the pre-change `monitoring/bridge_watchdog.py` (the builder runs the new test against `origin/main`'s watchdog and pastes the failing output into the PR description).
- [ ] A genuinely orphaned worker-spawned `claude -p` (PPID==1, no live owning AgentSession) is still cleaned up, by the worker reapers, whose existing tests stay green.
- [ ] Every orphan kill log line (hourly reaper SIGTERM, fast reaper SIGTERM and SIGKILL) includes the command, the parent PID, and the evidence behind the verdict. Covered by log-capture tests.
- [ ] No sweep anywhere in the repo matches `pyright` by name (`git grep -n '"pyright"'` over `monitoring/ agent/ worker/` is empty).
- [ ] None of the removed watchdog symbols remains referenced anywhere in the repo outside `docs/plans/` and `docs/archive/`.
- [ ] `docs/features/bridge-self-healing.md` and `docs/features/agent-session-health-monitor.md` describe the new behavior only.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog + reaper logs)**
  - Name: watchdog-builder
  - Role: delete the watchdog sweep, enrich worker reaper log lines, update tests
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: watchdog-validator
  - Role: verify the red-then-green proof, the symbol sweep, and the success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: watchdog-docs
  - Role: update the two feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Write the regression test first and prove it red
- **Task ID**: build-red-test
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_watchdog.py
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestWatchdogNeverSignalsClaudeProcesses` to `tests/unit/test_bridge_watchdog.py`. Fixture: patch `subprocess.run` so any `ps` invocation returns a table containing an interactive session line (e.g. `  170     03:00:12  568000 claude --continue --permission-mode bypassPermissions --model opus`) and a `pyright-langserver --stdio` line aged 3h; patch `os.kill` and `time.sleep`; patch `is_bridge_running`, `are_logs_fresh`, `detect_crash_pattern`, `get_recent_crashes`, `assess_update_flow`, `_get_watchdog_redis` as the existing health-check tests do.
- Case A: healthy bridge, call `check_bridge_health()`; assert `os.kill` was never called with PID 170 or the pyright PID.
- Case B: call `execute_recovery(level, [...])` for levels 2 and 3 (patch `kill_stale_processes`, `restart_bridge`, `clear_lock_files`); assert the same. Level 4 is covered by patching `AUTO_REVERT_ENABLED_FILE` to exist and `revert_last_commit` to return False.
- Run the new test against the unchanged watchdog and confirm it FAILS (capture output for the PR description). Do not proceed until it is red.

### 2. Delete the watchdog sweep
- **Task ID**: build-watchdog
- **Depends On**: build-red-test
- **Validates**: tests/unit/test_bridge_watchdog.py, tests/unit/test_reconciler_scan_health.py
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove every symbol listed under Technical Approach from `monitoring/bridge_watchdog.py`, the Check 4 block, the `HealthStatus` fields and `__post_init__`, the level 2-4 `_kill_detected_zombies()` calls, and the `--check-only` lines. Fix the docstring and level comments.
- Apply every Test Impact entry for `test_bridge_watchdog.py` and `test_reconciler_scan_health.py`.
- The Step 1 test is now green.

### 3. Enrich worker reaper kill logs
- **Task ID**: build-reaper-logs
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_orphan_process_reap.py
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `ORPHAN_KILL_LOG_CMD_CHARS = 500` near the other `ORPHAN_*` constants in `agent/session_health.py`.
- In `_reap_orphan_session_processes`, record the orphan route when the PPID gate passes, and extend the `[orphan-reap] Killed PID` line with `ppid`, the route, and the ownership evidence (no owning session, or the found session's id, status, and heartbeat age).
- In `_fast_reap_stale_print_oneshots`, extend the SIGTERM and SIGKILL lines with `ppid=1`, age in seconds, and `owner=not-live`.
- Add tests in `tests/unit/test_session_health_orphan_process_reap.py` using `caplog`: one per log line, including the session-`None` case and a session whose `last_heartbeat_at` is `None`, asserting the command text, `ppid=`, and the evidence token appear.

### 4. Validate
- **Task ID**: validate-all-code
- **Depends On**: build-watchdog, build-reaper-logs
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the red proof exists (Step 1 output) and the test is now green.
- Run the Verification table.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all-code
- **Assigned To**: watchdog-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every item in the Documentation section.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the Verification table and confirm every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Watchdog tests pass | `scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py tests/unit/test_reconciler_scan_health.py -q` | exit code 0 |
| Reaper tests pass | `scripts/pytest-clean.sh tests/unit/test_session_health_orphan_process_reap.py tests/integration/test_orphan_reap_forward_scan.py -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/bridge_watchdog.py agent/session_health.py tests/unit/test_bridge_watchdog.py tests/unit/test_reconciler_scan_health.py tests/unit/test_session_health_orphan_process_reap.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/bridge_watchdog.py agent/session_health.py tests/unit/test_bridge_watchdog.py tests/unit/test_reconciler_scan_health.py tests/unit/test_session_health_orphan_process_reap.py` | exit code 0 |
| Removed symbols gone | `git grep -n -E "ZOMBIE_THRESHOLD_SECONDS\|SOFT_INSTANCE_LIMIT\|ZOMBIE_PROCESS_PATTERNS\|ZOMBIE_PROCESS_EXCLUDES\|_enumerate_claude_processes\|classify_zombies\|kill_zombie_processes\|_kill_detected_zombies\|zombie_memory_mb\|active_claude_count" -- ':!docs/plans' ':!docs/archive' \| wc -l` | match count == 0 |
| No pyright sweep | `git grep -n '"pyright"' -- monitoring agent worker \| wc -l` | match count == 0 |
| Docs describe new state | `grep -c -i "zombie process detection\|kill zombies" docs/features/bridge-self-healing.md` | match count == 0 |
| Regression test present | `grep -c "class TestWatchdogNeverSignalsClaudeProcesses" tests/unit/test_bridge_watchdog.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Orphaned pyright is unreaped after the sweep is deleted. A harness that dies abnormally (OOM SIGKILL, crash) leaves its pyright-langserver at PPID==1. No worker reaper signature (`_CLAUDE_CMDLINE_RE`, `_MCP_SERVER_CMDLINE_RE`, `_is_stale_print_oneshot`) matches it, so it leaks forever: the #426 scenario. "Dies with the harness" holds only for a runner-initiated killpg. | pending | In `_reap_orphan_session_processes`, add an `is_pyright` signature (`pyright(-langserver)?\b`) beside is_claude/is_mcp and route it through the existing `ppid != 1 and not _parent_is_orphaned_shell_wrapper(ppid)` gate unchanged. Never add a name-only match without the PPID gate. Alternatively, record it as an accepted residual in Risks and say how an operator would detect it. |
| CONCERN | History & Consistency | Test Impact misses 7 tests that `@patch("monitoring.bridge_watchdog._enumerate_claude_processes")`: TestCrashDetectionOnBridgeDeath (3 tests; decorators at lines 611/638/665) and TestCrashStormActionAlertSplit (4 tests; lines 1006/1034/1058/1085). Once the symbol is deleted they all raise AttributeError, so Risk 2's completeness claim is false. | pending | These are stacked @patch decorators, so removing the decorator also means removing its mock parameter. The bottom-most decorator maps to the first mock argument, and dropping only the decorator shifts later mocks (TypeError or wrong-mock asserts). Confirm with `git grep -n _enumerate_claude_processes tests/`, which must return nothing. |
| CONCERN | Scope & Value | The issue AC says "Every kill log line includes the command, parent PID, and the evidence", but the plan covers only three lines. The hourly drain line `[orphan-reap] Drain: SIGKILL'd PID %d (escalation)` still logs a bare PID, and terminated descendants get no per-PID line. | pending | At drain time the code already holds a create_time-matched `proc`. Read `proc.cmdline()` and `proc.ppid()` before `proc.kill()`, inside a guard so AccessDenied never skips the kill. Log cmd capped at ORPHAN_KILL_LOG_CMD_CHARS plus `escalation: survived SIGTERM, create_time matched`. Also list descendant PIDs on the Killed line, or scope the Success Criterion explicitly. |
| NIT | Risk & Robustness | Risk 1's "worst case ... (next tick)" holds only for stale `-p` one-shots. Non-`-p` bypassPermissions or SDK-bundled orphans wait for worker startup or the hourly pass. | pending | Qualify the sentence. |
| NIT | History & Consistency | `worker/__main__.py:903` calls the shim `_cleanup_orphaned_claude_processes()`, not `_reap_orphan_session_processes()` directly. | pending | Name the shim in Data Flow and the Freshness Check. |

---

## Open Questions

None. The issue's one open question (is the watchdog sweep still needed?) is answered by recon: the worker's ownership-gated reapers already own orphan cleanup, so the sweep is deleted rather than patched.
