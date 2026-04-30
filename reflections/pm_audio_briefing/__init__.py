"""
reflections/pm_audio_briefing — Daily PM audio briefing reflection.

Per-project fan-out: iterates load_local_projects(), filters by
pm_briefing.enabled and machine ownership, and on each project's local
schedule slot constructs a 30-second voice brief (numbers-free) plus a
written follow-up (with numbers + links).

See docs/features/pm-audio-briefing.md for the full design.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from reflections.pm_audio_briefing import builder, collector, delivery
from reflections.utils import load_local_projects

logger = logging.getLogger("reflections.pm_audio_briefing")

# One-shot startup logging gate (logs project counts on first tick after
# process start, never again).
_startup_logged = False


def _resolve_machine() -> str:
    """Capture this machine's ComputerName (used to filter project ownership).

    Returns empty string on failure -- callers should treat that as "filter
    everything out" since matching against an empty string is dangerous.
    """
    try:
        out = subprocess.check_output(["scutil", "--get", "ComputerName"], text=True, timeout=5)
        return out.strip()
    except Exception as e:
        logger.warning("Could not resolve ComputerName: %s", e)
        return ""


def _slot_match(now_local: datetime, schedule: str) -> bool:
    """Absolute-minute arithmetic slot match.

    Returns True iff `now_local` is within the 5-minute slot window starting
    at `schedule` (HH:MM). Handles hour rollover correctly:
    schedule="00:58" matches now=01:02 because 58 <= 62 < 63.
    """
    try:
        sh, sm = schedule.split(":")
        slot_start_abs = int(sh) * 60 + int(sm)
    except (ValueError, AttributeError):
        return False
    now_abs = now_local.hour * 60 + now_local.minute
    return slot_start_abs <= now_abs < slot_start_abs + 5


def _today_in_project_tz(project: dict) -> tuple[Any, str]:
    """Return (date_obj, isoformat_str) anchored in the project's timezone."""
    tz_name = (project.get("pm_briefing") or {}).get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    today = datetime.fromtimestamp(time.time(), tz=tz).date()
    return today, today.isoformat()


def _now_in_project_tz(project: dict) -> datetime:
    tz_name = (project.get("pm_briefing") or {}).get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz=tz)


def _last_run_date_in_project_tz(reflection, project: dict):
    """Resolve the last-run calendar date in the project's timezone.

    `reflection.ran_at` is a unix-epoch float. The Reflection model has no
    `ran_at_date` shortcut -- we always compute it via
    datetime.fromtimestamp(ran_at, tz=ZoneInfo(...)).date().
    """
    if not reflection or not reflection.ran_at:
        return None
    tz_name = (project.get("pm_briefing") or {}).get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    try:
        return datetime.fromtimestamp(float(reflection.ran_at), tz=tz).date()
    except (TypeError, ValueError):
        return None


def _redis_lock_key(project_key: str, today_iso: str) -> str:
    return f"pm-briefing-lock:{project_key}:{today_iso}"


def _try_acquire_lock(redis_conn, key: str, ttl_s: int = 90000) -> bool:
    """Atomic SETNX with 25h TTL. Returns True if acquired."""
    try:
        return bool(redis_conn.set(key, str(time.time()), nx=True, ex=ttl_s))
    except Exception as e:
        logger.warning("Redis SETNX failed for %s: %s", key, e)
        return False


def _release_lock(redis_conn, key: str) -> None:
    """Best-effort lock release (used only on pre-side-effect failures)."""
    try:
        redis_conn.delete(key)
    except Exception as e:
        logger.debug("Failed to release lock %s: %s", key, e)


