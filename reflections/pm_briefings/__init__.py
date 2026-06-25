"""
reflections/pm_briefings — Slot-driven PM briefings reflection.

One reflection (registered as ``pm-briefings``) owns ALL PM-facing slot-
driven content. Each project declares any number of "briefing slots" in its
``pm_briefing.slots`` config; at each tick the dispatcher fans out
(project x slot), runs the slot-specific ``build()``, and delivers ONE
Telegram message per (project, slot) per day.

Backward compatibility: a project with the legacy
``pm_briefing.angles + pm_briefing.schedule`` shape (single morning brief)
is interpreted as a one-element slot list ``[{name: "morning", type:
"morning", schedule: <existing>, angles: <existing>}]`` -- zero
``projects.json`` edits required for existing morning-brief users.

Slot types and their builders are wired in ``_SLOT_BUILDERS``:
- ``morning`` -> ``morning.build``
- ``daily_log`` -> ``daily_log.build``
- ``log_audit`` -> ``log_audit.build``

Lock-release policy (per the plan's Implementation Notes): the dispatcher
acquires SETNX, dispatches to the pure ``slot.build()``, and ONLY THEN
performs side effects. Slot builders are pure functions returning
``(transcript, followup, raw_signals)``. Pre-side-effect failure releases
the lock; post-side-effect failure HOLDS the lock to prevent duplicates.

See ``docs/features/pm-briefings.md`` for the full design.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from reflections.pm_briefings import daily_log, log_audit, morning
from reflections.utilities import load_local_projects

logger = logging.getLogger("reflections.pm_briefings")

# One-shot startup logging gate (logs project counts on first tick after
# process start, never again).
_startup_logged = False


SlotBuilder = Callable[[dict, dict], tuple[str, str, dict[str, Any]]]

_SLOT_BUILDERS: dict[str, SlotBuilder] = {
    "morning": morning.build,
    "daily_log": daily_log.build,
    "log_audit": log_audit.build,
}


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
    """Absolute-minute arithmetic slot match (5-minute window starting at HH:MM)."""
    try:
        sh, sm = schedule.split(":")
        slot_start_abs = int(sh) * 60 + int(sm)
    except (ValueError, AttributeError):
        return False
    now_abs = now_local.hour * 60 + now_local.minute
    return slot_start_abs <= now_abs < slot_start_abs + 5


def _project_tz(project: dict) -> ZoneInfo:
    tz_name = (project.get("pm_briefing") or {}).get("timezone") or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:  # swallow-ok: bad/missing tz name falls back to UTC
        return ZoneInfo("UTC")


def _today_in_project_tz(project: dict) -> tuple[Any, str]:
    """Return ``(date_obj, isoformat_str)`` anchored in the project's timezone."""
    today = datetime.fromtimestamp(time.time(), tz=_project_tz(project)).date()
    return today, today.isoformat()


def _now_in_project_tz(project: dict) -> datetime:
    return datetime.now(tz=_project_tz(project))


def _redis_lock_key(project_key: str, slot_name: str, today_iso: str) -> str:
    return f"pm-briefings-lock:{project_key}:{slot_name}:{today_iso}"


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


def _resolve_target_groups(project: dict, slot_config: dict) -> list[str]:
    pm = project.get("pm_briefing") or {}
    return list(slot_config.get("target_groups") or pm.get("target_groups") or [])


def _send_text_only(
    redis_conn, project: dict, target_groups: list[str], text: str
) -> dict[str, str]:
    """Enqueue a plain text Telegram payload to each target group.

    Mirrors the text-payload pattern from
    ``reflections.pm_briefings.delivery._text_payload``.
    """
    import json

    from reflections.pm_briefings.delivery import _resolve_chat_id, _text_payload

    session_id = f"pm-briefings-text-{uuid.uuid4().hex[:8]}"
    queue_key = f"telegram:outbox:{session_id}"
    results: dict[str, str] = {}
    for group in target_groups:
        chat_id = _resolve_chat_id(project, group)
        if chat_id is None:
            results[group] = "skipped_no_chat_id"
            continue
        payload = _text_payload(chat_id=chat_id, text=text, session_id=session_id)
        try:
            redis_conn.rpush(queue_key, json.dumps(payload))
            redis_conn.expire(queue_key, 3600)
            results[group] = "enqueued"
        except Exception as e:
            logger.warning("text payload enqueue failed for %s: %s", group, e)
            results[group] = f"error:{type(e).__name__}"
    return results


