"""Unit tests for bridge/hibernation.py.

Tests cover:
- is_auth_error(): classification of permanent vs transient errors
- enter_hibernation(): flag file creation, osascript notification
- exit_hibernation(): flag file deletion
- is_hibernating(): flag file presence check
- replay_buffered_output(): log parsing and replay
- _parse_log_file(): log format handling including malformed inputs
- Failure paths: missing data/ dir, unreadable files, None input, etc.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from bridge.hibernation import (
    AUTH_REQUIRED_FLAG,
    _parse_log_file,
    enter_hibernation,
    exit_hibernation,
    is_auth_error,
    is_hibernating,
    replay_buffered_output,
)


# ── is_auth_error ──────────────────────────────────────────────────────────


class TestIsAuthError:
    """Tests for the auth error classifier."""

    def test_none_returns_false(self):
        """None input must not raise TypeError."""
        assert is_auth_error(None) is False

    def test_generic_exception_returns_false(self):
        assert is_auth_error(ValueError("oops")) is False

    def test_connection_error_returns_false(self):
        """Transient network errors must NOT trigger hibernation."""
        assert is_auth_error(ConnectionError("timeout")) is False

    def test_os_error_returns_false(self):
        assert is_auth_error(OSError("no route to host")) is False

    def test_session_expired_returns_true(self):
        from telethon.errors import SessionExpiredError

        assert is_auth_error(SessionExpiredError(None)) is True

    def test_session_revoked_returns_true(self):
        from telethon.errors import SessionRevokedError

        assert is_auth_error(SessionRevokedError(None)) is True

    def test_auth_key_unregistered_returns_true(self):
        from telethon.errors import AuthKeyUnregisteredError

        assert is_auth_error(AuthKeyUnregisteredError(None)) is True

    def test_auth_key_error_returns_true(self):
        from telethon.errors import AuthKeyError

        assert is_auth_error(AuthKeyError(None, "auth key error")) is True

    def test_auth_key_invalid_returns_true(self):
        from telethon.errors import AuthKeyInvalidError

        assert is_auth_error(AuthKeyInvalidError(None)) is True

    def test_auth_key_perm_empty_returns_true(self):
        from telethon.errors import AuthKeyPermEmptyError

        assert is_auth_error(AuthKeyPermEmptyError(None)) is True

    def test_unauthorized_error_returns_true(self):
        from telethon.errors import UnauthorizedError

        assert is_auth_error(UnauthorizedError(None, "unauthorized")) is True


# ── enter_hibernation ──────────────────────────────────────────────────────


class TestEnterHibernation:
    """Tests for enter_hibernation() flag file creation and notification."""

    def test_creates_flag_file(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)
        monkeypatch.setattr("bridge.hibernation.subprocess.run", MagicMock())

        enter_hibernation()

        assert flag.exists()
        assert flag.read_text() == "auth-required"

    def test_flag_file_created_atomically(self, tmp_path, monkeypatch):
        """No .tmp file should remain after write."""
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)
        monkeypatch.setattr("bridge.hibernation.subprocess.run", MagicMock())

        enter_hibernation()

        tmp = flag.with_suffix(".tmp")
        assert not tmp.exists()

    def test_fires_osascript_notification(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)
        mock_run = MagicMock()
        monkeypatch.setattr("bridge.hibernation.subprocess.run", mock_run)

        enter_hibernation()

        assert mock_run.called
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"

    def test_notification_text_contains_command(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)
        mock_run = MagicMock()
        monkeypatch.setattr("bridge.hibernation.subprocess.run", mock_run)

        enter_hibernation()

        script_arg = mock_run.call_args[0][0][2]  # osascript -e <script>
        assert "telegram_login.py" in script_arg

    def test_missing_data_dir_logs_warning_not_raises(self, tmp_path, monkeypatch, caplog):
        """If data/ dir can't be created, log warning but don't raise."""
        flag = tmp_path / "nonexistent_parent" / "nonexistent_subdir" / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)
        monkeypatch.setattr("bridge.hibernation.subprocess.run", MagicMock())

        # Simulate mkdir failure
        with patch("bridge.hibernation.AUTH_REQUIRED_FLAG") as mock_flag:
            mock_flag.parent.mkdir.side_effect = OSError("permission denied")
            mock_flag.with_suffix.return_value.write_text.side_effect = OSError("write failed")
            mock_flag.__str__.return_value = str(flag)
            # Should not raise
            import logging

            with caplog.at_level(logging.WARNING, logger="bridge.hibernation"):
                enter_hibernation()
                # Warning should have been logged
                assert any("Failed to write flag file" in r.message for r in caplog.records)

    def test_osascript_failure_is_non_fatal(self, tmp_path, monkeypatch, caplog):
        """osascript failure must not raise; logged as warning."""
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)
        monkeypatch.setattr(
            "bridge.hibernation.subprocess.run",
            MagicMock(side_effect=FileNotFoundError("osascript not found")),
        )

        import logging

        with caplog.at_level(logging.WARNING, logger="bridge.hibernation"):
            enter_hibernation()  # Must not raise

        assert any("notification" in r.message.lower() for r in caplog.records)


# ── exit_hibernation ───────────────────────────────────────────────────────


class TestExitHibernation:
    """Tests for exit_hibernation() flag file cleanup."""

    def test_deletes_flag_file(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        flag.write_text("auth-required")
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)

        exit_hibernation()

        assert not flag.exists()

    def test_no_error_when_flag_missing(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)

        # Must not raise even when flag doesn't exist
        exit_hibernation()

    def test_oserror_logs_warning_not_raises(self, tmp_path, monkeypatch, caplog):
        flag = tmp_path / "bridge-auth-required"
        flag.write_text("auth-required")
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)

        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            import logging

            with caplog.at_level(logging.WARNING, logger="bridge.hibernation"):
                exit_hibernation()

            assert any("Failed to clear flag file" in r.message for r in caplog.records)


# ── is_hibernating ─────────────────────────────────────────────────────────


class TestIsHibernating:
    """Tests for is_hibernating() flag presence check."""

    def test_returns_true_when_flag_exists(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        flag.write_text("auth-required")
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)

        assert is_hibernating() is True

    def test_returns_false_when_flag_absent(self, tmp_path, monkeypatch):
        flag = tmp_path / "bridge-auth-required"
        monkeypatch.setattr("bridge.hibernation.AUTH_REQUIRED_FLAG", flag)

        assert is_hibernating() is False


# ── replay_buffered_output ─────────────────────────────────────────────────


class TestReplayBufferedOutput:
    """Tests for replay_buffered_output() log scanning and delivery."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_empty_logs_dir_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()

        result = self._run(replay_buffered_output(client))

        assert result == 0
        client.send_message.assert_not_called()

    def test_no_logs_dir_returns_zero(self, tmp_path, monkeypatch):
        missing = tmp_path / "does_not_exist"
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", missing)
        client = AsyncMock()

        result = self._run(replay_buffered_output(client))

        assert result == 0

    def test_replays_valid_log_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()
        client.send_message = AsyncMock()

        # Write a log file with an old mtime (not recent)
        log = tmp_path / "session-abc.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nHello from agent!\n---\n"
        )
        # Set mtime to 2 hours ago so it qualifies for replay
        old_mtime = time.time() - 7200
        import os

        os.utime(log, (old_mtime, old_mtime))

        result = self._run(replay_buffered_output(client))

        assert result == 1
        client.send_message.assert_called_once()
        call_args = client.send_message.call_args
        assert call_args[0][0] == 123456  # chat_id (first positional arg)
        assert "Hello from agent!" in call_args[0][1]  # message text (second positional arg)

    def test_skips_recent_log_file(self, tmp_path, monkeypatch):
        """Files modified < recency_skip_minutes ago are skipped."""
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()

        log = tmp_path / "session-recent.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nRecent output\n---\n"
        )
        # mtime is now — very recent

        result = self._run(replay_buffered_output(client))

        assert result == 0
        client.send_message.assert_not_called()

    def test_skips_old_log_file(self, tmp_path, monkeypatch):
        """Files older than max_age_hours are skipped."""
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()

        log = tmp_path / "session-old.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nOld output\n---\n"
        )
        # Set mtime to 30 hours ago
        import os

        old_mtime = time.time() - 30 * 3600
        os.utime(log, (old_mtime, old_mtime))

        result = self._run(replay_buffered_output(client))

        assert result == 0

    def test_skips_already_replayed_file(self, tmp_path, monkeypatch):
        """Files with .replayed marker are not re-delivered."""
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()

        log = tmp_path / "session-done.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nAlready sent\n---\n"
        )
        marker = tmp_path / "session-done.replayed"
        marker.write_text(str(time.time()))

        import os

        old_mtime = time.time() - 7200
        os.utime(log, (old_mtime, old_mtime))

        result = self._run(replay_buffered_output(client))

        assert result == 0

    def test_writes_replayed_marker_after_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()
        client.send_message = AsyncMock()

        log = tmp_path / "session-abc.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nHello!\n---\n"
        )
        import os

        old_mtime = time.time() - 7200
        os.utime(log, (old_mtime, old_mtime))

        self._run(replay_buffered_output(client))

        marker = tmp_path / "session-abc.replayed"
        assert marker.exists()

    def test_send_failure_per_entry_logged_warning(self, tmp_path, monkeypatch, caplog):
        """send_message failure is caught per entry, not fatal."""
        monkeypatch.setattr("bridge.hibernation._WORKER_LOGS_DIR", tmp_path)
        client = AsyncMock()
        client.send_message = AsyncMock(side_effect=Exception("network error"))

        log = tmp_path / "session-fail.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nHello!\n---\n"
        )
        import os

        old_mtime = time.time() - 7200
        os.utime(log, (old_mtime, old_mtime))

        import logging

        with caplog.at_level(logging.WARNING, logger="bridge.hibernation"):
            result = self._run(replay_buffered_output(client))

        assert result == 0
        assert any("Failed to replay entry" in r.message for r in caplog.records)


