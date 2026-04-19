---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-19
tracking: https://github.com/tomcounsell/ai/issues/1030
last_comment_id: null
---

# Replace Root-Requiring newsyslog with User-Space Log Rotation

## Problem

Log rotation for launchd-managed stderr files relies on macOS's system `newsyslog` daemon,
which requires root to configure. This permanently breaks fully automatic, unattended updates.

**Current behavior:**
- `scripts/update/run.py` calls `newsyslog.check_newsyslog()` which tries `sudo -n tee /etc/newsyslog.d/valor.conf`
- When sudo requires a password (the common case on developer machines), the update prints `ACTION REQUIRED` asking the human to run a manual command
- This has recurred across machines and PRs — the newsyslog config drifts, sudo fails, the human is interrupted
- `worker/__main__.py` uses a plain `logging.FileHandler` with zero rotation, so `worker.log` and `worker_error.log` grow without bound between restarts
- `watchdog.log` is at 9.6 MB, approaching the 10 MB threshold right now

**Desired outcome:**
- Log rotation works without root
- `/update` completes fully automatically with no `ACTION REQUIRED` prompt related to log rotation
- `scripts/update/newsyslog.py` and `config/newsyslog.conf.template` are deleted
- No service log file exceeds 10 MB under normal operation

## Freshness Check

