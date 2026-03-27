---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/570
last_comment_id:
---

# Log Rotation Cleanup

## Problem

Reflections report (#570) flagged four log files growing without bounds on the bridge machine: `bridge.error.log` (18.7MB), `issue_poller_error.log` (145.4MB), `bridge.log` (34.2MB), `issue_poller.log` (145.4MB). PR #578 already fixed `bridge.log` by switching to `RotatingFileHandler`, but five other log files still grow unbounded.

**Current behavior:**
- `issue_poller.py` uses `logging.FileHandler` (no rotation, no size cap)
- launchd `StandardErrorPath`/`StandardOutPath` captures for bridge, issue_poller, watchdog, and reflections write directly to log files with no rotation mechanism
- On the bridge machine, `issue_poller_error.log` alone reached 145.4MB

**Desired outcome:**
- All Python-managed log files use `RotatingFileHandler` with consistent caps (10MB, 5 backups — matching bridge.log precedent)
- All launchd-managed log files (stderr/stdout redirects) are rotated via macOS `newsyslog` configuration
- No log file can grow unbounded

## Prior Art

- **Issue #569 / PR #578**: "Reflection observability: resource guards, log rotation, crash detection" — Fixed `bridge.log` rotation (RotatingFileHandler 10MB/5 backups). Did NOT address other log files.
- **Issue #223 / PR #224**: "Fix top 5 bridge error log issues" — Addressed error patterns but not log file growth.

## Data Flow

1. **Python logging** (`issue_poller.py` `setup_logging()`): Python writes to `logs/issue_poller.log` via `FileHandler`
2. **launchd stderr capture**: launchd redirects process stderr to `logs/issue_poller_error.log`, `logs/bridge.error.log`, `logs/reflections_error.log` via `StandardErrorPath` in plist configs
3. **launchd stdout capture**: launchd redirects watchdog stdout+stderr to `logs/watchdog.log` via plist config
4. **On disk**: Files grow without any rotation, truncation, or archival

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Issue poller RotatingFileHandler**: Switch `scripts/issue_poller.py` from `FileHandler` to `RotatingFileHandler` (10MB, 5 backups) matching the bridge.log precedent
- **newsyslog config**: Create `/etc/newsyslog.d/valor.conf` entries for all launchd-managed log files (`bridge.error.log`, `issue_poller_error.log`, `watchdog.log`, `reflections_error.log`, `reflections.log`) with 10MB rotation and 5 archives
- **Install script**: Add newsyslog config installation to `scripts/valor-service.sh` so it propagates on service install

### Flow

**Issue poller Python log** → RotatingFileHandler rotates at 10MB → keeps 5 backups → old files auto-deleted

**launchd stderr/stdout logs** → newsyslog checks hourly → rotates files > 10MB → keeps 5 compressed archives

### Technical Approach

- Replace `logging.FileHandler` with `logging.handlers.RotatingFileHandler` in `scripts/issue_poller.py` (same pattern as `bridge/telegram_bridge.py` lines 529-533)
- Create a newsyslog config file at `config/newsyslog.valor.conf` in the repo
- Add an installation step in `scripts/valor-service.sh` that copies this to `/etc/newsyslog.d/valor.conf` (requires sudo)
- newsyslog is built into macOS and runs hourly via launchd — no additional services needed

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is a configuration change and a one-line handler swap

### Empty/Invalid Input Handling
- Not applicable — log rotation is infrastructure, not input processing

### Error State Rendering
- Not applicable — no user-visible output

## Test Impact

- [ ] `tests/unit/test_reflections_scheduling.py::test_launchd_plist_content` — UPDATE: if test validates plist content, may need to account for newsyslog mention in install script
- [ ] `tests/test_issue_poller.py` — UPDATE: if test validates logging setup, assert RotatingFileHandler instead of FileHandler

## Rabbit Holes

- Building a custom log rotation daemon — macOS already has newsyslog, use it
- Rotating logs via Python for launchd-managed files — Python doesn't control these files, newsyslog does
- Adding log compression to Python RotatingFileHandler — the stdlib handler doesn't compress; 5x10MB is only 50MB total, acceptable
- Centralizing all logging into a single framework — separate concern, would touch every service

## Risks

### Risk 1: newsyslog requires sudo for /etc/newsyslog.d/
**Impact:** Install script may fail without elevated permissions
**Mitigation:** The `valor-service.sh install` already requires manual steps; document the sudo requirement clearly. Alternatively, use a user-level newsyslog path if available.

### Risk 2: newsyslog rotation of actively-written files
**Impact:** launchd holds file handles open; rotation could cause log loss
**Mitigation:** newsyslog sends SIGHUP by default after rotation, but launchd processes don't reopen files on SIGHUP. Use the `N` flag (no signal) which works because launchd reopens the file path on each write cycle.

## Race Conditions

No race conditions identified — RotatingFileHandler handles its own locking, and newsyslog operates at the filesystem level with atomic rename.

## No-Gos (Out of Scope)

- Log aggregation or centralized logging service
- Modifying log content or format (purely rotation)
- Addressing the warning counts mentioned in the reflections report (separate concern)
- Fixing bugs #567 and #564 (already tracked separately)

## Update System

The update script (`scripts/remote-update.sh`) needs a one-time addition: after pulling code, copy `config/newsyslog.valor.conf` to `/etc/newsyslog.d/valor.conf` if it doesn't exist (or if it has changed). This ensures new machines get log rotation on first update.

## Agent Integration

No agent integration required — this is infrastructure/ops configuration with no agent-facing tools or MCP changes.

## Documentation

- [ ] Update `docs/features/reflections.md` to document log rotation coverage (which files are Python-rotated vs newsyslog-rotated)
- [ ] Add inline comments in `scripts/valor-service.sh` explaining newsyslog setup

## Success Criteria

- [ ] `scripts/issue_poller.py` uses `RotatingFileHandler` (10MB, 5 backups)
- [ ] `config/newsyslog.valor.conf` exists with entries for all launchd-managed log files
- [ ] `scripts/valor-service.sh` installs newsyslog config during service setup
- [ ] No log file in `logs/` can grow beyond ~10MB without rotation
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (log-rotation)**
  - Name: rotation-builder
  - Role: Implement RotatingFileHandler swap and newsyslog config
  - Agent Type: builder
  - Resume: true

- **Validator (log-rotation)**
  - Name: rotation-validator
  - Role: Verify all log files have rotation configured
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Switch issue_poller to RotatingFileHandler
- **Task ID**: build-poller-rotation
- **Depends On**: none
- **Validates**: tests/test_issue_poller.py
- **Assigned To**: rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `logging.FileHandler` with `logging.handlers.RotatingFileHandler` in `scripts/issue_poller.py`
- Add `import logging.handlers` if not present
- Use `maxBytes=10*1024*1024, backupCount=5`

### 2. Create newsyslog config for launchd-managed logs
- **Task ID**: build-newsyslog-config
- **Depends On**: none
- **Assigned To**: rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/newsyslog.valor.conf` with entries for: `bridge.error.log`, `issue_poller_error.log`, `watchdog.log`, `reflections.log`, `reflections_error.log`
- Each entry: 10MB max, 5 archives, compressed, no signal (N flag)

### 3. Add newsyslog installation to valor-service.sh
- **Task ID**: build-install-newsyslog
- **Depends On**: build-newsyslog-config
- **Assigned To**: rotation-builder
- **Agent Type**: builder
- **Parallel**: false
- Add step in `install_bridge()` function to copy newsyslog config
- Add step in `scripts/remote-update.sh` to sync newsyslog config on updates

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-poller-rotation, build-newsyslog-config, build-install-newsyslog
- **Assigned To**: rotation-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` with log rotation coverage table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: rotation-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `issue_poller.py` uses RotatingFileHandler
- Verify newsyslog config has all 5 launchd-managed log files
- Verify `valor-service.sh` installs the config
- Run tests

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Poller uses RotatingFileHandler | `grep -c 'RotatingFileHandler' scripts/issue_poller.py` | output > 0 |
| newsyslog config exists | `test -f config/newsyslog.valor.conf` | exit code 0 |
| All launchd logs covered | `grep -c 'bridge.error.log\|issue_poller_error\|watchdog\|reflections' config/newsyslog.valor.conf` | output > 4 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the approach mirrors the existing bridge.log rotation (PR #578) and uses standard macOS infrastructure (newsyslog).
