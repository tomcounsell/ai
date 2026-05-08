"""Unit tests for the unified reflection scheduler (agent/reflection_scheduler.py).

Covers compute_next_due() / compute_interval_seconds() with the fazm-style
schedule grammar (cron:/every:/at:) replacing the legacy interval int field.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta

import pytest

from agent.reflection_scheduler import (
    ReflectionEntry,
    compute_interval_seconds,
    compute_next_due,
)

# ---------------------------------------------------------------------------
# compute_next_due — every:
# ---------------------------------------------------------------------------


class TestComputeNextDueEvery:
    def test_every_60s_no_last_run(self):
        now = time.time()
        nxt = compute_next_due("every:60s", None)
        assert abs(nxt - now) < 5.0  # ~now

    def test_every_5m_after_last_run(self):
        last = time.time()
        nxt = compute_next_due("every:5m", last)
        assert abs(nxt - (last + 300)) < 1.0

    def test_every_2h(self):
        last = time.time()
        nxt = compute_next_due("every:2h", last)
        assert abs(nxt - (last + 7200)) < 1.0

    def test_every_1d(self):
        last = time.time()
        nxt = compute_next_due("every:1d", last)
        assert abs(nxt - (last + 86400)) < 1.0

    def test_every_invalid_suffix(self):
        with pytest.raises(ValueError):
            compute_next_due("every:60x", None)

    def test_every_negative(self):
        with pytest.raises(ValueError):
            compute_next_due("every:-5s", None)


# ---------------------------------------------------------------------------
# compute_next_due — cron:
# ---------------------------------------------------------------------------


class TestComputeNextDueCron:
    def test_cron_daily_9am_utc(self):
        last = time.time()
        nxt = compute_next_due("cron:0 9 * * *", last, cron_tz="UTC")
        # nxt must be in the future and ≤ 24h ahead
        assert nxt > last
        assert nxt <= last + 86400 + 60

    def test_cron_with_la_timezone(self):
        last = time.time()
        nxt = compute_next_due("cron:0 9 * * *", last, cron_tz="America/Los_Angeles")
        assert nxt > last
        assert nxt <= last + 86400 + 60

    def test_cron_invalid(self):
        with pytest.raises(ValueError):
            compute_next_due("cron:not a cron", None)

    def test_cron_empty(self):
        with pytest.raises(ValueError):
            compute_next_due("cron:", None)


# ---------------------------------------------------------------------------
# compute_next_due — at:
# ---------------------------------------------------------------------------


class TestComputeNextDueAt:
    def test_at_future_returns_target(self):
        target = datetime.now(UTC) + timedelta(hours=2)
        iso = target.isoformat()
        nxt = compute_next_due(f"at:{iso}", None)
        assert abs(nxt - target.timestamp()) < 1.0

    def test_at_past_with_no_last_run_returns_inf(self):
        target = datetime.now(UTC) - timedelta(hours=1)
        iso = target.isoformat()
        nxt = compute_next_due(f"at:{iso}", None)
        assert nxt == math.inf

    def test_at_already_fired_returns_inf(self):
        target = datetime.now(UTC) + timedelta(hours=2)
        iso = target.isoformat()
        # last_run set means we already ran the one-shot
        nxt = compute_next_due(f"at:{iso}", time.time())
        assert nxt == math.inf

    def test_at_invalid_iso(self):
        with pytest.raises(ValueError):
            compute_next_due("at:not-iso", None)

    def test_at_empty(self):
        with pytest.raises(ValueError):
            compute_next_due("at:", None)


# ---------------------------------------------------------------------------
# compute_next_due — error cases
# ---------------------------------------------------------------------------


class TestComputeNextDueErrors:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            compute_next_due("", None)

    def test_none_raises(self):
        with pytest.raises(ValueError):
            compute_next_due(None, None)  # type: ignore[arg-type]

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError):
            compute_next_due("hourly:60", None)

    def test_legacy_interval_raises(self):
        with pytest.raises(ValueError) as ei:
            compute_next_due("interval:300", None)
        assert "interval" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# compute_interval_seconds
# ---------------------------------------------------------------------------


class TestComputeIntervalSeconds:
    def test_every_seconds(self):
        assert compute_interval_seconds("every:60s") == 60

    def test_every_minutes(self):
        assert compute_interval_seconds("every:5m") == 300

    def test_every_hours(self):
        assert compute_interval_seconds("every:1h") == 3600

    def test_at_returns_zero(self):
        assert compute_interval_seconds("at:2030-01-01T00:00:00Z") == 0

    def test_cron_returns_estimate(self):
        secs = compute_interval_seconds("cron:0 * * * *")  # hourly
        # Expected ~3600 ± a small slop
        assert 3500 <= secs <= 3700

    def test_empty_returns_zero(self):
        assert compute_interval_seconds("") == 0

    def test_unknown_returns_zero(self):
        assert compute_interval_seconds("nonsense:foo") == 0


# ---------------------------------------------------------------------------
# ReflectionEntry validation
# ---------------------------------------------------------------------------


class TestReflectionEntryValidation:
    def test_valid_function(self):
        e = ReflectionEntry(
            name="t",
            description="",
            schedule="every:60s",
            priority="low",
            execution_type="function",
            callable="m.f",
        )
        assert e.validate() == []

    def test_valid_agent(self):
        e = ReflectionEntry(
            name="t",
            description="",
            schedule="cron:0 * * * *",
            priority="low",
            execution_type="agent",
            command="echo hi",
        )
        assert e.validate() == []

    def test_invalid_schedule(self):
        e = ReflectionEntry(
            name="t",
            description="",
            schedule="nope",
            priority="low",
            execution_type="function",
            callable="m.f",
        )
        errors = e.validate()
        assert any("schedule" in err for err in errors)
