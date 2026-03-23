"""Tests for SDLC mode activation (issue #246).

Verifies that sessions classified as 'sdlc' are always detected as SDLC jobs,
regardless of whether sub-skills call session_progress.

Run with: pytest tests/test_sdlc_mode.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.agent_session import AgentSession


class TestIsSdlcJobClassificationType:
    """is_sdlc should return True when classification_type is 'sdlc'."""

    def test_classification_type_sdlc_returns_true(self):
        """Sessions classified as 'sdlc' should be detected as SDLC jobs."""
        session = AgentSession(
            session_id="test_sdlc_246",
            project_key="test",
            classification_type="sdlc",
        )
        assert session.is_sdlc is True

    def test_classification_type_none_with_no_history_returns_false(self):
        """Sessions with no classification and no history are not SDLC."""
        session = AgentSession(
            session_id="test_chat_246",
            project_key="test",
        )
        assert session.is_sdlc is False

    def test_classification_type_chat_returns_false(self):
        """Non-SDLC classifications should not be SDLC jobs."""
        session = AgentSession(
            session_id="test_chat_246",
            project_key="test",
            classification_type="chat",
        )
        assert session.is_sdlc is False

    def test_stage_states_triggers_sdlc(self):
        """stage_states with active stages should trigger SDLC."""
        import json

        session = AgentSession(
            session_id="test_legacy_246",
            project_key="test",
            stage_states=json.dumps({"PLAN": "in_progress"}),
        )
        assert session.is_sdlc is True

    def test_classification_takes_priority_over_empty_history(self):
        """classification_type=sdlc should work even with empty history."""
        session = AgentSession(
            session_id="test_priority_246",
            project_key="test",
            classification_type="sdlc",
            history=[],
        )
        assert session.is_sdlc is True

    def test_sdlc_mode_via_classification_type(self):
        """classification_type='sdlc' should make is_sdlc return True."""
        session = AgentSession(
            session_id="test_activated_246",
            project_key="test",
            classification_type="sdlc",
            history=["[user] SDLC issue 246"],
        )
        assert session.is_sdlc is True
