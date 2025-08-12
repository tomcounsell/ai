"""
Tools-MCP Integration Tests

Tests the integration between tools and MCP (Model Context Protocol) servers,
ensuring proper tool routing, server coordination, and distributed execution.
"""

import asyncio
import pytest
import json
import uuid
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch

from tools.base import ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext
from mcp_servers.orchestrator import MCPOrchestrator, ServerRegistration
from mcp_servers.base import MCPServer, MCPRequest, MCPResponse, MCPToolCapability
from agents.tool_registry import ToolRegistry


class MockMCPServer(MCPServer):
    """Mock MCP server for testing."""
    
    def __init__(self, name: str = "mock_mcp_server"):
        super().__init__(name=name, version="1.0.0", description="Mock MCP server for testing")
        self.processed_requests = []
    
    async def process_request(self, request: MCPRequest) -> MCPResponse:
        """Process a mock request."""
        self.processed_requests.append(request)
        
        if request.method == "health_check":
            return MCPResponse(
                id=request.id,
                success=True,
                result={
                    "healthy": True,
                    "health_score": 9.0,
                    "server_name": self.name
                }
            )
        elif request.method == "mock_tool_operation":
            return MCPResponse(
                id=request.id,
                success=True,
                result={
                    "result": f"Mock result for: {request.params.get('query', 'no query')}",
                    "server": self.name,
                    "processed_at": "2024-01-01T00:00:00Z"
                }
            )
        elif request.method.startswith("failing_"):
            return MCPResponse(
                id=request.id,
                success=False,
                error={
                    "code": "MOCK_ERROR",
                    "message": "Simulated failure for testing"
                }
            )
        else:
            return MCPResponse(
                id=request.id,
                success=True,
                result={"message": f"Processed {request.method}", "params": request.params}
            )
    
    def get_capabilities(self) -> List[MCPToolCapability]:
        """Return mock capabilities."""
        return [
            MCPToolCapability(
                name="mock_tool_operation",
                description="Mock tool operation for testing",
                parameters={"query": "string"}
            ),
            MCPToolCapability(
                name="health_check",
                description="Health check operation",
                parameters={}
            )
        ]


class MCPIntegratedTool(ToolImplementation):
    """Tool that integrates with MCP servers."""
    
    def __init__(self, mcp_orchestrator: MCPOrchestrator):
        super().__init__(
            name="mcp_integrated_tool",
            version="1.0.0",
            description="Tool that uses MCP servers for execution"
        )
        self.mcp_orchestrator = mcp_orchestrator
    
    @property
    def input_model(self):
        class MCPInput(BaseInputModel):
            operation: str
            query: str
            target_server: str = None
        return MCPInput
    
    @property
    def output_model(self):
        class MCPOutput(BaseOutputModel):
            result: str
            server_info: Dict[str, Any] = {}
            mcp_response: Dict[str, Any] = {}
        return MCPOutput
    
    async def _execute_core(self, input_data, context: ToolContext):
        # Create MCP request
        mcp_request = MCPRequest(
            method=input_data.operation,
            params={"query": input_data.query},
            id=str(uuid.uuid4())
        )
        
        # Route through MCP orchestrator
        response = await self.mcp_orchestrator.route_request(mcp_request)
        
        if response.success:
            return {
                "result": str(response.result),
                "server_info": {
                    "routed_by": response.metadata.get("routed_by"),
                    "target_server": response.metadata.get("target_server")
                },
                "mcp_response": {
                    "success": response.success,
                    "metadata": response.metadata
                }
            }
        else:
            raise Exception(f"MCP request failed: {response.error}")


