---
status: Ready
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/754
---

# Worker Service Operational Gaps (uninstall, restart, log rotation)

> **Note on title:** Issue #754 has a misleading title ("Module-level int() on TELEGRAM_API_ID..."), but the issue body, recon, and acceptance criteria all describe worker service operational gaps. This plan addresses the actual issue body content.

## Problem

Three operational gaps exist in the worker service integration with `scripts/valor-service.sh`:

**Current behavior:**
1. `uninstall_service()` (lines 447-476) unloads bridge, update, and watchdog plists but does NOT touch `com.valor.worker`. Running `./scripts/valor-service.sh uninstall` leaves the worker registered in launchd, auto-starting on every boot.
2. The top-level `restart` command only calls `restart_bridge`. After changes to shared modules (`agent/`, `worker/`), the worker keeps running stale code. Operators must remember to also run `worker-restart`.
3. `config/newsyslog.valor.conf` rotates `bridge.error.log`, `watchdog.log`, and `reflections*.log` but omits `worker.log` and `worker_error.log`. Worker logs grow unbounded.

**Desired outcome:**
- `uninstall` removes ALL Valor launchd services including the worker.
- `restart` restarts both bridge and worker in one command.
- Worker logs are rotated by newsyslog.
- `CLAUDE.md` accurately reflects the new restart behavior.

## Prior Art

- **#737**: Extract standalone worker service from bridge monolith — created the worker plist and management commands.
- **#751**: Enforce bridge/worker separation — completed the split, surfacing the gaps this plan closes.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: `restart` now restarts worker too; new `worker-uninstall` subcommand.
- **Coupling**: None added — purely operational glue.
- **Reversibility**: Trivial (revert script and conf changes).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all changes are localized to two files plus a doc update.

## Solution

### Key Elements

- **`uninstall_service()`**: Add a worker plist unload + remove block mirroring the bridge/update/watchdog blocks. Also stop any running worker process.
- **`worker-uninstall` subcommand**: New top-level case that unloads/removes only the worker plist for standalone use; called by `uninstall_service()`.
- **`restart` command**: Call both `restart_bridge` and `restart_worker` so a single command refreshes all running services.
- **`newsyslog.valor.conf`**: Add `worker.log` and `worker_error.log` rotation entries matching the existing pattern.
- **`CLAUDE.md`**: Update the Quick Commands table entry for `restart` to reflect that it now restarts both bridge and worker.

### Technical Approach

- Add code in `uninstall_service()` (after the watchdog block, before `stop_bridge`) that mirrors the existing pattern: check `$WORKER_PLIST_PATH`, `launchctl unload`, `rm -f`, echo result. Also call worker stop helper.
- Add a `worker-uninstall)` case in the main `case` statement near the other worker subcommands.
- In the top-level `restart)` case, call `restart_bridge` then `restart_worker` (worker only restarts if installed; `restart_worker` already handles the not-installed case gracefully).
- Append two lines to `config/newsyslog.valor.conf` for `worker.log` and `worker_error.log` using the same `644 5 10240 * NJ` pattern.
- Edit the `restart` row in `CLAUDE.md`'s Quick Commands table to say "Restart bridge AND worker after code changes".

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — bash script uses `2>/dev/null || true` for idempotent unloads, matching existing patterns.

### Empty/Invalid Input Handling
- `uninstall` is idempotent; if worker plist doesn't exist, the block prints "Worker service was not installed" and continues.
- `restart` calling `restart_worker` when worker isn't installed: existing `restart_worker` already handles this case.

### Error State Rendering
- All operations echo human-readable status to stdout, matching existing pattern.

## Test Impact

No existing tests affected — `scripts/valor-service.sh` is a bash service manager with no test coverage in this repo, and `config/newsyslog.valor.conf` is a static config file. Manual verification on a dev machine is the validation method (see Verification section).

## Rabbit Holes

- Do NOT refactor `valor-service.sh` to deduplicate the four near-identical plist-unload blocks. Tempting but out of scope.
- Do NOT add a generic "service registry" abstraction. Each service has subtle differences (watchdog depends on bridge, worker is independent) that resist abstraction.
- Do NOT touch the worker install/start logic — it works.

## Risks

### Risk 1: `restart` becomes slower
**Impact:** Operators waiting on a restart now wait for both services to come up.
**Mitigation:** Acceptable — the worker restart is fast (a few seconds) and correctness wins over speed.

### Risk 2: newsyslog rotation breaks if worker plist isn't installed on a machine
**Impact:** newsyslog may log a warning if a referenced log file doesn't exist.
**Mitigation:** newsyslog handles missing files silently — it just skips them. This matches the behavior for the existing `bridge.error.log` entry on machines without the bridge installed.

## Race Conditions

No race conditions identified — all operations are synchronous bash command sequences. `launchctl unload` followed by `rm -f` is idempotent and ordering-safe.

## No-Gos (Out of Scope)

