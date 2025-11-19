"""Pytest fixtures for AI app tests."""

import pytest


@pytest.fixture(autouse=True)
def setup_test_connectors():
    """Register demo connectors for testing.

    Demo connectors are not auto-loaded in production, but we need them
    for tests to have data to work with.
    """
    from apps.ai.mcp.security.connector_registry import ConnectorRegistry
    from apps.ai.mcp.security.examples.demo_connectors import (
        DemoCSPMConnector,
        DemoPolicyConnector,
        DemoSASTConnector,
    )

    # Reset initialization flag and clear connectors
    ConnectorRegistry._initialized = False
    ConnectorRegistry._connectors = {}

    # Register demo connectors for testing
    registry = ConnectorRegistry()
    registry._connectors["demo_sast"] = DemoSASTConnector(
        api_key="demo",
        api_url="https://demo.sast.example.com",
    )
    registry._connectors["demo_cspm"] = DemoCSPMConnector(
        api_key="demo",
        api_url="https://demo.cspm.example.com",
    )
    registry._connectors["demo_policy"] = DemoPolicyConnector(
        api_key="demo",
    )

    yield

    # Cleanup after tests
    ConnectorRegistry._initialized = False
    ConnectorRegistry._connectors = {}
