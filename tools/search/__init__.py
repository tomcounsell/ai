"""
Web Search Tool

Web search using Perplexity API with intelligent result ranking and summarization.
"""

import os
from typing import Literal

import requests

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"  # Current Perplexity model


class SearchError(Exception):
    """Search operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def search(
    query: str,
    search_type: Literal["conversational", "factual", "citations"] = "conversational",
    max_results: int = 10,
    time_filter: Literal["day", "week", "month", "year"] | None = None,
    domain_filter: list[str] | None = None,
    language: str = "en",
) -> dict:
    """
    Search the web using Perplexity API.

    Args:
        query: Search query
        search_type: Type of search (conversational, factual, citations)
        max_results: Maximum number of results (1-50)
        time_filter: Filter by time period
        domain_filter: List of domains to include
        language: ISO 639-1 language code

    Returns:
        dict with keys:
            - results: List of search results
            - summary: AI-generated summary
            - citations: Source citations (if search_type='citations')
            - error: Error message (if failed)
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return {"error": "PERPLEXITY_API_KEY environment variable not set"}

    # Validate parameters
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    max_results = max(1, min(50, max_results))

    # Build system prompt based on search type
    system_prompts = {
        "conversational": "Be precise and concise. Provide a helpful summary of the search results.",
        "factual": "Provide factual, verifiable information. Be precise and cite specific data points.",
        "citations": "Always cite your sources. Include URLs for verification. Format citations clearly.",
    }
    system_prompt = system_prompts.get(search_type, system_prompts["conversational"])

    # Add domain filter to query if specified
    search_query = query
    if domain_filter:
        domain_str = " OR ".join(f"site:{d}" for d in domain_filter)
        search_query = f"{query} ({domain_str})"

    # Add time filter context
    if time_filter:
        time_contexts = {
            "day": "Focus on results from the last 24 hours.",
            "week": "Focus on results from the last week.",
            "month": "Focus on results from the last month.",
            "year": "Focus on results from the last year.",
        }
        if time_filter in time_contexts:
            system_prompt += f" {time_contexts[time_filter]}"

    try:
        response = requests.post(
            PERPLEXITY_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": search_query},
                ],
                "max_tokens": 1024,
                "return_citations": search_type == "citations",
                "return_related_questions": True,
            },
            timeout=60,
        )

        response.raise_for_status()
        result = response.json()

        if "choices" not in result or len(result["choices"]) == 0:
            return {"error": "No response from Perplexity API", "query": query}

        message = result["choices"][0].get("message", {})
        content = message.get("content", "")

        # Build response
        search_result = {
            "query": query,
            "search_type": search_type,
            "summary": content,
            "results": [],
        }

        # Extract citations if available
        citations = result.get("citations", [])
        if citations:
            search_result["citations"] = citations
            search_result["results"] = [
                {"url": c, "title": c.split("/")[-1] or c}
                for c in citations[:max_results]
            ]

        # Add related questions if available
        related = result.get("related_questions", [])
        if related:
            search_result["suggested_refinements"] = related

        return search_result

    except requests.exceptions.Timeout:
        return {"error": "Search request timed out", "query": query}
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}", "query": query}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}", "query": query}


def search_with_context(
    query: str,
    context: str,
    search_type: Literal["conversational", "factual", "citations"] = "conversational",
) -> dict:
    """
    Search with additional context for more relevant results.

    Args:
        query: Search query
        context: Additional context to guide the search
        search_type: Type of search

    Returns:
        dict with search results
    """
    enhanced_query = f"{query}\n\nContext: {context}"
    return search(enhanced_query, search_type=search_type)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.search 'your search query'")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"Searching for: {query}")

    result = search(query)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nSummary:\n{result['summary']}")
        if result.get("citations"):
            print("\nSources:")
            for c in result["citations"]:
                print(f"  - {c}")
