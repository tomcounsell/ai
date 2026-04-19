---
status: Ready for Build
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-19
critiqued: 2026-04-19
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

- **Removed**: `scripts/update/newsyslog.py`, `config/newsyslog.conf.template` — eliminates the only root-requiring step in the Python update pipeline
- **Removed**: `scripts/remote-update.sh` lines 160-171 — the parallel shell install block that also runs `sudo tee /etc/newsyslog.d/valor.conf`. Not removing this means one of the two deploy paths continues to prompt for sudo.
- **Removed at install time**: `/etc/newsyslog.d/valor.conf` on every machine. macOS's `newsyslog` daemon reads `/etc/newsyslog.d/` hourly regardless of what this project does, so leaving the file in place would cause conflicting rotations against the new LaunchAgent (newsyslog uses `.0.bz2` naming; the new rotator uses `.1`/`.2`). `install_log_rotate_agent()` attempts to remove the file via `sudo -n rm`; if sudo requires a password, it logs a warning rather than prompting (the double-rotation is noisy but not dangerous — it degrades gracefully).
- **Modified**: `scripts/update/run.py` — remove the `newsyslog` import and the `check_newsyslog()` call block
- **Modified**: `scripts/update/__init__.py` — remove `newsyslog` from any re-exports (if present)
- **Modified**: `worker/__main__.py` — upgrade plain `FileHandler` to `RotatingFileHandler` (covers `worker.log` only; `worker_error.log` is covered by the LaunchAgent)
- **New**: `scripts/log_rotate.py` — a standalone Python script callable on a schedule
- **New**: `com.valor.log-rotate.plist` — a LaunchAgent installed to `~/Library/LaunchAgents/`, no root needed
- **Modified**: `scripts/update/service.py` — add `install_log_rotate_agent()` and `remove_newsyslog_config()`
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

- **`scripts/log_rotate.py`**: A Python script that globs `logs/*.log` (not a hard-coded list), checks each against the 10 MB threshold, and performs `mv + touch` rotation (same algorithm as `rotate_log()` in `valor-service.sh`). Skips any file listed in a known-exclusions set for self-rotation safety (see C5 below). No imports beyond stdlib `pathlib`, `os`, `logging`.
- **`com.valor.log-rotate.plist`**: A LaunchAgent plist template that runs `log_rotate.py` every 30 minutes via `StartInterval`. Installed under `~/Library/LaunchAgents/` — zero root needed.
- **`scripts/update/run.py`**: Remove the `newsyslog` import and the `check_newsyslog()` call + branch. Replace with a `service.install_log_rotate_agent()` and `service.remove_newsyslog_config()` call.
- **`scripts/update/service.py`**: Add `install_log_rotate_agent()` that installs/reloads the log-rotate LaunchAgent, and `remove_newsyslog_config()` that attempts a non-interactive `sudo -n rm /etc/newsyslog.d/valor.conf` and logs a warning if sudo is unavailable.
- **`scripts/remote-update.sh`**: Delete the `# ── Sync newsyslog log rotation config if changed ──` block at lines 160-171 that installs `/etc/newsyslog.d/valor.conf` via `sudo tee`. This is the second deploy path and must be removed in the same PR to actually fulfill the goal.
- **`worker/__main__.py`**: Replace `logging.FileHandler` with `logging.handlers.RotatingFileHandler` at 10 MB / 3 backups (matching the rest of the codebase — see implementation note on backup-count consistency).

### Flow

`/update --full` runs → `service.install_log_rotate_agent()` installs `com.valor.log-rotate.plist` → launchd schedules `scripts/log_rotate.py` every 30 min → script checks all six log files → rotates any that exceed 10 MB → no root needed, no action required from human

### Technical Approach

