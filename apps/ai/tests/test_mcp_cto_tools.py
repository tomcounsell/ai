"""
Tests for CTO Tools MCP Server.
"""

import inspect

import pytest

from apps.ai.mcp.cto_tools_server import (
    architecture_review,
    configure_connector,
    list_connectors,
    security_review,
    weekly_review,
)


# Weekly Review Tool Tests


def test_weekly_review_returns_string():
    """Test weekly_review returns a string."""
    result = weekly_review()

    assert isinstance(result, str)
    assert len(result) > 100  # Should be substantial content


def test_weekly_review_contains_framework_sections():
    """Test weekly_review contains expected framework sections."""
    result = weekly_review()

    # Check for main framework phases (3 phases - data, analysis, output)
    assert "PHASE 1: GATHER DATA" in result
    assert "PHASE 2: ANALYZE INTERNALLY" in result
    assert "PHASE 3: WRITE THE FINAL SUMMARY" in result


def test_weekly_review_contains_git_commands():
    """Test weekly_review includes git commands."""
    result = weekly_review()

    # Should contain git commands
    assert "git log" in result
    assert '--since="7 days ago"' in result
    assert "--no-merges" in result


def test_weekly_review_contains_categorization_guidance():
    """Test weekly_review includes work categorization guidance."""
    result = weekly_review()

    # Check for category guidance
    assert "Choose 5 categories" in result or "5 categories" in result
    assert "Category ideas" in result or "category" in result.lower()

    # Should have category suggestions
    assert "DevOps" in result or "API" in result or "Testing" in result


def test_weekly_review_contains_stats_guidance():
    """Test weekly_review includes stats extraction."""
    result = weekly_review()

    # Check for stats mentions
    assert "stats" in result.lower()
    assert "commits" in result.lower()
    assert "contributors" in result.lower()


def test_weekly_review_contains_output_template():
    """Test weekly_review includes structured output template."""
    result = weekly_review()

    # Check for plain text format and team statistics
    assert "• **" in result  # Bullet points with bold
    assert "Team Statistics" in result or "contributors" in result.lower()
    assert "commits" in result.lower()


def test_weekly_review_emphasizes_structured_output():
    """Test weekly_review emphasizes structured technical output."""
    result = weekly_review()

    # Check for output expectations
    assert "OUTPUT EXPECTATIONS" in result or "structured" in result.lower()
    assert "Plain text" in result or "plain text" in result.lower()
    assert "NOT multiple pages" in result or "not multiple" in result.lower()


def test_weekly_review_is_not_async():
    """Test that weekly_review is a synchronous function."""
    # Unlike security_review which is async, this is sync
    assert not inspect.iscoroutinefunction(weekly_review)


def test_weekly_review_deterministic():
    """Test that weekly_review returns the same content each time with same params."""
    result1 = weekly_review(days=7, categories=5)
    result2 = weekly_review(days=7, categories=5)

    # Should be identical with same parameters
    assert result1 == result2


def test_weekly_review_formatted_as_markdown():
    """Test that weekly_review content is formatted as markdown."""
    result = weekly_review()

    # Check for markdown formatting
    assert "##" in result  # Headers
    assert "```" in result  # Code blocks
    assert "- " in result or "* " in result  # Bullet points
    assert "**" in result  # Bold text


def test_weekly_review_comprehensive_coverage():
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


def test_weekly_review_custom_days_parameter():
    """Test that weekly_review accepts custom days parameter."""
    result_7 = weekly_review(days=7)
    result_14 = weekly_review(days=14)
    result_30 = weekly_review(days=30)

    # Should include the specified number of days in git commands
    assert '--since="7 days ago"' in result_7
    assert '--since="14 days ago"' in result_14
    assert '--since="30 days ago"' in result_30


def test_weekly_review_custom_categories_parameter():
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


def test_weekly_review_default_parameters():
    """Test that weekly_review uses correct defaults."""
    result_default = weekly_review()
    result_explicit = weekly_review(days=7, categories=5)

    # Defaults should match explicit 7 days and 5 categories
    assert result_default == result_explicit


def test_weekly_review_plain_text_format():
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


# Architecture Review Tool Tests


def test_architecture_review_returns_string():
    """Test architecture_review returns a string."""
    result = architecture_review()

    assert isinstance(result, str)
    assert len(result) > 100  # Should be substantial content


def test_architecture_review_contains_framework_sections():
    """Test architecture_review contains expected framework sections."""
    result = architecture_review()

    # Check for main framework phases
    assert "PHASE 1: EXPLORE THE CODEBASE" in result
    assert "PHASE 2: ANALYZE ARCHITECTURE" in result
    assert "PHASE 3: WRITE THE ARCHITECTURE DOCUMENT" in result


def test_architecture_review_contains_exploration_commands():
    """Test architecture_review includes exploration commands."""
    result = architecture_review()

    # Should contain exploration commands
    assert "find" in result
    assert "ls" in result
    assert "pyproject.toml" in result or "package.json" in result


def test_architecture_review_contains_diagram_guidance():
    """Test architecture_review includes diagram guidance when enabled."""
    result = architecture_review(include_diagrams=True)

    # Check for diagram guidance
    assert "mermaid" in result.lower()
    assert "C4" in result or "c4" in result.lower()
    assert "sequence" in result.lower()


def test_architecture_review_diagrams_optional():
    """Test architecture_review can exclude diagram guidance."""
    result_with = architecture_review(include_diagrams=True)
    result_without = architecture_review(include_diagrams=False)

    # With diagrams should be longer and contain mermaid
    assert len(result_with) > len(result_without)
    assert "mermaid" in result_with.lower()


