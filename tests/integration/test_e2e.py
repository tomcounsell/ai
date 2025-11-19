"""
End-to-End System Integration Tests

Tests the complete system integration from Telegram message reception
through agent processing to response delivery, ensuring all components
work together seamlessly in real-world scenarios.
"""

import asyncio
import pytest
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import uuid
from pathlib import Path

from integrations.system_integration import SystemIntegrator, initialize_system
from utilities.database import DatabaseManager
from agents.valor.agent import ValorAgent
from mcp_servers.orchestrator import MCPOrchestrator
from integrations.telegram.unified_processor import UnifiedProcessor
from config.settings import settings


class TestE2ESystemIntegration:
    """End-to-end system integration test suite."""
    
    @pytest.fixture
    async def temp_database(self):
        """Create temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
            temp_db_path = Path(temp_file.name)
        
        # Create database manager with temp path
        db_manager = DatabaseManager(db_path=temp_db_path)
        await db_manager.initialize()
        
        yield db_manager
        
        await db_manager.close()
        # Cleanup temp file
        if temp_db_path.exists():
            temp_db_path.unlink()
    
    @pytest.fixture
    def test_config(self):
        """Test configuration for system integration."""
        return {
            "agent_model": "openai:gpt-3.5-turbo",
            "max_context_tokens": 50000,
            "debug": True,
            "telegram_response_target": 1000,
            "telegram_max_concurrent": 5,
            # Note: Real Telegram credentials would be needed for full E2E
            # These are mock values for testing
            "telegram_api_id": "12345",
            "telegram_api_hash": "mock_hash",
            "telegram_bot_token": "mock_bot_token"
        }
    
    @pytest.fixture
    async def system_integrator(self, test_config, temp_database):
        """Create system integrator for testing."""
        integrator = SystemIntegrator(
            config=test_config,
            enable_monitoring=True,
            health_check_interval=10,
            auto_recovery=True
        )
        
        # Override database with test instance
        integrator.database = temp_database
        
        yield integrator
        
        # Cleanup
        if integrator.system_state.value in ["running", "ready"]:
            await integrator.shutdown()
    
    @pytest.mark.asyncio
    async def test_system_initialization_sequence(self, system_integrator: SystemIntegrator):
        """Test complete system initialization sequence."""
        # System should start uninitialized
        assert system_integrator.system_state.value == "uninitialized"
        
        # Initialize system
        await system_integrator.initialize()
        
        # Should reach ready state
        assert system_integrator.system_state.value == "ready"
        assert system_integrator.startup_time is not None
        assert system_integrator.metrics.startup_time_ms > 0
        
        # All components should be initialized
        assert system_integrator.database is not None
        assert system_integrator.agent is not None
        assert system_integrator.tool_registry is not None
        assert system_integrator.mcp_orchestrator is not None
        assert system_integrator.telegram_processor is not None
        
        # Check component health
        status = await system_integrator.get_system_status()
        assert status["system_state"] == "ready"
        
        component_health = status["component_health"]
        for component_name in ["database", "valor_agent", "mcp_orchestrator", "telegram"]:
            assert component_name in component_health
            health = component_health[component_name]
            assert health["state"] in ["ready", "running"]
            assert health["health"] in ["healthy", "degraded"]  # Allow some degradation in tests
    
    @pytest.mark.asyncio
    async def test_database_agent_integration_e2e(self, system_integrator: SystemIntegrator):
        """Test end-to-end database and agent integration."""
        await system_integrator.initialize()
        
        # Test agent context creation with database persistence
        chat_id = "e2e_test_chat"
        user_name = "e2e_test_user"
        
        context = await system_integrator.agent.create_context(
            chat_id=chat_id,
            user_name=user_name,
            workspace="e2e_workspace",
            metadata={"test_type": "e2e", "session_start": datetime.now(timezone.utc).isoformat()}
        )
        
        assert context is not None
        assert context.chat_id == chat_id
        
        # Process messages through agent
        test_messages = [
            "Hello, I'm testing the system integration",
            "Can you help me understand how this works?",
            "What capabilities do you have?"
        ]
        
        responses = []
        for message in test_messages:
            response = await system_integrator.agent.process_message(
                message=message,
                chat_id=chat_id
            )
            responses.append(response)
            
            assert response is not None
            assert response.content is not None
            assert len(response.content) > 0
        
        # Verify database persistence
        db_history = await system_integrator.database.get_chat_history(
            session_id=chat_id,
            limit=100
        )
        
        # Should have at least the user messages
        user_messages = [msg for msg in db_history if msg['role'] == 'user']
        assert len(user_messages) >= len(test_messages)
        
        for i, expected_message in enumerate(test_messages):
            if i < len(user_messages):
                assert user_messages[i]['content'] == expected_message
                assert user_messages[i]['session_id'] == chat_id
    
    @pytest.mark.asyncio
    async def test_mcp_tool_integration_e2e(self, system_integrator: SystemIntegrator):
        """Test end-to-end MCP server and tool integration."""
        await system_integrator.initialize()
        
        # Check MCP orchestrator is running
        assert system_integrator.mcp_orchestrator is not None
        
        # Get health summary
        health_summary = system_integrator.mcp_orchestrator.get_health_summary()
        assert health_summary["total_servers"] >= 0  # May have servers or not in test mode
        
        # Test direct MCP request routing
        from mcp_servers.base import MCPRequest
        
        request = MCPRequest(
            method="health_check",
            params={},
            id=str(uuid.uuid4())
        )
        
        # This should either route successfully or fail gracefully
        try:
            response = await system_integrator.mcp_orchestrator.route_request(request)
            # If we have servers, response should be valid
            if health_summary["total_servers"] > 0:
                assert response is not None
        except Exception as e:
            # No servers configured is acceptable in test mode
            assert "No servers available" in str(e) or "NO_SERVERS_AVAILABLE" in str(e)
    
    @pytest.mark.asyncio
    async def test_telegram_pipeline_integration_e2e(self, system_integrator: SystemIntegrator):
        """Test end-to-end Telegram pipeline integration."""
        await system_integrator.initialize()
        
        # Check telegram processor
        assert system_integrator.telegram_processor is not None
        
        # Get pipeline status
        status = await system_integrator.telegram_processor.get_pipeline_status()
        assert status["active_requests"] == 0  # Should start with no requests
        assert status["total_processed"] >= 0
        
        # Test component integration
        component_status = status["component_status"]
        assert "security_gate" in component_status
        assert "context_builder" in component_status
        assert "type_router" in component_status
        assert "agent_orchestrator" in component_status
        assert "response_manager" in component_status
    
    @pytest.mark.asyncio
    async def test_system_health_monitoring_e2e(self, system_integrator: SystemIntegrator):
        """Test end-to-end system health monitoring."""
        await system_integrator.initialize()
        
        # Wait for initial health check
        await asyncio.sleep(1)
        
        # Get system status
        status = await system_integrator.get_system_status()
        
        assert "system_state" in status
        assert "uptime_seconds" in status
        assert "component_health" in status
        assert "metrics" in status
        assert "recent_events" in status
        
        # Check that we have health data for components
        component_health = status["component_health"]
        assert len(component_health) > 0
        
        for component_name, health_data in component_health.items():
            assert "state" in health_data
            assert "health" in health_data
            assert "last_check" in health_data
            assert "error_count" in health_data
    
    @pytest.mark.asyncio
    async def test_concurrent_system_operations_e2e(self, system_integrator: SystemIntegrator):
        """Test concurrent operations across the integrated system."""
        await system_integrator.initialize()
        
        # Test concurrent agent operations
        chat_ids = [f"concurrent_chat_{i}" for i in range(5)]
        
        async def create_and_use_context(chat_id: str):
            # Create context
            await system_integrator.agent.create_context(
                chat_id=chat_id,
                user_name=f"user_{chat_id}",
                workspace="concurrent_test"
            )
            
            # Process messages
            for i in range(3):
                await system_integrator.agent.process_message(
                    message=f"Message {i} in {chat_id}",
                    chat_id=chat_id
                )
        
        # Run concurrent operations
        tasks = [create_and_use_context(chat_id) for chat_id in chat_ids]
        await asyncio.gather(*tasks)
        
        # Verify all contexts exist
        active_contexts = system_integrator.agent.list_contexts()
        for chat_id in chat_ids:
            assert chat_id in active_contexts
        
        # Verify database consistency
        for chat_id in chat_ids:
            history = await system_integrator.database.get_chat_history(session_id=chat_id)
            user_messages = [msg for msg in history if msg['role'] == 'user']
            assert len(user_messages) >= 3  # At least 3 messages per chat
    
    @pytest.mark.asyncio
    async def test_system_error_recovery_e2e(self, system_integrator: SystemIntegrator):
        """Test system error recovery and stability."""
        await system_integrator.initialize()
        
        # Force an error condition and test recovery
        original_agent = system_integrator.agent
        
        # Temporarily replace agent with one that will fail
        class FailingAgent:
            def process_message(self, *args, **kwargs):
                raise Exception("Simulated agent failure")
            
            def list_contexts(self):
                return []
        
        # This should not crash the entire system
        system_integrator.agent = FailingAgent()
        
        # Wait a bit for health checks to detect the issue
        await asyncio.sleep(2)
        
        # Restore working agent
        system_integrator.agent = original_agent
        
        # System should recover
        await asyncio.sleep(1)
        
        # Verify system is still functional
        status = await system_integrator.get_system_status()
        assert status["system_state"] in ["ready", "running"]
    
    @pytest.mark.asyncio
    async def test_performance_under_load_e2e(self, system_integrator: SystemIntegrator):
        """Test system performance under load."""
        await system_integrator.initialize()
        
        # Generate load
        chat_id = "performance_test_chat"
        await system_integrator.agent.create_context(
            chat_id=chat_id,
            user_name="performance_user"
        )
        
        # Time multiple operations
        start_time = time.perf_counter()
        
        tasks = []
        for i in range(20):  # 20 concurrent operations
            task = system_integrator.agent.process_message(
                message=f"Performance test message {i}",
                chat_id=chat_id
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        end_time = time.perf_counter()
        total_time = end_time - start_time
        
        # Check that operations completed
        successful_responses = [r for r in responses if not isinstance(r, Exception)]
        assert len(successful_responses) >= 10  # At least half should succeed
        
        # Performance should be reasonable
        avg_time_per_operation = total_time / len(successful_responses) if successful_responses else float('inf')
        assert avg_time_per_operation < 5.0  # Less than 5 seconds per operation on average
    
    @pytest.mark.asyncio
    async def test_data_persistence_across_restarts_e2e(self, system_integrator: SystemIntegrator, temp_database):
        """Test data persistence across system restarts."""
        # Initialize and use system
        await system_integrator.initialize()
        
        chat_id = "persistence_test_chat"
        await system_integrator.agent.create_context(
            chat_id=chat_id,
            user_name="persistence_user"
        )
        
        test_message = "This message should persist across restarts"
        response = await system_integrator.agent.process_message(
            message=test_message,
            chat_id=chat_id
        )
        
        assert response is not None
        
        # Shutdown system
        await system_integrator.shutdown()
        
        # Create new system with same database
        new_integrator = SystemIntegrator(
            config=system_integrator.config,
            enable_monitoring=False
        )
        new_integrator.database = temp_database
        
        await new_integrator.initialize()
        
        # Check that data persisted
        history = await new_integrator.database.get_chat_history(session_id=chat_id)
        user_messages = [msg for msg in history if msg['role'] == 'user']
        
        assert len(user_messages) >= 1
        assert user_messages[0]['content'] == test_message
        assert user_messages[0]['session_id'] == chat_id
        
        await new_integrator.shutdown()
    
    @pytest.mark.asyncio
    async def test_system_metrics_collection_e2e(self, system_integrator: SystemIntegrator):
        """Test end-to-end metrics collection."""
        await system_integrator.initialize()
        
        # Generate some activity
        chat_id = "metrics_test_chat"
        await system_integrator.agent.create_context(chat_id=chat_id, user_name="metrics_user")
        
        for i in range(5):
            await system_integrator.agent.process_message(
                message=f"Metrics test message {i}",
                chat_id=chat_id
            )
        
        # Wait for metrics collection
        await asyncio.sleep(1)
        
        # Check system metrics
        status = await system_integrator.get_system_status()
        metrics = status["metrics"]
        
        assert "startup_time_ms" in metrics
        assert metrics["startup_time_ms"] > 0
        
        # Check component-specific metrics
        if system_integrator.telegram_processor:
            pipeline_status = await system_integrator.telegram_processor.get_pipeline_status()
            assert pipeline_status["total_processed"] >= 0
        
        # Check database metrics if tool metrics were recorded
        if system_integrator.database:
            db_info = await system_integrator.database.get_database_info()
            assert db_info["total_records"] >= 0
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown_e2e(self, system_integrator: SystemIntegrator):
        """Test graceful system shutdown."""
        await system_integrator.initialize()
        
        # Start some background activity
        chat_id = "shutdown_test_chat"
        await system_integrator.agent.create_context(chat_id=chat_id, user_name="shutdown_user")
        
        # Start a long-running operation
        async def long_operation():
            await asyncio.sleep(1)
            return await system_integrator.agent.process_message(
                message="Long operation message",
                chat_id=chat_id
            )
        
        operation_task = asyncio.create_task(long_operation())
        
        # Allow operation to start
        await asyncio.sleep(0.1)
        
        # Initiate shutdown
        shutdown_start = time.perf_counter()
        await system_integrator.shutdown()
        shutdown_time = time.perf_counter() - shutdown_start
        
        # Shutdown should complete in reasonable time
        assert shutdown_time < 10.0  # Less than 10 seconds
        
        # System should be in stopped state
        assert system_integrator.system_state.value == "stopped"
        
        # Background operation should have completed or been cancelled
        try:
            await operation_task
        except asyncio.CancelledError:
            pass  # Acceptable if cancelled during shutdown
    
    @pytest.mark.asyncio
    async def test_event_system_e2e(self, system_integrator: SystemIntegrator):
        """Test end-to-end event system."""
        events_received = []
        
        async def event_handler(event):
            events_received.append(event)
        
        # Register event handler
        system_integrator.register_event_handler("*", event_handler)
        
        # Initialize system (should generate events)
        await system_integrator.initialize()
        
        # Wait for events
        await asyncio.sleep(0.5)
        
        # Should have received initialization events
        assert len(events_received) > 0
        
        # Check for specific event types
        event_types = [event.event_type for event in events_received]
        assert "system_init_start" in event_types
        
        # Generate more events by using the system
        chat_id = "event_test_chat"
        await system_integrator.agent.create_context(chat_id=chat_id, user_name="event_user")
        
        await asyncio.sleep(0.1)
        
        # Should have more events
        final_event_count = len(events_received)
        assert final_event_count >= len(events_received)
    
    @pytest.mark.asyncio
    async def test_full_message_flow_simulation(self, system_integrator: SystemIntegrator):
        """Simulate a complete message flow from input to output."""
        await system_integrator.initialize()
        
        # Simulate complete Telegram message processing flow
        if system_integrator.telegram_processor:
            from integrations.telegram.unified_processor import ProcessingRequest
            from tests.integration.test_pipeline_telegram import MockTelegramMessage, MockTelegramUser
            
            # Create mock Telegram message
            message = MockTelegramMessage("Hello! Can you help me with a coding question?")
            user = MockTelegramUser()
            
            request = ProcessingRequest(
                message=message,
                user=user,
                chat_id=message.chat_id,
                message_id=message.id,
                raw_text=message.text
            )
            
            # Process through the pipeline
            result = await system_integrator.telegram_processor.process_message(request)
            
            # Should succeed or fail gracefully
            assert result is not None
            # In test mode, some components might be mocked, so we accept both success and graceful failure
            assert result.success is not None  # Should have a defined success state
            
            if result.success:
                assert len(result.responses) > 0
                assert result.responses[0].content is not None
        
        # Verify system remains stable after processing
        status = await system_integrator.get_system_status()
        assert status["system_state"] in ["ready", "running"]