class TestToolsMCPIntegration:
    """Test suite for Tools-MCP integration."""
    
    @pytest.fixture
    async def mcp_orchestrator(self):
        """Create MCP orchestrator with mock servers."""
        orchestrator = MCPOrchestrator(
            name="test_orchestrator",
            enable_inter_server_messaging=False,  # Disable for simpler testing
            enable_load_balancing=True
        )
        
        await orchestrator.start()
        
        # Register mock servers
        mock_servers = [
            MockMCPServer("mock_server_1"),
            MockMCPServer("mock_server_2"),
            MockMCPServer("mock_server_3")
        ]
        
        for server in mock_servers:
            # Register server manually
            registration = ServerRegistration(
                server_name=server.name,
                server_type="mock_server",
                version=server.version,
                description=server.description,
                server_instance=server
            )
            orchestrator._registered_servers[server.name] = registration
            orchestrator._server_load_counters[server.name] = 0
        
        yield orchestrator
        
        await orchestrator.stop()
    
    @pytest.fixture
    async def tool_registry(self):
        """Create tool registry for testing."""
        return ToolRegistry()
    
    @pytest.mark.asyncio
    async def test_tool_mcp_basic_integration(self, mcp_orchestrator: MCPOrchestrator):
        """Test basic integration between tools and MCP servers."""
        # Create tool that uses MCP
        mcp_tool = MCPIntegratedTool(mcp_orchestrator)
        
        # Execute tool operation
        input_data = mcp_tool.input_model(
            operation="mock_tool_operation",
            query="test query for MCP integration"
        )
        
        context = ToolContext(
            execution_id=str(uuid.uuid4()),
            user_id="test_user"
        )
        
        result = await mcp_tool.execute(input_data, context)
        
        assert result is not None
        assert hasattr(result, 'result')
        assert "Mock result for: test query for MCP integration" in result.result
        assert result.server_info is not None
        assert result.mcp_response['success'] is True
    
    @pytest.mark.asyncio
    async def test_mcp_server_routing(self, mcp_orchestrator: MCPOrchestrator):
        """Test that requests are properly routed to MCP servers."""
        # Create request
        request = MCPRequest(
            method="mock_tool_operation",
            params={"query": "routing test"},
            id=str(uuid.uuid4())
        )
        
        # Route request
        response = await mcp_orchestrator.route_request(request)
        
        assert response.success
        assert "Mock result for: routing test" in str(response.result)
        assert response.metadata is not None
        assert "routed_by" in response.metadata
        assert response.metadata["routed_by"] == "test_orchestrator"
    
    @pytest.mark.asyncio
    async def test_mcp_load_balancing(self, mcp_orchestrator: MCPOrchestrator):
        """Test load balancing across multiple MCP servers."""
        requests = []
        responses = []
        
        # Send multiple requests
        for i in range(10):
            request = MCPRequest(
                method="mock_tool_operation",
                params={"query": f"load_test_{i}"},
                id=str(uuid.uuid4())
            )
            requests.append(request)
            
            response = await mcp_orchestrator.route_request(request)
            responses.append(response)
        
        # All requests should succeed
        assert all(response.success for response in responses)
        
        # Check that different servers were used (load balancing)
        target_servers = [resp.metadata.get("target_server") for resp in responses]
        unique_servers = set(target_servers)
        
        # Should distribute across available servers
        assert len(unique_servers) > 1 or len(mcp_orchestrator._registered_servers) == 1
    
    @pytest.mark.asyncio
    async def test_mcp_health_monitoring(self, mcp_orchestrator: MCPOrchestrator):
        """Test health monitoring of MCP servers."""
        # Trigger health checks
        await mcp_orchestrator._perform_health_checks()
        
        # Check health summary
        health_summary = mcp_orchestrator.get_health_summary()
        
        assert health_summary is not None
        assert "total_servers" in health_summary
        assert "healthy_servers" in health_summary
        assert health_summary["total_servers"] >= 3  # Our mock servers
        assert health_summary["healthy_servers"] >= 0
        
        # All mock servers should be healthy
        assert health_summary["healthy_servers"] == health_summary["total_servers"]
    
    @pytest.mark.asyncio
    async def test_mcp_error_handling(self, mcp_orchestrator: MCPOrchestrator):
        """Test error handling in MCP integration."""
        # Create request that will fail
        failing_request = MCPRequest(
            method="failing_operation",
            params={"data": "test"},
            id=str(uuid.uuid4())
        )
        
        response = await mcp_orchestrator.route_request(failing_request)
        
        assert not response.success
        assert response.error is not None
        assert "MOCK_ERROR" in response.error.get("code", "")
    
    @pytest.mark.asyncio
    async def test_tool_mcp_error_propagation(self, mcp_orchestrator: MCPOrchestrator):
        """Test that MCP errors are properly propagated to tools."""
        mcp_tool = MCPIntegratedTool(mcp_orchestrator)
        
        input_data = mcp_tool.input_model(
            operation="failing_operation",
            query="this will fail"
        )
        
        context = ToolContext(execution_id=str(uuid.uuid4()))
        
        # Tool execution should handle the MCP error
        with pytest.raises(Exception) as exc_info:
            await mcp_tool.execute(input_data, context)
        
        assert "MCP request failed" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_concurrent_mcp_requests(self, mcp_orchestrator: MCPOrchestrator):
        """Test concurrent requests to MCP servers."""
        async def make_request(index: int):
            request = MCPRequest(
                method="mock_tool_operation",
                params={"query": f"concurrent_test_{index}"},
                id=str(uuid.uuid4())
            )
            return await mcp_orchestrator.route_request(request)
        
        # Execute concurrent requests
        tasks = [make_request(i) for i in range(20)]
        responses = await asyncio.gather(*tasks)
        
        # All should succeed
        assert all(response.success for response in responses)
        
        # Verify unique responses
        results = [response.result for response in responses]
        assert len(set(str(result) for result in results)) == 20  # All unique
    
    @pytest.mark.asyncio
    async def test_mcp_server_capabilities(self, mcp_orchestrator: MCPOrchestrator):
        """Test MCP server capability discovery."""
        # Get server capabilities
        capabilities = mcp_orchestrator.get_server_capabilities()
        
        assert isinstance(capabilities, dict)
        assert len(capabilities) > 0
        
        # Check mock server capabilities
        for server_name in capabilities.keys():
            server_caps = capabilities[server_name]
            assert isinstance(server_caps, list)
            
            # Should have our mock capabilities
            cap_names = [cap['name'] for cap in server_caps]
            assert "mock_tool_operation" in cap_names
            assert "health_check" in cap_names
    
    @pytest.mark.asyncio
    async def test_mcp_orchestrator_stats(self, mcp_orchestrator: MCPOrchestrator):
        """Test MCP orchestrator statistics collection."""
        # Generate some activity
        for i in range(5):
            request = MCPRequest(
                method="mock_tool_operation",
                params={"query": f"stats_test_{i}"},
                id=str(uuid.uuid4())
            )
            await mcp_orchestrator.route_request(request)
        
        # Get stats
        stats = mcp_orchestrator.get_orchestrator_stats()
        
        assert stats is not None
        assert "requests_routed" in stats
        assert "uptime_seconds" in stats
        assert stats["requests_routed"] >= 5
        assert stats["uptime_seconds"] >= 0
    
    @pytest.mark.asyncio
    async def test_tool_registry_mcp_bridge(self, tool_registry: ToolRegistry, mcp_orchestrator: MCPOrchestrator):
        """Test bridging tool registry to MCP orchestrator."""
        # Create MCP bridge function
        async def mcp_bridge(tool_name: str, **kwargs):
            """Bridge tool calls to MCP servers."""
            request = MCPRequest(
                method=tool_name,
                params=kwargs,
                id=str(uuid.uuid4())
            )
            
            response = await mcp_orchestrator.route_request(request)
            return response.result if response.success else None
        
        # Register bridge with tool registry
        tool_registry.register_external_bridge("mcp", mcp_bridge)
        
        # Test bridge functionality
        result = await mcp_bridge("mock_tool_operation", query="bridge test")
        
        assert result is not None
        assert "Mock result for: bridge test" in str(result)
    
    @pytest.mark.asyncio
    async def test_mcp_tool_performance_metrics(self, mcp_orchestrator: MCPOrchestrator):
        """Test performance metrics collection for MCP tools."""
        mcp_tool = MCPIntegratedTool(mcp_orchestrator)
        
        # Execute tool multiple times
        for i in range(3):
            input_data = mcp_tool.input_model(
                operation="mock_tool_operation",
                query=f"performance_test_{i}"
            )
            
            context = ToolContext(execution_id=str(uuid.uuid4()))
            await mcp_tool.execute(input_data, context)
        
        # Check performance stats
        perf_stats = mcp_tool.get_performance_stats()
        
        assert perf_stats is not None
        if "total_executions" in perf_stats:
            assert perf_stats["total_executions"] >= 3
        if "average_duration_ms" in perf_stats:
            assert perf_stats["average_duration_ms"] > 0
    
    @pytest.mark.asyncio
    async def test_mcp_server_failover(self, mcp_orchestrator: MCPOrchestrator):
        """Test failover behavior when servers become unavailable."""
        # Mark one server as unhealthy
        server_names = list(mcp_orchestrator._registered_servers.keys())
        if server_names:
            first_server = server_names[0]
            registration = mcp_orchestrator._registered_servers[first_server]
            from mcp_servers.orchestrator import ServerHealth
            registration.health_status = ServerHealth.UNHEALTHY
        
        # Make requests - should route to healthy servers
        responses = []
        for i in range(5):
            request = MCPRequest(
                method="mock_tool_operation",
                params={"query": f"failover_test_{i}"},
                id=str(uuid.uuid4())
            )
            response = await mcp_orchestrator.route_request(request)
            responses.append(response)
        
        # All requests should still succeed (routed to healthy servers)
        assert all(response.success for response in responses)
        
        # Should avoid the unhealthy server
        target_servers = [resp.metadata.get("target_server") for resp in responses]
        if len(server_names) > 1:
            assert first_server not in target_servers or target_servers.count(first_server) < 5
    
    @pytest.mark.asyncio
    async def test_mcp_context_injection(self, mcp_orchestrator: MCPOrchestrator):
        """Test context injection in MCP requests."""
        # Create request with context
        request = MCPRequest(
            method="mock_tool_operation",
            params={"query": "context test"},
            id=str(uuid.uuid4()),
            context={"user_id": "test_user", "session_id": "test_session"}
        )
        
        response = await mcp_orchestrator.route_request(request)
        
        assert response.success
        assert response.metadata is not None
        
        # Context should be preserved/enhanced
        assert "routed_by" in response.metadata
        assert "routing_timestamp" in response.metadata
    
    @pytest.mark.asyncio
    async def test_tool_mcp_quality_assessment(self, mcp_orchestrator: MCPOrchestrator):
        """Test quality assessment for MCP-integrated tools."""
        mcp_tool = MCPIntegratedTool(mcp_orchestrator)
        
        # Execute tool with quality context
        input_data = mcp_tool.input_model(
            operation="mock_tool_operation",
            query="quality assessment test"
        )
        
        context = ToolContext(
            execution_id=str(uuid.uuid4()),
            quality_threshold=8.0
        )
        
        result = await mcp_tool.execute(input_data, context)
        
        assert result is not None
        assert hasattr(result, 'quality_score')
        
        if result.quality_score:
            assert result.quality_score.overall_score >= 0.0
            assert result.quality_score.overall_score <= 10.0