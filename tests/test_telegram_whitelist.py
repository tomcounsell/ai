"""
Test suite for Telegram whitelist functionality

Validates that:
1. MessageHandler properly enforces environment whitelist configuration
2. Non-whitelisted groups are rejected with proper logging
3. DM handling respects TELEGRAM_ALLOW_DMS setting
4. Error handling works correctly for invalid configurations
"""

import pytest
import os
from unittest.mock import patch, MagicMock, AsyncMock
from pyrogram.enums import ChatType

from integrations.telegram.handlers import MessageHandler


class TestTelegramWhitelist:
    """Test Telegram message handler whitelist functionality"""
    
    def setup_method(self):
        """Setup test environment"""
        self.mock_client = MagicMock()
        self.mock_chat_history = MagicMock()
        self.mock_notion_scout = MagicMock()
        
        # Mock chat history methods
        self.mock_chat_history.add_message = MagicMock()
        self.mock_chat_history.get_context = MagicMock(return_value=[])
        
    def test_load_chat_filters_valid_config(self):
        """Test loading chat filters with valid environment configuration"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890,-1008888888888',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            assert len(handler.allowed_groups) == 2
            assert -1001234567890 in handler.allowed_groups
            assert -1008888888888 in handler.allowed_groups
            assert handler.allow_dms == True

    def test_load_chat_filters_no_groups(self):
        """Test loading chat filters with no groups configured"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '',
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            assert len(handler.allowed_groups) == 0
            assert handler.allow_dms == False

    def test_load_chat_filters_invalid_groups(self):
        """Test loading chat filters with invalid group format falls back to safe defaults"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': 'invalid,format,123abc',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Should fall back to safe defaults (no groups)
            assert len(handler.allowed_groups) == 0

    def test_should_handle_chat_whitelisted_group(self):
        """Test that whitelisted groups are allowed"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890,-1008888888888',
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Whitelisted group should be handled
            assert handler._should_handle_chat(-1001234567890, is_private_chat=False) == True
            assert handler._should_handle_chat(-1008888888888, is_private_chat=False) == True

    def test_should_handle_chat_non_whitelisted_group(self):
        """Test that non-whitelisted groups are rejected"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890,-1008888888888',
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Non-whitelisted group should be rejected
            assert handler._should_handle_chat(-9999999999999, is_private_chat=False) == False

    def test_should_handle_chat_dm_allowed(self):
        """Test that DMs are handled when enabled"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # DM should be handled when enabled
            assert handler._should_handle_chat(12345, is_private_chat=True) == True

    def test_should_handle_chat_dm_disabled(self):
        """Test that DMs are rejected when disabled"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # DM should be rejected when disabled
            assert handler._should_handle_chat(12345, is_private_chat=True) == False

    @pytest.mark.asyncio
    async def test_handle_message_whitelisted_group_processed(self):
        """Test that messages from whitelisted groups are processed"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'false'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Mock message from whitelisted group
            mock_message = MagicMock()
            mock_message.chat.id = -1001234567890
            mock_message.chat.type = ChatType.SUPERGROUP
            mock_message.text = "ping"
            mock_message.id = 123
            mock_message.from_user = MagicMock()
            mock_message.from_user.username = "testuser"
            mock_message.reply = AsyncMock()
            
            # Mock client methods
            self.mock_client.read_chat_history = AsyncMock()
            self.mock_client.send_reaction = AsyncMock()
            
            # Should process the message (ping command)
            await handler.handle_message(self.mock_client, mock_message)
            
            # Verify message was processed (ping response sent)
            mock_message.reply.assert_called_once()
            reply_text = mock_message.reply.call_args[0][0]
            assert "pong" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_handle_message_non_whitelisted_group_rejected(self):
        """Test that messages from non-whitelisted groups are rejected with logging"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'false'
        }), patch('integrations.telegram.handlers.logging.getLogger') as mock_get_logger:
            
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Mock message from non-whitelisted group
            mock_message = MagicMock()
            mock_message.chat.id = -9999999999999
            mock_message.chat.type = ChatType.SUPERGROUP
            mock_message.text = "test message"
            mock_message.from_user = MagicMock()
            mock_message.from_user.username = "testuser"
            mock_message.reply = AsyncMock()
            
            # Should reject the message early
            await handler.handle_message(self.mock_client, mock_message)
            
            # Verify rejection was logged
            mock_logger.warning.assert_called_once()
            warning_call_args = mock_logger.warning.call_args[0][0]
            assert "MESSAGE REJECTED" in warning_call_args
            assert "Chat whitelist violation" in warning_call_args
            assert "-9999999999999" in warning_call_args
            
            # Verify message was not processed (no reply sent)
            mock_message.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_dm_when_disabled_rejected(self):
        """Test that DM messages are rejected when DMs are disabled"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'false'
        }), patch('integrations.telegram.handlers.logging.getLogger') as mock_get_logger:
            
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Mock DM message
            mock_message = MagicMock()
            mock_message.chat.id = 12345
            mock_message.chat.type = ChatType.PRIVATE
            mock_message.text = "hello"
            mock_message.from_user = MagicMock()
            mock_message.from_user.username = "testuser"
            mock_message.reply = AsyncMock()
            
            # Should reject the DM
            await handler.handle_message(self.mock_client, mock_message)
            
            # Verify rejection was logged
            mock_logger.warning.assert_called_once()
            warning_call_args = mock_logger.warning.call_args[0][0]
            assert "MESSAGE REJECTED" in warning_call_args
            assert "DM 12345" in warning_call_args
            
            # Verify message was not processed
            mock_message.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_dm_when_enabled_processed(self):
        """Test that DM messages are processed when DMs are enabled"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '',
            'TELEGRAM_ALLOW_DMS': 'true'
        }):
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Mock DM message
            mock_message = MagicMock()
            mock_message.chat.id = 12345
            mock_message.chat.type = ChatType.PRIVATE
            mock_message.text = "ping"
            mock_message.id = 123
            mock_message.from_user = MagicMock()
            mock_message.from_user.username = "testuser"
            mock_message.reply = AsyncMock()
            
            # Mock client methods
            self.mock_client.read_chat_history = AsyncMock()
            self.mock_client.send_reaction = AsyncMock()
            
            # Should process the DM (ping command)
            await handler.handle_message(self.mock_client, mock_message)
            
            # Verify message was processed (ping response sent)
            mock_message.reply.assert_called_once()
            reply_text = mock_message.reply.call_args[0][0]
            assert "pong" in reply_text.lower()

    def test_chat_validation_error_handling(self):
        """Test that chat validation errors are handled gracefully"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'true'
        }), patch('utilities.workspace_validator.validate_chat_whitelist_access') as mock_validate:
            
            # Mock validation function to raise an error
            mock_validate.side_effect = Exception("Validation error")
            
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Should return False (deny access) when validation fails
            result = handler._should_handle_chat(12345, is_private_chat=True)
            assert result == False

    def test_environment_validation_failure_safe_defaults(self):
        """Test that environment validation failures result in safe defaults"""
        with patch('utilities.workspace_validator.validate_telegram_environment') as mock_validate:
            # Mock validation to return failure
            mock_validate.return_value = {
                "status": "failed",
                "errors": ["Environment validation failed"]
            }
            
            handler = MessageHandler(
                self.mock_client, 
                self.mock_chat_history, 
                self.mock_notion_scout
            )
            
            # Should have safe defaults (no groups, no DMs)
            assert len(handler.allowed_groups) == 0
            assert handler.allow_dms == False


