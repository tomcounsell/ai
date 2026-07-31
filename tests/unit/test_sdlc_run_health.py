"""Unit tests for tools.sdlc_run_health (issue #2451).

run-health reports a per-run marker-write disposition:
- clean               -- zero fail writes (or no counters at all)
- transient_recovered -- fail writes occurred but the trail landed the stage
- never_landed        -- fail writes occurred and the trail is missing the stage
"""

from __future__ import annotations

from unittest.mock import patch


class TestRunHealthDisposition:
    def test_clean_when_no_failures(self):
        from tools.sdlc_run_health import run_health

        counters = {"ok_writes": 4, "fail_writes": 0, "last_failed_stage": None}
        with patch("tools._sdlc_marker_telemetry.read_marker_counters", return_value=counters):
            result = run_health(2451, "run-1")

        assert result["disposition"] == "clean"
        assert result["ok_writes"] == 4
        assert result["fail_writes"] == 0
        assert result["trail_complete"] is True

    def test_transient_recovered_when_failures_but_trail_complete(self):
        from tools.sdlc_run_health import run_health

        counters = {"ok_writes": 3, "fail_writes": 2, "last_failed_stage": "DOCS"}
        with (
            patch("tools._sdlc_marker_telemetry.read_marker_counters", return_value=counters),
            patch(
                "tools.sdlc_stage_query.query_stage_states",
                return_value={"DOCS": "completed"},
            ),
        ):
            result = run_health(2451, "run-1")

        assert result["disposition"] == "transient_recovered"
        assert result["fail_writes"] == 2
        assert result["trail_complete"] is True

    def test_never_landed_when_failures_and_trail_missing_stage(self):
        from tools.sdlc_run_health import run_health

        counters = {"ok_writes": 1, "fail_writes": 3, "last_failed_stage": "DOCS"}
        with (
            patch("tools._sdlc_marker_telemetry.read_marker_counters", return_value=counters),
            patch(
                "tools.sdlc_stage_query.query_stage_states",
                return_value={"DOCS": "in_progress"},  # never completed
            ),
        ):
            result = run_health(2451, "run-1")

        assert result["disposition"] == "never_landed"
        assert result["trail_complete"] is False

    def test_no_counters_is_clean_zero_counts(self):
        from tools.sdlc_run_health import run_health

        counters = {"ok_writes": 0, "fail_writes": 0, "last_failed_stage": None}
        with patch("tools._sdlc_marker_telemetry.read_marker_counters", return_value=counters):
            result = run_health(2451, "run-unknown")

        assert result["disposition"] == "clean"
        assert result["ok_writes"] == 0
        assert result["fail_writes"] == 0

    def test_stage_query_error_treats_trail_incomplete(self):
        from tools.sdlc_run_health import run_health

        counters = {"ok_writes": 0, "fail_writes": 1, "last_failed_stage": "TEST"}
        with (
            patch("tools._sdlc_marker_telemetry.read_marker_counters", return_value=counters),
            patch(
                "tools.sdlc_stage_query.query_stage_states",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            result = run_health(2451, "run-1")

        assert result["disposition"] == "never_landed"
        assert result["trail_complete"] is False
