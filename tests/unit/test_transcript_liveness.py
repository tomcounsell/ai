"""Unit tests for transcript liveness check in monitoring/session_watchdog.py.

Tests the _check_transcript_liveness() function that uses transcript file mtime
to determine if a session is actively doing sub-agent work (issue #360).
"""

import os
import time
from unittest.mock import patch

from monitoring.session_watchdog import (
    TRANSCRIPT_STALE_THRESHOLD_MIN,
    _check_transcript_liveness,
)


class TestTranscriptLivenessConstants:
    """Tests for the transcript stale threshold constant."""

    def test_default_threshold_is_15_minutes(self):
        """Default transcript stale threshold is 15 minutes."""
        assert TRANSCRIPT_STALE_THRESHOLD_MIN == 15


class TestCheckTranscriptLiveness:
    """Tests for _check_transcript_liveness()."""

    def test_missing_transcript_returns_stale(self, tmp_path):
        """If transcript file doesn't exist, return (True, inf-like value)."""
        is_stale, stale_minutes = _check_transcript_liveness(
            "nonexistent-session-id", logs_dir=tmp_path / "logs" / "sessions"
        )
        assert is_stale is True
        # stale_minutes should be a large positive number
        assert stale_minutes > 0

    def test_fresh_transcript_returns_not_stale(self, tmp_path):
        """A transcript modified just now is not stale."""
        session_id = "fresh-session"
        session_dir = tmp_path / "logs" / "sessions" / session_id
        session_dir.mkdir(parents=True)
        transcript = session_dir / "transcript.txt"
        transcript.write_text("[2026-03-12T10:00:00] USER: hello\n")

        is_stale, stale_minutes = _check_transcript_liveness(
            session_id, logs_dir=tmp_path / "logs" / "sessions"
        )
        assert is_stale is False
        assert stale_minutes < 1.0  # Just created, should be <1 min

    def test_old_transcript_returns_stale(self, tmp_path):
        """A transcript not modified for >15 min is stale."""
        session_id = "old-session"
        session_dir = tmp_path / "logs" / "sessions" / session_id
        session_dir.mkdir(parents=True)
        transcript = session_dir / "transcript.txt"
        transcript.write_text("[2026-03-12T10:00:00] USER: hello\n")

        # Set mtime to 20 minutes ago
        old_time = time.time() - (20 * 60)
        os.utime(transcript, (old_time, old_time))

        is_stale, stale_minutes = _check_transcript_liveness(
            session_id, logs_dir=tmp_path / "logs" / "sessions"
        )
        assert is_stale is True
        assert stale_minutes >= 19.0  # At least 19 minutes (allowing small delta)

    def test_boundary_just_under_threshold(self, tmp_path):
        """A transcript modified 14 minutes ago is NOT stale."""
        session_id = "boundary-fresh"
        session_dir = tmp_path / "logs" / "sessions" / session_id
        session_dir.mkdir(parents=True)
        transcript = session_dir / "transcript.txt"
        transcript.write_text("test content\n")

        # Set mtime to 14 minutes ago
        old_time = time.time() - (14 * 60)
        os.utime(transcript, (old_time, old_time))

        is_stale, stale_minutes = _check_transcript_liveness(
            session_id, logs_dir=tmp_path / "logs" / "sessions"
        )
        assert is_stale is False
        assert 13.0 <= stale_minutes <= 15.0

    def test_boundary_just_over_threshold(self, tmp_path):
        """A transcript modified 16 minutes ago IS stale."""
        session_id = "boundary-stale"
        session_dir = tmp_path / "logs" / "sessions" / session_id
        session_dir.mkdir(parents=True)
        transcript = session_dir / "transcript.txt"
        transcript.write_text("test content\n")

        # Set mtime to 16 minutes ago
        old_time = time.time() - (16 * 60)
        os.utime(transcript, (old_time, old_time))

        is_stale, stale_minutes = _check_transcript_liveness(
            session_id, logs_dir=tmp_path / "logs" / "sessions"
        )
        assert is_stale is True
        assert stale_minutes >= 15.0

    def test_empty_session_id_returns_stale(self, tmp_path):
        """Empty session ID returns stale (file won't exist)."""
        is_stale, stale_minutes = _check_transcript_liveness(
            "", logs_dir=tmp_path / "logs" / "sessions"
        )
        assert is_stale is True

    def test_uses_default_logs_dir_when_not_specified(self):
        """When logs_dir is not specified, uses the project default."""
        # Just verify the function is callable without logs_dir
        # (it will use the real logs dir which may or may not have the session)
        is_stale, stale_minutes = _check_transcript_liveness("definitely-not-a-real-session")
        assert is_stale is True  # File won't exist

    def test_returns_tuple(self, tmp_path):
        """Return value is always a tuple of (bool, float)."""
        is_stale, stale_minutes = _check_transcript_liveness(
            "test", logs_dir=tmp_path / "logs" / "sessions"
        )
        assert isinstance(is_stale, bool)
        assert isinstance(stale_minutes, float)


