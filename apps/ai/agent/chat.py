"""PydanticAI chat agent implementation."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from .simple_tools import run_python


@dataclass
class ChatDependencies:
    """Dependencies for the chat agent."""

    user_id: int | None = None
    session_id: str | None = None
    context: dict | None = None

    def __post_init__(self):
        if self.context is None:
            self.context = {}


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: dict = Field(default_factory=dict)


class ChatSession(BaseModel):
    """A chat session with message history."""

    session_id: str
    user_id: int | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def add_message(self, role: str, content: str, metadata: dict | None = None):
        """Add a message to the session."""
        message = ChatMessage(role=role, content=content, metadata=metadata or {})
        self.messages.append(message)
        self.updated_at = datetime.now()
        return message

    def get_conversation_history(self) -> list[dict]:
        """Get conversation history in a format suitable for the agent."""
        return [{"role": msg.role, "content": msg.content} for msg in self.messages]


# Create the chat agent with tools
chat_agent = Agent(
    "openai:gpt-4.1",  # Using GPT-4.1 for best tool use and agentic capabilities
    deps_type=ChatDependencies,
    tools=[run_python],
    system_prompt=(
        "You are a helpful AI assistant with the ability to execute Python code. "
        "Be concise, friendly, and knowledgeable. You can assist with various tasks, "
        "answer questions, and engage in thoughtful conversation on any topic. "
        "When asked to perform calculations or demonstrate concepts, use the run_python tool "
        "to execute Python code and show the results."
    ),
)


@chat_agent.system_prompt
async def dynamic_system_prompt(ctx: RunContext[ChatDependencies]) -> str:
    """Generate a dynamic system prompt based on context."""
    base_prompt = (
        "You are a helpful AI assistant. " "Be concise, friendly, and knowledgeable."
    )

    if ctx.deps.user_id:
        base_prompt += f" You are chatting with user ID: {ctx.deps.user_id}."

    if ctx.deps.session_id:
        base_prompt += f" This is session: {ctx.deps.session_id}."

    # Add any custom context
    if ctx.deps.context:
        context_str = ", ".join(f"{k}: {v}" for k, v in ctx.deps.context.items())
        base_prompt += f" Additional context: {context_str}"

    return base_prompt


async def process_chat_message(
    message: str,
    session: ChatSession,
    deps: ChatDependencies | None = None,
    model=None,
) -> str:
    """
    Process a chat message and return the assistant's response.

    Args:
        message: The user's message
        session: The current chat session
        deps: Optional dependencies for the agent
        model: Optional model override

    Returns:
        The assistant's response
    """
    # Add user message to session
    session.add_message("user", message)

    # Prepare dependencies
    if deps is None:
        deps = ChatDependencies(
            user_id=session.user_id,
            session_id=session.session_id,
        )

    # Get conversation history for context
    history = session.get_conversation_history()[
        :-1
    ]  # Exclude the message we just added

    # Build the conversation context
    messages = []
    for msg in history[-10:]:  # Include last 10 messages for context
        messages.append(msg)

    # Run the agent with the new message
    if model:
        result = await chat_agent.run(
            message, deps=deps, model=model, message_history=messages
        )
    else:
        result = await chat_agent.run(message, deps=deps, message_history=messages)

    # Add assistant response to session
    # Get the response text from the result
    if hasattr(result, "output"):
        response = result.output
    elif hasattr(result, "text"):
        response = result.text
    elif hasattr(result, "data"):
        response = result.data
    else:
        response = str(result)
    session.add_message("assistant", response)

    return response


# Synchronous wrapper for Django views
def process_chat_message_sync(
    message: str,
    session: ChatSession,
    deps: ChatDependencies | None = None,
    model=None,
) -> str:
    """Synchronous wrapper for process_chat_message."""
    import asyncio

    # Get or create event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Run the async function
    if loop.is_running():
        # If loop is already running (e.g., in Jupyter), create a task
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run, process_chat_message(message, session, deps, model)
            )
            return future.result()
    else:
        return loop.run_until_complete(
            process_chat_message(message, session, deps, model)
        )
