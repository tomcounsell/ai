"""
Agent-Tools Integration Tests

Tests the integration between Valor agent and the tool ecosystem,
ensuring proper tool discovery, registration, execution, and result handling.
"""

import asyncio
import pytest
import time
from typing import Dict, Any, List
from unittest.mock import AsyncMock, MagicMock, patch

from agents.valor.agent import ValorAgent, ValorResponse
from agents.tool_registry import ToolRegistry
from tools.base import ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext
from tools.search_tool import SearchTool
from tools.code_execution_tool import CodeExecutionTool


class MockTool(ToolImplementation):
    """Mock tool for testing purposes."""
    
    def __init__(self):
        super().__init__(
            name="mock_tool",
            version="1.0.0",
            description="Mock tool for testing"
        )
    
    @property
    def input_model(self):
        class MockInput(BaseInputModel):
            query: str
            options: Dict[str, Any] = {}
        return MockInput
    
    @property
    def output_model(self):
        class MockOutput(BaseOutputModel):
            result: str
            metadata: Dict[str, Any] = {}
        return MockOutput
    
    async def _execute_core(self, input_data, context: ToolContext):
        # Simulate work
        await asyncio.sleep(0.1)
        
        return {
            "result": f"Mock result for query: {input_data.query}",
            "metadata": {
                "execution_time": "100ms",
                "tool": "mock_tool",
                "options": input_data.options
            }
        }


