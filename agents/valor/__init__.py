"""
Valor Engels agent module.

This module contains the complete Valor Engels agent implementation with all tools,
handlers, and persona configuration organized in a structured directory.
"""

from .agent import ValorContext, run_valor_agent, valor_agent
from .handlers import (
    handle_general_question,
    handle_telegram_message,
    handle_user_priority_question,
)

__all__ = [
    "ValorContext",
    "run_valor_agent",
    "valor_agent",
    "handle_telegram_message",
    "handle_general_question",
    "handle_user_priority_question",
]
