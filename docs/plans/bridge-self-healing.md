---
status: Ready
type: feature
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-06
tracking: https://github.com/tomcounsell/ai/issues/63
---

# Bridge Self-Healing & Resilience

## Problem

The bridge crashes due to SQLite session DB locks during restarts, leaving the system unavailable until manual intervention. When multiple restart attempts fight over the same session file, or a previous process doesn't fully release the lock, the bridge fails to start.

**Current behavior:**
- Bridge startup fails with "database is locked" after 3 retry attempts
- No automatic recovery from crash states
- Session watchdog monitors agent sessions but not the bridge process itself
- Manual intervention required to kill stale processes and restart
- No mechanism to detect and recover from code-caused crashes

**Desired outcome:**
- Bridge automatically recovers from DB lock issues
- Stale processes are cleaned up before restart attempts
- A separate health monitor detects persistent failures
- When code changes cause crashes, system can revert and recover
- Self-healing happens without human intervention

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Prerequisites

No prerequisites — this work has no external dependencies beyond the existing codebase.

## Solution

### Key Elements

- **Session Lock Cleanup**: Aggressive cleanup of stale processes and lock files before startup
- **Bridge Health Monitor**: Separate watchdog process that monitors the bridge itself (not just agent sessions)
- **Crash Detection & Recovery**: Track recent commits and auto-revert if crashes correlate with code changes
- **Failover Escalation**: Progressive recovery attempts (retry → cleanup → revert → alert)

### Flow

**Bridge crashes** → Health monitor detects → **Cleanup stale locks** → Restart attempt → **Success** → Normal operation

**Restart fails** → Check recent commits → **Revert to last known good** → Restart → **Success** → Alert user of revert

**Revert fails** → **Alert human** → Provide diagnostic info

### Technical Approach

1. **Enhanced Session Lock Handling** (bridge/telegram_bridge.py)
   - Before retry loop: forcibly kill any process holding the session file
   - Use `lsof` to identify lock holders, `kill -9` to release
   - Add exponential backoff with jitter (2s, 5s, 10s instead of 2s, 4s, 6s)
   - Clear any `-journal` or `-wal` files that indicate incomplete transactions

2. **Bridge Health Monitor** (monitoring/bridge_watchdog.py)
   - Separate process (not inside the bridge) that monitors bridge health
   - Checks: process running, recent log activity, Telegram connection status
   - Runs every 60 seconds via launchd (separate from main bridge)
   - Can invoke recovery actions without being affected by bridge crashes

3. **Crash Correlation Tracker** (monitoring/crash_tracker.py)
   - Log each bridge start/crash with timestamp and current git commit
   - If 3+ crashes within 30 minutes after a commit, flag as "suspect commit"
   - Provide revert recommendation or auto-revert if enabled

4. **Auto-Revert Mechanism** (scripts/auto-revert.sh)
   - Triggered by bridge watchdog when crash pattern detected
   - `git revert HEAD --no-commit && git commit -m "Auto-revert: bridge crash recovery"`
   - Restart bridge after revert
   - Send Telegram alert about the revert

5. **Recovery Escalation Chain**
   - Level 1: Simple restart (current behavior)
   - Level 2: Kill stale processes + restart
   - Level 3: Clear lock files + restart
   - Level 4: Revert recent commit + restart
   - Level 5: Alert human with full diagnostics

## Rabbit Holes

- **Complex process management**: Don't build a full supervisor system (systemd, supervisord, etc.). Keep it simple: kill, clean, restart. No process pools or worker management.
- **Distributed locking**: SQLite locks are local. Don't over-engineer with Redis, file locks, or lock servers. Just use `lsof` and `kill`.
- **Git history analysis**: Only look at HEAD~1. Don't analyze commit history depth, bisect for bad commits, or build a "known good" commit database.
- **Monitoring dashboards**: Alerts via Telegram are sufficient. No web UI, Prometheus metrics, Grafana dashboards, or health check endpoints.
- **Retry sophistication**: Simple exponential backoff is enough. No circuit breakers, retry budgets, or adaptive algorithms.
- **State machines**: Don't model recovery as a formal state machine. Simple if/else escalation is fine.
- **Configuration complexity**: Hardcode sensible defaults. Don't add config files, environment variables, or runtime configuration for recovery parameters.
- **Log analysis**: Don't parse logs for error patterns beyond simple string matching. No log aggregation, structured logging pipelines, or ML-based anomaly detection.
- **Health check protocols**: Don't implement HTTP health endpoints, gRPC health checks, or standardized health check formats. Just check if process exists and logs are recent.

## Risks

### Risk 1: Auto-revert reverts good code
**Impact:** Working features get rolled back unnecessarily
**Mitigation:** Require 3+ crashes in 30 min AND commit within last hour. Conservative thresholds.

### Risk 2: Health monitor itself crashes
**Impact:** No monitoring, false sense of security
**Mitigation:** Use launchd KeepAlive for monitor too. Minimal dependencies in monitor code.

### Risk 3: Aggressive cleanup kills active sessions
**Impact:** User work interrupted mid-task
**Mitigation:** Only kill processes older than 60s that are holding locks. Check for active sessions before cleanup.

## No-Gos (Out of Scope)

