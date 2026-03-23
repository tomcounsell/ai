"""Tests for the stop hook SDLC stage progress warning (issue #486).

Verifies that _check_sdlc_stage_progress warns when an SDLC-classified
session completes without any stage progress.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# The stop hook uses sys.path manipulation. We replicate the import setup.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "hooks"))


class TestCheckSdlcStageProgress:
    """Tests for _check_sdlc_stage_progress in stop.py."""

    def _import_check_fn(self):
        """Import the function under test from the stop hook module."""
        # Import fresh each time to avoid module caching issues
        import importlib
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "stop_hook",
            PROJECT_ROOT / ".claude" / "hooks" / "stop.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._check_sdlc_stage_progress

    def _make_session(
        self, classification_type=None, sdlc_stages=None, stage_states=None
    ):
        """Create a mock AgentSession."""
        session = MagicMock()
        session.classification_type = classification_type
        session.sdlc_stages = sdlc_stages
        session.stage_states = stage_states
        return session

    def test_sdlc_no_stages_emits_warning(self, capsys):
        """SDLC-classified session with no stage progress emits a warning."""
        session = self._make_session(
            classification_type="sdlc", sdlc_stages=None, stage_states=None
        )

        with patch(
            "models.agent_session.AgentSession"
        ) as mock_cls:
            mock_cls.query.filter.return_value = [session]
            check_fn = self._import_check_fn()
            check_fn("test-session-123")

        captured = capsys.readouterr()
        assert "SDLC WARNING" in captured.err
        assert "test-session-123" in captured.err
        assert "no stage progress" in captured.err

    def test_sdlc_with_stages_no_warning(self, capsys):
        """SDLC-classified session with stage progress emits no warning."""
        session = self._make_session(
            classification_type="sdlc",
            sdlc_stages={"plan": "completed", "build": "in_progress"},
            stage_states=None,
        )

        with patch(
            "models.agent_session.AgentSession"
        ) as mock_cls:
            mock_cls.query.filter.return_value = [session]
            check_fn = self._import_check_fn()
            check_fn("test-session-456")

        captured = capsys.readouterr()
        assert "SDLC WARNING" not in captured.err

    def test_sdlc_with_stage_states_no_warning(self, capsys):
        """SDLC session with stage_states populated emits no warning."""
        session = self._make_session(
            classification_type="sdlc",
            sdlc_stages=None,
            stage_states={"plan": {"status": "done"}},
        )

        with patch(
            "models.agent_session.AgentSession"
        ) as mock_cls:
            mock_cls.query.filter.return_value = [session]
            check_fn = self._import_check_fn()
            check_fn("test-session-789")

        captured = capsys.readouterr()
        assert "SDLC WARNING" not in captured.err

    def test_non_sdlc_no_warning(self, capsys):
        """Non-SDLC session emits no warning regardless of stage state."""
        session = self._make_session(
            classification_type="question", sdlc_stages=None, stage_states=None
        )

        with patch(
            "models.agent_session.AgentSession"
        ) as mock_cls:
            mock_cls.query.filter.return_value = [session]
            check_fn = self._import_check_fn()
            check_fn("test-session-abc")

        captured = capsys.readouterr()
        assert "SDLC WARNING" not in captured.err

    def test_redis_unavailable_no_crash(self, capsys):
        """Redis/model errors are caught gracefully — no exception raised."""
        with patch(
            "models.agent_session.AgentSession",
            side_effect=Exception("Redis connection refused"),
        ):
            check_fn = self._import_check_fn()
            # Should not raise
            check_fn("test-session-err")

        captured = capsys.readouterr()
        # No warning, no crash
        assert "SDLC WARNING" not in captured.err

    def test_no_session_found_no_warning(self, capsys):
        """If no AgentSession exists for the session_id, no warning."""
        with patch(
            "models.agent_session.AgentSession"
        ) as mock_cls:
            mock_cls.query.filter.return_value = []
            check_fn = self._import_check_fn()
            check_fn("test-session-missing")

        captured = capsys.readouterr()
        assert "SDLC WARNING" not in captured.err

    def test_empty_dicts_emit_warning(self, capsys):
        """SDLC session with empty dicts (not None) still triggers warning."""
        session = self._make_session(
            classification_type="sdlc", sdlc_stages={}, stage_states={}
        )

        with patch(
            "models.agent_session.AgentSession"
        ) as mock_cls:
            mock_cls.query.filter.return_value = [session]
            check_fn = self._import_check_fn()
            check_fn("test-session-empty")

        captured = capsys.readouterr()
        assert "SDLC WARNING" in captured.err
