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

import os
from typing import Optional
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import our tools  
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from tools.search_tool import search_web


class ValorContext(BaseModel):
    """Context for Valor Engels agent interactions."""
    chat_id: Optional[int] = None
    username: Optional[str] = None
    is_group_chat: bool = False


# Create the Valor Engels agent with search tool
valor_agent = Agent(
    'anthropic:claude-3-5-sonnet-20241022',
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

You have access to web search to provide current information when needed."""
)


@valor_agent.tool
def search_current_info(ctx: RunContext[ValorContext], query: str) -> str:
    """
    Search for current information on the web using Perplexity AI.
    Use this when you need up-to-date information about:
    - Current events, news, or recent developments
    - Latest technology trends or releases
    - Current market conditions or company information
    - Recent research or publications
    - Any information that might have changed recently
    
    Args:
        query: The search query to find current information about
        
    Returns:
        Current information from web search formatted for conversation
    """
    return search_web(query)


async def run_valor_agent(message: str, context: Optional[ValorContext] = None) -> str:
    """
    Run the Valor agent with a message and optional context.
    
    Args:
        message: User message to process
        context: Optional context about the conversation
        
    Returns:
        Agent response as string
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
        """Test the Valor agent with various types of queries."""
        
        test_cases = [
            "How should I structure a FastAPI project for production?",
        ]
        
        print("🤖 Testing Valor Engels Agent with PydanticAI Tools")
        print("=" * 60)
        
        for i, query in enumerate(test_cases, 1):
            print(f"\n{i}. Query: {query}")
            print("-" * 40)
            
            context = ValorContext(
                chat_id=12345,
                username="test_user",
                is_group_chat=False
            )
            
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