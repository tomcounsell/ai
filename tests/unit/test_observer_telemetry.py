"""Unit tests for models/telemetry.py ObserverTelemetry model.

Tests the Popoto model that tracks observer agent telemetry data
with daily rollup keys and 7-day TTL.
"""

from models.telemetry import ObserverTelemetry


class TestObserverTelemetry:
    """Tests for the ObserverTelemetry Popoto model."""

    TEST_DATE = "test-2026-01-01"

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

    def test_get_or_create_new(self):
        """get_or_create returns a new record with zero counters."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert record.date_key == self.TEST_DATE
        assert record.decisions == 0
        assert record.interjections == 0
        assert record.skips == 0
        assert record.events == []

    def test_get_or_create_existing(self):
        """get_or_create returns existing record for known date."""
        ObserverTelemetry.create(
            date_key=self.TEST_DATE, decisions=5, interjections=2, skips=3, events=[]
        )
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert record.decisions == 5
        assert record.interjections == 2
        assert record.skips == 3

    def test_record_decision_increments(self):
        """record_decision increments the decisions counter."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        record.record_decision()
        record.record_decision()
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert reloaded.decisions == 2

    def test_record_decision_with_context(self):
        """record_decision stores context when provided."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        record.record_decision("test context string")
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert reloaded.last_decision_context == "test context string"

    def test_record_decision_truncates_context(self):
        """record_decision truncates context to 500 chars."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        long_context = "x" * 600
        record.record_decision(long_context)
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert len(reloaded.last_decision_context) == 500

    def test_record_interjection_increments(self):
        """record_interjection increments the interjections counter."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        record.record_interjection("test event 1")
        record.record_interjection("test event 2")
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert reloaded.interjections == 2

    def test_record_interjection_appends_event(self):
        """record_interjection appends description to events list."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        record.record_interjection("first event")
        record.record_interjection("second event")
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert "first event" in reloaded.events
        assert "second event" in reloaded.events

    def test_record_interjection_trims_events(self):
        """record_interjection trims events to MAX_EVENTS."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        # Fill beyond max
        for i in range(ObserverTelemetry._MAX_EVENTS + 5):
            record.record_interjection(f"event_{i}")
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert len(reloaded.events) == ObserverTelemetry._MAX_EVENTS
        # Most recent events should be kept
        assert f"event_{ObserverTelemetry._MAX_EVENTS + 4}" in reloaded.events

    def test_record_skip_increments(self):
        """record_skip increments the skips counter."""
        record = ObserverTelemetry.get_or_create(self.TEST_DATE)
        record.record_skip()
        record.record_skip()
        record.record_skip()
        reloaded = ObserverTelemetry.get_or_create(self.TEST_DATE)
        assert reloaded.skips == 3

    def test_ttl_is_7_days(self):
        """ObserverTelemetry should have a 7-day TTL."""
        assert ObserverTelemetry._meta.ttl == 604800

    def test_max_events_constant(self):
        """MAX_EVENTS should be 100."""
        assert ObserverTelemetry._MAX_EVENTS == 100