def _run_slot(
    project: dict,
    slot_config: dict,
    *,
    dry_run: bool,
) -> dict:
    """Run a single (project x slot). Returns a status dict.

    The dispatcher owns ALL side effects (lock acquire, delivery enqueue,
    Reflection record update). The slot's ``build()`` is pure -- it does
    NOT touch Redis, Telegram, or Reflection state.
    """
    from models.reflection import Reflection
    from reflections.pm_briefings.delivery import (
        BriefingTtsFailedError,
        _get_redis_connection,
        send,
    )

    project_key = project.get("slug") or "unknown"
    slot_name = slot_config.get("name") or slot_config.get("type") or "unknown"
    slot_type = slot_config.get("type") or slot_name

    # Schedule-slot filter.
    schedule = slot_config.get("schedule") or ""
    if not schedule:
        return {"status": "skipped", "reason": "no_schedule"}
    now_local = _now_in_project_tz(project)
    if not _slot_match(now_local, schedule):
        return {"status": "skipped", "reason": "outside_slot"}

    today_obj, today_iso = _today_in_project_tz(project)

    # Per-(project x slot) Reflection record (drives dashboard expansion).
    reflection_name = f"pm-briefings-{project_key}-{slot_name}"
    reflection = Reflection.get_or_create(name=reflection_name)

    # Idempotency: if the per-(project x slot) record already succeeded
    # today (in the project's local tz), skip without acquiring the lock.
    if reflection.ran_at:
        try:
            last_local_date = datetime.fromtimestamp(
                float(reflection.ran_at), tz=_project_tz(project)
            ).date()
        except (TypeError, ValueError):
            last_local_date = None
        if last_local_date == today_obj and reflection.last_status == "success":
            return {"status": "skipped", "reason": "already_succeeded_today"}

    # Acquire SETNX lock.
    redis_conn = _get_redis_connection()
    lock_key = _redis_lock_key(project_key, slot_name, today_iso)
    if not _try_acquire_lock(redis_conn, lock_key):
        return {"status": "skipped", "reason": "lock_held"}

    builder_fn = _SLOT_BUILDERS.get(slot_type)
    if builder_fn is None:
        _release_lock(redis_conn, lock_key)
        return {
            "status": "error",
            "phase": "dispatch",
            "error": f"unknown slot type {slot_type!r}",
        }

    started_at = time.time()
    side_effect_started = False
    findings_count = 0
    reflection.mark_started()
    try:
        # --- Pre-side-effect: pure build ---
        transcript, followup, raw_signals = builder_fn(project, slot_config)
        findings_count = len(raw_signals.get("findings", []))

        # Skip-when-empty: a slot whose build returns no transcript and no
        # followup is a noop.
        if not transcript and not followup:
            duration = time.time() - started_at
            reflection.mark_completed(duration=duration, error=None)
            return {
                "status": "noop",
                "reason": "skip_when_empty",
                "slot": slot_name,
                "date_iso": today_iso,
                "duration": duration,
                "findings_count": findings_count,
            }

        # --- Side-effect phase: delivery ---
        target_groups = _resolve_target_groups(project, slot_config)
        if not target_groups:
            raise RuntimeError(
                f"slot {slot_name!r} has no target_groups "
                "(and project.pm_briefing.target_groups is empty)"
            )
        side_effect_started = True

        if transcript:
            # Voice + (optional) follow-up.
            result = send(
                transcript,
                followup,
                target_groups,
                project,
                voice=slot_config.get("voice") or (project.get("pm_briefing") or {}).get("voice"),
                dry_run=dry_run,
            )
        else:
            # Text-only slot (e.g. log_audit). Use the delivery helpers
            # directly to enqueue ONE text payload per target group.
            result = _send_text_only(redis_conn, project, target_groups, followup)

        duration = time.time() - started_at
        reflection.mark_completed(duration=duration, error=None)

        # On dry-run, release the lock so re-runs are allowed during testing.
        if dry_run:
            _release_lock(redis_conn, lock_key)

        return {
            "status": "ok",
            "slot": slot_name,
            "date_iso": today_iso,
            "delivery": result,
            "duration": duration,
            "findings_count": findings_count,
        }
    except BriefingTtsFailedError as e:
        # TTS failure is a "post-side-effect" failure (the failure-notice
        # text payload was already enqueued by delivery.send). HOLD the lock.
        duration = time.time() - started_at
        err_msg = f"BriefingTtsFailedError: {e}"
        reflection.mark_completed(duration=duration, error=err_msg)
        return {
            "status": "error",
            "phase": "post_side_effect",
            "slot": slot_name,
            "date_iso": today_iso,
            "error": err_msg,
            "duration": duration,
            "findings_count": findings_count,
        }
    except Exception as e:
        duration = time.time() - started_at
        err_msg = f"{type(e).__name__}: {e}"
        reflection.mark_completed(duration=duration, error=err_msg)
        if not side_effect_started:
            _release_lock(redis_conn, lock_key)
            return {
                "status": "error",
                "phase": "pre_side_effect",
                "slot": slot_name,
                "date_iso": today_iso,
                "error": err_msg,
                "duration": duration,
                "findings_count": findings_count,
            }
        return {
            "status": "error",
            "phase": "post_side_effect",
            "slot": slot_name,
            "date_iso": today_iso,
            "error": err_msg,
            "duration": duration,
            "findings_count": findings_count,
        }


