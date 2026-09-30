"""
reflections/pm_briefings/morning.py — Morning brief slot.

Wraps the existing collector + builder + delivery scaffolding so the slot
dispatch loop in ``__init__.py`` can call a uniform interface across all
slot types (``morning``, ``daily_log``, ``log_audit``).

The slot returns ``(transcript, followup, raw_signals)`` and is PURE — it
does NOT enqueue Telegram payloads, does NOT release SETNX locks, does NOT
mark the per-project Reflection record. Those side effects stay in the
dispatcher (``__init__.py::_run_slot``) so the lock-release policy lives in
ONE place.

See ``docs/plans/daily-reflections-unification.md`` (Implementation Notes)
for the rationale.
"""

from __future__ import annotations

import logging
from typing import Any

from reflections.pm_briefings import builder, collector

logger = logging.getLogger("reflections.pm_briefings.morning")


SLOT_TYPE = "morning"


def build(project: dict, slot_config: dict) -> tuple[str, str, dict[str, Any]]:
    """Build the morning brief for one project.

    Args:
        project: The full project dict from ``load_local_projects()``.
        slot_config: The slot's config dict from ``project.pm_briefing.slots``
            (or the synthesized single-slot dict for legacy configs).

    Returns:
        ``(transcript, followup_markdown, raw_signals)`` where ``transcript``
        is the audio script, ``followup_markdown`` is the written follow-up,
        and ``raw_signals`` is the deterministic collector output (used by
        the dispatcher to detect skip-when-empty).

        On skip-when-empty, returns ``("", "", {})``.

    Raises:
        BriefingNumbersDetectedError: If the transcript contains forbidden
            number forms (issue/PR numbers).
        BriefingWordCountError: If Pass A undershoots the word count.
        RuntimeError: If the LLM call fails.
    """
    # Merge slot-level angles config with the project's pm_briefing config.
    pm = project.get("pm_briefing") or {}
    angles = slot_config.get("angles") or pm.get("angles") or {}
    include = list(angles.get("include") or [])
    exclude = list(angles.get("exclude") or [])

    raw_signals = collector.collect(project, include, exclude)

    fallback_message = (
        slot_config.get("fallback_message")
        or pm.get("fallback_message")
        or "Nothing shipped yesterday."
    )
    skip_when_empty = bool(slot_config.get("skip_when_empty", pm.get("skip_when_empty", True)))

    transcript, followup = builder.build(
        raw_signals,
        fallback_message=fallback_message,
        skip_when_empty=skip_when_empty,
        project=project,
    )
    return transcript, followup, raw_signals
