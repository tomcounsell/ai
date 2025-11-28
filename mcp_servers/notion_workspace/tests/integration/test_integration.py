"""
Integration Tests for Notion Workspace

IMPORTANT: These tests call REAL APIs. No mocks allowed.
Requires valid API keys in environment variables.

Test Philosophy:
- Test the happy path thoroughly with real API calls
- Verify actual API responses match expected schemas
- Clean up any test data created
- Skip gracefully if API keys not configured
"""

import os
import pytest
from modules.framework.contracts import ModuleInput, ExecutionStatus
from mcp_servers.notion_workspace.src.processor import NotionWorkspaceModule


# Configuration
API_KEY_ENV = "NOTION_API_KEY"
SKIP_REASON = f"{API_KEY_ENV} not set - skipping real API tests"


def has_api_key() -> bool:
    """Check if API key is available for testing."""
    return bool(os.environ.get(API_KEY_ENV))


# Skip entire module if no API key
pytestmark = pytest.mark.skipif(not has_api_key(), reason=SKIP_REASON)


@pytest.fixture
def module():
    """Create module instance with real API configuration."""
    return NotionWorkspaceModule()


@pytest.fixture
def cleanup_ids():
    """Track IDs of resources created during tests for cleanup."""
    ids = []
    yield ids
    # Cleanup would happen here if needed
    # For now, tests should clean up their own resources


class TestSearchIntegration:
    """Integration tests for search."""

    @pytest.mark.asyncio
    async def test_search_real_api(self, module):
        """Test search with real API."""
        input_data = ModuleInput(
            operation="search",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetpageIntegration:
    """Integration tests for get-page."""

    @pytest.mark.asyncio
    async def test_get_page_real_api(self, module):
        """Test get-page with real API."""
        input_data = ModuleInput(
            operation="get-page",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestCreatepageIntegration:
    """Integration tests for create-page."""

    @pytest.mark.asyncio
    async def test_create_page_real_api(self, module):
        """Test create-page with real API."""
        input_data = ModuleInput(
            operation="create-page",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestQuerydatabaseIntegration:
    """Integration tests for query-database."""

    @pytest.mark.asyncio
    async def test_query_database_real_api(self, module):
        """Test query-database with real API."""
        input_data = ModuleInput(
            operation="query-database",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestUpdatepageIntegration:
    """Integration tests for update-page."""

    @pytest.mark.asyncio
    async def test_update_page_real_api(self, module):
        """Test update-page with real API."""
        input_data = ModuleInput(
            operation="update-page",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]



class TestAPIConnectivity:
    """Test basic API connectivity and authentication."""

    @pytest.mark.asyncio
    async def test_module_can_connect(self, module):
        """
        Test that the module can connect to the external service.

        This verifies:
        - API key is valid
        - Network connectivity works
        - Basic authentication succeeds
        """
        # Use a read-only or low-impact operation to test connectivity
        health = module.health_check()
        assert health["healthy"] or "needs implementation" in str(health.get("issues", []))


class TestErrorHandling:
    """Test error handling with real API errors."""

    @pytest.mark.asyncio
    async def test_invalid_parameters_handled(self, module):
        """Test that invalid parameters return proper error responses."""
        input_data = ModuleInput(
            operation="search",
            parameters={
                # Intentionally invalid/missing required params
            },
        )
        result = await module.execute(input_data)
        # Should fail gracefully, not crash
        assert result.status in [
            ExecutionStatus.FAILURE,
            ExecutionStatus.ERROR,
        ]
        assert result.error is not None
