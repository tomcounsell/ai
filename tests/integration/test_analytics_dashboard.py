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
        assert "sessions_started_today" in summary
        assert "sessions_started_7d" in summary
        assert "sessions_completed_today" in summary
        assert "sessions_completed_7d" in summary
        assert "cost_today_usd" in summary
        assert "cost_7d_usd" in summary
        assert "turns_today" in summary
        assert "turns_7d" in summary
        assert "turns_avg_today" in summary
        assert "turns_avg_7d" in summary
        assert "memory_recalls_today" in summary
        assert "memory_recalls_7d" in summary
        assert "memory_extractions_today" in summary
        assert "memory_extractions_7d" in summary

    def test_analytics_summary_graceful_without_db(self, tmp_path, monkeypatch):
        """Analytics summary should return zeros when no database exists.

        Issue #1245 update: cost/turn aggregation now derives from the
        Popoto AgentSession query, not the analytics SQLite DB. This test
        also forces the Popoto query to fail so the helper short-circuits
        the same way the legacy ledger query did.
        """
        monkeypatch.setattr("analytics.query._DB_PATH", tmp_path / "nonexistent.db")

        def _boom(*a, **kw):
            raise RuntimeError("popoto unavailable")

        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            _boom,
        )

        from ui.data.analytics import get_analytics_summary

        summary = get_analytics_summary()
        assert summary["sessions_started_today"] == 0
        assert summary["sessions_started_7d"] == 0
        assert summary["cost_today_usd"] == 0.0
        assert summary["turns_today"] == 0.0

    def test_cost_today_from_agent_session(self, redis_test_db):
        """Issue #1245: completed AgentSession with cost+turns flows into summary.

        Creates a single AgentSession with status="completed",
        completed_at=now, total_cost_usd=1.23, turn_count=4. The Popoto
        helper should pick it up and the summary should reflect at least
        those values (other sessions may also be present from prior tests
        or running fixtures, so we assert ">=").
        """
        from datetime import UTC, datetime

        from models.agent_session import AgentSession
        from ui.data.analytics import get_analytics_summary

        session = AgentSession.create(
            session_id="test-1245-cost-today",
            project_key="test-1245",
            status="completed",
            created_at=datetime.now(tz=UTC),
            completed_at=datetime.now(tz=UTC),
            total_cost_usd=1.23,
            turn_count=4,
        )
        try:
            summary = get_analytics_summary()
            assert summary["cost_today_usd"] >= 1.23
            assert summary["turns_today"] >= 4
            assert summary["cost_7d_usd"] >= 1.23
            assert summary["turns_7d"] >= 4
        finally:
            session.delete()
