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
    """
    Search the web and return AI-synthesized answers using Perplexity.

    Args:
        query: The search query to execute
        max_results: Maximum number of results (not used with Perplexity, kept for compatibility)

    Returns:
        AI-synthesized answer based on current web information, formatted for messaging
    """
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
    """Async wrapper for the search tool function."""
    return search_web(query, max_results)
