#!/usr/bin/env python3
"""
Comprehensive Test Suite for MCP Servers

Tests all MCP server implementations to validate Phase 1 completion.
Validates tool functionality, context injection, and error handling.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import MCP tools for testing
from mcp_servers.social_tools import (
    create_image,
    save_link,
    search_current_info,
    search_links,
)
from mcp_servers.notion_tools import query_notion_projects
from mcp_servers.telegram_tools import (
    get_conversation_context,
    get_recent_history,
    search_conversation_history,
)


class TestSocialToolsMCP:
    """Test suite for Social Tools MCP Server."""

    def test_search_current_info_missing_api_key(self):
        """Test search_current_info handles missing API key gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            result = search_current_info("test query")
            assert "Search unavailable" in result
            assert "PERPLEXITY_API_KEY" in result

    def test_search_current_info_with_mock_api(self):
        """Test search_current_info with mocked API response."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Test search result"

        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test_key"}):
            with patch("mcp_servers.social_tools.OpenAI") as mock_openai:
                mock_client = Mock()
                mock_client.chat.completions.create.return_value = mock_response
                mock_openai.return_value = mock_client

                result = search_current_info("test query")
                assert "test query" in result
                assert "Test search result" in result

    def test_create_image_missing_api_key(self):
        """Test create_image handles missing API key gracefully."""
        with patch.dict(os.environ, {}, clear=True):
            result = create_image("test prompt")
            assert "Image generation unavailable" in result
            assert "OPENAI_API_KEY" in result

    def test_create_image_with_chat_id(self):
        """Test create_image returns correct format for Telegram when chat_id provided."""
        # Mock the DALL-E API response
        mock_response = Mock()
        mock_response.data = [Mock()]
        mock_response.data[0].url = "https://example.com/image.png"

        # Mock the image download
        mock_image_response = Mock()
        mock_image_response.content = b"fake image data"
        mock_image_response.raise_for_status = Mock()

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
            with patch("mcp_servers.social_tools.OpenAI") as mock_openai:
                with patch("mcp_servers.social_tools.requests.get", return_value=mock_image_response):
                    mock_client = Mock()
                    mock_client.images.generate.return_value = mock_response
                    mock_openai.return_value = mock_client

                    result = create_image("test prompt", chat_id="12345")
                    assert "TELEGRAM_IMAGE_GENERATED" in result
                    assert "12345" in result

    def test_save_link_invalid_url(self):
        """Test save_link handles invalid URLs."""
        result = save_link("not-a-url")
        assert "Invalid URL format" in result

    def test_save_link_valid_url(self):
        """Test save_link with valid URL and mocked analysis."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a temporary docs directory
            docs_dir = Path(temp_dir) / "docs"
            docs_dir.mkdir()

            # Mock the _analyze_url_content function
            with patch("mcp_servers.social_tools._analyze_url_content") as mock_analyze:
                mock_analyze.return_value = {
                    "title": "Test Title",
                    "main_topic": "Test Topic",
                    "reasons_to_care": "Test Reasons"
                }

                # Mock the storage file path
                with patch("mcp_servers.social_tools.Path") as mock_path:
                    mock_path.return_value = docs_dir / "links.json"

                    result = save_link("https://example.com", chat_id="12345")
                    assert "Link Saved" in result
                    assert "Test Title" in result

    def test_search_links_no_storage_file(self):
        """Test search_links when no links exist in database."""
        result = search_links("test query")
        assert "No links found matching" in result

    def test_search_links_with_results(self):
        """Test search_links with existing links."""
        # The database already has example.com from previous test
        result = search_links("example")
        assert "Found 1 link(s)" in result
        assert "example.com" in result


class TestNotionToolsMCP:
    """Test suite for Notion Tools MCP Server."""

    def test_query_notion_projects_missing_keys(self):
        """Test query_notion_projects handles missing API keys."""
        with patch.dict(os.environ, {}, clear=True):
            result = query_notion_projects("PsyOPTIMAL", "test question")
            assert "Notion engine not available" in result

    def test_query_notion_projects_unknown_workspace(self):
        """Test query_notion_projects handles unknown workspace."""
        with patch.dict(os.environ, {"NOTION_API_KEY": "test_key", "ANTHROPIC_API_KEY": "test_key"}):
            result = query_notion_projects("UnknownWorkspace", "test question")
            assert "Unknown workspace" in result
            assert "Available workspaces" in result

    def test_query_notion_projects_workspace_alias(self):
        """Test query_notion_projects resolves workspace aliases."""
        with patch.dict(os.environ, {"NOTION_API_KEY": "test_key", "ANTHROPIC_API_KEY": "test_key"}):
            with patch("mcp_servers.notion_tools.NotionQueryEngine") as mock_engine:
                mock_instance = Mock()
                mock_instance.query_workspace.return_value = "Mocked response"
                mock_engine.return_value = mock_instance

                with patch("asyncio.run", return_value="Mocked response"):
                    result = query_notion_projects("psy", "test question")
                    # Should resolve "psy" to "PsyOPTIMAL"
                    assert result == "Mocked response"


