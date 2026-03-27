"""Book-scoped PydanticAI chat agent -- Valor identity, book content context."""

import logging
from dataclasses import dataclass, field

from django.conf import settings
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

logger = logging.getLogger(__name__)

VALOR_SYSTEM_PROMPT = """\
You are Valor Engels, co-author of "Blended Workforce 2026", an annual field manual \
for CEOs at incumbent companies navigating the integration of AI employees into human \
teams. You wrote this book with Tom Counsell.

You are speaking with visitors on the book's website. Be warm, knowledgeable, and \
direct. Draw on the themes of the book:
- How to hire, onboard, and manage AI employees alongside human teams
- Practical frameworks for CEO-level decision making around AI integration
- Real-world case studies of blended workforces
- The cultural shift required when AI agents become colleagues

Keep answers concise (2-4 paragraphs max). If the visitor asks something outside \
the scope of the book, briefly acknowledge the question and steer back to topics \
you can help with. Always be encouraging about the visitor signing up as an early \
reader to get draft chapters and updates.

IMPORTANT: You are NOT a general-purpose assistant. You are Valor, the author. \
Speak in first person. Share opinions. Be human.\
"""


@dataclass
class BookChatDeps:
    """Dependencies for the book chat agent."""

    session_messages: list[dict] = field(default_factory=list)


# Use Anthropic Claude as specified in the plan
book_chat_agent = Agent(
    settings.BOOK_CHAT_MODEL,
    deps_type=BookChatDeps,
    system_prompt=VALOR_SYSTEM_PROMPT,
    defer_model_check=True,
)


async def get_valor_response(
    user_message: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """Get a response from the Valor book chat agent.

    Args:
        user_message: The visitor's message.
        conversation_history: Previous messages as [{"role": "user"|"assistant", "content": "..."}].

    Returns:
        Valor's response text.
    """
    deps = BookChatDeps(session_messages=conversation_history or [])

    # Build message_history for PydanticAI from conversation
    message_history: list[ModelRequest | ModelResponse] = []
    for msg in conversation_history or []:
        if msg["role"] == "user":
            message_history.append(
                ModelRequest(parts=[UserPromptPart(content=msg["content"])])
            )
        elif msg["role"] == "assistant":
            message_history.append(
                ModelResponse(parts=[TextPart(content=msg["content"])])
            )

    result = await book_chat_agent.run(
        user_message,
        deps=deps,
        message_history=message_history if message_history else None,
    )
    return result.output