- Refactoring `valor-service.sh` for DRY.
- Adding a service registry abstraction.
- Changing the watchdog or update cron behavior.
- Touching the worker plist generator or install logic.

## Update System

The `/update` skill calls `valor-service.sh restart` after pulling. Once this plan ships, that single call will refresh both bridge and worker — which is the desired behavior. No `/update` skill changes required; the fix flows through automatically.

The `newsyslog.valor.conf` file is installed via `sudo cp config/newsyslog.valor.conf /etc/newsyslog.d/valor.conf`. Machines that already have this installed will need to re-copy the file. Add a one-line note to the update skill if needed, but acceptable to handle manually since newsyslog config is rarely changed.

## Agent Integration

No agent integration required — this is a purely operational change to bash scripts and a config file. The agent does not call these scripts directly; humans (and the `/update` skill) invoke them.

## Documentation

### Feature Documentation
- [ ] Update `CLAUDE.md` Quick Commands table: change `restart` row description to "Restart bridge AND worker after code changes".
- [ ] No new feature doc needed — this is a bug fix to existing operational tooling.

### Inline Documentation
- [ ] Add a brief comment above the new worker block in `uninstall_service()` matching the existing block comment style.

## Success Criteria

- [ ] `./scripts/valor-service.sh uninstall` unloads and removes `com.valor.worker` plist along with bridge/update/watchdog.
- [ ] `worker-uninstall` subcommand exists and appears in the script's usage output.
- [ ] `./scripts/valor-service.sh restart` calls both `restart_bridge` and `restart_worker`.
- [ ] `config/newsyslog.valor.conf` contains entries for `worker.log` and `worker_error.log`.
- [ ] `CLAUDE.md` Quick Commands `restart` row reflects the new behavior.
- [ ] Manual verification on dev machine: install worker, run `restart`, confirm worker PID changes; run `uninstall`, confirm worker plist removed from `~/Library/LaunchAgents/`.

## Team Orchestration

Single builder; no parallelism needed for a Small bash/config change.

### Team Members

- **Builder (worker-service-fixes)**
  - Name: worker-service-builder
  - Role: Apply the four edits across `scripts/valor-service.sh`, `config/newsyslog.valor.conf`, and `CLAUDE.md`.
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Patch `valor-service.sh` uninstall and restart
- **Task ID**: build-script
- **Depends On**: none
- **Validates**: Manual run of `./scripts/valor-service.sh` shows `worker-uninstall` in usage; `uninstall_service()` contains a worker block.
- **Assigned To**: worker-service-builder
- **Agent Type**: builder
- **Parallel**: false
- Add worker plist unload/remove block to `uninstall_service()` after the watchdog block (around line 472, before `stop_bridge`). Mirror the existing pattern. Also call the worker stop helper after.
- Add a `worker-uninstall)` case in the main `case` statement that performs the same unload/remove for standalone use, and refactor `uninstall_service()` to call it.
- In the top-level `restart)` case (line 622-623), call `restart_bridge` followed by `restart_worker`.
- Update the script's `usage` function to list `worker-uninstall`.

### 2. Patch `newsyslog.valor.conf`
- **Task ID**: build-newsyslog
- **Depends On**: none
- **Assigned To**: worker-service-builder
- **Agent Type**: builder
- **Parallel**: true
- Append two lines to `config/newsyslog.valor.conf` for `worker.log` and `worker_error.log` using the existing `644 5 10240 * NJ` pattern and matching path prefix.

### 3. Update `CLAUDE.md`
- **Task ID**: build-claudemd
- **Depends On**: none
- **Assigned To**: worker-service-builder
- **Agent Type**: builder
- **Parallel**: true
- Update the Quick Commands table row for `./scripts/valor-service.sh restart` to say "Restart bridge AND worker after code changes".

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-script, build-newsyslog, build-claudemd
- **Assigned To**: worker-service-builder
- **Agent Type**: validator
- **Parallel**: false
- Run `bash -n scripts/valor-service.sh` for syntax check.
- Grep `scripts/valor-service.sh` for `worker-uninstall` and confirm it appears in both the case statement and usage.
- Grep `config/newsyslog.valor.conf` for `worker.log` and `worker_error.log`.
- Confirm `CLAUDE.md` row updated.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Bash syntax | `bash -n scripts/valor-service.sh` | exit code 0 |
| worker-uninstall present | `grep -c 'worker-uninstall' scripts/valor-service.sh` | output > 1 |
| Worker block in uninstall_service | `grep -A 30 '^uninstall_service' scripts/valor-service.sh \| grep -c WORKER_PLIST_PATH` | output > 0 |
| restart calls restart_worker | `awk '/restart\)/,/;;/' scripts/valor-service.sh \| grep -c restart_worker` | output > 0 |
| worker.log in newsyslog | `grep -c 'worker.log' config/newsyslog.valor.conf` | output > 1 |
| CLAUDE.md restart row updated | `grep -c 'Restart bridge AND worker' CLAUDE.md` | output > 0 |

## Critique Results

Skipped per user instruction.
