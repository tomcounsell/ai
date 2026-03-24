"""Unit tests for monitoring/telemetry.py facade functions.

Tests the public API that wraps the ObserverTelemetry model,
including error handling and the get_health dashboard function.
"""

from unittest.mock import patch

from models.telemetry import ObserverTelemetry
from monitoring.telemetry import get_health, record_decision, record_interjection, record_skip


class TestMonitoringTelemetryFacade:
    """Tests for the monitoring/telemetry.py facade functions."""

    TEST_DATE = "test-2026-03-24"

    def setup_method(self):
        """Clean up test records before each test."""
        for record in ObserverTelemetry.query.all():
            if str(record.date_key).startswith("test-"):
                record.delete()

    def teardown_method(self):
        """Clean up test records after each test."""
        for record in ObserverTelemetry.query.all():
            if str(record.date_key).startswith("test-"):
                record.delete()

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_decision_creates_record(self, mock_key):
        """record_decision creates a telemetry record and increments counter."""
        record_decision("test context")
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert record.decisions == 1
        assert record.last_decision_context == "test context"

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_decision_no_context(self, mock_key):
        """record_decision works without context."""
        record_decision()
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert record.decisions == 1

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_decision_error_handling(self, mock_key):
        """record_decision swallows errors silently."""
        with patch(
            "monitoring.telemetry.ObserverTelemetry.get_or_create",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            record_decision("test")

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_interjection(self, mock_key):
        """record_interjection increments counter and stores event."""
        record_interjection("test interjection event")
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert record.interjections == 1
        assert "test interjection event" in record.events

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_interjection_error_handling(self, mock_key):
        """record_interjection swallows errors silently."""
        with patch(
            "monitoring.telemetry.ObserverTelemetry.get_or_create",
            side_effect=RuntimeError("boom"),
        ):
            record_interjection("test")

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_skip(self, mock_key):
        """record_skip increments the skips counter."""
        record_skip()
        record_skip()
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert record.skips == 2

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_record_skip_error_handling(self, mock_key):
        """record_skip swallows errors silently."""
        with patch(
            "monitoring.telemetry.ObserverTelemetry.get_or_create",
            side_effect=RuntimeError("boom"),
        ):
            record_skip()

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_get_health_empty(self, mock_key):
        """get_health returns zero counters for a fresh date."""
        health = get_health()
        assert health["date"] == self.TEST_DATE
        assert health["decisions"] == 0
        assert health["interjections"] == 0
        assert health["skips"] == 0
        assert health["recent_events"] == []

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_get_health_with_data(self, mock_key):
        """get_health returns actual counters and recent events."""
        record_decision()
        record_decision()
        record_interjection("event_a")
        record_skip()
        health = get_health()
        assert health["decisions"] == 2
        assert health["interjections"] == 1
        assert health["skips"] == 1
        assert "event_a" in health["recent_events"]

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_get_health_limits_events(self, mock_key):
        """get_health returns at most 10 recent events."""
        for i in range(15):
            record_interjection(f"evt_{i}")
        health = get_health()
        assert len(health["recent_events"]) == 10

    @patch("monitoring.telemetry._today_key", return_value="test-2026-03-24")
    def test_get_health_error_handling(self, mock_key):
        """get_health returns default dict with error on failure."""
        with patch(
            "monitoring.telemetry.ObserverTelemetry.get_or_create",
            side_effect=RuntimeError("boom"),
        ):
            health = get_health()
            assert health["decisions"] == 0
            assert "error" in health
