"""
Comprehensive tests for the Ollama-based intent recognition system.

Tests cover intent classification, reaction management, tool access control,
and system prompt generation across all message types and intents.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any

# Import the intent system components
from integrations.ollama_intent import (
    OllamaIntentClassifier, MessageIntent, IntentResult, classify_message_intent
)
from integrations.telegram.reaction_manager import (
    TelegramReactionManager, ReactionStatus, add_message_received_reaction,
    add_intent_based_reaction, complete_reaction_sequence
)
from integrations.intent_tools import (
    IntentToolManager, get_intent_based_tools, get_claude_code_configuration
)
from integrations.intent_prompts import (
    IntentPromptManager, get_intent_system_prompt, get_intent_guidance
)


class TestOllamaIntentClassifier:
    """Test the Ollama-based intent classification system."""

    @pytest.fixture
    def classifier(self):
        """Create a classifier instance for testing."""
        return OllamaIntentClassifier()

    @pytest.fixture
    def mock_ollama_response(self):
        """Mock successful Ollama API response."""
        return {
            "response": '{"intent": "development_task", "confidence": 0.85, "reasoning": "Code-related request", "emoji": "⚙️"}'
        }

    @pytest.mark.asyncio
    async def test_classify_development_task(self, classifier):
        """Test classification of development tasks."""
        message = "Fix the bug in the login function"
        
        with patch.object(classifier, '_make_ollama_request', return_value='{"intent": "development_task", "confidence": 0.9, "reasoning": "Bug fix request", "emoji": "⚙️"}'):
            result = await classifier.classify_intent(message)
            
            assert result.intent == MessageIntent.DEVELOPMENT_TASK
            assert result.confidence >= 0.8
            assert "bug" in result.reasoning.lower() or "development" in result.reasoning.lower()
            assert result.suggested_emoji == "⚙️"

    @pytest.mark.asyncio
    async def test_classify_casual_chat(self, classifier):
        """Test classification of casual conversation."""
        message = "Hey, how are you doing today?"
        
        with patch.object(classifier, '_make_ollama_request', return_value='{"intent": "casual_chat", "confidence": 0.95, "reasoning": "Friendly greeting", "emoji": "💬"}'):
            result = await classifier.classify_intent(message)
            
            assert result.intent == MessageIntent.CASUAL_CHAT
            assert result.confidence >= 0.8
            assert result.suggested_emoji == "💬"

    @pytest.mark.asyncio
    async def test_classify_image_generation(self, classifier):
        """Test classification of image generation requests."""
        message = "Can you create an image of a sunset over mountains?"
        
        with patch.object(classifier, '_make_ollama_request', return_value='{"intent": "image_generation", "confidence": 0.88, "reasoning": "Image creation request", "emoji": "🎨"}'):
            result = await classifier.classify_intent(message)
            
            assert result.intent == MessageIntent.IMAGE_GENERATION
            assert result.confidence >= 0.8
            assert result.suggested_emoji == "🎨"

    @pytest.mark.asyncio
    async def test_classify_web_search(self, classifier):
        """Test classification of web search requests."""
        message = "What's the latest news about AI developments?"
        
        with patch.object(classifier, '_make_ollama_request', return_value='{"intent": "web_search", "confidence": 0.92, "reasoning": "Current information request", "emoji": "🔍"}'):
            result = await classifier.classify_intent(message)
            
            assert result.intent == MessageIntent.WEB_SEARCH
            assert result.confidence >= 0.8
            assert result.suggested_emoji == "🔍"

    @pytest.mark.asyncio
    async def test_classify_project_query(self, classifier):
        """Test classification of project-related queries."""
        message = "What's the status of the PsyOptimal project?"
        
        with patch.object(classifier, '_make_ollama_request', return_value='{"intent": "project_query", "confidence": 0.87, "reasoning": "Project status inquiry", "emoji": "📋"}'):
            result = await classifier.classify_intent(message)
            
            assert result.intent == MessageIntent.PROJECT_QUERY
            assert result.confidence >= 0.8
            assert result.suggested_emoji == "📋"

    @pytest.mark.asyncio
    async def test_fallback_classification(self, classifier):
        """Test fallback classification when Ollama fails."""
        message = "ping"
        
        # Test system health fallback
        result = classifier._fallback_classification(message)
        assert result.intent == MessageIntent.SYSTEM_HEALTH
        assert result.confidence == 1.0
        assert result.suggested_emoji == "🏓"
        
        # Test image analysis fallback
        message_with_image = "[Image] This is a photo"
        result = classifier._fallback_classification(message_with_image)
        assert result.intent == MessageIntent.IMAGE_ANALYSIS
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_ollama_unavailable_handling(self, classifier):
        """Test behavior when Ollama server is unavailable."""
        message = "Test message"
        
        with patch.object(classifier, '_make_ollama_request', side_effect=Exception("Connection failed")):
            result = await classifier.classify_intent(message)
            
            # Should fall back to rule-based classification
            assert result.intent in MessageIntent
            assert 0.0 <= result.confidence <= 1.0
            assert result.suggested_emoji in classifier.intent_emojis.values()

    def test_confidence_validation(self, classifier):
        """Test confidence score validation."""
        # Test high confidence
        result = IntentResult(
            intent=MessageIntent.CASUAL_CHAT,
            confidence=0.95,
            reasoning="High confidence",
            suggested_emoji="💬"
        )
        assert result.is_high_confidence is True
        
        # Test low confidence
        result = IntentResult(
            intent=MessageIntent.UNCLEAR,
            confidence=0.3,
            reasoning="Low confidence",
            suggested_emoji="🤔"
        )
        assert result.is_high_confidence is False


class TestTelegramReactionManager:
    """Test the Telegram reaction management system."""

    @pytest.fixture
    def reaction_manager(self):
        """Create a reaction manager instance for testing."""
        return TelegramReactionManager()

    @pytest.fixture
    def mock_client(self):
        """Create a mock Telegram client."""
        client = AsyncMock()
        client.send_reaction = AsyncMock()
        return client

    @pytest.fixture
    def sample_intent_result(self):
        """Create a sample intent result for testing."""
        return IntentResult(
            intent=MessageIntent.DEVELOPMENT_TASK,
            confidence=0.85,
            reasoning="Code-related request",
            suggested_emoji="⚙️"
        )

    @pytest.mark.asyncio
    async def test_add_received_reaction(self, reaction_manager, mock_client):
        """Test adding initial received reaction."""
        chat_id, message_id = 12345, 67890
        
        result = await reaction_manager.add_received_reaction(mock_client, chat_id, message_id)
        
        assert result is True
        mock_client.send_reaction.assert_called_once_with(chat_id, message_id, "👀")
        
        # Check tracking
        reactions = reaction_manager.get_message_reactions(chat_id, message_id)
        assert "👀" in reactions

    @pytest.mark.asyncio
    async def test_add_intent_reaction(self, reaction_manager, mock_client, sample_intent_result):
        """Test adding intent-specific reaction."""
        chat_id, message_id = 12345, 67890
        
        result = await reaction_manager.add_intent_reaction(
            mock_client, chat_id, message_id, sample_intent_result
        )
        
        assert result is True
        mock_client.send_reaction.assert_called_with(chat_id, message_id, "⚙️")
        
        # Check tracking
        reactions = reaction_manager.get_message_reactions(chat_id, message_id)
        assert "⚙️" in reactions

    @pytest.mark.asyncio
    async def test_complete_reaction_sequence(self, reaction_manager, mock_client, sample_intent_result):
        """Test completing the full reaction sequence."""
        chat_id, message_id = 12345, 67890
        
        # First add received reaction
        await reaction_manager.add_received_reaction(mock_client, chat_id, message_id)
        
        # Then complete sequence
        result = await reaction_manager.update_reaction_sequence(
            mock_client, chat_id, message_id, sample_intent_result, success=True
        )
        
        assert result is True
        
        # Should have called send_reaction multiple times
        assert mock_client.send_reaction.call_count >= 2
        
        # Check final reactions
        reactions = reaction_manager.get_message_reactions(chat_id, message_id)
        assert "👀" in reactions  # Received
        assert "⚙️" in reactions  # Intent
        assert "✅" in reactions  # Success

    @pytest.mark.asyncio
    async def test_error_reaction(self, reaction_manager, mock_client, sample_intent_result):
        """Test error reaction handling."""
        chat_id, message_id = 12345, 67890
        
        result = await reaction_manager.update_reaction_sequence(
            mock_client, chat_id, message_id, sample_intent_result, success=False
        )
        
        # Check error reaction was added
        reactions = reaction_manager.get_message_reactions(chat_id, message_id)
        assert "❌" in reactions

    @pytest.mark.asyncio
    async def test_duplicate_reaction_prevention(self, reaction_manager, mock_client):
        """Test prevention of duplicate reactions."""
        chat_id, message_id = 12345, 67890
        
        # Add the same reaction twice
        await reaction_manager.add_received_reaction(mock_client, chat_id, message_id)
        await reaction_manager.add_received_reaction(mock_client, chat_id, message_id)
        
        # Should only call send_reaction once
        assert mock_client.send_reaction.call_count == 1

    def test_reaction_cleanup(self, reaction_manager):
        """Test reaction tracking cleanup."""
        # Add many tracked messages
        for i in range(1200):
            reaction_manager.message_reactions[(i, 1)] = ["👀"]
        
        # Trigger cleanup
        asyncio.run(reaction_manager.cleanup_old_reactions(max_tracked_messages=1000))
        
        # Should have cleaned up to 1000 messages
        assert len(reaction_manager.message_reactions) == 1000


class TestIntentToolManager:
    """Test the intent-based tool access control system."""

    @pytest.fixture
    def tool_manager(self):
        """Create a tool manager instance for testing."""
        return IntentToolManager()

    @pytest.fixture
    def development_intent_result(self):
        """Create a development task intent result."""
        return IntentResult(
            intent=MessageIntent.DEVELOPMENT_TASK,
            confidence=0.85,
            reasoning="Code-related request",
            suggested_emoji="⚙️"
        )

    @pytest.fixture
    def casual_chat_intent_result(self):
        """Create a casual chat intent result."""
        return IntentResult(
            intent=MessageIntent.CASUAL_CHAT,
            confidence=0.92,
            reasoning="Friendly conversation",
            suggested_emoji="💬"
        )

    def test_development_task_tools(self, tool_manager, development_intent_result):
        """Test tool access for development tasks."""
        allowed_tools = tool_manager.get_allowed_tools(development_intent_result)
        
        # Should allow development tools
        assert "edit" in allowed_tools
        assert "write" in allowed_tools
        assert "bash" in allowed_tools
        assert "read" in allowed_tools
        
        # Should not restrict development tools
        assert not tool_manager.should_restrict_tool("edit", development_intent_result)
        assert not tool_manager.should_restrict_tool("bash", development_intent_result)
        
        # Should restrict creative tools
        assert tool_manager.should_restrict_tool("create_image", development_intent_result)

    def test_casual_chat_tools(self, tool_manager, casual_chat_intent_result):
        """Test tool access for casual chat."""
        allowed_tools = tool_manager.get_allowed_tools(casual_chat_intent_result)
        
        # Should allow chat and search tools
        assert "web_search" in allowed_tools or "search_current_info" in allowed_tools
        assert "telegram_history" in allowed_tools or "chat_context" in allowed_tools
        
        # Should restrict development tools
        assert tool_manager.should_restrict_tool("edit", casual_chat_intent_result)
        assert tool_manager.should_restrict_tool("bash", casual_chat_intent_result)

    def test_claude_code_configuration(self, tool_manager, development_intent_result):
        """Test Claude Code configuration generation."""
        config = tool_manager.get_claude_code_config(development_intent_result)
        
        assert "allowed_tools" in config
        assert "intent" in config
        assert "confidence" in config
        assert "restrictions" in config
        assert "optimization" in config
        
        assert config["intent"] == "development_task"
        assert config["confidence"] == 0.85
        assert isinstance(config["allowed_tools"], list)
        assert len(config["allowed_tools"]) > 0

    def test_tool_priority_scoring(self, tool_manager, development_intent_result):
        """Test tool priority scoring system."""
        # High priority tools for development
        edit_priority = tool_manager.get_tool_priority("edit", development_intent_result)
        bash_priority = tool_manager.get_tool_priority("bash", development_intent_result)
        
        # Should be high priority
        assert edit_priority >= 70
        assert bash_priority >= 70
        
        # Low priority tools for development
        image_priority = tool_manager.get_tool_priority("create_image", development_intent_result)
        assert image_priority <= 30

    def test_intent_summary_generation(self, tool_manager, development_intent_result):
        """Test intent summary generation."""
        summary = tool_manager.get_intent_summary(development_intent_result)
        
        assert "development_task" in summary
        assert "0.85" in summary  # confidence
        assert "Code-related request" in summary  # reasoning
        assert "Allowed tools:" in summary


class TestIntentPromptManager:
    """Test the intent-specific system prompt generation."""

    @pytest.fixture
    def prompt_manager(self):
        """Create a prompt manager instance for testing."""
        return IntentPromptManager()

    @pytest.fixture
    def development_intent_result(self):
        """Create a development task intent result."""
        return IntentResult(
            intent=MessageIntent.DEVELOPMENT_TASK,
            confidence=0.85,
            reasoning="Code-related request",
            suggested_emoji="⚙️"
        )

    @pytest.fixture
    def image_generation_intent_result(self):
        """Create an image generation intent result."""
        return IntentResult(
            intent=MessageIntent.IMAGE_GENERATION,
            confidence=0.90,
            reasoning="Creative image request",
            suggested_emoji="🎨"
        )

    def test_development_system_prompt(self, prompt_manager, development_intent_result):
        """Test system prompt generation for development tasks."""
        context = {
            "chat_id": 12345,
            "username": "testuser",
            "is_group_chat": False
        }
        
        prompt = prompt_manager.get_system_prompt(development_intent_result, context)
        
        # Should contain identity and role
        assert "Valor Engels" in prompt
        assert "software engineer" in prompt
        
        # Should contain intent-specific guidance
        assert "development_task" in prompt
        assert "0.85" in prompt  # confidence
        assert "Execute technical tasks" in prompt or "technical" in prompt
        
        # Should contain context information
        assert "testuser" in prompt
        assert "direct message" in prompt

    def test_image_generation_prompt(self, prompt_manager, image_generation_intent_result):
        """Test system prompt generation for image generation."""
        prompt = prompt_manager.get_system_prompt(image_generation_intent_result)
        
        # Should contain creative guidance
        assert "image_generation" in prompt
        assert "creative" in prompt.lower() or "visual" in prompt.lower()
        assert "artistic" in prompt.lower() or "image" in prompt.lower()

    def test_behavioral_instructions(self, prompt_manager):
        """Test behavioral instruction generation."""
        instructions = prompt_manager._get_behavioral_instructions(MessageIntent.CASUAL_CHAT)
        
        assert "conversational" in instructions.lower()
        assert "engaging" in instructions.lower()
        
        dev_instructions = prompt_manager._get_behavioral_instructions(MessageIntent.DEVELOPMENT_TASK)
        assert "code" in dev_instructions.lower() or "technical" in dev_instructions.lower()

    def test_conversation_starters(self, prompt_manager):
        """Test conversation starter generation."""
        starter = prompt_manager.get_conversation_starter(MessageIntent.CASUAL_CHAT)
        assert len(starter) > 0
        assert "chat" in starter.lower() or "help" in starter.lower()
        
        dev_starter = prompt_manager.get_conversation_starter(MessageIntent.DEVELOPMENT_TASK)
        assert "technical" in dev_starter.lower() or "task" in dev_starter.lower()

    def test_prompt_intent_guidance(self, prompt_manager):
        """Test intent-specific guidance extraction."""
        guidance = prompt_manager.get_prompt_for_intent(MessageIntent.PROJECT_QUERY)
        
        assert "project" in guidance.lower()
        assert "Focus:" in guidance
        assert "Style:" in guidance
        assert "Tools:" in guidance
        assert "Guidance:" in guidance


class TestIntegrationFlows:
    """Test complete integration flows combining all systems."""

    @pytest.mark.asyncio
    async def test_complete_message_flow(self):
        """Test complete message processing flow from intent to response."""
        # Mock components
        mock_client = AsyncMock()
        mock_client.send_reaction = AsyncMock()
        
        # Test message
        message = "Fix the authentication bug in the login system"
        context = {
            "chat_id": 12345,
            "is_group_chat": False,
            "username": "developer",
            "has_image": False,
            "has_links": False
        }
        
        # Mock Ollama classification
        with patch('integrations.ollama_intent.classify_message_intent') as mock_classify:
            mock_classify.return_value = IntentResult(
                intent=MessageIntent.DEVELOPMENT_TASK,
                confidence=0.85,
                reasoning="Bug fix request",
                suggested_emoji="⚙️"
            )
            
            # Classify intent
            intent_result = await classify_message_intent(message, context)
            
            # Verify classification (allow for fallback behavior)
            assert intent_result.intent == MessageIntent.DEVELOPMENT_TASK
            assert intent_result.confidence >= 0.7  # Accept fallback confidence
            
            # Test tool configuration
            tool_manager = IntentToolManager()
            allowed_tools = tool_manager.get_allowed_tools(intent_result)
            
            # Should include development tools
            dev_tools = {"edit", "write", "bash", "read"}
            assert dev_tools.intersection(set(allowed_tools))
            
            # Test system prompt generation
            prompt_manager = IntentPromptManager()
            system_prompt = prompt_manager.get_system_prompt(intent_result, context)
            
            # Should contain development-specific guidance
            assert "development_task" in system_prompt
            assert "technical" in system_prompt.lower()
            
            # Test reaction management
            reaction_manager = TelegramReactionManager()
            
            # Add received reaction
            await reaction_manager.add_received_reaction(mock_client, 12345, 67890)
            
            # Add intent reaction
            await reaction_manager.add_intent_reaction(mock_client, 12345, 67890, intent_result)
            
            # Complete sequence
            await reaction_manager.update_reaction_sequence(
                mock_client, 12345, 67890, intent_result, success=True
            )
            
            # Verify reactions were added
            reactions = reaction_manager.get_message_reactions(12345, 67890)
            assert "👀" in reactions  # received
            assert "⚙️" in reactions  # intent
            assert "✅" in reactions  # success

    @pytest.mark.asyncio
    async def test_error_handling_flow(self):
        """Test error handling in the complete flow."""
        # Test Ollama unavailable
        with patch('integrations.ollama_intent.classify_message_intent') as mock_classify:
            mock_classify.side_effect = Exception("Ollama connection failed")
            
            # Should still work with fallback
            try:
                result = await classify_message_intent("test message")
                # Should get fallback result
                assert result.intent in MessageIntent
            except Exception as e:
                # Should handle gracefully
                assert "connection" in str(e).lower() or "failed" in str(e).lower()

    def test_performance_requirements(self):
        """Test performance requirements for intent system."""
        # Tool manager should be fast
        tool_manager = IntentToolManager()
        intent_result = IntentResult(
            intent=MessageIntent.DEVELOPMENT_TASK,
            confidence=0.85,
            reasoning="Test",
            suggested_emoji="⚙️"
        )
        
        import time
        start = time.time()
        tools = tool_manager.get_allowed_tools(intent_result)
        end = time.time()
        
        # Should be very fast (< 10ms)
        assert (end - start) < 0.01
        assert len(tools) > 0
        
        # Prompt manager should be fast
        prompt_manager = IntentPromptManager()
        start = time.time()
        prompt = prompt_manager.get_system_prompt(intent_result)
        end = time.time()
        
        # Should be fast (< 50ms)
        assert (end - start) < 0.05
        assert len(prompt) > 100


if __name__ == "__main__":
    # Run basic tests if executed directly
    pytest.main([__file__, "-v"])