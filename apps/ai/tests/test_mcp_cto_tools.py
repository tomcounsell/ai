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

    # Check for main framework sections
    assert "Step 1: Gather Commit History" in result
    assert "Step 2: Analyze the Data" in result
    assert "Step 3: Generate Executive Summary" in result
    assert "Step 4: Team Recognition" in result
    assert "Step 5: Action Items" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_git_commands():
    """Test weekly_review includes git commands."""
    result = weekly_review()

    # Should contain git commands
    assert "git log" in result
    assert "git shortlog" in result
    assert "--since='1 week ago'" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_categorization_guidance():
    """Test weekly_review includes work categorization."""
    result = weekly_review()

    # Check for work categories
    assert "Features" in result
    assert "Bugs" in result
    assert "Refactoring" in result
    assert "Infrastructure" in result
    assert "Documentation" in result
    assert "Tests" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_metrics_guidance():
    """Test weekly_review includes metrics extraction."""
    result = weekly_review()

    # Check for metrics mentions
    assert "Key Metrics" in result or "metrics" in result.lower()
    assert "Commits per day" in result
    assert "Pull requests" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_summary_template():
    """Test weekly_review includes executive summary template."""
    result = weekly_review()

    # Check for summary sections
    assert "Highlights" in result
    assert "Team Velocity" in result
    assert "Areas of Focus" in result
    assert "Blockers" in result or "Risks" in result
    assert "Next Week Priorities" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_tips():
    """Test weekly_review includes pro tips."""
    result = weekly_review()

    # Check for tips section
    assert "Pro Tips" in result or "Tips" in result
    assert "Fast Reviews" in result or "15 min" in result
    assert "Deep Reviews" in result or "60 min" in result


@pytest.mark.asyncio
async def test_weekly_review_contains_sample_questions():
    """Test weekly_review includes sample questions."""
    result = weekly_review()

    # Check for sample questions section
    assert "Sample Questions" in result or "Questions to Answer" in result
    # Should have questions about achievements, goals, etc.
    assert "?" in result  # Should contain question marks


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
async def test_weekly_review_includes_action_checklist():
    """Test that weekly_review includes action items."""
    result = weekly_review()

    # Check for checklist items
    assert "- [ ]" in result or "[ ]" in result
    assert "Action Items" in result


@pytest.mark.asyncio
async def test_weekly_review_comprehensive_coverage():
    """Test that weekly_review covers all key aspects of team review."""
    result = weekly_review()

    # Verify comprehensive coverage
    coverage_aspects = [
        "commit",  # Git analysis
        "team",  # Team focus
        "velocity",  # Metrics
        "blockers" or "impediments",  # Challenges
        "recognition" or "contributions",  # Team recognition
        "priorities" or "next week",  # Planning
        "executive" or "stakeholder",  # Communication
    ]

    result_lower = result.lower()
    for aspect in coverage_aspects:
        assert aspect in result_lower, f"Missing aspect: {aspect}"
