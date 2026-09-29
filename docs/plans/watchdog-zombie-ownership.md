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

(filled below)

## Failure Path Test Strategy

(filled below)

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py` — UPDATE (details below)

## Rabbit Holes

(filled below)

## Risks

(filled below)

## Race Conditions

(filled below)

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this change deletes code from a launchd-run script that
`/update` already restarts; there are no new dependencies, config keys, or migrations.

## Agent Integration

No agent integration required — this is a watchdog/worker-internal change with no new tool or
CLI surface.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` (details below)

## Success Criteria

(filled below)

## Team Orchestration

(filled below)

## Step by Step Tasks

(filled below)

## Verification

(filled below)

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

(filled below)
