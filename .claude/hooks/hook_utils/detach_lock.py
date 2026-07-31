"""Absolute-path logging + concurrency-cap locking for detached Stop extraction.

This module backs the build-stop-detach fix (Phase A of
docs/plans/hook-registration-manifest-dispatcher.md): `stop.py` no longer runs
Haiku/`gh` extraction inline on the 10s Stop-hook wall. Instead it spawns a
real detached subprocess (`stop_detach_worker.py`) and exits immediately.

Everything here uses an ABSOLUTE, home-relative path (`~/.claude/...`) rather
than a repo-relative one. This hook also runs inside foreign repos under the
user/global hook scope, where a repo-relative `logs/` directory does not
exist — a silently-failing log write there would re-swallow the very drops
this fix is meant to surface (tech-debt 2 / Risk 3 log-path robustness).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

# Provisional/tunable — small cap on concurrently in-flight detached
# Stop-extraction workers. Bounds worst-case Haiku/gh fan-out when an SDLC
# batch ends many turns in a short window. Env: HOOK_DETACH_MAX_INFLIGHT.
DEFAULT_MAX_INFLIGHT = 3

# Provisional/tunable — comfortably above observed Haiku extraction + `gh`
# round-trip cost, but small enough that a wedged worker self-terminates
# instead of lingering indefinitely. Env: HOOK_DETACH_DEADLINE_SECONDS.
DEFAULT_DEADLINE_SECONDS = 120


def get_absolute_state_dir() -> Path:
    """Return the absolute, create-on-write dir for detach in-flight lock files.

    Lives under the user's home directory (not the repo) so it resolves the
    same way regardless of which repo's Stop hook is running.
    """
    return Path.home() / ".claude" / "hooks-state" / "stop-detach"


def get_absolute_log_path() -> Path:
    """Return the absolute, create-on-write path for the hooks drop log."""
    return Path.home() / ".claude" / "logs" / "hooks.log"


def log_hook_absolute(hook_name: str, message: str) -> None:
    """Append one line to the absolute hooks log. Never raises.

    Format matches ``log_hook_error`` in ``hook_utils/constants.py`` so
    reflections tooling can parse either line shape, but the path here is
    always absolute -- safe to call from a foreign-repo/user-scope context.
    """
    try:
        log_path = get_absolute_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"{ts} - {hook_name} - ERROR - {message}\n")
    except Exception:
        pass  # Last-resort silence -- logging must never crash a hook


def get_max_inflight() -> int:
    """Read HOOK_DETACH_MAX_INFLIGHT, falling back to the provisional default."""
    try:
        return int(os.environ.get("HOOK_DETACH_MAX_INFLIGHT", DEFAULT_MAX_INFLIGHT))
    except (TypeError, ValueError):
        return DEFAULT_MAX_INFLIGHT


def get_deadline_seconds() -> int:
    """Read HOOK_DETACH_DEADLINE_SECONDS, falling back to the provisional default."""
    try:
        return int(os.environ.get("HOOK_DETACH_DEADLINE_SECONDS", DEFAULT_DEADLINE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_DEADLINE_SECONDS


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check via os.kill(pid, 0)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user -- treat as alive.
        return True
    except OSError:
        return False
    return True


def _reap_stale_slots(state_dir: Path) -> None:
    """Remove slot lock files whose recorded PID is no longer alive.

    Also reaps corrupt/unreadable slot files (empty, non-integer content)
    since those can never be attributed to a live worker.
    """
    try:
        candidates = list(state_dir.glob("slot-*.lock"))
    except OSError:
        return

    for slot_path in candidates:
        try:
            content = slot_path.read_text().strip()
            pid = int(content)
        except (OSError, ValueError):
            # Corrupt/empty lock file -- reap it.
            try:
                slot_path.unlink()
            except OSError:
                pass
            continue

        if not _pid_alive(pid):
            try:
                slot_path.unlink()
            except OSError:
                pass


def try_reserve_detach_slot(max_inflight: int | None = None) -> Path | None:
    """Atomically reserve one of ``max_inflight`` numbered detach slots.

    Uses a fixed set of slot filenames (``slot-0.lock`` .. ``slot-{N-1}.lock``)
    and reserves one via ``os.open(path, O_CREAT | O_EXCL)`` -- an atomic
    create that either succeeds (this caller now owns that slot) or raises
    FileExistsError (someone else holds it). This avoids a TOCTOU race
    between *counting* live slots and *reserving* one: there is no separate
    count-then-write step, reservation IS the atomic operation.

    Stale slots (dead PID, or corrupt content) are reaped before attempting
    reservation, so a crashed worker's lock does not permanently burn a slot.

    Returns the reserved slot Path, or None if all slots are held by live
    workers (caller should skip spawning and log ``detach-skipped: at
    capacity``).
    """
    if max_inflight is None:
        max_inflight = get_max_inflight()

    state_dir = get_absolute_state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    _reap_stale_slots(state_dir)

    for i in range(max_inflight):
        slot_path = state_dir / f"slot-{i}.lock"
        try:
            fd = os.open(str(slot_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        except OSError:
            return None
        try:
            # Placeholder content until the caller knows the real child PID
            # (stop.py writes the actual worker PID immediately after Popen).
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return slot_path

    return None


def release_detach_slot(slot_path: str | Path | None) -> None:
    """Release a previously-reserved slot. Safe to call multiple times."""
    if not slot_path:
        return
    try:
        Path(slot_path).unlink(missing_ok=True)
    except OSError:
        pass
