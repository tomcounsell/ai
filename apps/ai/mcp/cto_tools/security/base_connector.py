"""Base connector interface for security tools."""

from abc import ABC, abstractmethod

from .types import Alert, Asset, QueryFilters


class BaseSecurityConnector(ABC):
    """Abstract base class for security tool connectors."""

    def __init__(self, api_key: str, api_url: str | None = None, **kwargs):
        """Initialize the connector.

        Args:
            api_key: API authentication key
            api_url: Optional custom API URL
            **kwargs: Additional connector-specific configuration
        """
        self.api_key = api_key
        self.api_url = api_url
        self.config = kwargs

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test if the connector can successfully connect to the API.

        Returns:
            True if connection successful, False otherwise
        """

    @abstractmethod
    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        """Fetch alerts matching the given filters.

        Args:
            filters: Query filters for time range, severity, etc.

        Returns:
            List of alerts from this tool
        """

    @abstractmethod
    async def fetch_assets(self, asset_ids: list[str]) -> list[Asset]:
        """Fetch detailed asset information.

        Args:
            asset_ids: List of asset IDs to fetch

        Returns:
            List of asset details
        """

    async def get_capabilities(self) -> list[str]:
        """Get list of capabilities supported by this connector.

        Returns:
            List of capability names (e.g., ["alerts", "assets", "vulnerabilities"])
        """
        return ["alerts", "assets"]

    async def get_status(self) -> dict:
        """Get connector status and metadata.

        Returns:
            Dictionary with status information
        """
        is_connected = await self.test_connection()
        return {
            "connected": is_connected,
            "capabilities": await self.get_capabilities(),
            "api_url": self.api_url,
        }


class SASTConnector(BaseSecurityConnector):
    """Connector for Static Application Security Testing tools."""

    async def get_capabilities(self) -> list[str]:
        """SAST tools provide code vulnerabilities and code assets."""
        return ["alerts", "assets", "vulnerabilities", "code_analysis"]


class DASTConnector(BaseSecurityConnector):
    """Connector for Dynamic Application Security Testing tools."""

    async def get_capabilities(self) -> list[str]:
        """DAST tools provide runtime vulnerabilities and endpoints."""
        return ["alerts", "vulnerabilities", "endpoints", "runtime_analysis"]


class CSPMConnector(BaseSecurityConnector):
    """Connector for Cloud Security Posture Management tools."""

    async def get_capabilities(self) -> list[str]:
        """CSPM tools provide cloud misconfigurations and cloud assets."""
        return ["alerts", "assets", "misconfigurations", "cloud_resources"]


class ThreatIntelConnector(BaseSecurityConnector):
    """Connector for Threat Intelligence feeds."""

    async def get_capabilities(self) -> list[str]:
        """Threat intel provides indicators of compromise and threat data."""
        return ["alerts", "indicators", "threat_actors", "malware"]


class PolicyConnector(BaseSecurityConnector):
    """Connector for policy and compliance document sources."""

    async def fetch_alerts(self, filters: QueryFilters) -> list[Alert]:
        """Policy connectors don't generate alerts."""
        return []

    async def fetch_assets(self, asset_ids: list[str]) -> list[Asset]:
        """Policy connectors don't manage assets."""
        return []

    @abstractmethod
    async def fetch_policies(self, keywords: list[str]) -> list[dict]:
        """Fetch policy documents matching keywords.

        Args:
            keywords: Keywords to search for in policies

        Returns:
            List of policy documents
        """

    async def get_capabilities(self) -> list[str]:
        """Policy connectors provide compliance and policy documents."""
        return ["policies", "compliance", "standards"]
