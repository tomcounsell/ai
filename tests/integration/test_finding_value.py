"""Value measurement tests for cross-agent knowledge relay.

Proves that the finding system delivers real value: findings from one
SDLC stage (BUILD) are available when querying for another stage (TEST),
and both injection paths (PostToolUse and pre-dispatch) correctly augment
the agent context.

Requires Redis to be running (integration test).
"""

import time

import pytest

from agent.finding_query import format_findings_for_injection, query_findings
from models.finding import Finding

# Unique slug to avoid collisions
_TEST_SLUG = f"_test-value-{int(time.time())}"


@pytest.fixture(autouse=True)
def cleanup_test_findings():
    """Clean up all findings created during tests."""
    yield
    try:
        results = Finding.query_by_slug(_TEST_SLUG, limit=100)
        for r in results:
            try:
                r.delete()
            except Exception:
                pass
    except Exception:
        pass


@pytest.mark.integration
class TestCrossStageRelay:
    """Findings from BUILD stage appear when querying for TEST context."""

    def test_build_findings_available_for_test_stage(self):
        """Save BUILD findings, then verify they appear in TEST context query."""
        slug = _TEST_SLUG

        # Simulate BUILD stage producing findings
        Finding.safe_save(
            slug=slug,
            project_key="test",
            session_id="build-session",
            stage="BUILD",
            category="pattern_found",
            content="Database uses connection pooling with max_connections=20",
            file_paths="db/pool.py",
            importance=7.0,
        )
        Finding.safe_save(
            slug=slug,
            project_key="test",
            session_id="build-session",
            stage="BUILD",
            category="decision_made",
            content="Chose PostgreSQL over SQLite for concurrent write support",
            file_paths="config/database.py",
            importance=8.0,
        )
        Finding.safe_save(
            slug=slug,
            project_key="test",
            session_id="build-session",
            stage="BUILD",
            category="artifact_produced",
            content="Created migration script for users table with indexes",
            file_paths="migrations/001_users.py",
            importance=5.0,
        )

        # Now query as if TEST stage wants prior context
        # query_findings is slug-scoped, not stage-scoped -- it returns all
        # findings for the slug regardless of which stage produced them.
        # Topics omitted: bloom filter uses full content fingerprints, not keywords.
        results = query_findings(slug)
        assert len(results) >= 2

        # Verify BUILD findings are present
        stages = {r.stage for r in results}
        assert "BUILD" in stages

        # Format for injection into TEST session
        text = format_findings_for_injection(results)
        assert text is not None
        assert "Prior Findings" in text
        assert "BUILD" in text
        # Content from BUILD findings should be in the injection text
        assert "connection pooling" in text or "PostgreSQL" in text

    def test_before_and_after_comparison(self):
        """Query with zero findings vs with findings -- 'with' case has actionable context."""
        slug = _TEST_SLUG

        # Before: no findings exist
        results_before = query_findings(slug)
        text_before = format_findings_for_injection(results_before)
        assert text_before is None  # No findings, no context

        # Create findings
        Finding.safe_save(
            slug=slug,
            project_key="test",
            session_id="build-session",
            stage="BUILD",
            category="pattern_found",
            content="API rate limiter uses token bucket algorithm with 100 req/min",
            file_paths="api/rate_limit.py",
            importance=7.0,
        )

        # After: findings exist (no topics -- bloom uses content fingerprints)
        results_after = query_findings(slug)
        text_after = format_findings_for_injection(results_after)

        assert text_after is not None
        assert "Prior Findings" in text_after
        assert "token bucket" in text_after
        assert "api/rate_limit.py" in text_after

        # The "with" case returns actionable context that "without" lacks
        assert len(results_after) > len(results_before)


@pytest.mark.integration
class TestPostToolUseInjection:
    """Test _inject_findings() from memory_hook.py with real Redis findings."""

    def test_inject_findings_returns_thought_blocks(self, monkeypatch):
        """Create findings, set VALOR_WORK_ITEM_SLUG, verify thought blocks."""
        slug = _TEST_SLUG

        # Create findings in Redis
        Finding.safe_save(
            slug=slug,
            project_key="test",
            session_id="build-session",
            stage="BUILD",
            category="pattern_found",
            content="Auth module validates tokens using RSA256 public key",
            file_paths="auth/validator.py",
            importance=7.0,
        )

        # Set the env var that _inject_findings reads
        monkeypatch.setenv("VALOR_WORK_ITEM_SLUG", slug)

        from agent.memory_hook import _inject_findings

        result = _inject_findings("test-session", ["auth", "token", "validator"])

        assert len(result) >= 1
        assert any("<thought>" in block for block in result)
        assert any("RSA256" in block or "Auth module" in block for block in result)
        assert any("Prior finding from BUILD" in block for block in result)

    def test_inject_findings_empty_when_no_slug(self, monkeypatch):
        """Without slug env var and no session, returns empty."""
        monkeypatch.delenv("VALOR_WORK_ITEM_SLUG", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        from agent.memory_hook import _inject_findings

        result = _inject_findings("test-session", ["auth"])
        assert result == []


@pytest.mark.integration
class TestPreDispatchInjection:
    """Test _maybe_inject_findings_into_prompt() with real Redis findings."""

    def test_prompt_augmented_with_prior_findings(self, monkeypatch):
        """Create findings for a slug, simulate pre_tool_use flow, verify prompt augmented."""
        slug = _TEST_SLUG

        # Create findings in Redis
        Finding.safe_save(
            slug=slug,
            project_key="test",
            session_id="build-session",
            stage="BUILD",
            category="decision_made",
            content="Chose WebSocket over polling for real-time updates",
            file_paths="ws/handler.py",
            importance=8.0,
        )

        # Set parent session env var
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-session-test")

        # We need to mock AgentSession since we do not want to create real sessions,
        # but the Finding query itself uses real Redis
        from unittest.mock import MagicMock, patch

        mock_session = MagicMock()
        mock_session.slug = slug
        mock_session.work_item_slug = None
        mock_session.project_key = "test"

        tool_input = {
            "type": "dev-session",
            "prompt": "Run the test suite for the WebSocket feature",
        }

        with patch("models.agent_session.AgentSession.query") as mock_q:
            mock_q.filter.return_value = [mock_session]

            from agent.hooks.pre_tool_use import _maybe_inject_findings_into_prompt

            _maybe_inject_findings_into_prompt(tool_input)

        # Prompt should now contain prior findings
        assert "Prior Findings" in tool_input["prompt"]
        assert "WebSocket" in tool_input["prompt"]
        # Original prompt content should still be there
        assert "Run the test suite" in tool_input["prompt"]

    def test_no_injection_for_non_dev_session(self):
        """Non-dev-session agent types should not get findings injected."""
        from agent.hooks.pre_tool_use import _maybe_inject_findings_into_prompt

        tool_input = {"type": "research", "prompt": "Do research on topic"}
        _maybe_inject_findings_into_prompt(tool_input)
        assert tool_input["prompt"] == "Do research on topic"
