# tools/search_tool.py
"""
PydanticAI function tool for web search using Perplexity API.
This replaces integrations/search/web_search.py with a proper tool implementation.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

# Ensure environment variables are loaded
load_dotenv()


def search_web(query: str, max_results: int = 3) -> str:
    """Search the web and return AI-synthesized answers using Perplexity.
    
    This function uses the Perplexity API to search for current web information
    and return AI-synthesized answers. It's designed for conversational use
    and provides concise, informative responses.

    Args:
        query: The search query to execute.
        max_results: Maximum number of results (not used with Perplexity, kept for compatibility).

    Returns:
        str: AI-synthesized answer based on current web information, formatted for messaging.
             Returns an error message if the API key is missing or if an error occurs.
             
    Example:
        >>> search_web("latest Python features")
        '🔍 **latest Python features**\n\nPython 3.12 introduces...'
        
    Note:
        Requires PERPLEXITY_API_KEY environment variable to be set.
    """
    # Add input validation to match agent and MCP implementations
    if not query or not query.strip():
        return "🔍 Search error: Please provide a search query."
    
    if len(query) > 500:
        return "🔍 Search error: Query too long (maximum 500 characters)."

    api_key = os.getenv("PERPLEXITY_API_KEY")

    if not api_key:
        return "🔍 Search unavailable: Missing PERPLEXITY_API_KEY configuration."

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai", timeout=180)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful search assistant. Provide a concise, "
                    "informative answer based on current web information. "
                    "Keep responses under 300 words for messaging platforms. "
                    "Format your response clearly and include key facts."
                ),
            },
            {
                "role": "user",
                "content": query,
            },
        ]

        response = client.chat.completions.create(
            model="sonar-pro", messages=messages, temperature=0.2, max_tokens=400
        )

        answer = response.choices[0].message.content
        return f"🔍 **{query}**\n\n{answer}"

    except Exception as e:
        return f"🔍 Search error: {str(e)}"


# Additional utility function for backward compatibility during transition
async def search_web_async(query: str, max_results: int = 3) -> str:
    """Async wrapper for the search tool function.
    
    Provides an asynchronous interface for web search functionality
    to maintain compatibility with async codebases during the transition
    to the new tool architecture.
    
    Args:
        query: The search query to execute.
        max_results: Maximum number of results (kept for compatibility).
        
    Returns:
        str: Same result as search_web() function.
        
    Note:
        This is a compatibility wrapper. The underlying search_web()
        function is synchronous but wrapped for async contexts.
    """
    return search_web(query, max_results)