- Multi-machine coordination (this is single-machine self-healing)
- Complex rollback strategies (only HEAD~1 revert, no cherry-picks or selective reverts)
- Performance monitoring (focus on crash recovery only, not latency/throughput)
- Database corruption recovery (only lock issues, not data integrity)
- Agent session healing (existing watchdog handles that)
- Automatic dependency updates or pip install during recovery
- Remote restart capabilities or SSH-based recovery
- Slack/Discord/email alerting (Telegram only)
- Crash dump analysis or core file inspection
- Memory leak detection or resource usage trending
- Scheduled maintenance windows or planned downtime handling
- Blue-green deployments or canary releases
- Backup/restore mechanisms for session data
- Integration with external monitoring services (Datadog, New Relic, etc.)
- Custom logging frameworks or structured log formats
- Retry queues for failed operations beyond immediate restart

## Update System

The `/update` skill and `scripts/remote-update.sh` need awareness of:
- New launchd plist for bridge watchdog (`com.valor.bridge-watchdog`)
- The update process should NOT run while recovery is in progress
- Add lock file check: if `data/recovery-in-progress` exists, skip update

Changes to update:
- `scripts/valor-service.sh install` should also install the bridge watchdog service
- Add `scripts/update/recovery.py` module to check recovery state

## Agent Integration

No agent integration required — this is infrastructure-level self-healing that operates below the agent layer. The agent benefits from increased uptime but doesn't need to invoke these mechanisms.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/bridge-self-healing.md` describing the recovery system
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Document recovery escalation levels in code comments
- [ ] Add troubleshooting section to deployment docs

## Success Criteria

- [ ] Bridge recovers from DB lock errors without manual intervention
- [ ] Stale processes are killed before startup attempts
- [ ] Bridge watchdog runs as separate launchd service
- [ ] Crash tracker logs start/crash events with commit info
- [ ] Auto-revert triggers after 3+ crashes within 30 min of a commit
- [ ] Telegram alerts sent for recovery actions and escalations
- [ ] Recovery lock prevents concurrent recovery/update operations
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (lock-cleanup)**
  - Name: lock-cleanup-builder
  - Role: Implement session lock detection and cleanup
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-watchdog)**
  - Name: watchdog-builder
  - Role: Create separate bridge health monitor
  - Agent Type: builder
  - Resume: true

- **Builder (crash-tracker)**
  - Name: crash-tracker-builder
  - Role: Implement crash correlation and auto-revert
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all components work together
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Enhanced Session Lock Cleanup
- **Task ID**: build-lock-cleanup
- **Depends On**: none
- **Assigned To**: lock-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `cleanup_session_locks()` function to `bridge/telegram_bridge.py`
- Use `lsof` to find processes holding `*.session` files
- Kill stale processes (>60s old) before retry loop
- Clear `-journal` and `-wal` files
- Add exponential backoff with jitter to retry loop

### 2. Bridge Health Monitor
- **Task ID**: build-bridge-watchdog
- **Depends On**: none
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `monitoring/bridge_watchdog.py` as standalone script
- Check: process exists, recent log activity (<5 min), no crash indicators
- Implement recovery escalation chain (5 levels)
- Add launchd plist template to `scripts/valor-service.sh install`
- Run every 60 seconds, independent of main bridge

### 3. Crash Tracker & Auto-Revert
- **Task ID**: build-crash-tracker
- **Depends On**: none
- **Assigned To**: crash-tracker-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `monitoring/crash_tracker.py`
- Log each start/crash to `data/crash_history.jsonl`
- Include timestamp, git commit hash, crash reason
- Detect pattern: 3+ crashes within 30 min after recent commit
- Create `scripts/auto-revert.sh` for safe revert operation
- Send Telegram alert on auto-revert

### 4. Update System Integration
- **Task ID**: build-update-integration
- **Depends On**: build-bridge-watchdog, build-crash-tracker
- **Assigned To**: lock-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add recovery state check to `scripts/update/run.py`
- Create `data/recovery-in-progress` lock file during recovery
- Update `scripts/valor-service.sh install` to install watchdog service
- Ensure update skips when recovery in progress

### 5. Integration Validation
- **Task ID**: validate-integration
- **Depends On**: build-lock-cleanup, build-bridge-watchdog, build-crash-tracker, build-update-integration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify bridge starts cleanly after simulated lock
- Verify watchdog detects stopped bridge
- Verify crash tracker logs events correctly
- Verify recovery lock prevents concurrent operations
- Run `./scripts/valor-service.sh health`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/bridge-self-healing.md`
- Add entry to `docs/features/README.md`
- Update `docs/deployment.md` with recovery info

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Test end-to-end recovery scenario

## Validation Commands

- `./scripts/valor-service.sh status` - Bridge running
- `launchctl list | grep com.valor.bridge-watchdog` - Watchdog service installed
- `python -c "from monitoring.bridge_watchdog import check_bridge_health; print(check_bridge_health())"` - Health check works
- `python -c "from monitoring.crash_tracker import get_recent_crashes; print(get_recent_crashes())"` - Crash tracker works
- `ls data/crash_history.jsonl` - Crash history file exists
- `cat docs/features/bridge-self-healing.md` - Documentation exists

## Decisions

- **Auto-revert**: Opt-in via config, disabled by default. Clear alerts when triggered.
- **Alert destination**: DM to supervisor only.
- **Watchdog frequency**: Hardcoded 60 seconds. No configuration.
