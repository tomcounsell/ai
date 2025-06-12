"""
Comprehensive tests for UnifiedMessageProcessor.
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

from integrations.telegram.unified_processor import UnifiedMessageProcessor
from integrations.telegram.models import ProcessingResult, AccessResult


class TestUnifiedMessageProcessor:
    """Test the complete unified processing pipeline."""
    
    @pytest.fixture
    def mock_bot(self):
        """Create mock Telegram bot."""
        bot = Mock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()
        bot.set_message_reaction = AsyncMock()
        return bot
    
    @pytest.fixture
    def mock_valor_agent(self):
        """Create mock Valor agent."""
        agent = Mock()
        return agent
    
    @pytest.fixture
    async def processor(self, mock_bot, mock_valor_agent):
        """Create UnifiedMessageProcessor instance."""
        processor = UnifiedMessageProcessor(
            telegram_bot=mock_bot,
            valor_agent=mock_valor_agent
        )
        return processor
    
    @pytest.fixture
    def mock_update(self):
        """Create mock Telegram update with message."""
        update = Mock()
        message = Mock()
        message.message_id = 123
        message.chat = Mock(id=-1001234567890)
        message.from_user = Mock(id=111, username="testuser", is_bot=False)
        message.date = datetime.now()
        message.text = "Hello bot!"
        message.caption = None
        message.entities = []
        message.reply_to_message = None
        message.photo = None
        message.document = None
        message.audio = None
        message.video = None
        message.voice = None
        message.video_note = None
        update.message = message
        return update
    
    @pytest.mark.asyncio
    async def test_successful_message_processing(self, processor, mock_update, mock_bot):
        """Test successful end-to-end message processing."""
        # Mock component responses
        with patch.object(processor.security_gate, 'validate_access') as mock_security:
            mock_security.return_value = AccessResult(allowed=True)
            
            with patch.object(processor.context_builder, 'build_context') as mock_context:
                mock_context_obj = Mock()
                mock_context_obj.requires_response = True
                mock_context_obj.chat_id = -1001234567890
                mock_context_obj.message = mock_update.message
                mock_context_obj.cleaned_text = "Hello bot!"
                mock_context.return_value = mock_context_obj
                
                with patch.object(processor.type_router, 'route_message') as mock_router:
                    mock_plan = Mock()
                    mock_plan.message_type = Mock(value="text")
                    mock_plan.priority = Mock(value="medium")
                    mock_plan.requires_agent = True
                    mock_router.return_value = mock_plan
                    
                    with patch.object(processor.agent_orchestrator, 'process_with_agent') as mock_agent:
                        mock_response = Mock()
                        mock_response.content = "Hello! How can I help you?"
                        mock_response.has_media = False
                        mock_response.reactions = []
                        mock_agent.return_value = mock_response
                        
                        with patch.object(processor.response_manager, 'deliver_response') as mock_deliver:
                            mock_delivery = Mock()
                            mock_delivery.success = True
                            mock_delivery.message_id = 456
                            mock_deliver.return_value = mock_delivery
                            
                            # Process message
                            result = await processor.process_message(mock_update, None)
                            
                            # Verify pipeline execution
                            assert result.success is True
                            assert "Processed text in" in result.summary
                            mock_security.assert_called_once()
                            mock_context.assert_called_once()
                            mock_router.assert_called_once()
                            mock_agent.assert_called_once()
                            mock_deliver.assert_called_once()
                            
                            # Check metrics
                            assert processor.processed_count == 1
                            assert processor.error_count == 0
    
    @pytest.mark.asyncio
    async def test_security_denial(self, processor, mock_update):
        """Test message denied by security gate."""
        with patch.object(processor.security_gate, 'validate_access') as mock_security:
            mock_security.return_value = AccessResult(
                allowed=False,
                reason="User not in whitelist"
            )
            
            result = await processor.process_message(mock_update, None)
            
            assert result.success is False
            assert "Access denied" in result.summary
            assert "User not in whitelist" in result.error
            
            # Should not proceed to other steps
            with patch.object(processor.context_builder, 'build_context') as mock_context:
                mock_context.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_silent_skip(self, processor, mock_update):
        """Test silent skip for bot messages."""
        with patch.object(processor.security_gate, 'validate_access') as mock_security:
            mock_security.return_value = AccessResult(
                allowed=False,
                reason="Bot self-message",
                metadata={"skip_silently": True}
            )
            
            result = await processor.process_message(mock_update, None)
            
            assert result.success is False
            assert "Bot self-message" in result.error
    
    @pytest.mark.asyncio
    async def test_no_response_required(self, processor, mock_update):
        """Test message that doesn't require response."""
        with patch.object(processor.security_gate, 'validate_access') as mock_security:
            mock_security.return_value = AccessResult(allowed=True)
            
            with patch.object(processor.context_builder, 'build_context') as mock_context:
                mock_context_obj = Mock()
                mock_context_obj.requires_response = False
                mock_context.return_value = mock_context_obj
                
                result = await processor.process_message(mock_update, None)
                
                assert result.success is True
                assert "no response needed" in result.summary
    
    @pytest.mark.asyncio
    async def test_error_handling(self, processor, mock_update):
        """Test error handling in pipeline."""
        with patch.object(processor.security_gate, 'validate_access') as mock_security:
            mock_security.return_value = AccessResult(allowed=True)
            
            with patch.object(processor.context_builder, 'build_context') as mock_context:
                mock_context.side_effect = Exception("Context building failed")
                
                result = await processor.process_message(mock_update, None)
                
                assert result.success is False
                assert "Context building failed" in result.error
                assert processor.error_count == 1
    
    @pytest.mark.asyncio
    async def test_batch_processing(self, processor, mock_update):
        """Test batch message processing."""
        # Create multiple updates
        updates = [mock_update for _ in range(5)]
        
        with patch.object(processor, 'process_message') as mock_process:
            mock_process.return_value = ProcessingResult(
                success=True,
                summary="Processed"
            )
            
            results = await processor.process_message_batch(updates)
            
            assert len(results) == 5
            assert all(r.success for r in results)
            assert mock_process.call_count == 5
    
    @pytest.mark.asyncio
    async def test_metrics_tracking(self, processor):
        """Test metrics tracking."""
        # Process some successful messages
        with patch.object(processor, 'process_message') as mock_process:
            processor.processed_count = 10
            processor.error_count = 2
            processor.total_processing_time = 15.5
            
            metrics = processor.get_metrics()
            
            assert metrics["processed_count"] == 10
            assert metrics["error_count"] == 2
            assert metrics["error_rate"] == 2/12  # 2 errors out of 12 total
            assert metrics["average_processing_time"] == 1.55
    
    @pytest.mark.asyncio
    async def test_health_check(self, processor):
        """Test health check functionality."""
        health = await processor.health_check()
        
        assert health["status"] in ["healthy", "degraded"]
        assert "components" in health
        assert "metrics" in health
        assert "security_gate" in health["components"]
    
    @pytest.mark.asyncio
    async def test_no_message_in_update(self, processor):
        """Test handling update with no message."""
        empty_update = Mock()
        empty_update.message = None
        
        result = await processor.process_message(empty_update, None)
        
        assert result.success is False
        assert "No message in update" in result.error
    
    @pytest.mark.asyncio
    async def test_metrics_reset(self, processor):
        """Test metrics reset functionality."""
        processor.processed_count = 100
        processor.error_count = 10
        processor.total_processing_time = 200.0
        
        processor.reset_metrics()
        
        assert processor.processed_count == 0
        assert processor.error_count == 0
        assert processor.total_processing_time == 0
    
    @pytest.mark.asyncio
    async def test_error_response_delivery(self, processor, mock_update, mock_bot):
        """Test error response delivery on processing failure."""
        with patch.object(processor.security_gate, 'validate_access') as mock_security:
            mock_security.return_value = AccessResult(allowed=True)
            
            with patch.object(processor.context_builder, 'build_context') as mock_context:
                mock_context_obj = Mock()
                mock_context_obj.requires_response = True
                mock_context_obj.chat_id = -1001234567890
                mock_context_obj.message = mock_update.message
                mock_context.return_value = mock_context_obj
                
                with patch.object(processor.type_router, 'route_message') as mock_router:
                    mock_router.side_effect = Exception("Routing failed")
                    
                    with patch.object(processor.response_manager, 'create_fallback_response') as mock_fallback:
                        mock_fallback.return_value = "❌ An error occurred"
                        
                        result = await processor.process_message(mock_update, None)
                        
                        assert result.success is False
                        assert "Routing failed" in result.error
                        mock_fallback.assert_called_once()