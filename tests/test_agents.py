"""Comprehensive test suite for the agent architecture system."""

import asyncio
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from agents import (
    ValorAgent,
    ValorContext,
    ContextWindowManager,
    CompressionStrategy,
    TokenEstimator,
    ToolRegistry,
    ToolMetadata,
    tool_registry_decorator,
)
from agents.valor.context import MessageEntry, UserPreferences, ToolUsage


class TestValorContext:
    """Test suite for ValorContext model."""
    
    def test_context_creation(self):
        """Test basic context creation."""
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        assert context.chat_id == "test_chat"
        assert context.user_name == "test_user"
        assert context.workspace == "default"
        assert len(context.message_history) == 0
        assert len(context.active_tools) == 0
    
    def test_add_message(self):
        """Test adding messages to context."""
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        message = context.add_message(
            role="user",
            content="Hello world",
            importance_score=5.0
        )
        
        assert len(context.message_history) == 1
        assert message.role == "user"
        assert message.content == "Hello world"
        assert message.importance_score == 5.0
        assert context.context_metrics.total_messages == 1
    
    def test_add_tool_usage(self):
        """Test recording tool usage."""
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        tool_usage = context.add_tool_usage(
            tool_name="test_tool",
            parameters={"param1": "value1"},
            execution_time=1.5,
            success=True
        )
        
        assert len(context.tool_usage_history) == 1
        assert tool_usage.tool_name == "test_tool"
        assert tool_usage.parameters == {"param1": "value1"}
        assert tool_usage.execution_time == 1.5
        assert tool_usage.success is True
        assert "test_tool" in context.active_tools
        assert context.context_metrics.tools_used_count == 1
    
    def test_mark_message_important(self):
        """Test marking messages as important."""
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        message = context.add_message(role="user", content="Important message")
        result = context.mark_message_important(message.id)
        
        assert result is True
        assert message.id in context.important_messages
        assert message.importance_score >= 8.0
    
    def test_get_recent_messages(self):
        """Test retrieving recent messages."""
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        # Add multiple messages
        for i in range(25):
            context.add_message(role="user", content=f"Message {i}")
        
        recent = context.get_recent_messages(10)
        assert len(recent) == 10
        assert recent[-1].content == "Message 24"  # Most recent
    
    def test_compress_history(self):
        """Test history compression."""
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        # Add messages with some important ones
        for i in range(30):
            message = context.add_message(role="user", content=f"Message {i}")
            if i % 10 == 0:  # Mark every 10th message as important
                context.mark_message_important(message.id)
        
        removed_count = context.compress_history(keep_recent=15, keep_important=True)
        
        assert removed_count > 0
        assert len(context.message_history) <= 30  # Should be reduced
        assert context.context_metrics.context_compressions == 1


class TestTokenEstimator:
    """Test suite for TokenEstimator."""
    
    def test_estimate_tokens_basic(self):
        """Test basic token estimation."""
        text = "Hello world, this is a test message."
        tokens = TokenEstimator.estimate_tokens(text)
        
        assert tokens > 0
        assert isinstance(tokens, int)
    
    def test_estimate_tokens_empty(self):
        """Test token estimation for empty text."""
        tokens = TokenEstimator.estimate_tokens("")
        assert tokens == 0
    
    def test_estimate_message_tokens(self):
        """Test message token estimation."""
        message = MessageEntry(
            role="user",
            content="This is a test message",
            metadata={"test": "value"}
        )
        
        tokens = TokenEstimator.estimate_message_tokens(message)
        assert tokens > 0
        assert message.token_count == tokens  # Should be cached
    
    def test_different_model_families(self):
        """Test token estimation for different model families."""
        text = "Test message for token estimation"
        
        gpt4_tokens = TokenEstimator.estimate_tokens(text, "gpt-4")
        claude_tokens = TokenEstimator.estimate_tokens(text, "claude")
        
        # Different models should have different ratios
        assert gpt4_tokens != claude_tokens