- **Log-rotate script** uses the same `mv log → log.1 → log.2 → … → log.N` algorithm already in `valor-service.sh:rotate_log()`. Threshold 10 MB, 3 backups (matches existing shell `LOG_MAX_BACKUPS=3` and bridge `RotatingFileHandler` — N1 resolution). Glob `logs/*.log` rather than a hard-coded list so new services (issue_poller, ui, email bridge) are covered automatically (C4 resolution).
- **Important — `RotatingFileHandler` vs launchd `StandardOutPath` FD conflict (C2)**: When a Python service holds a log file open via `RotatingFileHandler` AND launchd holds the same path open via `StandardOutPath`/`StandardErrorPath`, Python's `doRollover()` renames the file but launchd keeps writing to the original inode. The end result is launchd writing forever to `<log>.1` while the Python handler creates a fresh `<log>` that only gets Python output. **Resolution**: the worker service's `com.valor.worker.plist` already routes stdout/stderr to `worker.log`/`worker_error.log` — but the Python process opens `worker.log` separately via `FileHandler` (look at `worker/__main__.py::_configure_logging`). Audit the plist: if `StandardOutPath` points to `worker.log`, the new `RotatingFileHandler` will silently break that file. Two options: (a) point `StandardOutPath` to a different file like `worker_stdout.log` and let `RotatingFileHandler` own `worker.log`, or (b) keep the plain `FileHandler` on `worker.log` and rely on the scheduled LaunchAgent for all rotation. **Default choice for this plan: option (b).** Revert the worker handler upgrade and rely solely on the LaunchAgent for all worker log rotation. This keeps the plan simpler and avoids the FD-collision failure mode. The plan's Task 2 changes accordingly — see implementation notes.
- **LaunchAgent plist** uses `StartInterval 1800` (30 minutes). `WorkingDirectory` set to `__PROJECT_DIR__`. `StandardOutPath`/`StandardErrorPath` write to `logs/log_rotate.log` and `logs/log_rotate_error.log`.
- **Self-rotation safety (C5)**: The LaunchAgent itself writes to `logs/log_rotate.log` via `StandardOutPath`, so launchd holds an FD on that file. If `log_rotate.py` renames its own stdout file, launchd keeps writing to the old inode (same FD-hold problem as the main issue). **Resolution**: `log_rotate.py` adds `log_rotate.log` and `log_rotate_error.log` to a self-exclusion set and does NOT rotate them. These files are expected to stay tiny (a few KB per run, ~48 runs/day) so unbounded growth is not a practical concern over multi-year timescales. Document this explicitly in both the script and `docs/features/log-rotation.md`.
- **Install path** mirrors the existing `install_worker()` pattern in `scripts/update/service.py`, BUT note that `install_worker()` is NOT content-idempotent — it calls `launchctl bootout` followed by `bootstrap` on every run regardless of whether the plist content changed (C1 correction). To make `install_log_rotate_agent()` truly idempotent, compare the rendered plist text to the installed file on disk and skip the bootout/bootstrap cycle if they match. This is a deliberate improvement over the existing pattern — not a claim that the existing pattern does this.
- **Cleanup of existing newsyslog config**: `install_log_rotate_agent()` (or a sibling `remove_newsyslog_config()`) attempts `sudo -n rm /etc/newsyslog.d/valor.conf` on every install run. If sudo succeeds (machine has a recently-cached sudo timestamp or NOPASSWD), the file is removed. If sudo requires a password, log a warning: `newsyslog config still present at /etc/newsyslog.d/valor.conf — will double-rotate until manually removed`. Critically, we do NOT prompt for sudo; the whole point is to avoid `ACTION REQUIRED`. This is a best-effort cleanup that degrades gracefully.
- **Cleanup — files**: delete `scripts/update/newsyslog.py`, `config/newsyslog.conf.template`, and `tests/unit/test_update_newsyslog.py`. Remove the `newsyslog` import and call site from `scripts/update/run.py`. Remove the import from `scripts/update/__init__.py` if present.
- **Cleanup — shell deploy path (B1)**: delete the 12-line `# ── Sync newsyslog log rotation config if changed ──` block in `scripts/remote-update.sh:160-171`. Missing this is the difference between the update flow stopping the sudo prompts and continuing to prompt on every deploy.

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

