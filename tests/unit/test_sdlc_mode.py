"""Tests for SDLC mode detection (issue #436).

Verifies that is_sdlc derives SDLC status from stage_states (primary),
history [stage] entries (secondary), and classification_type (tertiary).

Run with: pytest tests/unit/test_sdlc_mode.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.agent_session import AgentSession


class TestIsSdlcDerivedProperty:
    """is_sdlc should derive SDLC status from observable state."""

    def test_stage_states_in_progress_returns_true(self):
        """Sessions with in_progress stage_states are SDLC."""
        session = AgentSession(
            session_id="test_stage_states",
            project_key="test",
            stage_states=json.dumps({"PLAN": "completed", "BUILD": "in_progress"}),
        )
        assert session.is_sdlc is True

    def test_stage_states_completed_returns_true(self):
        """Sessions with completed stage_states are SDLC."""
        session = AgentSession(
            session_id="test_completed",
            project_key="test",
            stage_states=json.dumps({"PLAN": "completed"}),
        )
        assert session.is_sdlc is True

    def test_stage_states_failed_returns_true(self):
        """Sessions with failed stage_states are SDLC."""
        session = AgentSession(
            session_id="test_failed",
            project_key="test",
            stage_states=json.dumps({"BUILD": "failed"}),
        )
        assert session.is_sdlc is True

    def test_stage_states_all_pending_returns_false(self):
        """Sessions with only pending stages are not SDLC (from stage_states alone)."""
        session = AgentSession(
            session_id="test_pending",
            project_key="test",
            stage_states=json.dumps({"PLAN": "pending", "BUILD": "ready"}),
        )
        assert session.is_sdlc is False

    def test_classification_type_sdlc_returns_true(self):
        """Sessions classified as 'sdlc' should be detected as SDLC (tertiary)."""
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

    def test_stage_history_still_works(self):
        """Legacy path: [stage] entries in history should still trigger SDLC."""
        session = AgentSession(
            session_id="test_legacy_246",
            project_key="test",
            history=["[stage] PLAN in_progress"],
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

    def test_sdlc_mode_activated_entry(self):
        """The SDLC_MODE activated entry should make is_sdlc return True."""
        session = AgentSession(
            session_id="test_activated_246",
            project_key="test",
            history=["[user] SDLC issue 246", "[stage] SDLC_MODE activated"],
        )
        assert session.is_sdlc is True

    def test_none_stage_states_handled_gracefully(self):
        """Property handles None stage_states without error."""
        session = AgentSession(
            session_id="test_none",
            project_key="test",
            stage_states=None,
        )
        assert session.is_sdlc is False

    def test_empty_history_handled_gracefully(self):
        """Property handles empty history list without error."""
        session = AgentSession(
            session_id="test_empty",
            project_key="test",
            history=[],
        )
        assert session.is_sdlc is False

    def test_malformed_stage_states_falls_through(self):
        """Property handles malformed JSON stage_states gracefully."""
        session = AgentSession(
            session_id="test_malformed",
            project_key="test",
            stage_states="not valid json{{{",
        )
        # Falls through to secondary/tertiary checks
        assert session.is_sdlc is False

    def test_stage_states_takes_priority_over_classification(self):
        """stage_states is checked before classification_type."""
        session = AgentSession(
            session_id="test_priority",
            project_key="test",
            stage_states=json.dumps({"PLAN": "completed"}),
            classification_type="chat",  # contradicts stage_states
        )
        # stage_states wins — it has a completed stage
        assert session.is_sdlc is True
