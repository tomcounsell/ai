"""
CTO Tools MCP Server implementation using FastMCP.

Provides security review and risk correlation tools for CTOs and security teams.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("CTO Tools")


# Response schemas
class RiskAction(BaseModel):
    """Recommended action for risk remediation."""

    action: str
    assignee: str | None = None
    priority: Literal["Low", "Medium", "High", "Critical"]


class Risk(BaseModel):
    """Correlated security risk with scoring and context."""

    risk_id: str
    severity: Literal["Low", "Medium", "High", "Critical"]
    title: str
    description: str
    linked_alerts: list[str] = Field(default_factory=list)
    policy_violations: list[str] = Field(default_factory=list)
    affected_assets: list[str] = Field(default_factory=list)
    actions: list[RiskAction] = Field(default_factory=list)
    ticket_id: str | None = None
    score: float = Field(ge=0, le=100)  # 0-100 scale


class SecurityReviewResponse(BaseModel):
    """Response from security_review tool."""

    summary: str
    risks: list[Risk] = Field(default_factory=list)
    total_alerts_reviewed: int
    correlation_confidence: float = Field(ge=0, le=1)
    timestamp: str


# Tool implementations
@mcp.tool()
async def security_review(
    query: str,
    time_window_hours: int = 72,
    min_severity: Literal["Low", "Medium", "High", "Critical"] = "Medium",
    data_types: list[str] | None = None,
    create_tickets: bool = False,
    max_results: int = 10,
) -> str:
    """
    Correlate security alerts across multiple tools with policy context.

    This tool integrates with security scanners (SAST/DAST), cloud posture
    management tools (CSPM), threat intelligence feeds, and compliance/policy
    documents to identify, score, and prioritize security risks.

    Args:
        query: Natural language query describing the security review scope
               (e.g., "top 3 high-severity PII risks in last 72h")
        time_window_hours: How many hours back to review alerts (default: 72)
        min_severity: Minimum severity level to include (default: Medium)
        data_types: Filter by data types (e.g., ["PII", "credentials"])
        create_tickets: Reserved for future Linear integration (not yet implemented)
        max_results: Maximum number of risks to return (default: 10)

    Returns:
        Natural language summary with structured JSON of correlated risks
    """
    from .security.correlation_engine import SecurityCorrelationEngine
    from .security.risk_scorer import RiskScorer

    logger.info(
        f"Security review requested: query='{query}', "
        f"time_window={time_window_hours}h, min_severity={min_severity}"
    )

    # Initialize components
    correlation_engine = SecurityCorrelationEngine()
    risk_scorer = RiskScorer()

    # Calculate time window
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=time_window_hours)

    # Parse query and extract filter criteria
    filters = correlation_engine.parse_query(
        query=query,
        start_time=start_time,
        end_time=end_time,
        min_severity=min_severity,
        data_types=data_types,
    )

    # Fetch alerts from all connected tools
    alerts = await correlation_engine.fetch_alerts(filters)

    # Fetch relevant policy documents
    policies = await correlation_engine.fetch_policies(filters)

    # Correlate alerts with assets and policies
    correlations = await correlation_engine.correlate(
        alerts=alerts,
        policies=policies,
        query=query,
    )

    # Score each risk
    risks = []
    for correlation in correlations[: max_results]:
        risk = risk_scorer.score_risk(correlation)
        risks.append(risk)

    # Create Linear tickets if requested
    # TODO: Linear integration requires user authentication and secure API key storage
    # See backlog item for multi-tenant Linear support
    if create_tickets:
        logger.warning(
            "Linear ticket creation requested but not yet supported. "
            "This requires user authentication and secure credential storage. "
            "See docs/plans/cto-tools-security-review.md for details."
        )

    # Generate response
    response = SecurityReviewResponse(
        summary=correlation_engine.generate_summary(risks, query),
        risks=risks,
        total_alerts_reviewed=len(alerts),
        correlation_confidence=correlation_engine.calculate_confidence(correlations),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Format output as natural language + JSON
    output = f"{response.summary}\n\n"
    output += f"**Alerts Reviewed**: {response.total_alerts_reviewed}\n"
    output += f"**Correlation Confidence**: {response.correlation_confidence:.0%}\n"
    output += f"**Timestamp**: {response.timestamp}\n\n"

    if response.risks:
        output += "## Top Risks\n\n"
        for risk in response.risks:
            output += f"### {risk.risk_id}: {risk.title}\n"
            output += f"**Severity**: {risk.severity} (Score: {risk.score:.1f}/100)\n"
            output += f"{risk.description}\n\n"

            if risk.policy_violations:
                output += f"**Policy Violations**: {', '.join(risk.policy_violations)}\n"
            if risk.affected_assets:
                output += f"**Affected Assets**: {', '.join(risk.affected_assets)}\n"
            if risk.linked_alerts:
                output += f"**Linked Alerts**: {', '.join(risk.linked_alerts)}\n"

            if risk.actions:
                output += "\n**Recommended Actions**:\n"
                for action in risk.actions:
                    assignee_str = f" (→ {action.assignee})" if action.assignee else ""
                    output += f"- [{action.priority}] {action.action}{assignee_str}\n"

            if risk.ticket_id:
                output += f"\n**Ticket Created**: {risk.ticket_id}\n"

            output += "\n"
    else:
        output += "No risks found matching the specified criteria.\n"

    # Append structured JSON
    output += "\n---\n\n"
    output += "**Structured Data (JSON)**:\n```json\n"
    output += response.model_dump_json(indent=2)
    output += "\n```\n"

    return output


@mcp.tool()
async def list_connectors() -> str:
    """
    List all available security tool connectors and their status.

    Returns information about connected SAST/DAST tools, CSPM tools,
    threat intelligence feeds, and policy document sources.

    Returns:
        Formatted list of connectors with status and capabilities
    """
    from .security.connector_registry import ConnectorRegistry

    registry = ConnectorRegistry()
    connectors = registry.list_all()

    output = "## Available Security Tool Connectors\n\n"

    for connector in connectors:
        status_emoji = "✅" if connector["status"] == "connected" else "❌"
        output += f"### {status_emoji} {connector['name']}\n"
        output += f"**Type**: {connector['type']}\n"
        output += f"**Status**: {connector['status']}\n"

        if connector.get("capabilities"):
            output += f"**Capabilities**: {', '.join(connector['capabilities'])}\n"

        if connector.get("last_sync"):
            output += f"**Last Sync**: {connector['last_sync']}\n"

        output += "\n"

    return output


@mcp.tool()
async def configure_connector(
    connector_type: Literal["sast", "dast", "cspm", "threat_intel", "policy"],
    connector_name: str,
    api_key: str,
    api_url: str | None = None,
    additional_config: dict | None = None,
) -> str:
    """
    Configure a new security tool connector.

    Args:
        connector_type: Type of security tool (sast, dast, cspm, threat_intel, policy)
        connector_name: Name/identifier for this connector
        api_key: API key or authentication token
        api_url: Optional API URL if not using default
        additional_config: Additional connector-specific configuration

    Returns:
        Confirmation message with connector details
    """
    from .security.connector_registry import ConnectorRegistry

    registry = ConnectorRegistry()

    config = {
        "api_key": api_key,
        "api_url": api_url,
        **(additional_config or {}),
    }

    success = await registry.register_connector(
        connector_type=connector_type,
        name=connector_name,
        config=config,
    )

    if success:
        return (
            f"✅ Successfully configured {connector_name} ({connector_type})\n"
            f"The connector is now available for security reviews."
        )
    else:
        return f"❌ Failed to configure {connector_name}. Check logs for details."


def main():
    """Main entry point for the MCP server.

    Supports two modes:
    - stdio (default): For local development and testing
    - streamable-http: For production hosting at ai.yuda.me

    Set MCP_TRANSPORT environment variable to switch modes.
    """
    import os

    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "streamable-http":
        # Production mode - HTTP transport for hosting at ai.yuda.me
        logger.info("Starting CTO Tools MCP server in HTTP mode")
        mcp.run(transport="streamable-http")
    else:
        # Development mode - stdio transport
        logger.info("Starting CTO Tools MCP server in stdio mode")
        mcp.run()


if __name__ == "__main__":
    main()
