"""Tests for the unified valor-telegram CLI tool."""

import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

# Import the module under test
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from tools.valor_telegram import format_timestamp, parse_since, resolve_chat


class TestParseSince:
    """Test relative time parsing."""

    def test_hours_ago(self):
        result = parse_since("1 hour ago")
        assert result is not None
        expected = datetime.now() - timedelta(hours=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_multiple_hours(self):
        result = parse_since("3 hours ago")
        assert result is not None
        expected = datetime.now() - timedelta(hours=3)
        assert abs((result - expected).total_seconds()) < 2

    def test_minutes_ago(self):
        result = parse_since("30 minutes ago")
        assert result is not None
        expected = datetime.now() - timedelta(minutes=30)
        assert abs((result - expected).total_seconds()) < 2

    def test_days_ago(self):
        result = parse_since("2 days ago")
        assert result is not None
        expected = datetime.now() - timedelta(days=2)
        assert abs((result - expected).total_seconds()) < 2

    def test_weeks_ago(self):
        result = parse_since("1 week ago")
        assert result is not None
        expected = datetime.now() - timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_invalid_input(self):
        assert parse_since("not a time") is None
        assert parse_since("") is None
        assert parse_since("yesterday") is None

    def test_case_insensitive(self):
        result = parse_since("2 Hours Ago")
        assert result is not None

    def test_singular_plural(self):
        result1 = parse_since("1 minute ago")
        result2 = parse_since("1 minutes ago")
        assert result1 is not None
        assert result2 is not None


class TestResolveChat:
    """Test chat name resolution."""

    @patch("tools.telegram_history.resolve_chat_id", return_value="-123456")
    def test_resolves_from_history(self, mock_resolve):
        result = resolve_chat("Dev: Valor")
        assert result == "-123456"
        mock_resolve.assert_called_once_with("Dev: Valor")

    def test_returns_none_for_unknown(self):
        result = resolve_chat("nonexistent_chat_xyz_12345")
        assert result is None


class TestFormatTimestamp:
    """Test timestamp formatting."""

    def test_valid_iso_timestamp(self):
        result = format_timestamp("2026-02-14T10:30:00")
        assert result == "2026-02-14 10:30"

    def test_none_input(self):
        assert format_timestamp(None) == "unknown"

    def test_invalid_timestamp(self):
        result = format_timestamp("not-a-date")
        assert isinstance(result, str)
        assert len(result) > 0


class TestCLIParsing:
    """Test CLI argument parsing."""

    def test_read_help(self):
        """Verify read subcommand parses without error."""
        from tools.valor_telegram import main

        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["valor-telegram", "read", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_send_help(self):
        """Verify send subcommand parses without error."""
        from tools.valor_telegram import main

        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["valor-telegram", "send", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_chats_help(self):
        """Verify chats subcommand parses without error."""
        from tools.valor_telegram import main

        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["valor-telegram", "chats", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_no_command_shows_help(self):
        """No subcommand returns error code."""
        from tools.valor_telegram import main

        sys.argv = ["valor-telegram"]
        result = main()
        assert result == 1
