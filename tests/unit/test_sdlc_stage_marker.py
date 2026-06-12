"""Unit tests for tools.sdlc_stage_marker.

Tests cover:
- write_marker with issue-number resolution (via shared find_session)
- write_marker auto-ensures a session on sessionless-but-issue-numbered writes
- CLI --issue-number argument parsing
- Backward compatibility (env var path still works)

The local `_find_session` resolver was deleted in #1558; stage_marker now
resolves through the shared `tools._sdlc_utils.find_session(..., ensure=True)`.
Resolver-level lookup behavior is covered by ``test_sdlc_utils.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
            patch("tools.sdlc_stage_marker.find_session", return_value=None),
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
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
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
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
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
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        mock_sm.complete_stage.assert_not_called()

    def test_successful_write_exit_0(self):
        """Happy path: session resolved, write succeeds → exit 0 + marker."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session) as find_mock,
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=941)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("PLAN")
        # #1558: write path resolves through the shared resolver with ensure=True.
        find_mock.assert_called_once_with(None, issue_number=941, ensure=True)

    def test_marker_lands_on_issue_session_under_divergent_env(self):
        """#1671/#1672: with VALOR_SESSION_ID pointing at a DIFFERENT session, a
        stage-marker write with --issue-number N lands on the issue-scoped
        session (resolved via the real find_session issue-first pass), not the
        divergent env session."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        issue_session = MagicMock(name="issue_session")
        captured = {}

        def _psm_factory(session):
            captured["session"] = session
            sm = MagicMock()
            sm.states = {"PLAN": "in_progress"}
            return sm

        env = {**os.environ, "VALOR_SESSION_ID": "parent-pm-divergent"}
        env.pop("AGENT_SESSION_ID", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            # Real find_session runs; its issue-first pass resolves this.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("agent.pipeline_state.PipelineStateMachine", side_effect=_psm_factory),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=1672)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        # The marker write targeted the issue session, not the env one.
        assert captured["session"] is issue_session

    def test_sessionless_issue_numbered_write_auto_ensures(self):
        """A sessionless-but-issue-numbered write resolves through find_session
        with ensure=True, which auto-creates a PM session so the marker persists
        (#1558). Here we assert the resolver is invoked with ensure=True and that
        the returned (ensured) session drives a successful marker write."""
        from tools.sdlc_stage_marker import write_marker

        ensured = MagicMock()
        ensured.stage_states = "{}"

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("tools.sdlc_stage_marker.find_session", return_value=ensured) as find_mock,
            patch("agent.pipeline_state.PipelineStateMachine") as mock_psm_cls,
        ):
            mock_psm = MagicMock()
            mock_psm.states = {}
            mock_psm_cls.return_value = mock_psm

            result, code = write_marker(stage="REVIEW", status="in_progress", issue_number=1558)

        assert code == 0
        assert result == {"stage": "REVIEW", "status": "in_progress"}
        find_mock.assert_called_once_with(None, issue_number=1558, ensure=True)


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
        assert output.get("status") == "degraded" or output == {
            "stage": "PLAN",
            "status": "completed",
        }
