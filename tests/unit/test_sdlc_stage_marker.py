"""Unit tests for tools.sdlc_stage_marker.

Tests cover:
- _find_session with --issue-number fallback
- write_marker with issue-number resolution
- CLI --issue-number argument parsing
- Backward compatibility (env var path still works)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFindSession:
    """Tests for _find_session resolution order."""

    def test_resolves_by_session_id_arg(self):
        from tools.sdlc_stage_marker import _find_session

        mock_session = MagicMock()
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]
        mock_session.session_type = "pm"

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session("explicit-id")

        assert result == mock_session

    def test_resolves_by_env_var(self):
        from tools.sdlc_stage_marker import _find_session

        mock_session = MagicMock()
        mock_session.session_type = "pm"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with (
            patch.dict(os.environ, {"VALOR_SESSION_ID": "env-session-id"}),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = _find_session(None)

        assert result == mock_session

    def test_resolves_by_issue_number_when_no_env(self):
        from tools.sdlc_stage_marker import _find_session

        mock_session = MagicMock()

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=mock_session),
        ):
            result = _find_session(None, issue_number=941)

        assert result == mock_session

    def test_returns_none_when_nothing_available(self):
        from tools.sdlc_stage_marker import _find_session

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with patch.dict(os.environ, clean_env, clear=True):
            result = _find_session(None)

        assert result is None

    def test_issue_number_lookup_handles_exception(self):
        from tools.sdlc_stage_marker import _find_session

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch(
                "tools._sdlc_utils.find_session_by_issue",
                side_effect=ConnectionError("Redis down"),
            ),
        ):
            result = _find_session(None, issue_number=941)

        assert result is None


class TestWriteMarker:
    """Tests for write_marker function."""

    def test_rejects_invalid_stage(self):
        from tools.sdlc_stage_marker import write_marker

        result = write_marker(stage="BOGUS", status="completed")
        assert result == {}

    def test_rejects_invalid_status(self):
        from tools.sdlc_stage_marker import write_marker

        result = write_marker(stage="PLAN", status="bogus")
        assert result == {}

    def test_returns_empty_when_no_session(self):
        from tools.sdlc_stage_marker import write_marker

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with patch.dict(os.environ, clean_env, clear=True):
            result = write_marker(stage="PLAN", status="completed")

        assert result == {}

    def test_passes_issue_number_to_find_session(self):
        from tools.sdlc_stage_marker import write_marker

        mock_session = MagicMock()
        mock_session.stage_states = "{}"

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine") as mock_psm_cls,
        ):
            mock_psm = MagicMock()
            mock_psm.set_stage_status.return_value = True
            mock_psm_cls.return_value = mock_psm

            result = write_marker(stage="PLAN", status="completed", issue_number=941)

        assert result == {"stage": "PLAN", "status": "completed"}


class TestCLI:
    """Tests for CLI argument parsing."""

    def test_help_shows_issue_number(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_marker", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "--issue-number" in result.stdout

    def test_no_args_exits_with_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_marker"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        # Missing required --stage and --status
        assert result.returncode != 0

    def test_with_issue_number_outputs_json(self):
        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_stage_marker",
                "--stage",
                "PLAN",
                "--status",
                "completed",
                "--issue-number",
                "99999",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=clean_env,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        # Output depends on Redis state: {} if no session for issue 99999,
        # or {"stage": "PLAN", "status": "completed"} if one exists.
        # Both are valid — the test verifies CLI accepts --issue-number
        # and produces well-formed JSON output.
        assert output == {} or output == {"stage": "PLAN", "status": "completed"}
