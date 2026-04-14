"""Unit tests for tools.sdlc_session_ensure.

Tests cover:
- Creates session when none exists
- Returns existing session (idempotent)
- Handles Redis errors gracefully
- CLI output format
- Invalid input handling
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEnsureSession:
    """Tests for the ensure_session function."""

    def test_returns_existing_session_by_issue(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_session = MagicMock()
        mock_session.session_id = "sdlc-local-941"

        with patch("tools._sdlc_utils.find_session_by_issue", return_value=mock_session):
            result = ensure_session(issue_number=941)

        assert result == {"session_id": "sdlc-local-941", "created": False}

    def test_creates_new_session(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-942"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(
                issue_number=942,
                issue_url="https://github.com/tomcounsell/ai/issues/942",
            )

        assert result == {"session_id": "sdlc-local-942", "created": True}
        mock_as.create_local.assert_called_once()

    def test_idempotent_by_session_id(self):
        """If a session with sdlc-local-{N} already exists, return it."""
        from tools.sdlc_session_ensure import ensure_session

        mock_existing = MagicMock()
        mock_existing.session_id = "sdlc-local-943"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_existing]

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=943)

        assert result == {"session_id": "sdlc-local-943", "created": False}

    def test_returns_empty_for_invalid_issue_number(self):
        from tools.sdlc_session_ensure import ensure_session

        assert ensure_session(issue_number=0) == {}
        assert ensure_session(issue_number=-1) == {}

    def test_handles_redis_error_gracefully(self):
        from tools.sdlc_session_ensure import ensure_session

        with patch(
            "tools._sdlc_utils.find_session_by_issue",
            side_effect=ConnectionError("Redis down"),
        ):
            result = ensure_session(issue_number=941)

        assert result == {}

    def test_transition_status_failure_still_returns_session(self):
        """Session is usable even if transition_status fails."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-944"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.transition_status",
                side_effect=RuntimeError("transition failed"),
            ),
        ):
            result = ensure_session(issue_number=944)

        assert result == {"session_id": "sdlc-local-944", "created": True}


class TestCLI:
    """Tests for CLI invocation."""

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_session_ensure", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "--issue-number" in result.stdout
        assert "--issue-url" in result.stdout

    def test_missing_required_arg(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_session_ensure"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode != 0
