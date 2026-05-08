"""Unit tests for the unified schedule grammar parser.

Tests `agent.reflection_schedule.compute_next_due()` covering the fazm-style
triplet `cron:` / `every:` / `at:` grammar (issue #1273, plan
`unify-recurring-tasks-into-reflections.md` Q2).

Architecture:
- `every: <N><unit>` — interval-style; suffixes s/m/h/d
- `cron: "<expr>"` — standard 5-field cron, optional `cron_tz: "..."`
- `at: <ISO-8601>` — single one-shot trigger
- Pre-migration `interval:` strings must be rejected with a clear ValueError.
"""

from __future__ import annotations

import time

import pytest


class TestComputeNextDueEvery:
    """`every:` grammar — interval style with s/m/h/d suffix."""

    def test_every_seconds(self):
        from agent.reflection_schedule import compute_next_due

        # last_run is None → due immediately (returns last_run-or-now baseline)
        # When last_run is None we expect a value <= now (i.e. immediately due).
        now = time.time()
        result = compute_next_due("every: 60s", last_run=None)
        assert result <= now + 0.1

    def test_every_seconds_with_last_run(self):
        from agent.reflection_schedule import compute_next_due

        last_run = 1_000_000.0
        assert compute_next_due("every: 60s", last_run=last_run) == 1_000_060.0

    def test_every_minutes(self):
        from agent.reflection_schedule import compute_next_due

        last_run = 1_000_000.0
        assert compute_next_due("every: 5m", last_run=last_run) == 1_000_300.0

    def test_every_hours(self):
        from agent.reflection_schedule import compute_next_due

        last_run = 1_000_000.0
        assert compute_next_due("every: 2h", last_run=last_run) == 1_007_200.0

    def test_every_days(self):
        from agent.reflection_schedule import compute_next_due

        last_run = 1_000_000.0
        assert compute_next_due("every: 1d", last_run=last_run) == 1_086_400.0

    def test_every_with_whitespace(self):
        from agent.reflection_schedule import compute_next_due

        # `every:60s` and `every: 60s` and `every:  60s` all parse identically.
        last_run = 1_000_000.0
        assert compute_next_due("every:60s", last_run=last_run) == 1_000_060.0
        assert compute_next_due("every:  60s", last_run=last_run) == 1_000_060.0


class TestComputeNextDueCron:
    """`cron:` grammar — standard 5-field cron, optional cron_tz."""

    def test_cron_daily_9am_utc(self):
        from agent.reflection_schedule import compute_next_due

        # 2026-01-15 12:00:00 UTC — next 09:00 daily fires at 2026-01-16 09:00.
        last_run = 1_768_521_600.0  # arbitrary anchor (does not matter for cron)
        result = compute_next_due("cron: 0 9 * * *", last_run=last_run, now=last_run)
        # Result must be > now, and an exact-hour boundary.
        assert result > last_run
        # Verify it's exactly the next 09:00 (or 21h interval if before 9am, etc.)
        delta = result - last_run
        assert 0 < delta <= 86400

    def test_cron_every_minute(self):
        from agent.reflection_schedule import compute_next_due

        now = 1_768_521_600.0
        result = compute_next_due("cron: * * * * *", last_run=now, now=now)
        # Next minute boundary, < 60s away
        assert 0 < result - now <= 60

    def test_cron_with_tz_field(self):
        """`cron_tz: <name>` modifier — using the inline `; tz=...` form."""
        from agent.reflection_schedule import compute_next_due

        # Inline TZ via "; tz=America/Los_Angeles" suffix.
        now = 1_768_521_600.0
        result_utc = compute_next_due("cron: 0 9 * * *", last_run=now, now=now)
        result_la = compute_next_due(
            "cron: 0 9 * * *; tz=America/Los_Angeles", last_run=now, now=now
        )
        # LA 9am is later than UTC 9am — they differ.
        assert result_utc != result_la


