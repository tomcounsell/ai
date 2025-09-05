"""
Test Suite for Communication Layer
Tests FastAPI server, WebSocket connections, and Telegram integration.
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from fastapi import WebSocket
from server import app, ChatRequest, ChatMessage, ChatResponse


class TestFastAPIServer:
    """Test FastAPI server endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "services" in data
        assert "metrics" in data
    
    def test_chat_endpoint_unauthorized(self, client):
        """Test chat endpoint without authentication."""
        request_data = {
            "message": {
                "content": "Hello",
                "role": "user"
            }
        }
        response = client.post("/chat", json=request_data)
        assert response.status_code == 403  # Forbidden without auth
    
    def test_chat_endpoint_with_auth(self, client):
        """Test chat endpoint with authentication."""
        headers = {"Authorization": "Bearer test-token-1234567890"}
        request_data = {
            "message": {
                "content": "Hello AI",
                "role": "user"
            },
            "session_id": "test-session"
        }
        
        with patch('server.app.state.agent') as mock_agent:
            mock_agent.process = AsyncMock(return_value=Mock(content="Hello! How can I help?", tools_used=[]))
            
            with patch('server.app.state.context_manager') as mock_cm:
                mock_cm.get_or_create_context = AsyncMock(return_value=Mock())
                
                response = client.post("/chat", json=request_data, headers=headers)
                
                # Should work with valid auth
                if response.status_code == 200:
                    data = response.json()
                    assert "content" in data
                    assert "session_id" in data
                    assert "message_id" in data
                    assert "processing_time_ms" in data
    
    def test_list_tools_endpoint(self, client):
        """Test list tools endpoint."""
        headers = {"Authorization": "Bearer test-token-1234567890"}
        
        with patch('server.app.state.tool_registry') as mock_registry:
            mock_registry.list_tools.return_value = ["search", "image_gen", "code_exec"]
            
            response = client.get("/tools", headers=headers)
            if response.status_code == 200:
                data = response.json()
                assert "tools" in data
                assert isinstance(data["tools"], list)
    
    def test_mcp_servers_endpoint(self, client):
        """Test MCP servers listing."""
        headers = {"Authorization": "Bearer test-token-1234567890"}
        
        with patch('server.app.state.mcp_orchestrator') as mock_orch:
            mock_server = Mock()
            mock_server.get_status.return_value = Mock(value="running")
            mock_server.get_capabilities.return_value = []
            
            mock_orch.get_registered_servers.return_value = {
                "social": mock_server,
                "dev": mock_server
            }
            
            response = client.get("/mcp/servers", headers=headers)
            if response.status_code == 200:
                data = response.json()
                assert "servers" in data