def test_architecture_review_is_not_async():
    """Test that architecture_review is a synchronous function."""
    assert not inspect.iscoroutinefunction(architecture_review)


def test_architecture_review_deterministic():
    """Test that architecture_review returns the same content with same params."""
    result1 = architecture_review(focus="system", depth="detailed")
    result2 = architecture_review(focus="system", depth="detailed")

    # Should be identical with same parameters
    assert result1 == result2


def test_architecture_review_formatted_as_markdown():
    """Test that architecture_review content is formatted as markdown."""
    result = architecture_review()

    # Check for markdown formatting
    assert "##" in result  # Headers
    assert "```" in result  # Code blocks
    assert "**" in result  # Bold text


def test_architecture_review_focus_parameter():
    """Test that architecture_review adapts to focus parameter."""
    result_system = architecture_review(focus="system")
    result_api = architecture_review(focus="api")
    result_data = architecture_review(focus="data")
    result_security = architecture_review(focus="security")

    # Each should mention its focus area
    assert "system" in result_system.lower()
    assert "api" in result_api.lower()
    assert "data" in result_data.lower()
    assert "security" in result_security.lower()


def test_architecture_review_depth_parameter():
    """Test that architecture_review adapts to depth parameter."""
    result_overview = architecture_review(depth="overview")
    result_detailed = architecture_review(depth="detailed")
    result_deep = architecture_review(depth="deep-dive")

    # Each should mention its depth level
    assert "overview" in result_overview.lower()
    assert "detailed" in result_detailed.lower()
    assert "deep-dive" in result_deep.lower()


def test_architecture_review_contains_document_template():
    """Test architecture_review includes a document template."""
    result = architecture_review()

    # Check for document structure sections
    assert "Executive Summary" in result
    assert "Key Components" in result
    assert "Recommendations" in result


def test_architecture_review_contains_quality_checklist():
    """Test architecture_review includes a quality checklist."""
    result = architecture_review()

    # Check for quality checklist
    assert "QUALITY CHECKLIST" in result or "checklist" in result.lower()


def test_architecture_review_default_parameters():
    """Test that architecture_review uses correct defaults."""
    result_default = architecture_review()
    result_explicit = architecture_review(
        focus="system", depth="detailed", include_diagrams=True
    )

    # Defaults should match explicit values
    assert result_default == result_explicit


# Security Review Tool Tests


@pytest.mark.asyncio
async def test_security_review_basic_query():
    """Test security_review with a basic query."""
    result = await security_review(query="What are the critical security risks?")

    assert isinstance(result, str)
    assert len(result) > 100
    assert "Alerts Reviewed" in result
    assert "Correlation Confidence" in result


@pytest.mark.asyncio
async def test_security_review_returns_risks():
    """Test security_review returns risk information."""
    result = await security_review(
        query="PII-related risks", min_severity="Medium", time_window_hours=72
    )

    assert (
        "Risk Summary" in result or "Top Risk" in result or "No risks found" in result
    )
    assert "Structured Data (JSON)" in result


@pytest.mark.asyncio
async def test_security_review_with_severity_filter():
    """Test security_review respects severity filter."""
    result_critical = await security_review(
        query="all risks", min_severity="Critical", max_results=5
    )
    result_low = await security_review(
        query="all risks", min_severity="Low", max_results=5
    )

    # Both should complete successfully
    assert "Alerts Reviewed" in result_critical
    assert "Alerts Reviewed" in result_low


@pytest.mark.asyncio
async def test_security_review_with_data_types():
    """Test security_review with data type filters."""
    result = await security_review(
        query="exposed data", data_types=["PII", "credentials"], max_results=3
    )

    assert isinstance(result, str)
    assert "Alerts Reviewed" in result


@pytest.mark.asyncio
async def test_security_review_json_output():
    """Test security_review includes JSON structured data."""
    result = await security_review(query="security risks", max_results=2)

    assert "```json" in result
    assert "summary" in result.lower()
    assert "risks" in result.lower()


@pytest.mark.asyncio
async def test_security_review_with_ticket_creation():
    """Test security_review with ticket creation flag."""
    # Note: Ticket creation not yet implemented (requires multi-tenant auth)
    # Tool should handle gracefully and log warning
    result = await security_review(
        query="critical risks", create_tickets=True, min_severity="Critical"
    )

    assert isinstance(result, str)
    # Should still work even though ticket creation is not supported yet


@pytest.mark.asyncio
async def test_list_connectors_basic():
    """Test list_connectors returns connector information."""
    result = await list_connectors()

    assert isinstance(result, str)
    assert (
        "Available Security Tool Connectors" in result or "connector" in result.lower()
    )


@pytest.mark.asyncio
async def test_list_connectors_shows_demo_connectors():
    """Test list_connectors shows demo connectors."""
    result = await list_connectors()

    # Should show at least the demo connectors
    assert "demo" in result.lower() or "Demo" in result


@pytest.mark.asyncio
async def test_configure_connector_basic():
    """Test configure_connector with valid parameters."""
    result = await configure_connector(
        connector_type="sast",
        connector_name="test_sast",
        api_key="test_key_123",
        api_url="https://example.com/api",
    )

    assert isinstance(result, str)
    # Should either succeed or fail gracefully
    assert "configured" in result.lower() or "failed" in result.lower()


@pytest.mark.asyncio
async def test_configure_connector_types():
    """Test configure_connector accepts all connector types."""
    connector_types = ["sast", "dast", "cspm", "threat_intel", "policy"]

    for conn_type in connector_types:
        result = await configure_connector(
            connector_type=conn_type,
            connector_name=f"test_{conn_type}",
            api_key="test_key",
        )
        assert isinstance(result, str)
