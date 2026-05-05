"""
reflections/pm_audio_briefing/daily_log.py — Daily-log slot.

End-of-day activity recap delivered as a per-project audio brief plus
written follow-up. Wraps ``reflections.daily_report`` collection and audio-
brief construction so existing tested code stays the source of truth while
the slot dispatcher can call a uniform ``build(project, slot_config)``.

This slot is PURE — it does NOT enqueue Telegram payloads, does NOT release
the SETNX lock, does NOT mark the per-project Reflection record. The
dispatcher in ``__init__.py`` owns all side effects.

The vault file write (Markdown day log under ``~/work-vault/.../daily-logs/``)
is gated by ``slot_config.vault_writer: true`` to avoid iCloud conflict-copy
races across machines (see Risk 3 in the plan and the Implementation Notes).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

logger = logging.getLogger("reflections.pm_audio_briefing.daily_log")


SLOT_TYPE = "daily_log"


def _to_signals_dict(activity) -> dict[str, list[dict]]:
    """Convert ``DayActivity`` to the dict shape ``builder.build`` expects.

    Reuses ``daily_report._activity_to_signals`` so the categorization stays
    in lockstep with the existing renderer.
    """
    from reflections.daily_report import _activity_to_signals

    return _activity_to_signals(activity)


def build(project: dict, slot_config: dict) -> tuple[str, str, dict[str, Any]]:
    """Build the per-project daily-log recap.

    Args:
        project: The full project dict.
        slot_config: The slot's config dict. Recognized keys:
            ``vault_writer`` (bool, default False) — if True, this slot
            writes the per-day vault Markdown file. Single-machine-ownership
            invariant ensures only one slot across all projects/machines has
            this set.

    Returns:
        ``(transcript, followup_markdown, raw_signals)``. On skip-when-empty
        (no activity for the target day), returns ``("", "", {})``.
    """
    from bridge.utc import utc_now
    from reflections.daily_report import (
        _collect_day_activity,
        _write_vault_log,
    )
    from reflections.pm_audio_briefing import builder

    target_date = utc_now() - timedelta(days=1)

    # Run the async collector. In production we are inside a running event
    # loop: the parent ``pm_audio_briefing.run()`` is ``async def`` and the
    # reflection scheduler awaits it directly (see
    # ``agent/reflection_scheduler.py``), so the ``loop.is_running()`` branch
    # is the production path. ``asyncio.run`` cannot run inside an existing
    # loop, so we hand the coroutine to a dedicated thread that owns its own
    # event loop. The else branch handles direct sync invocations (CLI tools,
    # tests) where no loop is running yet.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                activity = pool.submit(asyncio.run, _collect_day_activity(target_date)).result()
        else:
            activity = asyncio.run(_collect_day_activity(target_date))
    except RuntimeError:
        activity = asyncio.run(_collect_day_activity(target_date))

    # Vault file write — gated by vault_writer flag (default False).
    if slot_config.get("vault_writer") is True:
        try:
            dest = _write_vault_log(activity, target_date)
            logger.info("Vault log written: %s", dest)
        except Exception as e:  # swallow-ok: vault write is best-effort
            logger.warning("Vault write failed for %s: %s", target_date.date().isoformat(), e)

    raw_signals = _to_signals_dict(activity)

    pm = project.get("pm_briefing") or {}
    fallback_message = (
        slot_config.get("fallback_message")
        or pm.get("fallback_message")
        or "Nothing shipped yesterday."
    )
    skip_when_empty = bool(slot_config.get("skip_when_empty", pm.get("skip_when_empty", True)))

    # Drive the brief through the same builder pipeline as the morning slot
    # so the no-numbers + word-count guards stay applied uniformly.
    transcript, followup = builder.build(
        raw_signals,
        fallback_message=fallback_message,
        skip_when_empty=skip_when_empty,
        project=project,
    )
    return transcript, followup, raw_signals
