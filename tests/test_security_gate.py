"""
Comprehensive tests for SecurityGate component.
"""

import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from integrations.telegram.components.security_gate import SecurityGate
from integrations.telegram.models import AccessResult


class TestSecurityGate:
    """Test SecurityGate access control functionality."""
    
    @pytest.fixture
    def security_gate(self):
        """Create SecurityGate instance with mock workspace validator."""
        mock_validator = Mock()
        mock_validator.config = {
            "dm_whitelist": {
                "allowed_users": {
                    "testuser": {"description": "Test user"},
                    "alloweduser": {"description": "Allowed user"}
                },
                "allowed_user_ids": {
                    "123456": {"description": "User without username"}
                }
            },
            "workspaces": {
                "TestWorkspace": {
                    "telegram_chat_ids": ["-1001234567890"],
                    "is_dev_group": True
                }
            }
        }
        mock_validator.get_workspace_for_chat.return_value = None
        
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_USER_ID": "987654321",
            "TELEGRAM_BOT_USERNAME": "testbot"
        }):
            return SecurityGate(workspace_validator=mock_validator)
    
    def test_bot_self_message_detection(self, security_gate):
        """Test detection of bot's own messages."""
        # Create mock message from bot
        message = Mock()
        message.from_user = Mock()
        message.from_user.id = 987654321
        message.from_user.username = "testbot"
        
        assert security_gate.is_bot_self_message(message) is True
        
        # Test with different user
        message.from_user.id = 123456
        message.from_user.username = "otheruser"
        assert security_gate.is_bot_self_message(message) is False
    
    def test_validate_access_bot_self_message(self, security_gate):
        """Test that bot self-messages are denied."""
        message = Mock()
        message.from_user = Mock(id=987654321, username="testbot")
        message.chat = Mock(id=12345)
        message.date = datetime.now()
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is False
        assert result.reason == "Bot self-message"
        assert result.metadata["skip_silently"] is True
    
    def test_dm_whitelist_allowed_username(self, security_gate):
        """Test DM access for whitelisted username."""
        message = Mock()
        message.from_user = Mock(id=999, username="testuser")
        message.chat = Mock(id=999)  # Positive ID = DM
        message.date = datetime.now()
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is True
        assert result.metadata["is_private"] is True
    
    def test_dm_whitelist_allowed_user_id(self, security_gate):
        """Test DM access for whitelisted user ID."""
        message = Mock()
        message.from_user = Mock(id=123456, username=None)
        message.chat = Mock(id=123456)
        message.date = datetime.now()
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is True
    
    def test_dm_whitelist_denied(self, security_gate):
        """Test DM access denied for non-whitelisted user."""
        message = Mock()
        message.from_user = Mock(id=789, username="randomuser")
        message.chat = Mock(id=789)
        message.date = datetime.now()
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is False
        assert "not in whitelist" in result.reason
    
    def test_group_chat_allowed(self, security_gate):
        """Test group chat access for configured workspace."""
        # Configure validator to return workspace
        security_gate.workspace_validator.get_workspace_for_chat.return_value = "TestWorkspace"
        
        message = Mock()
        message.from_user = Mock(id=111, username="user1")
        message.chat = Mock(id=-1001234567890)  # Negative ID = group
        message.date = datetime.now()
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is True
        security_gate.workspace_validator.get_workspace_for_chat.assert_called_with("-1001234567890")
    
    def test_group_chat_denied(self, security_gate):
        """Test group chat access denied for unconfigured chat."""
        message = Mock()
        message.from_user = Mock(id=111, username="user1")
        message.chat = Mock(id=-999999999)
        message.date = datetime.now()
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is False
        assert "not in whitelist" in result.reason
    
    def test_rate_limiting(self, security_gate):
        """Test rate limiting functionality."""
        # Set aggressive rate limit for testing
        security_gate._rate_limit_max_messages = 3
        
        message = Mock()
        message.from_user = Mock(id=111, username="user1")
        message.chat = Mock(id=111)
        message.date = datetime.now()
        
        # First 3 messages should pass
        for i in range(3):
            result = security_gate.validate_access(message)
            assert result.allowed is True
            assert result.rate_limit_remaining == (2 - i)
        
        # 4th message should be rate limited
        result = security_gate.validate_access(message)
        assert result.allowed is False
        assert "Rate limit exceeded" in result.reason
        assert result.rate_limit_remaining == 0
        assert "retry_after" in result.metadata
    
    def test_rate_limit_window_reset(self, security_gate):
        """Test rate limit window reset."""
        security_gate._rate_limit_max_messages = 2
        security_gate._rate_limit_window = 1  # 1 second window
        
        message = Mock()
        message.from_user = Mock(id=111, username="user1")
        message.chat = Mock(id=111)
        message.date = datetime.now()
        
        # Use up rate limit
        security_gate.validate_access(message)
        security_gate.validate_access(message)
        
        # Should be blocked
        result = security_gate.validate_access(message)
        assert result.allowed is False
        
        # Wait for window to reset
        import time
        time.sleep(1.1)
        
        # Should be allowed again
        result = security_gate.validate_access(message)
        assert result.allowed is True
    
    def test_old_message_rejection(self, security_gate):
        """Test that old messages are rejected."""
        message = Mock()
        message.from_user = Mock(id=111, username="testuser")
        message.chat = Mock(id=111)
        message.date = datetime.now() - timedelta(minutes=10)  # 10 minutes old
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is False
        assert "Message too old" in result.reason
        assert result.metadata["skip_silently"] is True
    
    def test_clear_rate_limits(self, security_gate):
        """Test clearing rate limits."""
        # Create some rate limit data
        security_gate._rate_limits["123"] = {"count": 5}
        security_gate._rate_limits["456"] = {"count": 3}
        
        # Clear specific chat
        security_gate.clear_rate_limits(123)
        assert "123" not in security_gate._rate_limits
        assert "456" in security_gate._rate_limits
        
        # Clear all
        security_gate.clear_rate_limits()
        assert len(security_gate._rate_limits) == 0
    
    def test_get_chat_status(self, security_gate):
        """Test getting chat status information."""
        # Set up some rate limit data
        security_gate._rate_limits["111"] = {
            "count": 5,
            "window_start": datetime.now(),
            "username": "testuser"
        }
        
        status = security_gate.get_chat_status(111)
        
        assert status["chat_id"] == 111
        assert status["is_private"] is True
        assert status["rate_limit"]["remaining"] == 25  # 30 - 5
        assert status["rate_limit"]["window_seconds"] == 60
        assert "reset_at" in status["rate_limit"]
    
    def test_error_handling(self, security_gate):
        """Test error handling in validation."""
        # Create message that will cause an error
        message = Mock()
        message.from_user = None  # Will cause AttributeError
        message.chat = Mock(id=111)
        
        result = security_gate.validate_access(message)
        
        assert result.allowed is False
        assert "Security validation error" in result.reason