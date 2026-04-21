# Log Rotation

User-space log rotation for all files under `logs/`. Replaces the prior
macOS `newsyslog` path that required root to configure.

## Why user-space

macOS's built-in `newsyslog` daemon reads `/etc/newsyslog.d/*.conf` hourly.
Installing a config there requires `sudo`, which breaks fully unattended
`/update` runs — every time the newsyslog config drifted, the update
pipeline would stop at `ACTION REQUIRED: ... Run: sudo cp ...` and wait
for a human to type a password.

A user-space LaunchAgent (`~/Library/LaunchAgents/com.valor.log-rotate.plist`)
needs no root, runs under the current user's launchd session, and handles
every log file the project produces on a 30-minute schedule.

## Architecture

Three rotation layers cover overlapping time windows. Each layer has a
specific reason to exist.

| Layer | Runs | Covers | Defined in |
|-------|------|--------|------------|
| **Python `RotatingFileHandler`** | Every write | App logs the Python process opens itself with a rotating handler (`bridge.log`, `watchdog.log`, `worker_watchdog.log`) | `bridge/telegram_bridge.py`, `monitoring/bridge_watchdog.py`, `monitoring/worker_watchdog.py` |
| **Startup `rotate_log`** | Every service start/restart | launchd-managed stderr files (`*.error.log`) — covers the moment the service restarts (FD closes, rename lands) | `scripts/valor-service.sh:148-179` |
| **Log-rotate LaunchAgent** | Every 30 min | Every `logs/*.log` file — between-restart coverage for long-running services, and the sole rotator for `worker.log` and `reflections.log` (both written via plain file append / launchd `StandardOutPath`, no in-process rotation) | `scripts/log_rotate.py` + `com.valor.log-rotate.plist` |

The LaunchAgent is the new layer. The other two pre-existed and continue
to work unchanged.

### Why the LaunchAgent exists when Python already rotates

`RotatingFileHandler` only rotates file descriptors that the Python process
itself opened. When launchd writes to `StandardOutPath`/`StandardErrorPath`,
it holds the FD open for the lifetime of the service. Even for services
that do install a Python rotating handler (like the bridge), renaming
`bridge.log` to `bridge.log.1` does not affect launchd's FD — launchd
keeps writing to the old inode. And for services that deliberately use a
plain `FileHandler` (worker) or raw file append (`sdlc_reflection.py`),
there is no in-process rotation at all; those files rely entirely on the
LaunchAgent.

The LaunchAgent sidesteps the FD problem the same way the old newsyslog
config did: it renames the file, creates a fresh empty one, and accepts
that launchd continues writing to the old inode until the service next
restarts. At most 30 minutes of new data accumulates at the old inode
before being replaced.

### Why the startup rotation still matters

The 30-minute schedule is fine for the average case, but a burst of output
right after a restart (e.g. a service crash-looping) could cross the 10 MB
threshold in under 30 minutes. The startup `rotate_log()` calls in
`valor-service.sh` catch that case: any oversized file is rotated before
the service starts writing again. The two layers together cover both
event-driven and scheduled rotation with no gap.

## Rotation parameters

All layers use matching settings so behavior is consistent across the
codebase:

- **Threshold**: 10 MB (`LOG_MAX_SIZE = 10 * 1024 * 1024`)
- **Backups retained**: 3 (`LOG_MAX_BACKUPS = 3`)
- **Naming**: `foo.log` → `foo.log.1` → `foo.log.2` → `foo.log.3` → dropped
- **Method**: `mv + touch` (same as `rotate_log()` in `valor-service.sh`);
  no compression, no signal sent to the writer (launchd holds the FD open)

## Self-exclusion

`log_rotate.py` writes its own stdout and stderr via the LaunchAgent's
`StandardOutPath`/`StandardErrorPath` to `logs/log_rotate.log` and
`logs/log_rotate_error.log`. launchd holds FDs on those files, which means
rotating them here would recreate the exact FD-hold problem we are solving
(launchd would keep writing to the old inode while the script created a
fresh file it never writes to).

The script includes a `SELF_EXCLUDED_FILES` set containing both filenames
and skips them explicitly. The files are expected to stay tiny (a few KB
per run at ~48 runs/day), so unbounded growth is not a practical concern
over multi-year timescales.

## Idempotency

`install_log_rotate_agent()` in `scripts/update/service.py` is
**content-idempotent**: it compares the rendered plist against the file
already on disk and skips `launchctl bootout`/`bootstrap` entirely when
they match. Running `/update --full` twice in a row is a no-op the second
time. This is a deliberate improvement over the existing
`install_worker()` pattern, which unconditionally tears down and re-bootstraps
on every run regardless of whether the plist content changed.

## Migration: removing the old newsyslog config

Machines updated before this migration have `/etc/newsyslog.d/valor.conf`
installed. Leaving it in place produces double-rotation with two different
naming schemes (newsyslog uses `.0.bz2`; the new LaunchAgent uses `.1`).

`remove_newsyslog_config()` runs during `/update --full` and attempts
`sudo -n rm /etc/newsyslog.d/valor.conf` — non-interactive, so it never
prompts. When sudo requires a password, the cleanup is skipped with a
warning logged to the update output. The double-rotation is noisy but
safe; the next time cached-sudo coincides with `/update --full`, the
cleanup completes silently.

## Failure modes and recovery

- **LaunchAgent fails to load**: `install_log_rotate_agent()` verifies
  with `launchctl list` after bootstrap and returns False if the service
  isn't present. The update pipeline surfaces that as a warning but does
  not fail outright — the startup `rotate_log()` calls remain as a
  fallback on every service restart.
- **One log file fails to rotate**: `log_rotate.py` swallows per-file
  `OSError` (bad stat, permission denied, rename failure) and continues
  to the next file. The script always exits 0 so launchd does not
  throttle the agent into a 10+ minute penalty window.
- **Unexpected exception in `main()`**: Caught and logged; still exits 0
  for the same reason.
- **30-minute interval too infrequent for burst growth**: The startup
  `rotate_log()` pass on every service restart handles event-driven
  rotation. At the 10 MB threshold, sustaining a write rate above ~20 KB/min
  between rotation windows would be required to overflow — well above
  typical operation.

## Files

| File | Purpose |
|------|---------|
| `scripts/log_rotate.py` | Rotator script invoked by the LaunchAgent |
| `com.valor.log-rotate.plist` | LaunchAgent plist template (installed to `~/Library/LaunchAgents/`) |
| `scripts/update/service.py::install_log_rotate_agent` | Installer called from `/update --full` |
| `scripts/update/service.py::remove_newsyslog_config` | Best-effort cleanup of the stale system config |
| `tests/unit/test_log_rotate.py` | Rotator unit tests (rotation, self-exclusion, shift, failure handling) |
| `tests/unit/test_update_log_rotate_agent.py` | Installer unit tests (idempotency, re-render on drift, sudo -n semantics) |

## See also

- `scripts/valor-service.sh:rotate_log` — startup rotation for `*.error.log` files
- `docs/features/deployment.md` — deployment-level overview of the update pipeline
- `docs/features/reflections.md` — rotation settings for reflection logs
- `docs/features/bridge-self-healing.md` — why log rotation matters for watchdog reliability
