"""Telegram Integration Package

This package provides a comprehensive communication layer for Telegram integration
with the AI Rebuild system, including unified message processing, security,
context management, and response handling.
"""

from .unified_processor import UnifiedProcessor
from .client import TelegramClient
from .handlers import HandlerRegistry

__all__ = ["UnifiedProcessor", "TelegramClient", "HandlerRegistry"]