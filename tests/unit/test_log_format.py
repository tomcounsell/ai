"""Tests for bridge.log_format StructuredJsonFormatter UTC timestamps."""

import json
import logging

from bridge.log_format import StructuredJsonFormatter


class TestStructuredJsonFormatterUTC:
    def setup_method(self):
        self.formatter = StructuredJsonFormatter()
        self.logger = logging.getLogger("test.log_format")
        self.logger.setLevel(logging.DEBUG)

    def _make_record(self, msg="test message"):
        return self.logger.makeRecord(
            name="test.log_format",
            level=logging.INFO,
            fn="test_file.py",
            lno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_timestamp_ends_with_z(self):
        record = self._make_record()
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["timestamp"].endswith("Z"), f"Expected Z suffix, got: {data['timestamp']}"

    def test_utc_field_present(self):
        record = self._make_record()
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["utc"] is True

    def test_timestamp_is_iso_format(self):
        record = self._make_record()
        output = self.formatter.format(record)
        data = json.loads(output)
        ts = data["timestamp"]
        # Should parse without error when we replace Z with +00:00
        from datetime import datetime

        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed is not None

    def test_no_plus_zero_offset(self):
        record = self._make_record()
        output = self.formatter.format(record)
        data = json.loads(output)
        assert "+00:00" not in data["timestamp"]

    def test_output_is_valid_json(self):
        record = self._make_record("hello world")
        output = self.formatter.format(record)
        data = json.loads(output)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