**Baseline commit:** `3ab061dfa3d2a0fca64183518c97511434dede0c`
**Issue filed at:** 2026-04-17T09:56:00Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/update/newsyslog.py` — root-requiring sudo install — still present, unchanged
- `config/newsyslog.conf.template` — system-path template — still present, unchanged
- `scripts/update/run.py:900` — `newsyslog.check_newsyslog()` call — still present
- `worker/__main__.py:66` — plain `logging.FileHandler` (no rotation) — confirmed
- `monitoring/bridge_watchdog.py:52` — `RotatingFileHandler` already in use — confirmed
- `scripts/valor-service.sh:152-178` — `rotate_log()` startup-only rotation — confirmed

**Cited sibling issues/PRs re-checked:**
- #610 — closed 2026-03-31, triggered hybrid newsyslog+Python approach in PR #618
- #755 — closed 2026-04-07, worker log-rotation gaps partially addressed in PR #766
- PR #579 — merged 2026-03-27, added `RotatingFileHandler` to bridge only
- PR #618 — merged 2026-03-31, added newsyslog as system-wide safety net (introduced the root dependency)
- PR #766 — merged 2026-04-07, added `rotate_log()` calls to worker start/restart

**Commits on main since issue was filed (touching referenced files):**
- `b7e1a1db` refactor: split agent_session_queue.py — irrelevant, no log-rotation files touched

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** All references still accurate. The watchdog log size cited in the issue (9.6 MB) is confirmed;
`watchdog.log` now measures 9,363,193 bytes against the 10 MB limit. This is live.

## Prior Art

- **Issue #610 / PR #579** — "Log rotation not working" — Added `RotatingFileHandler` to bridge only. Did not cover launchd `StandardErrorPath` FD-hold problem; files opened directly by launchd are not the same FD as the Python logger.
- **PR #618** — "Fix log rotation across all services" — Added newsyslog as system-wide safety net; also added `RotatingFileHandler` to reflections and watchdog. Introduced the root dependency. The newsyslog layer was added because `RotatingFileHandler` cannot rotate files whose FDs are held by launchd (via `StandardErrorPath`).
- **Issue #755 / PR #766** — "Worker service gaps" — Added startup `rotate_log()` calls to worker start/restart paths. Still leaves the between-restart window uncovered for long-running services.

## Research

No relevant external findings — the solution space is fully covered by codebase context and the issue's own recon. The three candidate approaches (User LaunchAgent, watchdog extension, startup-only) are all user-space and well-understood. No external libraries or APIs are involved.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #579 | Added `RotatingFileHandler` to bridge | Only covers FDs the Python process opens; launchd `StandardErrorPath` FDs are held by launchd itself, so the Python handler never rotates them |
| PR #618 | Added newsyslog as system-wide rotator | newsyslog can rename the file (N flag = no signal), but launchd continues writing to the old inode — so the approach works only when services restart. More critically, installing `/etc/newsyslog.d/valor.conf` requires root, breaking unattended updates |
| PR #766 | Added `rotate_log()` calls to worker start/restart | Correct approach; only runs at service restart, leaving long-running services uncovered |

**Root cause pattern:** Every fix correctly identified that `RotatingFileHandler` is insufficient for `StandardErrorPath` files, but reached for system-level tools (newsyslog) rather than user-space solutions. The `rotate_log()` function in `valor-service.sh` already implements the correct pattern at zero privilege — it just doesn't run continuously.

## Architectural Impact

- **Removed**: `scripts/update/newsyslog.py`, `config/newsyslog.conf.template` — eliminates the only root-requiring step in the update pipeline
- **Modified**: `scripts/update/run.py` — remove the `newsyslog` import and the `check_newsyslog()` call block
- **Modified**: `worker/__main__.py` — upgrade plain `FileHandler` to `RotatingFileHandler`
- **New**: `scripts/log_rotate.py` — a standalone Python script callable on a schedule
- **New**: `com.valor.log-rotate.plist` — a LaunchAgent installed to `~/Library/LaunchAgents/`, no root needed
- **Modified**: `scripts/update/service.py` or `run.py` — install/reload the log-rotate LaunchAgent during `--full` update
- **Reversibility**: High — deleting the plist and reverting the import is a one-line change each

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All tools are stdlib Python and standard macOS launchd.

## Solution

### Key Elements

- **`scripts/log_rotate.py`**: A Python script that checks all six log files against the 10 MB threshold and performs `mv + touch` rotation (same algorithm as `rotate_log()` in `valor-service.sh`). Runs as a standalone script; no imports beyond stdlib `pathlib` and `os`.
- **`com.valor.log-rotate.plist`**: A LaunchAgent plist template that runs `log_rotate.py` every 30 minutes via `StartInterval`. Installed under `~/Library/LaunchAgents/` — zero root needed.
- **`scripts/update/run.py`**: Remove the `newsyslog` import and the `check_newsyslog()` call + branch. Replace with a `service.install_log_rotate_agent()` call (idempotent).
- **`scripts/update/service.py`**: Add `install_log_rotate_agent()` that installs/reloads the log-rotate LaunchAgent via `launchctl bootstrap` or `kickstart`.
- **`worker/__main__.py`**: Replace `logging.FileHandler` with `logging.handlers.RotatingFileHandler` at 10 MB / 5 backups.

### Flow

`/update --full` runs → `service.install_log_rotate_agent()` installs `com.valor.log-rotate.plist` → launchd schedules `scripts/log_rotate.py` every 30 min → script checks all six log files → rotates any that exceed 10 MB → no root needed, no action required from human

### Technical Approach

- **Log-rotate script** uses the same `mv log → log.1 → log.2 → … → log.N` algorithm already in `valor-service.sh:rotate_log()`. Keep count at 5 backups, threshold at 10 MB. No signal to services needed (launchd continues writing to old inode after rotation; the file accumulates at most one 10 MB block between 30-minute rotation checks).
- **LaunchAgent plist** uses `StartInterval 1800` (30 minutes). `WorkingDirectory` set to `__PROJECT_DIR__`. `StandardOutPath`/`StandardErrorPath` write to `logs/log_rotate.log` and `logs/log_rotate_error.log` (small files, rotated by the script itself on the next run).
- **Install path** mirrors the existing `install_worker()` pattern in `scripts/update/service.py`: read plist template, substitute `__PROJECT_DIR__` and `__HOME_DIR__`, write to `~/Library/LaunchAgents/com.valor.log-rotate.plist`, `launchctl bootstrap gui/$UID` if not loaded, `kickstart -k` if already loaded.
- **Idempotency**: compare rendered plist content to installed content before bootstrapping, just like `install_worker()`.
- **worker/__main__.py**: `logging.FileHandler(str(log_file))` → `logging.handlers.RotatingFileHandler(str(log_file), maxBytes=10*1024*1024, backupCount=5)`. This covers files the worker Python process opens itself. The `worker_error.log` (written by launchd via `StandardErrorPath`) is covered by the new LaunchAgent.
- **Cleanup**: delete `scripts/update/newsyslog.py`, `config/newsyslog.conf.template`, and `tests/unit/test_update_newsyslog.py`. Remove the `newsyslog` import and call site from `scripts/update/run.py`. Remove the import from `scripts/update/__init__.py` if present.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `scripts/log_rotate.py` must catch `OSError` from `stat`/`mv`/`touch` calls and log to stderr without crashing — the script must exit 0 even if one file fails so the LaunchAgent doesn't thrash
- [ ] `service.install_log_rotate_agent()` must handle `launchctl` failures gracefully (non-zero exit, missing binary) and surface a warning to the update run log rather than raising

### Empty/Invalid Input Handling
- [ ] `log_rotate.py` must handle the case where a log file doesn't exist yet (new install, service not yet started) — skip without error
- [ ] `log_rotate.py` must handle a log file that is a symlink — follow or skip, document the behavior

### Error State Rendering
- [ ] If `install_log_rotate_agent()` fails, the update run must still succeed (the agent is a safety net, not a hard dependency); failure is a warning, not a fatal error

## Test Impact

- [ ] `tests/unit/test_update_newsyslog.py` — DELETE: tests a module being removed entirely
- [ ] `tests/unit/test_update_run.py` (if it exists) — UPDATE: remove assertions that reference newsyslog or `ACTION REQUIRED` log-rotation prompts

No other existing tests are expected to be affected. The `worker/__main__.py` change is purely internal to the logging setup and is not currently covered by any unit test (the plain `FileHandler` has no test).

## Rabbit Holes

- **Removing `StandardErrorPath` from plists entirely** — wrapping stderr through a Python process adds complexity and a new failure mode. The 30-minute LaunchAgent is simpler and sufficient.
- **Consolidating `rotate_log()` in `valor-service.sh` with the new Python script** — the shell function runs at service start; the Python script runs on a schedule. They serve different windows. Deduplication is cosmetically nice but not worth the risk of breaking the shell-based rotation.
- **Adding log-size alerting to the watchdog** — the watchdog already has a 60-second heartbeat. Adding size checks there would work, but coupling log rotation to the watchdog creates a single point of failure. A separate LaunchAgent is cleaner.
- **Making the Python logger intercept launchd's stderr FDs** — not possible without changing the plist to pipe through a wrapper process (the "remove StandardErrorPath" approach). Higher complexity, out of appetite.

## Risks

### Risk 1: launchd bootstrap fails silently on some machines
**Impact:** The log-rotate LaunchAgent is not installed; files grow unbounded again on that machine.
**Mitigation:** `install_log_rotate_agent()` checks `launchctl list` after bootstrap to confirm the service appears. If absent, log a warning to the update run output. The startup `rotate_log()` calls in `valor-service.sh` remain as a fallback.

### Risk 2: 30-minute interval too infrequent for burst log growth
**Impact:** A single high-traffic burst could generate >10 MB between rotation windows.
**Mitigation:** The startup `rotate_log()` in `valor-service.sh` provides an extra rotation pass on every service restart. Together they cover both scheduled and event-driven rotation. The 10 MB threshold plus 30-minute interval allows ~20 KB/min sustained write rate before overflow — well above typical operation.

### Risk 3: `worker_error.log` grows between LaunchAgent installs on fresh machines
**Impact:** On a machine that hasn't run `/update --full` yet, the log-rotate agent isn't installed.
**Mitigation:** The same `rotate_log()` startup calls in `valor-service.sh` already handle this window. The new worker `RotatingFileHandler` covers `worker.log` (the Python-opened file) from first launch.

## Race Conditions

No race conditions identified. `scripts/log_rotate.py` is a single-threaded script invoked by a scheduled LaunchAgent. The only shared resource is the log files, which are written by separate processes. The `mv + touch` rotation is not atomic, but the worst case is a few lines of log lost during rotation — acceptable for log files.

## No-Gos (Out of Scope)

- Changing `StandardErrorPath` plist key or wrapping stderr through a Python process
- Consolidating the shell `rotate_log()` function into Python
- Adding per-service log-size metrics to the dashboard
- Rotating logs from other services (email bridge, issue poller) — those are already small
- Cross-platform support (Linux) — this is a macOS-only codebase

## Update System

The update system is the primary consumer of this change:

- `scripts/update/run.py` — remove `newsyslog` import and `check_newsyslog()` call; add `service.install_log_rotate_agent()` call in the `--full` branch
- `scripts/update/service.py` — add `install_log_rotate_agent()` function
- `com.valor.log-rotate.plist` — new file committed to repo root (parallel to `com.valor.worker.plist`); substituted and installed by `install_log_rotate_agent()`
- No migration step needed: the LaunchAgent installs idempotently on the next `--full` run; the old `/etc/newsyslog.d/valor.conf` file (if present from a previous install) can be left in place — it will simply stop being maintained and will eventually be irrelevant. No manual removal needed.

## Agent Integration

No agent integration required — this is a pure infrastructure/scripts change. No new Python tools in `tools/`, no MCP server changes, no bridge changes.

## Documentation

- [ ] Create `docs/features/log-rotation.md` describing the new architecture (LaunchAgent, 30-min schedule, relationship to startup `rotate_log()`, why newsyslog was removed)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline comments in `scripts/update/run.py` at the removed newsyslog call site to note the replacement

## Success Criteria

- [ ] `/update --full` runs to completion with no `ACTION REQUIRED` prompt on a machine where sudo requires a password
- [ ] `scripts/update/newsyslog.py` and `config/newsyslog.conf.template` are deleted
- [ ] `tests/unit/test_update_newsyslog.py` is deleted
- [ ] `com.valor.log-rotate.plist` is installed under `~/Library/LaunchAgents/` after `/update --full`
- [ ] `launchctl list | grep log-rotate` shows the service is loaded after install
- [ ] `scripts/log_rotate.py` correctly rotates a log file >10 MB and leaves a `.1` backup
- [ ] `worker/__main__.py` uses `RotatingFileHandler` instead of plain `FileHandler`
- [ ] No service log file exceeds 10 MB within 30 minutes of a rotation-triggering event in automated tests
- [ ] Tests pass (`pytest tests/ -x -q`)

## Team Orchestration

### Team Members

- **Builder (log-rotation)**
  - Name: log-rotation-builder
  - Role: Implement all changes: new plist, new script, update service.py and run.py, upgrade worker FileHandler, delete newsyslog artifacts
  - Agent Type: builder
  - Resume: true

- **Validator (log-rotation)**
  - Name: log-rotation-validator
  - Role: Verify deletion of newsyslog artifacts, confirm plist installs correctly, run tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Write `docs/features/log-rotation.md` and update the features index
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Delete newsyslog artifacts and remove call sites
- **Task ID**: build-remove-newsyslog
- **Depends On**: none
- **Validates**: `tests/unit/test_update_newsyslog.py` deleted, `scripts/update/newsyslog.py` deleted
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `scripts/update/newsyslog.py`
- Delete `config/newsyslog.conf.template`
- Delete `tests/unit/test_update_newsyslog.py`
- Remove `newsyslog` from the import block in `scripts/update/run.py`
- Remove the `newsyslog.check_newsyslog()` call and its `if ns_status.installed / elif ns_status.needs_sudo` branch from `scripts/update/run.py`

### 2. Upgrade worker FileHandler to RotatingFileHandler
- **Task ID**: build-worker-handler
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_*.py` passes (or create new)
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- In `worker/__main__.py::_configure_logging()`, replace `logging.FileHandler(str(log_file))` with `logging.handlers.RotatingFileHandler(str(log_file), maxBytes=10*1024*1024, backupCount=5)`
- Add `import logging.handlers` to the imports block

