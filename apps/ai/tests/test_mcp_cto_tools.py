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

    # Check for main framework phases (3 phases - data, analysis, output)
    assert "PHASE 1: GATHER DATA" in result
    assert "PHASE 2: ANALYZE INTERNALLY" in result
    assert "PHASE 3: WRITE THE FINAL SUMMARY" in result


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
    """Test weekly_review includes work categorization guidance."""
    result = weekly_review()

    # Check for category guidance
    assert "Choose 5 categories" in result or "5 categories" in result
    assert "Category ideas" in result or "category" in result.lower()

    # Should have category suggestions
    assert "DevOps" in result or "API" in result or "Testing" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_stats_guidance():
    """Test weekly_review includes stats extraction."""
    result = weekly_review()

    # Check for stats mentions
    assert "stats" in result.lower()
    assert "commits" in result.lower()
    assert "contributors" in result.lower()


@pytest.mark.asyncio
async def test_weekly_review_contains_output_template():
    """Test weekly_review includes structured output template."""
    result = weekly_review()

    # Check for plain text format and team statistics
    assert "• **" in result  # Bullet points with bold
    assert "Team Statistics" in result or "contributors" in result.lower()
    assert "commits" in result.lower()


@pytest.mark.asyncio
async def test_weekly_review_emphasizes_structured_output():
    """Test weekly_review emphasizes structured technical output."""
    result = weekly_review()

    # Check for output expectations
    assert "OUTPUT EXPECTATIONS" in result or "structured" in result.lower()
    assert "Plain text" in result or "plain text" in result.lower()
    assert "NOT multiple pages" in result or "not multiple" in result.lower()


@pytest.mark.asyncio
async def test_weekly_review_is_not_async():
    """Test that weekly_review is a synchronous function."""
    # Unlike Creative Juices tools which are async, this is sync
    assert not inspect.iscoroutinefunction(weekly_review)


@pytest.mark.asyncio
async def test_weekly_review_deterministic():
    """Test that weekly_review returns the same content each time with same params."""
    result1 = weekly_review(days=7, categories=5)
    result2 = weekly_review(days=7, categories=5)

    # Should be identical with same parameters
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

    # Verify comprehensive coverage (data, analysis, output)
    coverage_aspects = [
        "commit",  # Git analysis
        "contributors",  # Team focus
        "categor",  # Categorization (matches "categorize" or "categories")
        "bullet",  # Bullet point format
        "structured",  # Emphasis on structured output
    ]

    result_lower = result.lower()
    for aspect in coverage_aspects:
        assert aspect in result_lower, f"Missing aspect: {aspect}"


@pytest.mark.asyncio
async def test_weekly_review_custom_days_parameter():
    """Test that weekly_review accepts custom days parameter."""
    result_7 = weekly_review(days=7)
    result_14 = weekly_review(days=14)
    result_30 = weekly_review(days=30)

    # Should include the specified number of days in git commands
    assert '--since="7 days ago"' in result_7
    assert '--since="14 days ago"' in result_14
    assert '--since="30 days ago"' in result_30

    # Should mention the number of days in the goal section
    assert "past 7 days" in result_7
    assert "past 14 days" in result_14
    assert "past 30 days" in result_30


@pytest.mark.asyncio
async def test_weekly_review_custom_categories_parameter():
    """Test that weekly_review accepts custom categories parameter."""
    result_3 = weekly_review(categories=3)
    result_5 = weekly_review(categories=5)
    result_7 = weekly_review(categories=7)

    # Should mention the number of categories in the goal
    assert "organized by 3 categories" in result_3
    assert "organized by 5 categories" in result_5
    assert "organized by 7 categories" in result_7

    # Should mention choosing the right number of categories
    assert "Choose 3 categories" in result_3
    assert "Choose 5 categories" in result_5
    assert "Choose 7 categories" in result_7


@pytest.mark.asyncio
async def test_weekly_review_default_parameters():
    """Test that weekly_review uses correct defaults."""
    result_default = weekly_review()
    result_explicit = weekly_review(days=7, categories=5)

    # Defaults should match explicit 7 days and 5 categories
    assert result_default == result_explicit


@pytest.mark.asyncio
async def test_weekly_review_plain_text_format():
    """Test that weekly_review outputs plain text format, not RTF."""
    result = weekly_review()

    # Should mention plain text format
    assert "plain text" in result.lower() or ".txt" in result

    # Should NOT mention RTF
    assert "rtf" not in result.lower()
    assert "\\par" not in result
    assert "\\rtf1" not in result

    # Should mention Unicode emoji support
    assert "Unicode emoji" in result or "unicode emoji" in result.lower()