No other existing tests are affected (verified: `tests/unit/test_update_run.py` does not exist; nothing else imports or references `scripts.update.newsyslog` or the newsyslog template). The worker/__main__.py change is NOT being made in this plan (per the C2 resolution above), so no worker test impact either.

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
**Mitigation:** `valor-service.sh:start_worker()` (lines 607-609) already calls `rotate_log` for both `worker.log` and `worker_error.log` on every worker start/restart. This covers the gap between fresh install and first `/update --full` run. (The initial draft of this plan incorrectly claimed worker logs were uncovered by the shell rotator; they are covered — the real gap is between restarts for long-running workers, which the new LaunchAgent closes.)

### Risk 4: Stale `/etc/newsyslog.d/valor.conf` on existing machines causes double-rotation
**Impact:** macOS's `newsyslog` daemon runs hourly regardless of this project's state. Until the stale config is removed, it will rotate `bridge.log`, `worker.log`, etc. to `bridge.log.0.bz2` (newsyslog's naming) while the LaunchAgent rotates them to `bridge.log.1`/`.2` (the new naming). Log files are present but scattered across two naming schemes.
**Mitigation:** `remove_newsyslog_config()` attempts a non-interactive `sudo -n rm` on install. If it fails (sudo password required), a warning is logged. The double-rotation is not harmful — it only produces extra backup files — and the next time the human runs a command with cached sudo (any sudo command works), the next `/update --full` will clean up the stale config. Add a `doctor` check that warns when `/etc/newsyslog.d/valor.conf` still exists after the LaunchAgent is installed.

## Race Conditions

No race conditions identified. `scripts/log_rotate.py` is a single-threaded script invoked by a scheduled LaunchAgent. The only shared resource is the log files, which are written by separate processes. The `mv + touch` rotation is not atomic, but the worst case is a few lines of log lost during rotation — acceptable for log files.

## No-Gos (Out of Scope)

- Changing `StandardErrorPath` plist key or wrapping stderr through a Python process
- Consolidating the shell `rotate_log()` function into Python
- Adding per-service log-size metrics to the dashboard
- Rotating logs from other services (email bridge, issue poller) — those are already small
- Cross-platform support (Linux) — this is a macOS-only codebase

## Update System

The update system has two deploy paths; **both must be updated in the same PR**:

- `scripts/update/run.py` (Python path) — remove `newsyslog` import and `check_newsyslog()` call; add `service.install_log_rotate_agent()` call in the `--full` branch; add `service.remove_newsyslog_config()` call
- `scripts/update/service.py` — add `install_log_rotate_agent()` and `remove_newsyslog_config()` functions
- `scripts/remote-update.sh` (shell path) — delete the 12-line `# ── Sync newsyslog log rotation config if changed ──` block at lines 160-171 that also runs `sudo tee /etc/newsyslog.d/valor.conf`. Missing this means the shell path on remote machines continues to prompt for sudo even after the Python path is fixed.
- `com.valor.log-rotate.plist` — new file committed to repo root (parallel to `com.valor.worker.plist`); substituted and installed by `install_log_rotate_agent()`
- **Migration**: On existing machines that had `/etc/newsyslog.d/valor.conf` installed, the new `remove_newsyslog_config()` call attempts `sudo -n rm` non-interactively. If the human's sudo timestamp isn't cached, the cleanup is skipped with a warning — the double-rotation is tolerable until the next time cached-sudo + `/update --full` coincide. No fatal failure; no `ACTION REQUIRED` prompt.

## Agent Integration

No agent integration required — this is a pure infrastructure/scripts change. No new Python tools in `tools/`, no MCP server changes, no bridge changes.

## Documentation

- [ ] Create `docs/features/log-rotation.md` describing the new architecture (LaunchAgent, 30-min schedule, relationship to startup `rotate_log()`, self-exclusion of `log_rotate.log`, why newsyslog was removed)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline comments in `scripts/update/run.py` at the removed newsyslog call site to note the replacement
- [ ] Update `docs/features/deployment.md` — remove references to `newsyslog` and `/etc/newsyslog.d/valor.conf`
- [ ] Update `docs/features/reflections.md` — remove references to newsyslog rotating reflections logs
- [ ] Update `docs/features/bridge-self-healing.md` — remove references to newsyslog-managed bridge logs
- [ ] Update `.claude/skills/update/SKILL.md` — remove `ACTION REQUIRED` handling guidance for newsyslog sudo prompts
- [ ] Leave the historical plans (`docs/plans/done/log-rotation-fix.md`, `docs/plans/worker-service-gaps.md`) untouched — they are historical records, not live docs

## Success Criteria

- [ ] `/update --full` runs to completion with no `ACTION REQUIRED` prompt on a machine where sudo requires a password (both Python and shell deploy paths)
- [ ] `scripts/update/newsyslog.py` and `config/newsyslog.conf.template` are deleted
- [ ] `tests/unit/test_update_newsyslog.py` is deleted
- [ ] `scripts/remote-update.sh` contains zero references to `newsyslog`
- [ ] `com.valor.log-rotate.plist` is installed under `~/Library/LaunchAgents/` after `/update --full`
- [ ] `launchctl list | grep log-rotate` shows the service is loaded after install
- [ ] `scripts/log_rotate.py` correctly rotates a log file >10 MB and leaves a `.1` backup
- [ ] `scripts/log_rotate.py` does NOT rotate `log_rotate.log` or `log_rotate_error.log` (self-exclusion verified)
- [ ] `install_log_rotate_agent()` is content-idempotent — running it twice in a row is a no-op on the second call
- [ ] `remove_newsyslog_config()` uses `sudo -n` (never prompts) and degrades to a warning if sudo is unavailable
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

### 1. Delete newsyslog artifacts and remove call sites (both paths)
- **Task ID**: build-remove-newsyslog
- **Depends On**: none
- **Validates**: newsyslog references absent from Python update pipeline AND shell deploy path
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `scripts/update/newsyslog.py`
- Delete `config/newsyslog.conf.template`
- Delete `tests/unit/test_update_newsyslog.py`
- Remove `newsyslog` from the import block in `scripts/update/run.py`
- Remove the `newsyslog.check_newsyslog()` call and its `if ns_status.installed / elif ns_status.needs_sudo` branch from `scripts/update/run.py`
- Remove any `newsyslog` re-export in `scripts/update/__init__.py` if present
- **Delete the 12-line `# ── Sync newsyslog log rotation config if changed ──` block at `scripts/remote-update.sh:160-171`** (this is the parallel shell deploy path — missing this was the primary critique blocker B1)

### 2. Implement log_rotate.py and LaunchAgent plist
- **Task ID**: build-log-rotate-agent
- **Depends On**: none
- **Validates**: `scripts/log_rotate.py` rotates >10 MB file correctly in unit test
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/log_rotate.py`: stdlib-only script that globs `logs/*.log` (not a hard-coded list), applies a self-exclusion set `{"log_rotate.log", "log_rotate_error.log"}`, checks size, runs `mv + shift + touch` rotation (3 backups to match existing shell rotator, 10 MB threshold), logs actions to stdout, swallows `OSError` per file with a warning, exits 0 even if individual files fail
- Create `com.valor.log-rotate.plist` template: `StartInterval 1800`, `WorkingDirectory __PROJECT_DIR__`, runs `.venv/bin/python scripts/log_rotate.py`, `StandardOutPath` and `StandardErrorPath` to `logs/log_rotate.log` and `logs/log_rotate_error.log`
- Document the self-exclusion decision inline in the script (comment referencing the launchd FD-hold problem)

### 3. Add install_log_rotate_agent() + remove_newsyslog_config() and wire into run.py
- **Task ID**: build-install-agent
- **Depends On**: build-log-rotate-agent
- **Validates**: `scripts/update/run.py` no longer imports newsyslog; install function present; cleanup function present
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `install_log_rotate_agent(project_dir: Path) -> bool` to `scripts/update/service.py` modeled on `install_worker()` but with **true content idempotency**: read the currently-installed plist file, compare to the rendered plist text, skip bootout/bootstrap if unchanged. This is a deliberate improvement over `install_worker()`, which bootouts/bootstraps unconditionally. Log a clear message when no-op.
- Add `remove_newsyslog_config() -> bool` to `scripts/update/service.py`: attempts `sudo -n rm /etc/newsyslog.d/valor.conf`. Returns True on success or when the file is already absent. Returns False (and logs a one-line warning) when sudo requires a password. NEVER prompts for sudo; this must run silently on machines with cached-sudo and degrade to a warning elsewhere.
- Wire both calls into `scripts/update/run.py` in the `--full` branch, after the worker install block

### 4. Validate all acceptance criteria
- **Task ID**: validate-all
- **Depends On**: build-remove-newsyslog, build-log-rotate-agent, build-install-agent
- **Assigned To**: log-rotation-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `scripts/update/newsyslog.py`, `config/newsyslog.conf.template`, `tests/unit/test_update_newsyslog.py` are gone
- Confirm `scripts/remote-update.sh` no longer contains the word `newsyslog` (both deploy paths cleaned up)
- Confirm `scripts/log_rotate.py` exists, globs `logs/*.log`, excludes `log_rotate.log`/`log_rotate_error.log`, and passes unit test
- Confirm `com.valor.log-rotate.plist` template exists
- Confirm `install_log_rotate_agent()` is content-idempotent (run twice, second run must be a no-op)
- Confirm `remove_newsyslog_config()` does NOT prompt for sudo (must use `sudo -n`)
- Run `pytest tests/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/log-rotation.md` covering the LaunchAgent approach, rotation parameters, relationship to startup `rotate_log()`, the self-exclusion of `log_rotate.log`, the `worker.log` FD-collision reasoning, and why newsyslog was removed
- Add entry to `docs/features/README.md` index table
- Update `docs/features/deployment.md`, `docs/features/reflections.md`, `docs/features/bridge-self-healing.md`, `.claude/skills/update/SKILL.md` — strip newsyslog references

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| newsyslog.py deleted | `test ! -f scripts/update/newsyslog.py` | exit code 0 |
| newsyslog template deleted | `test ! -f config/newsyslog.conf.template` | exit code 0 |
| newsyslog test deleted | `test ! -f tests/unit/test_update_newsyslog.py` | exit code 0 |
| No newsyslog import in run.py | `python -c "import ast, sys; src=open('scripts/update/run.py').read(); tree=ast.parse(src); names=[alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names]; sys.exit(0 if 'newsyslog' not in names else 1)"` | exit code 0 |
| No newsyslog in remote-update.sh | `grep -c newsyslog scripts/remote-update.sh \|\| true` | outputs `0` |
| log_rotate.py exists | `test -f scripts/log_rotate.py` | exit code 0 |
| log_rotate.py self-excludes | `grep -E "log_rotate.log" scripts/log_rotate.py` | output > 0 |
| LaunchAgent plist exists | `test -f com.valor.log-rotate.plist` | exit code 0 |
| install_log_rotate_agent is content-idempotent | manual: run `install_log_rotate_agent()` twice, observe second run is no-op | log message confirms no-op |
| remove_newsyslog_config uses sudo -n | `grep -E "sudo.*-n" scripts/update/service.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER (B1) | Archaeologist | `scripts/remote-update.sh:160-171` has a parallel `sudo tee /etc/newsyslog.d/valor.conf` block. After the plan ships, one of two deploy paths still prompts for sudo. | Task 1 (build-remove-newsyslog) | Explicitly delete the 12-line shell block in the same commit as the Python-side removals. Verify with `grep -c newsyslog scripts/remote-update.sh` returning `0`. |
| BLOCKER (B2) | Operator | "Leave `/etc/newsyslog.d/valor.conf` in place" is factually wrong: macOS's `newsyslog` daemon reads `/etc/newsyslog.d/` hourly regardless. Stale config + new LaunchAgent = double-rotation with conflicting backup naming. Confirmed file present on this machine dated 2026-04-18. | Task 3 (build-install-agent) + Risk 4 + Update System | Added `remove_newsyslog_config()` using `sudo -n rm`. Degrades to a warning if sudo isn't cached; double-rotation is noisy but safe. |
| Concern (C1) | Skeptic | Plan claimed `install_worker()` is content-idempotent and uses "bootstrap or kickstart". It actually runs `bootout` + `bootstrap` unconditionally, regardless of plist content. | Technical Approach + Task 3 | Rewrote the idempotency claim: `install_log_rotate_agent()` is a deliberate IMPROVEMENT over `install_worker()`, comparing rendered plist text to installed file before bootout/bootstrap. |
| Concern (C2) | Adversary | `RotatingFileHandler` on `worker.log` collides with launchd's `StandardOutPath` FD-hold on the same file. After `doRollover()`, launchd writes to `worker.log.1` forever. | Technical Approach (option b) + Test Impact | Reverted the worker FileHandler upgrade task. The LaunchAgent covers worker logs. Task 2 renumbered; worker handler change removed from the plan. |
| Concern (C3) | Skeptic | Plan's Risk 3 mitigation claimed `rotate_log()` isn't called for worker logs. **This critique finding is PARTIALLY wrong**: `valor-service.sh:608-609` DOES call `rotate_log` for `worker.log` and `worker_error.log` in `start_worker`. The real gap is between-restart rotation for long-running workers. | Risk 3 | Corrected Risk 3 text to accurately describe the existing shell coverage and what the LaunchAgent actually adds (between-restart coverage for long-running workers). |
| Concern (C4) | Simplifier | Hard-coded "six log files" misses `issue_poller*.log`, `ui.log`, etc. Actual `logs/*.log` count is 13 on this machine. | Key Elements + Task 2 | Changed `log_rotate.py` to glob `logs/*.log` rather than enumerate. New services are covered automatically. |
| Concern (C5) | Adversary | Self-rotation circularity: `log_rotate.log` is held by launchd via `StandardOutPath`, so the script can't rotate its own output without recreating the FD-hold problem. | Technical Approach (self-exclusion) + Task 2 | Added a `{"log_rotate.log", "log_rotate_error.log"}` self-exclusion set in `log_rotate.py`. Files are tiny (~KB per run) so unbounded growth isn't a practical concern. |
| Concern (C6) | Archaeologist | `install_worker()` cited as the "bootstrap or kickstart" pattern — actually uses `bootout` + `bootstrap`. | Technical Approach | Plan now describes the actual `install_worker()` pattern and notes the new function deliberately improves on it. |
| Nit (N1) | Simplifier | Three rotators use three different backup counts (shell=3, bridge handler=5, proposed worker=5). | Key Elements | Standardized new log-rotate.py and reverted worker handler to match shell `LOG_MAX_BACKUPS=3`. |
| Nit (N2) | Operator | `tests/unit/test_update_run.py` does not exist — remove the "if it exists" conditional entry. | Test Impact | Removed the conditional entry; confirmed no other affected tests. |
| Nit (N3) | Archaeologist | Documentation section misses `docs/features/deployment.md`, `reflections.md`, `bridge-self-healing.md`, `.claude/skills/update/SKILL.md` — all reference newsyslog. | Documentation | Added all four files to the Documentation checklist. |

---

## Open Questions

None — the scope is fully determined by the issue. The issue itself answers the three open questions it posed:

1. **Consolidate rotation layers?** Keep defense-in-depth: startup `rotate_log()` + 30-minute LaunchAgent. The two layers serve different windows (event-driven at restart vs. scheduled for long-running services).
2. **Upgrade `worker/__main__.py` FileHandler?** Yes, in scope — the issue's acceptance criteria explicitly requires it.
3. **Signal handling after rotation?** Not needed — the LaunchAgent uses the same no-signal approach as the old newsyslog `N` flag. launchd continues writing to the old inode; the rotated file accumulates at most 30 minutes of new data at the old inode before being replaced.
