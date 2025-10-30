"""Registry for managing security tool connectors."""

import logging
from typing import Literal

from .base_connector import (
    BaseSecurityConnector,
    CSPMConnector,
    DASTConnector,
    PolicyConnector,
    SASTConnector,
    ThreatIntelConnector,
)
from .demo_connectors import (
    DemoCSPMConnector,
    DemoPolicyConnector,
    DemoSASTConnector,
)

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Registry for managing and accessing security tool connectors."""

    # Class-level registry shared across instances
    _connectors: dict[str, BaseSecurityConnector] = {}

    def __init__(self):
        """Initialize the connector registry.

        Loads demo connectors by default for testing.
        """
        # Initialize with demo connectors if registry is empty
        if not self._connectors:
            self._initialize_demo_connectors()

    def _initialize_demo_connectors(self):
        """Initialize demo connectors for testing and demonstration."""
        try:
            # Demo SAST connector
            self._connectors["demo_sast"] = DemoSASTConnector(
                api_key="demo",
                api_url="https://demo.sast.example.com",
            )

            # Demo CSPM connector
            self._connectors["demo_cspm"] = DemoCSPMConnector(
                api_key="demo",
                api_url="https://demo.cspm.example.com",
            )

            # Demo policy connector (Memory MCP)
            self._connectors["demo_policy"] = DemoPolicyConnector(
                api_key="demo",
            )

            logger.info("Initialized demo connectors for testing")
        except Exception as e:
            logger.error(f"Failed to initialize demo connectors: {e}")

    async def register_connector(
        self,
        connector_type: Literal["sast", "dast", "cspm", "threat_intel", "policy"],
        name: str,
        config: dict,
    ) -> bool:
        """Register a new security tool connector.

        Args:
            connector_type: Type of security tool
            name: Unique name for this connector
            config: Configuration including api_key, api_url, etc.

        Returns:
            True if registration successful, False otherwise
        """
        try:
            # Map connector types to classes
            connector_classes = {
                "sast": SASTConnector,
                "dast": DASTConnector,
                "cspm": CSPMConnector,
                "threat_intel": ThreatIntelConnector,
                "policy": PolicyConnector,
            }

            connector_class = connector_classes.get(connector_type)
            if not connector_class:
                logger.error(f"Unknown connector type: {connector_type}")
                return False

            # Create connector instance
            connector = connector_class(**config)

            # Test connection
            is_connected = await connector.test_connection()
            if not is_connected:
                logger.warning(f"Connector {name} failed connection test")
                # Still register it, but mark as disconnected
                # This allows configuration even if temporarily unreachable

            # Store in registry
            self._connectors[name] = connector
            logger.info(f"Registered connector: {name} ({connector_type})")
            return True

        except Exception as e:
            logger.error(f"Failed to register connector {name}: {e}")
            return False

    def get_connector(self, name: str) -> BaseSecurityConnector | None:
        """Get a connector by name.

        Args:
            name: Connector name

        Returns:
            Connector instance or None if not found
        """
        return self._connectors.get(name)

    def get_connectors_by_type(
        self, connector_type: Literal["sast", "dast", "cspm", "threat_intel", "policy"]
    ) -> list[BaseSecurityConnector]:
        """Get all connectors of a specific type.

        Args:
            connector_type: Type of connectors to retrieve

        Returns:
            List of connectors matching the type
        """
        type_classes = {
            "sast": SASTConnector,
            "dast": DASTConnector,
            "cspm": CSPMConnector,
            "threat_intel": ThreatIntelConnector,
            "policy": PolicyConnector,
        }

        target_class = type_classes.get(connector_type)
        if not target_class:
            return []

        return [
            connector
            for connector in self._connectors.values()
            if isinstance(connector, target_class)
        ]

    def list_all(self) -> list[dict]:
        """List all registered connectors with their status.

        Returns:
            List of connector information dictionaries
        """
        result = []

        for name, connector in self._connectors.items():
            # Determine connector type
            connector_type = "unknown"
            if isinstance(connector, SASTConnector):
                connector_type = "sast"
            elif isinstance(connector, DASTConnector):
                connector_type = "dast"
            elif isinstance(connector, CSPMConnector):
                connector_type = "cspm"
            elif isinstance(connector, ThreatIntelConnector):
                connector_type = "threat_intel"
            elif isinstance(connector, PolicyConnector):
                connector_type = "policy"

            result.append(
                {
                    "name": name,
                    "type": connector_type,
                    "status": "connected",  # Simplified for now
                    "capabilities": [],  # Will be populated async
                }
            )

        return result

    def remove_connector(self, name: str) -> bool:
        """Remove a connector from the registry.

        Args:
            name: Connector name to remove

        Returns:
            True if removed, False if not found
        """
        if name in self._connectors:
            del self._connectors[name]
            logger.info(f"Removed connector: {name}")
            return True
        return False

    def clear_all(self):
        """Clear all connectors from the registry.

        Useful for testing or reset scenarios.
        """
        self._connectors.clear()
        logger.info("Cleared all connectors from registry")
