# Bridge Self-Healing & Resilience

The bridge includes a multi-layered self-healing system to recover from crashes without manual intervention.

## Components

### 1. Session Lock Cleanup (`bridge/telegram_bridge.py`)

**Problem**: SQLite session DB locks occur when:
- Multiple bridge instances fight over the same session file during restarts
- A previous process doesn't fully release the lock before exit

**Solution**: Before attempting to connect, the bridge:
1. Uses `lsof` to find processes holding `*.session` files
2. Kills stale processes (>60 seconds old) that aren't the current process
3. Clears orphaned `-journal`, `-wal`, `-shm` files
4. Adds jitter to prevent thundering herd on restart

**Retry Logic**: Exponential backoff with cleanup between attempts (2s, 5s, 10s).

### 2. Crash Tracker (`monitoring/crash_tracker.py`)

Logs bridge start/crash events with:
- Timestamp
- Current git commit SHA
- Commit age in seconds
- Crash reason (if available)

**Pattern Detection**: Identifies when 3+ crashes occur within 30 minutes after a recent commit (<1 hour old), suggesting code-caused crashes.

**Usage**:
```python
from monitoring.crash_tracker import log_start, log_crash, detect_crash_pattern

# Log events
log_start()
log_crash("database is locked")

# Check for patterns
should_revert, commit_sha = detect_crash_pattern()
```

### 3. Bridge Watchdog (`monitoring/bridge_watchdog.py`)

A separate process that monitors bridge health and executes recovery. Runs via launchd every 60 seconds.

**Health Checks**:
- Process running (`pgrep -f telegram_bridge.py`)
- Logs fresh (written within 5 minutes)
- No crash pattern detected

**5-Level Recovery Escalation**:

| Level | Condition | Action |
|-------|-----------|--------|
| 1 | Process not running | Simple restart (launchd) |
| 2 | Process running but logs stale | Kill stale + restart |
| 3 | Lock files present | Clear locks + restart |
| 4 | Crash pattern detected | Revert HEAD + restart (if enabled) |
| 5 | Recovery exhausted | Alert human via Telegram |

**Auto-Revert** (Level 4):
- Disabled by default
- Enable: `touch data/auto-revert-enabled`
- Creates a git revert commit and pushes to remote
- Sends Telegram alert about the revert

### 4. Service Installation

The watchdog is installed alongside the bridge:
```bash
./scripts/valor-service.sh install
# Installs:
# - com.valor.bridge (main bridge)
# - com.valor.update (cron at 06:00/18:00)
# - com.valor.bridge-watchdog (every 60s)
```

## Recovery Lock

During recovery, `data/recovery-in-progress` is created to prevent:
- Concurrent recovery attempts
- Updates running during recovery

The lock auto-expires after 5 minutes.

## Manual Operations

**Check health**:
```bash
python monitoring/bridge_watchdog.py --check-only
```

**View crash history**:
```bash
cat data/crash_history.jsonl
```

**Enable auto-revert** (use with caution):
```bash
touch data/auto-revert-enabled
```

**Disable auto-revert**:
```bash
rm data/auto-revert-enabled
```

**Manual revert**:
```bash
./scripts/auto-revert.sh
```

## Files

| File | Purpose |
|------|---------|
| `monitoring/crash_tracker.py` | Crash event logging and pattern detection |
| `monitoring/bridge_watchdog.py` | External health monitor |
| `scripts/auto-revert.sh` | Git revert and restart |
| `data/crash_history.jsonl` | Crash event log |
| `data/recovery-in-progress` | Recovery lock file |
| `data/auto-revert-enabled` | Auto-revert enable flag |
| `logs/watchdog.log` | Watchdog output |

## Design Principles

Per the plan (docs/plans/bridge-self-healing.md):

- **No complex process management** - Just kill, clean, restart
- **No distributed locking** - Local SQLite only
- **No deep git analysis** - Only HEAD~1 revert
- **No monitoring dashboards** - Telegram alerts only
- **No configuration** - Hardcoded 60s watchdog, sensible defaults
- **No external services** - Self-contained recovery
