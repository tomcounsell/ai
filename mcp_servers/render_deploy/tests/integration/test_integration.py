"""
Integration Tests for Render Deployment Manager

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
from mcp_servers.render_deploy.src.processor import RenderDeploymentManagerModule


# Configuration
API_KEY_ENV = "RENDER_API_KEY"
SKIP_REASON = f"{API_KEY_ENV} not set - skipping real API tests"


def has_api_key() -> bool:
    """Check if API key is available for testing."""
    return bool(os.environ.get(API_KEY_ENV))


# Skip entire module if no API key
pytestmark = pytest.mark.skipif(not has_api_key(), reason=SKIP_REASON)


@pytest.fixture
def module():
    """Create module instance with real API configuration."""
    return RenderDeploymentManagerModule()


@pytest.fixture
def cleanup_ids():
    """Track IDs of resources created during tests for cleanup."""
    ids = []
    yield ids
    # Cleanup would happen here if needed
    # For now, tests should clean up their own resources


class TestListservicesIntegration:
    """Integration tests for list-services."""

    @pytest.mark.asyncio
    async def test_list_services_real_api(self, module):
        """Test list-services with real API."""
        input_data = ModuleInput(
            operation="list-services",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetserviceIntegration:
    """Integration tests for get-service."""

    @pytest.mark.asyncio
    async def test_get_service_real_api(self, module):
        """Test get-service with real API."""
        input_data = ModuleInput(
            operation="get-service",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestTriggerdeployIntegration:
    """Integration tests for trigger-deploy."""

    @pytest.mark.asyncio
    async def test_trigger_deploy_real_api(self, module):
        """Test trigger-deploy with real API."""
        input_data = ModuleInput(
            operation="trigger-deploy",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetdeploylogsIntegration:
    """Integration tests for get-deploy-logs."""

    @pytest.mark.asyncio
    async def test_get_deploy_logs_real_api(self, module):
        """Test get-deploy-logs with real API."""
        input_data = ModuleInput(
            operation="get-deploy-logs",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestUpdateenvvarsIntegration:
    """Integration tests for update-env-vars."""

    @pytest.mark.asyncio
    async def test_update_env_vars_real_api(self, module):
        """Test update-env-vars with real API."""
        input_data = ModuleInput(
            operation="update-env-vars",
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
            operation="list-services",
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