class TestContextWindowManager:
    """Test suite for ContextWindowManager."""
    
    def test_manager_initialization(self):
        """Test context manager initialization."""
        manager = ContextWindowManager(max_tokens=50000)
        
        assert manager.max_tokens == 50000
        assert manager.model_family == "default"
        assert isinstance(manager.compression_strategy, CompressionStrategy)
    
    def test_count_tokens(self):
        """Test token counting for context."""
        manager = ContextWindowManager()
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        context.add_message(role="user", content="Hello world")
        context.add_message(role="assistant", content="Hi there!")
        
        token_count = manager.count_tokens(context)
        assert token_count > 0
        assert context.context_metrics.total_tokens == token_count
    
    def test_needs_compression(self):
        """Test compression need detection."""
        manager = ContextWindowManager(max_tokens=100)
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        # Add a lot of content
        for i in range(20):
            context.add_message(
                role="user", 
                content="This is a very long message that should trigger compression " * 10
            )
        
        needs_compression = manager.needs_compression(context, threshold=0.5)
        assert needs_compression is True
    
    @pytest.mark.asyncio
    async def test_compress_context(self):
        """Test context compression."""
        manager = ContextWindowManager(max_tokens=1000)
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        # Add many messages
        for i in range(50):
            context.add_message(role="user", content=f"Message {i} with some content")
        
        original_count = len(context.message_history)
        compressed_context = await manager.compress_context(context)
        
        assert len(compressed_context.message_history) <= original_count
        assert compressed_context.context_metrics.context_compressions >= 1
    
    @pytest.mark.asyncio
    async def test_prepare_context_for_inference(self):
        """Test context preparation for inference."""
        manager = ContextWindowManager(max_tokens=1000)
        context = ValorContext(
            chat_id="test_chat",
            user_name="test_user"
        )
        
        # Add messages
        for i in range(20):
            context.add_message(role="user", content="Test message " * 10)
        
        prepared_context, info = await manager.prepare_context_for_inference(
            context, additional_tokens=200
        )
        
        assert "original_message_count" in info
        assert "final_message_count" in info
        assert info["final_message_count"] <= info["original_message_count"]


class TestToolRegistry:
    """Test suite for ToolRegistry."""
    
    def test_registry_initialization(self):
        """Test tool registry initialization."""
        registry = ToolRegistry()
        
        assert len(registry.get_available_tools()) == 0
        assert len(registry.list_tools()) == 0
    
    def test_register_simple_tool(self):
        """Test registering a simple tool."""
        registry = ToolRegistry()
        
        def test_tool(message: str) -> str:
            """A simple test tool."""
            return f"Processed: {message}"
        
        tool_name = registry.register_tool(test_tool, description="Test tool")
        
        assert tool_name == "test_tool"
        assert registry.get_tool("test_tool") == test_tool
        assert "test_tool" in registry.list_tools()
    
    def test_register_tool_with_metadata(self):
        """Test registering a tool with custom metadata."""
        registry = ToolRegistry()
        
        def advanced_tool(param1: str, param2: int = 10) -> dict:
            """An advanced test tool."""
            return {"param1": param1, "param2": param2}
        
        metadata = ToolMetadata(
            name="advanced_tool",
            description="Advanced test tool",
            category="testing",
            version="2.0.0",
            tags=["test", "advanced"]
        )
        
        tool_name = registry.register_tool(advanced_tool, metadata=metadata)
        
        assert tool_name == "advanced_tool"
        retrieved_metadata = registry.get_tool_metadata("advanced_tool")
        assert retrieved_metadata.version == "2.0.0"
        assert retrieved_metadata.category == "testing"
        assert "test" in retrieved_metadata.tags
    
    @pytest.mark.asyncio
    async def test_execute_tool(self):
        """Test tool execution."""
        registry = ToolRegistry()
        
        def multiply_tool(a: int, b: int) -> int:
            """Multiply two numbers."""
            return a * b
        
        registry.register_tool(multiply_tool)
        
        execution = await registry.execute_tool(
            "multiply_tool",
            {"a": 5, "b": 3}
        )
        
        assert execution.success is True
        assert execution.result == 15
        assert execution.tool_name == "multiply_tool"
        assert execution.execution_time is not None
    
    @pytest.mark.asyncio
    async def test_execute_async_tool(self):
        """Test async tool execution."""
        registry = ToolRegistry()
        
        async def async_tool(delay: float) -> str:
            """An async test tool."""
            await asyncio.sleep(delay)
            return f"Completed after {delay} seconds"
        
        registry.register_tool(async_tool)
        
        execution = await registry.execute_tool(
            "async_tool",
            {"delay": 0.1}
        )
        
        assert execution.success is True
        assert "Completed after 0.1 seconds" in execution.result
        assert execution.execution_time >= 0.1
    
    @pytest.mark.asyncio
    async def test_tool_execution_error_handling(self):
        """Test tool execution error handling."""
        registry = ToolRegistry()
        
        def failing_tool(should_fail: bool) -> str:
            """A tool that can fail."""
            if should_fail:
                raise ValueError("This tool failed intentionally")
            return "Success"
        
        registry.register_tool(failing_tool)
        
        # Test successful execution
        execution = await registry.execute_tool(
            "failing_tool",
            {"should_fail": False}
        )
        assert execution.success is True
        assert execution.result == "Success"
        
        # Test failed execution
        execution = await registry.execute_tool(
            "failing_tool",
            {"should_fail": True}
        )
        assert execution.success is False
        assert "failed intentionally" in execution.error
    
    def test_tool_search(self):
        """Test tool search functionality."""
        registry = ToolRegistry()
        
        def calculator_tool():
            """Calculator for math operations."""
            pass
        
        def text_processor():
            """Process text data."""
            pass
        
        registry.register_tool(
            calculator_tool,
            description="Math calculator",
            category="math",
            tags=["calculator", "math"]
        )
        registry.register_tool(
            text_processor,
            description="Text processing utility",
            category="text",
            tags=["text", "processing"]
        )
        
        # Search by name
        results = registry.search_tools("calculator")
        assert "calculator_tool" in results
        
        # Search by description
        results = registry.search_tools("math")
        assert "calculator_tool" in results
        
        # Search by tag
        results = registry.search_tools("processing")
        assert "text_processor" in results
    
    def test_list_tools_with_filters(self):
        """Test listing tools with category/tag filters."""
        registry = ToolRegistry()
        
        def tool1():
            pass
        def tool2():
            pass
        def tool3():
            pass
        
        registry.register_tool(tool1, category="math", tags=["calculation"])
        registry.register_tool(tool2, category="text", tags=["processing"])
        registry.register_tool(tool3, category="math", tags=["geometry"])
        
        # Filter by category
        math_tools = registry.list_tools(category="math")
        assert len(math_tools) == 2
        assert "tool1" in math_tools
        assert "tool3" in math_tools
        
        # Filter by tags
        processing_tools = registry.list_tools(tags=["processing"])
        assert len(processing_tools) == 1
        assert "tool2" in processing_tools
    
    def test_get_usage_stats(self):
        """Test usage statistics collection."""
        registry = ToolRegistry()
        
        def test_tool() -> str:
            return "test"
        
        registry.register_tool(test_tool)
        
        # Initially no usage
        stats = registry.get_tool_usage_stats("test_tool")
        assert stats["usage_count"] == 0
        
        # After registering executions manually
        registry._executions.append(
            ToolUsage(
                tool_name="test_tool",
                parameters={},
                execution_time=1.0,
                success=True
            )
        )
        
        stats = registry.get_tool_usage_stats("test_tool")
        assert stats["usage_count"] == 1
        assert stats["success_rate"] == 1.0


