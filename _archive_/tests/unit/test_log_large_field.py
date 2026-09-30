"""Unit tests for tools/field_utils.py log_large_field utility."""

import logging

from tools.field_utils import log_large_field


class TestLogLargeField:
    """Tests for the log_large_field observability helper."""

    def test_none_input_no_op(self, caplog):
        """None value should not log anything."""
        with caplog.at_level(logging.WARNING):
            log_large_field("test_field", None)
        assert len(caplog.records) == 0

    def test_empty_string_no_op(self, caplog):
        """Empty string should not log anything."""
        with caplog.at_level(logging.WARNING):
            log_large_field("test_field", "")
        assert len(caplog.records) == 0

    def test_short_string_no_warning(self, caplog):
        """String under threshold should not log a warning."""
        with caplog.at_level(logging.WARNING):
            log_large_field("test_field", "hello world")
        assert len(caplog.records) == 0

    def test_over_threshold_logs_warning(self, caplog):
        """String over threshold should log a warning with field name and length."""
        with caplog.at_level(logging.WARNING):
            log_large_field("test_field", "x" * 60_000)
        assert len(caplog.records) == 1
        assert "test_field" in caplog.records[0].message
        assert "60000" in caplog.records[0].message

    def test_custom_threshold(self, caplog):
        """Custom threshold should be respected."""
        with caplog.at_level(logging.WARNING):
            log_large_field("test_field", "x" * 200, threshold=100)
        assert len(caplog.records) == 1
        assert "200" in caplog.records[0].message

    def test_at_threshold_no_warning(self, caplog):
        """String exactly at threshold should not log (only over)."""
        with caplog.at_level(logging.WARNING):
            log_large_field("test_field", "x" * 50_000)
        assert len(caplog.records) == 0
