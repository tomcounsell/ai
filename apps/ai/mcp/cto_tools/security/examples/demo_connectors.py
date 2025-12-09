"""
Example connector implementations for reference.

These demonstrate how to build custom connectors but are NOT automatically loaded.
To use these as templates, copy them to apps/ai/mcp/security/connectors/

See apps/ai/mcp/security/connectors/README.md for more details.
"""

import uuid
from datetime import datetime, timezone

from ..base_connector import CSPMConnector, PolicyConnector, SASTConnector
from ..types import Alert, Asset, Policy, QueryFilters


class DemoSASTConnector(SASTConnector):
    """Demo SAST connector with synthetic vulnerability data."""

    async def test_connection(self) -> bool:
        """Demo connection always succeeds."""
        return True

    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        """Generate demo SAST alerts."""
        # Generate synthetic vulnerability alerts
        demo_alerts = [
            Alert(
                alert_id=f"SAST-{uuid.uuid4().hex[:8]}",
                source="Demo SAST Scanner",
                severity="High",
                title="SQL Injection vulnerability in user authentication",
                description=(
                    "Unsanitized user input in login form allows SQL injection. "
                    "Attacker could bypass authentication or extract database contents."
                ),
                detected_at=datetime.now(timezone.utc),
                alert_type="vulnerability",
                asset_id="repo-auth-module",
                asset_type="code_module",
                cvss_score=8.5,
                cwe_id="CWE-89",
                metadata={
                    "file": "src/auth/login.py",
                    "line": 42,
                    "confidence": "high",
                },
            ),
            Alert(
                alert_id=f"SAST-{uuid.uuid4().hex[:8]}",
                source="Demo SAST Scanner",
                severity="Critical",
                title="Hardcoded credentials in payment processing module",
                description=(
                    "Database credentials are hardcoded in payment processing code. "
                    "This violates security best practices and exposes sensitive data."
                ),
                detected_at=datetime.now(timezone.utc),
                alert_type="vulnerability",
                asset_id="repo-payment-module",
                asset_type="code_module",
                cvss_score=9.1,
                cwe_id="CWE-798",
                metadata={
                    "file": "src/payments/processor.py",
                    "line": 156,
                    "confidence": "high",
                },
            ),
            Alert(
                alert_id=f"SAST-{uuid.uuid4().hex[:8]}",
                source="Demo SAST Scanner",
                severity="Medium",
                title="Insufficient input validation in user profile API",
                description=(
                    "User profile API endpoints accept unvalidated input that could "
                    "lead to XSS or data corruption."
                ),
                detected_at=datetime.now(timezone.utc),
                alert_type="vulnerability",
                asset_id="repo-api-module",
                asset_type="code_module",
                cvss_score=6.3,
                cwe_id="CWE-20",
                metadata={
                    "file": "src/api/profiles.py",
                    "line": 89,
                    "confidence": "medium",
                },
            ),
        ]

        # Filter by severity
        severity_order = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
        min_severity_level = severity_order.get(filters.min_severity, 0)

        filtered = [
            alert
            for alert in demo_alerts
            if severity_order.get(alert.severity, 0) >= min_severity_level
            and filters.start_time <= alert.detected_at <= filters.end_time
        ]

        return filtered

    async def fetch_assets(self, asset_ids: list[str]) -> list[Asset]:
        """Fetch demo code module assets."""
        demo_assets = {
            "repo-auth-module": Asset(
                asset_id="repo-auth-module",
                asset_type="code_module",
                name="Authentication Module",
                environment="production",
                classification=["PII", "authentication"],
                owner_team="platform-security",
                metadata={"repository": "main-app", "language": "python"},
            ),
            "repo-payment-module": Asset(
                asset_id="repo-payment-module",
                asset_type="code_module",
                name="Payment Processing Module",
                environment="production",
                classification=["PII", "PCI-DSS", "financial"],
                owner_team="payments",
                metadata={"repository": "payment-service", "language": "python"},
            ),
            "repo-api-module": Asset(
                asset_id="repo-api-module",
                asset_type="code_module",
                name="User Profile API",
                environment="production",
                classification=["PII"],
                owner_team="backend-api",
                metadata={"repository": "api-service", "language": "python"},
            ),
        }

        return [
            demo_assets[asset_id] for asset_id in asset_ids if asset_id in demo_assets
        ]


