"""Unit tests for tools.sdlc_stage_query CLI tool.

Tests cover:
- query_stage_states with valid session data
- Graceful handling of missing sessions
- Graceful handling of malformed stage_states
- CLI argument parsing and output format
- Fallback to issue number lookup
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch  # noqa: F401 - patch used in tests below

# Resolve the repo root for subprocess cwd
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQueryStageStates:
    """Tests for the query_stage_states function."""

    def test_returns_empty_dict_when_no_args(self):
        from tools.sdlc_stage_query import query_stage_states

        result = query_stage_states()
        assert result == {}

    def test_returns_empty_dict_when_session_not_found(self):
        from tools.sdlc_stage_query import query_stage_states

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=None):
            result = query_stage_states(session_id="nonexistent")
        assert result == {}

    def test_returns_stage_states_from_session(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "in_progress",
            "TEST": "pending",
            "PATCH": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }
        mock_session.stage_states = json.dumps(stages)

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "completed"
        assert result["BUILD"] == "in_progress"
        assert result["TEST"] == "pending"

    def test_filters_out_metadata_keys(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "_patch_cycle_count": 2,
            "_critique_cycle_count": 0,
        }
        mock_session.stage_states = json.dumps(stages)

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert "_patch_cycle_count" not in result
        assert "_critique_cycle_count" not in result
        assert result["ISSUE"] == "completed"

    def test_handles_malformed_json_gracefully(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        mock_session.stage_states = "not-valid-json"

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result == {}

    def test_handles_none_stage_states(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        mock_session.stage_states = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result == {}

    def test_issue_number_fallback(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {"ISSUE": "completed", "PLAN": "in_progress"}
        mock_session.stage_states = json.dumps(stages)

        with (
            patch("tools.sdlc_stage_query._find_session_by_id", return_value=None),
            patch("tools.sdlc_stage_query._find_session_by_issue", return_value=mock_session),
        ):
            result = query_stage_states(session_id="missing", issue_number=704)

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "in_progress"

    def test_handles_dict_stage_states(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        mock_session.stage_states = {"ISSUE": "completed", "PLAN": "ready"}

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "ready"

    def test_only_returns_known_stages(self):
        """Verify unknown stage names are filtered out."""
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {
            "ISSUE": "completed",
            "UNKNOWN_STAGE": "completed",
            "BOGUS": "in_progress",
        }
        mock_session.stage_states = json.dumps(stages)

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert "ISSUE" in result
        assert "UNKNOWN_STAGE" not in result
        assert "BOGUS" not in result


class TestFindSessionByIssue:
    """Tests for _find_session_by_issue."""

    def test_matches_issue_url_suffix(self):
        from tools.sdlc_stage_query import _find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/704"
        mock_session.session_type = "pm"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_issue(704)

        assert result == mock_session

    def test_returns_none_when_no_match(self):
        from tools.sdlc_stage_query import _find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/999"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_issue(704)

        assert result is None

    def test_handles_redis_exception_gracefully(self):
        from tools.sdlc_stage_query import _find_session_by_issue

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = ConnectionError("Redis down")

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_issue(704)

        assert result is None


class TestFindSessionById:
    """Tests for _find_session_by_id."""

    def test_prefers_pm_session(self):
        from tools.sdlc_stage_query import _find_session_by_id

        pm_session = MagicMock()
        pm_session.session_type = "pm"
        dev_session = MagicMock()
        dev_session.session_type = "dev"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [dev_session, pm_session]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_id("test-session")

        assert result == pm_session

    def test_returns_first_session_when_no_pm(self):
        from tools.sdlc_stage_query import _find_session_by_id

        dev_session = MagicMock()
        dev_session.session_type = "dev"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [dev_session]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_id("test-session")

        assert result == dev_session

    def test_returns_none_for_empty_results(self):
        from tools.sdlc_stage_query import _find_session_by_id

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_id("nonexistent")

        assert result is None


class TestCLIOutput:
    """Tests for CLI invocation and output format."""

    def test_no_args_returns_empty_json(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_query"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout.strip()) == {}

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_query", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "--session-id" in result.stdout
        assert "--issue-number" in result.stdout
