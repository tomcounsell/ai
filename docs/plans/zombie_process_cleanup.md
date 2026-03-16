---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-03-16
tracking: https://github.com/tomcounsell/ai/issues/426
last_comment_id:
---

# Zombie Process Detection and Cleanup in Bridge Watchdog

## Problem

Claude Code CLI subprocesses become orphaned when their parent session ends abnormally (timeout, crash, network disconnect). These zombie processes persist indefinitely, accumulating memory pressure that starves new SDK init calls.

**Current behavior:**
The bridge watchdog (`monitoring/bridge_watchdog.py`) checks only bridge process health: is it running, are logs fresh, is there a crash pattern. It has zero visibility into zombie Claude Code processes. The in-session health check (`agent/health_check.py`) only monitors its own session. Nobody is watching for orphans at the system level.

In a recent incident: 3 zombie `claude` processes idle 6–9 days consumed 1.75 GB RAM. Combined with a stale 635 MB Pyright process and 7 concurrent active instances on a 16 GB machine, memory hit 81% — enough to starve SDK init and cause two worker crashes (600s timeout).

**Desired outcome:**
The watchdog detects zombie processes, reports them in health checks, auto-kills them during recovery, and tracks concurrent instance count with configurable limits.

## Prior Art

No prior issues found related to zombie Claude process detection or cleanup.

The session watchdog (`monitoring/session_watchdog.py`, lines 722–778) already has a `_cancel_worker_and_kill_subprocess()` function that kills SDK subprocesses for stalled sessions. It uses two techniques: (1) accessing `_transport._process` on active SDK clients, and (2) `pgrep -P {bridge_pid}` for orphaned child processes. However, this only catches children of the current bridge PID — true zombies (whose parent died or was restarted) are invisible to it.

## Data Flow

1. **Entry point**: Bridge watchdog fires every 60s via launchd
2. **`check_bridge_health()`**: Currently checks process, logs, crash pattern → extended to also enumerate claude/pyright processes and classify as active vs zombie
3. **`HealthStatus`**: Extended dataclass carries zombie count, PIDs, memory → surfaced in `--check-only` output
4. **Recovery escalation**: Level 2+ calls new `kill_zombie_processes()` before restart
5. **Output**: Diagnostic reporting and log warnings for concurrent instance limits

## Architectural Impact

- **New dependencies**: None. Uses `subprocess` (already imported) and `ps` (macOS standard)
- **Interface changes**: `HealthStatus` dataclass gets new fields; `--check-only` output gets new lines
- **Coupling**: Contained within `monitoring/bridge_watchdog.py`. No cross-module coupling added
- **Reversibility**: Trivially reversible — remove the new functions and `HealthStatus` fields

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Contained to one file (`monitoring/bridge_watchdog.py`) plus tests. No architectural complexity.

## Prerequisites

No prerequisites — uses only macOS built-in `ps` command and existing Python stdlib.

## Solution

### Key Elements

- **Process scanner**: Enumerates all `claude` and `pyright` processes system-wide using `ps`, calculates age and memory per process
- **Zombie classifier**: Distinguishes zombies (idle > threshold) from active sessions by cross-referencing CPU time delta or process age
- **Cleanup function**: Kills identified zombies with SIGTERM → SIGKILL escalation, integrated into recovery level 2+
- **Instance tracker**: Counts concurrent active `claude` processes, warns when exceeding soft limit

### Flow

**Watchdog fires (60s)** → `check_bridge_health()` → enumerate claude/pyright processes → classify active vs zombie → update `HealthStatus` with zombie data → if recovery needed, `kill_zombie_processes()` at level 2+ → log instance count warnings

### Technical Approach

**Zombie detection signal: process elapsed time (etimes)**

The key architectural choice is how to distinguish a zombie from a slow-starting or legitimately busy process. Options considered:

1. **CPU time delta over interval** — requires state between watchdog runs, adds complexity
2. **Process elapsed time (etimes) with threshold** — stateless, simple, reliable
3. **Cross-reference with `_active_clients` registry** — requires the watchdog to import bridge internals

**Decision: Use elapsed time (option 2).** A `claude` process running for >2 hours is almost certainly a zombie — normal sessions complete in minutes to tens of minutes. The 2-hour threshold provides a large safety margin. This is stateless (no inter-run bookkeeping), uses only `ps` output, and avoids coupling the external watchdog to bridge internals.

**Process enumeration via `ps`:**
```
ps -eo pid,etime,rss,command | grep -E '(claude|pyright)'
```
Parse PID, elapsed time, RSS (memory), and command. Filter to relevant process names. Convert `etime` to seconds for threshold comparison.

**Integration into recovery escalation:**
- Level 1 (process not running): No change — just restart
- Level 2+ (stale logs, locks, crash pattern): Call `kill_zombie_processes()` before existing recovery steps
- Standalone: `--check-only` reports zombie data regardless of recovery level

**Concurrent instance soft limit:**
Count active `claude` processes (those under the zombie threshold). Log a warning when count exceeds a configurable soft limit (default: 5). This is informational — it doesn't trigger recovery, just alerts.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_enumerate_claude_processes()` wraps `subprocess.run()` in try/except — test that `ps` failure returns empty list (not crash)
- [ ] `kill_zombie_processes()` handles `ProcessLookupError` (process died between detection and kill) — test with mock

### Empty/Invalid Input Handling
- [ ] `ps` returns no matching processes → empty list, no action
- [ ] `ps` output has unexpected format → parser skips malformed lines, logs warning
- [ ] Elapsed time parsing handles all `ps` formats: `MM:SS`, `HH:MM:SS`, `D-HH:MM:SS`