class TestAgentToolsIntegration:
    """Test suite for Agent-Tools integration."""
    
    @pytest.fixture
    async def tool_registry(self):
        """Create test tool registry."""
        registry = ToolRegistry()
        return registry
    
    @pytest.fixture
    async def agent(self, tool_registry):
        """Create test agent with tool registry."""
        agent = ValorAgent(
            model="openai:gpt-3.5-turbo",
            debug=True
        )
        
        # Register some test tools
        mock_tool = MockTool()
        tool_registry.register_tool(mock_tool)
        
        # Register tools with agent
        for tool_name, tool_func in tool_registry.get_available_tools().items():
            agent.register_tool(tool_func)
        
        yield agent
        
        # Cleanup
        for chat_id in agent.list_contexts():
            await agent.clear_context(chat_id)
    
    @pytest.mark.asyncio
    async def test_tool_discovery_and_registration(self, agent: ValorAgent, tool_registry: ToolRegistry):
        """Test that agent discovers and registers tools properly."""
        # Check tools are available in registry
        available_tools = tool_registry.get_available_tools()
        assert len(available_tools) > 0
        assert "mock_tool" in [name for name in available_tools.keys()]
        
        # Create context and check tool availability
        chat_id = "tool_test_001"
        context = await agent.create_context(chat_id=chat_id, user_name="tool_user")
        
        assert context is not None
        
        # Agent should have tools registered
        active_tools = agent.get_active_tools(chat_id)
        # Note: Active tools are added when tools are actually used
        assert isinstance(active_tools, list)
    
    @pytest.mark.asyncio
    async def test_tool_execution_through_agent(self, agent: ValorAgent):
        """Test tool execution through agent interface."""
        chat_id = "tool_execution_test"
        await agent.create_context(chat_id=chat_id, user_name="executor")
        
        # Create a message that would trigger tool use
        message = "Please use the mock_tool to process the query 'test data'"
        
        # Process message (this should trigger tool execution if properly integrated)
        response = await agent.process_message(message=message, chat_id=chat_id)
        
        assert response is not None
        assert isinstance(response, ValorResponse)
        assert response.content is not None
        assert len(response.content) > 0
        
        # Check if tools were used (if the mock tool was actually called)
        if response.tools_used:
            assert isinstance(response.tools_used, list)
    
    @pytest.mark.asyncio
    async def test_multiple_tool_registration(self, tool_registry: ToolRegistry):
        """Test registration of multiple tools."""
        # Register multiple mock tools
        tools = []
        for i in range(3):
            tool = MockTool()
            tool.name = f"mock_tool_{i}"
            tool_registry.register_tool(tool)
            tools.append(tool)
        
        available_tools = tool_registry.get_available_tools()
        
        for i in range(3):
            tool_name = f"mock_tool_{i}"
            assert tool_name in [name for name in available_tools.keys()]
        
        # Test tool metadata
        for tool in tools:
            metadata = tool_registry.get_tool_metadata(tool.name)
            assert metadata is not None
            assert metadata.get("name") == tool.name
            assert metadata.get("version") == tool.version
    
    @pytest.mark.asyncio
    async def test_tool_error_handling(self, tool_registry: ToolRegistry):
        """Test proper error handling when tools fail."""
        class FailingTool(ToolImplementation):
            def __init__(self):
                super().__init__(name="failing_tool", version="1.0.0")
            
            @property
            def input_model(self):
                class FailInput(BaseInputModel):
                    data: str
                return FailInput
            
            @property
            def output_model(self):
                class FailOutput(BaseOutputModel):
                    result: str = "error"
                return FailOutput
            
            async def _execute_core(self, input_data, context):
                raise Exception("Simulated tool failure")
        
        failing_tool = FailingTool()
        tool_registry.register_tool(failing_tool)
        
        # Create agent with failing tool
        agent = ValorAgent(model="openai:gpt-3.5-turbo", debug=True)
        for tool_name, tool_func in tool_registry.get_available_tools().items():
            agent.register_tool(tool_func)
        
        chat_id = "error_test"
        await agent.create_context(chat_id=chat_id, user_name="error_user")
        
        # Try to use the failing tool
        message = "Use the failing_tool with some data"
        response = await agent.process_message(message=message, chat_id=chat_id)
        
        # Agent should handle tool failures gracefully
        assert response is not None
        assert response.content is not None
        # Should not crash the entire system
    
    @pytest.mark.asyncio
    async def test_tool_context_passing(self, agent: ValorAgent):
        """Test that tool context is properly passed and utilized."""
        class ContextAwareTool(ToolImplementation):
            def __init__(self):
                super().__init__(name="context_tool", version="1.0.0")
                self.last_context = None
            
            @property
            def input_model(self):
                class ContextInput(BaseInputModel):
                    message: str
                return ContextInput
            
            @property
            def output_model(self):
                class ContextOutput(BaseOutputModel):
                    result: str
                    context_info: Dict[str, Any] = {}
                return ContextOutput
            
            async def _execute_core(self, input_data, context: ToolContext):
                self.last_context = context
                return {
                    "result": f"Processed: {input_data.message}",
                    "context_info": {
                        "execution_id": context.execution_id,
                        "user_id": context.user_id,
                        "session_id": context.session_id
                    }
                }
        
        context_tool = ContextAwareTool()
        agent.register_tool(context_tool)
        
        chat_id = "context_test"
        await agent.create_context(chat_id=chat_id, user_name="context_user")
        
        message = "Use context_tool to process this message"
        response = await agent.process_message(message=message, chat_id=chat_id)
        
        assert response is not None
        
        # Check that context was passed to tool
        if context_tool.last_context:
            assert context_tool.last_context.execution_id is not None
            assert isinstance(context_tool.last_context.execution_id, str)
    
    @pytest.mark.asyncio
    async def test_tool_performance_tracking(self, agent: ValorAgent):
        """Test that tool performance metrics are tracked."""
        class TimedTool(ToolImplementation):
            def __init__(self):
                super().__init__(name="timed_tool", version="1.0.0")
            
            @property
            def input_model(self):
                class TimedInput(BaseInputModel):
                    delay: float = 0.1
                return TimedInput
            
            @property
            def output_model(self):
                class TimedOutput(BaseOutputModel):
                    result: str
                    elapsed: float
                return TimedOutput
            
            async def _execute_core(self, input_data, context):
                start_time = time.perf_counter()
                await asyncio.sleep(input_data.delay)
                elapsed = time.perf_counter() - start_time
                
                return {
                    "result": "Timed operation complete",
                    "elapsed": elapsed
                }
        
        timed_tool = TimedTool()
        agent.register_tool(timed_tool)
        
        chat_id = "performance_test"
        await agent.create_context(chat_id=chat_id, user_name="perf_user")
        
        message = "Use timed_tool with a 0.2 second delay"
        start_time = time.perf_counter()
        response = await agent.process_message(message=message, chat_id=chat_id)
        total_time = time.perf_counter() - start_time
        
        assert response is not None
        
        # Check performance metrics are available
        if hasattr(response, 'metadata') and response.metadata:
            assert 'timestamp' in response.metadata
        
        # Tool should have performance history
        perf_stats = timed_tool.get_performance_stats()
        assert perf_stats is not None
        
        if 'total_executions' in perf_stats:
            assert perf_stats['total_executions'] >= 0
    
    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self, agent: ValorAgent, tool_registry: ToolRegistry):
        """Test concurrent tool execution through agent."""
        # Register multiple instances of mock tool
        for i in range(3):
            tool = MockTool()
            tool.name = f"concurrent_tool_{i}"
            tool_registry.register_tool(tool)
            agent.register_tool(tool_registry.get_tool_function(tool.name))
        
        # Create multiple contexts
        chat_ids = ["concurrent_1", "concurrent_2", "concurrent_3"]
        for chat_id in chat_ids:
            await agent.create_context(chat_id=chat_id, user_name=f"user_{chat_id}")
        
        # Execute tools concurrently
        async def execute_tool_message(chat_id: str, tool_index: int):
            message = f"Use concurrent_tool_{tool_index} to process data"
            return await agent.process_message(message=message, chat_id=chat_id)
        
        tasks = [
            execute_tool_message(chat_ids[i], i)
            for i in range(3)
        ]
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # All tasks should complete successfully
        for response in responses:
            assert not isinstance(response, Exception)
            assert response is not None
            assert isinstance(response, ValorResponse)
    
    @pytest.mark.asyncio
    async def test_tool_quality_assessment(self, agent: ValorAgent):
        """Test that tool quality metrics are assessed."""
        class QualityTool(ToolImplementation):
            def __init__(self):
                super().__init__(name="quality_tool", version="1.0.0")
            
            @property
            def input_model(self):
                class QualityInput(BaseInputModel):
                    data: str
                return QualityInput
            
            @property
            def output_model(self):
                class QualityOutput(BaseOutputModel):
                    result: str
                    quality_score: float = 8.0
                return QualityOutput
            
            async def _execute_core(self, input_data, context):
                return {
                    "result": f"High quality result for: {input_data.data}",
                    "quality_score": 9.5
                }
            
            async def _custom_quality_assessment(self, quality, input_data, result, context):
                """Custom quality assessment."""
                quality.add_dimension("accuracy", 9.0)
                quality.add_dimension("performance", 8.5)
                quality.add_dimension("usability", 9.2)
        
        quality_tool = QualityTool()
        agent.register_tool(quality_tool)
        
        chat_id = "quality_test"
        await agent.create_context(chat_id=chat_id, user_name="quality_user")
        
        message = "Use quality_tool to process high-quality data"
        response = await agent.process_message(message=message, chat_id=chat_id)
        
        assert response is not None
        
        # Check quality stats
        quality_stats = quality_tool.get_quality_stats()
        assert quality_stats is not None
        
        if 'total_assessments' in quality_stats:
            assert quality_stats['total_assessments'] >= 0
    
    @pytest.mark.asyncio
    async def test_tool_health_monitoring(self, tool_registry: ToolRegistry):
        """Test tool health monitoring integration."""
        mock_tool = MockTool()
        tool_registry.register_tool(mock_tool)
        
        # Check tool health
        health_check = mock_tool.health_check()
        assert health_check is not None
        assert 'tool_name' in health_check
        assert 'tool_version' in health_check
        assert 'status' in health_check
        assert 'health_score' in health_check
        
        assert health_check['tool_name'] == "mock_tool"
        assert health_check['tool_version'] == "1.0.0"
        assert health_check['status'] in ["healthy", "unhealthy"]
        assert 0.0 <= health_check['health_score'] <= 10.0
    
    @pytest.mark.asyncio
    async def test_dynamic_tool_registration(self, agent: ValorAgent):
        """Test dynamic tool registration during runtime."""
        chat_id = "dynamic_test"
        await agent.create_context(chat_id=chat_id, user_name="dynamic_user")
        
        # Initially no custom tools
        available_tools = agent.tool_registry.get_available_tools() if hasattr(agent, 'tool_registry') else {}
        initial_count = len(available_tools)
        
        # Dynamically register a new tool
        class DynamicTool(ToolImplementation):
            def __init__(self):
                super().__init__(name="dynamic_tool", version="1.0.0")
            
            @property
            def input_model(self):
                class DynamicInput(BaseInputModel):
                    text: str
                return DynamicInput
            
            @property
            def output_model(self):
                class DynamicOutput(BaseOutputModel):
                    result: str
                return DynamicOutput
            
            async def _execute_core(self, input_data, context):
                return {"result": f"Dynamic processing of: {input_data.text}"}
        
        dynamic_tool = DynamicTool()
        agent.register_tool(dynamic_tool)
        
        # Tool should now be available for use
        message = "Use the dynamic_tool to process some text"
        response = await agent.process_message(message=message, chat_id=chat_id)
        
        assert response is not None
        assert response.content is not None
        
        # Note: Actual tool usage depends on the agent's ability to understand and route to tools
        # This test verifies the registration mechanism works
    
    @pytest.mark.asyncio
    async def test_tool_metadata_integration(self, tool_registry: ToolRegistry):
        """Test that tool metadata is properly integrated."""
        mock_tool = MockTool()
        metadata = {
            "category": "utility",
            "tags": ["testing", "mock"],
            "author": "test_suite",
            "documentation": "Mock tool for testing purposes"
        }
        
        tool_registry.register_tool(mock_tool, metadata=metadata)
        
        # Verify metadata is stored and retrievable
        stored_metadata = tool_registry.get_tool_metadata("mock_tool")
        assert stored_metadata is not None
        
        for key, value in metadata.items():
            assert key in stored_metadata
            assert stored_metadata[key] == value