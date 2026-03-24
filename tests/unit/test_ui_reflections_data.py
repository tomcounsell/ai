"""Tests for the reflections data access layer."""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


class TestReflectionsDataLayer:
    """Tests for ui.data.reflections query functions."""

    def test_get_all_reflections_returns_list(self):
        from ui.data.reflections import get_all_reflections

        result = get_all_reflections()
        assert isinstance(result, list)
        # Should have entries from config/reflections.yaml
        assert len(result) > 0

    def test_reflection_entry_has_required_fields(self):
        from ui.data.reflections import get_all_reflections

        result = get_all_reflections()
        if result:
            entry = result[0]
            assert "name" in entry
            assert "description" in entry
            assert "interval" in entry
            assert "last_status" in entry
            assert "run_count" in entry


class TestReflectionFormatters:
    """Tests for Jinja2 filter formatting functions (canonical location: ui.app)."""

    def test_format_duration(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(None) == "-"
        assert _filter_format_duration(5.0) == "5.0s"
        assert _filter_format_duration(120.0) == "2.0m"
        assert _filter_format_duration(7200.0) == "2.0h"

    def test_format_timestamp(self):
        from ui.app import _filter_format_timestamp

        assert _filter_format_timestamp(None) == "-"
        result = _filter_format_timestamp(1711000000.0)
        assert "2024" in result

    def test_format_interval(self):
        from ui.app import _filter_format_interval

        assert _filter_format_interval(300) == "5m"
        assert _filter_format_interval(3600) == "1h"
        assert _filter_format_interval(86400) == "1d"
        assert _filter_format_interval(30) == "30s"

    def test_format_relative_time(self):
        from ui.app import _filter_format_relative

        assert _filter_format_relative(None) == "-"
        assert "in" in _filter_format_relative(300.0)
        assert "overdue" in _filter_format_relative(-300.0)


class TestReflectionModelExtension:
    """Tests for the run_history extension on the Reflection model."""

    def test_mark_completed_appends_history(self):
        """mark_completed() should append to run_history internally."""
        from models.reflection import Reflection

        ref = Reflection.get_or_create("_test_ui_history")
        try:
            initial_count = len(ref.run_history) if isinstance(ref.run_history, list) else 0

            ref.mark_completed(duration=1.5)
            ref = Reflection.query.filter(name="_test_ui_history")[0]

            history = ref.run_history if isinstance(ref.run_history, list) else []
            assert len(history) == initial_count + 1
            latest = history[-1]
            assert latest["status"] == "success"
            assert latest["duration"] == 1.5
            assert latest["error"] is None
        finally:
            ref.delete()

    def test_mark_completed_with_error_appends_history(self):
        """mark_completed() with error should record error in history."""
        from models.reflection import Reflection

        ref = Reflection.get_or_create("_test_ui_error_history")
        try:
            ref.mark_completed(duration=2.0, error="test error")
            ref = Reflection.query.filter(name="_test_ui_error_history")[0]

            history = ref.run_history if isinstance(ref.run_history, list) else []
            assert len(history) >= 1
            latest = history[-1]
            assert latest["status"] == "error"
            assert latest["error"] == "test error"
        finally:
            ref.delete()

    def test_mark_completed_signature_unchanged(self):
        """Existing callers pass (duration) and (duration, error=msg)."""
        from models.reflection import Reflection

        ref = Reflection.get_or_create("_test_ui_compat")
        try:
            # Positional arg only (like scheduler does)
            ref.mark_completed(1.0)
            # Keyword error arg (like scheduler does)
            ref.mark_completed(2.0, error="some error")
            assert ref.run_count >= 2
        finally:
            ref.delete()

    def test_run_history_cap(self):
        """run_history should be capped at _RUN_HISTORY_CAP entries."""
        from models.reflection import Reflection

        ref = Reflection.get_or_create("_test_ui_cap")
        try:
            # Pre-populate with many entries
            ref.run_history = [
                {"timestamp": i, "status": "success", "duration": 1.0, "error": None}
                for i in range(250)
            ]
            ref.save()

            ref.mark_completed(duration=1.0)
            ref = Reflection.query.filter(name="_test_ui_cap")[0]

            history = ref.run_history if isinstance(ref.run_history, list) else []
            assert len(history) <= 200
        finally:
            ref.delete()
