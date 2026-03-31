---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/610
last_comment_id:
---

# Log Rotation Fix: Files Growing Past Configured Limits

## Problem

Log files in `logs/` grow unbounded despite two rotation mechanisms being in place. `bridge.error.log` reached 32MB, `bridge.log` 28MB, and the now-removed issue poller's error log hit 203MB before it was cleaned up.

**Current behavior:**
- The bridge uses `RotatingFileHandler` for `bridge.log` (correct), but `config/settings.py` uses plain `logging.FileHandler` for any service that calls `configure_logging()`.
- `scripts/reflections.py` and `monitoring/bridge_watchdog.py` use `logging.basicConfig()` with no file handler -- their logs go to launchd's stdout/stderr pipes, which write to files that are never rotated in-process.
- `bridge/session_logs.py` defines `cleanup_old_snapshots()` (removes session dirs older than 7 days) but it is never called.
- `config/newsyslog.valor.conf` still references the removed `issue_poller_error.log`.

**Desired outcome:**
All log files in `logs/` stay under 10MB via in-process `RotatingFileHandler`, stale config references are cleaned up, and session log directories are pruned automatically.

## Prior Art

- **PR #579**: "Log rotation cleanup: all log files capped at 10MB" -- Added `RotatingFileHandler` to the bridge's root logger and newsyslog config. Did not address reflections, watchdog, or `config/settings.py`. The bridge logs are now correctly rotated; the remaining services are not.
- **PR #578**: "Reflection observability: resource guards, log rotation, crash detection" -- Added newsyslog config and startup rotation in `valor-service.sh`. These mechanisms cannot reliably rotate files held open by launchd (file descriptor stays open after rename).
- **Issue #569**: "Reflection observability" -- Parent issue for #578/#579. Closed but log rotation gaps remain.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #578 | Added newsyslog config and startup rotation | newsyslog cannot rotate files held open by launchd (FD stays open after rename). Startup rotation only runs on restart, not continuously. |
| PR #579 | Added RotatingFileHandler to bridge | Only fixed bridge.log. Did not touch reflections, watchdog, or settings.py. |

**Root cause pattern:** Each fix targeted one service (bridge) but left the others using launchd stdout/stderr capture with no in-process rotation. The architectural fix is: every Python process that writes logs must use `RotatingFileHandler` directly, not rely on external rotation of launchd-captured output.

## Architectural Impact

- **No new dependencies**: `logging.handlers.RotatingFileHandler` is stdlib.
- **Interface changes**: `config/settings.py:configure_logging()` switches from `FileHandler` to `RotatingFileHandler`. Any code calling `settings.configure_logging()` automatically gets rotation.
- **Coupling**: No change -- each service independently configures its own logging.
- **Reversibility**: Trivial -- revert handler type.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Settings handler fix**: Replace `logging.FileHandler` with `RotatingFileHandler` in `config/settings.py:configure_logging()`, using the existing `max_file_size` setting.
- **Reflections file handler**: Add `RotatingFileHandler` to `scripts/reflections.py` so logs are written to `logs/reflections.log` directly with rotation, in addition to console output.
- **Watchdog file handler**: Add `RotatingFileHandler` to `monitoring/bridge_watchdog.py` for `logs/watchdog.log`.
- **Session cleanup wiring**: Call `cleanup_old_snapshots()` from `scripts/reflections.py` during its daily run.
- **Stale reference cleanup**: Remove `issue_poller_error.log` entry from `config/newsyslog.valor.conf`.

### Technical Approach

- Follow the bridge pattern from `bridge/telegram_bridge.py:531-546` -- `RotatingFileHandler` with `maxBytes=10*1024*1024`, `backupCount=5`.
- For reflections and watchdog: add a file handler to the module logger (not replace `basicConfig` -- keep console output for launchd to capture stderr for error logs).
- For `config/settings.py`: swap `logging.FileHandler(self.logging.file_path)` with `logging.handlers.RotatingFileHandler(self.logging.file_path, maxBytes=self.logging.max_file_size, backupCount=5)`.
- Call `cleanup_old_snapshots()` near the end of the reflections `main()` function, after all analysis is complete.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `cleanup_old_snapshots()` already has `try/except` with `shutil.rmtree(ignore_errors=True)` -- test that errors during cleanup do not crash reflections

### Empty/Invalid Input Handling
- [ ] Test `cleanup_old_snapshots()` when `logs/sessions/` does not exist (returns 0, no crash)
- [ ] Test `cleanup_old_snapshots()` with empty session directories

### Error State Rendering
- No user-visible output -- this is infrastructure-only

## Test Impact

No existing tests affected -- log handler configuration is not covered by existing unit tests, and `cleanup_old_snapshots` has no existing test coverage. All new tests are additive.

## Rabbit Holes

- Do not refactor all logging into a shared utility module -- each service has slightly different needs (JSON formatting in bridge, plain text in reflections). Keep changes local.
- Do not try to make newsyslog work reliably with launchd -- the FD problem is architectural and `RotatingFileHandler` is the correct solution.
- Do not add log compression or aggregation -- out of scope.

## Risks

### Risk 1: Dual rotation (RotatingFileHandler + newsyslog)
**Impact:** Both mechanisms could try to rotate the same file, causing confusion with backup numbering.
**Mitigation:** newsyslog is a safety net, not the primary mechanism. With `RotatingFileHandler` keeping files under 10MB, newsyslog's 10MB threshold will rarely trigger. The two mechanisms are compatible (newsyslog renames files, RotatingFileHandler checks size before each write).

