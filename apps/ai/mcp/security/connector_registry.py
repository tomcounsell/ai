"""Registry for managing security tool connectors."""

import importlib
import inspect
import logging
import os
from pathlib import Path
from typing import Literal

from .base_connector import (
    BaseSecurityConnector,
    CSPMConnector,
    DASTConnector,
    PolicyConnector,
    SASTConnector,
    ThreatIntelConnector,
)

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Registry for managing and accessing security tool connectors."""

    # Class-level registry shared across instances
    _connectors: dict[str, BaseSecurityConnector] = {}
    _initialized: bool = False

    def __init__(self):
        """Initialize the connector registry.

        Auto-discovers and registers:
        1. Built-in connectors (if env vars are set)
        2. Plugin connectors from apps/ai/mcp/security/connectors/
        """
        if not self._initialized:
            self._initialize_connectors()
            self._initialized = True

    def _initialize_connectors(self):
        """Initialize connectors from built-in and plugin sources."""
        # Load built-in connectors (activated by env vars)
        self._load_builtin_connectors()

        # Auto-discover plugin connectors
        self._load_plugin_connectors()

        logger.info(f"Initialized {len(self._connectors)} connectors")

    def _load_builtin_connectors(self):
        """Load built-in connectors if their environment variables are set."""
        try:
            from .builtin_connectors import discover_builtin_connectors

            builtin = discover_builtin_connectors()
            self._connectors.update(builtin)
            if builtin:
                logger.info(
                    f"Loaded {len(builtin)} built-in connectors: {', '.join(builtin.keys())}"
                )
        except ImportError:
            logger.debug("No built-in connectors available")
        except Exception as e:
            logger.error(f"Failed to load built-in connectors: {e}")

    def _load_plugin_connectors(self):
        """Auto-discover and load connector plugins from connectors/ directory."""
        try:
            connectors_dir = Path(__file__).parent / "connectors"
            if not connectors_dir.exists():
                logger.debug("No connectors plugin directory found")
                return

            # Discover Python files in connectors/
            for plugin_file in connectors_dir.glob("*.py"):
                if plugin_file.name.startswith("_"):
                    continue

                try:
                    # Import the plugin module
                    module_name = f"apps.ai.mcp.security.connectors.{plugin_file.stem}"
                    module = importlib.import_module(module_name)

                    # Find connector classes in the module
                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if (
                            issubclass(obj, BaseSecurityConnector)
                            and obj != BaseSecurityConnector
                            and not inspect.isabstract(obj)
                        ):
                            # Instantiate and register
                            try:
                                connector_instance = obj()
                                connector_name = (
                                    getattr(connector_instance, "name", None)
                                    or plugin_file.stem
                                )
                                self._connectors[connector_name] = connector_instance
                                logger.info(f"Loaded plugin connector: {connector_name}")
                            except Exception as e:
                                logger.warning(
                                    f"Failed to instantiate {name} from {plugin_file.name}: {e}"
                                )

                except Exception as e:
                    logger.error(f"Failed to load plugin {plugin_file.name}: {e}")

        except Exception as e:
            logger.error(f"Failed to discover plugin connectors: {e}")

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
