"""Tests for PM session resume hydration (#874).

Validates that resumed PM sessions get a <resumed-session-context> block
prepended to message_text, and that non-PM sessions, first starts, and
sessions with falsy working_dir are correctly skipped.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id="test-session-123",
    session_type="pm",
    working_dir="/tmp/fake-worktree",
    message_text="original message",
):
    """Create a minimal mock AgentSession for hydration tests."""
    session = MagicMock()
    session.id = "abc123"
    session.session_id = session_id
    session.session_type = session_type
    session.working_dir = working_dir
    session.message_text = message_text
    session.async_save = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# _get_git_summary log_depth tests
# ---------------------------------------------------------------------------


class TestGetGitSummaryLogDepth:
    """Verify that log_depth controls the git log --oneline -N argument."""

    @patch("subprocess.run")
    def test_default_depth_is_3(self, mock_run):
        """Default log_depth=3 produces git log --oneline -3."""
        from agent.session_logs import _get_git_summary

        mock_run.return_value = MagicMock(returncode=0, stdout="clean", stderr="")
        _get_git_summary(working_dir="/tmp/test")

        # Find the git log call
        log_calls = [c for c in mock_run.call_args_list if "log" in str(c)]
        assert len(log_calls) == 1
        assert "-3" in log_calls[0].args[0]

    @patch("subprocess.run")
    def test_custom_depth_10(self, mock_run):
        """log_depth=10 produces git log --oneline -10."""
        from agent.session_logs import _get_git_summary

        mock_run.return_value = MagicMock(returncode=0, stdout="clean", stderr="")
        _get_git_summary(working_dir="/tmp/test", log_depth=10)

        log_calls = [c for c in mock_run.call_args_list if "log" in str(c)]
        assert len(log_calls) == 1
        assert "-10" in log_calls[0].args[0]

    @patch("subprocess.run")
    def test_depth_appears_in_output(self, mock_run):
        """Output includes commit lines from the git log call."""
        from agent.session_logs import _get_git_summary

        def side_effect(cmd, **kwargs):
            if "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "log" in cmd:
                commits = "\n".join(f"abc{i} commit {i}" for i in range(5))
                return MagicMock(returncode=0, stdout=commits, stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = _get_git_summary(working_dir="/tmp/test", log_depth=5)
        assert "abc0 commit 0" in result
        assert "abc4 commit 4" in result


# ---------------------------------------------------------------------------
# _maybe_inject_resume_hydration tests
# ---------------------------------------------------------------------------


class TestMaybeInjectResumeHydration:
    """Test the resume hydration helper."""

    def _run(self, session, worker_key="test-worker"):
        """Run the async helper synchronously."""
        from agent.agent_session_queue import _maybe_inject_resume_hydration

        asyncio.run(_maybe_inject_resume_hydration(session, worker_key))

    # -- Session type gating --

    def test_dev_session_skipped(self, tmp_path):
        """Dev sessions never receive hydration regardless of resume state."""
        session = _make_session(session_type="dev")
        self._run(session)
        # message_text unchanged
        assert session.message_text == "original message"
        session.async_save.assert_not_called()

    def test_teammate_session_skipped(self, tmp_path):
        """Teammate sessions never receive hydration."""
        session = _make_session(session_type="teammate")
        self._run(session)
        assert session.message_text == "original message"
        session.async_save.assert_not_called()

    # -- Working dir guard --

    def test_falsy_working_dir_none(self):
        """PM session with working_dir=None is skipped."""
        session = _make_session(working_dir=None)
        self._run(session)
        assert session.message_text == "original message"
        session.async_save.assert_not_called()

    def test_falsy_working_dir_empty_string(self):
        """PM session with working_dir='' is skipped."""
        session = _make_session(working_dir="")
        self._run(session)
        assert session.message_text == "original message"
        session.async_save.assert_not_called()

    # -- Resume detection threshold --

    def test_no_resume_files_skipped(self, tmp_path):
        """Session with 0 resume files (no log dir) is skipped."""
        # session_id dir does not exist under tmp_path
        with patch("agent.session_logs.SESSION_LOGS_DIR", tmp_path):
            session = _make_session()
            self._run(session)
        assert session.message_text == "original message"
        session.async_save.assert_not_called()

    def test_one_resume_file_skipped(self, tmp_path):
        """Session with exactly 1 resume file (first start only) is skipped."""
        session_dir = tmp_path / "test-session-123"
        session_dir.mkdir()
        (session_dir / "1234567890_resume.json").write_text("{}")

        with patch("agent.session_logs.SESSION_LOGS_DIR", tmp_path):
            session = _make_session()
            self._run(session)

        assert session.message_text == "original message"
        session.async_save.assert_not_called()

    def test_two_resume_files_triggers_hydration(self, tmp_path):
        """Session with 2+ resume files gets hydration prepended."""
        session_dir = tmp_path / "test-session-123"
        session_dir.mkdir()
        (session_dir / "1234567890_resume.json").write_text("{}")
        (session_dir / "1234567891_resume.json").write_text("{}")

        git_summary = "Recent commits:\nabc123 Fix bug\ndef456 Add feature"

        with (
            patch("agent.session_logs.SESSION_LOGS_DIR", tmp_path),
            patch(
                "agent.session_logs._get_git_summary",
                return_value=git_summary,
            ) as mock_git,
        ):
            session = _make_session()
            self._run(session)

        # Hydration was injected
        assert "<resumed-session-context>" in session.message_text
        assert "abc123 Fix bug" in session.message_text
        assert "original message" in session.message_text
        session.async_save.assert_called_once()

        # log_depth=10 was passed
        mock_git.assert_called_once_with(working_dir="/tmp/fake-worktree", log_depth=10)

    def test_hydration_prepends_before_original(self, tmp_path):
        """Hydration block appears BEFORE the original message."""
        session_dir = tmp_path / "test-session-123"
        session_dir.mkdir()
        (session_dir / "1234567890_resume.json").write_text("{}")
        (session_dir / "1234567891_resume.json").write_text("{}")

        with (
            patch("agent.session_logs.SESSION_LOGS_DIR", tmp_path),
            patch(
                "agent.session_logs._get_git_summary",
                return_value="commits here",
            ),
        ):
            session = _make_session(message_text="do the next stage")
            self._run(session)

        # Hydration comes first
        hydration_pos = session.message_text.index("<resumed-session-context>")
        original_pos = session.message_text.index("do the next stage")
        assert hydration_pos < original_pos

    # -- Silent failure --

    def test_git_summary_exception_does_not_crash(self, tmp_path):
        """If _get_git_summary raises, helper returns without crashing."""
        session_dir = tmp_path / "test-session-123"
        session_dir.mkdir()
        (session_dir / "1234567890_resume.json").write_text("{}")
        (session_dir / "1234567891_resume.json").write_text("{}")

        with (
            patch("agent.session_logs.SESSION_LOGS_DIR", tmp_path),
            patch(
                "agent.session_logs._get_git_summary",
                side_effect=RuntimeError("git broke"),
            ),
        ):
            session = _make_session()
            # Should not raise
            self._run(session)

        # message_text unchanged on failure
        assert session.message_text == "original message"

    # -- Glob pattern match --

    def test_glob_matches_actual_filename_format(self, tmp_path):
        """The *_resume.json glob matches the format save_session_snapshot uses."""
        session_dir = tmp_path / "test-session-123"
        session_dir.mkdir()
        # Actual format: {int_timestamp}_{event}.json
        (session_dir / "1712745600_resume.json").write_text("{}")
        (session_dir / "1712745700_resume.json").write_text("{}")
        # Non-resume files should NOT match
        (session_dir / "1712745800_complete.json").write_text("{}")
        (session_dir / "1712745900_error.json").write_text("{}")

        matches = list(session_dir.glob("*_resume.json"))
        assert len(matches) == 2
        assert all("resume" in m.name for m in matches)

    def test_three_resume_files_triggers_hydration(self, tmp_path):
        """Session with 3 resume files (multiple resumes) also gets hydration."""
        session_dir = tmp_path / "test-session-123"
        session_dir.mkdir()
        for i in range(3):
            (session_dir / f"123456789{i}_resume.json").write_text("{}")

        with (
            patch("agent.session_logs.SESSION_LOGS_DIR", tmp_path),
            patch(
                "agent.session_logs._get_git_summary",
                return_value="commits",
            ),
        ):
            session = _make_session()
            self._run(session)

        assert "<resumed-session-context>" in session.message_text