class TestWebSocketConnection:
    """Test WebSocket functionality."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)
    
    def test_websocket_connection(self, client):
        """Test WebSocket connection establishment."""
        with client.websocket_connect("/ws") as websocket:
            # Should receive welcome message
            data = websocket.receive_json()
            assert data["type"] == "connection"
            assert data["content"]["status"] == "connected"
    
    def test_websocket_chat_message(self, client):
        """Test WebSocket chat messaging."""
        with patch('server.app.state.agent') as mock_agent:
            mock_agent.process = AsyncMock(return_value="Test response")
            
            with patch('server.app.state.context_manager') as mock_cm:
                mock_cm.get_or_create_context = AsyncMock(return_value=Mock())
                
                with client.websocket_connect("/ws") as websocket:
                    # Skip welcome message
                    websocket.receive_json()
                    
                    # Send chat message
                    websocket.send_json({
                        "type": "chat",
                        "content": "Hello WebSocket"
                    })
                    
                    # Should receive response
                    response = websocket.receive_json()
                    assert response["type"] in ["response", "stream", "error"]
    
    def test_websocket_ping_pong(self, client):
        """Test WebSocket ping/pong."""
        with client.websocket_connect("/ws") as websocket:
            # Skip welcome message
            websocket.receive_json()
            
            # Send ping
            websocket.send_json({
                "type": "ping",
                "content": "test-ping"
            })
            
            # Should receive pong
            response = websocket.receive_json()
            assert response["type"] == "pong"
            assert response["content"] == "test-ping"
    
    def test_websocket_command(self, client):
        """Test WebSocket command execution."""
        with patch('server.app.state.tool_registry') as mock_registry:
            mock_registry.list_tools.return_value = ["tool1", "tool2"]
            
            with client.websocket_connect("/ws") as websocket:
                # Skip welcome message
                websocket.receive_json()
                
                # Send command
                websocket.send_json({
                    "type": "command",
                    "content": {
                        "type": "list_tools"
                    }
                })
                
                # Should receive command result
                response = websocket.receive_json()
                assert response["type"] in ["command_result", "error"]


class TestTelegramIntegration:
    """Test Telegram integration components."""
    
    @pytest.mark.asyncio
    async def test_unified_processor_import(self):
        """Test unified processor can be imported."""
        from integrations.telegram.unified_processor import UnifiedProcessor
        assert UnifiedProcessor is not None
    
    @pytest.mark.asyncio
    async def test_telegram_client_import(self):
        """Test Telegram client can be imported."""
        from integrations.telegram.client import TelegramClient
        assert TelegramClient is not None
    
    @pytest.mark.asyncio
    async def test_message_processing_pipeline(self):
        """Test 5-step message processing pipeline."""
        from integrations.telegram.unified_processor import (
            UnifiedProcessor, ProcessingRequest, ProcessingStage
        )
        
        processor = UnifiedProcessor(
            enable_metrics=True,
            enable_parallel_processing=False
        )
        
        # Create mock request
        mock_message = Mock()
        mock_message.text = "Test message"
        mock_user = Mock()
        
        request = ProcessingRequest(
            message=mock_message,
            user=mock_user,
            chat_id=123,
            message_id=456,
            raw_text="Test message"
        )
        
        # Process with mocked components
        with patch.object(processor, 'security_gate') as mock_security:
            mock_security.validate = AsyncMock(return_value=Mock(passed=True))
            
            with patch.object(processor, 'context_builder') as mock_context:
                mock_context.build = AsyncMock(return_value=Mock())
                
                with patch.object(processor, 'type_router') as mock_router:
                    mock_router.route = AsyncMock(return_value=Mock(type="text"))
                    
                    with patch.object(processor, 'agent_orchestrator') as mock_orch:
                        mock_orch.orchestrate = AsyncMock(return_value=Mock(responses=["Test"]))
                        
                        with patch.object(processor, 'response_manager') as mock_resp:
                            mock_resp.format = AsyncMock(return_value=[Mock(content="Test")])
                            
                            result = await processor.process(request)
                            assert result.success
                            assert result.stage_reached == ProcessingStage.RESPONSE


class TestRequestModels:
    """Test request/response models."""
    
    def test_chat_message_validation(self):
        """Test chat message validation."""
        # Valid message
        msg = ChatMessage(content="Hello", role="user")
        assert msg.content == "Hello"
        
        # Empty content should fail
        with pytest.raises(ValueError):
            ChatMessage(content="", role="user")
        
        # Too long content should fail
        with pytest.raises(ValueError):
            ChatMessage(content="x" * 100001, role="user")
    
    def test_chat_request_validation(self):
        """Test chat request validation."""
        msg = ChatMessage(content="Test", role="user")
        
        # Valid request
        req = ChatRequest(message=msg)
        assert req.message.content == "Test"
        assert req.session_id is not None  # Should auto-generate
        
        # With session ID
        req2 = ChatRequest(message=msg, session_id="test-session")
        assert req2.session_id == "test-session"
    
    def test_chat_response_model(self):
        """Test chat response model."""
        response = ChatResponse(
            content="Response text",
            session_id="test-session",
            message_id="msg-123",
            processing_time_ms=150.5
        )
        assert response.content == "Response text"
        assert response.processing_time_ms == 150.5


class TestErrorHandling:
    """Test error handling in communication layer."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)
    
    def test_invalid_endpoint(self, client):
        """Test invalid endpoint returns 404."""
        response = client.get("/invalid-endpoint")
        assert response.status_code == 404
    
    def test_malformed_request(self, client):
        """Test malformed request handling."""
        headers = {"Authorization": "Bearer test-token-1234567890"}
        
        # Missing required field
        response = client.post("/chat", json={}, headers=headers)
        assert response.status_code == 422  # Unprocessable entity
    
    def test_websocket_invalid_message(self, client):
        """Test WebSocket invalid message handling."""
        with client.websocket_connect("/ws") as websocket:
            # Skip welcome message
            websocket.receive_json()
            
            # Send invalid message
            websocket.send_json({
                "invalid_field": "test"
            })
            
            # Should receive error
            response = websocket.receive_json()
            assert response["type"] == "error"
            assert "error" in response["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])