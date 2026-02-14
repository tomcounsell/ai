"""Risk scoring engine for security review."""

import logging
from typing import Literal

from ..server import Risk, RiskAction
from .types import Correlation

logger = logging.getLogger(__name__)


class RiskScorer:
    """Scores and categorizes security risks based on multiple factors."""

    # Severity weights
    SEVERITY_WEIGHTS = {
        "Critical": 4.0,
        "High": 3.0,
        "Medium": 2.0,
        "Low": 1.0,
    }

    # Environment weights
    ENVIRONMENT_WEIGHTS = {
        "production": 3.0,
        "staging": 1.5,
        "development": 0.5,
    }

    # Data classification weights
    CLASSIFICATION_WEIGHTS = {
        "PII": 2.5,
        "credentials": 3.0,
        "financial": 2.8,
        "PCI-DSS": 3.0,
        "regulated": 2.2,
        "customer_data": 2.0,
        "authentication": 2.3,
    }

    def score_risk(self, correlation: Correlation) -> Risk:
        """Score a correlated risk and generate recommendations.

        Args:
            correlation: Correlated risk data

        Returns:
            Risk object with score and severity
        """
        # Calculate base score from alerts
        alert_score = self._calculate_alert_score(correlation)

        # Calculate exposure score from assets
        exposure_score = self._calculate_exposure_score(correlation)

        # Calculate policy violation weight
        policy_score = self._calculate_policy_score(correlation)

        # Calculate business impact multiplier
        business_multiplier = self._calculate_business_impact(correlation)

        # Combine scores: base formula is weighted average with business multiplier
        # risk_score = (alert_score * 0.4 + exposure_score * 0.3 + policy_score * 0.3) * business_multiplier
        raw_score = (
            alert_score * 0.4 + exposure_score * 0.3 + policy_score * 0.3
        ) * business_multiplier

        # Normalize to 0-100 scale
        risk_score = min(100, max(0, raw_score))

        # Determine severity label
        severity = self._score_to_severity(risk_score)

        # Generate risk ID
        risk_id = self._generate_risk_id(correlation)

        # Create title and description
        title = self._generate_title(correlation)
        description = self._generate_description(correlation)

        # Generate recommended actions
        actions = self._generate_actions(correlation, severity)

        # Extract linked data
        linked_alerts = [alert.alert_id for alert in correlation.alerts]
        policy_violations = [policy.title for policy in correlation.policies]
        affected_assets = [asset.name for asset in correlation.assets]

        return Risk(
            risk_id=risk_id,
            severity=severity,
            title=title,
            description=description,
            linked_alerts=linked_alerts,
            policy_violations=policy_violations,
            affected_assets=affected_assets,
            actions=actions,
            score=risk_score,
        )

    def _calculate_alert_score(self, correlation: Correlation) -> float:
        """Calculate score from alert severities."""
        if not correlation.alerts:
            return 0

        # Use highest severity and CVSS if available
        max_weight = 0
        max_cvss = 0

        for alert in correlation.alerts:
            weight = self.SEVERITY_WEIGHTS.get(alert.severity, 1.0)
            max_weight = max(max_weight, weight)

            if alert.cvss_score:
                max_cvss = max(max_cvss, alert.cvss_score)

        # Combine severity weight and CVSS (CVSS is 0-10 scale)
        alert_score = (max_weight * 10) + (max_cvss * 2)
        return min(50, alert_score)  # Cap at 50

    def _calculate_exposure_score(self, correlation: Correlation) -> float:
        """Calculate score from asset exposure."""
        if not correlation.assets:
            return 0

        max_exposure = 0

        for asset in correlation.assets:
            # Environment weight
            env_weight = self.ENVIRONMENT_WEIGHTS.get(asset.environment or "", 1.0)

            # Classification weight (max of all classifications)
            class_weight = 1.0
            for classification in asset.classification:
                class_weight = max(
                    class_weight,
                    self.CLASSIFICATION_WEIGHTS.get(classification, 1.0),
                )

            # Check for public access in metadata
            is_public = asset.metadata.get("public_access", False)
            public_multiplier = 2.0 if is_public else 1.0

            exposure = env_weight * class_weight * public_multiplier
            max_exposure = max(max_exposure, exposure)

        return min(50, max_exposure * 2.5)  # Scale and cap at 50

    def _calculate_policy_score(self, correlation: Correlation) -> float:
        """Calculate score from policy violations."""
        if not correlation.policies:
            return 0

        # Sum policy severity weights
        total_weight = sum(policy.severity_weight for policy in correlation.policies)

        # Multiple policy violations increase severity
        violation_count_multiplier = min(2.0, 1 + (len(correlation.policies) - 1) * 0.3)

        policy_score = total_weight * 8 * violation_count_multiplier
        return min(50, policy_score)  # Cap at 50

    def _calculate_business_impact(self, correlation: Correlation) -> float:
        """Calculate business impact multiplier."""
        # Start at 1.0 baseline
        multiplier = 1.0

        # Check for high-impact data classifications
        high_impact_classes = {"PII", "credentials", "financial", "PCI-DSS"}
        for asset in correlation.assets:
            if any(c in high_impact_classes for c in asset.classification):
                multiplier = max(multiplier, 1.3)

        # Production environment increases impact
        for asset in correlation.assets:
            if asset.environment == "production":
                multiplier = max(multiplier, 1.25)

        # Public exposure dramatically increases impact
        for asset in correlation.assets:
            if asset.metadata.get("public_access"):
                multiplier = max(multiplier, 1.5)

        # Correlation confidence affects multiplier
        multiplier *= correlation.confidence

        return multiplier

    def _score_to_severity(
        self, score: float
    ) -> Literal["Low", "Medium", "High", "Critical"]:
        """Convert numeric score to severity label."""
        if score >= 80:
            return "Critical"
        elif score >= 50:
            return "High"
        elif score >= 20:
            return "Medium"
        else:
            return "Low"

    def _generate_risk_id(self, correlation: Correlation) -> str:
        """Generate a unique risk ID."""
        # Use first alert ID as basis
        if correlation.alerts:
            base_id = correlation.alerts[0].alert_id.split("-")[0]
            return f"RISK-{base_id}-{len(correlation.alerts):02d}"
        return f"RISK-{hash(correlation.reasoning) % 10000:04d}"

    def _generate_title(self, correlation: Correlation) -> str:
        """Generate a concise risk title."""
        # Try to extract key elements
        asset_names = [asset.name for asset in correlation.assets[:2]]
        alert_keywords = set()

        for alert in correlation.alerts:
            # Extract key words from title
            words = alert.title.lower().split()
            for word in words:
                if word in {
                    "sql",
                    "injection",
                    "xss",
                    "csrf",
                    "pii",
                    "credentials",
                    "encryption",
                    "public",
                    "exposed",
                    "vulnerable",
                }:
                    alert_keywords.add(word)

        if alert_keywords and asset_names:
            keywords_str = ", ".join(sorted(alert_keywords))
            assets_str = ", ".join(asset_names)
            return f"{keywords_str.title()} affecting {assets_str}"
        elif correlation.alerts:
            return correlation.alerts[0].title
        else:
            return "Security risk requiring review"

    def _generate_description(self, correlation: Correlation) -> str:
        """Generate detailed risk description."""
        # Start with LLM reasoning
        description = correlation.reasoning

        # Add asset context
        if correlation.assets:
            asset_list = ", ".join(
                f"{a.name} ({a.asset_type})" for a in correlation.assets
            )
            description += f"\n\nAffected assets: {asset_list}"

        # Add policy context
        if correlation.policies:
            policy_list = ", ".join(p.title for p in correlation.policies)
            description += f"\n\nPolicy violations: {policy_list}"

        # Add business impact
        if correlation.business_impact:
            description += f"\n\nBusiness impact: {correlation.business_impact}"

        return description

    def _generate_actions(
        self, correlation: Correlation, severity: str
    ) -> list[RiskAction]:
        """Generate recommended remediation actions."""
        actions = []

        # Analyze what needs to be fixed
        needs_encryption = False
        needs_access_control = False
        needs_code_fix = False
        needs_credential_rotation = False

        for alert in correlation.alerts:
            title_lower = alert.title.lower()
            desc_lower = alert.description.lower()

            if "encryption" in title_lower or "unencrypted" in desc_lower:
                needs_encryption = True
            if "public" in title_lower or "exposed" in desc_lower:
                needs_access_control = True
            if (
                "injection" in title_lower
                or "xss" in title_lower
                or "vulnerable" in title_lower
            ):
                needs_code_fix = True
            if "credential" in title_lower or "password" in desc_lower:
                needs_credential_rotation = True

        # Generate specific actions
        if needs_encryption:
            actions.append(
                RiskAction(
                    action="Enable encryption at rest for affected data stores",
                    assignee=self._get_assignee(correlation, "platform-security"),
                    priority=severity,
                )
            )

        if needs_access_control:
            actions.append(
                RiskAction(
                    action="Remove public access and implement proper access controls",
                    assignee=self._get_assignee(correlation, "platform-security"),
                    priority=severity,
                )
            )

        if needs_code_fix:
            actions.append(
                RiskAction(
                    action="Fix code vulnerability with input validation and sanitization",
                    assignee=self._get_assignee(correlation, "engineering"),
                    priority=severity,
                )
            )

        if needs_credential_rotation:
            actions.append(
                RiskAction(
                    action="Rotate exposed credentials and move to secret management system",
                    assignee=self._get_assignee(correlation, "platform-security"),
                    priority="Critical",  # Always critical
                )
            )

        # Add monitoring/verification
        if severity in ["Critical", "High"]:
            actions.append(
                RiskAction(
                    action="Verify fix and update security monitoring alerts",
                    assignee="platform-security",
                    priority="Medium",
                )
            )

        # Add DPO notification for PII issues
        has_pii = any("PII" in asset.classification for asset in correlation.assets)
        if has_pii and severity in ["Critical", "High"]:
            actions.append(
                RiskAction(
                    action="Notify Data Protection Officer (DPO) of PII exposure",
                    assignee="compliance",
                    priority="High",
                )
            )

        return actions

    def _get_assignee(self, correlation: Correlation, default: str) -> str:
        """Determine best assignee based on asset ownership."""
        # Check if assets have owner teams
        for asset in correlation.assets:
            if asset.owner_team:
                return asset.owner_team

        return default
