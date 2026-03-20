"""Tests for persona-aware scheduling restrictions in job_scheduler.

Validates:
1. Teammate persona is blocked from schedule and playlist operations
2. Developer persona can schedule and create playlists
3. Project-manager persona can schedule and create playlists
4. Default persona (unset) is permissive (developer)
5. Non-SDLC operations (status, push, bump, pop, cancel) are unrestricted for all
"""

from unittest.mock import patch

import pytest

from tools.job_scheduler import _check_persona_permission


class TestPersonaGate:
    """Tests for _check_persona_permission."""

    def test_developer_can_schedule(self):
        with patch.dict("os.environ", {"PERSONA": "developer"}):
            result = _check_persona_permission("schedule")
            assert result is None

    def test_developer_can_playlist(self):
        with patch.dict("os.environ", {"PERSONA": "developer"}):
            result = _check_persona_permission("playlist")
            assert result is None

    def test_project_manager_can_schedule(self):
        with patch.dict("os.environ", {"PERSONA": "project-manager"}):
            result = _check_persona_permission("schedule")
            assert result is None

    def test_project_manager_can_playlist(self):
        with patch.dict("os.environ", {"PERSONA": "project-manager"}):
            result = _check_persona_permission("playlist")
            assert result is None

    def test_teammate_blocked_from_schedule(self):
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("schedule")
            assert result is not None
            assert result["status"] == "error"
            assert "Permission denied" in result["message"]
            assert result["persona"] == "teammate"
            assert result["action"] == "schedule"

    def test_teammate_blocked_from_playlist(self):
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("playlist")
            assert result is not None
            assert result["status"] == "error"

    def test_teammate_can_view_status(self):
        """Status and other read operations are unrestricted."""
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("status")
            assert result is None

    def test_teammate_can_push(self):
        with patch.dict("os.environ", {"PERSONA": "teammate"}):
            result = _check_persona_permission("push")
            assert result is None

    def test_default_persona_is_permissive(self):
        """When PERSONA env var is not set, default to developer (permissive)."""
        with patch.dict("os.environ", {}, clear=True):
            result = _check_persona_permission("schedule")
            assert result is None

    def test_persona_case_insensitive(self):
        """Persona check should be case-insensitive."""
        with patch.dict("os.environ", {"PERSONA": "Teammate"}):
            result = _check_persona_permission("schedule")
            assert result is not None
            assert result["status"] == "error"
