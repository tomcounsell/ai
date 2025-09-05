"""
Test Suite for MCP Server Implementations
Tests all MCP servers for proper initialization, tool registration, and functionality.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.base import MCPServer, MCPRequest, MCPResponse, MCPError, MCPToolCapability
from mcp_servers.social_tools import SocialToolsServer
from mcp_servers.pm_tools import ProjectManagementServer
from mcp_servers.telegram_tools import TelegramToolsServer
from mcp_servers.development_tools import DevelopmentToolsServer
from mcp_servers.orchestrator import MCPOrchestrator
from mcp_servers.context_manager import MCPContextManager


class TestMCPServerImports:
    """Test that all MCP servers can be imported successfully."""
    
    def test_base_mcp_import(self):
        """Test base MCP framework imports."""
        assert MCPServer is not None
        assert MCPRequest is not None
        assert MCPResponse is not None
        assert MCPError is not None
        assert MCPToolCapability is not None
    
    def test_social_tools_server_import(self):
        """Test social tools server imports."""
        assert SocialToolsServer is not None
    
    def test_pm_tools_server_import(self):
        """Test PM tools server imports."""
        assert ProjectManagementServer is not None
    
    def test_telegram_tools_server_import(self):
        """Test Telegram tools server imports."""
        assert TelegramToolsServer is not None
    
    def test_development_tools_server_import(self):
        """Test development tools server imports."""
        assert DevelopmentToolsServer is not None
    
    def test_orchestrator_import(self):
        """Test MCP orchestrator imports."""
        assert MCPOrchestrator is not None
    
    def test_context_manager_import(self):
        """Test MCP context manager imports."""
        assert MCPContextManager is not None


class TestMCPServerInitialization:
    """Test MCP server initialization."""
    
    def test_social_tools_server_creation(self):
        """Test social tools server can be created."""
        server = SocialToolsServer(name="social_tools", version="1.0.0")
        assert server.name == "social_tools"
        assert server.version == "1.0.0"
        assert server.get_status() is not None
    
    def test_pm_tools_server_creation(self):
        """Test PM tools server can be created."""
        server = ProjectManagementServer(name="pm_tools", version="1.0.0")
        assert server.name == "pm_tools"
        assert server.version == "1.0.0"
        assert server.get_status() is not None
    
    def test_telegram_tools_server_creation(self):
        """Test Telegram tools server can be created."""
        server = TelegramToolsServer(name="telegram_tools", version="1.0.0")
        assert server.name == "telegram_tools"
        assert server.version == "1.0.0"
        assert server.get_status() is not None
    
    def test_development_tools_server_creation(self):
        """Test development tools server can be created."""
        server = DevelopmentToolsServer(name="dev_tools", version="1.0.0")
        assert server.name == "dev_tools"
        assert server.version == "1.0.0"
        assert server.get_status() is not None


@pytest.mark.asyncio
class TestMCPServerToolRegistration:
    """Test tool registration in MCP servers."""
    
    async def test_social_tools_server_initialization(self):
        """Test social tools server initialization and tool registration."""
        server = SocialToolsServer(name="social_tools", version="1.0.0")
        
        # Initialize server
        await server.initialize()
        
        # Check tools are registered
        capabilities = server.get_capabilities()
        assert len(capabilities) > 0
        
        # Check for expected tools
        tool_names = [cap.name for cap in capabilities]
        assert any("search" in name for name in tool_names)
    
    async def test_development_tools_server_initialization(self):
        """Test development tools server initialization."""
        server = DevelopmentToolsServer(name="dev_tools", version="1.0.0")
        
        # Initialize server
        await server.initialize()
        
        # Check tools are registered
        capabilities = server.get_capabilities()
        assert len(capabilities) > 0
        
        # Check for expected tools
        tool_names = [cap.name for cap in capabilities]
        assert any("lint" in name or "test" in name or "format" in name for name in tool_names)


class TestMCPRequestHandling:
    """Test MCP request/response handling."""
    
    def test_mcp_request_creation(self):
        """Test MCP request creation."""
        request = MCPRequest(
            method="web_search",
            params={"query": "test search", "engine": "google"}
        )
        assert request.method == "web_search"
        assert request.params["query"] == "test search"
        assert request.id is not None
        assert request.timestamp is not None
    
    def test_mcp_response_creation(self):
        """Test MCP response creation."""
        response = MCPResponse(
            id="test-123",
            success=True,
            result={"data": "test result"}
        )
        assert response.id == "test-123"
        assert response.success is True
        assert response.result["data"] == "test result"
    
    def test_mcp_error_creation(self):
        """Test MCP error creation."""
        error = MCPError(
            message="Test error",
            error_code="TEST_ERROR",
            details={"reason": "testing"},
            request_id="test-123"
        )
        assert error.message == "Test error"
        assert error.error_code == "TEST_ERROR"
        assert error.details["reason"] == "testing"
        assert error.request_id == "test-123"


@pytest.mark.asyncio
class TestMCPServerExecution:
    """Test MCP server execution functionality."""
    
    async def test_social_tools_web_search(self):
        """Test web search functionality in social tools server."""
        server = SocialToolsServer(name="social_tools", version="1.0.0")
        await server.initialize()
        
        # Create request
        request = MCPRequest(
            method="web_search",
            params={"query": "Python programming", "max_results": 5}
        )
        
        # Mock the HTTP client to avoid actual API calls
        with patch.object(server, 'http_client') as mock_client:
            mock_response = Mock()
            mock_response.json.return_value = {
                "results": [
                    {"title": "Python.org", "url": "https://python.org", "snippet": "Official Python site"}
                ]
            }
            mock_client.get = AsyncMock(return_value=mock_response)
            
            # Handle request
            response = await server.handle_request(request)
            assert response.success is True
    
    async def test_development_tools_linting(self):
        """Test linting functionality in development tools server."""
        server = DevelopmentToolsServer(name="dev_tools", version="1.0.0")
        await server.initialize()
        
        # Create request
        request = MCPRequest(
            method="lint_code",
            params={
                "code": "print('hello')",
                "language": "python",
                "fix": False
            }
        )
        
        # Handle request (this should work with actual linting)
        response = await server.handle_request(request)
        assert response is not None


class TestMCPOrchestration:
    """Test MCP orchestrator functionality."""
    
    def test_orchestrator_creation(self):
        """Test orchestrator can be created."""
        orchestrator = MCPOrchestrator()
        assert orchestrator is not None
    
    @pytest.mark.asyncio
    async def test_orchestrator_server_registration(self):
        """Test server registration with orchestrator."""
        orchestrator = MCPOrchestrator()
        
        # Create and register servers
        social_server = SocialToolsServer(name="social_tools", version="1.0.0")
        dev_server = DevelopmentToolsServer(name="dev_tools", version="1.0.0")
        
        await orchestrator.register_server("social", social_server)
        await orchestrator.register_server("dev", dev_server)
        
        # Check servers are registered
        servers = orchestrator.get_registered_servers()
        assert "social" in servers
        assert "dev" in servers


class TestMCPContextInjection:
    """Test context injection in MCP servers."""
    
    @pytest.mark.asyncio
    async def test_context_manager_creation(self):
        """Test context manager can be created."""
        context_manager = MCPContextManager()
        assert context_manager is not None
        
        # Test context validation
        test_context = {
            "user_id": "test-user",
            "session_id": "test-session",
            "timestamp": datetime.now().isoformat()
        }
        
        is_valid = await context_manager.validate_context(test_context)
        assert is_valid is True
    
    @pytest.mark.asyncio
    async def test_context_injection(self):
        """Test context injection into requests."""
        context_manager = MCPContextManager()
        
        request = MCPRequest(
            method="test_method",
            params={"test": "data"}
        )
        
        server_context = {
            "server_name": "test_server",
            "version": "1.0.0"
        }
        
        # Inject context
        enriched_context = await context_manager.inject_context(request, server_context)
        assert enriched_context is not None
        assert "server_name" in enriched_context or "request_id" in enriched_context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])