"""Linear GraphQL API client for ticket creation."""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LinearClient:
    """Client for Linear GraphQL API."""

    def __init__(self, api_key: str | None = None):
        """Initialize Linear client.

        Args:
            api_key: Linear API key (defaults to LINEAR_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("LINEAR_API_KEY", "")
        self.api_url = "https://api.linear.app/graphql"
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    async def create_ticket(self, risk: Any) -> str | None:
        """Create a Linear issue from a risk.

        Args:
            risk: Risk object with details

        Returns:
            Ticket ID if successful, None otherwise
        """
        if not self.api_key:
            logger.warning("LINEAR_API_KEY not configured, skipping ticket creation")
            return None

        try:
            # Build ticket description
            description = self._build_ticket_description(risk)

            # Map severity to Linear priority
            priority = self._severity_to_priority(risk.severity)

            # Create the issue via GraphQL mutation
            mutation = """
                mutation CreateIssue($input: IssueCreateInput!) {
                    issueCreate(input: $input) {
                        success
                        issue {
                            id
                            identifier
                            title
                            url
                        }
                    }
                }
            """

            variables = {
                "input": {
                    "title": f"[Security] {risk.title}",
                    "description": description,
                    "priority": priority,
                    # Team ID would be configurable in production
                    # For now, we'll let it go to the default team
                    "labelIds": [],  # Would add security labels here
                }
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    json={"query": mutation, "variables": variables},
                    headers=self.headers,
                    timeout=30.0,
                )

                response.raise_for_status()
                data = response.json()

                if data.get("data", {}).get("issueCreate", {}).get("success"):
                    issue = data["data"]["issueCreate"]["issue"]
                    ticket_id = issue["identifier"]
                    logger.info(f"Created Linear ticket: {ticket_id}")
                    return ticket_id
                else:
                    errors = data.get("errors", [])
                    logger.error(f"Failed to create Linear ticket: {errors}")
                    return None

        except Exception as e:
            logger.error(f"Error creating Linear ticket: {e}")
            return None

    async def test_connection(self) -> bool:
        """Test if Linear API is accessible.

        Returns:
            True if connection successful, False otherwise
        """
        if not self.api_key:
            return False

        try:
            query = """
                query {
                    viewer {
                        id
                        name
                    }
                }
            """

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    json={"query": query},
                    headers=self.headers,
                    timeout=10.0,
                )

                response.raise_for_status()
                data = response.json()

                return "data" in data and "viewer" in data["data"]

        except Exception as e:
            logger.error(f"Linear connection test failed: {e}")
            return False

    async def find_existing_ticket(self, risk_id: str) -> str | None:
        """Find existing ticket for a risk ID.

        Args:
            risk_id: Risk ID to search for

        Returns:
            Ticket ID if found, None otherwise
        """
        try:
            query = """
                query SearchIssues($filter: IssueFilter) {
                    issues(filter: $filter) {
                        nodes {
                            id
                            identifier
                            title
                        }
                    }
                }
            """

            variables = {"filter": {"title": {"contains": risk_id}}}

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    json={"query": query, "variables": variables},
                    headers=self.headers,
                    timeout=10.0,
                )

                response.raise_for_status()
                data = response.json()

                issues = data.get("data", {}).get("issues", {}).get("nodes", [])
                if issues:
                    return issues[0]["identifier"]

                return None

        except Exception as e:
            logger.error(f"Error searching for existing ticket: {e}")
            return None

    def _build_ticket_description(self, risk: Any) -> str:
        """Build markdown description for Linear ticket.

        Args:
            risk: Risk object

        Returns:
            Formatted markdown description
        """
        description = f"**Risk ID**: {risk.risk_id}\n"
        description += f"**Severity**: {risk.severity}\n"
        description += f"**Risk Score**: {risk.score:.1f}/100\n\n"

        description += "## Description\n\n"
        description += f"{risk.description}\n\n"

        if risk.linked_alerts:
            description += "## Linked Alerts\n\n"
            for alert_id in risk.linked_alerts:
                description += f"- {alert_id}\n"
            description += "\n"

        if risk.policy_violations:
            description += "## Policy Violations\n\n"
            for policy in risk.policy_violations:
                description += f"- {policy}\n"
            description += "\n"

        if risk.affected_assets:
            description += "## Affected Assets\n\n"
            for asset in risk.affected_assets:
                description += f"- {asset}\n"
            description += "\n"

        if risk.actions:
            description += "## Recommended Actions\n\n"
            for action in risk.actions:
                assignee_str = f" (→ {action.assignee})" if action.assignee else ""
                description += f"- [{action.priority}] {action.action}{assignee_str}\n"
            description += "\n"

        description += "---\n"
        description += (
            "*This ticket was automatically created by CTO Tools Security Review*\n"
        )

        return description

    def _severity_to_priority(self, severity: str) -> int:
        """Map risk severity to Linear priority.

        Linear priorities:
        0 = No priority
        1 = Urgent
        2 = High
        3 = Medium
        4 = Low

        Args:
            severity: Risk severity level

        Returns:
            Linear priority number
        """
        mapping = {
            "Critical": 1,  # Urgent
            "High": 2,  # High
            "Medium": 3,  # Medium
            "Low": 4,  # Low
        }
        return mapping.get(severity, 0)
