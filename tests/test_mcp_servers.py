"""
Comprehensive tests for MCP servers ensuring stateless operation and context injection.
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from mcp_servers.base import (
    MCPServer, MCPRequest, MCPResponse, MCPToolCapability,
    MCPError, MCPServerFactory, DefaultContextInjector
)
from mcp_servers.context_manager import (
    MCPContextManager, WorkspaceContext, UserContext, SessionContext,
    SecurityLevel, EnrichedContext
)
from mcp_servers.social_tools import SocialToolsServer
from mcp_servers.pm_tools import ProjectManagementServer
from mcp_servers.telegram_tools import TelegramToolsServer
from mcp_servers.development_tools import DevelopmentToolsServer
from mcp_servers.orchestrator import MCPOrchestrator, ServerRegistration


class MockMCPServer(MCPServer):
    """Mock MCP server for testing base functionality."""
    
    def __init__(self, name: str = "test_server", **kwargs):
        super().__init__(name, **kwargs)
        self.initialize_called = False
        self.shutdown_called = False
    
    async def initialize(self) -> None:
        self.initialize_called = True
        # Register a test tool
        test_capability = MCPToolCapability(
            name="test_tool",
            description="Test tool for unit tests",
            parameters={"input": {"type": "string", "required": True}},
            returns={"type": "string"},
            tags=["test"]
        )
        self.register_tool(test_capability, self._handle_test_tool)
    
    async def shutdown(self) -> None:
        self.shutdown_called = True
    
    async def _handle_test_tool(self, request: MCPRequest, context: Dict[str, Any]) -> str:
        input_value = request.params.get("input", "")
        return f"Processed: {input_value}"


@pytest.fixture
async def mock_server():
    """Create a mock MCP server for testing."""
    server = MockMCPServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def context_manager():
    """Create a context manager for testing."""
    return MCPContextManager()


@pytest.fixture
async def orchestrator():
    """Create an orchestrator for testing."""
    orch = MCPOrchestrator()
    await orch.start()
    yield orch
    await orch.stop()


class TestMCPServerBase:
    """Test cases for MCP server base functionality."""
    
    @pytest.mark.asyncio
    async def test_server_lifecycle(self):
        """Test server startup and shutdown lifecycle."""
        server = MockMCPServer()
        
        # Test initial state
        assert server.status.name == "INITIALIZING"
        assert not server.initialize_called
        assert not server.shutdown_called
        
        # Test startup
        await server.start()
        assert server.status.name == "RUNNING"
        assert server.initialize_called
        
        # Test shutdown
        await server.stop()
        assert server.status.name == "STOPPED"
        assert server.shutdown_called
    
    @pytest.mark.asyncio
    async def test_tool_registration(self):
        """Test tool capability registration and retrieval."""
        server = MockMCPServer()
        await server.start()
        
        try:
            # Test tool is registered during initialization
            capabilities = server.get_capabilities()
            assert len(capabilities) == 1
            assert capabilities[0].name == "test_tool"
            
            # Test getting specific capability
            capability = server.get_tool_capability("test_tool")
            assert capability is not None
            assert capability.name == "test_tool"
            
            # Test non-existent capability
            assert server.get_tool_capability("nonexistent") is None
            
        finally:
            await server.stop()
    
    @pytest.mark.asyncio
    async def test_request_processing(self, mock_server):
        """Test request processing with comprehensive error handling."""
        # Test successful request
        request = MCPRequest(
            method="call_tool",
            params={"name": "test_tool", "parameters": {"input": "hello"}}
        )
        
        response = await mock_server.process_request(request)
        assert response.success
        assert response.result == "Processed: hello"
        assert response.execution_time_ms is not None
        
        # Test invalid method
        invalid_request = MCPRequest(method="invalid_method")
        response = await mock_server.process_request(invalid_request)
        assert not response.success
        assert "UNKNOWN_METHOD" in response.error["code"]
        
        # Test missing tool name
        missing_tool_request = MCPRequest(
            method="call_tool",
            params={"parameters": {"input": "test"}}
        )
        response = await mock_server.process_request(missing_tool_request)
        assert not response.success
        assert "MISSING_TOOL_NAME" in response.error["code"]
        
        # Test unknown tool
        unknown_tool_request = MCPRequest(
            method="call_tool",
            params={"name": "unknown_tool", "parameters": {}}
        )
        response = await mock_server.process_request(unknown_tool_request)
        assert not response.success
        assert "UNKNOWN_TOOL" in response.error["code"]
    
    @pytest.mark.asyncio
    async def test_server_metrics(self, mock_server):
        """Test server performance metrics collection."""
        # Process some requests to generate metrics
        for i in range(5):
            request = MCPRequest(
                method="call_tool",
                params={"name": "test_tool", "parameters": {"input": f"test{i}"}}
            )
            await mock_server.process_request(request)
        
        # Check metrics
        assert mock_server.metrics.total_requests == 5
        assert mock_server.metrics.successful_requests == 5
        assert mock_server.metrics.failed_requests == 0
        assert mock_server.metrics.average_response_time_ms > 0
        
        # Test health check
        health_request = MCPRequest(method="health_check")
        health_response = await mock_server.process_request(health_request)
        
        assert health_response.success
        assert health_response.result["healthy"]
        assert health_response.result["health_score"] >= 7.0
    
    @pytest.mark.asyncio
    async def test_stateless_operation(self):
        """Test that servers maintain stateless operation."""
        server1 = MockMCPServer(name="server1")
        server2 = MockMCPServer(name="server2")
        
        try:
            await server1.start()
            await server2.start()
            
            # Both servers should be independent
            request = MCPRequest(
                method="call_tool",
                params={"name": "test_tool", "parameters": {"input": "test"}}
            )
            
            response1 = await server1.process_request(request)
            response2 = await server2.process_request(request)
            
            # Results should be identical (stateless)
            assert response1.result == response2.result
            
            # But metrics should be independent
            assert server1.metrics.total_requests == 1
            assert server2.metrics.total_requests == 1
            
        finally:
            await server1.stop()
            await server2.stop()


class TestMCPContextManager:
    """Test cases for MCP context manager."""
    
    @pytest.mark.asyncio
    async def test_context_injection(self, context_manager):
        """Test comprehensive context injection."""
        # Register test workspace and user
        workspace = await context_manager.register_workspace(
            "test_workspace", "Test Workspace", "project"
        )
        user = await context_manager.register_user(
            "test_user", "testuser", "Test User"
        )
        session = await context_manager.create_session(
            "test_user", "test_workspace"
        )
        
        # Create request with context
        request = MCPRequest(
            method="test_method",
            context={
                "workspace_id": "test_workspace",
                "user_id": "test_user",
                "session_id": session.session_id
            }
        )
        
        # Inject context
        server_context = {"server_info": {"name": "test_server"}}
        enriched_context = await context_manager.inject_context(request, server_context)
        
        # Verify context injection
        assert enriched_context["request_id"] == request.id
        assert enriched_context["workspace"]["workspace_id"] == "test_workspace"
        assert enriched_context["user"]["user_id"] == "test_user"
        assert enriched_context["session"]["session_id"] == session.session_id
        assert enriched_context["injected_data"]["workspace_injected"]
        assert enriched_context["injected_data"]["user_injected"]
        assert enriched_context["injected_data"]["session_injected"]
    
    @pytest.mark.asyncio
    async def test_context_validation(self, context_manager):
        """Test context security validation."""
        # Valid context
        valid_context = {
            "request_id": "test_123",
            "timestamp": datetime.now(timezone.utc),
            "security": {
                "security_level": "internal",
                "permissions": [],
                "access_scopes": ["REQUEST"],
                "authenticated": False,
                "authentication_method": None,
                "authorization_checks": [],
                "denied_permissions": []
            }
        }
        
        assert await context_manager.validate_context(valid_context)
        
        # Invalid context (missing required fields)
        invalid_context = {
            "timestamp": datetime.now(timezone.utc)
        }
        
        try:
            await context_manager.validate_context(invalid_context)
            assert False, "Should have raised validation error"
        except Exception:
            pass  # Expected
    
    @pytest.mark.asyncio
    async def test_workspace_security(self, context_manager):
        """Test workspace security level enforcement."""
        # Create restricted workspace
        restricted_workspace = await context_manager.register_workspace(
            "restricted", "Restricted Workspace", security_level=SecurityLevel.RESTRICTED
        )
        
        # Create user with insufficient clearance
        low_clearance_user = await context_manager.register_user(
            "low_user", "lowuser", security_clearance=SecurityLevel.INTERNAL
        )
        
        # Create request
        request = MCPRequest(
            method="test_method",
            context={
                "workspace_id": "restricted",
                "user_id": "low_user"
            }
        )
        
        # Context injection should work
        enriched_context = await context_manager.inject_context(request, {})
        
        # But validation should fail due to security mismatch
        is_valid = await context_manager.validate_context(enriched_context)
        assert not is_valid
    
    @pytest.mark.asyncio
    async def test_context_serialization(self, context_manager):
        """Test context serialization and deserialization."""
        # Create enriched context
        enriched_context = EnrichedContext(
            request_id="test_123",
            timestamp=datetime.now(timezone.utc),
            workspace=WorkspaceContext(
                workspace_id="test_ws",
                name="Test Workspace"
            ),
            user=UserContext(
                user_id="test_user",
                username="testuser"
            )
        )
        
        # Serialize
        serialized = await context_manager.serialize_context(enriched_context)
        assert isinstance(serialized, str)
        
        # Deserialize
        deserialized = await context_manager.deserialize_context(serialized)
        assert deserialized.request_id == enriched_context.request_id
        assert deserialized.workspace.workspace_id == enriched_context.workspace.workspace_id
        assert deserialized.user.username == enriched_context.user.username


class TestSocialToolsServer:
    """Test cases for Social Tools MCP server."""
    
    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """Test social tools server initialization."""
        server = SocialToolsServer()
        await server.start()
        
        try:
            # Check that server started successfully
            assert server.status.name == "RUNNING"
            
            # Check capabilities registration
            capabilities = server.get_capabilities()
            capability_names = [cap.name for cap in capabilities]
            
            expected_tools = [
                "web_search", "extract_url_content", "create_calendar_event",
                "list_calendar_events", "generate_content", "list_content_templates",
                "search_knowledge_base"
            ]
            
            for tool in expected_tools:
                assert tool in capability_names
            
        finally:
            await server.stop()
    
    @pytest.mark.asyncio
    async def test_content_generation(self):
        """Test content generation functionality."""
        server = SocialToolsServer()
        await server.start()
        
        try:
            # Test content generation
            request = MCPRequest(
                method="call_tool",
                params={
                    "name": "generate_content",
                    "parameters": {
                        "template_name": "blog_post",
                        "variables": {
                            "title": "Test Blog Post",
                            "content": "This is test content",
                            "author": "Test Author",
                            "date": "2024-01-01"
                        }
                    }
                }
            )
            
            response = await server.process_request(request)
            assert response.success
            assert "Test Blog Post" in response.result["content"]
            assert "Test Author" in response.result["content"]
            
        finally:
            await server.stop()
    
    @pytest.mark.asyncio
    async def test_calendar_management(self):
        """Test calendar event management."""
        server = SocialToolsServer()
        await server.start()
        
        try:
            # Create calendar event
            create_request = MCPRequest(
                method="call_tool",
                params={
                    "name": "create_calendar_event",
                    "parameters": {
                        "title": "Test Meeting",
                        "description": "Test meeting description",
                        "start_time": "2024-12-01T10:00:00Z",
                        "end_time": "2024-12-01T11:00:00Z",
                        "location": "Conference Room A"
                    }
                }
            )
            
            create_response = await server.process_request(create_request)
            assert create_response.success
            assert create_response.result["title"] == "Test Meeting"
            
            # List calendar events
            list_request = MCPRequest(
                method="call_tool",
                params={
                    "name": "list_calendar_events",
                    "parameters": {}
                }
            )
            
            list_response = await server.process_request(list_request)
            assert list_response.success
            assert len(list_response.result) >= 1
            
        finally:
            await server.stop()


class TestDevelopmentToolsServer:
    """Test cases for Development Tools MCP server."""
    
    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """Test development tools server initialization."""
        server = DevelopmentToolsServer()
        await server.start()
        
        try:
            assert server.status.name == "RUNNING"
            
            capabilities = server.get_capabilities()
            capability_names = [cap.name for cap in capabilities]
            
            expected_tools = [
                "execute_code", "execute_file", "get_execution_history",
                "list_processes", "get_process_info", "monitor_system_resources",
                "start_debug_session", "profile_code", "run_tests"
            ]
            
            for tool in expected_tools:
                assert tool in capability_names
            
        finally:
            await server.stop()
    
    @pytest.mark.asyncio
    async def test_code_execution(self):
        """Test code execution functionality."""
        server = DevelopmentToolsServer()
        await server.start()
        
        try:
            # Test Python code execution
            request = MCPRequest(
                method="call_tool",
                params={
                    "name": "execute_code",
                    "parameters": {
                        "language": "python",
                        "code": "print('Hello, World!')\nresult = 2 + 2\nprint(f'2 + 2 = {result}')"
                    }
                }
            )
            
            response = await server.process_request(request)
            assert response.success
            assert "Hello, World!" in response.result["stdout"]
            assert "2 + 2 = 4" in response.result["stdout"]
            assert response.result["return_code"] == 0
            assert response.result["execution_time_ms"] > 0
            
        finally:
            await server.stop()
    
    @pytest.mark.asyncio
    async def test_security_restrictions(self):
        """Test security restrictions in code execution."""
        server = DevelopmentToolsServer(sandbox_enabled=True)
        await server.start()
        
        try:
            # Test restricted import
            dangerous_request = MCPRequest(
                method="call_tool",
                params={
                    "name": "execute_code",
                    "parameters": {
                        "language": "python",
                        "code": "import os\nos.system('ls')"
                    }
                }
            )
            
            response = await server.process_request(dangerous_request)
            assert not response.success
            assert "SECURITY_VIOLATION" in response.error["code"]
            
        finally:
            await server.stop()


class TestMCPOrchestrator:
    """Test cases for MCP orchestrator."""
    
    @pytest.mark.asyncio
    async def test_orchestrator_lifecycle(self):
        """Test orchestrator startup and shutdown."""
        orchestrator = MCPOrchestrator()
        
        # Test startup
        await orchestrator.start()
        assert orchestrator._running
        
        # Test shutdown
        await orchestrator.stop()
        assert not orchestrator._running
    
    @pytest.mark.asyncio
    async def test_server_registration(self, orchestrator):
        """Test server registration and management."""
        # Register a mock server
        registration = await orchestrator.register_server(
            "test_server",
            "development_tools",
            {"allowed_languages": ["python"]},
            auto_start=True
        )
        
        assert registration.server_name == "test_server"
        assert registration.server_type == "development_tools"
        assert registration.server_instance is not None
        
        # Test server listing
        servers = orchestrator.list_servers()
        assert len(servers) == 1
        assert servers[0].server_name == "test_server"
        
        # Test server retrieval
        server = orchestrator.get_server("test_server")
        assert server is not None
        assert server.server_name == "test_server"
        
        # Test unregistration
        success = await orchestrator.unregister_server("test_server")
        assert success
        
        servers = orchestrator.list_servers()
        assert len(servers) == 0
    
    @pytest.mark.asyncio
    async def test_request_routing(self, orchestrator):
        """Test request routing to appropriate servers."""
        # Register multiple servers
        await orchestrator.register_server(
            "dev_server",
            "development_tools",
            {"allowed_languages": ["python"]},
            auto_start=True
        )
        
        # Test routing to development server
        request = MCPRequest(
            method="execute_code",
            params={
                "language": "python",
                "code": "print('Hello from orchestrator!')"
            }
        )
        
        response = await orchestrator.route_request(request)
        assert response.success
        assert "Hello from orchestrator!" in response.result["stdout"]
        assert response.metadata["target_server"] == "dev_server"
        
        # Clean up
        await orchestrator.unregister_server("dev_server")
    
    @pytest.mark.asyncio
    async def test_health_monitoring(self, orchestrator):
        """Test health monitoring functionality."""
        # Register a server
        await orchestrator.register_server(
            "health_test_server",
            "development_tools",
            auto_start=True
        )
        
        # Perform health check
        await orchestrator._perform_health_checks()
        
        # Check health summary
        health_summary = orchestrator.get_health_summary()
        assert health_summary["total_servers"] == 1
        assert health_summary["healthy_servers"] >= 0
        
        server_health = health_summary["servers"]["health_test_server"]
        assert "status" in server_health
        assert "last_check" in server_health
        
        # Clean up
        await orchestrator.unregister_server("health_test_server")
    
    @pytest.mark.asyncio
    async def test_inter_server_messaging(self, orchestrator):
        """Test inter-server messaging capabilities."""
        if not orchestrator.enable_inter_server_messaging:
            pytest.skip("Inter-server messaging disabled")
        
        # Register two servers
        await orchestrator.register_server(
            "sender_server",
            "development_tools",
            auto_start=True
        )
        
        await orchestrator.register_server(
            "receiver_server",
            "social_tools",
            auto_start=True
        )
        
        # Send a message
        message_id = await orchestrator.send_message(
            "sender_server",
            "receiver_server",
            "test_message",
            {"data": "test payload"}
        )
        
        assert message_id is not None
        assert len(orchestrator._message_queue) == 1
        
        # Process messages
        await orchestrator._process_messages()
        
        # Clean up
        await orchestrator.unregister_server("sender_server")
        await orchestrator.unregister_server("receiver_server")
    
    @pytest.mark.asyncio
    async def test_load_balancing(self, orchestrator):
        """Test load balancing across multiple servers."""
        if not orchestrator.enable_load_balancing:
            pytest.skip("Load balancing disabled")
        
        # Register multiple servers of the same type
        await orchestrator.register_server(
            "dev_server_1",
            "development_tools",
            auto_start=True
        )
        
        await orchestrator.register_server(
            "dev_server_2", 
            "development_tools",
            auto_start=True
        )
        
        # Send multiple requests
        responses = []
        for i in range(4):
            request = MCPRequest(
                method="execute_code",
                params={
                    "language": "python",
                    "code": f"print('Request {i}')"
                }
            )
            response = await orchestrator.route_request(request)
            responses.append(response)
        
        # Check that requests were distributed
        servers_used = set()
        for response in responses:
            if response.success:
                servers_used.add(response.metadata["target_server"])
        
        # Should use both servers (load balancing)
        assert len(servers_used) <= 2  # At most 2 servers
        
        # Clean up
        await orchestrator.unregister_server("dev_server_1")
        await orchestrator.unregister_server("dev_server_2")


class TestIntegration:
    """Integration tests for complete MCP system."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_workflow(self):
        """Test complete end-to-end workflow with context injection."""
        # Set up orchestrator with context manager
        orchestrator = MCPOrchestrator()
        await orchestrator.start()
        
        try:
            # Register workspace, user, and session
            workspace = await orchestrator.context_manager.register_workspace(
                "integration_test", "Integration Test Workspace"
            )
            
            user = await orchestrator.context_manager.register_user(
                "test_user", "testuser", "Integration Test User"
            )
            
            session = await orchestrator.context_manager.create_session(
                "test_user", "integration_test"
            )
            
            # Register servers
            await orchestrator.register_server(
                "dev_server",
                "development_tools",
                auto_start=True
            )
            
            await orchestrator.register_server(
                "social_server",
                "social_tools",
                auto_start=True
            )
            
            # Test development tools with context
            dev_request = MCPRequest(
                method="execute_code",
                params={
                    "language": "python",
                    "code": "print('Integration test successful!')"
                },
                context={
                    "workspace_id": "integration_test",
                    "user_id": "test_user",
                    "session_id": session.session_id
                }
            )
            
            dev_response = await orchestrator.route_request(dev_request)
            assert dev_response.success
            assert "Integration test successful!" in dev_response.result["stdout"]
            
            # Test social tools with context
            content_request = MCPRequest(
                method="generate_content",
                params={
                    "template_name": "blog_post",
                    "variables": {
                        "title": "Integration Test Post",
                        "content": "This is an integration test",
                        "author": user.username,
                        "date": datetime.now().strftime("%Y-%m-%d")
                    }
                },
                context={
                    "workspace_id": "integration_test",
                    "user_id": "test_user",
                    "session_id": session.session_id
                }
            )
            
            content_response = await orchestrator.route_request(content_request)
            assert content_response.success
            assert "Integration Test Post" in content_response.result["content"]
            
            # Verify orchestrator stats
            stats = orchestrator.get_orchestrator_stats()
            assert stats["requests_routed"] >= 2
            assert stats["servers_registered"] == 2
            
            # Verify health monitoring
            health_summary = orchestrator.get_health_summary()
            assert health_summary["total_servers"] == 2
            assert health_summary["healthy_servers"] >= 0
            
        finally:
            await orchestrator.stop()
    
    @pytest.mark.asyncio
    async def test_error_handling_and_recovery(self):
        """Test system error handling and recovery capabilities."""
        orchestrator = MCPOrchestrator()
        await orchestrator.start()
        
        try:
            # Register server
            await orchestrator.register_server(
                "error_test_server",
                "development_tools",
                auto_start=True
            )
            
            # Test handling of invalid requests
            invalid_request = MCPRequest(
                method="nonexistent_method",
                params={}
            )
            
            response = await orchestrator.route_request(invalid_request)
            assert not response.success
            assert "error" in response.__dict__
            
            # Test handling of requests to non-existent servers
            no_server_request = MCPRequest(
                method="some_specific_method_that_no_server_handles",
                params={}
            )
            
            response = await orchestrator.route_request(no_server_request)
            # Should either route to a server or return appropriate error
            assert response.id == no_server_request.id
            
            # Test security violations
            security_request = MCPRequest(
                method="execute_code",
                params={
                    "language": "python",
                    "code": "import os; os.system('rm -rf /')"  # Dangerous code
                }
            )
            
            response = await orchestrator.route_request(security_request)
            # Should be blocked by security measures
            if response.success:
                # If it succeeded, check that the dangerous code was sanitized
                assert "SECURITY_VIOLATION" in response.result.get("error_message", "")
            else:
                # Expected to fail due to security
                assert not response.success
            
        finally:
            await orchestrator.stop()


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])