"""Schema-routing fallback-rate alert threshold (plan #2000 Task 2.3).

Covers ``monitoring.schema_routing_alert.check_schema_routing_fallback_rate``:
no turn volume -> no alert, healthy rate -> no alert, breach -> a WARNING
Alert with the fallback rate/threshold, and analytics-query failure -> no
alert (fail-quiet).
"""

from __future__ import annotations

from unittest.mock import patch

from monitoring.alerts import AlertLevel
from monitoring.schema_routing_alert import check_schema_routing_fallback_rate


def _patch_counts(turns: int, fallbacks: int):
    """Patch query_metric_count to return turns first, fallbacks second —
    matching check_schema_routing_fallback_rate's call order."""
    return patch(
        "analytics.query.query_metric_count",
        side_effect=[turns, fallbacks],
    )


def test_no_turn_volume_returns_none():
    with _patch_counts(turns=0, fallbacks=0):
        assert check_schema_routing_fallback_rate() is None


def test_healthy_rate_returns_none():
    # 1 fallback out of 100 turns = 1% — below the 5% default threshold.
    with _patch_counts(turns=100, fallbacks=1):
        assert check_schema_routing_fallback_rate() is None


def test_rate_at_threshold_returns_none():
    # Exactly 5% — the threshold is exclusive ("exceeds", not "meets").
    with _patch_counts(turns=100, fallbacks=5):
        assert check_schema_routing_fallback_rate() is None


def test_breach_returns_warning_alert():
    with _patch_counts(turns=100, fallbacks=10):
        alert = check_schema_routing_fallback_rate()
    assert alert is not None
    assert alert.level is AlertLevel.WARNING
    assert alert.current_value == 0.1
    assert alert.threshold == 0.05
    assert "10/100" in alert.message


def test_custom_window_and_threshold_honored():
    with _patch_counts(turns=20, fallbacks=3):
        # 15% fallback rate; custom threshold of 20% must NOT alert.
        assert check_schema_routing_fallback_rate(threshold=0.2) is None
    with _patch_counts(turns=20, fallbacks=3):
        # Same rate against the default 5% threshold DOES alert.
        assert check_schema_routing_fallback_rate() is not None


def test_analytics_query_failure_fails_quiet():
    with patch("analytics.query.query_metric_count", side_effect=RuntimeError("db locked")):
        assert check_schema_routing_fallback_rate() is None