class DemoCSPMConnector(CSPMConnector):
    """Demo CSPM connector with synthetic cloud misconfiguration data."""

    async def test_connection(self) -> bool:
        """Demo connection always succeeds."""
        return True

    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        """Generate demo CSPM alerts."""
        demo_alerts = [
            Alert(
                alert_id=f"CSPM-{uuid.uuid4().hex[:8]}",
                source="Demo CSPM Scanner",
                severity="Critical",
                title="Publicly accessible S3 bucket contains PII data",
                description=(
                    "S3 bucket 'customer-data-prod' has public read access enabled. "
                    "Bucket contains unencrypted customer PII including names, emails, "
                    "and phone numbers. This violates data protection policies."
                ),
                detected_at=datetime.now(timezone.utc),
                alert_type="misconfiguration",
                asset_id="s3-customer-data-prod",
                asset_type="s3_bucket",
                metadata={
                    "region": "us-east-1",
                    "public_access": True,
                    "encryption": False,
                    "bucket_policy": "public-read",
                },
            ),
            Alert(
                alert_id=f"CSPM-{uuid.uuid4().hex[:8]}",
                source="Demo CSPM Scanner",
                severity="High",
                title="RDS database not encrypted at rest",
                description=(
                    "Production RDS instance 'user-db-prod' does not have encryption "
                    "at rest enabled. Database contains user authentication data."
                ),
                detected_at=datetime.now(timezone.utc),
                alert_type="misconfiguration",
                asset_id="rds-user-db-prod",
                asset_type="rds_instance",
                metadata={
                    "region": "us-east-1",
                    "engine": "postgresql",
                    "encryption": False,
                    "publicly_accessible": False,
                },
            ),
            Alert(
                alert_id=f"CSPM-{uuid.uuid4().hex[:8]}",
                source="Demo CSPM Scanner",
                severity="Medium",
                title="IAM role has overly permissive S3 access",
                description=(
                    "IAM role 'app-backend-role' has s3:* permissions on all buckets. "
                    "This violates principle of least privilege."
                ),
                detected_at=datetime.now(timezone.utc),
                alert_type="misconfiguration",
                asset_id="iam-app-backend-role",
                asset_type="iam_role",
                metadata={
                    "permissions": ["s3:*"],
                    "resources": ["*"],
                    "attached_to": ["ec2-app-backend"],
                },
            ),
        ]

        # Filter by severity
        severity_order = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
        min_severity_level = severity_order.get(filters.min_severity, 0)

        filtered = [
            alert
            for alert in demo_alerts
            if severity_order.get(alert.severity, 0) >= min_severity_level
            and filters.start_time <= alert.detected_at <= filters.end_time
        ]

        return filtered

    async def fetch_assets(self, asset_ids: list[str]) -> list[Asset]:
        """Fetch demo cloud assets."""
        demo_assets = {
            "s3-customer-data-prod": Asset(
                asset_id="s3-customer-data-prod",
                asset_type="s3_bucket",
                name="customer-data-prod",
                environment="production",
                classification=["PII", "customer_data"],
                owner_team="platform",
                metadata={
                    "region": "us-east-1",
                    "size_gb": 1250,
                    "object_count": 45000,
                },
            ),
            "rds-user-db-prod": Asset(
                asset_id="rds-user-db-prod",
                asset_type="rds_instance",
                name="user-db-prod",
                environment="production",
                classification=["PII", "authentication"],
                owner_team="platform",
                metadata={
                    "region": "us-east-1",
                    "engine": "postgresql",
                    "version": "14.7",
                },
            ),
            "iam-app-backend-role": Asset(
                asset_id="iam-app-backend-role",
                asset_type="iam_role",
                name="app-backend-role",
                environment="production",
                classification=[],
                owner_team="platform-security",
                metadata={"attached_policies": 3, "last_used": "2025-10-29"},
            ),
        }

        return [
            demo_assets[asset_id] for asset_id in asset_ids if asset_id in demo_assets
        ]


class DemoPolicyConnector(PolicyConnector):
    """Demo policy connector with synthetic compliance policies."""

    async def test_connection(self) -> bool:
        """Demo connection always succeeds."""
        return True

    async def fetch_policies(self, keywords: list[str]) -> list[dict]:
        """Fetch demo policy documents."""
        demo_policies = [
            Policy(
                policy_id="POL-001",
                title="PII Data Handling Policy",
                content=(
                    "All personally identifiable information (PII) must be encrypted "
                    "at rest and in transit. PII includes names, email addresses, phone "
                    "numbers, physical addresses, social security numbers, and financial "
                    "information. Storage systems containing PII must have encryption "
                    "enabled. Access to PII must be logged and audited. Public access "
                    "to PII data stores is strictly prohibited."
                ),
                category="data_protection",
                severity_weight=2.0,
                applicable_to=["s3_bucket", "rds_instance", "code_module"],
                metadata={"version": "2.1", "last_updated": "2025-01-15"},
            ),
            Policy(
                policy_id="POL-002",
                title="Encryption at Rest Policy",
                content=(
                    "All production data stores must have encryption at rest enabled. "
                    "This includes but is not limited to: S3 buckets, RDS instances, "
                    "EBS volumes, and DynamoDB tables. Encryption keys must be managed "
                    "through AWS KMS with automatic rotation enabled."
                ),
                category="data_protection",
                severity_weight=1.8,
                applicable_to=["s3_bucket", "rds_instance", "ebs_volume"],
                metadata={"version": "1.5", "last_updated": "2025-02-01"},
            ),
            Policy(
                policy_id="POL-003",
                title="Credential Management Policy",
                content=(
                    "Hardcoded credentials in source code are strictly prohibited. "
                    "All credentials must be stored in secure secret management systems "
                    "(e.g., AWS Secrets Manager, HashiCorp Vault). Database credentials, "
                    "API keys, and service tokens must be rotated every 90 days. "
                    "Credentials must never be committed to version control."
                ),
                category="access_control",
                severity_weight=2.5,
                applicable_to=["code_module"],
                metadata={"version": "3.0", "last_updated": "2025-01-10"},
            ),
            Policy(
                policy_id="POL-004",
                title="Principle of Least Privilege - IAM",
                content=(
                    "IAM roles and policies must follow the principle of least privilege. "
                    "Grant only the minimum permissions required for the specific use case. "
                    "Avoid wildcard permissions (e.g., s3:*, *:*). Regularly audit and "
                    "remove unused permissions. Use resource-based policies to restrict "
                    "access to specific resources."
                ),
                category="access_control",
                severity_weight=1.5,
                applicable_to=["iam_role", "iam_policy"],
                metadata={"version": "2.0", "last_updated": "2025-03-01"},
            ),
        ]

        # Filter by keywords if provided
        if keywords:
            keywords_lower = [kw.lower() for kw in keywords]
            filtered = [
                policy
                for policy in demo_policies
                if any(
                    kw in policy.title.lower() or kw in policy.content.lower()
                    for kw in keywords_lower
                )
            ]
            return [p.model_dump() for p in filtered]

        return [p.model_dump() for p in demo_policies]