class TestTelegramToolsMCP:
    """Test suite for Telegram Tools MCP Server."""

    def test_search_conversation_history_no_chat_id(self):
        """Test search_conversation_history handles missing chat_id."""
        result = search_conversation_history("test query")
        assert "No chat ID provided" in result
        assert "CONTEXT_DATA" in result

    def test_search_conversation_history_invalid_chat_id(self):
        """Test search_conversation_history handles invalid chat_id format."""
        result = search_conversation_history("test query", chat_id="invalid")
        assert "Invalid chat ID format" in result

    def test_search_conversation_history_import_error(self):
        """Test search_conversation_history handles missing chat history system."""
        with patch("builtins.__import__", side_effect=ImportError):
            result = search_conversation_history("test query", chat_id="12345")
            assert "Telegram chat history system not available" in result

    def test_get_conversation_context_no_chat_id(self):
        """Test get_conversation_context handles missing chat_id."""
        result = get_conversation_context()
        assert "No chat ID provided" in result

    def test_get_conversation_context_with_mocked_history(self):
        """Test get_conversation_context with mocked chat history."""
        mock_messages = [
            {"timestamp": "2024-01-01", "role": "user", "content": "Test message 1"},
            {"timestamp": "2024-01-01", "role": "assistant", "content": "Test response 1"}
        ]

        mock_chat_history = Mock()
        mock_chat_history.get_context.return_value = mock_messages

        with patch("mcp_servers.telegram_tools.ChatHistoryManager", return_value=mock_chat_history):
            result = get_conversation_context(chat_id="12345", hours_back=24)
            assert "Conversation Context Summary" in result
            assert "Test message 1" in result

    def test_get_recent_history_no_messages(self):
        """Test get_recent_history when no recent messages found."""
        mock_chat_history = Mock()
        mock_chat_history.get_context.return_value = []

        with patch("mcp_servers.telegram_tools.ChatHistoryManager", return_value=mock_chat_history):
            result = get_recent_history(chat_id="12345")
            assert "No recent messages found" in result


class TestContextInjection:
    """Test suite for context injection functionality."""

    def test_social_tools_context_extraction(self):
        """Test that social tools accept and handle context parameters."""
        # Test create_image with chat_id
        with patch.dict(os.environ, {}, clear=True):
            result = create_image("test", chat_id="12345")
            assert "OPENAI_API_KEY" in result  # Should still fail due to missing key

        # Test save_link with context parameters
        result = save_link("not-a-url", chat_id="12345", username="testuser")
        assert "Invalid URL format" in result

        # Test search_links with chat_id
        result = search_links("nonexistentquery", chat_id="12345")
        assert "No links found matching" in result

    def test_telegram_tools_context_extraction(self):
        """Test that telegram tools properly extract and validate chat_id."""
        # All telegram tools should require chat_id
        result1 = search_conversation_history("query", chat_id="")
        assert "No chat ID provided" in result1

        result2 = get_conversation_context(chat_id="")
        assert "No chat ID provided" in result2

        result3 = get_recent_history(chat_id="")
        assert "No chat ID provided" in result3


class TestMCPServerExecutables:
    """Test that MCP servers can be executed as standalone scripts."""

    def test_social_tools_server_executable(self):
        """Test that social_tools.py can be executed."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "mcp_servers/social_tools.py", "--help"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        # Should exit without error (exit code 0 or potentially 2 for help)
        assert result.returncode in [0, 2] or result.returncode is None

    def test_notion_tools_server_executable(self):
        """Test that notion_tools.py can be executed."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "mcp_servers/notion_tools.py", "--help"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode in [0, 2] or result.returncode is None

    def test_telegram_tools_server_executable(self):
        """Test that telegram_tools.py can be executed."""
        import subprocess

        result = subprocess.run(
            [sys.executable, "mcp_servers/telegram_tools.py", "--help"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode in [0, 2] or result.returncode is None


def test_mcp_configuration_file():
    """Test that .mcp.json configuration file exists and is valid."""
    config_file = project_root / ".mcp.json"
    assert config_file.exists(), ".mcp.json configuration file should exist"

    with open(config_file) as f:
        config = json.load(f)

    assert "mcpServers" in config, "Configuration should have mcpServers section"

    # Check that all three servers are configured
    servers = config["mcpServers"]
    assert "social-tools" in servers, "social-tools server should be configured"
    assert "notion-tools" in servers, "notion-tools server should be configured"
    assert "telegram-tools" in servers, "telegram-tools server should be configured"

    # Validate server configurations
    for server_name, server_config in servers.items():
        assert "command" in server_config, f"{server_name} should have command"
        assert "args" in server_config, f"{server_name} should have args"
        assert "description" in server_config, f"{server_name} should have description"
        assert isinstance(server_config["args"], list), f"{server_name} args should be a list"


if __name__ == "__main__":
    # Run tests directly
    import pytest
    pytest.main([__file__, "-v"])