"""Unit tests for the Reflection Popoto model (models/reflection.py).

Post-#1273 (unified Reflection system): per-run history lives in
``ReflectionRun`` rows; embedded ``run_history`` is gone. Tests for
``mark_completed`` now assert the ``last_run_summary`` dict + a
correctly-written ``ReflectionRun`` row.
"""

from __future__ import annotations

import time


class TestReflectionMarkCompleted:
    """Tests for Reflection.mark_completed()."""

    def _create_reflection(self, name: str = "test-reflection"):
        from models.reflection import Reflection

        return Reflection.create(
            name=name,
            ran_at=None,
            run_count=0,
            last_status="pending",
            last_error=None,
            last_duration=None,
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

    def test_mark_completed_writes_reflection_run_row(self):
        """Each mark_completed() call writes a ReflectionRun row."""
        from models.reflection_run import ReflectionRun

        # Clean any pre-existing rows for the deterministic name.
        for r in ReflectionRun.query.filter(name="test-reflection-runs"):
            r.delete()

        reflection = self._create_reflection(name="test-reflection-runs")
        reflection.mark_completed(duration=1.0)
        reflection.mark_completed(duration=2.0)

        rows = list(ReflectionRun.query.filter(name="test-reflection-runs"))
        assert len(rows) == 2

        for r in rows:
            r.delete()

    def test_last_run_summary_record_structure(self):
        """last_run_summary has timestamp, status, duration, error."""
        reflection = self._create_reflection()
        before = time.time()
        reflection.mark_completed(duration=2.5)

        summary = reflection.last_run_summary
        assert "timestamp" in summary
        assert "status" in summary
        assert "duration" in summary
        assert "error" in summary

        assert summary["status"] == "success"
        assert summary["duration"] == 2.5
        assert summary["error"] is None
        assert summary["timestamp"] >= before

    def test_last_run_summary_records_error(self):
        """last_run_summary captures error message when run fails."""
        reflection = self._create_reflection()
        reflection.mark_completed(duration=0.1, error="Timeout after 10s")

        summary = reflection.last_run_summary
        assert summary["status"] == "error"
        assert summary["error"] == "Timeout after 10s"

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

    def test_error_truncated_in_last_run_summary(self):
        """Long error messages are truncated to 500 chars in last_run_summary."""
        reflection = self._create_reflection()
        long_error = "x" * 1000
        reflection.mark_completed(duration=0.1, error=long_error)

        assert len(reflection.last_run_summary["error"]) == 500


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
