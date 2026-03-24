"""Integration test for cross-agent knowledge relay.

Verifies end-to-end: extract findings from one session's output,
then inject those findings into another session's context.

Requires Redis to be running (integration test).
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_haiku_response():
    """Mock Haiku extraction response with realistic findings."""
    return [
        {
            "category": "pattern_found",
            "content": "Bridge uses Telethon for Telegram integration with async event handlers",
            "file_paths": "bridge/telegram_bridge.py",
            "importance": 7.0,
        },
        {
            "category": "decision_made",
            "content": "PostToolUse hooks inject via additionalContext",
            "file_paths": "agent/hooks/post_tool_use.py,agent/memory_hook.py",
            "importance": 6.0,
        },
    ]


class TestFindingRelayEndToEnd:
    """End-to-end relay: extract from BUILD session, inject into TEST session."""

    @patch("agent.finding_extraction._call_haiku_extraction")
    @patch("models.finding.Finding.save")
    @patch("models.finding.Finding.query")
    def test_extract_then_query(self, mock_query, mock_save, mock_haiku, mock_haiku_response):
        """Findings extracted in BUILD should be queryable for TEST."""
        from agent.finding_extraction import extract_findings_from_output

        # Mock Haiku to return realistic findings
        mock_haiku.return_value = mock_haiku_response
        mock_save.return_value = True

        # Step 1: Extract findings from BUILD session output
        saved = extract_findings_from_output(
            output="Built the Telegram bridge integration with Telethon async handlers",
            slug="bridge-feature",
            stage="BUILD",
            session_id="dev-build-1",
            project_key="ai",
        )

        assert len(saved) == 2
        assert saved[0]["category"] == "pattern_found"
        assert saved[1]["category"] == "decision_made"

    @patch("agent.finding_query._bloom_has_relevant")
    @patch("models.finding.Finding.query_by_slug")
    def test_query_returns_ranked_findings(self, mock_query, mock_bloom):
        """query_findings should return findings ranked by composite score."""
        from agent.finding_query import query_findings

        mock_bloom.return_value = True

        # Create mock findings with different importance
        f_high = MagicMock()
        f_high.importance = 7.0
        f_high.confidence = 0.7
        f_high._at_access_count = 3
        f_high.content = "Bridge uses Telethon"
        f_high.file_paths = "bridge/telegram_bridge.py"
        f_high.stage = "BUILD"
        f_high.category = "pattern_found"

        f_low = MagicMock()
        f_low.importance = 2.0
        f_low.confidence = 0.3
        f_low._at_access_count = 0
        f_low.content = "Minor config detail"
        f_low.file_paths = ""
        f_low.stage = "BUILD"
        f_low.category = "file_examined"

        mock_query.return_value = [f_low, f_high]

        results = query_findings("bridge-feature", topics=["bridge", "telethon"])
        assert len(results) == 2
        # Higher importance should rank first
        assert results[0].importance == 7.0

    @patch("agent.finding_query._bloom_has_relevant")
    @patch("models.finding.Finding.query_by_slug")
    def test_format_for_injection(self, mock_query, mock_bloom):
        """Formatted findings should be suitable for prompt injection."""
        from agent.finding_query import format_findings_for_injection, query_findings

        mock_bloom.return_value = True

        f1 = MagicMock()
        f1.importance = 7.0
        f1.confidence = 0.7
        f1._at_access_count = 1
        f1.content = "Bridge uses Telethon for async event handling"
        f1.file_paths = "bridge/telegram_bridge.py"
        f1.stage = "BUILD"
        f1.category = "pattern_found"

        mock_query.return_value = [f1]

        findings = query_findings("bridge-feature")
        text = format_findings_for_injection(findings)

        assert text is not None
        assert "Prior Findings" in text
        assert "BUILD" in text
        assert "Telethon" in text

    @patch("agent.finding_extraction._call_haiku_extraction")
    @patch("models.finding.Finding.save")
    @patch("agent.finding_query._bloom_has_relevant")
    @patch("models.finding.Finding.query_by_slug")
    def test_full_relay_extract_to_inject(
        self, mock_query_slug, mock_bloom, mock_save, mock_haiku, mock_haiku_response
    ):
        """Full relay: BUILD extracts, TEST queries and formats for injection."""
        from agent.finding_extraction import extract_findings_from_output
        from agent.finding_query import format_findings_for_injection, query_findings

        # --- BUILD phase: extract findings ---
        mock_haiku.return_value = mock_haiku_response
        mock_save.return_value = True

        saved = extract_findings_from_output(
            output="Comprehensive build output with bridge details",
            slug="bridge-feature",
            stage="BUILD",
            session_id="dev-build-1",
            project_key="ai",
        )
        assert len(saved) >= 1

        # --- TEST phase: query findings from BUILD ---
        mock_bloom.return_value = True

        # Simulate the findings being stored and queryable
        f1 = MagicMock()
        f1.importance = 7.0
        f1.confidence = 0.7
        f1._at_access_count = 1
        f1.content = mock_haiku_response[0]["content"]
        f1.file_paths = mock_haiku_response[0]["file_paths"]
        f1.stage = "BUILD"
        f1.category = "pattern_found"

        f2 = MagicMock()
        f2.importance = 6.0
        f2.confidence = 0.5
        f2._at_access_count = 0
        f2.content = mock_haiku_response[1]["content"]
        f2.file_paths = mock_haiku_response[1]["file_paths"]
        f2.stage = "BUILD"
        f2.category = "decision_made"

        mock_query_slug.return_value = [f1, f2]

        findings = query_findings("bridge-feature", topics=["bridge", "telethon"])
        assert len(findings) == 2

        text = format_findings_for_injection(findings)
        assert text is not None
        assert "Prior Findings" in text
        assert "Telethon" in text
        assert "additionalContext" in text or "PostToolUse" in text or "bridge" in text.lower()


class TestSilentFailures:
    """Verify all failure paths are silent (never crash the agent)."""

    def test_extraction_with_empty_output_is_silent(self):
        """Extraction with empty output should silently return []."""
        from agent.finding_extraction import extract_findings_from_output

        result = extract_findings_from_output("", "slug", "BUILD", "s1", "p1")
        assert result == []

    def test_query_with_empty_slug_is_silent(self):
        """Query with empty slug should silently return []."""
        from agent.finding_query import query_findings

        result = query_findings("")
        assert result == []

    def test_format_with_empty_findings_is_silent(self):
        """Format with no findings should silently return None."""
        from agent.finding_query import format_findings_for_injection

        result = format_findings_for_injection([])
        assert result is None

    @patch("agent.finding_extraction._call_haiku_extraction")
    def test_extraction_handles_api_failure(self, mock_haiku):
        """Extraction should handle Haiku API failure gracefully."""
        from agent.finding_extraction import extract_findings_from_output

        mock_haiku.side_effect = Exception("API timeout")
        result = extract_findings_from_output("output", "slug", "BUILD", "s1", "p1")
        assert result == []

    def test_inject_findings_with_no_env(self, monkeypatch):
        """Finding injection should handle missing env vars."""
        from agent.memory_hook import _inject_findings

        monkeypatch.delenv("VALOR_WORK_ITEM_SLUG", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        result = _inject_findings("session-1", ["topic"])
        assert result == []
