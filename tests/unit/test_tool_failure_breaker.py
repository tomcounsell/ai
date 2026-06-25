"""Unit tests for the tool-failure circuit breaker (issue #1413).

The breaker lives in ``agent/health_check.py`` and counts consecutive failed
tool calls per session, flagging the session ``watchdog_unhealthy`` once the
threshold is reached. These tests exercise ``_check_tool_failure_breaker`` and
``_is_tool_failure`` directly, mocking ``_set_unhealthy`` so no Redis is touched.

NOTE: This is distinct from ``test_circuit_breaker.py`` (a pre-existing
network-level breaker) and ``test_session_watchdog.py`` (a separate post-hoc
watchdog). Do not merge these files.
"""

import re
from unittest.mock import patch

import pytest

from agent import health_check
from agent.health_check import (
    CONSECUTIVE_FAILURE_THRESHOLD,
    _check_tool_failure_breaker,
    _consecutive_failures,
    _is_tool_failure,
    _recent_failures,
)


def _fail(tool_name: str = "Bash") -> dict:
    """A PostToolUse input_data dict representing a failed tool call."""
    return {"tool_name": tool_name, "tool_response": {"is_error": True}}


def _ok(tool_name: str = "Bash") -> dict:
    """A PostToolUse input_data dict representing a successful tool call."""
    return {"tool_name": tool_name, "tool_response": {"is_error": False}}


@pytest.fixture(autouse=True)
def _clear_state():
    """Reset module-level breaker state before and after each test."""
    _consecutive_failures.clear()
    _recent_failures.clear()
    yield
    _consecutive_failures.clear()
    _recent_failures.clear()


class TestIsToolFailure:
    def test_dict_is_error_true_is_failure(self):
        assert _is_tool_failure({"is_error": True}) is True

    def test_dict_is_error_false_is_success(self):
        assert _is_tool_failure({"is_error": False}) is False

    def test_dict_without_is_error_is_success(self):
        assert _is_tool_failure({"content": "ok"}) is False

    def test_string_error_prefix_is_failure(self):
        assert _is_tool_failure("Error: file not found") is True

    def test_string_without_error_prefix_is_success(self):
        assert _is_tool_failure("all good") is False

    @pytest.mark.parametrize("value", [None, [], {}, 123, 0, "", ["Error: x"]])
    def test_malformed_shapes_are_success(self, value):
        assert _is_tool_failure(value) is False


class TestConsecutiveFailureBreaker:
    def test_five_consecutive_failures_trips_breaker(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                _check_tool_failure_breaker("sess-1", _fail("Bash"))
        mock_unhealthy.assert_called_once()
        session_id, reason = mock_unhealthy.call_args.args
        assert session_id == "sess-1"
        assert re.search(
            r"5 consecutive tool failures \(.*\) — strategy reassessment required", reason
        )

    def test_reason_includes_recent_tool_names_in_order(self):
        tools = ["Bash", "Bash", "Edit", "Read", "Bash"]
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for t in tools:
                _check_tool_failure_breaker("sess-2", _fail(t))
        _, reason = mock_unhealthy.call_args.args
        assert "(Bash, Bash, Edit, Read, Bash)" in reason

    def test_single_success_resets_counter_and_ring(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(4):
                _check_tool_failure_breaker("sess-3", _fail("Bash"))
            _check_tool_failure_breaker("sess-3", _ok("Bash"))  # reset
            for _ in range(4):
                _check_tool_failure_breaker("sess-3", _fail("Bash"))
        mock_unhealthy.assert_not_called()
        assert _consecutive_failures["sess-3"] == 4
        # Ring holds only the 4 post-reset failures (success cleared it).
        assert len(_recent_failures["sess-3"]) == 4

    def test_interleaved_success_failure_does_not_trip(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(10):
                _check_tool_failure_breaker("sess-4", _fail("Bash"))
                _check_tool_failure_breaker("sess-4", _ok("Bash"))
        mock_unhealthy.assert_not_called()

    def test_counter_resets_after_firing_so_breaker_re_fires(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            # First 5 trip the breaker.
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                _check_tool_failure_breaker("sess-5", _fail("Bash"))
            assert mock_unhealthy.call_count == 1
            # Counter reset to 0 — a 6th failure does NOT immediately re-trip.
            _check_tool_failure_breaker("sess-5", _fail("Bash"))
            assert mock_unhealthy.call_count == 1
            # 5 more consecutive failures re-fire.
            for _ in range(4):
                _check_tool_failure_breaker("sess-5", _fail("Bash"))
            assert mock_unhealthy.call_count == 2

    def test_ring_respects_maxlen_only_last_five_tools(self):
        tools = ["T1", "T2", "T3", "T4", "T5", "T6", "T7"]
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for t in tools:
                _check_tool_failure_breaker("sess-6", _fail(t))
        # Threshold is 5, so the breaker fires on the 5th failure (T5), then
        # resets. T6, T7 begin a new streak. Assert the fired reason carried
        # only the last 5 of the first streak.
        _, reason = mock_unhealthy.call_args_list[0].args
        assert "(T1, T2, T3, T4, T5)" in reason
        # After firing, ring cleared; only T6, T7 remain.
        assert list(_recent_failures["sess-6"]) == ["T6", "T7"]

    def test_string_error_response_counts_as_failure(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                _check_tool_failure_breaker(
                    "sess-7",
                    {"tool_name": "Bash", "tool_response": "Error: boom"},
                )
        mock_unhealthy.assert_called_once()

    @pytest.mark.parametrize("response", [None, {}, [], 123, {"content": "ok"}])
    def test_malformed_response_never_increments(self, response):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(10):
                _check_tool_failure_breaker(
                    "sess-8",
                    {"tool_name": "Bash", "tool_response": response},
                )
        mock_unhealthy.assert_not_called()
        assert _consecutive_failures.get("sess-8", 0) == 0

    def test_missing_session_id_is_noop(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                _check_tool_failure_breaker("", _fail("Bash"))
        mock_unhealthy.assert_not_called()
        assert "" not in _consecutive_failures

    def test_per_session_isolation(self):
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(4):
                _check_tool_failure_breaker("sess-a", _fail("Bash"))
            for _ in range(4):
                _check_tool_failure_breaker("sess-b", _fail("Edit"))
        mock_unhealthy.assert_not_called()
        assert _consecutive_failures["sess-a"] == 4
        assert _consecutive_failures["sess-b"] == 4

    def test_reason_format_distinct_from_haiku_watchdog(self):
        """The breaker reason must match a regex dashboards can attribute to it,
        distinguishing it from the Haiku watchdog's prose reason (both write the
        same single, latching ``watchdog_unhealthy`` field)."""
        with patch.object(health_check, "_set_unhealthy") as mock_unhealthy:
            for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
                _check_tool_failure_breaker("sess-9", _fail("Bash"))
        _, reason = mock_unhealthy.call_args.args
        assert re.match(r"\d+ consecutive tool failures", reason)
