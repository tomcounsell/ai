"""Unit tests for maybe_send_revival_prompt helper in agent_session_queue."""

from unittest.mock import patch

from agent.agent_session_queue import maybe_send_revival_prompt


class TestMaybeSendRevivalPrompt:
    """Tests for the maybe_send_revival_prompt helper."""

    def test_returns_none_for_empty_project_key(self):
        """Returns None immediately when project_key is empty."""
        result = maybe_send_revival_prompt("", "/some/path", "chat123")
        assert result is None

    def test_returns_none_for_empty_working_dir(self):
        """Returns None immediately when working_dir is empty."""
        result = maybe_send_revival_prompt("my_project", "", "chat123")
        assert result is None

    def test_returns_none_for_both_empty(self):
        """Returns None when both project_key and working_dir are empty."""
        result = maybe_send_revival_prompt("", "", "chat123")
        assert result is None

    def test_calls_check_revival_with_correct_args(self):
        """Delegates to check_revival with all three arguments."""
        with patch("agent.agent_session_queue.check_revival", return_value=None) as mock_check:
            maybe_send_revival_prompt("proj", "/work/dir", "chat456")
            mock_check.assert_called_once_with("proj", "/work/dir", "chat456")

    def test_records_cooldown_when_revival_found(self):
        """Records cooldown when check_revival returns revival info."""
        revival_info = {"branch": "session/my-feature", "project_key": "proj"}
        with (
            patch("agent.agent_session_queue.check_revival", return_value=revival_info),
            patch("agent.agent_session_queue.record_revival_cooldown") as mock_cooldown,
        ):
            result = maybe_send_revival_prompt("proj", "/work/dir", "chat789")
            assert result == revival_info
            mock_cooldown.assert_called_once_with("chat789")

    def test_does_not_record_cooldown_when_no_revival(self):
        """Does not record cooldown when check_revival returns None."""
        with (
            patch("agent.agent_session_queue.check_revival", return_value=None),
            patch("agent.agent_session_queue.record_revival_cooldown") as mock_cooldown,
        ):
            result = maybe_send_revival_prompt("proj", "/work/dir", "chat000")
            assert result is None
            mock_cooldown.assert_not_called()

    def test_returns_revival_info_dict(self):
        """Passes through the revival_info dict from check_revival unchanged."""
        revival_info = {
            "branch": "session/fix-thing",
            "project_key": "myproject",
            "session_id": "sess-abc",
            "working_dir": "/work/dir",
        }
        with (
            patch("agent.agent_session_queue.check_revival", return_value=revival_info),
            patch("agent.agent_session_queue.record_revival_cooldown"),
        ):
            result = maybe_send_revival_prompt("myproject", "/work/dir", "chatXYZ")
            assert result is revival_info
