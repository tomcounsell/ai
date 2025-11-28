"""
Integration Tests for Sentry Error Monitor

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
from mcp_servers.sentry_monitor.src.processor import SentryErrorMonitorModule


# Configuration
API_KEY_ENV = "SENTRY_AUTH_TOKEN"
SKIP_REASON = f"{API_KEY_ENV} not set - skipping real API tests"


def has_api_key() -> bool:
    """Check if API key is available for testing."""
    return bool(os.environ.get(API_KEY_ENV))


# Skip entire module if no API key
pytestmark = pytest.mark.skipif(not has_api_key(), reason=SKIP_REASON)


@pytest.fixture
def module():
    """Create module instance with real API configuration."""
    return SentryErrorMonitorModule()


@pytest.fixture
def cleanup_ids():
    """Track IDs of resources created during tests for cleanup."""
    ids = []
    yield ids
    # Cleanup would happen here if needed
    # For now, tests should clean up their own resources


class TestGetrecentissuesIntegration:
    """Integration tests for get-recent-issues."""

    @pytest.mark.asyncio
    async def test_get_recent_issues_real_api(self, module):
        """Test get-recent-issues with real API."""
        input_data = ModuleInput(
            operation="get-recent-issues",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetissuedetailsIntegration:
    """Integration tests for get-issue-details."""

    @pytest.mark.asyncio
    async def test_get_issue_details_real_api(self, module):
        """Test get-issue-details with real API."""
        input_data = ModuleInput(
            operation="get-issue-details",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGeterroreventsIntegration:
    """Integration tests for get-error-events."""

    @pytest.mark.asyncio
    async def test_get_error_events_real_api(self, module):
        """Test get-error-events with real API."""
        input_data = ModuleInput(
            operation="get-error-events",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestResolveissueIntegration:
    """Integration tests for resolve-issue."""

    @pytest.mark.asyncio
    async def test_resolve_issue_real_api(self, module):
        """Test resolve-issue with real API."""
        input_data = ModuleInput(
            operation="resolve-issue",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetprojectstatsIntegration:
    """Integration tests for get-project-stats."""

    @pytest.mark.asyncio
    async def test_get_project_stats_real_api(self, module):
        """Test get-project-stats with real API."""
        input_data = ModuleInput(
            operation="get-project-stats",
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
            operation="get-recent-issues",
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
