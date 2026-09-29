---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-30
tracking: https://github.com/tomcounsell/ai/issues/3592
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-29T19:52:02Z
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
- `worker/__main__.py:903` — worker startup Step 4 calls the shim `_cleanup_orphaned_claude_processes()` (`agent/session_health.py:6858`), which delegates to `_reap_orphan_session_processes()` — still holds
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
2. Orphaned worker-spawned processes are handled solely by the worker: worker startup (`worker/__main__.py:903`, via the shim `_cleanup_orphaned_claude_processes()`) and the hourly `agent-session-cleanup` pass reach `_reap_orphan_session_processes()`; the health loop (`agent/session_health.py:5687`) calls `_fast_reap_stale_print_oneshots()` every tick. Both require PPID==1 (or an orphaned `sh -c` parent) and no live owning AgentSession before signalling.
3. An orphaned `pyright` whose harness died abnormally (so it reparented to launchd) is reaped by the same hourly/startup pass only when ownership is proven: its executable is `pyright` / `pyright-langserver`, it passes the unchanged PPID==1 (or orphaned `sh -c` parent) gate, its own environment carries the `AGENT_SESSION_ID` the worker injected into the harness env (`agent/session_executor.py:2379`, inherited by every child the harness spawns), and that session is not live per `_session_is_alive`. A `pyright` without the marker (every human- or editor-launched one) is never a candidate.
4. **Output:** orphan kill log lines that name the command, parent PID, and the evidence behind the verdict, including the staged-SIGKILL escalation line and each terminated descendant.

## Architectural Impact

