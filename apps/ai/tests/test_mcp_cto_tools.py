"""
Tests for CTO Tools MCP Server.
"""

import inspect

import pytest

from apps.ai.mcp.cto_tools_server import weekly_review


@pytest.mark.asyncio
async def test_weekly_review_returns_string():
    """Test weekly_review returns a string."""
    result = weekly_review()

    assert isinstance(result, str)
    assert len(result) > 100  # Should be substantial content


@pytest.mark.asyncio
async def test_weekly_review_contains_framework_sections():
    """Test weekly_review contains expected framework sections."""
    result = weekly_review()

    # Check for main framework phases (3 phases - data, analysis, summaries with team recognition)
    assert "PHASE 1: GATHER DATA" in result
    assert "PHASE 2: ANALYZE & CATEGORIZE" in result
    assert "PHASE 3: CREATE SUMMARIES" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_git_commands():
    """Test weekly_review includes git commands."""
    result = weekly_review()

    # Should contain git commands
    assert "git log" in result
    assert '--since="7 days ago"' in result
    assert "--no-merges" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_categorization_guidance():
    """Test weekly_review includes work categorization guidance with proper LLM workflow."""
    result = weekly_review()

    # Check for proper LLM workflow: draft first, then categorize
    assert "Draft All Updates" in result or "working draft" in result.lower()
    assert "Identify Natural Groupings" in result or "natural" in result.lower()
    assert "Choose Category Names" in result or "choose" in result.lower()
    assert "Category suggestions" in result or "suggestions" in result.lower()

    # Should have expanded category list
    assert "DevOps" in result or "API" in result or "Billing" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_metrics_guidance():
    """Test weekly_review includes metrics extraction."""
    result = weekly_review()

    # Check for metrics mentions
    assert "metrics" in result.lower()
    assert "commits" in result.lower()
    assert "contributors" in result.lower()


@pytest.mark.asyncio
async def test_weekly_review_contains_summary_template():
    """Test weekly_review includes executive summary template."""
    result = weekly_review()

    # Check for summary sections (3-phase framework with integrated team recognition)
    assert "Technical Summary" in result
    assert "Executive Summary" in result
    assert "Quick Summary" in result
    assert "Team Recognition" in result
    assert "Overview" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_tips():
    """Test weekly_review includes pro tips."""
    result = weekly_review()

    # Check for tips section
    assert "EXECUTION TIPS" in result or "Pro Tips" in result
    assert "Speed" in result or "15-20 min" in result
    assert "Depth" in result or "45-60 min" in result


@pytest.mark.asyncio
async def test_weekly_review_is_not_async():
    """Test that weekly_review is a synchronous function."""
    # Unlike Creative Juices tools which are async, this is sync
    assert not inspect.iscoroutinefunction(weekly_review)


@pytest.mark.asyncio
async def test_weekly_review_deterministic():
    """Test that weekly_review returns the same content each time."""
    result1 = weekly_review()
    result2 = weekly_review()

    # Should be identical since it's just returning static instructions
    assert result1 == result2


@pytest.mark.asyncio
async def test_weekly_review_formatted_as_markdown():
    """Test that weekly_review content is formatted as markdown."""
    result = weekly_review()

    # Check for markdown formatting
    assert "##" in result  # Headers
    assert "```" in result  # Code blocks
    assert "- " in result or "* " in result  # Bullet points
    assert "**" in result  # Bold text


@pytest.mark.asyncio
async def test_weekly_review_comprehensive_coverage():
    """Test that weekly_review covers all key aspects of team review."""
    result = weekly_review()

    # Verify comprehensive coverage (3-phase: data, analysis, summaries+team)
    coverage_aspects = [
        "commit",  # Git analysis
        "team",  # Team focus
        "metrics",  # Metrics
        "recognition",  # Team recognition
        "categor",  # Categorization (matches "categorize" or "categories")
        "summary",  # Multi-level summaries
    ]

    result_lower = result.lower()
    for aspect in coverage_aspects:
        assert aspect in result_lower, f"Missing aspect: {aspect}"
