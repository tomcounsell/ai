"""Type definitions for security review system."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Alert(BaseModel):
    """Security alert from external tool."""

    alert_id: str
    source: str  # Tool that generated the alert
    severity: Literal["Low", "Medium", "High", "Critical"]
    title: str
    description: str
    detected_at: datetime
    alert_type: str  # e.g., "vulnerability", "misconfiguration", "threat"
    asset_id: str | None = None
    asset_type: str | None = None  # e.g., "code_module", "s3_bucket", "ec2_instance"
    cvss_score: float | None = None
    cwe_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class Asset(BaseModel):
    """Cloud or code asset referenced in alerts."""

    asset_id: str
    asset_type: str
    name: str
    environment: Literal["production", "staging", "development"] | None = None
    classification: list[str] = Field(
        default_factory=list
    )  # e.g., ["PII", "regulated"]
    owner_team: str | None = None
    metadata: dict = Field(default_factory=dict)


class Policy(BaseModel):
    """Compliance or security policy."""

    policy_id: str
    title: str
    content: str
    category: str  # e.g., "data_protection", "access_control"
    severity_weight: float = 1.0  # How severe violations are
    applicable_to: list[str] = Field(
        default_factory=list
    )  # Asset types or classifications
    metadata: dict = Field(default_factory=dict)


class Correlation(BaseModel):
    """Correlated risk combining alerts, assets, and policy violations."""

    alerts: list[Alert]
    assets: list[Asset]
    policies: list[Policy]
    reasoning: str  # LLM's explanation of the correlation
    confidence: float = Field(ge=0, le=1)  # How confident the correlation is
    business_impact: str | None = None


class ConnectorConfig(BaseModel):
    """Configuration for a security tool connector."""

    connector_type: Literal["sast", "dast", "cspm", "threat_intel", "policy"]
    name: str
    api_key: str
    api_url: str | None = None
    enabled: bool = True
    metadata: dict = Field(default_factory=dict)


class QueryFilters(BaseModel):
    """Filters for security review queries."""

    start_time: datetime
    end_time: datetime
    min_severity: Literal["Low", "Medium", "High", "Critical"]
    data_types: list[str] = Field(default_factory=list)
    asset_types: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
