"""Built-in connector implementations for popular security tools.

These connectors are activated when their environment variables are set.
Supported tools:
- Snyk (SAST)
- AWS Security Hub (CSPM)
"""

import logging
import os

import httpx

from .base_connector import BaseSecurityConnector, CSPMConnector, SASTConnector
from .types import Alert, Asset

logger = logging.getLogger(__name__)


class SnykConnector(SASTConnector):
    """Snyk SAST connector for code vulnerability scanning.

    Environment variables required:
    - SNYK_API_KEY: Your Snyk API token
    - SNYK_ORG_ID: Your Snyk organization ID

    Optional:
    - SNYK_API_URL: Custom API URL (defaults to https://api.snyk.io/v1)
    """

    name = "snyk"

    def __init__(
        self,
        api_key: str,
        org_id: str,
        api_url: str = "https://api.snyk.io/v1",
    ):
        super().__init__(api_key=api_key, api_url=api_url)
        self.org_id = org_id

    async def test_connection(self) -> bool:
        """Test connection to Snyk API."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/orgs/{self.org_id}",
                    headers={"Authorization": f"token {self.api_key}"},
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Snyk connection test failed: {e}")
            return False

    async def fetch_alerts(self, time_window_hours: int = 24) -> list[Alert]:
        """Fetch vulnerability alerts from Snyk.

        Args:
            time_window_hours: Time window for fetching alerts (default: 24 hours)

        Returns:
            List of Alert objects
        """
        try:
            alerts = []

            async with httpx.AsyncClient() as client:
                # Fetch projects
                response = await client.post(
                    f"{self.api_url}/org/{self.org_id}/projects",
                    headers={"Authorization": f"token {self.api_key}"},
                    timeout=30.0,
                )

                if response.status_code != 200:
                    logger.error(f"Failed to fetch Snyk projects: {response.text}")
                    return []

                projects = response.json().get("projects", [])

                # Fetch issues for each project
                for project in projects[:10]:  # Limit to first 10 projects
                    project_id = project.get("id")
                    issues_response = await client.post(
                        f"{self.api_url}/org/{self.org_id}/project/{project_id}/issues",
                        headers={"Authorization": f"token {self.api_key}"},
                        timeout=30.0,
                    )

                    if issues_response.status_code == 200:
                        issues_data = issues_response.json()

                        # Process vulnerabilities
                        for vuln in issues_data.get("issues", {}).get(
                            "vulnerabilities", []
                        ):
                            alert = Alert(
                                id=vuln.get("id", ""),
                                title=vuln.get("title", "Unknown vulnerability"),
                                severity=self._map_severity(
                                    vuln.get("severity", "medium")
                                ),
                                description=vuln.get("description", ""),
                                source="snyk",
                                source_id=vuln.get("id", ""),
                                asset_id=project.get("name", ""),
                                tags=[
                                    "sast",
                                    "code",
                                    vuln.get("packageName", ""),
                                ],
                                created_at=vuln.get("creationTime", ""),
                                updated_at=vuln.get("modificationTime", ""),
                                metadata={
                                    "cvss_score": vuln.get("cvssScore"),
                                    "cve": vuln.get("identifiers", {}).get("CVE", []),
                                    "cwe": vuln.get("identifiers", {}).get("CWE", []),
                                    "exploit_maturity": vuln.get("exploitMaturity"),
                                    "package": vuln.get("packageName"),
                                    "version": vuln.get("version"),
                                },
                            )
                            alerts.append(alert)

            logger.info(f"Fetched {len(alerts)} alerts from Snyk")
            return alerts

        except Exception as e:
            logger.error(f"Failed to fetch Snyk alerts: {e}")
            return []

    async def fetch_assets(self) -> list[Asset]:
        """Fetch asset information from Snyk (projects)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/org/{self.org_id}/projects",
                    headers={"Authorization": f"token {self.api_key}"},
                    timeout=30.0,
                )

                if response.status_code != 200:
                    return []

                projects = response.json().get("projects", [])

                assets = [
                    Asset(
                        id=p.get("id", ""),
                        name=p.get("name", ""),
                        type="repository",
                        environment="production",  # Default, could be customized
                        classification="application",
                        owner="",
                        tags=[p.get("origin", "")],
                        metadata={
                            "branch": p.get("branch"),
                            "origin": p.get("origin"),
                        },
                    )
                    for p in projects
                ]

                return assets

        except Exception as e:
            logger.error(f"Failed to fetch Snyk assets: {e}")
            return []

    def _map_severity(self, snyk_severity: str) -> str:
        """Map Snyk severity to standard severity levels."""
        mapping = {
            "critical": "Critical",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
        }
        return mapping.get(snyk_severity.lower(), "Medium")


