"""D2 (#2458): pending-session age math must coerce datetime created_at.

``AgentSession.created_at`` is a datetime (Popoto SortedField); the intake
classifier's pending-window check previously computed
``now_ts - (ps.created_at or 0)`` which raised ``TypeError`` on every
invocation, and — because it sat inside the classifier's broad
``except Exception`` — suppressed the whole intake classifier (including the
load-bearing running/dormant interjection arm) for any chat with a pending
session.

These tests call the extracted production helper
``bridge.telegram_bridge._pending_session_age_seconds`` directly.
"""

from datetime import UTC, datetime, timedelta

import pytest

from bridge.telegram_bridge import _pending_session_age_seconds

NOW_TS = 1_700_000_000.0
NOW_DT = datetime.fromtimestamp(NOW_TS, tz=UTC)


@pytest.mark.parametrize(
    ("created_at", "expected_age"),
    [
        # Naive datetime — Popoto strips tzinfo on save; must be read as UTC.
        (NOW_DT.replace(tzinfo=None) - timedelta(seconds=5), 5.0),
        # Aware UTC datetime.
        (NOW_DT - timedelta(seconds=3), 3.0),
        # Float unix timestamp passthrough.
        (NOW_TS - 7.0, 7.0),
    ],
)
def test_age_coerces_datetime_and_float(created_at, expected_age):
    assert _pending_session_age_seconds(created_at, NOW_TS) == pytest.approx(
        expected_age, abs=0.001
    )


def test_missing_created_at_is_infinitely_old():
    """None created_at must exclude the session (age > any window), not crash."""
    assert _pending_session_age_seconds(None, NOW_TS) == float("inf")


def test_datetime_input_does_not_raise_typeerror():
    """The original defect: raw ``now_ts - datetime`` raised TypeError."""
    age = _pending_session_age_seconds(NOW_DT, NOW_TS)
    assert age == pytest.approx(0.0, abs=0.001)
