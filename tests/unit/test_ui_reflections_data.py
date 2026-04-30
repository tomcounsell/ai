"""Tests for the reflections data access layer."""

from unittest.mock import MagicMock, patch

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

    def test_prefix_expanded_reflections_tuple_is_wired(self):
        """The pm-audio-briefing prefix is registered for per-project expansion."""
        from ui.data.reflections import _PREFIX_EXPANDED_REFLECTIONS

        assert "pm-audio-briefing" in _PREFIX_EXPANDED_REFLECTIONS

    def test_prefix_merge_renders_per_project_rows(self):
        """When Reflection records exist for `pm-audio-briefing-<key>`, the
        helper appends per-project rows that reuse the parent's group."""
        from ui.data import reflections as reflections_module

        parent_entry = {
            "interval": 300,
            "timeout": 1500,
            "priority": "low",
            "execution_type": "function",
            "callable": "reflections.pm_audio_briefing.run",
            "enabled": True,
            "description": "Daily PM voice briefing",
        }

        # Build mock states: parent entry + two per-project records
        state_parent = MagicMock()
        state_parent.name = "pm-audio-briefing"
        state_parent.ran_at = 1_700_000_000.0
        state_parent.run_count = 1
        state_parent.last_status = "success"
        state_parent.last_error = None
        state_parent.last_duration = 5.0
        state_parent.run_history = []

        state_a = MagicMock()
        state_a.name = "pm-audio-briefing-psyoptimal"
        state_a.ran_at = 1_700_000_100.0
        state_a.run_count = 1
        state_a.last_status = "success"
        state_a.last_error = None
        state_a.last_duration = 6.0
        state_a.run_history = []

        state_b = MagicMock()
        state_b.name = "pm-audio-briefing-otherproj"
        state_b.ran_at = 1_700_000_200.0
        state_b.run_count = 1
        state_b.last_status = "success"
        state_b.last_error = None
        state_b.last_duration = 7.0
        state_b.run_history = []

        with (
            patch.object(
                reflections_module,
                "_get_registry_map",
                return_value={"pm-audio-briefing": parent_entry},
            ),
            patch(
                "models.reflection.Reflection.get_all_states",
                return_value=[state_parent, state_a, state_b],
            ),
        ):
            rows = reflections_module.get_all_reflections()

        names = [r["name"] for r in rows]
        assert "pm-audio-briefing" in names  # parent registry entry
        assert "pm-audio-briefing-psyoptimal" in names  # per-project row
        assert "pm-audio-briefing-otherproj" in names  # per-project row

        # Per-project rows reuse the parent's group classification
        per_proj_row = next(r for r in rows if r["name"] == "pm-audio-briefing-psyoptimal")
        parent_row = next(r for r in rows if r["name"] == "pm-audio-briefing")
        assert per_proj_row["group"] == parent_row["group"]

    def test_prefix_merge_handles_zero_per_project_records(self):
        """Renderer doesn't blow up when only the parent has a record."""
        from ui.data import reflections as reflections_module

        parent_entry = {
            "interval": 300,
            "priority": "low",
            "execution_type": "function",
            "callable": "reflections.pm_audio_briefing.run",
            "enabled": True,
            "description": "Daily PM voice briefing",
        }

        state_parent = MagicMock()
        state_parent.name = "pm-audio-briefing"
        state_parent.ran_at = 1_700_000_000.0
        state_parent.run_count = 1
        state_parent.last_status = "success"
        state_parent.last_error = None
        state_parent.last_duration = 5.0
        state_parent.run_history = []

        with (
            patch.object(
                reflections_module,
                "_get_registry_map",
                return_value={"pm-audio-briefing": parent_entry},
            ),
            patch("models.reflection.Reflection.get_all_states", return_value=[state_parent]),
        ):
            rows = reflections_module.get_all_reflections()

        names = [r["name"] for r in rows]
        assert "pm-audio-briefing" in names
        # No per-project rows because only the parent record exists
        assert len([n for n in names if n.startswith("pm-audio-briefing-")]) == 0

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

    def test_get_run_history_empty(self):
        from ui.data.reflections import get_run_history

        result = get_run_history("nonexistent-reflection")
        assert result["runs"] == []
        assert result["total_pages"] == 1
        assert result["total_runs"] == 0

    def test_get_run_detail_not_found(self):
        from ui.data.reflections import get_run_detail

        result = get_run_detail("nonexistent-reflection", 0)
        assert result is None


class TestReflectionFormatters:
    """Tests for Jinja2 filter formatting functions (canonical location: ui.app)."""

    def test_format_duration(self):
        from ui.app import _filter_format_duration

        assert _filter_format_duration(None) == "-"
        assert _filter_format_duration(5.0) == "5s"
        assert _filter_format_duration(120.0) == "2m"
        assert _filter_format_duration(7200.0) == "2h"

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