### Error State Rendering
- [ ] `--check-only` output includes zombie section even when 0 zombies found ("Zombie processes: 0")

## Rabbit Holes

- **Monitoring Pyright and Node deeply** — Just kill them if they're old. Don't build a taxonomy of Claude Code's child processes.
- **State between watchdog runs** — Don't persist process lists in Redis. Keep it stateless — `ps` on each run is fast and sufficient.
- **Importing bridge internals** — The watchdog is deliberately an external process. Don't couple it to `_active_clients` or SDK internals.

## Risks

### Risk 1: False positive kills an active session
**Impact:** An actively working Claude Code session gets terminated mid-work
**Mitigation:** 2-hour elapsed time threshold provides enormous safety margin (sessions rarely exceed 30 minutes). The threshold is configurable so it can be raised if needed. Only processes matching `claude` command pattern are targeted.

### Risk 2: `ps` output format differs across macOS versions
**Impact:** Parser fails to detect zombies
**Mitigation:** Test with actual `ps` output on the target machine. Use `-eo` explicit column format (stable across versions). Parser logs warnings on unparseable lines rather than crashing.

## Race Conditions

No race conditions identified. The watchdog runs as a single-threaded external process on a 60s interval. Process enumeration via `ps` is a point-in-time snapshot. Even if a process dies between enumeration and kill, `ProcessLookupError` is caught explicitly.

## No-Gos (Out of Scope)

- Real-time process monitoring (polling faster than 60s)
- Tracking process genealogy (parent-child trees)
- Integration with macOS Activity Monitor or `top`
- Persisting historical process data in Redis
- Modifying the in-session health check (`agent/health_check.py`) — this is watchdog-only

## Update System

No update system changes required — this modifies only `monitoring/bridge_watchdog.py` and its tests. No new dependencies, no config files, no migration steps.

## Agent Integration

No agent integration required — this is a watchdog-internal change. No MCP server exposure needed. The bridge does not call this code directly (launchd runs it as a separate process).

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` — add section on zombie process detection under the Bridge Watchdog component
- [ ] Update CLAUDE.md critical thresholds table if concurrent instance limit is added

## Success Criteria

- [ ] `python monitoring/bridge_watchdog.py --check-only` reports zombie process count, PIDs, ages, and memory
- [ ] Zombie processes (elapsed time > configurable threshold, default 2h) are killed during recovery level 2+
- [ ] Concurrent active `claude` instance count is logged; warning emitted when exceeding soft limit (default 5)
- [ ] No false positives: threshold ensures active sessions are not killed
- [ ] Tests cover: process enumeration parsing, zombie classification, elapsed time parsing, kill with SIGTERM/SIGKILL escalation, `--check-only` output format
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (watchdog)**
  - Name: watchdog-builder
  - Role: Implement zombie detection, classification, cleanup, and diagnostic output in bridge_watchdog.py
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog)**
  - Name: watchdog-validator
  - Role: Verify implementation meets all success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update bridge-self-healing.md with zombie detection docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement zombie process detection and cleanup
- **Task ID**: build-watchdog
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_watchdog.py (create)
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_parse_elapsed_time(etime_str) -> int` helper to convert ps etime formats to seconds
- Add `_enumerate_claude_processes() -> list[dict]` using `ps -eo pid,etime,rss,command` filtered to claude/pyright patterns
- Add `classify_zombies(processes, threshold_seconds=7200) -> tuple[list, list]` returning (zombies, active)
- Add `kill_zombie_processes(zombies) -> int` with SIGTERM → 3s wait → SIGKILL escalation
- Extend `HealthStatus` dataclass with `zombie_count: int`, `zombie_pids: list[int]`, `zombie_memory_mb: float`, `active_claude_count: int`
- Extend `check_bridge_health()` to call enumerate/classify and populate new fields; log warning if `active_claude_count > SOFT_INSTANCE_LIMIT`
- Integrate `kill_zombie_processes()` into `execute_recovery()` at level 2+
- Extend `--check-only` output to print zombie and instance count data
- Add configurable constants: `ZOMBIE_THRESHOLD_SECONDS = 7200`, `SOFT_INSTANCE_LIMIT = 5`
- Write unit tests covering: etime parsing (MM:SS, HH:MM:SS, D-HH:MM:SS), process enumeration with mocked ps output, zombie classification logic, kill escalation, HealthStatus population, --check-only output format

### 2. Validate implementation
- **Task ID**: validate-watchdog
- **Depends On**: build-watchdog
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_watchdog.py -v` — all tests pass
- Run `python -m ruff check monitoring/bridge_watchdog.py` — lint clean
- Run `python monitoring/bridge_watchdog.py --check-only` — verify zombie section appears in output
- Verify no imports from bridge internals (agent/, bridge/) in watchdog
- Verify SIGTERM before SIGKILL in kill function

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-watchdog
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with zombie detection section
- Add zombie detection to the health checks table and recovery escalation table

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify all success criteria met
- Verify docs updated

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_bridge_watchdog.py -v` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/bridge_watchdog.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/bridge_watchdog.py` | exit code 0 |
| Check-only output | `python monitoring/bridge_watchdog.py --check-only 2>&1 \| grep -i zombie` | exit code 0 |
| No bridge imports | `grep -E '^from (agent|bridge)\.' monitoring/bridge_watchdog.py \| grep -v crash_tracker` | exit code 1 |