class AWSSecurityHubConnector(CSPMConnector):
    """AWS Security Hub connector for cloud security posture management.

    Environment variables required:
    - AWS_REGION: AWS region (e.g., us-east-1)
    - AWS_ACCESS_KEY_ID: AWS access key
    - AWS_SECRET_ACCESS_KEY: AWS secret key

    Optional:
    - AWS_SECURITY_HUB_ACCOUNT_ID: Specific account ID to query
    """

    name = "aws_security_hub"

    def __init__(
        self,
        api_key: str,  # AWS_SECRET_ACCESS_KEY
        api_url: str = "",  # Not used for AWS
        region: str | None = None,
        access_key_id: str | None = None,
        account_id: str | None = None,
    ):
        super().__init__(api_key=api_key, api_url=api_url)
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self.access_key_id = access_key_id or os.environ.get("AWS_ACCESS_KEY_ID", "")
        self.account_id = account_id

    async def test_connection(self) -> bool:
        """Test connection to AWS Security Hub."""
        try:
            # Import boto3 only if AWS connector is used
            import boto3

            client = boto3.client(
                "securityhub",
                region_name=self.region,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.api_key,
            )

            # Simple test: describe hub
            client.describe_hub()
            return True

        except Exception as e:
            logger.error(f"AWS Security Hub connection test failed: {e}")
            return False

    async def fetch_alerts(self, time_window_hours: int = 24) -> list[Alert]:
        """Fetch findings from AWS Security Hub.

        Args:
            time_window_hours: Time window for fetching alerts (default: 24 hours)

        Returns:
            List of Alert objects
        """
        try:
            from datetime import datetime, timedelta, timezone

            import boto3

            client = boto3.client(
                "securityhub",
                region_name=self.region,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.api_key,
            )

            # Calculate time filter
            start_time = datetime.now(timezone.utc) - timedelta(hours=time_window_hours)

            # Build filters
            filters = {
                "RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}],
                "CreatedAt": [
                    {
                        "Start": start_time.isoformat(),
                        "End": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            }

            # Fetch findings
            paginator = client.get_paginator("get_findings")
            findings = []

            for page in paginator.paginate(Filters=filters):
                findings.extend(page.get("Findings", []))

            # Convert to Alert objects
            alerts = []
            for finding in findings:
                alert = Alert(
                    id=finding.get("Id", ""),
                    title=finding.get("Title", ""),
                    severity=self._map_severity(finding.get("Severity", {})),
                    description=finding.get("Description", ""),
                    source="aws_security_hub",
                    source_id=finding.get("Id", ""),
                    asset_id=self._extract_resource_id(finding),
                    tags=[
                        "cspm",
                        "cloud",
                        "aws",
                    ]
                    + finding.get("Types", []),
                    created_at=finding.get("CreatedAt", ""),
                    updated_at=finding.get("UpdatedAt", ""),
                    metadata={
                        "compliance": finding.get("Compliance", {}),
                        "remediation": finding.get("Remediation", {}),
                        "resources": finding.get("Resources", []),
                        "generator_id": finding.get("GeneratorId", ""),
                        "workflow_state": finding.get("WorkflowState", ""),
                    },
                )
                alerts.append(alert)

            logger.info(f"Fetched {len(alerts)} findings from AWS Security Hub")
            return alerts

        except Exception as e:
            logger.error(f"Failed to fetch AWS Security Hub findings: {e}")
            return []

    async def fetch_assets(self) -> list[Asset]:
        """Fetch asset information from AWS Security Hub findings."""
        try:
            import boto3

            client = boto3.client(
                "securityhub",
                region_name=self.region,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.api_key,
            )

            # Get all active findings to extract resources
            filters = {"RecordState": [{"Value": "ACTIVE", "Comparison": "EQUALS"}]}

            paginator = client.get_paginator("get_findings")
            resources = set()

            for page in paginator.paginate(Filters=filters):
                for finding in page.get("Findings", []):
                    for resource in finding.get("Resources", []):
                        resource_id = resource.get("Id", "")
                        if resource_id:
                            resources.add(
                                (
                                    resource_id,
                                    resource.get("Type", "Unknown"),
                                    resource.get("Region", self.region),
                                )
                            )

            # Convert to Asset objects
            assets = [
                Asset(
                    id=res[0],
                    name=res[0].split(":")[-1],  # Extract name from ARN
                    type=res[1],
                    environment="production",  # Default
                    classification="infrastructure",
                    owner="",
                    tags=["aws", res[2]],
                    metadata={"region": res[2]},
                )
                for res in resources
            ]

            return assets

        except Exception as e:
            logger.error(f"Failed to fetch AWS Security Hub assets: {e}")
            return []

    def _map_severity(self, severity_obj: dict) -> str:
        """Map AWS Security Hub severity to standard severity levels."""
        label = severity_obj.get("Label", "MEDIUM")

        mapping = {
            "CRITICAL": "Critical",
            "HIGH": "High",
            "MEDIUM": "Medium",
            "LOW": "Low",
            "INFORMATIONAL": "Low",
        }

        return mapping.get(label.upper(), "Medium")

    def _extract_resource_id(self, finding: dict) -> str:
        """Extract primary resource ID from finding."""
        resources = finding.get("Resources", [])
        if resources:
            return resources[0].get("Id", "")
        return ""


def discover_builtin_connectors() -> dict[str, BaseSecurityConnector]:
    """Discover and instantiate built-in connectors based on environment variables.

    Returns:
        Dictionary of connector name -> connector instance
    """
    connectors = {}

    # Snyk connector
    snyk_api_key = os.environ.get("SNYK_API_KEY")
    snyk_org_id = os.environ.get("SNYK_ORG_ID")

    if snyk_api_key and snyk_org_id:
        try:
            connectors["snyk"] = SnykConnector(
                api_key=snyk_api_key,
                org_id=snyk_org_id,
                api_url=os.environ.get("SNYK_API_URL", "https://api.snyk.io/v1"),
            )
            logger.info("Registered Snyk connector")
        except Exception as e:
            logger.error(f"Failed to initialize Snyk connector: {e}")

    # AWS Security Hub connector
    aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_access = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_region = os.environ.get("AWS_REGION")

    if aws_secret and aws_access and aws_region:
        try:
            connectors["aws_security_hub"] = AWSSecurityHubConnector(
                api_key=aws_secret,
                access_key_id=aws_access,
                region=aws_region,
                account_id=os.environ.get("AWS_SECURITY_HUB_ACCOUNT_ID"),
            )
            logger.info("Registered AWS Security Hub connector")
        except Exception as e:
            logger.error(f"Failed to initialize AWS Security Hub connector: {e}")

    return connectors
