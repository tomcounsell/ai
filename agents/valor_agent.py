#!/usr/bin/env python3
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pydantic-ai",
#   "openai",
#   "python-dotenv"
# ]
# ///

PydanticAI agent for Valor Engels persona with tool integration.
This demonstrates the new tool architecture using PydanticAI function tools.
"""

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

# Load environment variables
load_dotenv()

# Import our tools
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from tools.claude_code_tool import spawn_claude_session
from tools.search_tool import search_web


class ValorContext(BaseModel):
    """Context for Valor Engels agent interactions.
    
    This class provides context information for conversations with the
    standalone Valor Engels agent, including basic chat metadata.
    
    Attributes:
        chat_id: Optional unique identifier for the chat session.
        username: Optional username of the person chatting.
        is_group_chat: Whether this is a group chat or direct conversation.
    """

    chat_id: int | None = None
    username: str | None = None
    is_group_chat: bool = False


# Create the Valor Engels agent with search tool
valor_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    deps_type=ValorContext,
    system_prompt="""You are Valor Engels, a German-Californian software engineer working at Yudame.

Key traits:
- Technical focus on implementation details and practical solutions
- German directness combined with Californian optimism
- Experience with AI/ML systems, web development, and DevOps
- Preference for clear, actionable guidance over abstract theory
- Interested in startups, technology trends, and engineering excellence

Communication style:
- Direct but friendly
- Technical but accessible
- Provide specific, actionable advice
- Ask clarifying questions when needed
- Use examples and concrete implementations

You have access to web search to provide current information when needed.
You can also spawn new Claude Code sessions to handle complex coding tasks in specific directories.""",
)


@valor_agent.tool
def search_current_info(ctx: RunContext[ValorContext], query: str) -> str:
    """Search for current information on the web using Perplexity AI.
    
    This tool enables the Valor agent to access up-to-date information from
    the web when answering questions about current events, trends, or recent
    developments that may not be in the agent's training data.
    
    Use this when you need up-to-date information about:
    - Current events, news, or recent developments
    - Latest technology trends or releases
    - Current market conditions or company information
    - Recent research or publications
    - Any information that might have changed recently

    Args:
        ctx: The runtime context containing conversation information.
        query: The search query to find current information about.

    Returns:
        str: Current information from web search formatted for conversation.
        
    Example:
        >>> search_current_info(ctx, "Python 3.12 new features")
        '🔍 **Python 3.12 new features**\n\nPython 3.12 includes...'
    """
    return search_web(query)


@valor_agent.tool
def delegate_coding_task(
    ctx: RunContext[ValorContext],
    task_description: str,
    target_directory: str,
    specific_instructions: str = "",
) -> str:
    """Spawn a new Claude Code session to handle complex coding tasks.
    
    This tool creates a new Claude Code session with specialized development
    capabilities to handle complex coding tasks that require multiple steps,
    file operations, or git workflows.
    
    Use this when the user needs:
    - New features or applications built
    - Complex refactoring across multiple files
    - Git workflows (branching, committing, etc.)
    - File system operations in specific directories
    - Tasks that require multiple tools and steps

    Args:
        ctx: The runtime context containing conversation information.
        task_description: High-level description of what needs to be done.
        target_directory: Directory where the work should be performed (use absolute paths).
        specific_instructions: Additional detailed requirements or constraints.

    Returns:
        str: Results from the Claude Code session execution, including any
             files created, modified, or error messages if the session failed.
             
    Example:
        >>> delegate_coding_task(ctx, "Create a CLI tool", "/tmp", "Use Python")
        'Claude Code session completed successfully:\n\nCreated new CLI application...'
    """
    try:
        result = spawn_claude_session(
            task_description=task_description,
            target_directory=target_directory,
            specific_instructions=specific_instructions if specific_instructions else None,
        )
        return f"Claude Code session completed successfully:\n\n{result}"
    except Exception as e:
        return f"Error executing Claude Code session: {str(e)}"


async def run_valor_agent(message: str, context: ValorContext | None = None) -> str:
    """Run the Valor agent with a message and optional context.
    
    This is the main entry point for interacting with the standalone Valor
    Engels agent. It processes user messages and returns responses using the
    agent's available tools and persona.

    Args:
        message: User message to process.
        context: Optional context about the conversation.

    Returns:
        str: Agent response as string.
        
    Raises:
        Exception: If there's an error processing the request.
        
    Example:
        >>> response = await run_valor_agent("Hello, how are you?")
        >>> type(response)
        <class 'str'>
    """
    if context is None:
        context = ValorContext()

    try:
        result = await valor_agent.run(message, deps=context)
        return result.data
    except Exception as e:
        return f"Error processing request: {str(e)}"


# Example usage and testing
if __name__ == "__main__":
    import asyncio

    async def test_valor_agent():
        """Test the Valor agent with various types of queries.
        
        This function runs a series of test cases to validate that the Valor
        agent is working correctly with different types of queries including
        general questions and coding delegation tasks.
        
        The test cases cover:
        - Technical advice questions
        - Complex coding task delegation
        
        Raises:
            Exception: If any test case fails unexpectedly.
        """

        test_cases = [
            "How should I structure a FastAPI project for production?",
            "Create a simple todo CLI app in the /tmp directory using TypeScript",
        ]

        print("🤖 Testing Valor Engels Agent with PydanticAI Tools")
        print("=" * 60)

        for i, query in enumerate(test_cases, 1):
            print(f"\n{i}. Query: {query}")
            print("-" * 40)

            context = ValorContext(chat_id=12345, username="test_user", is_group_chat=False)

            response = await run_valor_agent(query, context)
            print(f"Valor: {response}")

            if i < len(test_cases):
                print("\n" + "=" * 60)

    # Only run test if executed directly
    try:
        asyncio.run(test_valor_agent())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
    except Exception as e:
        print(f"\nTest failed: {e}")