class TestWhitelistSecurityLogging:
    """Test security logging functionality for whitelist violations"""
    
    @pytest.mark.asyncio
    async def test_security_logging_includes_message_preview(self):
        """Test that security logs include message preview for audit trail"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'false'
        }), patch('integrations.telegram.handlers.logging.getLogger') as mock_get_logger:
            
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            
            handler = MessageHandler(
                MagicMock(), 
                MagicMock(), 
                MagicMock()
            )
            
            # Mock message with long text
            mock_message = MagicMock()
            mock_message.chat.id = -9999999999999
            mock_message.chat.type = ChatType.SUPERGROUP
            mock_message.text = "This is a very long message that should be truncated in the log for security audit purposes"
            mock_message.from_user = MagicMock()
            mock_message.from_user.username = "attacker"
            
            await handler.handle_message(MagicMock(), mock_message)
            
            # Verify logging includes message preview and user info
            mock_logger.warning.assert_called_once()
            log_message = mock_logger.warning.call_args[0][0]
            assert "MESSAGE REJECTED" in log_message
            assert "attacker" in log_message
            assert "This is a very long message that should be trun..." in log_message

    @pytest.mark.asyncio
    async def test_console_output_for_rejected_messages(self):
        """Test that rejected messages also produce console output for visibility"""
        with patch.dict(os.environ, {
            'TELEGRAM_ALLOWED_GROUPS': '-1001234567890',
            'TELEGRAM_ALLOW_DMS': 'false'
        }), patch('builtins.print') as mock_print:
            
            handler = MessageHandler(
                MagicMock(), 
                MagicMock(), 
                MagicMock()
            )
            
            # Mock rejected message
            mock_message = MagicMock()
            mock_message.chat.id = -9999999999999
            mock_message.chat.type = ChatType.SUPERGROUP
            mock_message.text = "rejected message"
            mock_message.from_user = MagicMock()
            mock_message.from_user.username = "testuser"
            
            await handler.handle_message(MagicMock(), mock_message)
            
            # Verify console output was produced
            mock_print.assert_called_once()
            print_message = mock_print.call_args[0][0]
            assert "🚫 Rejected" in print_message
            assert "-9999999999999" in print_message
            assert "testuser" in print_message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])