# ── _parse_log_file ────────────────────────────────────────────────────────


class TestParseLogFile:
    """Tests for the log file parser."""

    def test_parses_valid_entry(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=123456 reply_to=42\nHello world\n---\n"
        )
        entries = _parse_log_file(log)
        assert len(entries) == 1
        chat_id, reply_to, text, timestamp = entries[0]
        assert chat_id == "123456"
        assert reply_to == 42
        assert text == "Hello world"
        assert "10:00:00" in timestamp

    def test_parses_multiple_entries(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(
            "[2024-01-15 10:00:00] chat=111 reply_to=1\nFirst\n---\n"
            "[2024-01-15 10:01:00] chat=222 reply_to=2\nSecond\n---\n"
        )
        entries = _parse_log_file(log)
        assert len(entries) == 2

    def test_skips_reaction_entries(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(
            "[2024-01-15 10:00:00] REACTION chat=123 msg=42 emoji=👍\n"
        )
        entries = _parse_log_file(log)
        assert len(entries) == 0

    def test_handles_missing_reply_to(self, tmp_path):
        """reply_to=None is valid when field is absent or non-integer."""
        log = tmp_path / "test.log"
        log.write_text("[2024-01-15 10:00:00] chat=999 reply_to=None\nHello\n---\n")
        entries = _parse_log_file(log)
        assert len(entries) == 1
        _, reply_to, _, _ = entries[0]
        assert reply_to is None

    def test_skips_malformed_header(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("not a valid header\nsome text\n---\n")
        entries = _parse_log_file(log)
        assert len(entries) == 0

    def test_empty_file_returns_empty(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("")
        entries = _parse_log_file(log)
        assert len(entries) == 0

    def test_unreadable_file_logs_warning(self, tmp_path, caplog):
        log = tmp_path / "ghost.log"
        # File doesn't exist

        import logging

        with caplog.at_level(logging.WARNING, logger="bridge.hibernation"):
            entries = _parse_log_file(log)

        assert entries == []
        assert any("Cannot read log file" in r.message for r in caplog.records)

    def test_skips_block_without_chat(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("[2024-01-15 10:00:00] no-chat-field here\nHello\n---\n")
        entries = _parse_log_file(log)
        assert len(entries) == 0

    def test_skips_empty_text(self, tmp_path):
        """Entries with no body text are skipped."""
        log = tmp_path / "test.log"
        log.write_text("[2024-01-15 10:00:00] chat=123 reply_to=1\n\n---\n")
        entries = _parse_log_file(log)
        assert len(entries) == 0