- **New dependencies:** none.
- **Reaper signature set:** `_reap_orphan_session_processes` gains a `pyright` signature that is matched on the executable name only and carries an extra ownership gate the other signatures do not need: a worker session marker in the process environment, resolved to a non-live session.
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
- **Worker reapers stay the sole owner of orphan cleanup.** `_reap_orphan_session_processes` and `_fast_reap_stale_print_oneshots` keep their existing gates (PPID==1 or orphaned `sh -c` parent, no live owning AgentSession, `create_time` PID-reuse fence). The one widening: the hourly/startup reaper gains a `pyright` signature, so an orphaned worker-spawned language server (the #426 leak) is still cleaned up once the watchdog stops matching `pyright` by name. Ownership is proven, not inferred from the name: executable-name match, orphan gate, a worker-injected `AGENT_SESSION_ID` in the process's own environment, and that session not live. Any read failure along the way leaves the process alone (fail closed). A human's `pyright` has no marker and is untouchable.
- **Evidence-bearing kill logs.** Every signal the worker reapers send (parent SIGTERM, each descendant SIGTERM, staged-SIGKILL escalation, fast-reaper SIGTERM and SIGKILL) is logged with three required fields: the command (capped, long enough to show the distinguishing flags), the parent PID, and the evidence behind the verdict. The evidence string is built before the kill by a helper that cannot raise; formatting is the builder's call.

### Flow

Interactive `claude` session runs for 5h → watchdog ticks every 60s → watchdog checks bridge process, logs, crash pattern, update flow, scan health → never looks at the `claude` process → session keeps running.

Worker dies mid-turn → its `claude -p` reparents to launchd (PPID==1) → worker restarts (launchd KeepAlive) → startup orphan reap finds PPID==1 + no live owning session → SIGTERM, staged SIGKILL → log line names command, `ppid=1`, and "no live owning session".

### Technical Approach

- In `monitoring/bridge_watchdog.py`, delete: `ZOMBIE_THRESHOLD_SECONDS`, `SOFT_INSTANCE_LIMIT`, `ZOMBIE_PROCESS_PATTERNS`, `ZOMBIE_PROCESS_EXCLUDES`, `_parse_elapsed_time` (its only caller is `_enumerate_claude_processes`), `_enumerate_claude_processes`, `classify_zombies`, `kill_zombie_processes`, `_kill_detected_zombies`; the "Check 4: Zombie process detection" block in `check_bridge_health()`; the four `HealthStatus` zombie/instance fields and the `__post_init__` that only defaults `zombie_pids`; the `_kill_detected_zombies()` calls in `execute_recovery()` levels 2, 3, 4; the zombie/instance lines in `--check-only`. Update the module docstring's level list ("Kill stale processes + restart" stays; it refers to `kill_stale_processes`, the bridge-PID sweep, which is untouched) and the inline level comments ("Kill stale + zombie processes" becomes "Kill stale bridge processes").
- Consequence to accept, not paper over: with the sweep gone, a dead bridge is level 1 (restart) rather than being bumped to level 2 by an unrelated old `claude` process. That is the correct level; the bridge spawns no `claude` subprocess (only `bridge/routing.py:1735`, a customer-id resolver), so no `claude` process has any bearing on bridge health.
- **Evidence helper that cannot raise.** Add `_orphan_owner_evidence(session) -> str` next to `_session_is_alive` in `agent/session_health.py`. It returns `no owning session` for `None`, otherwise the session id, status, and heartbeat age, reusing the same heartbeat branches `_session_is_alive` handles (`datetime` with or without tzinfo, float epoch, `None`). The whole body sits in `try/except Exception` returning a fixed `owning session <unreadable>` string, so no input can make it raise. It is called **before** `proc.terminate()`, and the resulting string (plus the orphan route and the matched signature) is held in a local. The Killed log line after the kill only interpolates already-built strings, so neither the log line nor the `_increment_orphan_process_counter(session)` call after it can be skipped by a formatting error.
- In `_reap_orphan_session_processes`, the parent Killed line carries the required fields: command, `ppid`, and evidence. Evidence = the orphan route (PPID==1 or orphaned `sh -c` wrapper, captured as a local when the PPID gate passes, so the log reports what the gate decided), the matched signature (claude / mcp / pyright / oneshot), and the `_orphan_owner_evidence` string.
- In `_fast_reap_stale_print_oneshots`, both the SIGTERM and SIGKILL lines carry the command, `ppid`, and evidence (process age and the not-live `_oneshot_owner_is_live` verdict). That helper deliberately folds "no live owner" and "lookup timed out" into one `False` (fail toward reapable); the log reports that verdict and the helper's contract stays as is.
- Command text in both reapers' lines is capped by one named module constant, long enough to keep the flags that distinguish an interactive session from a harness (the current `[:100]` slice drops them). The builder picks the value.
- The reaper's drain-phase escalation line (`[orphan-reap] Drain: SIGKILL'd PID ... (escalation)`, currently a bare PID): the drain already holds a `create_time`-matched `proc`. Before `proc.kill()`, read `cmdline()` and `ppid()` in their own `try`/`except` that falls back to `<unreadable>`, so a read failure never skips the kill. Evidence: survived SIGTERM, `create_time` matched. The ownership verdict for this PID was logged on the earlier Killed or descendant line.
- Descendant kills: for each descendant, read cmdline and ppid in the same guarded way before `terminate()`, and after a successful `terminate()` log one line with command, `ppid`, and evidence naming the orphan parent PID whose verdict condemned it. The parent's Killed line keeps the descendant count.
- **`pyright` signature with provable ownership.** An orphaned worker-spawned `pyright-langserver` must still be reaped once the watchdog stops: a runner-initiated `killpg` takes the harness's group down, but an OOM SIGKILL or crash of the harness PID alone reparents its language server to launchd, and nothing else would reap it. A `pyright` never has a matching `exec_pid`, so `find_live_session_by_pid` can never vouch for or against it; ownership must come from the process itself. A process is a pyright candidate only if all of these hold, evaluated in this order so the cheap checks filter first:
  1. **Executable name, never a cmdline substring.** Add a helper `_is_pyright_executable(cmdline) -> bool`: true if the basename of `cmdline[0]` is `pyright` or `pyright-langserver` (optionally with a `.js` suffix), or if the basename of `cmdline[0]` is an interpreter (`node`, `python`, `python3`, `python3.N`) and the basename of `cmdline[1]` is one of those names. It never inspects `cmdline[2:]` or the joined `cmdline_str`. So `tail -f pyright.log`, `python worker.py --tool pyright`, and `claude -p "run pyright"` do not match.
  2. **Orphan gate, unchanged:** `ppid == 1` or `_parent_is_orphaned_shell_wrapper(ppid)`.
  3. **Worker session marker:** `proc.environ().get("AGENT_SESSION_ID")` is a non-empty string. The worker writes `AGENT_SESSION_ID` into every harness env (`agent/session_executor.py:2379`), and the harness's children inherit it. `psutil.AccessDenied`, `psutil.ZombieProcess`, `psutil.NoSuchProcess`, or any other exception from `environ()` means **not reapable** (fail closed, `continue`). A missing or empty marker means not reapable: every human-launched or editor-launched `pyright` lands here.
  4. **Marked session is not live:** resolve the marker with `AgentSession.get_by_id_strict(marker)` (the raising sibling of `get_by_id`, so a Redis error is distinguishable from "no such session"). A lookup exception means not reapable (fail closed). A found session that passes `_session_is_alive` means not reapable. A found non-live session, or no record at all (the session was deleted after finishing), means reapable.
  The pyright branch replaces the `find_live_session_by_pid` lookup for this signature (it can never match), and `session` for the evidence helper is the marker's session. Its evidence adds the marker id. The fast reaper stays `-p`-only; an orphaned `pyright` waits for startup or the hourly pass, ample for a leak measured in days (#426). `DISABLE_ORPHAN_PROCESS_REAP=1` disables this signature along with the rest of the reaper.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The watchdog code being deleted contains the only exception handlers in scope on the watchdog side; no handler is added there. The worker reapers' existing per-PID `except` blocks are unchanged.
- [ ] New handlers on the worker side, each tested: `_orphan_owner_evidence` swallows everything and returns the fixed unreadable string (test: a session whose attribute access raises); the pyright `environ()` read fails closed on `AccessDenied`, `ZombieProcess`, and a generic `Exception` (test: each leaves the process untouched); the pyright `get_by_id_strict` lookup fails closed on an exception (test: untouched); the guarded cmdline/ppid reads in the drain and descendant lines fall back to `<unreadable>` and never skip the kill (test: `cmdline()` raising `AccessDenied`, `kill()` / `terminate()` still called).

### Empty/Invalid Input Handling
- [ ] `_orphan_owner_evidence` handles session `None`, `last_heartbeat_at` `None`, a tz-naive `datetime`, and a float epoch heartbeat without raising; the Killed line and the counter increment both happen in each case. Empty `cmdline` never reaches the log (the reaper already `continue`s on it). An empty-string `AGENT_SESSION_ID` is treated as no marker.

### Error State Rendering
- [ ] No user-visible output changes except `--check-only`, whose zombie lines are removed. `TestCheckOnlyOutput` asserts the remaining fields still print and that no "Zombie" / "Active claude instances" text appears.

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py::TestParseElapsedTime` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestEnumerateClaudeProcesses` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestClassifyZombies` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestKillZombieProcesses` — DELETE: function removed.
- [ ] `tests/unit/test_bridge_watchdog.py::TestHealthStatus::test_default_zombie_fields` and `::test_zombie_fields_populated` — DELETE: fields removed. `::test_alert_signal_fields_settable` — UPDATE only if it passes zombie kwargs.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCheckBridgeHealthZombieIntegration` — REPLACE: `test_populates_zombie_data` and `test_no_zombies_still_populates` become the new never-signals-claude regression test (see Step 2); `test_a_wedged_update_flow_makes_the_bridge_unhealthy` (`tests/unit/test_bridge_watchdog.py:484-500`) — UPDATE, keep the assertion, and move it to a class whose name does not mention zombies. Stacked `@patch` decorators bind bottom-up to positional parameters, and the two removed mocks sit mid-list, so remove decorator and parameter together:
  - delete the decorators `@patch("monitoring.bridge_watchdog.kill_zombie_processes")` and `@patch("monitoring.bridge_watchdog._enumerate_claude_processes")` (2nd and 3rd from the top);
  - delete the parameters `mock_enumerate` and `mock_kill` (5th and 6th of 7);
  - the resulting signature is `(self, mock_running, mock_logs, mock_crash, mock_crashes, mock_update_flow)`, with `assess_update_flow` still the top decorator bound to the last parameter;
  - delete any body line that references `mock_enumerate` or `mock_kill`.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCrashDetectionOnBridgeDeath` (3 tests: `test_calls_log_crash_when_bridge_not_running`, `test_does_not_call_log_crash_when_bridge_running`, `test_log_crash_failure_does_not_break_health_check`) — UPDATE: remove the `@patch("monitoring.bridge_watchdog._enumerate_claude_processes")` decorator, its `mock_enumerate` parameter, and the `mock_enumerate.return_value = []` line. The decorator is the top-most in each stack, so its mock is the LAST positional parameter; removing both together leaves every other mock's position unchanged.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCrashStormActionAlertSplit` (4 tests: `test_wedge_dominated_crash_storm_livelock_regression`, `test_non_wedge_storm_opens_circuit`, `test_mixed_50_50_storm_opens_circuit`, `test_below_threshold_no_alert_no_circuit`) — UPDATE: same edit as above (top-most decorator, last parameter `mock_enumerate`, plus its `return_value` line).
- [ ] `tests/unit/test_bridge_watchdog.py` imports (lines ~12, ~16) — UPDATE: drop the imports of every removed symbol.
- [ ] `tests/unit/test_bridge_watchdog.py::TestCheckOnlyOutput` (3 tests) — UPDATE: drop zombie/instance kwargs; replace `test_check_only_includes_zombie_section`, `test_check_only_with_zombies`, `test_check_only_instance_limit_warning` with one test asserting the zombie and active-instance lines are absent.
- [ ] `tests/unit/test_bridge_watchdog.py::TestRecoveryExhaustedFallback::test_revert_failure_routes_to_recovery_exhausted` (`:1157-1170`) — UPDATE, removing decorator and parameter together:
  - delete the decorator `@patch("monitoring.bridge_watchdog._kill_detected_zombies")` (5th from the top, 2nd from the bottom);
  - delete the parameter `mock_kill_zombies` (2nd, directly after `mock_clear_locks`);
  - the resulting signature is `(self, mock_clear_locks, mock_kill_stale, mock_restart, mock_revert, mock_log_crash, tmp_path)`;
  - delete any body line that references `mock_kill_zombies`.
- [ ] After each of the two edits above, run that single test by node id (`scripts/pytest-clean.sh "tests/unit/test_bridge_watchdog.py::<Class>::<test>" -q`) before moving on; a misbound mock shows up there as a `TypeError` or a wrong assertion target.
- [ ] `tests/unit/test_reconciler_scan_health.py` (lines ~327 and ~541) — UPDATE: drop the `_enumerate_claude_processes` patch from both `with` blocks (patching a deleted attribute raises `AttributeError`).
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE (extend): add log-evidence tests for both reapers (parent Killed line, descendant line, drain escalation line, fast-reaper SIGTERM and SIGKILL lines, each asserting command, `ppid`, and an evidence token) and `pyright` ownership tests, all with a fake psutil process at PPID==1:
  - orphaned `tail -f pyright.log` (with a dead-session marker) is not matched: the executable is `tail`;
  - orphaned `python worker.py --tool pyright` and `claude -p "run pyright"` are not matched as pyright;
  - orphaned `node /x/node_modules/.bin/pyright-langserver --stdio` whose `environ()` has no `AGENT_SESSION_ID` (or an empty one) is not terminated;
  - the same process whose marker names a session that `_session_is_alive` rejects (terminal status) is terminated, and so is one whose marker resolves to no record;
  - the same process whose marker names a live session is not terminated;
  - `environ()` raising `AccessDenied`, `ZombieProcess`, or a generic `Exception` means not terminated;
  - `get_by_id_strict` raising means not terminated;
  - the marker-carrying, dead-session process with a live non-1 parent is not a candidate.
  Plus `_orphan_owner_evidence` tests: `None`, heartbeat `None`, tz-naive `datetime`, float epoch, and an attribute that raises; each returns a string, and in the reaper the Killed line and counter increment still happen. No existing test asserts on the `[orphan-reap] Killed PID`, `Drain: SIGKILL'd`, or `[fast-oneshot-reap] SIG...` text (verified: `git grep` over `tests/` returns nothing), so no existing assertion breaks.
- [ ] Completeness gate: after the edits, `git grep -n -E "_enumerate_claude_processes|kill_zombie_processes|_kill_detected_zombies|classify_zombies" tests/` must return nothing. This is the check that Risk 2's mitigation depends on, not the enumerated list above.

## Rabbit Holes

- **Making the watchdog ownership-aware instead of deleting the sweep** (reading `find_live_session_by_pid`, walking process trees, checking TTYs). It would duplicate the worker's gates in a second process with a second liveness opinion, the exact pattern the issue calls out. Delete, don't patch.
- **Tightening the worker reaper's `_CLAUDE_CMDLINE_RE`.** Its second branch matches any non-`-p` `claude ... --permission-mode bypassPermissions`, which includes interactive sessions. It is gated on PPID==1 (or an orphaned wrapper) plus no live owning session. A terminal or tmux `claude` has a live shell parent and is never a candidate. Changing that regex is a separate design question with its own history (#1271, #1632); leave it.
- **Recovering the true identity of the 186 historical kills.** The log never recorded command lines; there is nothing to recover.
- **Adding a memory-pressure alarm to replace the "active instance" warning.** `SOFT_INSTANCE_LIMIT` counted human sessions as load and was never acted on. If memory pressure needs watching, that is its own issue.

## Risks

### Risk 1: A genuinely orphaned worker `claude -p` lingers longer
**Impact:** with the watchdog gone, an orphaned harness waits for the worker's next reap. The fast reaper runs every health-loop tick for stale one-shots; the full reaper runs at worker startup and hourly. If the worker is down (e.g. `worker-disable`), orphans persist until it returns.
**Mitigation:** the worker going down is itself the event that orphans harnesses, and launchd `KeepAlive` restarts it, whereupon the startup reap runs. A deliberately disabled worker is an operator choice. Latency by class, for an enabled worker: a stale `-p` one-shot is reaped on the next health-loop tick (faster than the old 2h sweep); a non-`-p` `bypassPermissions` harness, an SDK-bundled `claude`, an MCP server, or a `pyright` waits for worker startup or the next hourly pass (at most ~1h, still faster than the old 2h sweep).

### Risk 2: Test or tooling still patches a deleted attribute
**Impact:** `patch("monitoring.bridge_watchdog._enumerate_claude_processes")` raises `AttributeError` at test time (ten decorator sites in `test_bridge_watchdog.py` across three classes, plus two `with` blocks in `test_reconciler_scan_health.py`).
**Mitigation:** Test Impact lists every class, but the enumeration is not the guarantee: the completeness gate (`git grep` over `tests/` for the removed symbols returns nothing) and the repo-wide Verification sweep are. A sweep, not a checklist.

### Risk 3: A human process with PPID==1 matches the worker reaper
**Impact:** an interactive `claude --permission-mode bypassPermissions` whose shell parent died (reparented to launchd, no TTY) could be reaped by the worker.
**Mitigation:** this is unchanged by the plan and matches the issue's own standard of positive orphan evidence (parent gone, no owning session). The enriched log line makes any such kill attributable. Noted, not changed (see Rabbit Holes).

### Risk 4: The new `pyright` signature reaps a pyright a human still wants
**Impact:** the worker could signal a `pyright` a human launched.
**Mitigation:** it cannot become a candidate. Candidacy needs all four of: executable name `pyright` / `pyright-langserver` (never a cmdline substring), the orphan gate, a non-empty `AGENT_SESSION_ID` in the process's own environment, and that session not live. Human shells and editors do not set `AGENT_SESSION_ID`, so a human's `pyright`, orphaned or not, fails the marker gate. Every read failure (`environ()`, the session lookup) fails closed. The only `pyright` that can be reaped is one a worker harness spawned whose session is already dead or gone. Any kill is attributable: the Killed line records the command, `ppid`, and the marker session evidence in `logs/worker.log`. Break-glass: `DISABLE_ORPHAN_PROCESS_REAP=1` (`agent/session_health.py:6539`) turns off the whole reaper, the pyright signature included.
**Residual:** an orphaned worker `pyright` whose session is still live is left alone until the session ends. That errs toward keeping a process, which is the direction this issue asks for.

## Race Conditions

No race conditions introduced. The change removes a second, uncoordinated killer (the watchdog) that raced the worker reapers over the same PIDs; after it, only the worker signals these processes. The log-line edits read values already captured in the same loop iteration (`ppid`, `create_time`, `cmdline`, the resolved `session`) and add no new shared state. The pyright `environ()` read and session lookup act on the same psutil `Process` the iterator yielded (already `create_time`-fenced against PID reuse); if the process exits between the read and the kill, `terminate()` raises `NoSuchProcess` and the existing handler skips it.

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
- [ ] Update `docs/features/agent-session-health-monitor.md` (~line 63): same comparison bullet, same rewrite; mention that kill log lines carry command, parent PID, and evidence, and that an orphaned `pyright` is reaped only when its executable name matches, it is orphaned, its environment carries a worker `AGENT_SESSION_ID`, and that session is not live.
- [ ] No `docs/features/README.md` change: no feature is added or removed from the index.

### Inline Documentation
- [ ] Update the `monitoring/bridge_watchdog.py` module docstring and `execute_recovery` level comments so none mention zombies.
- [ ] Add a comment at the watchdog health check (where Check 4 was) only if the builder judges a reader would otherwise look for it; if added, it states the rule ("the watchdog never signals `claude` processes; the worker's orphan reapers own that") without narrating the removal.

## Success Criteria

- [ ] An interactive `claude` process older than 2h, not descended from the bridge or worker, is never signalled by the watchdog, from `check_bridge_health()` or from `execute_recovery()` at levels 2, 3, or 4. Covered by a regression test that is **proven red** against the pre-change `monitoring/bridge_watchdog.py` (the builder runs the new test against `origin/main`'s watchdog and pastes the failing output into the PR description).
- [ ] A genuinely orphaned worker-spawned `claude -p` (PPID==1, no live owning AgentSession) is still cleaned up, by the worker reapers, whose existing tests stay green.
- [ ] Every orphan-reaper kill log line includes the command, the parent PID, and the evidence behind the verdict: the hourly reaper's parent Killed line, its per-descendant Killed line, its `Drain: SIGKILL'd` escalation line, and the fast reaper's SIGTERM and SIGKILL lines. Covered by log-capture tests. Scope note: `[reap-killlist] SIGKILL'd boot-persisted survivor` (`agent/reap_killlist.py`) is a recorded-PID kill of a wedge survivor whose evidence (`pgid`, `session`) was captured at wedge time and is already on the line; it is not a name-matched orphan kill and is unchanged.
- [ ] An orphaned `pyright-langserver` (PPID==1) whose environment carries an `AGENT_SESSION_ID` naming a non-live or absent session is reaped by the hourly/startup worker reaper. A `pyright` without the marker, with a live marked session, with a live parent, whose `environ()` or session lookup raises, or matched only by a cmdline substring (`tail -f pyright.log`) is never signalled. Covered by unit tests.
- [ ] A formatting failure in the evidence cannot lose a Killed line or counter increment: the evidence is built before the kill by a helper that cannot raise. Covered by unit tests.
- [ ] Nothing in the watchdog matches `pyright` or `claude` process names (`git grep -n -i pyright -- monitoring/` is empty).
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
- Add a named command-length cap constant near the other `ORPHAN_*` constants in `agent/session_health.py`.
- Add `_orphan_owner_evidence(session) -> str` (cannot raise) next to `_session_is_alive`.
- In `_reap_orphan_session_processes`, record the orphan route when the PPID gate passes, build the evidence string before `proc.terminate()`, and log the parent Killed line with command, `ppid`, and evidence (route, matched signature, owner evidence).
- Add a per-descendant Killed line (command, `ppid`, evidence naming the orphan parent), reading cmdline/ppid in a guard before `terminate()`.
- Enrich the reaper's drain-phase escalation line (`[orphan-reap] Drain: SIGKILL'd PID`) with command, `ppid`, and the escalation evidence, reading cmdline/ppid in a guard that can never skip `proc.kill()`.
- Add `_is_pyright_executable(cmdline)` and the pyright branch with its four ordered gates (executable name, orphan gate, `environ()` marker, `get_by_id_strict` + `_session_is_alive`), each failing closed, as specified in Technical Approach.
- In `_fast_reap_stale_print_oneshots`, enrich the SIGTERM and SIGKILL lines with command, `ppid`, and evidence (age, not-live owner verdict).
- Add the tests listed under Test Impact for `tests/unit/test_session_health_orphan_process_reap.py`, using `caplog` for the log lines.

### 4. Validate
- **Task ID**: validate-all-code
- **Depends On**: build-watchdog, build-reaper-logs
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the red proof exists (Step 1 output) and the test is now green.
- Run the **Code checks** block under Verification only. The repo-wide symbol sweep and the docs check are not run here: the feature docs still name the removed functions until Step 5 rewrites them, so those checks would fail by construction.

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
- Re-run the **Code checks** block, then run the **Post-docs checks** block, and confirm every Success Criterion.

## Verification

Both tables are machine-parsed (`agent/verification_parser.py`), which splits cells on unescaped `|`. Every command below is therefore written with no pipe character at all: alternation uses repeated `-e` patterns (real alternation, one pattern per `-e`), and "nothing matches" is asserted through `git grep -q`'s exit status (1 = no match; an error exits 128, so it cannot pass vacuously) or `grep -c`'s literal `0`. No cell needs Markdown escaping.

### Code checks (Step 4 and Step 6)

| Check | Command | Expected |
|-------|---------|----------|
| Watchdog tests pass | `scripts/pytest-clean.sh tests/unit/test_bridge_watchdog.py tests/unit/test_reconciler_scan_health.py -q` | exit code 0 |
| Reaper tests pass | `scripts/pytest-clean.sh tests/unit/test_session_health_orphan_process_reap.py tests/integration/test_orphan_reap_forward_scan.py -q` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/bridge_watchdog.py agent/session_health.py tests/unit/test_bridge_watchdog.py tests/unit/test_reconciler_scan_health.py tests/unit/test_session_health_orphan_process_reap.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/bridge_watchdog.py agent/session_health.py tests/unit/test_bridge_watchdog.py tests/unit/test_reconciler_scan_health.py tests/unit/test_session_health_orphan_process_reap.py` | exit code 0 |
| Watchdog matches no process names | `git grep -q -i -e pyright -e '"claude "' -- monitoring` | exit code 1 |
| No code or test references a removed symbol | `git grep -q -e ZOMBIE_THRESHOLD_SECONDS -e SOFT_INSTANCE_LIMIT -e ZOMBIE_PROCESS_PATTERNS -e ZOMBIE_PROCESS_EXCLUDES -e _enumerate_claude_processes -e classify_zombies -e kill_zombie_processes -e _kill_detected_zombies -e zombie_memory_mb -e active_claude_count -- '*.py'` | exit code 1 |
| Pyright gated on the session marker | `grep -c AGENT_SESSION_ID agent/session_health.py` | output > 0 |
| Pyright matched by executable only | `grep -c _is_pyright_executable agent/session_health.py` | output > 0 |
| Regression test present | `grep -c "class TestWatchdogNeverSignalsClaudeProcesses" tests/unit/test_bridge_watchdog.py` | output > 0 |

### Post-docs checks (Step 6 only, after Step 5 rewrites the feature docs)

| Check | Command | Expected |
|-------|---------|----------|
| Removed symbols gone repo-wide | `git grep -q -e ZOMBIE_THRESHOLD_SECONDS -e SOFT_INSTANCE_LIMIT -e ZOMBIE_PROCESS_PATTERNS -e ZOMBIE_PROCESS_EXCLUDES -e _enumerate_claude_processes -e classify_zombies -e kill_zombie_processes -e _kill_detected_zombies -e zombie_memory_mb -e active_claude_count -- . ':!docs/plans' ':!docs/archive'` | exit code 1 |
| Self-healing doc describes new state | `grep -c -i -e "zombie process detection" -e "kill zombies" docs/features/bridge-self-healing.md` | match count == 0 |
| Health-monitor doc names no removed function | `grep -c -e kill_zombie_processes -e _enumerate_claude_processes docs/features/agent-session-health-monitor.md` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Scope & Value dissents, see NIT) | `_PYRIGHT_CMDLINE_RE = \bpyright(?:-langserver)?\b` would run against the whole joined `cmdline_str` (`agent/session_health.py:6649`), so any PPID==1 process with "pyright" as a word anywhere in its argv becomes a candidate (e.g. `tail -f pyright.log`, a script with `--tool pyright`). No AgentSession ever owns a pyright, so `find_live_session_by_pid` returns None and the ownership gate adds nothing. The verdict reduces to PPID==1 plus a substring, and the descendant walk also SIGTERMs the subtree. That is the name-only kill Risk 4 says never happens. | Technical Approach (pyright signature with provable ownership); Test Impact; Risk 4 | Compute `is_pyright` from the executable identity only, e.g. `" ".join(str(x) for x in cmdline[:2])` (covers `node .../pyright-langserver` and `python -m pyright`), with a basename-anchored pattern such as `(?:^\|[/\s])pyright(?:-langserver)?(?:\.js)?(?:\s\|$)`. Do not reuse `cmdline_str`. Add negative tests: orphaned PPID==1 `python worker_thing.py --tool pyright` and orphaned `claude -p "run pyright"` must not match as pyright. |
| CONCERN | Risk & Robustness | The enriched parent `[orphan-reap] Killed PID` line is built after `terminate()`, inside the per-PID `try` whose `except Exception` logs only at DEBUG and continues (`agent/session_health.py:6738-6755`). `_increment_orphan_process_counter(session)` (`:6748`) comes after the log call. If the new `heartbeat_age` / status formatting raises (naive datetime, float heartbeat), the process is already dead but the Killed line and the counter are silently lost, exactly in the found-but-not-alive case the evidence is meant for. | Technical Approach (evidence helper that cannot raise); Failure Path Test Strategy | Add a never-raising helper `_orphan_owner_evidence(session) -> str` next to `_session_is_alive`, wrapped in `try/except Exception: return "owning session <unreadable>"`, reusing the datetime / tz-naive / float / None branches of `_session_is_alive` (`:6435-6446`). Compute it before `proc.terminate()`. Extend the tests to cover a naive-datetime heartbeat and a float heartbeat, not only None. |
| CONCERN | History & Consistency | Test Impact spells out "remove the decorator AND its mock parameter" only for the two crash classes, where the mock is the last parameter. Two other tests have the removed mocks mid-list: `test_a_wedged_update_flow_makes_the_bridge_unhealthy` (`mock_enumerate`, `mock_kill` are the 5th and 6th of 7 params, `tests/unit/test_bridge_watchdog.py:484-500`) and `test_revert_failure_routes_to_recovery_exhausted` (`mock_kill_zombies` is the 2nd param, `:1157-1170`). Removing only the decorators binds the wrong mocks or raises TypeError. | Test Impact (explicit decorator and parameter removal, resulting signatures) | Stacked `@patch` decorators bind bottom-up to positional params. For each removed decorator, delete the parameter at the matching position: in the wedged-update test drop both `mock_enumerate` and `mock_kill` (leaving `mock_update_flow` last); in the revert test drop `mock_kill_zombies` (leaving `mock_clear_locks, mock_kill_stale, ...`). Run each test individually after the edit. |
| CONCERN | History & Consistency | Step 4 (validate-all-code) runs the full Verification table before Step 5 rewrites the docs. The feature docs still name `kill_zombie_processes` / `_enumerate_claude_processes` (`docs/features/bridge-self-healing.md:76,78,239`, `docs/features/agent-session-health-monitor.md:63`), so the "Removed symbols gone" and "Docs describe new state" rows necessarily fail at Step 4. | Step 4/6 scoping; Verification split into Code checks and Post-docs checks, pipe-free commands | Scope Step 4 to the code rows only (tests, lint, format, "Watchdog matches no process names", "No test patches a removed symbol", "Pyright reaped only behind the gate", "Regression test present"); leave the repo-wide symbol sweep and docs rows to Step 6. Also: the `\|` in the Verification cells is Markdown table escaping; the validator must unescape each to a bare pipe before running (both the `-E` alternation and the `wc -l` pipe), otherwise the grep patterns are literal and the zero-count rows pass vacuously. State this once above the table. |
| NIT | Scope & Value | The pyright signature adds a new kill class to the worker reaper in the same change that removes a mis-scoped one; the issue only asks that pyright matching be "reviewed under the same rule". This dissents from the prior round's CONCERN that added it; the anchoring CONCERN above addresses the risk if it stays. | Kept with provable ownership (executable name + orphan gate + AGENT_SESSION_ID marker + non-live session); no name-only kill remains | Keep it with executable-anchored matching, or drop it and record orphaned pyright as an accepted residual in Risks. |
| NIT | Scope & Value | Log enrichment is over-specified for a Small appetite (500-char constant, `sig=` token names, exact line formats). | Solution Key Elements; Technical Approach; Step 3 now name required fields only | Name the required fields (command, ppid, evidence) and let the builder own formatting. |
| NIT | History & Consistency | Step 3 says "Extend the Step 1 drain line"; Step 1 of this plan is the red regression test. It means the reaper's Step-1 drain loop. | Step 3 wording | Reword to "the reaper's drain-phase line". |
| NIT | Risk & Robustness | Risk 4 does not name the existing break-glass `DISABLE_ORPHAN_PROCESS_REAP=1` (`agent/session_health.py:6539`), which also disables the new pyright signature. | Risk 4 | Mention it in Risk 4's mitigation. |

---

## Open Questions

None. The issue's one open question (is the watchdog sweep still needed?) is answered by recon: the worker's ownership-gated reapers already own orphan cleanup, so the sweep is deleted rather than patched.