## Race Conditions

No race conditions identified -- each Python process manages its own log file independently. `RotatingFileHandler` uses internal locking for thread safety within a single process.

## No-Gos (Out of Scope)

- Centralized log aggregation or shipping
- Log compression for rotated backups
- Refactoring all services to share a common logging setup module
- Removing newsyslog config entirely (it serves as a safety net)

## Update System

The update script (`scripts/remote-update.sh`) does not need changes. The newsyslog config change requires re-running `sudo cp config/newsyslog.valor.conf /etc/newsyslog.d/valor.conf` on each machine, but this is already part of the standard setup process and the update skill handles config propagation. No new dependencies or migration steps.

## Agent Integration

No agent integration required -- this is a logging infrastructure fix internal to bridge services and scripts. No MCP server changes, no `.mcp.json` changes, no new tools.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` to reflect that reflections and watchdog now use RotatingFileHandler
- [ ] Update `docs/features/reflections.md` log rotation table to include reflections.log and watchdog.log as Python-rotated

## Success Criteria

- [ ] `config/settings.py:configure_logging()` uses `RotatingFileHandler` with `max_file_size` and `backupCount=5`
- [ ] `scripts/reflections.py` adds `RotatingFileHandler` for `logs/reflections.log` (10MB, 5 backups)
- [ ] `monitoring/bridge_watchdog.py` adds `RotatingFileHandler` for `logs/watchdog.log` (10MB, 5 backups)
- [ ] `cleanup_old_snapshots()` is called during the reflections daily run
- [ ] `config/newsyslog.valor.conf` no longer references `issue_poller_error.log`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (log-rotation)**
  - Name: log-rotation-builder
  - Role: Implement RotatingFileHandler changes across all services and wire cleanup
  - Agent Type: builder
  - Resume: true

- **Validator (log-rotation)**
  - Name: log-rotation-validator
  - Role: Verify all log handlers are RotatingFileHandler, cleanup is wired
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix settings.py FileHandler
- **Task ID**: build-settings-handler
- **Depends On**: none
- **Validates**: tests/unit/test_log_rotation.py (create)
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- In `config/settings.py:configure_logging()`, replace `logging.FileHandler(self.logging.file_path)` with `logging.handlers.RotatingFileHandler(self.logging.file_path, maxBytes=self.logging.max_file_size, backupCount=5)`
- Add `import logging.handlers` if not already present

### 2. Add RotatingFileHandler to reflections
- **Task ID**: build-reflections-handler
- **Depends On**: none
- **Validates**: tests/unit/test_log_rotation.py (create)
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- After the `logging.basicConfig()` call in `scripts/reflections.py`, add a `RotatingFileHandler` for `logs/reflections.log` to the reflections logger
- Use 10MB max, 5 backups, matching bridge pattern

### 3. Add RotatingFileHandler to watchdog
- **Task ID**: build-watchdog-handler
- **Depends On**: none
- **Validates**: tests/unit/test_log_rotation.py (create)
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- After the `logging.basicConfig()` call in `monitoring/bridge_watchdog.py`, add a `RotatingFileHandler` for `logs/watchdog.log`
- Use 10MB max, 5 backups, matching bridge pattern

### 4. Wire cleanup_old_snapshots into reflections
- **Task ID**: build-cleanup-wiring
- **Depends On**: none
- **Validates**: tests/unit/test_log_rotation.py (create)
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Import `cleanup_old_snapshots` from `bridge.session_logs` in `scripts/reflections.py`
- Call `cleanup_old_snapshots()` near the end of `main()`, after analysis is complete
- Log the count of removed session directories

### 5. Clean up stale newsyslog entry
- **Task ID**: build-newsyslog-cleanup
- **Depends On**: none
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove the `issue_poller_error.log` line from `config/newsyslog.valor.conf`

### 6. Validation
- **Task ID**: validate-all
- **Depends On**: build-settings-handler, build-reflections-handler, build-watchdog-handler, build-cleanup-wiring, build-newsyslog-cleanup
- **Assigned To**: log-rotation-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no `logging.FileHandler` remains in `config/settings.py`
- Verify `RotatingFileHandler` in reflections.py and bridge_watchdog.py
- Verify `cleanup_old_snapshots` is called in reflections main()
- Verify newsyslog.valor.conf has no issue_poller references
- Run all tests

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: log-rotation-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` and `docs/features/reflections.md` log rotation tables

### 8. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: log-rotation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No plain FileHandler in settings | `grep -c 'logging.FileHandler' config/settings.py` | exit code 1 |
| RotatingFileHandler in settings | `grep -c 'RotatingFileHandler' config/settings.py` | output > 0 |
| RotatingFileHandler in reflections | `grep -c 'RotatingFileHandler' scripts/reflections.py` | output > 0 |
| RotatingFileHandler in watchdog | `grep -c 'RotatingFileHandler' monitoring/bridge_watchdog.py` | output > 0 |
| cleanup_old_snapshots called | `grep -c 'cleanup_old_snapshots' scripts/reflections.py` | output > 0 |
| No issue_poller in newsyslog | `grep -c 'issue_poller' config/newsyslog.valor.conf` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- the scope is well-defined and all assumptions were validated during recon.