async def run() -> dict:
    """Entry point invoked by the reflection scheduler.

    Iterates all local projects with ``pm_briefing.enabled = True`` AND
    ``machine`` matching this host, then iterates each project's slot list
    and dispatches matching slots. Per-(project x slot) failures are
    isolated: one slot's exception does NOT abort other slots or projects.

    Returns an aggregate status dict suitable for
    ``Reflection.mark_completed(projects=[...])``.
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
            "pm-briefings: loaded %d projects with pm_briefing.enabled=true on %s "
            "(skipped %d not owned by this machine)",
            len(owned),
            this_machine or "(unknown machine)",
            max(0, skipped),
        )
        _startup_logged = True

    results: dict[str, dict] = {}
    project_records: list[dict] = []
    successes = 0
    failures = 0

    for project in owned:
        slug = project.get("slug") or "unknown"
        # Read slots directly from config -- there is no longer a synthesis
        # step from legacy ``angles + schedule``. A project with
        # ``pm_briefing.enabled = true`` but no ``slots`` is logged-warned
        # and skipped (single canonical config path).
        raw_slots = (project.get("pm_briefing") or {}).get("slots")
        if not isinstance(raw_slots, list) or not raw_slots:
            logger.warning("pm-briefings: project %s has no slots configured; skipping", slug)
            results[slug] = {"status": "skipped", "reason": "no_slots"}
            continue
        slots = [dict(s) for s in raw_slots if isinstance(s, dict)]
        if not slots:
            logger.warning("pm-briefings: project %s has no valid slot dicts; skipping", slug)
            results[slug] = {"status": "skipped", "reason": "no_slots"}
            continue
        for slot in slots:
            slot_name = slot.get("name") or slot.get("type") or "unknown"
            key = f"{slug}:{slot_name}"
            try:
                res = _run_slot(project, slot, dry_run=dry_run)
            except Exception as e:
                res = {
                    "status": "error",
                    "phase": "outer",
                    "slot": slot_name,
                    "error": f"{type(e).__name__}: {e}",
                }
            results[key] = res
            project_records.append(
                {
                    "slug": slug,
                    "slot": slot_name,
                    "status": res.get("status", "unknown"),
                    "duration": float(res.get("duration") or 0.0),
                    "findings_count": int(res.get("findings_count") or 0),
                    "error": res.get("error"),
                    "date_iso": res.get("date_iso"),
                }
            )
            # "skipped" slots (outside_slot, no_schedule, lock_held,
            # already_succeeded_today) are intentionally NOT counted as
            # successes or failures — they aren't run-attempts. As a result,
            # summary.succeeded + summary.failed <= considered * slots_per_project.
            if res.get("status") in ("ok", "noop"):
                successes += 1
            elif res.get("status") == "error":
                failures += 1

    return {
        "status": "ok" if failures == 0 else ("partial" if successes else "error"),
        "projects": project_records,
        "results": results,
        "summary": {
            "considered": len(owned),
            "succeeded": successes,
            "failed": failures,
        },
    }
