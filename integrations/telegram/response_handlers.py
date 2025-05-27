"""Response handlers for different types of Telegram questions."""

import sys
from pathlib import Path

# Add parent directory to path for agent imports
sys.path.append(str(Path(__file__).parent.parent.parent))

# Import the PydanticAI chat agent functions and alias them
from agents.valor_agent import (
    handle_general_question as handle_general_question_impl,
    handle_user_priority_question as handle_user_priority_question_impl,
)


async def handle_user_priority_question(
    question: str, anthropic_client, chat_id: int, notion_scout, chat_history
) -> str:
    """Handle questions about user's work priorities using PydanticAI agent."""
    return await handle_user_priority_question_impl(
        question=question, chat_id=chat_id, chat_history_obj=chat_history, notion_scout=notion_scout
    )


async def handle_general_question(
    question: str, anthropic_client, chat_id: int, chat_history
) -> str:
    """Handle general questions using PydanticAI agent."""
    return await handle_general_question_impl(
        question=question, chat_id=chat_id, chat_history_obj=chat_history
    )
