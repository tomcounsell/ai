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
    """Tests for write_marker's tri-state degradation contract (D7).

    write_marker now returns ``(result, exit_code)``:
    - degraded / success / idempotent → exit 0
    - genuine write failure (substrate present, session resolved) → exit 1
    """

    def test_rejects_invalid_stage(self):
        from tools.sdlc_stage_marker import write_marker

        result, code = write_marker(stage="BOGUS", status="completed")
        assert result == {}
        assert code == 0

    def test_rejects_invalid_status(self):
        from tools.sdlc_stage_marker import write_marker

        result, code = write_marker(stage="PLAN", status="bogus")
        assert result == {}
        assert code == 0

    def test_absent_substrate_emits_degraded_marker_exit_0(self):
        """ABSENT: substrate probe fails → degraded marker, exit 0 (non-`ai` repo)."""
        from tools.sdlc_stage_marker import SUBSTRATE_ABSENT, write_marker

        with patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_ABSENT):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result["status"] == "degraded"
        assert "substrate absent" in result["reason"]
        assert result["stage"] == "PLAN"

    def test_present_no_session_emits_degraded_marker_exit_0_quiet(self):
        """PRESENT_NO_SESSION: substrate present but no session → degraded, exit 0, quiet."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker._find_session", return_value=None),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result["status"] == "degraded"
        assert "no PM session" in result["reason"]

    def test_present_write_failed_exits_1_loud(self):
        """PRESENT_WRITE_FAILED: session resolved but state-machine raises → exit 1."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker._find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", side_effect=RuntimeError("boom")),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 1
        assert result == {}

    def test_present_start_stage_rejected_exits_1(self):
        """PRESENT_WRITE_FAILED: start_stage raising ValueError (misorder) → exit 1."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.start_stage.side_effect = ValueError("predecessor not completed")

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker._find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="REVIEW", status="in_progress")

        assert code == 1
        assert result == {}

    def test_idempotent_already_completed_exit_0(self):
        """Idempotent already-completed path stays exit 0 (not loud)."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "completed"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker._find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_sm.complete_stage.assert_not_called()

    def test_successful_write_exit_0(self):
        """Happy path: session resolved, write succeeds → exit 0 + marker."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker._find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=941)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("PLAN")


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
        # Output depends on substrate state (D7 tri-state contract):
        # - degraded marker if the substrate is absent or no session resolves
        #   for issue 99999 (the common case in CI),
        # - {"stage": "PLAN", "status": "completed"} if a session happens to
        #   exist. All are valid — the test verifies the CLI accepts
        #   --issue-number and produces well-formed JSON with exit 0.
        assert (
            output.get("status") == "degraded"
            or output == {"stage": "PLAN", "status": "completed"}
        )
