"""Tests for the reflections data access layer."""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]


class TestReflectionsDataLayer:
    """Tests for ui.data.reflections query functions."""

    def test_get_all_reflections_returns_list(self):
        """get_all_reflections returns a list (may be empty if registry path
        is unresolved in this checkout — it's a symlink to the iCloud vault).

        Content presence is exercised by the prefix-merge tests below using
        an explicit registry mock; here we only assert the shape.
        """
        from ui.data.reflections import get_all_reflections

        result = get_all_reflections()
        assert isinstance(result, list)

    def test_prefix_expanded_reflections_tuple_is_wired(self):
        """The pm-briefings prefix is registered for per-project expansion."""
        from ui.data.reflections import _PREFIX_EXPANDED_REFLECTIONS

        assert "pm-briefings" in _PREFIX_EXPANDED_REFLECTIONS

    def test_prefix_merge_renders_per_project_rows(self):
        """When Reflection records exist for `pm-briefings-<key>`, the
        helper appends per-project rows that reuse the parent's group."""
        from ui.data import reflections as reflections_module

        parent_entry = {
            "schedule": "every:300s",
            "timeout": 1500,
            "priority": "low",
            "execution_type": "function",
            "callable": "reflections.pm_audio_briefing.run",
            "enabled": True,
            "description": "Daily PM voice briefing",
        }

        # Build mock states: parent entry + two per-project records
        state_parent = MagicMock()
        state_parent.name = "pm-briefings"
        state_parent.ran_at = 1_700_000_000.0
        state_parent.run_count = 1
        state_parent.last_status = "success"
        state_parent.last_error = None
        state_parent.last_duration = 5.0

        state_a = MagicMock()
        state_a.name = "pm-briefings-psyoptimal"
        state_a.ran_at = 1_700_000_100.0
        state_a.run_count = 1
        state_a.last_status = "success"
        state_a.last_error = None
        state_a.last_duration = 6.0

        state_b = MagicMock()
        state_b.name = "pm-briefings-otherproj"
        state_b.ran_at = 1_700_000_200.0
        state_b.run_count = 1
        state_b.last_status = "success"
        state_b.last_error = None
        state_b.last_duration = 7.0

        with (
            patch.object(
                reflections_module,
                "_get_registry_map",
                return_value={"pm-briefings": parent_entry},
            ),
            patch(
                "models.reflection.Reflection.get_all_states",
                return_value=[state_parent, state_a, state_b],
            ),
        ):
            rows = reflections_module.get_all_reflections()

        names = [r["name"] for r in rows]
        assert "pm-briefings" in names  # parent registry entry
        assert "pm-briefings-psyoptimal" in names  # per-project row
        assert "pm-briefings-otherproj" in names  # per-project row

        # Per-project rows reuse the parent's group classification
        per_proj_row = next(r for r in rows if r["name"] == "pm-briefings-psyoptimal")
        parent_row = next(r for r in rows if r["name"] == "pm-briefings")
        assert per_proj_row["group"] == parent_row["group"]

    def test_prefix_merge_handles_zero_per_project_records(self):
        """Renderer doesn't blow up when only the parent has a record."""
        from ui.data import reflections as reflections_module

        parent_entry = {
            "schedule": "every:300s",
            "priority": "low",
            "execution_type": "function",
            "callable": "reflections.pm_audio_briefing.run",
            "enabled": True,
            "description": "Daily PM voice briefing",
        }

        state_parent = MagicMock()
        state_parent.name = "pm-briefings"
        state_parent.ran_at = 1_700_000_000.0
        state_parent.run_count = 1
        state_parent.last_status = "success"
        state_parent.last_error = None
        state_parent.last_duration = 5.0

        with (
            patch.object(
                reflections_module,
                "_get_registry_map",
                return_value={"pm-briefings": parent_entry},
            ),
            patch("models.reflection.Reflection.get_all_states", return_value=[state_parent]),
        ):
            rows = reflections_module.get_all_reflections()

        names = [r["name"] for r in rows]
        assert "pm-briefings" in names
        # No per-project rows because only the parent record exists
        assert len([n for n in names if n.startswith("pm-briefings-")]) == 0

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


# NOTE: TestReflectionModelExtension was removed as part of the unify-recurring-tasks
# migration. The legacy `Reflection.run_history` JSON field was replaced by the
# `ReflectionRun` model (one row per execution). Equivalent coverage now lives in
# tests/unit/test_reflection_model.py and tests/unit/test_reflection_run.py.