### 3. Implement log_rotate.py and LaunchAgent plist
- **Task ID**: build-log-rotate-agent
- **Depends On**: none
- **Validates**: `scripts/log_rotate.py` rotates >10 MB file correctly in unit test
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/log_rotate.py`: stdlib-only script that iterates `logs/*.log`, checks size, runs `mv + shift + touch` rotation (5 backups, 10 MB threshold), logs actions to stdout, swallows `OSError` per file with a warning
- Create `com.valor.log-rotate.plist` template: `StartInterval 1800`, `WorkingDirectory __PROJECT_DIR__`, runs `.venv/bin/python scripts/log_rotate.py`, `StandardOutPath` and `StandardErrorPath` to `logs/log_rotate.log` and `logs/log_rotate_error.log`

### 4. Add install_log_rotate_agent() to service.py and wire into run.py
- **Task ID**: build-install-agent
- **Depends On**: build-log-rotate-agent
- **Validates**: `scripts/update/run.py` no longer imports newsyslog; install function present
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `install_log_rotate_agent(project_dir: Path) -> bool` to `scripts/update/service.py` following the same pattern as `install_worker()`: substitute `__PROJECT_DIR__` / `__HOME_DIR__`, write to `~/Library/LaunchAgents/com.valor.log-rotate.plist`, `launchctl bootstrap` or `kickstart -k` if already loaded
- Wire the call into `scripts/update/run.py` in the `--full` branch, after the worker install block

### 5. Validate all acceptance criteria
- **Task ID**: validate-all
- **Depends On**: build-remove-newsyslog, build-worker-handler, build-log-rotate-agent, build-install-agent
- **Assigned To**: log-rotation-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `scripts/update/newsyslog.py`, `config/newsyslog.conf.template`, `tests/unit/test_update_newsyslog.py` are gone
- Confirm `worker/__main__.py` uses `RotatingFileHandler`
- Confirm `scripts/log_rotate.py` exists and passes unit test
- Confirm `com.valor.log-rotate.plist` template exists
- Run `pytest tests/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/log-rotation.md` covering the LaunchAgent approach, rotation parameters, relationship to startup `rotate_log()`, and why newsyslog was removed
- Add entry to `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| newsyslog.py deleted | `test ! -f scripts/update/newsyslog.py` | exit code 0 |
| newsyslog template deleted | `test ! -f config/newsyslog.conf.template` | exit code 0 |
| newsyslog test deleted | `test ! -f tests/unit/test_update_newsyslog.py` | exit code 0 |
| Worker uses RotatingFileHandler | `grep -n "RotatingFileHandler" worker/__main__.py` | output > 0 |
| No newsyslog import in run.py | `python -c "import ast, sys; src=open('scripts/update/run.py').read(); tree=ast.parse(src); names=[alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names]; sys.exit(0 if 'newsyslog' not in names else 1)"` | exit code 0 |
| log_rotate.py exists | `test -f scripts/log_rotate.py` | exit code 0 |
| LaunchAgent plist exists | `test -f com.valor.log-rotate.plist` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique runs. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the scope is fully determined by the issue. The issue itself answers the three open questions it posed:

1. **Consolidate rotation layers?** Keep defense-in-depth: startup `rotate_log()` + 30-minute LaunchAgent. The two layers serve different windows (event-driven at restart vs. scheduled for long-running services).
2. **Upgrade `worker/__main__.py` FileHandler?** Yes, in scope — the issue's acceptance criteria explicitly requires it.
3. **Signal handling after rotation?** Not needed — the LaunchAgent uses the same no-signal approach as the old newsyslog `N` flag. launchd continues writing to the old inode; the rotated file accumulates at most 30 minutes of new data at the old inode before being replaced.
