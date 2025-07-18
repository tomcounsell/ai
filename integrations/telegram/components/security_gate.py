"""
SecurityGate: Centralized access control and security validation.

Consolidates all access control logic from scattered locations into
a single, testable component.
"""

import logging
import os
from datetime import datetime, timedelta

# Using pyrogram Message type
from typing import Any as TelegramMessage

from integrations.telegram.models import AccessResult
from utilities.workspace_validator import WorkspaceValidator, get_workspace_validator

logger = logging.getLogger(__name__)


class SecurityGate:
    """Centralized access control and security validation."""

    def __init__(self, workspace_validator: WorkspaceValidator | None = None):
        """Initialize SecurityGate with optional workspace validator."""
        self.workspace_validator = workspace_validator or get_workspace_validator()
        self.bot_user_id = int(os.getenv("TELEGRAM_BOT_USER_ID", "0"))
        self.bot_username = os.getenv("TELEGRAM_BOT_USERNAME", "valorengels")
        
        # Dynamic bot info (will be set by the client during initialization)
        self._dynamic_bot_user_id = None
        self._dynamic_bot_username = None

        # Rate limiting storage (in production, use Redis)
        self._rate_limits: dict[int, dict[str, any]] = {}
        self._rate_limit_window = 60  # seconds
        self._rate_limit_max_messages = 30  # messages per window

    def validate_access(self, message: TelegramMessage) -> AccessResult:
        """
        Single method for all access control decisions.

        Validates:
        1. Bot self-messages (always skip)
        2. Chat whitelist validation
        3. DM permissions
        4. Rate limiting
        5. Message age checks

        Returns:
            AccessResult with allowed status and reason if denied
        """
        try:
            # Check if bot self-message
            if self.is_bot_self_message(message):
                logger.info(f"🚫 Blocking bot self-message in chat {message.chat.id}, message {message.id}")
                return AccessResult(
                    allowed=False, reason="Bot self-message", metadata={"skip_silently": True}
                )

            chat_id = message.chat.id
            username = message.from_user.username if message.from_user else "unknown"

            # Check if chat is allowed
            if not self._is_chat_allowed(chat_id, username):
                return AccessResult(allowed=False, reason=f"Chat {chat_id} not in whitelist")

            # Check rate limits
            rate_limit_result = self._check_rate_limits(chat_id, username)
            if not rate_limit_result.allowed:
                return rate_limit_result

            # Check message age (skip very old messages)
            if self._is_message_too_old(message):
                return AccessResult(
                    allowed=False,
                    reason="Message too old (>5 minutes)",
                    metadata={"skip_silently": True},
                )

            # All checks passed
            return AccessResult(
                allowed=True,
                rate_limit_remaining=rate_limit_result.rate_limit_remaining,
                metadata={"chat_id": chat_id, "username": username, "is_private": chat_id > 0},
            )

        except Exception as e:
            logger.error(f"Security validation error: {str(e)}")
            return AccessResult(allowed=False, reason=f"Security validation error: {str(e)}")

    def is_bot_self_message(self, message: TelegramMessage) -> bool:
        """Check if message is from bot itself."""
        if not message.from_user:
            return False

        user_id = message.from_user.id
        username = message.from_user.username
        
        # Enhanced logging for debugging self-reaction issue
        logger.info(f"🔍 Checking self-message: user_id={user_id}, username={username}, dynamic_bot_id={self._dynamic_bot_user_id}, dynamic_bot_username={self._dynamic_bot_username}")

        # Check by dynamic user ID (most reliable, set during initialization)
        if self._dynamic_bot_user_id and user_id == self._dynamic_bot_user_id:
            logger.info(f"✅ Detected self-message by dynamic user ID: {user_id}")
            return True

        # Check by dynamic username
        if self._dynamic_bot_username and username == self._dynamic_bot_username:
            logger.info(f"✅ Detected self-message by dynamic username: {username}")
            return True

        # Check by environment variable user ID (fallback)
        if self.bot_user_id and user_id == self.bot_user_id:
            logger.info(f"✅ Detected self-message by env user ID: {user_id}")
            return True

        # Check by environment variable username (fallback)
        if self.bot_username and username == self.bot_username:
            logger.info(f"✅ Detected self-message by env username: {username}")
            return True

        # Additional safety check: check against known bot user ID from config
        # Updated with actual bot user ID: 6914249008
        known_bot_user_ids = [6914249008, 66968934582]  # Actual bot ID + legacy from config
        if user_id in known_bot_user_ids:
            logger.info(f"✅ Detected self-message by known bot user ID: {user_id}")
            return True

        # Not a self-message
        logger.info(f"❌ Not a self-message: user_id={user_id}, username={username}")
        return False

    def set_bot_info(self, user_id: int, username: str = None):
        """Set the bot's user info dynamically during initialization."""
        self._dynamic_bot_user_id = user_id
        self._dynamic_bot_username = username
        logger.info(f"SecurityGate: Bot info set - ID: {user_id}, Username: {username}")

    def _is_chat_allowed(self, chat_id: int, username: str) -> bool:
        """Check if chat/user is allowed to interact with bot."""
        # Use the centralized validation function that respects TELEGRAM_ALLOW_DMS
        from utilities.workspace_validator import validate_chat_whitelist_access
        
        is_private = chat_id > 0
        return validate_chat_whitelist_access(chat_id, is_private, username)


    def _check_rate_limits(self, chat_id: int, username: str) -> AccessResult:
        """Check and update rate limits for chat."""
        current_time = datetime.now()
        chat_key = str(chat_id)

        # Initialize rate limit data if needed
        if chat_key not in self._rate_limits:
            self._rate_limits[chat_key] = {
                "count": 0,
                "window_start": current_time,
                "username": username,
            }

        rate_data = self._rate_limits[chat_key]

        # Reset window if expired
        if (current_time - rate_data["window_start"]).total_seconds() > self._rate_limit_window:
            rate_data["count"] = 0
            rate_data["window_start"] = current_time

        # Check limit
        if rate_data["count"] >= self._rate_limit_max_messages:
            remaining_time = (
                self._rate_limit_window - (current_time - rate_data["window_start"]).total_seconds()
            )
            return AccessResult(
                allowed=False,
                reason=f"Rate limit exceeded. Try again in {int(remaining_time)}s",
                rate_limit_remaining=0,
                metadata={"retry_after": int(remaining_time)},
            )

        # Increment counter
        rate_data["count"] += 1
        remaining = self._rate_limit_max_messages - rate_data["count"]

        return AccessResult(allowed=True, rate_limit_remaining=remaining)

    def _is_message_too_old(self, message: TelegramMessage) -> bool:
        """Check if message is too old to process."""
        if not message.date:
            return False

        message_age = (datetime.now(message.date.tzinfo) - message.date).total_seconds()
        return message_age > 300  # 5 minutes

    def clear_rate_limits(self, chat_id: int | None = None):
        """Clear rate limits for a specific chat or all chats."""
        if chat_id:
            chat_key = str(chat_id)
            if chat_key in self._rate_limits:
                del self._rate_limits[chat_key]
        else:
            self._rate_limits.clear()

    def get_chat_status(self, chat_id: int) -> dict[str, any]:
        """Get current status for a chat including rate limits."""
        chat_key = str(chat_id)

        status = {
            "chat_id": chat_id,
            "is_allowed": self._is_chat_allowed(chat_id, ""),
            "is_private": chat_id > 0,
            "rate_limit": {
                "remaining": self._rate_limit_max_messages,
                "window_seconds": self._rate_limit_window,
                "reset_at": None,
            },
        }

        if chat_key in self._rate_limits:
            rate_data = self._rate_limits[chat_key]
            remaining = self._rate_limit_max_messages - rate_data["count"]
            reset_at = rate_data["window_start"] + timedelta(seconds=self._rate_limit_window)

            status["rate_limit"]["remaining"] = max(0, remaining)
            status["rate_limit"]["reset_at"] = reset_at.isoformat()

        return status
