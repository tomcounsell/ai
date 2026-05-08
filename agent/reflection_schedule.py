"""Unified schedule grammar for the Reflection scheduler (issue #1273).

Implements the fazm-style triplet `cron:` / `every:` / `at:`. This module is
imported by both the asyncio scheduler tick loop and the MCP `reflections_create`
validator so the parsing logic is never duplicated (per Q2 implementation guard
in `docs/plans/unify-recurring-tasks-into-reflections.md`).

Supported forms:

- ``every: <N>{s|m|h|d}`` — interval-style. e.g. ``every: 60s``, ``every: 1d``.
  Replaces the legacy ``interval: <seconds>`` form (rejected with a clear hint).
- ``cron: <5-field-expr>`` — standard cron, optional inline timezone via
  ``cron: 0 9 * * *; tz=America/Los_Angeles``.
- ``at: <ISO-8601-with-tz>`` — single one-shot trigger.

Public API:

- ``compute_next_due(schedule_str, last_run, *, now=None) -> float`` — returns the
  unix timestamp at which the reflection should fire next. Raises ``ValueError``
  on empty or unparseable input. Never silently falls back to a default schedule.
- ``parse_every_duration(s)`` — helper for the ``<N><unit>`` form.
- ``is_legacy_interval_format(s)`` — true for the pre-migration shape so callers
  can produce a friendlier error.
- ``is_one_shot_schedule(s)`` — true for ``at:`` schedules (drives the
  ``auto_delete_after_run`` lifecycle decision in Q2 cycle-4 fix).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Final

# Suffix → multiplier (seconds).
_DURATION_UNITS: Final[dict[str, int]] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

_DURATION_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_LEGACY_INTERVAL_RE: Final[re.Pattern[str]] = re.compile(r"^\s*interval\s*:")
_INLINE_TZ_RE: Final[re.Pattern[str]] = re.compile(r";\s*tz\s*=\s*([^;\s]+)\s*$")


def is_legacy_interval_format(schedule_str: str) -> bool:
    """Return True for pre-migration ``interval: N`` strings.

    The migration script rewrites these to ``every: Ns``. Calling
    ``compute_next_due`` on a legacy string raises ValueError pointing the
    operator at the migration script.
    """
    if not isinstance(schedule_str, str):
        return False
    return bool(_LEGACY_INTERVAL_RE.match(schedule_str))


def parse_every_duration(s: str) -> int:
    """Parse ``<N><unit>`` (e.g. ``60s``) → integer seconds.

    Raises ValueError on empty or malformed input.
    """
    if not s or not s.strip():
        raise ValueError("every: duration is empty")
    match = _DURATION_RE.match(s)
    if not match:
        raise ValueError(
            f"every: duration {s!r} is not in <N>{{s|m|h|d}} form (e.g. '60s', '5m', '1d')"
        )
    n = int(match.group(1))
    unit = match.group(2)
    if n <= 0:
        raise ValueError(f"every: duration must be positive, got {n}")
    return n * _DURATION_UNITS[unit]


def is_one_shot_schedule(schedule_str: str) -> bool:
    """Return True for ``at:`` schedules (one-shot)."""
    if not isinstance(schedule_str, str):
        return False
    return schedule_str.strip().lower().startswith("at:")


def _split_prefix(schedule_str: str) -> tuple[str, str]:
    """Split ``"<prefix>: <body>"`` into ``(prefix, body)`` (lowercased prefix).

    Raises ValueError if no ``:`` separator is present.
    """
    if ":" not in schedule_str:
        raise ValueError(
            f"schedule {schedule_str!r} has no grammar prefix; "
            "expected one of 'every:', 'cron:', 'at:'"
        )
    prefix, _, body = schedule_str.partition(":")
    return prefix.strip().lower(), body.strip()


def _next_cron(body: str, now: float) -> float:
    """Return the next fire time for a cron expression.

    Supports inline ``; tz=<zoneinfo>`` suffix. Anchors on ``now`` (not
    last_run) so the next fire is wall-clock-deterministic regardless of
    when the reflection last ran.
    """
    try:
        from croniter import croniter
    except ImportError as e:  # pragma: no cover - dependency declared in pyproject
        raise ValueError(f"cron schedules require the croniter package: {e}") from e

    expr = body
    tz = None
    tz_match = _INLINE_TZ_RE.search(body)
    if tz_match:
        tz = tz_match.group(1)
        expr = _INLINE_TZ_RE.sub("", body).strip()

    if not expr:
        raise ValueError("cron: expression is empty")

    if tz:
        try:
            from zoneinfo import ZoneInfo
        except ImportError as e:  # pragma: no cover
            raise ValueError(f"timezone requires zoneinfo: {e}") from e
        try:
            zone = ZoneInfo(tz)
        except Exception as e:
            raise ValueError(f"cron: unknown timezone {tz!r}: {e}") from e
        anchor = datetime.fromtimestamp(now, tz=zone)
    else:
        anchor = datetime.fromtimestamp(now, tz=UTC)

    try:
        itr = croniter(expr, anchor)
    except Exception as e:
        raise ValueError(f"cron: invalid expression {expr!r}: {e}") from e

    next_dt = itr.get_next(datetime)
    return next_dt.timestamp()


def _parse_at(body: str) -> float:
    """Parse an ISO-8601 timestamp body."""
    if not body:
        raise ValueError("at: ISO-8601 timestamp is required")
    try:
        dt = datetime.fromisoformat(body)
    except ValueError as e:
        raise ValueError(f"at: invalid ISO-8601 timestamp {body!r}: {e}") from e
    return dt.timestamp()


def compute_next_due(
    schedule_str: str,
    last_run: float | None,
    *,
    now: float | None = None,
) -> float:
    """Compute the next-due timestamp for a unified schedule.

    Args:
        schedule_str: One of ``every: <dur>``, ``cron: <expr>[; tz=<zone>]``,
            or ``at: <iso>``.
        last_run: Unix timestamp of the most recent run, or None if the
            reflection has never run.
        now: Optional injection point for testability. Defaults to ``time.time()``.

    Returns:
        Unix timestamp at which the reflection should next fire.
        For ``every:``, this is ``last_run + duration`` (or ``now`` if no last_run).
        For ``cron:``, this is the next wall-clock fire after ``now``.
        For ``at:``, this is the parsed ISO timestamp (regardless of past/future).

    Raises:
        ValueError on empty input, legacy ``interval:`` syntax, unknown prefix,
        or malformed body.
    """
    if not schedule_str or not schedule_str.strip():
        raise ValueError("schedule is empty / required")

    if is_legacy_interval_format(schedule_str):
        raise ValueError(
            "schedule uses legacy 'interval: N' form; rewrite to "
            "'every: Ns' (or run scripts/migrate_reflections_yaml.py)"
        )

    if now is None:
        now = time.time()

    prefix, body = _split_prefix(schedule_str)

    if prefix == "every":
        duration = parse_every_duration(body)
        if last_run is None:
            return now
        return last_run + duration

    if prefix == "cron":
        return _next_cron(body, now)

    if prefix == "at":
        return _parse_at(body)

    raise ValueError(
        f"unknown schedule grammar prefix {prefix!r}: expected one of 'every:', 'cron:', 'at:'"
    )
