"""Correlation engine for linking alerts, assets, and policies."""

import logging
from datetime import datetime

from ..cto_tools_server import Risk
from .connector_registry import ConnectorRegistry
from .types import Alert, Asset, Correlation, Policy, QueryFilters

logger = logging.getLogger(__name__)


class SecurityCorrelationEngine:
    """Engine for correlating security alerts with assets and policies."""

    def __init__(self):
        """Initialize the correlation engine."""
        self.registry = ConnectorRegistry()

    def parse_query(
        self,
        query: str,
        start_time: datetime,
        end_time: datetime,
        min_severity: str,
        data_types: list[str] | None,
    ) -> QueryFilters:
        """Parse natural language query into structured filters.

        Args:
            query: Natural language query string
            start_time: Start of time window
            end_time: End of time window
            min_severity: Minimum severity level
            data_types: Optional data type filters

        Returns:
            Structured query filters
        """
        query_lower = query.lower()

        # Extract keywords from query
        keywords = []

        # Common security terms
        security_terms = [
            "sql",
            "injection",
            "xss",
            "csrf",
            "pii",
            "credentials",
            "encryption",
            "exposed",
            "public",
            "vulnerable",
            "misconfiguration",
            "bucket",
            "database",
            "s3",
            "rds",
            "iam",
            "access",
        ]

        for term in security_terms:
            if term in query_lower:
                keywords.append(term)

        # Extract asset types
        asset_types = []
        if "bucket" in query_lower or "s3" in query_lower:
            asset_types.append("s3_bucket")
        if "database" in query_lower or "rds" in query_lower:
            asset_types.append("rds_instance")
        if "code" in query_lower or "module" in query_lower:
            asset_types.append("code_module")
        if "iam" in query_lower or "role" in query_lower:
            asset_types.append("iam_role")

        # Extract environments
        environments = []
        if "production" in query_lower or "prod" in query_lower:
            environments.append("production")
        if "staging" in query_lower or "stage" in query_lower:
            environments.append("staging")
        if "development" in query_lower or "dev" in query_lower:
            environments.append("development")

        # Combine with provided data_types
        if data_types:
            keywords.extend(data_types)

        return QueryFilters(
            start_time=start_time,
            end_time=end_time,
            min_severity=min_severity,
            data_types=data_types or [],
            asset_types=asset_types,
            environments=environments,
            keywords=keywords,
        )

    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        """Fetch alerts from all connectors.

        Args:
            filters: Query filters

        Returns:
            Combined list of alerts from all sources
        """
        all_alerts = []

        # Get all SAST, DAST, CSPM, and threat intel connectors
        connector_types = ["sast", "dast", "cspm", "threat_intel"]

        for connector_type in connector_types:
            connectors = self.registry.get_connectors_by_type(connector_type)
            for connector in connectors:
                try:
                    alerts = await connector.fetch_alerts(filters)
                    all_alerts.extend(alerts)
                    logger.info(
                        f"Fetched {len(alerts)} alerts from {connector_type} connector"
                    )
                except Exception as e:
                    logger.error(f"Failed to fetch alerts from {connector_type}: {e}")

        # Filter by keywords if specified
        if filters.keywords:
            filtered_alerts = []
            for alert in all_alerts:
                alert_text = f"{alert.title} {alert.description}".lower()
                if any(keyword.lower() in alert_text for keyword in filters.keywords):
                    filtered_alerts.append(alert)
            all_alerts = filtered_alerts

        logger.info(f"Total alerts fetched: {len(all_alerts)}")
        return all_alerts

    async def fetch_policies(self, filters: QueryFilters) -> list[Policy]:
        """Fetch relevant policy documents.

        Args:
            filters: Query filters including keywords

        Returns:
            List of relevant policies
        """
        all_policies = []

        # Get policy connectors
        policy_connectors = self.registry.get_connectors_by_type("policy")

        for connector in policy_connectors:
            try:
                # Fetch policies matching keywords
                policy_dicts = await connector.fetch_policies(filters.keywords)

                # Convert to Policy objects
                for policy_dict in policy_dicts:
                    all_policies.append(Policy(**policy_dict))

                logger.info(
                    f"Fetched {len(policy_dicts)} policies from policy connector"
                )
            except Exception as e:
                logger.error(f"Failed to fetch policies: {e}")

        return all_policies

    async def correlate(
        self, alerts: list[Alert], policies: list[Policy], query: str
    ) -> list[Correlation]:
        """Correlate alerts with assets and policies using LLM reasoning.

        Args:
            alerts: List of security alerts
            policies: List of relevant policies
            query: Original user query for context

        Returns:
            List of correlated risks
        """
        if not alerts:
            return []

        # Group alerts by asset
        alerts_by_asset: dict[str, list[Alert]] = {}
        for alert in alerts:
            if alert.asset_id:
                if alert.asset_id not in alerts_by_asset:
                    alerts_by_asset[alert.asset_id] = []
                alerts_by_asset[alert.asset_id].append(alert)

        correlations = []

        # For each asset with alerts, create a correlation
        for asset_id, asset_alerts in alerts_by_asset.items():
            # Fetch asset details from appropriate connector
            asset = await self._fetch_asset_details(asset_id, asset_alerts[0].source)

            if not asset:
                continue

            # Find applicable policies based on asset type and classification
            applicable_policies = self._find_applicable_policies(asset, policies)

            # Generate reasoning using simple heuristics (in production, use LLM)
            reasoning = self._generate_reasoning(
                asset_alerts, asset, applicable_policies
            )

            # Calculate confidence based on data completeness
            confidence = self._calculate_correlation_confidence(
                asset_alerts, asset, applicable_policies
            )

            # Determine business impact
            business_impact = self._assess_business_impact(asset, asset_alerts)

            correlation = Correlation(
                alerts=asset_alerts,
                assets=[asset] if asset else [],
                policies=applicable_policies,
                reasoning=reasoning,
                confidence=confidence,
                business_impact=business_impact,
            )

            correlations.append(correlation)

        # Sort by number of alerts and severity
        correlations.sort(
            key=lambda c: (
                len(c.alerts),
                sum(self._severity_to_int(a.severity) for a in c.alerts),
            ),
            reverse=True,
        )

        return correlations

    async def _fetch_asset_details(self, asset_id: str, source: str) -> Asset | None:
        """Fetch detailed asset information.

        Args:
            asset_id: Asset ID to fetch
            source: Source tool name to determine which connector to use

        Returns:
            Asset details or None if not found
        """
        # Determine connector type from source
        connector_type = self._source_to_connector_type(source)
        connectors = self.registry.get_connectors_by_type(connector_type)

        for connector in connectors:
            try:
                assets = await connector.fetch_assets([asset_id])
                if assets:
                    return assets[0]
            except Exception as e:
                logger.error(f"Failed to fetch asset {asset_id}: {e}")

        return None

    def _source_to_connector_type(self, source: str) -> str:
        """Map source tool name to connector type."""
        source_lower = source.lower()
        if "sast" in source_lower:
            return "sast"
        elif "dast" in source_lower:
            return "dast"
        elif "cspm" in source_lower or "cloud" in source_lower:
            return "cspm"
        elif "threat" in source_lower:
            return "threat_intel"
        else:
            return "sast"  # Default

    def _find_applicable_policies(
        self, asset: Asset, policies: list[Policy]
    ) -> list[Policy]:
        """Find policies applicable to an asset.

        Args:
            asset: Asset to check
            policies: All available policies

        Returns:
            List of applicable policies
        """
        applicable = []

        for policy in policies:
            # Check if policy applies to this asset type
            if asset.asset_type in policy.applicable_to:
                applicable.append(policy)
                continue

            # Check if policy applies to any of the asset's classifications
            if any(
                classification in policy.applicable_to
                for classification in asset.classification
            ):
                applicable.append(policy)

        return applicable

    def _generate_reasoning(
        self, alerts: list[Alert], asset: Asset, policies: list[Policy]
    ) -> str:
        """Generate correlation reasoning.

        In production, this would use an LLM. For now, use template-based reasoning.

        Args:
            alerts: Related alerts
            asset: Affected asset
            policies: Violated policies

        Returns:
            Reasoning explanation
        """
        reasoning_parts = []

        # Describe the alerts
        if len(alerts) == 1:
            reasoning_parts.append(
                f"A {alerts[0].severity.lower()} severity {alerts[0].alert_type} "
                f"was detected: {alerts[0].title}."
            )
        else:
            severities = [a.severity for a in alerts]
            reasoning_parts.append(
                f"Multiple security issues were detected affecting this asset "
                f"({len(alerts)} alerts ranging from {min(severities)} to {max(severities)} severity)."
            )

        # Describe the asset
        env_str = f" in {asset.environment}" if asset.environment else ""
        class_str = (
            f" containing {', '.join(asset.classification)}"
            if asset.classification
            else ""
        )
        reasoning_parts.append(
            f"The affected asset is {asset.name} ({asset.asset_type}){env_str}{class_str}."
        )

        # Describe policy violations
        if policies:
            policy_titles = [p.title for p in policies]
            reasoning_parts.append(
                f"This violates the following policies: {', '.join(policy_titles)}."
            )

        return " ".join(reasoning_parts)

    def _calculate_correlation_confidence(
        self, alerts: list[Alert], asset: Asset, policies: list[Policy]
    ) -> float:
        """Calculate confidence in the correlation.

        Args:
            alerts: Related alerts
            asset: Affected asset
            policies: Applicable policies

        Returns:
            Confidence score (0-1)
        """
        confidence = 0.5  # Base confidence

        # More alerts increases confidence
        if len(alerts) > 1:
            confidence += 0.1

        # Asset classification data increases confidence
        if asset.classification:
            confidence += 0.1

        # Environment data increases confidence
        if asset.environment:
            confidence += 0.1

        # Policy matches increase confidence
        if policies:
            confidence += 0.1 * min(len(policies), 2)

        return min(1.0, confidence)

    def _assess_business_impact(self, asset: Asset, alerts: list[Alert]) -> str | None:
        """Assess business impact of the risk.

        Args:
            asset: Affected asset
            alerts: Related alerts

        Returns:
            Business impact description
        """
        impact_factors = []

        # Check for sensitive data
        sensitive_classes = {"PII", "credentials", "financial", "PCI-DSS"}
        if any(c in sensitive_classes for c in asset.classification):
            impact_factors.append("sensitive data exposure")

        # Check environment
        if asset.environment == "production":
            impact_factors.append("production system affected")

        # Check for public exposure
        if asset.metadata.get("public_access"):
            impact_factors.append("publicly accessible")

        # Check alert severity
        if any(a.severity == "Critical" for a in alerts):
            impact_factors.append("critical security vulnerabilities")

        if impact_factors:
            return "High business impact due to: " + ", ".join(impact_factors)

        return None

    def _severity_to_int(self, severity: str) -> int:
        """Convert severity to integer for sorting."""
        return {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}.get(severity, 0)

    def generate_summary(self, risks: list[Risk], query: str) -> str:
        """Generate natural language summary of risks.

        Args:
            risks: List of identified risks
            query: Original query

        Returns:
            Summary text
        """
        if not risks:
            return f"Security review completed for query: '{query}'. No significant risks found matching the specified criteria."

        # Count by severity
        critical_count = sum(1 for r in risks if r.severity == "Critical")
        high_count = sum(1 for r in risks if r.severity == "High")
        medium_count = sum(1 for r in risks if r.severity == "Medium")
        low_count = sum(1 for r in risks if r.severity == "Low")

        summary = f"Security review completed for query: '{query}'.\n\n"
        summary += f"**Risk Summary**: {len(risks)} risks identified:\n"

        if critical_count:
            summary += f"- {critical_count} Critical\n"
        if high_count:
            summary += f"- {high_count} High\n"
        if medium_count:
            summary += f"- {medium_count} Medium\n"
        if low_count:
            summary += f"- {low_count} Low\n"

        # Highlight top risk
        top_risk = risks[0]
        summary += f"\n**Top Risk**: {top_risk.title} ({top_risk.severity}, Score: {top_risk.score:.1f}/100)\n"
        summary += f"{top_risk.description[:200]}..."

        return summary

    def calculate_confidence(self, correlations: list[Correlation]) -> float:
        """Calculate overall correlation confidence.

        Args:
            correlations: List of correlations

        Returns:
            Average confidence score
        """
        if not correlations:
            return 0.0

        return sum(c.confidence for c in correlations) / len(correlations)
