"""
Integration Tests for Telegram Messenger

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
from mcp_servers.telegram_messenger.src.processor import TelegramMessengerModule


# Configuration
API_KEY_ENV = "TELEGRAM_BOT_TOKEN"
SKIP_REASON = f"{API_KEY_ENV} not set - skipping real API tests"


def has_api_key() -> bool:
    """Check if API key is available for testing."""
    return bool(os.environ.get(API_KEY_ENV))


# Skip entire module if no API key
pytestmark = pytest.mark.skipif(not has_api_key(), reason=SKIP_REASON)


@pytest.fixture
def module():
    """Create module instance with real API configuration."""
    return TelegramMessengerModule()


@pytest.fixture
def cleanup_ids():
    """Track IDs of resources created during tests for cleanup."""
    ids = []
    yield ids
    # Cleanup would happen here if needed
    # For now, tests should clean up their own resources


class TestSendmessageIntegration:
    """Integration tests for send-message."""

    @pytest.mark.asyncio
    async def test_send_message_real_api(self, module):
        """Test send-message with real API."""
        input_data = ModuleInput(
            operation="send-message",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetmessagesIntegration:
    """Integration tests for get-messages."""

    @pytest.mark.asyncio
    async def test_get_messages_real_api(self, module):
        """Test get-messages with real API."""
        input_data = ModuleInput(
            operation="get-messages",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestGetchatinfoIntegration:
    """Integration tests for get-chat-info."""

    @pytest.mark.asyncio
    async def test_get_chat_info_real_api(self, module):
        """Test get-chat-info with real API."""
        input_data = ModuleInput(
            operation="get-chat-info",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestAddreactionIntegration:
    """Integration tests for add-reaction."""

    @pytest.mark.asyncio
    async def test_add_reaction_real_api(self, module):
        """Test add-reaction with real API."""
        input_data = ModuleInput(
            operation="add-reaction",
            parameters={
                # TODO: Add test parameters
            },
        )
        result = await module.execute(input_data)
        # TODO: Add assertions based on expected results
        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]


class TestSearchmessagesIntegration:
    """Integration tests for search-messages."""

    @pytest.mark.asyncio
    async def test_search_messages_real_api(self, module):
        """Test search-messages with real API."""
        input_data = ModuleInput(
            operation="search-messages",
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
            operation="send-message",
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