class TestCheckStalledSessionsWithTranscript:
    """Tests that check_stalled_sessions uses transcript liveness for active sessions."""

    def test_active_session_with_fresh_transcript_not_stalled(self, tmp_path):
        """An active session whose transcript is fresh should NOT be marked stalled,
        even if last_activity is old."""
        from types import SimpleNamespace

        from monitoring.session_watchdog import (
            STALL_THRESHOLD_ACTIVE,
            check_stalled_sessions,
        )

        now = time.time()
        # last_activity is old (would normally trigger stall)
        session = SimpleNamespace(
            session_id="transcript-fresh",
            job_id="job-001",
            status="active",
            started_at=now - 3600,
            created_at=now - 3600,
            last_activity=now - (STALL_THRESHOLD_ACTIVE + 120),
            project_key="test",
        )
        session._get_history_list = lambda: []

        # Create a fresh transcript file
        session_dir = tmp_path / "transcript-fresh"
        session_dir.mkdir(parents=True)
        transcript = session_dir / "transcript.txt"
        transcript.write_text("recent activity\n")

        def mock_filter(**kwargs):
            status = kwargs.get("status", "")
            if status == "active":
                return [session]
            return []

        mock_query = SimpleNamespace(filter=mock_filter)

        with (
            patch("monitoring.session_watchdog.AgentSession.query", mock_query),
            patch(
                "monitoring.session_watchdog._check_transcript_liveness",
                return_value=(False, 2.0),  # Fresh transcript
            ) as mock_check,
        ):
            result = check_stalled_sessions()
            # Session should NOT be stalled because transcript is fresh
            ids = [s["session_id"] for s in result]
            assert "transcript-fresh" not in ids
            # Verify transcript check was called
            mock_check.assert_called_once_with("transcript-fresh")

    def test_active_session_with_stale_transcript_is_stalled(self):
        """An active session whose transcript is stale should be marked stalled."""
        from types import SimpleNamespace

        from monitoring.session_watchdog import (
            STALL_THRESHOLD_ACTIVE,
            check_stalled_sessions,
        )

        now = time.time()
        session = SimpleNamespace(
            session_id="transcript-stale",
            job_id="job-002",
            status="active",
            started_at=now - 3600,
            created_at=now - 3600,
            last_activity=now - (STALL_THRESHOLD_ACTIVE + 120),
            project_key="test",
        )
        session._get_history_list = lambda: []

        def mock_filter(**kwargs):
            status = kwargs.get("status", "")
            if status == "active":
                return [session]
            return []

        mock_query = SimpleNamespace(filter=mock_filter)

        with (
            patch("monitoring.session_watchdog.AgentSession.query", mock_query),
            patch(
                "monitoring.session_watchdog._check_transcript_liveness",
                return_value=(True, 20.0),  # Stale transcript
            ),
        ):
            result = check_stalled_sessions()
            ids = [s["session_id"] for s in result]
            assert "transcript-stale" in ids

    def test_transcript_check_not_called_for_pending_sessions(self):
        """Transcript liveness is only checked for active sessions, not pending."""
        from types import SimpleNamespace

        from monitoring.session_watchdog import (
            STALL_THRESHOLD_PENDING,
            check_stalled_sessions,
        )

        now = time.time()
        session = SimpleNamespace(
            session_id="pending-session",
            job_id="job-003",
            status="pending",
            started_at=None,
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            last_activity=now,
            project_key="test",
        )
        session._get_history_list = lambda: []

        def mock_filter(**kwargs):
            status = kwargs.get("status", "")
            if status == "pending":
                return [session]
            return []

        mock_query = SimpleNamespace(filter=mock_filter)

        with (
            patch("monitoring.session_watchdog.AgentSession.query", mock_query),
            patch(
                "monitoring.session_watchdog._check_transcript_liveness",
            ) as mock_check,
        ):
            check_stalled_sessions()
            # Should NOT be called for pending sessions
            mock_check.assert_not_called()