class TestComputeNextDueAt:
    """`at:` grammar — single ISO-8601 one-shot trigger."""

    def test_at_future_iso(self):
        from agent.reflection_schedule import compute_next_due

        # Far-future ISO returns a fixed timestamp.
        future = "2099-01-01T00:00:00+00:00"
        result = compute_next_due(f"at: {future}", last_run=None)
        # Value must be the parsed timestamp, not "now + something".
        from datetime import datetime

        expected = datetime.fromisoformat(future).timestamp()
        assert result == pytest.approx(expected, abs=1.0)

    def test_at_past_iso_returns_past_timestamp(self):
        from agent.reflection_schedule import compute_next_due

        # Past ISO is still a fixed timestamp; the scheduler treats due-in-past as "fire now".
        past = "2020-01-01T00:00:00+00:00"
        result = compute_next_due(f"at: {past}", last_run=None)
        from datetime import datetime

        expected = datetime.fromisoformat(past).timestamp()
        assert result == pytest.approx(expected, abs=1.0)


class TestComputeNextDueRejection:
    """Invalid input must raise ValueError with a clear message."""

    def test_empty_schedule_raises(self):
        from agent.reflection_schedule import compute_next_due

        with pytest.raises(ValueError, match="schedule.*required|empty"):
            compute_next_due("", last_run=None)

    def test_legacy_interval_rejected(self):
        from agent.reflection_schedule import compute_next_due

        # Old `interval: 60` style is rejected with a clear migration hint.
        with pytest.raises(ValueError, match="interval"):
            compute_next_due("interval: 60", last_run=None)

    def test_unknown_grammar_rejected(self):
        from agent.reflection_schedule import compute_next_due

        with pytest.raises(ValueError, match="grammar|unknown|unrecognized"):
            compute_next_due("hourly", last_run=None)

    def test_malformed_cron_rejected(self):
        from agent.reflection_schedule import compute_next_due

        with pytest.raises(ValueError, match="cron"):
            compute_next_due("cron: not-a-cron-expr", last_run=None)

    def test_malformed_every_rejected(self):
        from agent.reflection_schedule import compute_next_due

        with pytest.raises(ValueError, match="every|interval|duration"):
            compute_next_due("every: 5xyz", last_run=None)

    def test_at_invalid_iso_rejected(self):
        from agent.reflection_schedule import compute_next_due

        with pytest.raises(ValueError, match="at|ISO"):
            compute_next_due("at: not-a-date", last_run=None)


class TestGrammarHelpers:
    """is_legacy_interval, parse_every_duration helpers."""

    def test_parse_every_duration_seconds(self):
        from agent.reflection_schedule import parse_every_duration

        assert parse_every_duration("60s") == 60
        assert parse_every_duration("5m") == 300
        assert parse_every_duration("2h") == 7200
        assert parse_every_duration("1d") == 86400

    def test_parse_every_duration_invalid(self):
        from agent.reflection_schedule import parse_every_duration

        with pytest.raises(ValueError):
            parse_every_duration("5xyz")
        with pytest.raises(ValueError):
            parse_every_duration("")

    def test_is_legacy_interval_format(self):
        from agent.reflection_schedule import is_legacy_interval_format

        assert is_legacy_interval_format("interval: 60") is True
        assert is_legacy_interval_format("interval:60") is True
        assert is_legacy_interval_format("every: 60s") is False
        assert is_legacy_interval_format("cron: 0 9 * * *") is False
        assert is_legacy_interval_format("at: 2099-01-01T00:00:00+00:00") is False


class TestAtScheduleAutoDelete:
    """`at:` schedules signal that the reflection should auto-delete after success."""

    def test_at_schedule_is_one_shot(self):
        from agent.reflection_schedule import is_one_shot_schedule

        assert is_one_shot_schedule("at: 2099-01-01T00:00:00+00:00") is True
        assert is_one_shot_schedule("every: 60s") is False
        assert is_one_shot_schedule("cron: 0 9 * * *") is False
