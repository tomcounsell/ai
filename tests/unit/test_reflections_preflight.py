"""Tests for reflections pre-flight validation."""

import unittest
from unittest.mock import MagicMock, patch


class TestReflectionsPreflight(unittest.TestCase):
    """Test the _preflight_check method on ReflectionRunner."""

    def _make_runner(self):
        """Create a ReflectionRunner with mocked state."""
        with patch("scripts.reflections.ReflectionRunner._load_state") as mock_load:
            mock_state = MagicMock()
            mock_state.daily_report = []
            mock_state.completed_steps = []
            mock_state.date = "2026-03-24"
            mock_state.findings = {}
            mock_state.session_analysis = {}
            mock_state.reflections = []
            mock_state.auto_fix_attempts = []
            mock_state.step_progress = {}
            mock_load.return_value = mock_state

            with patch("scripts.reflections.load_local_projects", return_value={}):
                from scripts.reflections import ReflectionRunner

                runner = ReflectionRunner()
                return runner

    @patch("popoto.redis_db.POPOTO_REDIS_DB")
    def test_preflight_passes_with_redis(self, mock_redis):
        """Pre-flight passes when Redis is available."""
        mock_redis.ping.return_value = True
        runner = self._make_runner()
        assert runner._preflight_check("legacy_code_scan", "Clean Up Legacy Code") is True

    @patch("popoto.redis_db.POPOTO_REDIS_DB")
    def test_preflight_fails_without_redis(self, mock_redis):
        """Pre-flight fails gracefully when Redis is down."""
        mock_redis.ping.side_effect = ConnectionError("Redis down")
        runner = self._make_runner()
        result = runner._preflight_check("legacy_code_scan", "Clean Up Legacy Code")
        assert result is False
        assert "Skipped" in runner.state.daily_report[0]

    @patch("shutil.which", return_value=None)
    @patch("popoto.redis_db.POPOTO_REDIS_DB")
    def test_preflight_fails_without_gh_cli(self, mock_redis, mock_which):
        """Pre-flight for gh-dependent steps fails when gh CLI is missing."""
        mock_redis.ping.return_value = True
        runner = self._make_runner()
        result = runner._preflight_check("session_intelligence", "Session Intelligence")
        assert result is False
        assert "gh CLI" in runner.state.daily_report[0]

    @patch("shutil.which", return_value="/usr/local/bin/gh")
    @patch("popoto.redis_db.POPOTO_REDIS_DB")
    def test_preflight_passes_with_gh_cli(self, mock_redis, mock_which):
        """Pre-flight for gh-dependent steps passes when gh CLI exists."""
        mock_redis.ping.return_value = True
        runner = self._make_runner()
        result = runner._preflight_check("session_intelligence", "Session Intelligence")
        assert result is True
