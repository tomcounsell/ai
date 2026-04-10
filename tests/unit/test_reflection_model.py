"""Unit tests for the Reflection Popoto model (models/reflection.py).

Covers mark_completed() with run_history append behavior added in
the Unified Web UI PR (issue #477, PR #511).
"""

from __future__ import annotations

import time


class TestReflectionMarkCompleted:
    """Tests for Reflection.mark_completed() including run_history append."""

    def _create_reflection(self, name: str = "test-reflection"):
        from models.reflection import Reflection

        return Reflection.create(
            name=name,
            last_run=None,
            next_due=None,
            run_count=0,
            last_status="pending",
            last_error=None,
            last_duration=None,
            run_history=[],
        )

    def test_mark_completed_success(self):
        """mark_completed() sets status to success and updates fields."""
        reflection = self._create_reflection()
        reflection.mark_completed(duration=1.5)

        assert reflection.last_status == "success"
        assert reflection.last_duration == 1.5
        assert reflection.last_error is None
        assert reflection.run_count == 1

    def test_mark_completed_error(self):
        """mark_completed(error=...) sets status to error and stores error."""
        reflection = self._create_reflection()
        reflection.mark_completed(duration=0.3, error="Something went wrong")

        assert reflection.last_status == "error"
        assert reflection.last_duration == 0.3
        assert reflection.last_error == "Something went wrong"
        assert reflection.run_count == 1

    def test_mark_completed_appends_to_run_history(self):
        """Each mark_completed() call appends a run record to run_history."""
        reflection = self._create_reflection()

        reflection.mark_completed(duration=1.0)
        assert len(reflection.run_history) == 1

        reflection.mark_completed(duration=2.0)
        assert len(reflection.run_history) == 2

    def test_run_history_record_structure(self):
        """run_history entries have required fields: timestamp, status, duration, error."""
        reflection = self._create_reflection()
        before = time.time()
        reflection.mark_completed(duration=2.5)

        record = reflection.run_history[0]
        assert "timestamp" in record
        assert "status" in record
        assert "duration" in record
        assert "error" in record

        assert record["status"] == "success"
        assert record["duration"] == 2.5
        assert record["error"] is None
        assert record["timestamp"] >= before

    def test_run_history_records_error(self):
        """run_history entries capture error message when run fails."""
        reflection = self._create_reflection()
        reflection.mark_completed(duration=0.1, error="Timeout after 10s")

        record = reflection.run_history[0]
        assert record["status"] == "error"
        assert record["error"] == "Timeout after 10s"

    def test_run_history_capped_at_200(self):
        """run_history is capped at 200 entries — oldest are dropped."""
        reflection = self._create_reflection()

        # Add 205 runs
        for i in range(205):
            reflection.mark_completed(duration=float(i))

        assert len(reflection.run_history) == 200
        # Most recent 200 entries are kept (last one should have duration 204)
        assert reflection.run_history[-1]["duration"] == 204.0

    def test_mark_completed_increments_run_count(self):
        """run_count increments on every call."""
        reflection = self._create_reflection()
        assert reflection.run_count == 0

        reflection.mark_completed(duration=1.0)
        assert reflection.run_count == 1

        reflection.mark_completed(duration=1.0)
        assert reflection.run_count == 2

    def test_mark_completed_caller_signature_unchanged(self):
        """Existing callers using (duration) and (duration, error=msg) still work."""

        # Verify the method signature matches what reflection_scheduler calls
        import inspect

        from models.reflection import Reflection

        sig = inspect.signature(Reflection.mark_completed)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "duration" in params
        assert "error" in params

        # error should be optional (has default)
        error_param = sig.parameters["error"]
        assert error_param.default is None

    def test_error_truncated_in_last_error_field(self):
        """Long error messages are truncated to 1000 chars in last_error field."""
        reflection = self._create_reflection()
        long_error = "x" * 2000
        reflection.mark_completed(duration=0.1, error=long_error)

        assert len(reflection.last_error) == 1000

    def test_error_truncated_in_run_history(self):
        """Long error messages are truncated to 500 chars in run_history records."""
        reflection = self._create_reflection()
        long_error = "x" * 1000
        reflection.mark_completed(duration=0.1, error=long_error)

        record = reflection.run_history[0]
        assert len(record["error"]) == 500


class TestReflectionGetOrCreate:
    """Tests for Reflection.get_or_create() factory method."""

    def test_creates_new_record(self):
        from models.reflection import Reflection

        r = Reflection.get_or_create("health-check")
        assert r.name == "health-check"
        assert r.last_status == "pending"
        assert r.run_count == 0

    def test_returns_existing_record(self):
        from models.reflection import Reflection

        r1 = Reflection.get_or_create("idempotent-check")
        r1.mark_completed(duration=5.0)

        r2 = Reflection.get_or_create("idempotent-check")
        assert r2.run_count == 1
        assert r2.last_status == "success"