class TestToolDecorator:
    """Test suite for tool registration decorator."""
    
    def test_basic_decorator(self):
        """Test basic tool decorator usage."""
        @tool_registry_decorator(name="decorated_tool", description="A decorated tool")
        def my_tool(input_text: str) -> str:
            return f"Processed: {input_text}"
        
        assert hasattr(my_tool, '_tool_registration')
        assert my_tool._tool_registration['name'] == "decorated_tool"
        assert my_tool._tool_registration['description'] == "A decorated tool"
    
    def test_async_decorator(self):
        """Test decorator with async function."""
        @tool_registry_decorator(name="async_decorated_tool")
        async def my_async_tool(delay: float) -> str:
            await asyncio.sleep(delay)
            return "Done"
        
        assert hasattr(my_async_tool, '_tool_registration')
        assert asyncio.iscoroutinefunction(my_async_tool)


@pytest.mark.asyncio
async def test_valor_agent_integration():
    """Integration test for ValorAgent with mocked PydanticAI."""
    # Mock PydanticAI components
    with patch('agents.valor.agent.Agent') as mock_agent_class:
        mock_agent_instance = AsyncMock()
        mock_agent_class.return_value = mock_agent_instance
        
        # Mock agent run result
        mock_result = MagicMock()
        mock_result.data.content = "Test response"
        mock_result.data.tools_used = ["test_tool"]
        mock_agent_instance.run.return_value = mock_result
        
        # Create agent
        agent = ValorAgent(model="test_model", debug=True)
        
        # Test message processing
        response = await agent.process_message(
            message="Hello, test message",
            chat_id="test_chat",
            user_name="test_user"
        )
        
        assert response.content == "Test response"
        assert response.context_updated is True
        assert "test_tool" in response.tools_used
        
        # Verify context was created
        context = agent.get_context("test_chat")
        assert context is not None
        assert context.chat_id == "test_chat"
        assert context.user_name == "test_user"
        assert len(context.message_history) == 2  # User message + assistant response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])