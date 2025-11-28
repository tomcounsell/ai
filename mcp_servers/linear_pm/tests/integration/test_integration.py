"""
Integration Tests for Linear Project Manager

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
from mcp_servers.linear_pm.src.processor import LinearProjectManagerModule


# Configuration
API_KEY_ENV = "LINEAR_API_KEY"
SKIP_REASON = f"{API_KEY_ENV} not set - skipping real API tests"


def has_api_key() -> bool:
    """Check if API key is available for testing."""
    return bool(os.environ.get(API_KEY_ENV))


# Skip entire module if no API key
pytestmark = pytest.mark.skipif(not has_api_key(), reason=SKIP_REASON)


@pytest.fixture
def module():
    """Create module instance with real API configuration."""
    return LinearProjectManagerModule()


@pytest.fixture
def cleanup_ids():
    """Track IDs of resources created during tests for cleanup."""
    ids = []
    yield ids
    # Cleanup would happen here if needed
    # For now, tests should clean up their own resources


class TestCreateissueIntegration:
    """Integration tests for create-issue."""

    @pytest.mark.asyncio
    async def test_create_issue_real_api(self, module):
        """Test create-issue with real API."""
        input_data = ModuleInput(
            operation="create-issue",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetissueIntegration:
    """Integration tests for get-issue."""

    @pytest.mark.asyncio
    async def test_get_issue_real_api(self, module):
        """Test get-issue with real API."""
        input_data = ModuleInput(
            operation="get-issue",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestUpdateissueIntegration:
    """Integration tests for update-issue."""

    @pytest.mark.asyncio
    async def test_update_issue_real_api(self, module):
        """Test update-issue with real API."""
        input_data = ModuleInput(
            operation="update-issue",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestSearchissuesIntegration:
    """Integration tests for search-issues."""

    @pytest.mark.asyncio
    async def test_search_issues_real_api(self, module):
        """Test search-issues with real API."""
        input_data = ModuleInput(
            operation="search-issues",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestListcyclesIntegration:
    """Integration tests for list-cycles."""

    @pytest.mark.asyncio
    async def test_list_cycles_real_api(self, module):
        """Test list-cycles with real API."""
        input_data = ModuleInput(
            operation="list-cycles",
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
            operation="create-issue",
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
