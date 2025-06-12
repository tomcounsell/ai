"""
Tests for AgentOrchestrator component.

Tests agent selection, intent classification, tool management, and error handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from integrations.telegram.components.agent_orchestrator import AgentOrchestrator
from integrations.telegram.models import (
    MessageContext,
    ProcessingPlan,
    AgentResponse,
    MessageType,
    ProcessingPriority,
    MediaAttachment
)


@pytest.fixture
def mock_valor_agent():
    """Create mock Valor agent."""
    agent = AsyncMock()
    agent.arun.return_value = "Test response"
    return agent


@pytest.fixture
def orchestrator(mock_valor_agent):
    """Create AgentOrchestrator instance."""
    return AgentOrchestrator(valor_agent=mock_valor_agent)


@pytest.fixture
def basic_context():
    """Create basic message context."""
    message = MagicMock()
    message.message_id = 123
    message.text = "Hello, how are you?"
    message.from_user = MagicMock(username="testuser")
    message.date = datetime.now()
    
    return MessageContext(
        message=message,
        chat_id=12345,
        username="testuser",
        workspace="test_workspace",
        working_directory="/test/dir",
        is_dev_group=False,
        is_mention=False,
        cleaned_text="Hello, how are you?",
        timestamp=datetime.now()
    )


@pytest.fixture
def text_plan():
    """Create text processing plan."""
    return ProcessingPlan(
        message_type=MessageType.TEXT,
        priority=ProcessingPriority.MEDIUM,
        needs_intent_classification=True,
        processing_strategy="agent",
        handler_name="valor_agent",
        metadata={}
    )


class TestAgentOrchestrator:
    """Test suite for AgentOrchestrator."""

    @pytest.mark.asyncio
    async def test_basic_agent_processing(self, orchestrator, basic_context, text_plan, mock_valor_agent):
        """Test basic agent processing with text message."""
        # Process with agent
        response = await orchestrator.process_with_agent(basic_context, text_plan)
        
        # Verify response
        assert response.success is True
        assert response.content == "Test response"
        assert response.agent_name == "valor_agent"
        assert response.message_type == MessageType.TEXT
        assert response.processing_time > 0
        
        # Verify agent was called
        mock_valor_agent.arun.assert_called_once()

    @pytest.mark.asyncio
    async def test_intent_classification(self, orchestrator, basic_context, text_plan):
        """Test intent classification integration."""
        # Mock intent classifier
        with patch.object(orchestrator, '_classify_intent', return_value="general_conversation"):
            response = await orchestrator.process_with_agent(basic_context, text_plan)
            
            assert response.success is True
            assert response.metadata.get("intent") == "general_conversation"

    @pytest.mark.asyncio
    async def test_tool_configuration(self, orchestrator, basic_context, mock_valor_agent):
        """Test tool enabling/disabling based on context."""
        # Create coding task plan
        coding_plan = ProcessingPlan(
            message_type=MessageType.TEXT,
            priority=ProcessingPriority.HIGH,
            needs_intent_classification=True,
            processing_strategy="agent",
            handler_name="valor_agent",
            metadata={"detected_patterns": ["code_block"]}
        )
        
        # Mock tool configuration
        with patch.object(orchestrator, '_configure_tools_for_context') as mock_configure:
            await orchestrator.process_with_agent(basic_context, coding_plan)
            
            # Verify tools were configured
            mock_configure.assert_called_once_with(basic_context, coding_plan)

    @pytest.mark.asyncio
    async def test_streaming_response(self, orchestrator, basic_context, text_plan):
        """Test streaming response handling."""
        # Mock streaming agent response
        async def mock_stream():
            yield "Part 1"
            yield "Part 2"
            yield "Part 3"
        
        orchestrator.valor_agent.astream = mock_stream
        
        # Process with streaming
        response = await orchestrator.process_with_agent(basic_context, text_plan)
        
        assert response.success is True
        assert response.content == "Part 1Part 2Part 3"
        assert response.metadata.get("streamed") is True

    @pytest.mark.asyncio
    async def test_media_handling(self, orchestrator, basic_context):
        """Test media attachment handling."""
        # Create photo message plan
        photo_plan = ProcessingPlan(
            message_type=MessageType.PHOTO,
            priority=ProcessingPriority.MEDIUM,
            needs_intent_classification=False,
            processing_strategy="agent",
            handler_name="valor_agent",
            metadata={"media_info": {"type": "photo", "file_id": "photo123"}}
        )
        
        # Mock agent response with image generation
        orchestrator.valor_agent.arun.return_value = "Here's the analyzed image"
        
        response = await orchestrator.process_with_agent(basic_context, photo_plan)
        
        assert response.success is True
        assert response.message_type == MessageType.PHOTO
        assert "analyzed image" in response.content

    @pytest.mark.asyncio
    async def test_voice_message_handling(self, orchestrator, basic_context):
        """Test voice message processing."""
        # Create voice message plan
        voice_plan = ProcessingPlan(
            message_type=MessageType.VOICE,
            priority=ProcessingPriority.HIGH,
            needs_intent_classification=False,
            processing_strategy="agent",
            handler_name="valor_agent",
            metadata={"transcription": "Hello, this is a voice message"}
        )
        
        # Update context with transcription
        basic_context.cleaned_text = "Hello, this is a voice message"
        
        response = await orchestrator.process_with_agent(basic_context, voice_plan)
        
        assert response.success is True
        assert response.message_type == MessageType.VOICE
        assert response.metadata.get("had_transcription") is True

    @pytest.mark.asyncio
    async def test_command_handling(self, orchestrator, basic_context):
        """Test command message handling."""
        # Create command plan
        command_plan = ProcessingPlan(
            message_type=MessageType.COMMAND,
            priority=ProcessingPriority.HIGH,
            needs_intent_classification=False,
            processing_strategy="command",
            handler_name="help_command",
            metadata={"command": "/help"}
        )
        
        # Mock command handler
        with patch.object(orchestrator, '_handle_command', return_value="Help text"):
            response = await orchestrator.process_with_agent(basic_context, command_plan)
            
            assert response.success is True
            assert response.content == "Help text"
            assert response.message_type == MessageType.COMMAND

    @pytest.mark.asyncio
    async def test_error_handling(self, orchestrator, basic_context, text_plan, mock_valor_agent):
        """Test error handling during agent processing."""
        # Mock agent error
        mock_valor_agent.arun.side_effect = Exception("Agent processing failed")
        
        response = await orchestrator.process_with_agent(basic_context, text_plan)
        
        assert response.success is False
        assert response.error == "Agent processing failed"
        assert response.content == ""

    @pytest.mark.asyncio
    async def test_context_preparation(self, orchestrator, basic_context, text_plan):
        """Test context preparation for agent."""
        # Mock context preparation
        with patch.object(orchestrator, '_prepare_agent_context') as mock_prepare:
            mock_prepare.return_value = {
                "chat_id": basic_context.chat_id,
                "username": basic_context.username,
                "message": basic_context.cleaned_text,
                "workspace": basic_context.workspace
            }
            
            await orchestrator.process_with_agent(basic_context, text_plan)
            
            # Verify context was prepared
            mock_prepare.assert_called_once_with(basic_context, text_plan)

    @pytest.mark.asyncio
    async def test_reaction_suggestion(self, orchestrator, basic_context, text_plan):
        """Test reaction suggestion based on response."""
        # Mock agent response with emotion
        orchestrator.valor_agent.arun.return_value = "That's hilarious! 😂"
        
        response = await orchestrator.process_with_agent(basic_context, text_plan)
        
        assert response.success is True
        assert len(response.reactions) > 0
        assert "😂" in response.reactions or "😁" in response.reactions

    @pytest.mark.asyncio
    async def test_multiple_agent_support(self, orchestrator, basic_context):
        """Test support for multiple agents."""
        # Create plan for different agent
        technical_plan = ProcessingPlan(
            message_type=MessageType.TEXT,
            priority=ProcessingPriority.HIGH,
            needs_intent_classification=False,
            processing_strategy="agent",
            handler_name="technical_agent",
            metadata={"intent": "technical_support"}
        )
        
        # Mock technical agent
        mock_technical_agent = AsyncMock()
        mock_technical_agent.arun.return_value = "Technical response"
        
        with patch.object(orchestrator, '_get_agent', return_value=mock_technical_agent):
            response = await orchestrator.process_with_agent(basic_context, technical_plan)
            
            assert response.success is True
            assert response.content == "Technical response"
            assert response.agent_name == "technical_agent"

    @pytest.mark.asyncio
    async def test_token_usage_tracking(self, orchestrator, basic_context, text_plan):
        """Test token usage tracking."""
        # Mock agent with token usage
        orchestrator.valor_agent.arun.return_value = "Response with token tracking"
        
        with patch.object(orchestrator, '_get_token_usage', return_value=150):
            response = await orchestrator.process_with_agent(basic_context, text_plan)
            
            assert response.success is True
            assert response.tokens_used == 150

    @pytest.mark.asyncio
    async def test_parallel_tool_execution(self, orchestrator, basic_context):
        """Test parallel tool execution for efficiency."""
        # Create plan requiring multiple tools
        multi_tool_plan = ProcessingPlan(
            message_type=MessageType.TEXT,
            priority=ProcessingPriority.HIGH,
            needs_intent_classification=True,
            processing_strategy="agent",
            handler_name="valor_agent",
            metadata={"requires_tools": ["search", "image_generation"]}
        )
        
        # Mock parallel tool execution
        with patch.object(orchestrator, '_execute_tools_parallel') as mock_parallel:
            mock_parallel.return_value = {
                "search_results": "Found information",
                "generated_image": "image_url.png"
            }
            
            response = await orchestrator.process_with_agent(basic_context, multi_tool_plan)
            
            assert response.success is True
            assert response.has_media is True
            assert len(response.media_attachments) > 0

    @pytest.mark.asyncio
    async def test_workspace_specific_behavior(self, orchestrator, basic_context, text_plan):
        """Test workspace-specific agent behavior."""
        # Create contexts for different workspaces
        workspace1_context = basic_context
        workspace1_context.workspace = "personal"
        
        workspace2_context = basic_context
        workspace2_context.workspace = "work" 
        
        # Process same message in different workspaces
        response1 = await orchestrator.process_with_agent(workspace1_context, text_plan)
        response2 = await orchestrator.process_with_agent(workspace2_context, text_plan)
        
        # Responses should be contextual to workspace
        assert response1.success is True
        assert response2.success is True
        # In real implementation, responses would differ based on workspace context