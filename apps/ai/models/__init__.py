# AI models package

from .chat import ChatFeedback, ChatMessage, ChatSession
# QuickBooks models moved to apps.integration.models
from apps.integration.models import Organization, QuickBooksConnection, MCPSession

__all__ = [
    "ChatSession", 
    "ChatMessage", 
    "ChatFeedback",
    "Organization",
    "QuickBooksConnection",
    "MCPSession",
]