def _process_one_project(project: dict, this_machine: str, *, dry_run: bool) -> dict:
    """Run the full per-project pipeline. Returns a status dict.

    The lock-release policy is split:
    - Pre-side-effect failure (collector or builder raises BEFORE the first
      r.rpush): release the lock so the next tick can retry on
      last_status="error".
    - Post-side-effect failure (delivery raises after the first r.rpush):
      DO NOT release the lock. Subsequent ticks see the held lock and skip.
      The next day's lock key is different (different date) so the briefing
      resumes naturally tomorrow.
    """
    from models.reflection import Reflection

    project_key = project.get("slug") or "unknown"
    pm = project.get("pm_briefing") or {}

    # Machine-ownership filter.
    project_machine = (project.get("machine") or "").strip()
    if this_machine and project_machine and project_machine != this_machine:
        return {"status": "skipped", "reason": "wrong_machine"}

    # Schedule-slot filter.
    schedule = pm.get("schedule") or ""
    if not schedule:
        return {"status": "skipped", "reason": "no_schedule"}
    now_local = _now_in_project_tz(project)
    if not _slot_match(now_local, schedule):
        return {"status": "skipped", "reason": "outside_slot"}

    # Idempotency check (cheap, skip-only-on-success).
    today_obj, today_iso = _today_in_project_tz(project)
    reflection_name = f"pm-audio-briefing-{project_key}"
    reflection = Reflection.get_or_create(name=reflection_name)
    last_run_date = _last_run_date_in_project_tz(reflection, project)
    if last_run_date == today_obj and reflection.last_status == "success":
        return {"status": "skipped", "reason": "already_succeeded_today"}

    # Acquire SETNX lock (the within-tick atomic gate).
    try:
        from delivery import _get_redis_connection  # type: ignore

        # The above import is a deliberate no-op except in tests; the real
        # import path is below.
    except Exception:
        pass
    from reflections.pm_audio_briefing.delivery import _get_redis_connection

    redis_conn = _get_redis_connection()
    lock_key = _redis_lock_key(project_key, today_iso)
    if not _try_acquire_lock(redis_conn, lock_key):
        return {"status": "skipped", "reason": "lock_held"}

    started_at = time.time()
    side_effect_started = False
    reflection.mark_started()
    try:
        # --- Pre-side-effect phase ---
        angles = pm.get("angles") or {}
        include = list(angles.get("include") or [])
        exclude = list(angles.get("exclude") or [])
        raw = collector.collect(project, include, exclude)
        transcript, followup = builder.build(
            raw,
            fallback_message=pm.get("fallback_message") or "Nothing shipped yesterday.",
            skip_when_empty=bool(pm.get("skip_when_empty", False)),
            project=project,
        )

        # If skip_when_empty fired and produced an empty transcript, record
        # success-with-noop and release the lock for the day.
        if not transcript:
            reflection.mark_completed(
                duration=time.time() - started_at,
                error=None,
            )
            return {"status": "noop", "reason": "skip_when_empty"}

        # --- Side-effect phase begins inside delivery.send() ---
        side_effect_started = True
        target_groups = list(pm.get("target_groups") or [])
        if not target_groups:
            raise RuntimeError("pm_briefing.target_groups is empty")

        result = delivery.send(
            transcript,
            followup,
            target_groups,
            project,
            voice=pm.get("voice"),
            dry_run=dry_run,
        )
        reflection.mark_completed(duration=time.time() - started_at, error=None)
        # On dry-run, release the lock so re-runs are allowed during testing.
        if dry_run:
            _release_lock(redis_conn, lock_key)
        return {"status": "ok", "delivery": result}
    except Exception as e:
        # Distinguish pre- vs post-side-effect failure.
        err_msg = f"{type(e).__name__}: {e}"
        reflection.mark_completed(duration=time.time() - started_at, error=err_msg)
        if not side_effect_started:
            # Safe to release: nothing was enqueued.
            _release_lock(redis_conn, lock_key)
            return {"status": "error", "phase": "pre_side_effect", "error": err_msg}
        # Side-effects already started -- HOLD the lock to prevent duplicate
        # voice-notes on next tick. Next-day's lock is different.
        return {"status": "error", "phase": "post_side_effect", "error": err_msg}


async def run() -> dict:
    """Entry point invoked by the reflection scheduler.

    Iterates all local projects with pm_briefing configured, runs the
    per-project pipeline in isolation (one project's failure does NOT abort
    others), and returns an aggregate status dict.

    The `interval: 300` registry entry means this is invoked every 5
    minutes. The per-project schedule slot inside the callable is the real
    once-per-day gate.
    """
    global _startup_logged
    dry_run = os.environ.get("DRY_RUN") == "1"

    this_machine = _resolve_machine()
    projects_all = load_local_projects()

    # Filter to projects with pm_briefing.enabled = True.
    eligible = [p for p in projects_all if (p.get("pm_briefing") or {}).get("enabled") is True]
    owned = (
        [p for p in eligible if (p.get("machine") or "").strip() == this_machine]
        if this_machine
        else []
    )

    if not _startup_logged:
        skipped = len(eligible) - len(owned)
        logger.info(
            "pm-audio-briefing: loaded %d projects with pm_briefing.enabled=true on %s "
            "(skipped %d not owned by this machine)",
            len(owned),
            this_machine or "(unknown machine)",
            max(0, skipped),
        )
        _startup_logged = True

    results: dict[str, dict] = {}
    successes = 0
    failures = 0
    for project in owned:
        slug = project.get("slug") or "unknown"
        try:
            res = _process_one_project(project, this_machine, dry_run=dry_run)
        except Exception as e:
            res = {"status": "error", "phase": "outer", "error": str(e)}
        results[slug] = res
        if res.get("status") == "ok" or res.get("status") == "noop":
            successes += 1
        elif res.get("status") == "error":
            failures += 1

    return {
        "status": "ok" if failures == 0 else "partial" if successes else "error",
        "projects": results,
        "summary": {
            "considered": len(owned),
            "succeeded": successes,
            "failed": failures,
        },
    }
