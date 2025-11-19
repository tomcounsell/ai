"""Tests for Handler Architecture

Comprehensive tests for the unified handler system including
registration, middleware, priorities, and execution management.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from integrations.telegram.handlers import (
    HandlerRegistry, MessageHandler, CommandHandler, MiddlewareHandler,
    HandlerType, HandlerPriority, HandlerStatus, HandlerResult,
    message_handler, command_handler, middleware_handler
)
from integrations.telegram.unified_processor import ProcessingRequest
from integrations.telegram.components.context_builder import MessageContext
from integrations.telegram.components.type_router import MessageType
from integrations.telegram.components.response_manager import FormattedResponse


# Mock classes for testing
class MockMessage:
    def __init__(self, message_id=12345, text="Test message"):
        self.id = message_id
        self.message = text


class MockUser:
    def __init__(self, user_id=67890):
        self.id = user_id


class TestHandlerRegistry:
    """Test suite for HandlerRegistry"""
    
    @pytest.fixture
    def registry(self):
        """Create handler registry for testing"""
        return HandlerRegistry()
    
    @pytest.fixture
    def sample_request(self):
        """Create sample processing request"""
        return ProcessingRequest(
            message=MockMessage(),
            user=MockUser(),
            chat_id=123,
            message_id=12345,
            raw_text="Test message"
        )
    
    @pytest.fixture
    def sample_context(self):
        """Create sample message context"""
        return MessageContext(
            message_id=12345,
            chat_id=123,
            user_id=67890,
            timestamp=time.time(),
            text_content="Test message"
        )
    
    def test_registry_initialization(self):
        """Test handler registry initialization"""
        
        registry = HandlerRegistry(
            enable_middleware=True,
            enable_handler_timeout=True,
            default_timeout_seconds=30,
            max_concurrent_handlers=5
        )
        
        assert registry.enable_middleware is True
        assert registry.enable_handler_timeout is True
        assert registry.default_timeout_seconds == 30
        assert len(registry.handlers) == 0
        assert len(registry.middleware_handlers) == 0
    
    def test_message_handler_registration(self, registry):
        """Test registering message handlers"""
        
        def test_handler(request, context):
            return "Test response"
        
        registry.register_message_handler(
            handler_id="test_handler",
            handler_func=test_handler,
            message_types=[MessageType.TEXT_CASUAL],
            priority=HandlerPriority.NORMAL
        )
        
        assert "test_handler" in registry.handlers
        assert len(registry.handlers_by_type[HandlerType.MESSAGE]) == 1
        
        handler = registry.handlers["test_handler"]
        assert isinstance(handler, MessageHandler)
        assert handler.priority == HandlerPriority.NORMAL
    
    def test_command_handler_registration(self, registry):
        """Test registering command handlers"""
        
        def help_command(command, args, request, context):
            return f"Help for command: {command}"
        
        registry.register_command_handler(
            command="help",
            handler_func=help_command,
            description="Show help information",
            aliases=["h", "?"]
        )
        
        assert "cmd_help" in registry.handlers
        assert "help" in registry.command_handlers
        assert "h" in registry.command_handlers
        assert "?" in registry.command_handlers
        
        handler = registry.command_handlers["help"]
        assert isinstance(handler, CommandHandler)
        assert handler.command == "help"
        assert handler.description == "Show help information"
    
    def test_middleware_registration(self, registry):
        """Test registering middleware"""
        
        def auth_middleware(request, context):
            return True  # Allow processing
        
        registry.register_middleware(
            middleware_id="auth_middleware",
            middleware_func=auth_middleware,
            priority=HandlerPriority.CRITICAL
        )
        
        assert "auth_middleware" in registry.handlers
        assert len(registry.middleware_handlers) == 1
        
        middleware = registry.middleware_handlers[0]
        assert isinstance(middleware, MiddlewareHandler)
        assert middleware.priority == HandlerPriority.CRITICAL
    
    @pytest.mark.asyncio
    async def test_message_handler_execution(self, registry, sample_request, sample_context):
        """Test executing message handlers"""
        
        def echo_handler(request, context):
            return f"Echo: {request.raw_text}"
        
        registry.register_message_handler(
            handler_id="echo_handler",
            handler_func=echo_handler,
            message_types=[MessageType.TEXT_CASUAL]
        )
        
        # Add message_type to context for routing
        sample_context.message_type = MessageType.TEXT_CASUAL
        
        results = await registry.execute_handlers(sample_request, sample_context)
        
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].handler_id == "echo_handler"
        assert len(results[0].responses) == 1
        assert "Echo: Test message" in results[0].responses[0].text
    
    @pytest.mark.asyncio
    async def test_command_handler_execution(self, registry, sample_request, sample_context):
        """Test executing command handlers"""
        
        def status_command(command, args, request, context):
            return f"Status: OK (args: {args})"
        
        registry.register_command_handler(
            command="status",
            handler_func=status_command
        )
        
        # Modify request to be a command
        sample_request.raw_text = "/status arg1 arg2"
        
        results = await registry.execute_handlers(sample_request, sample_context)
        
        # Should execute command handler
        command_results = [r for r in results if r.handler_id == "cmd_status"]
        assert len(command_results) == 1
        assert command_results[0].success is True
        assert "Status: OK (args: ['arg1', 'arg2'])" in command_results[0].responses[0].text
    
    @pytest.mark.asyncio
    async def test_middleware_execution(self, registry, sample_request, sample_context):
        """Test middleware execution"""
        
        middleware_calls = []
        
        def logging_middleware(request, context):
            middleware_calls.append("logging")
            return True
        
        def auth_middleware(request, context):
            middleware_calls.append("auth")
            return True
        
        registry.register_middleware("logging", logging_middleware, HandlerPriority.LOW)
        registry.register_middleware("auth", auth_middleware, HandlerPriority.CRITICAL)
        
        await registry.execute_handlers(sample_request, sample_context)
        
        # Middleware should be called in priority order (CRITICAL before LOW)
        assert middleware_calls == ["auth", "logging"]
    
    @pytest.mark.asyncio
    async def test_middleware_blocking(self, registry, sample_request, sample_context):
        """Test middleware blocking further processing"""
        
        def blocking_middleware(request, context):
            return False  # Block processing
        
        def regular_handler(request, context):
            return "Should not be called"
        
        registry.register_middleware("blocking", blocking_middleware)
        registry.register_message_handler("regular", regular_handler)
        
        results = await registry.execute_handlers(sample_request, sample_context)
        
        # Should only have middleware results, no handler results
        middleware_results = [r for r in results if "middleware" in r.handler_id]
        handler_results = [r for r in results if "regular" in r.handler_id]
        
        assert len(middleware_results) > 0
        assert len(handler_results) == 0  # Should be blocked
    
    @pytest.mark.asyncio
    async def test_handler_timeout(self, registry, sample_request, sample_context):
        """Test handler timeout functionality"""
        
        async def slow_handler(request, context):
            await asyncio.sleep(2)  # Longer than default timeout
            return "Slow response"
        
        # Create handler with short timeout
        handler = MessageHandler(
            handler_id="slow_handler",
            message_types=[],
            handler_func=slow_handler,
            priority=HandlerPriority.NORMAL
        )
        handler.timeout_seconds = 1  # 1 second timeout
        
        registry.register_handler(handler, HandlerType.MESSAGE)
        
        results = await registry.execute_handlers(sample_request, sample_context)
        
        # Should timeout
        slow_results = [r for r in results if r.handler_id == "slow_handler"]
        if slow_results:
            assert slow_results[0].success is False
            assert slow_results[0].status == HandlerStatus.TIMEOUT
    
    @pytest.mark.asyncio
    async def test_handler_error_handling(self, registry, sample_request, sample_context):
        """Test handler error handling"""
        
        def error_handler(request, context):
            raise ValueError("Test error")
        
        registry.register_message_handler(
            handler_id="error_handler",
            handler_func=error_handler
        )
        
        results = await registry.execute_handlers(sample_request, sample_context)
        
        error_results = [r for r in results if r.handler_id == "error_handler"]
        assert len(error_results) == 1
        assert error_results[0].success is False
        assert error_results[0].status == HandlerStatus.FAILED
        assert "Test error" in error_results[0].error
    
    @pytest.mark.asyncio
    async def test_handler_priority_ordering(self, registry, sample_request, sample_context):
        """Test handler execution priority ordering"""
        
        execution_order = []
        
        def high_priority_handler(request, context):
            execution_order.append("high")
            return "High priority"
        
        def normal_priority_handler(request, context):
            execution_order.append("normal")
            return "Normal priority"
        
        def critical_priority_handler(request, context):
            execution_order.append("critical")
            return "Critical priority"
        
        registry.register_message_handler(
            "normal", normal_priority_handler, priority=HandlerPriority.NORMAL
        )
        registry.register_message_handler(
            "high", high_priority_handler, priority=HandlerPriority.HIGH
        )
        registry.register_message_handler(
            "critical", critical_priority_handler, priority=HandlerPriority.CRITICAL
        )
        
        await registry.execute_handlers(sample_request, sample_context)
        
        # Should execute in priority order: CRITICAL (0), HIGH (1), NORMAL (2)
        assert execution_order == ["critical", "high", "normal"]
    
    @pytest.mark.asyncio
    async def test_concurrent_handler_execution(self, registry, sample_request, sample_context):
        """Test concurrent execution within same priority group"""
        
        execution_times = {}
        
        async def timed_handler(handler_id):
            async def handler(request, context):
                start_time = time.perf_counter()
                await asyncio.sleep(0.1)  # Simulate work
                execution_times[handler_id] = time.perf_counter() - start_time
                return f"Response from {handler_id}"
            return handler
        
        # Register multiple handlers with same priority
        for i in range(3):
            handler_func = await timed_handler(f"handler_{i}")
            registry.register_message_handler(
                f"handler_{i}",
                handler_func,
                priority=HandlerPriority.NORMAL
            )
        
        start_time = time.perf_counter()
        await registry.execute_handlers(sample_request, sample_context)
        total_time = time.perf_counter() - start_time
        
        # Total time should be less than sum of individual times (parallel execution)
        individual_time_sum = sum(execution_times.values())
        assert total_time < individual_time_sum * 0.8  # Allow some overhead
    
    def test_handler_enable_disable(self, registry):
        """Test enabling and disabling handlers"""
        
        def test_handler(request, context):
            return "Test"
        
        registry.register_message_handler("test", test_handler)
        
        # Handler should be enabled by default
        handler = registry.get_handler("test")
        assert handler.enabled is True
        
        # Disable handler
        assert registry.disable_handler("test") is True
        assert handler.enabled is False
        
        # Enable handler
        assert registry.enable_handler("test") is True
        assert handler.enabled is True
        
        # Test with non-existent handler
        assert registry.enable_handler("nonexistent") is False
        assert registry.disable_handler("nonexistent") is False
    
    def test_handler_unregistration(self, registry):
        """Test unregistering handlers"""
        
        def test_handler(request, context):
            return "Test"
        
        registry.register_message_handler("test", test_handler)
        registry.register_command_handler("test", test_handler)
        
        # Verify handlers are registered
        assert "test" in registry.handlers
        assert "cmd_test" in registry.handlers
        
        # Unregister message handler
        assert registry.unregister_handler("test") is True
        assert "test" not in registry.handlers
        
        # Unregister command handler
        assert registry.unregister_handler("cmd_test") is True
        assert "cmd_test" not in registry.handlers
        
        # Test with non-existent handler
        assert registry.unregister_handler("nonexistent") is False
    
    def test_handler_listing(self, registry):
        """Test listing handlers"""
        
        def handler1(request, context):
            return "Handler 1"
        
        def handler2(request, context):
            return "Handler 2"
        
        registry.register_message_handler("handler1", handler1)
        registry.register_command_handler("test", handler2)
        
        # List all handlers
        all_handlers = registry.list_handlers()
        assert len(all_handlers) == 2
        
        # List by type
        message_handlers = registry.list_handlers(HandlerType.MESSAGE)
        assert len(message_handlers) == 1
        assert message_handlers[0].handler_id == "handler1"
        
        command_handlers = registry.list_handlers(HandlerType.COMMAND)
        assert len(command_handlers) == 1
        assert command_handlers[0].handler_id == "cmd_test"
        
        # Disable one handler and test enabled_only
        registry.disable_handler("handler1")
        enabled_handlers = registry.list_handlers(enabled_only=True)
        assert len(enabled_handlers) == 1
        
        all_handlers_including_disabled = registry.list_handlers(enabled_only=False)
        assert len(all_handlers_including_disabled) == 2
    
    @pytest.mark.asyncio
    async def test_registry_status(self, registry):
        """Test registry status reporting"""
        
        def test_handler(request, context):
            return "Test"
        
        registry.register_message_handler("test1", test_handler)
        registry.register_command_handler("test2", test_handler)
        registry.register_middleware("middleware1", test_handler)
        
        status = await registry.get_status()
        
        # Verify status structure
        assert "total_handlers" in status
        assert "handler_counts" in status
        assert "middleware_count" in status
        assert "command_count" in status
        assert "total_executions" in status
        assert "success_rate" in status
        
        assert status["total_handlers"] == 3
        assert status["middleware_count"] == 1
        assert status["command_count"] == 1
    
    @pytest.mark.asyncio
    async def test_registry_shutdown(self, registry, sample_request, sample_context):
        """Test registry graceful shutdown"""
        
        async def long_handler(request, context):
            await asyncio.sleep(1)
            return "Done"
        
        registry.register_message_handler("long", long_handler)
        
        # Start processing
        task = asyncio.create_task(
            registry.execute_handlers(sample_request, sample_context)
        )
        
        # Allow processing to start
        await asyncio.sleep(0.1)
        
        # Shutdown
        shutdown_task = asyncio.create_task(registry.shutdown())
        
        # Wait for both to complete
        results, _ = await asyncio.gather(task, shutdown_task)
        
        # Verify cleanup
        assert len(registry.handlers) == 0
        assert len(registry.middleware_handlers) == 0


class TestHandlerDecorators:
    """Test suite for handler decorators"""
    
    @pytest.fixture
    def registry(self):
        return HandlerRegistry()
    
    def test_message_handler_decorator(self, registry):
        """Test message handler decorator"""
        
        @message_handler(
            registry,
            message_types=[MessageType.TEXT_CASUAL],
            priority=HandlerPriority.HIGH
        )
        def decorated_handler(request, context):
            return "Decorated response"
        
        assert "decorated_handler" in registry.handlers
        handler = registry.handlers["decorated_handler"]
        assert isinstance(handler, MessageHandler)
        assert handler.priority == HandlerPriority.HIGH
        assert MessageType.TEXT_CASUAL in handler.message_types
    
    def test_command_handler_decorator(self, registry):
        """Test command handler decorator"""
        
        @command_handler(
            registry,
            command="decorated",
            description="Decorated command",
            aliases=["dec"]
        )
        def decorated_command(command, args, request, context):
            return f"Decorated command: {command}"
        
        assert "decorated" in registry.command_handlers
        handler = registry.command_handlers["decorated"]
        assert isinstance(handler, CommandHandler)
        assert handler.description == "Decorated command"
        assert "dec" in handler.aliases
    
    def test_middleware_decorator(self, registry):
        """Test middleware decorator"""
        
        @middleware_handler(
            registry,
            priority=HandlerPriority.CRITICAL
        )
        def decorated_middleware(request, context):
            return True
        
        assert "decorated_middleware" in registry.handlers
        assert len(registry.middleware_handlers) == 1
        middleware = registry.middleware_handlers[0]
        assert middleware.priority == HandlerPriority.CRITICAL


class TestHandlerClasses:
    """Test suite for individual handler classes"""
    
    def test_message_handler_can_handle(self):
        """Test MessageHandler can_handle logic"""
        
        def test_func(request, context):
            return "test"
        
        handler = MessageHandler(
            handler_id="test",
            message_types=[MessageType.TEXT_CASUAL, MessageType.TEXT_QUESTION],
            handler_func=test_func
        )
        
        context1 = MessageContext(
            message_id=1, chat_id=1, user_id=1, timestamp=time.time()
        )
        context1.message_type = MessageType.TEXT_CASUAL
        
        context2 = MessageContext(
            message_id=2, chat_id=2, user_id=2, timestamp=time.time()
        )
        context2.message_type = MessageType.TEXT_TECHNICAL
        
        request = ProcessingRequest(
            message=MockMessage(), user=MockUser(), chat_id=1, message_id=1
        )
        
        # Should handle TEXT_CASUAL
        assert asyncio.run(handler.can_handle(request, context1)) is True
        
        # Should not handle TEXT_TECHNICAL
        assert asyncio.run(handler.can_handle(request, context2)) is False
    
    def test_command_handler_can_handle(self):
        """Test CommandHandler can_handle logic"""
        
        def test_func(command, args, request, context):
            return "test"
        
        handler = CommandHandler(
            handler_id="help_cmd",
            command="help",
            handler_func=test_func,
            aliases=["h", "?"]
        )
        
        context = MessageContext(
            message_id=1, chat_id=1, user_id=1, timestamp=time.time()
        )
        
        # Test command recognition
        request1 = ProcessingRequest(
            message=MockMessage(), user=MockUser(),
            chat_id=1, message_id=1, raw_text="/help"
        )
        assert asyncio.run(handler.can_handle(request1, context)) is True
        
        # Test alias recognition
        request2 = ProcessingRequest(
            message=MockMessage(), user=MockUser(),
            chat_id=1, message_id=1, raw_text="!h"
        )
        assert asyncio.run(handler.can_handle(request2, context)) is True
        
        # Test non-command
        request3 = ProcessingRequest(
            message=MockMessage(), user=MockUser(),
            chat_id=1, message_id=1, raw_text="help me"
        )
        assert asyncio.run(handler.can_handle(request3, context)) is False
    
    def test_handler_metrics(self):
        """Test handler metrics collection"""
        
        def test_func(request, context):
            return "test"
        
        handler = MessageHandler("test", [], test_func)
        
        # Initial metrics
        metrics = handler.get_metrics()
        assert metrics["execution_count"] == 0
        assert metrics["success_count"] == 0
        assert metrics["success_rate"] == 0.0
        
        # Simulate executions
        handler.execution_count = 10
        handler.success_count = 8
        handler.total_execution_time = 5.0
        
        metrics = handler.get_metrics()
        assert metrics["execution_count"] == 10
        assert metrics["success_count"] == 8
        assert metrics["success_rate"] == 0.8
        assert metrics["avg_execution_time"] == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])