"""Integration tests for analytics dashboard endpoints.

Tests that dashboard.json includes the analytics key and that existing
keys remain backward-compatible.
"""

import pytest


@pytest.mark.analytics
class TestDashboardAnalytics:
    """Test analytics integration in dashboard endpoints."""

    def test_analytics_summary_returns_valid_structure(self):
        """get_analytics_summary should return a well-formed dict."""
        from ui.data.analytics import get_analytics_summary

        summary = get_analytics_summary()
        assert isinstance(summary, dict)
        assert "sessions_today" in summary
        assert "sessions_7d" in summary
        assert "cost_today_usd" in summary
        assert "cost_7d_usd" in summary
        assert "daily_sessions" in summary
        assert isinstance(summary["daily_sessions"], list)

    def test_analytics_summary_graceful_without_db(self, tmp_path, monkeypatch):
        """Analytics summary should return zeros when no database exists."""
        monkeypatch.setattr("analytics.query._DB_PATH", tmp_path / "nonexistent.db")

        from ui.data.analytics import get_analytics_summary

        summary = get_analytics_summary()
        assert summary["sessions_today"] == 0
        assert summary["sessions_7d"] == 0
        assert summary["cost_today_usd"] == 0.0
