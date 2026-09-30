"""Tests for the escape hatch tool."""

import pytest

from bridge.escape_hatch import (
    HUMAN_INPUT_MARKER,
    clear_pending_request,
    get_pending_request,
    has_pending_request,
    is_human_input_required,
    request_human_input,
)


class TestRequestHumanInput:
    """Tests for the request_human_input function."""

    def setup_method(self):
        """Clear any pending requests before each test."""
        clear_pending_request()

    def test_basic_request_formats_correctly(self):
        """Test that a basic request without options formats correctly."""
        result = request_human_input("I need clarification on the API endpoint")

        assert result.startswith(HUMAN_INPUT_MARKER)
        assert "I need clarification on the API endpoint" in result
        assert "Human Input Needed:" in result

    def test_request_with_options_formats_correctly(self):
        """Test that a request with options includes them in the output."""
        result = request_human_input(
            "Which database should I use?",
            options=["PostgreSQL", "SQLite", "MySQL"],
        )

        assert result.startswith(HUMAN_INPUT_MARKER)
        assert "Which database should I use?" in result
        assert "Options:" in result
        assert "1. PostgreSQL" in result
        assert "2. SQLite" in result
        assert "3. MySQL" in result

    def test_request_with_empty_options_list(self):
        """Test that empty options list is handled gracefully."""
        result = request_human_input("Need help", options=[])

        assert result.startswith(HUMAN_INPUT_MARKER)
        assert "Need help" in result
        assert "Options:" not in result

    def test_request_with_whitespace_only_options(self):
        """Test that whitespace-only options are filtered out."""
        result = request_human_input(
            "Choose one",
            options=["Valid", "  ", "", "Also Valid"],
        )

        assert "1. Valid" in result
        assert "2. Also Valid" in result
        # Should only have 2 numbered options
        assert "3." not in result

    def test_empty_reason_raises_error(self):
        """Test that an empty reason raises ValueError."""
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            request_human_input("")

    def test_whitespace_only_reason_raises_error(self):
        """Test that a whitespace-only reason raises ValueError."""
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            request_human_input("   \t\n  ")

    def test_none_reason_raises_error(self):
        """Test that None reason raises ValueError."""
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            request_human_input(None)  # type: ignore

    def test_reason_is_stripped(self):
        """Test that leading/trailing whitespace in reason is stripped."""
        result = request_human_input("  trimmed reason  ")

        assert "trimmed reason" in result
        assert "  trimmed reason  " not in result


class TestPendingRequestTracking:
    """Tests for pending request tracking functions."""

    def setup_method(self):
        """Clear any pending requests before each test."""
        clear_pending_request()

    def test_no_pending_request_initially(self):
        """Test that there's no pending request before any are made."""
        assert not has_pending_request()
        assert get_pending_request() is None

    def test_has_pending_request_after_call(self):
        """Test that a pending request is tracked after request_human_input."""
        request_human_input("Test reason")

        assert has_pending_request()

    def test_get_pending_request_returns_details(self):
        """Test that get_pending_request returns the request details."""
        request_human_input("Test reason", options=["A", "B"])

        pending = get_pending_request()
        assert pending is not None
        assert pending["reason"] == "Test reason"
        assert pending["options"] == ["A", "B"]
        assert "timestamp" in pending
        assert "formatted_message" in pending

    def test_clear_pending_request(self):
        """Test that clear_pending_request removes the pending request."""
        request_human_input("Test reason")
        assert has_pending_request()

        clear_pending_request()
        assert not has_pending_request()
        assert get_pending_request() is None

    def test_subsequent_request_overwrites_previous(self):
        """Test that a new request overwrites the previous one."""
        request_human_input("First reason")
        request_human_input("Second reason")

        pending = get_pending_request()
        assert pending is not None
        assert pending["reason"] == "Second reason"


class TestIsHumanInputRequired:
    """Tests for the is_human_input_required function."""

    def test_detects_marker_at_start(self):
        """Test that the marker is detected at the start of a message."""
        message = f"{HUMAN_INPUT_MARKER}\nSome content"
        assert is_human_input_required(message)

    def test_detects_marker_with_leading_whitespace(self):
        """Test that the marker is detected even with leading whitespace."""
        message = f"  \n{HUMAN_INPUT_MARKER}\nSome content"
        assert is_human_input_required(message)

    def test_returns_false_for_regular_message(self):
        """Test that regular messages return False."""
        assert not is_human_input_required("Just a normal message")
        assert not is_human_input_required("Question: what should I do?")

    def test_returns_false_for_empty_message(self):
        """Test that empty messages return False."""
        assert not is_human_input_required("")
        assert not is_human_input_required(None)  # type: ignore

    def test_returns_false_for_marker_in_middle(self):
        """Test that marker in the middle of message returns False."""
        message = f"Some text before {HUMAN_INPUT_MARKER}"
        assert not is_human_input_required(message)
